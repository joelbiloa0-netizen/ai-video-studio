"""Agent Ingénieur Son du Studio Vidéo IA.

Génère une voix off MP3 et des sous-titres synchronisés (SRT ou WebVTT).

Deux moteurs sont enchaînés automatiquement :

1. ``edge-tts`` — voix naturelle, nécessite Internet ;
2. ``piper-tts`` — moteur local CPU, 100 % hors-ligne, utilisé en secours dès
   qu'edge-tts échoue (403, timeout, coupure réseau). Dans ce cas, les
   sous-titres sont reconstruits à partir de la **durée réelle** du fichier
   audio produit et du découpage du texte en phrases.

Le format des sous-titres est déduit de l'extension du chemin fourni.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import edge_tts
import imageio_ffmpeg

logger = logging.getLogger(__name__)

TICKS_PAR_SECONDE = 10_000_000
"""Le service edge-tts exprime ses horodatages en unités de 100 ns."""

LONGUEUR_MAX_SOUS_TITRE = 84
MOTS_MAX_PAR_SOUS_TITRE = 14
TIMEOUT_SYNTHESE = 90.0
"""Garde-fou : évite qu'une requête réseau bloquée immobilise l'API."""

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELE_PIPER = (
    PROJECT_ROOT / "media_cache" / "piper" / "fr_FR-siwis-medium.onnx"
)
"""Voix française Piper (61 Mo) mise en cache, ignorée par Git."""


class VoiceOverError(RuntimeError):
    """Erreur de synthèse vocale ou d'écriture des fichiers générés."""


@dataclass(frozen=True)
class _Replique:
    """Une phrase de sous-titre avec ses positions dans le flux audio."""

    debut: int
    fin: int
    texte: str


class _ConstructeurSousTitres:
    """Regroupe les mots reçus en répliques lisibles (et non mot par mot)."""

    def __init__(self) -> None:
        self._mots: list[tuple[int, int, str]] = []
        self._repliques: list[_Replique] = []

    def ajouter_mot(self, offset: int, duration: int, texte: str) -> None:
        mot = texte.strip()
        if not mot:
            return
        self._mots.append((offset, duration, mot))
        if self._doit_clore(mot):
            self._clore()

    def _doit_clore(self, dernier_mot: str) -> bool:
        if dernier_mot and dernier_mot[-1] in ".!?…;:":
            return True
        if len(self._mots) >= MOTS_MAX_PAR_SOUS_TITRE:
            return True
        longueur = len(" ".join(mot for _, _, mot in self._mots))
        return longueur >= LONGUEUR_MAX_SOUS_TITRE

    def _clore(self) -> None:
        if not self._mots:
            return
        debut = self._mots[0][0]
        fin = max(offset + duree for offset, duree, _ in self._mots)
        self._repliques.append(
            _Replique(debut=debut, fin=fin, texte=" ".join(m for _, _, m in self._mots))
        )
        self._mots.clear()

    def terminer(self) -> list[_Replique]:
        """Clôt la réplique en cours et renvoie toutes les répliques."""
        self._clore()
        return self._repliques


def _horodatage(ticks: int, separateur: str) -> str:
    """Convertit un temps en ticks en ``HH:MM:SS,mmm`` (ou ``.mmm`` pour VTT)."""
    millisecondes = max(0, round(ticks / TICKS_PAR_SECONDE * 1000))
    heures, reste = divmod(millisecondes, 3_600_000)
    minutes, reste = divmod(reste, 60_000)
    secondes, ms = divmod(reste, 1000)
    return f"{heures:02d}:{minutes:02d}:{secondes:02d}{separateur}{ms:03d}"


def _rendre_sous_titres(repliques: list[_Replique], format_vtt: bool) -> str:
    """Sérialise les répliques en SRT ou en WebVTT."""
    separateur = "." if format_vtt else ","
    lignes: list[str] = []
    if format_vtt:
        lignes.extend(["WEBVTT", ""])

    for numero, replique in enumerate(repliques, start=1):
        fin = replique.fin if replique.fin > replique.debut else replique.debut + 1_000_000
        lignes.extend(
            [
                str(numero),
                (
                    f"{_horodatage(replique.debut, separateur)} --> "
                    f"{_horodatage(fin, separateur)}"
                ),
                replique.texte,
                "",
            ]
        )
    return "\n".join(lignes) + "\n"


