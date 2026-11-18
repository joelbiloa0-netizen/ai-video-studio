"""Point d'entrée de l'API du Studio Vidéo IA & Motion Design.

Application FastAPI minimale : elle expose l'état du studio et garantit
l'existence des dossiers de travail dès le démarrage.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .agent_sound import VoiceOverError, generate_voiceover_and_subs
from .agent_writer import FormatVideo, Scenario, generate_script

# backend/main.py -> parent de backend/ = racine du projet
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
TEMP_DIR = PROJECT_ROOT / "temp"


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


@app.get("/")
def read_root() -> dict[str, Any]:
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
            "voix off edge-tts",
            "montage FFmpeg / MoviePy",
        ],
    }


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
