"""Agent de transcription du Studio Vidéo IA & Motion Design.

Transforme un rush audio ou vidéo en sous-titres SRT avec ``faster-whisper``.
Le modèle tourne **exclusivement sur CPU**, en précision **int8**, et le
média est normalisé en WAV 16 kHz mono avant analyse. Aucun GPU n'est requis.

Le modèle ``tiny`` (~75 Mo) est utilisé par défaut pour préserver la mémoire
de la machine (5,7 Go de RAM, faible place disponible). ``base`` reste
disponible pour les cas où la précision prime.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_DIR = PROJECT_ROOT / "temp"

MODEL_SIZES = ("tiny", "base")
"""Modèles autorisés : assez légers pour un rendu CPU sur petite RAM."""

TIMEOUT_EXTRACTION = 120.0
"""Garde-fou pour l'extraction FFmpeg (en secondes)."""

_cpu_threads = min(2, os.cpu_count() or 1)

_lock: asyncio.Lock | None = None


class TranscriptionError(RuntimeError):
    """Erreur d'extraction audio, de chargement du modèle ou de transcription."""


@dataclass(frozen=True)
class ResultatTranscription:
    """Résultat d'une transcription : fichier SRT et texte intégral."""

    chemin_srt: Path
    texte: str
    langue: str | None = None
    duree_secondes: float = 0.0


@functools.lru_cache(maxsize=2)
def _binaire_ffmpeg() -> str:
    """Utilise FFmpeg système si présent, sinon le binaire embarqué imageio."""
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


@functools.lru_cache(maxsize=2)
def _modele(model_size: str):
    """Charge et met en cache le modèle Whisper (coûteux, ~1 à 2 s)."""
    from faster_whisper import WhisperModel

    logger.info(
        "Chargement du modèle Whisper '%s' (CPU, int8, %d threads)",
        model_size,
        _cpu_threads,
    )
    return WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        cpu_threads=_cpu_threads,
        num_workers=1,
    )


def _verrou() -> asyncio.Lock:
    """Verrou paresseux : une seule transcription à la fois ( RAM / CPU )."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _horodatage(secondes: float) -> str:
    """Formate un temps en ``HH:MM:SS,mmm`` attendu par le format SRT."""
    millisecondes = max(0, round(float(secondes) * 1000))
    heures, reste = divmod(millisecondes, 3_600_000)
    minutes, reste = divmod(reste, 60_000)
    secondes_entieres, ms = divmod(reste, 1000)
    return f"{heures:02d}:{minutes:02d}:{secondes_entieres:02d},{ms:03d}"


def _rendre_srt(segments: list[tuple[float, float, str]]) -> str:
    """Sérialise les segments Whisper en fichier SRT valide."""
    lignes: list[str] = []
    for numero, (debut, fin, texte) in enumerate(segments, start=1):
        fin_utile = fin if fin > debut else debut + 0.001
        lignes.extend(
            [
                str(numero),
                f"{_horodatage(debut)} --> {_horodatage(fin_utile)}",
                texte,
                "",
            ]
        )
    return "\n".join(lignes) + "\n"


def _extraire_audio(media: Path, destination: Path) -> None:
    """Normalise le média en WAV 16 kHz, mono, PCM signé 16 bits."""
    commande = [
        _binaire_ffmpeg(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(destination),
    ]
    resultat = subprocess.run(
        commande,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_EXTRACTION,
        check=False,
    )
    if resultat.returncode != 0:
        raise TranscriptionError(
            "Impossible d'extraire l'audio du média : "
            f"{resultat.stderr.strip()[:300]}"
        )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise TranscriptionError("L'extraction audio n'a produit aucun fichier.")


def _transcrire_sync(
    media: Path, srt: Path, model_size: str, wav_temporaire: Path
) -> ResultatTranscription:
    """Pipeline synchrone exécuté dans un thread dédié."""
    _extraire_audio(media, wav_temporaire)

    try:
        modele = _modele(model_size)
        segments_bruts, informations = modele.transcribe(
            str(wav_temporaire),
            beam_size=1,
            vad_filter=True,
        )

        segments: list[tuple[float, float, str]] = []
        for segment in segments_bruts:
            texte = (segment.text or "").strip()
            if not texte:
                continue
            segments.append((float(segment.start), float(segment.end), texte))

        if not segments:
            raise TranscriptionError("Aucune parole détectée dans le média fourni.")

        srt.parent.mkdir(parents=True, exist_ok=True)
        srt.write_text(_rendre_srt(segments), encoding="utf-8")

        return ResultatTranscription(
            chemin_srt=srt,
            texte=" ".join(texte for _, _, texte in segments),
            langue=getattr(informations, "language", None),
            duree_secondes=max(0.0, segments[-1][1]),
        )
    except TranscriptionError:
        srt.unlink(missing_ok=True)
        raise
    except Exception as exc:
        srt.unlink(missing_ok=True)
        logger.warning("Échec de la transcription Whisper : %s", exc)
        raise TranscriptionError(f"Échec de la transcription : {exc}") from exc


async def transcribe_audio_or_video(
    media_path: str,
    output_srt_path: str,
    model_size: str = "tiny",
) -> ResultatTranscription:
    """Transcrit un fichier audio ou vidéo en sous-titres SRT.

    Args:
        media_path: Chemin du rush (audio ou vidéo) à transcrire.
        output_srt_path: Chemin du fichier `.srt` à générer.
        model_size: ``tiny`` (défaut, ~75 Mo) ou ``base`` (~145 Mo).

    Returns:
        Le :class:`ResultatTranscription` contenant le chemin du SRT, le texte
        intégral, la langue détectée et la fin du dernier segment.

    Raises:
        TranscriptionError: Si le média est absent, le modèle invalide,
            l'extraction échoue ou aucune parole n'est détectée.
    """
    if model_size not in MODEL_SIZES:
        raise TranscriptionError(
            f"Modèle invalide '{model_size}'. Modèles acceptés : "
            f"{', '.join(MODEL_SIZES)}."
        )

    media = Path(media_path)
    if not media.is_file():
        raise TranscriptionError(f"Média introuvable : {media}")

    srt = Path(output_srt_path)
    if srt.suffix.lower() != ".srt":
        raise TranscriptionError("Le fichier de sous-titres doit avoir l'extension .srt")

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    wav_temporaire = TEMP_DIR / f"transcription-{uuid4().hex[:12]}.wav"

    try:
        async with _verrou():
            return await asyncio.to_thread(
                _transcrire_sync, media, srt, model_size, wav_temporaire
            )
    finally:
        wav_temporaire.unlink(missing_ok=True)
