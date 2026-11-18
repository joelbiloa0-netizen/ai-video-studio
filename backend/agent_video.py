"""Agent Monteur / Motion Designer du Studio Vidéo IA.

Compose une vidéo à partir d'images fixes et d'une voix off, au format cible,
uniquement sur CPU. MoviePy gère le montage, FFmpeg l'encodage final et
l'incrustation optionnelle des sous-titres (via libass).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg
from moviepy.editor import AudioFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageOps

_RESAMPLAGE = Image.Resampling.LANCZOS

logger = logging.getLogger(__name__)

FORMATS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}
FPS = 24
COULEUR_FOND = (10, 10, 15)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_DIR = PROJECT_ROOT / "temp"


class VideoAssemblyError(RuntimeError):
    """Erreur de montage ou d'encodage de la vidéo finale."""


@dataclass(frozen=True)
class ResultatMontage:
    """Résultat de l'assemblage vidéo."""

    chemin_video: Path
    duree_secondes: float
    sous_titres_integres: bool
    chemin_sous_titres: Path | None = None


@functools.lru_cache(maxsize=1)
def _binaire_ffmpeg() -> str:
    """Utilise FFmpeg système si présent, sinon le binaire embarqué imageio."""
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


@functools.lru_cache(maxsize=1)
def _soutient_sous_titres() -> bool:
    """Détecte la présence du filtre `subtitles` (libass) dans FFmpeg."""
    try:
        resultat = subprocess.run(
            [_binaire_ffmpeg(), "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return " subtitles " in resultat.stdout


def _echapper_filtre(chemin: Path) -> str:
    """Échappe un chemin pour l'inclure dans une expression de filtre FFmpeg."""
    return (
        str(chemin.resolve())
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


def _vtt_vers_srt(source: Path, destination: Path) -> None:
    """Convertit un WebVTT simple en SRT afin d'uniformiser le rendu FFmpeg."""
    lignes = source.read_text(encoding="utf-8").splitlines()
    sortie: list[str] = []
    numero = 1
    index = 0

    while index < len(lignes):
        ligne = lignes[index].strip()
        if not ligne or ligne.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            index += 1
            continue
        if "-->" not in ligne:
            index += 1
            continue

        parties = ligne.split("-->")
        debut = parties[0].strip().split()[0].replace(".", ",")
        fin = parties[1].strip().split()[0].replace(".", ",")
        index += 1
        texte: list[str] = []
        while index < len(lignes) and lignes[index].strip():
            texte.append(lignes[index].strip())
            index += 1
        sortie.extend([str(numero), f"{debut} --> {fin}", *texte, ""])
        numero += 1

    destination.write_text("\n".join(sortie) + "\n", encoding="utf-8")


def _bruler_sous_titres(
    video_source: Path, srt: Path, video_cible: Path
) -> bool:
    """Incruste les sous-titres avec FFmpeg. Renvoie False en cas d'échec."""
    commande = [
        _binaire_ffmpeg(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_source),
        "-vf",
        f"subtitles={_echapper_filtre(srt)}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "26",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(video_cible),
    ]
    try:
        resultat = subprocess.run(
            commande, capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Incrustation des sous-titres impossible : %s", exc)
        return False

    if resultat.returncode != 0:
        logger.warning(
            "Filtre subtitles indisponible ou défaillant : %s",
            resultat.stderr.strip()[:400],
        )
        return False
    return True


def _preparer_image(
    source: Path, destination: Path, dimensions: tuple[int, int]
) -> None:
    """Redimensionne sans déformation et centre l'image sur le fond du studio.

    Le redimensionnement est fait avec Pillow plutôt qu'avec l'effet `resize`
    de MoviePy 1.0.3, incompatible avec Pillow >= 10 (`Image.ANTIALIAS`).
    """
    largeur, hauteur = dimensions
    with Image.open(source) as image_brute:
        image = ImageOps.exif_transpose(image_brute).convert("RGB")
        facteur = min(hauteur / image.height, largeur / image.width)
        nouvelle_taille = (
            max(2, round(image.width * facteur)),
            max(2, round(image.height * facteur)),
        )
        redimensionnee = image.resize(nouvelle_taille, _RESAMPLAGE)
        toile = Image.new("RGB", (largeur, hauteur), COULEUR_FOND)
        toile.paste(
            redimensionnee,
            ((largeur - redimensionnee.width) // 2, (hauteur - redimensionnee.height) // 2),
        )
        toile.save(destination)


def _preparer_sous_titres(
    chemin_sous_titres: Path | None, identifiant: str
) -> Path | None:
    """Renvoie un SRT exploitable, en convertissant les VTT au besoin."""
    if not chemin_sous_titres or not chemin_sous_titres.is_file():
        if chemin_sous_titres:
            logger.warning("Fichier de sous-titres introuvable : %s", chemin_sous_titres)
        return None

    if chemin_sous_titres.suffix.lower() == ".vtt":
        srt_converti = TEMP_DIR / f"montage-{identifiant}-sous-titres.srt"
        _vtt_vers_srt(chemin_sous_titres, srt_converti)
        return srt_converti
    return chemin_sous_titres


def _assembler_sync(
    image_paths: list[str],
    audio_path: str,
    subtitle_path: str | None,
    output_video_path: str,
    dimensions: tuple[int, int],
) -> ResultatMontage:
    """Rendu synchrone (exécuté dans un thread dédié)."""
    images = [Path(chemin) for chemin in image_paths]
    if not images:
        raise VideoAssemblyError("Au moins une image est requise.")
    for image in images:
        if not image.is_file():
            raise VideoAssemblyError(f"Image introuvable : {image}")

    audio_source = Path(audio_path)
    if not audio_source.is_file():
        raise VideoAssemblyError(f"Fichier audio introuvable : {audio_source}")

    sortie = Path(output_video_path)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    identifiant = uuid4().hex[:12]
    brut = TEMP_DIR / f"montage-{identifiant}-brut.mp4"
    srt_temporaire: Path | None = None
    images_preparees: list[Path] = []
    audio = None
    video = None

    try:
        srt = _preparer_sous_titres(
            Path(subtitle_path) if subtitle_path else None, identifiant
        )
        if srt and srt.name.startswith(f"montage-{identifiant}-sous-titres"):
            srt_temporaire = srt

        audio = AudioFileClip(str(audio_source))
        duree = float(audio.duration)
        if duree <= 0:
            raise VideoAssemblyError("La durée de la voix off est nulle.")

        duree_par_image = duree / len(images)
        clips = []
        for index, image in enumerate(images):
            preparee = TEMP_DIR / f"montage-{identifiant}-img-{index}.png"
            _preparer_image(image, preparee, dimensions)
            images_preparees.append(preparee)
            clips.append(
                ImageClip(str(preparee))
                .set_start(index * duree_par_image)
                .set_duration(duree_par_image)
            )

        video = CompositeVideoClip(clips, size=dimensions, bg_color=COULEUR_FOND)
        video = video.set_audio(audio).set_duration(duree)

        avec_incrustation = bool(srt) and _soutient_sous_titres()
        cible_rendu = brut if avec_incrustation else sortie

        video.write_videofile(
            str(cible_rendu),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            ffmpeg_params=["-crf", "28", "-pix_fmt", "yuv420p"],
            verbose=False,
            logger=None,
        )

        sous_titres_integres = False
        chemin_sidecar: Path | None = None

        if avec_incrustation and srt:
            if _bruler_sous_titres(brut, srt, sortie):
                sous_titres_integres = True
            else:
                shutil.copyfile(brut, sortie)

        if srt and not sous_titres_integres:
            # Fallback : on livre les sous-titres à côté de la vidéo.
            chemin_sidecar = sortie.with_suffix(".srt")
            shutil.copyfile(srt, chemin_sidecar)
            logger.warning(
                "Sous-titres non incrustés ; fichier séparé livré : %s",
                chemin_sidecar,
            )

        return ResultatMontage(
            chemin_video=sortie,
            duree_secondes=duree,
            sous_titres_integres=sous_titres_integres,
            chemin_sous_titres=chemin_sidecar,
        )
    except VideoAssemblyError:
        sortie.unlink(missing_ok=True)
        raise
    except Exception as exc:
        sortie.unlink(missing_ok=True)
        logger.warning("Échec du montage vidéo : %s", exc)
        raise VideoAssemblyError(f"Échec du montage vidéo : {exc}") from exc
    finally:
        if video is not None:
            video.close()
        if audio is not None:
            audio.close()
        brut.unlink(missing_ok=True)
        for preparee in images_preparees:
            preparee.unlink(missing_ok=True)
        if srt_temporaire is not None:
            srt_temporaire.unlink(missing_ok=True)


async def assemble_video(
    image_paths: list[str],
    audio_path: str,
    subtitle_path: str,
    output_video_path: str,
    format_video: str = "9:16",
) -> ResultatMontage:
    """Assemble images, voix off et sous-titres en une vidéo finale.

    Args:
        image_paths: Chemins des images sources, dans l'ordre d'apparition.
        audio_path: Fichier audio de la voix off (durée de référence).
        subtitle_path: Fichier SRT/VTT (chaîne vide pour désactiver).
        output_video_path: Chemin de la vidéo MP3/MP4 de sortie.
        format_video: ``9:16``, ``16:9`` ou ``1:1``.

    Returns:
        Le :class:`ResultatMontage` décrivant la vidéo produite.

    Raises:
        VideoAssemblyError: Si un fichier est absent, le format invalide ou
            le rendu FFmpeg échoue.
    """
    if format_video not in FORMATS:
        formats = ", ".join(FORMATS)
        raise VideoAssemblyError(
            f"Format vidéo invalide. Formats acceptés : {formats}."
        )

    sous_titres = subtitle_path.strip() if isinstance(subtitle_path, str) else ""
    return await asyncio.to_thread(
        _assembler_sync,
        list(image_paths),
        audio_path,
        sous_titres or None,
        output_video_path,
        FORMATS[format_video],
    )
