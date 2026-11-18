"""Point d'entrée de l'API du Studio Vidéo IA & Motion Design.

Application FastAPI minimale : elle expose l'état du studio et garantit
l'existence des dossiers de travail dès le démarrage.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .agent_editor import (
    SegmentsInvalidesError,
    VideoEditError,
    cut_and_merge_rush,
)
from .agent_sound import VoiceOverError, generate_voiceover_and_subs
from .agent_transcriber import TranscriptionError, transcribe_audio_or_video
from .agent_video import VideoAssemblyError, assemble_video
from .agent_writer import FormatVideo, Scenario, generate_script

# backend/main.py -> parent de backend/ = racine du projet
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
TEMP_DIR = PROJECT_ROOT / "temp"
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def ensure_directories() -> None:
    """Crée les dossiers de rendu et temporaires s'ils n'existent pas.

    Ces dossiers sont ignorés par Git ; les recréer au démarrage rend
    l'application autonome après un clonage ou un nettoyage du dépôt.
    """
    for directory in (OUTPUTS_DIR, TEMP_DIR):
        directory.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prépare l'environnement au démarrage et libère les ressources à l'arrêt."""
    ensure_directories()
    yield
    # Les fichiers de temp/ sont nettoyés par le pipeline de montage lui-même.


app = FastAPI(
    title="Studio Vidéo IA & Motion Design",
    description="API de génération de vidéos courtes, 100 % CPU et faible RAM.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/status")
def read_status() -> dict[str, Any]:
    """Renvoie un message de bienvenue et l'état actuel du studio."""
    return {
        "message": "Bienvenue dans le Studio Vidéo IA & Motion Design !",
        "statut": "en ligne",
        "studio": "ai-video-studio",
        "version": "0.1.0",
        "dossiers": {
            "outputs": str(OUTPUTS_DIR),
            "temp": str(TEMP_DIR),
        },
        "fonctionnalites": [
            "formats 9:16, 16:9 et 1:1",
            "génération de scénario structuré (agent IA)",
            "sous-titres synchronisés",
            "voix off edge-tts avec secours local",
            "transcription de rush via Whisper CPU int8",
            "édition d'un rush par le texte (découpe + concaténation)",
            "montage FFmpeg / MoviePy",
        ],
    }


@app.get("/", include_in_schema=False)
def read_index() -> FileResponse:
    """Sert l'interface web à la racine de l'application."""
    return FileResponse(FRONTEND_DIR / "index.html")


class ScriptRequest(BaseModel):
    """Corps de la requête de génération de scénario."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Requête utilisateur décrivant la vidéo souhaitée.",
    )
    format_video: FormatVideo = Field(
        default=FormatVideo.VERTICAL,
        description="Format de rendu : 9:16, 16:9 ou 1:1.",
    )


@app.post(
    "/api/generate-script",
    response_model=Scenario,
    tags=["Scénario"],
    summary="Génère un scénario structuré à partir d'une requête",
)
async def create_script(requete: ScriptRequest) -> Scenario:
    """Délègue la génération à l'agent Scénariste.

    Renvoie toujours un scénario valide : en l'absence de ``AI_API_KEY`` ou en
    cas d'erreur du LLM, l'agent retourne son scénario de secours hors ligne.
    """
    return await generate_script(requete.prompt, requete.format_video.value)


class AudioRequest(BaseModel):
    """Corps de la requête de génération de voix off et de sous-titres."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Texte exact à prononcer par la voix off.",
    )
    voice: str = Field(
        default="fr-FR-DeniseNeural",
        min_length=2,
        max_length=100,
        description="Voix edge-tts à utiliser.",
    )
    format_sous_titres: Literal["srt", "vtt"] = Field(
        default="srt",
        description="Format des sous-titres générés.",
    )
    dossier: Literal["temp", "outputs"] = Field(
        default="temp",
        description="Dossier de destination des fichiers générés.",
    )


class AudioResponse(BaseModel):
    """Chemins relatifs des fichiers générés."""

    audio_path: str
    sous_titres_path: str
    voix: str
    format_sous_titres: str


