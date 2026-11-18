"""Point d'entrée de l'API du Studio Vidéo IA & Motion Design.

Application FastAPI minimale : elle expose l'état du studio et garantit
l'existence des dossiers de travail dès le démarrage.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

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
            "sous-titres synchronisés",
            "voix off edge-tts",
            "montage FFmpeg / MoviePy",
        ],
    }
