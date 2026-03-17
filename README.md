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

## Variables d'environnement
Copier et adapter:
```bash
cp .env.example .env
```
Variables principales:
- `FLASK_HOST` (défaut: `0.0.0.0`)
- `FLASK_PORT` (défaut: `5000`)
- `FLASK_DEBUG` (`true`/`false`)
- `FLASK_SSL` (`true`/`false`, défaut `true`)
- `SECRET_KEY`

## Arborescence
- `app/` : backend Flask (routes, état)
- `templates/` : pages HTML
- `static/` : CSS, JS, images
- `run.py` : point d'entrée
