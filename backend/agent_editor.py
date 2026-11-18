"""Agent d'édition vidéo par le texte du Studio Vidéo IA.

Découpe un rush selon les segments conservés par l'utilisateur, puis
concatène les morceaux. Chaque segment est **ré-encodé** avec des réglages
ultra-légers avant concaténation : cela évite les décalages liés aux images
clés que l'on observe avec une copie de flux directe sur des coupes arbitraires.

Le fichier SRT produit ne contient que les passages gardés, avec des
horodatages recalculés sur la durée réelle de chaque morceau.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_DIR = PROJECT_ROOT / "temp"

NOMBRE_SEGMENTS_MAX = 200
"""Garde-fou : évite un concat.txt gigantesque sur une petite machine."""


class VideoEditError(RuntimeError):
    """Erreur de validation, de découpe ou de concaténation du rush."""


class SegmentsInvalidesError(VideoEditError):
    """Segments fournis par l'utilisateur invalides (erreur client)."""


@dataclass(frozen=True)
class ResultatEdition:
    """Résultat du montage nettoyé."""

    chemin_video: Path
    chemin_srt: Path
    duree_secondes: float
    nombre_segments: int


@functools.lru_cache(maxsize=1)
def _binaire_ffmpeg() -> str:
    """Privilégie le FFmpeg système, sinon utilise celui d'imageio-ffmpeg."""
    systeme = shutil.which("ffmpeg")
    if systeme:
        return systeme
    embarque = imageio_ffmpeg.get_ffmpeg_exe()
    logger.warning(
        "FFmpeg système absent ; utilisation du binaire embarqué %s", embarque
    )
    return embarque


