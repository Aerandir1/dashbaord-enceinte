# Dashboard Enceinte Connectée

Dashboard Flask pour piloter une enceinte connectée (simulation):
- Lecture / pause / piste suivante-précédente
- Volume et mute
- Services audio (Spotify / AirPlay)
- EQ 5 bandes
- Thème clair/sombre
- Visualisation spectrale (FFT)

## Prérequis
- Python 3.14+

## Installation
```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

## Lancement
```bash
./env/bin/python run.py
```
Puis ouvrir: http://127.0.0.1:5000

## Variables d'environnement
Copier et adapter:
```bash
cp .env.example .env
```
Variables principales:
- `FLASK_HOST` (défaut: `0.0.0.0`)
- `FLASK_PORT` (défaut: `5000`)
- `FLASK_DEBUG` (`true`/`false`)
- `SECRET_KEY`

## Arborescence
- `app/` : backend Flask (routes, état)
- `templates/` : pages HTML
- `static/` : CSS, JS, images
- `run.py` : point d'entrée

## Publication sur GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <URL_DU_REPO_GITHUB>
git push -u origin main
```

> Note: le dossier `env/` et le fichier `.env` sont ignorés via `.gitignore`.
