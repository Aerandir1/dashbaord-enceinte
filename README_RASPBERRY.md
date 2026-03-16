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