def _valider_segments(kept_segments: list[dict]) -> list[tuple[float, float, str]]:
    """Contrôle, normalise et trie les segments conservés."""
    if not kept_segments:
        raise SegmentsInvalidesError("Aucun segment à conserver.")
    if len(kept_segments) > NOMBRE_SEGMENTS_MAX:
        raise SegmentsInvalidesError(
            f"Trop de segments (maximum {NOMBRE_SEGMENTS_MAX})."
        )

    segments: list[tuple[float, float, str]] = []
    for index, brut in enumerate(kept_segments, start=1):
        if not isinstance(brut, dict):
            raise SegmentsInvalidesError(f"Le segment {index} doit être un objet.")
        try:
            debut = float(brut["start"])
            fin = float(brut["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SegmentsInvalidesError(
                f"Segment {index} : 'start' et 'end' doivent être numériques."
            ) from exc
        if debut < 0 or fin <= debut:
            raise SegmentsInvalidesError(
                f"Segment {index} : plage horaire invalide ({debut} → {fin})."
            )
        texte = str(brut.get("text") or "").strip()
        segments.append((debut, fin, texte))

    segments.sort(key=lambda element: element[0])
    for precedent, suivant in zip(segments, segments[1:]):
        if suivant[0] < precedent[1] - 0.001:
            raise SegmentsInvalidesError("Les segments conservés se chevauchent.")
    return segments


def _horodatage(secondes: float) -> str:
    """Formate un temps en ``HH:MM:SS,mmm`` (norme SRT)."""
    millisecondes = max(0, round(float(secondes) * 1000))
    heures, reste = divmod(millisecondes, 3_600_000)
    minutes, reste = divmod(reste, 60_000)
    secses, ms = divmod(reste, 1000)
    return f"{heures:02d}:{minutes:02d}:{secses:02d},{ms:03d}"


def _rendre_srt(cues: list[tuple[float, float, str]]) -> str:
    """Sérialise les répliques conservées en SRT valide."""
    lignes: list[str] = []
    for numero, (debut, fin, texte) in enumerate(cues, start=1):
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


async def _executer(commande: list[str]) -> None:
    """Lance FFmpeg de façon asynchrone et lève une erreur propre."""
    try:
        processus = await asyncio.create_subprocess_exec(
            *commande,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise VideoEditError(f"Impossible de lancer FFmpeg : {exc}") from exc

    _, erreur = await processus.communicate()
    if processus.returncode != 0:
        detail = erreur.decode("utf-8", errors="replace").strip()[:400]
        raise VideoEditError(f"FFmpeg a échoué (code {processus.returncode}) : {detail}")


def _duree_moviepy(chemin: Path) -> float:
    """Lit la durée d'un segment sans décoder la vidéo (métadonnées seules)."""
    from moviepy.editor import VideoFileClip

    clip = VideoFileClip(str(chemin))
    try:
        return float(clip.duration)
    finally:
        clip.close()


async def _duree_segment(chemin: Path, demandee: float) -> float:
    """Renvoie la durée réellement encodée d'un segment."""
    sonde = shutil.which("ffprobe")
    if sonde:
        commande = [
            sonde,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(chemin),
        ]
        try:
            processus = await asyncio.create_subprocess_exec(
                *commande,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            sortie, _ = await processus.communicate()
            if processus.returncode == 0:
                return float(sortie.decode().strip())
        except (OSError, ValueError):
            pass

    try:
        return await asyncio.to_thread(_duree_moviepy, chemin)
    except Exception as exc:
        logger.warning(
            "Durée réelle indisponible pour %s (%s) ; durée demandée utilisée.",
            chemin.name,
            exc,
        )
        return demandee


def _ecrire_concat(chemins: list[Path], destination: Path) -> None:
    """Écrit le fichier `concat.txt` attendu par le démultiplexeur FFmpeg."""
    lignes: list[str] = []
    for chemin in chemins:
        chemin_absolu = str(chemin.resolve()).replace("'", "'\\''")
        lignes.append(f"file '{chemin_absolu}'")
    destination.write_text("\n".join(lignes) + "\n", encoding="utf-8")


async def cut_and_merge_rush(
    media_path: str,
    kept_segments: list[dict],
    output_video_path: str,
    output_srt_path: str,
) -> ResultatEdition:
    """Découpe un rush puis assemble les segments conservés.

    Args:
        media_path: Chemin de la vidéo (ou de l'audio) source.
        kept_segments: Objets ``{"start": 1.2, "end": 4.5, "text": "…"}``.
        output_video_path: Chemin de la vidéo nettoyée à générer.
        output_srt_path: Chemin du SRT recalculé à générer.

    Returns:
        Le :class:`ResultatEdition` décrivant la vidéo et le SRT produits.

    Raises:
        SegmentsInvalidesError: Segments vides, chevauchants ou mal formés.
        VideoEditError: Média absent ou échec FFmpeg.
    """
    segments = _valider_segments(kept_segments)

    media = Path(media_path)
    if not media.is_file():
        raise VideoEditError(f"Média introuvable : {media}")

    sortie = Path(output_video_path)
    srt = Path(output_srt_path)
    if srt.suffix.lower() != ".srt":
        raise VideoEditError("Le fichier de sous-titres doit avoir l'extension .srt")
    sortie.parent.mkdir(parents=True, exist_ok=True)
    srt.parent.mkdir(parents=True, exist_ok=True)

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    dossier_travail = TEMP_DIR / f"edition-{uuid4().hex[:12]}"
    dossier_travail.mkdir(parents=True, exist_ok=True)
    concat = dossier_travail / "concat.txt"
    ffmpeg = _binaire_ffmpeg()

    try:
        fichiers: list[tuple[Path, str, float]] = []
        for index, (debut, fin, texte) in enumerate(segments):
            chemin_segment = dossier_travail / f"segment-{index:03d}.mp4"
            duree_demandee = fin - debut
            await _executer(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{debut:.3f}",
                    "-i",
                    str(media),
                    "-t",
                    f"{duree_demandee:.3f}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "23",
                    "-threads",
                    "2",
                    "-c:a",
                    "aac",
                    "-pix_fmt",
                    "yuv420p",
                    "-avoid_negative_ts",
                    "make_zero",
                    str(chemin_segment),
                ]
            )
            fichiers.append((chemin_segment, texte, duree_demandee))

        durees = [
            max(0.0, await _duree_segment(chemin, demandee))
            for chemin, _, demandee in fichiers
        ]

        _ecrire_concat([chemin for chemin, _, _ in fichiers], concat)
        await _executer(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(sortie),
            ]
        )

        curs = 0.0
        cues: list[tuple[float, float, str]] = []
        for (_, texte, _), duree in zip(fichiers, durees):
            if texte:
                cues.append((curs, curs + duree, texte))
            curs += duree
        srt.write_text(_rendre_srt(cues), encoding="utf-8")

        return ResultatEdition(
            chemin_video=sortie,
            chemin_srt=srt,
            duree_secondes=curs,
            nombre_segments=len(segments),
        )
    except VideoEditError:
        sortie.unlink(missing_ok=True)
        srt.unlink(missing_ok=True)
        raise
    except Exception as exc:
        sortie.unlink(missing_ok=True)
        srt.unlink(missing_ok=True)
        logger.warning("Échec du montage du rush : %s", exc)
        raise VideoEditError(f"Échec du montage du rush : {exc}") from exc
    finally:
        shutil.rmtree(dossier_travail, ignore_errors=True)