@app.post(
    "/api/generate-audio",
    response_model=AudioResponse,
    tags=["Audio"],
    summary="Génère une voix off et ses sous-titres synchronisés",
)
async def create_audio(requete: AudioRequest) -> AudioResponse:
    """Synthétise le texte en MP3 et produit les sous-titres associés.

    En cas d'échec réseau d'edge-tts, renvoie une erreur HTTP 503 explicite
    sans laisser de fichiers partiels dans `temp/` ou `outputs/`.
    """
    dossier_cible = PROJECT_ROOT / requete.dossier
    identifiant = uuid4().hex[:12]
    chemin_audio = dossier_cible / f"voiceover-{identifiant}.mp3"
    chemin_sous_titres = (
        dossier_cible / f"voiceover-{identifiant}.{requete.format_sous_titres}"
    )

    try:
        audio, sous_titres = await generate_voiceover_and_subs(
            requete.text,
            str(chemin_audio),
            str(chemin_sous_titres),
            requete.voice,
        )
    except VoiceOverError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Synthèse vocale indisponible : {exc}",
        ) from exc

    return AudioResponse(
        audio_path=audio.relative_to(PROJECT_ROOT).as_posix(),
        sous_titres_path=sous_titres.relative_to(PROJECT_ROOT).as_posix(),
        voix=requete.voice,
        format_sous_titres=requete.format_sous_titres,
    )


def _chemin_dans_projet(chemin: str) -> Path:
    """Résout un chemin fourni par le client en interdisant toute sortie du projet."""
    chemin_resolu = Path(chemin)
    if not chemin_resolu.is_absolute():
        chemin_resolu = PROJECT_ROOT / chemin_resolu
    chemin_resolu = chemin_resolu.resolve()
    if not chemin_resolu.is_relative_to(PROJECT_ROOT):
        raise HTTPException(status_code=400, detail="Chemin hors du projet.")
    return chemin_resolu


class VideoRequest(BaseModel):
    """Corps de la requête d'assemblage vidéo."""

    model_config = ConfigDict(extra="forbid")

    image_paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Chemins des images, dans l'ordre d'apparition.",
    )
    audio_path: str = Field(..., description="Chemin de la voix off MP3.")
    subtitle_path: str | None = Field(
        default=None,
        description="Chemin d'un fichier SRT/VTT (optionnel).",
    )
    format_video: FormatVideo = Field(
        default=FormatVideo.VERTICAL,
        description="Format de rendu : 9:16, 16:9 ou 1:1.",
    )
    mode_leger: bool = Field(
        default=True,
        description="Rendu 720p/20 fps (défaut) au lieu du 1080p/24 fps.",
    )


class VideoResponse(BaseModel):
    """Chemin de la vidéo finale et informations de rendu."""

    video_path: str
    duree_secondes: float
    sous_titres_integres: bool
    sous_titres_path: str | None = None
    mode_leger: bool = True


@app.post(
    "/api/assemble-video",
    response_model=VideoResponse,
    tags=["Vidéo"],
    summary="Assemble images, voix off et sous-titres en vidéo finale",
)
async def create_video(requete: VideoRequest) -> VideoResponse:
    """Orchestre le montage CPU via MoviePy/FFmpeg dans `outputs/`.

    Les chemins fournis sont cantonnés à la racine du projet. La durée de la
    vidéo est strictement calée sur celle de la voix off.
    """
    images = [_chemin_dans_projet(chemin) for chemin in requete.image_paths]
    audio = _chemin_dans_projet(requete.audio_path)
    sous_titres = (
        _chemin_dans_projet(requete.subtitle_path) if requete.subtitle_path else None
    )

    for image in images:
        if not image.is_file():
            raise HTTPException(status_code=400, detail=f"Image introuvable : {image}")
    if not audio.is_file():
        raise HTTPException(status_code=400, detail=f"Audio introuvable : {audio}")
    if sous_titres is not None and not sous_titres.is_file():
        raise HTTPException(
            status_code=400, detail=f"Sous-titres introuvables : {sous_titres}"
        )

    sortie = PROJECT_ROOT / "outputs" / f"video-{uuid4().hex[:12]}.mp4"
    try:
        resultat = await assemble_video(
            [str(image) for image in images],
            str(audio),
            str(sous_titres) if sous_titres else "",
            str(sortie),
            requete.format_video.value,
            requete.mode_leger,
        )
    except VideoAssemblyError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Impossible d'assembler la vidéo : {exc}",
        ) from exc

    return VideoResponse(
        video_path=resultat.chemin_video.relative_to(PROJECT_ROOT).as_posix(),
        duree_secondes=round(resultat.duree_secondes, 3),
        sous_titres_integres=resultat.sous_titres_integres,
        mode_leger=requete.mode_leger,
        sous_titres_path=(
            resultat.chemin_sous_titres.relative_to(PROJECT_ROOT).as_posix()
            if resultat.chemin_sous_titres
            else None
        ),
    )


