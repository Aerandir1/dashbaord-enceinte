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
- `SPOTIFY_BACKEND` : `librespot` (défaut) ou `go-librespot` (expérimental, voir ci-dessous)
- `GO_LIBRESPOT_PORT` : port de l'API locale de go-librespot (défaut `3678`)

## Spotify Connect : librespot ou go-librespot (expérimental)

L'installeur (`sudo ./deploy/install.sh`) demande quelle implémentation installer, ou la prend
de `--spotify=librespot|go-librespot` :

- **librespot** (défaut, éprouvé) : le volume de l'appli Spotify est indépendant du potar du dashboard.
- **go-librespot** (expérimental) : le potar du dashboard et le volume Spotify sont **jumeaux**.
  Tourner l'un fait bouger l'autre. go-librespot tourne en `external_volume`, donc le mixeur du
  dashboard reste le seul étage de volume, et `app/golibrespot.py` synchronise les deux sens par
  l'API locale de go-librespot (websocket `/events` et `POST /player/volume`).

Détails, fichiers installés et logs : [README_RASPBERRY.md, section 10](README_RASPBERRY.md).

## Arborescence
- `app/` : backend Flask (routes, état) ; `app/golibrespot.py` : pont go-librespot (volume jumeau)
- `templates/` : pages HTML
- `static/` : CSS, JS, images
- `run.py` : point d'entrée

## Pilotage par Domus (protocole Onde)

L'enceinte parle **Onde** (`/onde/v1`) : Domus la découvre par mDNS et pilote volume,
lecture, source, et affiche le titre et la pochette en direct. Spécification et notes
d'implémentation : [ONDE.md](ONDE.md).