async def _stream_audio(
    texte: str,
    voix: str,
    chemin_audio: Path,
    constructeur: _ConstructeurSousTitres,
) -> None:
    """Écrit le flux MP3 et collecte les repères temporels des mots."""
    communication = edge_tts.Communicate(texte, voix)
    audio_recu = False

    with chemin_audio.open("wb") as fichier:
        async for morceau in communication.stream():
            type_morceau = morceau.get("type")
            if type_morceau == "audio":
                fichier.write(morceau["data"])
                audio_recu = True
            elif type_morceau == "WordBoundary":
                constructeur.ajouter_mot(
                    morceau["offset"], morceau["duration"], morceau["text"]
                )

    if not audio_recu:
        raise VoiceOverError("Aucun flux audio n'a été reçu du service de synthèse.")


# ---------------------------------------------------------------------------
# Moteur local de secours (Piper, CPU, hors-ligne)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _binaire_ffmpeg() -> str:
    """Utilise FFmpeg système si présent, sinon le binaire embarqué imageio."""
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


@functools.lru_cache(maxsize=1)
def _voix_piper():
    """Charge une seule fois le modèle Piper (assembly ONNX ~61 Mo)."""
    from piper import PiperVoice

    return PiperVoice.load(str(MODELE_PIPER))


def _extraire_phrases(texte: str) -> list[str]:
    """Découpe le texte en répliques courtes, ponctuation conservée."""
    morceaux = re.split(r"(?<=[.!?…;:])\s+", texte.strip())
    phrases: list[str] = []
    for morceau in morceaux:
        morceau = morceau.strip()
        if not morceau:
            continue
        # Une phrase trop longue est redecoupée au dernier comma disponible.
        while len(morceau) > LONGUEUR_MAX_SOUS_TITRE and "," in morceau:
            gauche, droite = morceau.rsplit(",", 1)
            if not gauche.strip() or not droite.strip():
                break
            phrases.append(gauche.strip() + ",")
            morceau = droite.strip()
        phrases.append(morceau)
    return phrases


def _repliques_depuis_duree(texte: str, duree_secondes: float) -> list[_Replique]:
    """Répartit la durée réelle de l'audio entre les phrases du texte.

    Le poids de chaque phrase est son nombre de mots : une phrase longue
    reçoit proportionnellement plus de temps qu'une courte. L'horodatage
    final tombe exactement sur la fin du fichier audio.
    """
    phrases = _extraire_phrases(texte) or [texte.strip()]
    poids = [max(1, len(phrase.split())) for phrase in phrases]
    total = sum(poids)
    ticks_total = max(1, round(duree_secondes * TICKS_PAR_SECONDE))

    repliques: list[_Replique] = []
    debut = 0
    cumul = 0
    for phrase, poids_phrase in zip(phrases, poids):
        cumul += poids_phrase
        fin = round(ticks_total * cumul / total)
        fin = max(debut + 1, fin)
        repliques.append(_Replique(debut=debut, fin=fin, texte=phrase))
        debut = fin
    return repliques


def _piper_synthese_sync(texte: str, chemin_wav: Path) -> float:
    """Synthétise le texte en WAV et renvoie sa durée réelle (en secondes)."""
    voix = _voix_piper()
    with wave.open(str(chemin_wav), "wb") as fichier:
        voix.synthesize_wav(texte, fichier)
    with wave.open(str(chemin_wav), "rb") as fichier:
        return fichier.getnframes() / fichier.getframerate()


