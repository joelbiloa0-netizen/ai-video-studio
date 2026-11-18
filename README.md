# Studio Vidéo IA & Motion Design

> Générateur de vidéos courtes et de motion design 100 % gratuit, léger et optimisé pour les environnements CPU à faible mémoire vive.

---

## 🎯 Présentation

**Studio Vidéo IA & Motion Design** est une application web modulaire qui transforme un simple texte en vidéo prête à publier. Elle combine une API Python **FastAPI**, des agents IA légers et une interface web en **HTML/CSS/JavaScript pur**, sans dépendance frontend lourde ni GPU dédié.

Le projet privilégie la simplicité, la gratuité et la sobriété : chaque étape du pipeline (texte → voix → sous-titres → montage) peut être traitée en streaming ou image par image afin de maintenir une empreinte mémoire minimale.

> **État du projet :** arborescence initialisée. Les modules applicatifs sont implémentés progressivement dans les dossiers dédiés.

---

## ✨ Fonctionnalités

- **Multi-formats** : rendu automatique en **9:16** (Stories / Reels / TikTok), **16:9** (YouTube / paysage) et **1:1** (publications carrées).
- **Sous-titres** : génération et incrustation de sous-titres synchronisés, avec style personnalisable (police, couleur, position, contour).
- **Voix off** : synthèse vocale naturelle et gratuite via **edge-tts** (plusieurs voix et langues, dont le français).
- **Montage automatisé** : assemblage, fondus, transitions et habillage graphique pilotés par **FFmpeg** et **MoviePy**.
- **Motion design léger** : animations de texte et d'éléments graphiques calculées image par image, sans moteur de rendu 3D.
- **Interface web minimaliste** : formulaire de création, aperçu des rendus et téléchargement des vidéos générées.
- **Traitement local** : les fichiers temporaires restent sur la machine et sont nettoyés après chaque rendu.

---

## 🏗️ Architecture

```
Studio Vidéo IA
├── backend/     API FastAPI, agents IA et scripts de traitement multimédia
├── frontend/    Interface web (HTML, CSS, JavaScript pur)
├── tests/       Tests unitaires basiques
├── outputs/     Vidéos générées (vide, ignoré par Git)
└── temp/        Fichiers temporaires de montage (vide, ignoré par Git)
```

Le flux de génération suit un pipeline linéaire :

```
Texte  →  Script / découpage  →  Voix off (edge-tts)
       →  Sous-titres synchronisés  →  Rendu FFmpeg / MoviePy
       →  Vidéo finale dans outputs/
```

---

## 🛠️ Pile technique

| Brique | Technologie | Rôle |
| --- | --- | --- |
| API | **FastAPI** + Uvicorn | Endpoints REST et orchestration des rendus |
| Frontend | **HTML5 / CSS3 / JavaScript natif** | Interface web sans framework |
| Voix off | **edge-tts** | Synthèse vocale gratuite et multilingue |
| Montage | **FFmpeg** + **MoviePy** | Encodage, découpe, transitions et incrustation |
| Langage | **Python 3** | Logique métier, agents et scripts multimédia |
| Tests | **unittest / pytest** | Tests unitaires des fonctions critiques |
| Scénario | **Google Gemini** (`google-generativeai`, optionnel) | Génération du scénario JSON structuré |
| Coût | **100 % gratuit** | Aucun service payant ni abonnement requis |

> ℹ️ `edge-tts` utilise le service vocal en ligne de Microsoft Edge : une connexion Internet est nécessaire pour la génération de la voix off. Tout le reste fonctionne localement.

---

## ⚙️ Prérequis

- **Python 3.10+**
- **FFmpeg** installé et accessible dans le `PATH`
- Connexion Internet pour la synthèse vocale `edge-tts`

Vérification rapide de FFmpeg :

```bash
ffmpeg -version
```

---

## 🚀 Installation

```bash
# Cloner le projet
git clone <url-du-depot> ai-video-studio
cd ai-video-studio

# Créer un environnement virtuel
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

# Installer les dépendances
pip install -r backend/requirements.txt
```

Les dossiers `outputs/` et `temp/` sont ignorés par Git : ils sont créés localement **et recréés automatiquement au démarrage de l'API** (voir `backend/main.py`).

### Mode scénariste IA (optionnel)

Le scénario est généré hors ligne par défaut. Pour activer Gemini (quota gratuit) :

```bash
pip install -r backend/requirements-ai.txt
export AI_API_KEY="votre-cle-gemini"
# Optionnel : export AI_MODEL="gemini-2.0-flash"
```

Sans `AI_API_KEY`, si la bibliothèque est absente ou en cas d'erreur réseau, l'agent renvoie un scénario de secours statique et le pipeline continue normalement.

---

## 📖 Utilisation (une fois l'API en place)

```bash
# Lancer le serveur de développement
uvicorn backend.main:app --reload
```

Ouvrir ensuite `frontend/index.html` dans un navigateur (ou via un serveur statique local). Documentation interactive de l'API : `http://localhost:8000/docs`.

Générer un scénario :

```bash
curl -X POST http://localhost:8000/api/generate-script \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Une vidéo de 30 secondes sur les bienfaits de la marche", "format_video": "9:16"}'
```

Générer une voix off et ses sous-titres synchronisés :

```bash
curl -X POST http://localhost:8000/api/generate-audio \
  -H "Content-Type: application/json" \
  -d '{"text": "Bonjour et bienvenue au studio.", "voice": "fr-FR-DeniseNeural", "format_sous_titres": "srt", "dossier": "temp"}'
```

Le format des sous-titres est déduit de l'extension (`.srt` ou `.vtt`). Les fichiers partiels sont supprimés automatiquement en cas d'échec réseau, et l'API renvoie alors une erreur HTTP 503 explicite.

---

## 🧪 Tests

```bash
python -m unittest discover -s tests
# ou
pytest tests/
```

---

## 🧠 Optimisations faible RAM (CPU)

- Aucun modèle de deep learning local lourd : uniquement des outils légers et gratuits.
- Traitement vidéo **image par image** et écriture en flux avec FFmpeg, sans charger la vidéo entière en mémoire.
- Résolutions et durées réduites par défaut pour les rendus de test.
- Fichiers intermédiaires stockés dans `temp/` puis supprimés après chaque génération.
- Aucun build frontend : le navigateur charge directement HTML, CSS et JavaScript natifs.

---

## 🗺️ Roadmap

- [x] Endpoint de génération de script à partir d'un sujet
- [ ] Sélection des formats 9:16 / 16:9 / 1:1 depuis l'interface
- [x] Voix off edge-tts et sous-titres synchronisés SRT / VTT
- [ ] Style de sous-titres configurable
- [x] Choix de la voix et de la langue (edge-tts)
- [ ] File d'attente de rendus et suivi de progression
- [ ] Tests unitaires du pipeline de montage

---

## 📄 Licence

Projet libre et gratuit. Licence à définir selon vos souhaits (MIT proposée par défaut).