@app.post(
    "/api/upload-images",
    tags=["Vidéo"],
    summary="Dépose des images de montage dans temp/",
)
async def upload_images(
    files: list[UploadFile] = File(..., max_length=50),
) -> dict[str, Any]:
    """Reçoit des images et les enregistre dans `temp/` pour le montage."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    chemins: list[str] = []
    for fichier in files:
        type_media = (fichier.content_type or "").lower()
        if not type_media.startswith("image/"):
            raise HTTPException(
                status_code=400, detail=f"Fichier non image : {fichier.filename}"
            )
        suffixe = Path(fichier.filename or "").suffix.lower()
        if suffixe not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            suffixe = ".png"
        chemin = TEMP_DIR / f"upload-{uuid4().hex[:12]}{suffixe}"
        try:
            donnees = await fichier.read()
            if not donnees:
                raise HTTPException(
                    status_code=400, detail=f"Fichier vide : {fichier.filename}"
                )
            chemin.write_bytes(donnees)
        finally:
            await fichier.close()
        chemins.append(chemin.relative_to(PROJECT_ROOT).as_posix())
    return {"image_paths": chemins}


EXTENSIONS_MEDIA = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".flac", ".opus", ".wma",
    ".aiff", ".mp4", ".mov", ".mkv", ".avi", ".webm", ".mpeg", ".mpg", ".3gp",
}
TAILLE_MAX_MEDIA = 200 * 1024 * 1024
"""Limite de 200 Mo : évite de saturer le disque et la RAM avec un gros rush."""


class TranscriptionResponse(BaseModel):
    """SRT généré, texte intégral et métadonnées de la transcription."""

    sous_titres_path: str
    texte: str
    langue: str | None = None
    duree_secondes: float
    modele: str
    media_path: str
    segments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Phrases transcrites avec start/end/text pour l'édition.",
    )


@app.post(
    "/api/transcribe",
    response_model=TranscriptionResponse,
    tags=["Transcription"],
    summary="Transcrit un rush audio/vidéo en sous-titres SRT (Whisper CPU)",
)
async def transcribe_media(
    media: UploadFile = File(..., description="Fichier audio ou vidéo à transcrire."),
    model_size: Literal["tiny", "base"] = Form(
        "tiny",
        description="Modèle Whisper : tiny (~75 Mo, défaut) ou base (~145 Mo).",
    ),
) -> TranscriptionResponse:
    """Dépose un rush dans `temp/`, le transcrit et renvoie le SRT + le texte.

    L'extraction audio et l'inférence tournent sur CPU en int8, dans un thread
    dédié. Le rush est conservé dans `temp/` pour alimenter le montage.
    """
    type_media = (media.content_type or "").lower()
    suffixe = Path(media.filename or "").suffix.lower()
    if suffixe not in EXTENSIONS_MEDIA and not type_media.startswith(
        ("audio/", "video/")
    ):
        raise HTTPException(
            status_code=400,
            detail="Format de média non reconnu (audio ou vidéo attendu).",
        )
    if suffixe not in EXTENSIONS_MEDIA:
        suffixe = ".mp4" if type_media.startswith("video/") else ".mp3"

    identifiant = uuid4().hex[:12]
    chemin_media = TEMP_DIR / f"rush-{identifiant}{suffixe}"
    chemin_srt = TEMP_DIR / f"transcription-{identifiant}.srt"
    reussi = False

    try:
        taille = 0
        with chemin_media.open("wb") as fichier:
            while morceau := await media.read(1024 * 1024):
                taille += len(morceau)
                if taille > TAILLE_MAX_MEDIA:
                    raise HTTPException(
                        status_code=413,
                        detail="Fichier trop volumineux (limite : 200 Mo).",
                    )
                fichier.write(morceau)
        if taille == 0:
            raise HTTPException(status_code=400, detail="Le fichier envoyé est vide.")

        resultat = await transcribe_audio_or_video(
            str(chemin_media), str(chemin_srt), model_size
        )
        reussi = True
    except TranscriptionError as exc:
        raise HTTPException(
            status_code=500, detail=f"Transcription impossible : {exc}"
        ) from exc
    finally:
        await media.close()
        if not reussi:
            chemin_media.unlink(missing_ok=True)
            chemin_srt.unlink(missing_ok=True)

    return TranscriptionResponse(
        sous_titres_path=resultat.chemin_srt.relative_to(PROJECT_ROOT).as_posix(),
        texte=resultat.texte,
        langue=resultat.langue,
        duree_secondes=round(resultat.duree_secondes, 3),
        modele=model_size,
        media_path=chemin_media.relative_to(PROJECT_ROOT).as_posix(),
        segments=resultat.segments,
    )


class SegmentConserve(BaseModel):
    """Un passage du rush à garder, validé par l'utilisateur."""

    start: float = Field(..., ge=0, description="Début en secondes.")
    end: float = Field(..., gt=0, description="Fin en secondes.")
    text: str = Field(default="", description="Texte du sous-titre associé.")