def _wav_vers_mp3_sync(source: Path, cible: Path) -> None:
    """Convertit le WAV Piper en MP3 avec l'encodeur lame de FFmpeg."""
    commande = [
        _binaire_ffmpeg(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(cible),
    ]
    resultat = subprocess.run(
        commande, capture_output=True, text=True, timeout=120, check=False
    )
    if resultat.returncode != 0:
        raise RuntimeError(
            f"Conversion WAV -> MP3 impossible : {resultat.stderr.strip()[:300]}"
        )


async def _synthese_piper(texte: str, chemin_audio: Path) -> list[_Replique]:
    """Génère voix off et sous-titres avec le moteur local, sans réseau."""
    if not MODELE_PIPER.is_file():
        raise VoiceOverError(
            "Modèle vocal local introuvable "
            f"({MODELE_PIPER.relative_to(PROJECT_ROOT).as_posix()})."
        )

    chemin_wav = chemin_audio.with_name(f"{chemin_audio.stem}.piper.wav")
    try:
        duree = await asyncio.to_thread(_piper_synthese_sync, texte, chemin_wav)
        await asyncio.to_thread(_wav_vers_mp3_sync, chemin_wav, chemin_audio)
    except Exception as exc:
        logger.warning("Échec du moteur vocal local piper : %s", exc)
        raise VoiceOverError(
            "Le moteur vocal local n'a pas pu générer la voix off."
        ) from exc
    finally:
        chemin_wav.unlink(missing_ok=True)

    if not chemin_audio.is_file() or chemin_audio.stat().st_size == 0:
        raise VoiceOverError("Le moteur vocal local n'a produit aucun audio.")

    logger.info(
        "Voix off locale générée : %.2f s de audio, %d phrase(s)",
        duree,
        len(_extraire_phrases(texte)),
    )
    return _repliques_depuis_duree(texte, duree)


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


async def generate_voiceover_and_subs(
    text: str,
    output_audio_path: str,
    output_sub_path: str,
    voice: str = "fr-FR-DeniseNeural",
) -> tuple[Path, Path]:
    """Génère la voix off et ses sous-titres synchronisés.

    ``edge-tts`` est tenté en priorité. Toute défaillance déclenche
    automatiquement le moteur local Piper, sans erreur renvoyée au client.

    Args:
        text: Texte à prononcer.
        output_audio_path: Chemin du fichier MP3 de sortie.
        output_sub_path: Chemin des sous-titres (`.srt` ou `.vtt` détecté
            d'après l'extension).
        voice: Voix edge-tts (défaut : ``fr-FR-DeniseNeural``).

    Returns:
        Le couple ``(chemin_audio, chemin_sous_titres)`` sous forme de ``Path``.

    Raises:
        VoiceOverError: Si le texte est vide ou si aucun des deux moteurs ne
            parvient à produire un fichier. Les fichiers partiels sont alors
            supprimés automatiquement.
    """
    texte_nettoye = text.strip() if isinstance(text, str) else ""
    if not texte_nettoye:
        raise VoiceOverError("Le texte à synthétiser ne peut pas être vide.")

    chemin_audio = Path(output_audio_path)
    chemin_sous_titres = Path(output_sub_path)
    format_vtt = chemin_sous_titres.suffix.lower() == ".vtt"
    constructeur = _ConstructeurSousTitres()

    try:
        chemin_audio.parent.mkdir(parents=True, exist_ok=True)
        chemin_sous_titres.parent.mkdir(parents=True, exist_ok=True)

        try:
            await asyncio.wait_for(
                _stream_audio(texte_nettoye, voice, chemin_audio, constructeur),
                timeout=TIMEOUT_SYNTHESE,
            )
            repliques = constructeur.terminer()
            if not repliques:
                raise VoiceOverError("Aucun repère de sous-titres reçu d'edge-tts.")
            logger.info("Voix off générée via edge-tts (%d répliques).", len(repliques))
        except Exception as exc_edge:
            # 403, timeout, coupure réseau ou sous-titres vides : on bascule.
            chemin_audio.unlink(missing_ok=True)
            logger.warning(
                "edge-tts indisponible (%s) ; bascule sur le moteur local Piper.",
                exc_edge,
            )
            repliques = await _synthese_piper(texte_nettoye, chemin_audio)

        chemin_sous_titres.write_text(
            _rendre_sous_titres(repliques, format_vtt), encoding="utf-8"
        )
    except VoiceOverError:
        _nettoyer(chemin_audio, chemin_sous_titres)
        raise
    except Exception as exc:
        _nettoyer(chemin_audio, chemin_sous_titres)
        logger.warning("Échec de la synthèse vocale : %s", exc)
        raise VoiceOverError(
            "Impossible de générer la voix off "
            "(moteurs en ligne et local tous deux défaillants)."
        ) from exc

    logger.info(
        "Voix off prête : %s (%d répliques de sous-titres)",
        chemin_audio,
        len(repliques),
    )
    return chemin_audio, chemin_sous_titres


def _nettoyer(*chemins: Path) -> None:
    """Supprime les fichiers partiels en cas d'échec."""
    for chemin in chemins:
        try:
            chemin.unlink(missing_ok=True)
        except OSError:
            logger.debug("Impossible de supprimer le fichier partiel %s", chemin)
