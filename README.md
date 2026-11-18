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
| Voix off | **edge-tts** + **Piper** (secours local) | Synthèse gratuite, avec bascule automatique hors-ligne |
| Transcription | **faster-whisper** (tiny/base, CPU int8) | Sous-titres SRT d'un rush audio ou vidéo |
| Montage | **FFmpeg** + **MoviePy** | Encodage, découpe, transitions et incrustation |
| Langage | **Python 3** | Logique métier, agents et scripts multimédia |
| Tests | **unittest / pytest** | Tests unitaires des fonctions critiques |
| Scénario | **Google Gemini** (`google-generativeai`, optionnel) | Génération du scénario JSON structuré |
| Coût | **100 % gratuit** | Aucun service payant ni abonnement requis |

> ℹ️ `edge-tts` utilise le service vocal en ligne de Microsoft Edge. S'il est bloqué (HTTP 403, timeout, coupure réseau), l'agent bascule **automatiquement et sans erreur** sur **Piper**, un moteur vocal local CPU qui fonctionne hors-ligne. Les sous-titres sont alors recalculés à partir de la durée réelle de l'audio et du découpage du texte en phrases. Le pipeline audio réussit donc dans tous les cas.

---

## ⚙️ Prérequis

- **Python 3.10+**
- **FFmpeg** : le binaire embarqué fourni par `imageio-ffmpeg` suffit (aucune installation système requise)
- Connexion Internet **facultative** : edge-tts en tire parti, sinon le moteur local Piper prend le relais

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

### Voix locale hors-ligne (recommandé)

Pour garantir un fonctionnement 100 % autonome même si Microsoft bloque edge-tts :

```bash
pip install -r backend/requirements-tts.txt   # piper-tts, aucun sudo requis
python backend/telecharger_voix_piper.py      # voix française, ~61 Mo, une seule fois
```

Le modèle est stocké dans `media_cache/piper/` (ignoré par Git). Sans ce paquet, le pipeline renvoie une erreur 503 explicite si edge-tts est injoignable ; avec lui, l'audio est **toujours** généré.

### Mode scénariste IA (optionnel)

Le scénario est généré hors ligne par défaut. Pour activer Gemini (quota gratuit) :

```bash
pip install -r backend/requirements-ai.txt
export AI_API_KEY="votre-cle-gemini"
# Optionnel : export AI_MODEL="gemini-2.0-flash"
```

Sans `AI_API_KEY`, si la bibliothèque est absente ou en cas d'erreur réseau, l'agent renvoie un scénario de secours statique et le pipeline continue normalement.

---

## 📖 Utilisation

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

L'interface web est servie directement à **http://localhost:8000** (HTML/CSS/JS pur, sans build). Documentation interactive de l'API : `http://localhost:8000/docs`, état du service : `http://localhost:8000/api/status`.

Depuis l'interface : saisissez une idée, choisissez le format (9:16 / 16:9 / 1:1) et cliquez sur « Générer la vidéo ». Les quatre onglets affichent l'avancement (scénario → voix off → montage → résultat). Vous pouvez fournir vos propres images ; sinon, un visuel par scène est généré automatiquement dans le navigateur. Le lecteur HTML5 permet de prévisualiser le rendu et le bouton « Télécharger » récupère le MP4 final.

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

Le format des sous-titres est déduit de l'extension (`.srt` ou `.vtt`). Si edge-tts échoue, le moteur local Piper prend automatiquement le relais et les sous-titres sont reconstruits sur la durée réelle de l'audio — l'appel renvoie donc bien HTTP 200. Les fichiers partiels ne sont supprimés que si **les deux** moteurs échouent (HTTP 503 explicite).

Assembler la vidéo finale (les chemins sont relatifs à la racine du projet) :

```bash
curl -X POST http://localhost:8000/api/assemble-video \
  -H "Content-Type: application/json" \
  -d '{"image_paths": ["temp/img1.png", "temp/img2.png"], "audio_path": "temp/vo.mp3", "subtitle_path": "temp/vo.srt", "format_video": "9:16", "mode_leger": true}'
```

La vidéo est écrite dans `outputs/`, calée sur la durée de la voix off. `mode_leger` (actif par défaut) rend en **720p / 20 fps** — 720×1280 en 9:16, 1280×720 en 16:9, 720×720 en 1:1 — contre 1080p / 24 fps quand il est à `false`. Les images sont redimensionnées sans déformation puis centrées. Si FFmpeg ne dispose pas du filtre `subtitles`, les sous-titres sont livrés en fichier `.srt` séparé à côté de la vidéo.

Les images destinées au montage peuvent être déposées dans `temp/` via `POST /api/upload-images` (multipart/form-data, champ `files`) ; le service renvoie les chemins relatifs à utiliser dans `/api/assemble-video`.