class EditRushRequest(BaseModel):
    """Corps de la requête d'édition par le texte."""

    model_config = ConfigDict(extra="forbid")

    media_path: str = Field(..., description="Chemin du rush dans le projet.")
    kept_segments: list[SegmentConserve] = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Segments conservés, dans leur ordre d'apparition.",
    )


class EditRushResponse(BaseModel):
    """Vidéo nettoyée et sous-titres recalculés, prêts pour l'aperçu web."""

    video_path: str
    video_url: str
    sous_titres_path: str
    sous_titres_url: str
    duree_secondes: float
    nombre_segments: int


@app.post(
    "/api/edit-rush",
    response_model=EditRushResponse,
    tags=["Vidéo"],
    summary="Découpe un rush selon les segments conservés et concatène le tout",
)
async def edit_rush(requete: EditRushRequest) -> EditRushResponse:
    """Monte les passages gardés et livre la vidéo + le SRT dans `outputs/`."""
    media = _chemin_dans_projet(requete.media_path)
    if not media.is_file():
        raise HTTPException(status_code=400, detail=f"Rush introuvable : {media}")

    identifiant = uuid4().hex[:12]
    sortie = OUTPUTS_DIR / f"clean-{identifiant}.mp4"
    sous_titres = OUTPUTS_DIR / f"clean-{identifiant}.srt"

    try:
        resultat = await cut_and_merge_rush(
            str(media),
            [segment.model_dump() for segment in requete.kept_segments],
            str(sortie),
            str(sous_titres),
        )
    except SegmentsInvalidesError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VideoEditError as exc:
        raise HTTPException(
            status_code=500, detail=f"Échec de l'édition du rush : {exc}"
        ) from exc

    return EditRushResponse(
        video_path=resultat.chemin_video.relative_to(PROJECT_ROOT).as_posix(),
        video_url=f"/outputs/{resultat.chemin_video.name}",
        sous_titres_path=resultat.chemin_srt.relative_to(PROJECT_ROOT).as_posix(),
        sous_titres_url=f"/outputs/{resultat.chemin_srt.name}",
        duree_secondes=round(resultat.duree_secondes, 3),
        nombre_segments=resultat.nombre_segments,
    )


# Les montages statiques sont déclarés en dernier : les routes API restent prioritaires.
ensure_directories()
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
