# Déploiement sur Raspberry Pi (enceinte connectée)

## 1) Prérequis
- Raspberry Pi OS à jour
- Python 3 installé
- Accès réseau à la Pi (SSH)

## 2) Copier le projet sur la Raspberry
Exemple:
- `git clone <votre_repo>`
- ou copie des fichiers via SCP/USB

## 3) Installer l'environnement
```bash
cd /chemin/vers/Dashboard\ enceinte
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4) Configurer les variables
```bash
cp .env.example .env
# puis éditer .env avec vos valeurs
```

## 5) Lancer l'application
```bash
source .venv/bin/activate
set -a
source .env
set +a
python run.py
```

Le dashboard sera accessible sur:
- `http://IP_DE_LA_RASPBERRY:5000`

## 6) Démarrage automatique (systemd)
Créer `/etc/systemd/system/enceinte-dashboard.service` :

```ini
[Unit]
Description=Dashboard Enceinte Connectee
After=network.target

[Service]
User=pi
WorkingDirectory=/chemin/vers/Dashboard enceinte
EnvironmentFile=/chemin/vers/Dashboard enceinte/.env
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
ExecStart=/chemin/vers/Dashboard enceinte/.venv/bin/python /chemin/vers/Dashboard enceinte/run.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Puis:
```bash
sudo systemctl daemon-reload
sudo systemctl enable enceinte-dashboard
sudo systemctl start enceinte-dashboard
sudo systemctl status enceinte-dashboard
```

## 7) Option intégration matérielle
Le backend expose des routes API de contrôle:
- `POST /api/power`
- `POST /api/playback`
- `POST /api/volume`

Vous pouvez brancher ces routes à la vraie logique de l’enceinte (Bluetooth, ALSA, PulseAudio, MQTT, etc.) dans `app/views.py`.

## 8) Intégration AirPlay avec shairport-sync
Le bouton `AirPlay` du dashboard peut piloter directement le service `shairport-sync` via `systemctl`.

Installer et activer le service :
```bash
sudo apt install shairport-sync
sudo systemctl enable shairport-sync
sudo systemctl start shairport-sync
```

Variables optionnelles pour le dashboard :
```bash
export SHAIRPORT_SYNC_SERVICE=shairport-sync
export SHAIRPORT_SYNC_SYSTEMD_USER=false
export SHAIRPORT_SYNC_USE_SUDO=true
```

Si l'application Flask tourne sans les droits nécessaires, autoriser uniquement ce service dans `sudoers` par exemple avec `visudo` :
```bash
pi ALL=(root) NOPASSWD: /usr/bin/systemctl start shairport-sync, /usr/bin/systemctl stop shairport-sync, /usr/bin/systemctl is-active shairport-sync
```

Ensuite, le bouton `Activer/Couper AirPlay` démarre ou arrête `shairport-sync`, et l'état affiché dans l'interface reflète l'état réel du service.

## 9) Intégration Spotify avec librespot (service user sans mot de passe)
Créer un service user `librespot` pour éviter `sudo` et mot de passe.

Créer `~/.config/systemd/user/librespot.service` :

```ini
[Unit]
Description=Librespot (Spotify Connect)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/librespot --name "Raspberry Spotify"
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

Si `librespot` est compilé localement (par exemple dans `~/librespot/target/release/librespot`), utilisez ce chemin absolu dans `ExecStart`.

Si les logs montrent `Invalid --backend "alsa"`, retirez l'option `--backend alsa`: votre build ne supporte pas ALSA et utilise généralement `rodio` par défaut.

Activer le service user :
```bash
systemctl --user daemon-reload
systemctl --user enable --now librespot
systemctl --user status librespot
```

Autoriser le service user à rester actif après reboot (même sans session ouverte) :
```bash
sudo loginctl enable-linger pi
```

Variables dashboard (dans `.env`) :
```bash
LIBRESPOT_SERVICE=librespot
LIBRESPOT_SYSTEMD_USER=true
LIBRESPOT_USE_SUDO=false
```

Le bouton `Activer/Couper Spotify` du dashboard démarre ou arrête alors le service user `librespot` sans demander de mot de passe.

## 10) go-librespot : volume synchronisé avec l'appli Spotify (expérimental)

Avec librespot, le volume de l'appli Spotify et le potar du dashboard sont deux étages indépendants qui se multiplient. L'installeur peut installer [go-librespot](https://github.com/devgianlu/go-librespot) à la place, pour que les deux potars se comportent comme des jumeaux : tourner l'un fait bouger l'autre.

```bash
sudo ./deploy/install.sh --spotify=go-librespot   # ou sans option : le script pose la question
sudo ./deploy/install.sh --spotify=librespot      # revenir à librespot
```

Fonctionnement :

- go-librespot tourne en `external_volume` : il n'applique aucun volume au son. Le seul étage reste le mixeur du dashboard, avec sa courbe perceptive.
- Le dashboard écoute le websocket local de go-librespot (`127.0.0.1:3678/events`). Un changement dans l'appli Spotify règle le mixeur, et le potar suit en direct (SSE). Tourner le potar, ou régler le volume par Onde, renvoie la valeur à Spotify (`POST /player/volume`).
- L'unité systemd garde le nom `librespot.service` : sudoers, exclusivité avec AirPlay et renommage n'ont pas changé.
- Fichiers : binaire `/usr/local/bin/go-librespot` (version figée par `GO_LIBRESPOT_VERSION` dans `install.sh`), config `/var/lib/go-librespot/config.yml` (régénérée à chaque installation), identifiants `/var/lib/go-librespot/state.json`.
- Logs : `journalctl -fu librespot` (go-librespot) et `journalctl -fu dashboard-enceinte` (pont).

Différences de niveau sonore entre les deux :

| | librespot | go-librespot |
|---|---|---|
| Normalisation | désactivée par défaut | activée par défaut (−14 LUFS, soit 5 à 8 dB de moins sur un titre récent) : **désactivée** dans `deploy/go-librespot.yml` pour retrouver le niveau de librespot |
| Volume Spotify | atténuation logicielle avant le mixeur | aucune (`external_volume`) : le son arrive à pleine échelle, seul le mixeur l'atténue |
| Potar du dashboard | mixeur du DAC | mixeur du DAC (le même) |

Avec l'appli Spotify et le dashboard tous les deux à 100 %, les deux implémentations donnent donc le même niveau. Si l'ampli sature en haut du potar (craquement puis coupure), on peut plafonner le mixeur avec `VOLUME_MAX_PERCENT` dans `/etc/default/enceinte` (100 = pas de plafond), puis `sudo systemctl restart dashboard-enceinte`.