Transcrire un rush (audio ou vidéo) en sous-titres :

```bash
curl -X POST http://localhost:8000/api/transcribe \
  -F "media=@mon-rush.mp4;type=video/mp4" \
  -F "model_size=tiny"
```

Le média est enregistré dans `temp/`, converti en WAV 16 kHz mono par FFmpeg puis transcrit par Whisper **uniquement sur CPU en int8**. La réponse contient `sous_titres_path`, `texte`, `langue`, `duree_secondes`, `media_path` (utile pour l'édition) et `segments` (tableau `{start, end, text}` par phrase). Le champ `model_size` accepte `tiny` (défaut, ~75 Mo) ou `base` (~145 Mo) ; un seul modèle est chargé en mémoire et une seule transcription tourne à la fois pour préserver la RAM. Le premier appel télécharge le modèle depuis Hugging Face (une seule fois).

Éditer un rush en gardant uniquement certaines phrases (la réponse de `/api/transcribe` fournit directement `segments` et `media_path`) :

```bash
curl -X POST http://localhost:8000/api/edit-rush \
  -H "Content-Type: application/json" \
  -d '{"media_path": "temp/rush-xxx.mp4", "kept_segments": [
        {"start": 0.0, "end": 2.2, "text": "Bonjour et bienvenue."},
        {"start": 4.9, "end": 8.0, "text": "À vous de jouer !"}
      ]}'
```

Chaque passage conservé est ré-encodé isolément (`libx264 -preset ultrafast -crf 23 -threads 2 -c:a aac`) puis concaténé sans ré-encodage : les coupes tombent juste, sans décalage d'image clé. La réponse renvoie `video_url` et `sous_titres_url` (servis depuis `/outputs/`) pour l'aperçu et le téléchargement, ainsi que `duree_secondes` et `nombre_segments`. Le SRT livré ne contient que les phrases gardées, avec des horodatages recalculés sur la **durée réellement encodée** de chaque morceau. FFmpeg système est utilisé en priorité, avec repli sur le binaire embarqué imageio s'il est absent.

Dans l'interface, la carte « Nettoyer un rush existant » enchaîne transcription → liste des phrases cochées → bouton « Générer le montage nettoyé ». « Tout garder » / « Tout couper » permettent de préparer rapidement la sélection.

---

## 🧪 Tests

```bash
python -m unittest discover -s tests
# ou
pytest tests/
```

---

## 🧠 Optimisations faible RAM (CPU)

- **Mode léger 720p / 20 fps activé par défaut** dans l'interface et dans l'API, pour rester sous 1 Go de RAM pendant l'encodage.
- Moteur vocal local Piper en int8/ONNX CPU (~61 Mo de modèle), chargé une seule fois puis réutilisé.
- Transcription Whisper en `tiny` int8 : **aucun GPU**, modèle chargé une seule fois, verrou sérialisant les requêtes, WAV temporaire supprimé après chaque appel.
- Édition d'un rush : découpe en subprocess asynchrones, 2 threads FFmpeg maximum par segment, fichiers intermédiaires regroupés dans un dossier `temp/edition-*` supprimé intégralement après le rendu.
- Aucun modèle de deep learning local lourd : uniquement des outils légers et gratuits.
- Traitement vidéo **image par image** et écriture en flux avec FFmpeg, sans charger la vidéo entière en mémoire.
- Résolutions et durées réduites par défaut pour les rendus de test.
- Fichiers intermédiaires stockés dans `temp/` puis supprimés après chaque génération.
- Aucun build frontend : le navigateur charge directement HTML, CSS et JavaScript natifs.
- Rendu MoviePy/FFmpeg **CPU uniquement** (aucun accélérateur NVIDIA), images pré-redimensionnées puis centrées avant encodage.

---

## 🗺️ Roadmap

- [x] Endpoint de génération de script à partir d'un sujet
- [x] Interface web responsive et sélection des formats 9:16 / 16:9 / 1:1
- [x] Assemblage multi-formats (9:16, 16:9, 1:1) avec sous-titres incrustés
- [x] Voix off edge-tts avec fallback local Piper + sous-titres synchronisés SRT / VTT
- [x] Mode léger 720p par défaut pour les machines à faible RAM
- [x] Agent de transcription Whisper CPU int8 d'un rush avec route POST /api/transcribe
- [x] Édition d'un rush par le texte (cases à cocher + POST /api/edit-rush)
- [ ] Style de sous-titres configurable
- [x] Choix de la voix et de la langue (edge-tts)
- [ ] File d'attente de rendus et suivi de progression
- [ ] Tests unitaires du pipeline de montage

---

## 📄 Licence

Projet libre et gratuit. Licence à définir selon vos souhaits (MIT proposée par défaut).
