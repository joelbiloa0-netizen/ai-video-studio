"""Télécharge la voix française Piper utilisée par le fallback TTS local.

Le modèle (~61 Mo) est stocké dans `media_cache/piper/`, dossier ignoré par
Git. Le téléchargement n'est nécessaire qu'une seule fois et ne requiert
aucun droit administrateur.

Usage :
    python backend/telecharger_voix_piper.py
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOSSIER_MODELE = PROJECT_ROOT / "media_cache" / "piper"
NOM_FICHIER = "fr_FR-siwis-medium.onnx"
BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "fr/fr_FR/siwis/medium/"
)


def _telecharger(url: str, destination: Path) -> None:
    """Télécharge un fichier avec barre de progression, sauf s'il existe déjà."""
    if destination.is_file() and destination.stat().st_size > 0:
        print(f"Déjà présent : {destination.name}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporaire = destination.with_suffix(destination.suffix + ".part")
    print(f"Téléchargement de {destination.name}…")
    try:
        with urllib.request.urlopen(url, timeout=120) as reponse, temporaire.open(
            "wb"
        ) as fichier:
            taille = int(reponse.headers.get("Content-Length", 0))
            recu = 0
            while morceau := reponse.read(1024 * 256):
                fichier.write(morceau)
                recu += len(morceau)
                if taille:
                    print(f"  {recu * 100 // taille:3d} %", end="\r", flush=True)
        temporaire.replace(destination)
        print(f"  terminé ({destination.stat().st_size // 1_048_576} Mo)")
    finally:
        temporaire.unlink(missing_ok=True)


def main() -> None:
    """Récupère le modèle ONNX et sa configuration."""
    _telecharger(BASE + NOM_FICHIER, DOSSIER_MODELE / NOM_FICHIER)
    _telecharger(BASE + NOM_FICHIER + ".json", DOSSIER_MODELE / (NOM_FICHIER + ".json"))
    print("Voix locale prête :", DOSSIER_MODELE / NOM_FICHIER)


if __name__ == "__main__":
    main()
