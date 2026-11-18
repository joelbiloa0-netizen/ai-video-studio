"""Agent Ingénieur Son du Studio Vidéo IA.

Génère une voix off MP3 avec ``edge-tts`` et construit des sous-titres
synchronisés (SRT ou WebVTT) à partir des événements ``WordBoundary`` du
service. Le format des sous-titres est déduit de l'extension du chemin fourni.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import edge_tts

logger = logging.getLogger(__name__)

TICKS_PAR_SECONDE = 10_000_000
"""Le service edge-tts exprime ses horodatages en unités de 100 ns."""

LONGUEUR_MAX_SOUS_TITRE = 84
MOTS_MAX_PAR_SOUS_TITRE = 14
TIMEOUT_SYNTHESE = 90.0
"""Garde-fou : évite qu'une requête réseau bloquée immobilise l'API."""


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


async def generate_voiceover_and_subs(
    text: str,
    output_audio_path: str,
    output_sub_path: str,
    voice: str = "fr-FR-DeniseNeural",
) -> tuple[Path, Path]:
    """Génère la voix off et ses sous-titres synchronisés.

    Args:
        text: Texte à prononcer.
        output_audio_path: Chemin du fichier MP3 de sortie.
        output_sub_path: Chemin des sous-titres (`.srt` ou `.vtt` détecté
            d'après l'extension).
        voice: Voix edge-tts (défaut : ``fr-FR-DeniseNeural``).

    Returns:
        Le couple ``(chemin_audio, chemin_sous_titres)`` sous forme de ``Path``.

    Raises:
        VoiceOverError: Si le texte est vide, si le service est injoignable ou
            si l'écriture des fichiers échoue. Les fichiers partiels sont
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

        await asyncio.wait_for(
            _stream_audio(texte_nettoye, voice, chemin_audio, constructeur),
            timeout=TIMEOUT_SYNTHESE,
        )

        repliques = constructeur.terminer()
        if not repliques:
            raise VoiceOverError("Aucun repère de sous-titres n'a été reçu.")

        contenu = _rendre_sous_titres(repliques, format_vtt)
        chemin_sous_titres.write_text(contenu, encoding="utf-8")
    except VoiceOverError:
        _nettoyer(chemin_audio, chemin_sous_titres)
        raise
    except Exception as exc:
        _nettoyer(chemin_audio, chemin_sous_titres)
        logger.warning("Échec de la synthèse vocale edge-tts : %s", exc)
        raise VoiceOverError(
            "Service de synthèse vocale indisponible "
            "(connexion Internet requise par edge-tts)."
        ) from exc

    logger.info(
        "Voix off générée : %s (%d répliques de sous-titres)",
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
