# Dashboard Enceinte — déploiement Docker

Image unique qui embarque **les trois composants** dans un seul conteneur :

- 🎛️ le **dashboard web** Flask ;
- 🟢 **librespot** (Spotify Connect) ;
- 🔵 **shairport-sync** (AirPlay).

À l'intérieur du conteneur, `supervisord` remplace `systemd` : c'est lui qui
démarre les processus, et le dashboard les pilote (marche/arrêt, état) via
`supervisorctl` grâce à la variable `SERVICE_MANAGER=supervisor`. Le déploiement
natif Raspberry Pi (avec systemd) continue de fonctionner à l'identique.

## Prérequis

- Docker + le plugin Docker Compose v2.
- Un hôte Linux avec une **carte son ALSA** (`/dev/snd`).
- Recommandé : OS **64 bits** (arm64/amd64) pour bénéficier des roues Python
  préconstruites (numpy, cryptography).

## Démarrage rapide

```bash
# Depuis la racine du projet
docker compose up -d --build

# Suivre les logs (dashboard + librespot + shairport-sync)
docker compose logs -f
```

Le dashboard est ensuite accessible sur **http://<ip-de-l-hôte>:5000**.

> Pour personnaliser : `cp .env.docker.example .env`, éditez-le, puis
> décommentez la section `env_file` dans `docker-compose.yml`.

## Pourquoi `network_mode: host` ?

AirPlay et Spotify Connect s'appuient sur la **découverte mDNS/Bonjour**, qui ne
traverse pas le NAT d'un réseau Docker classique. Le mode réseau *host* est donc
**obligatoire**. Conséquence : la directive `ports:` est ignorée et le service
écoute directement sur le port de l'hôte (`FLASK_PORT`, 5000 par défaut).

⚠️ Le conteneur lance son propre `avahi-daemon`. Si l'hôte fait déjà tourner
Avahi, il peut y avoir conflit. Sur une machine dédiée (Raspberry Pi appliance),
désactivez l'Avahi de l'hôte :

```bash
sudo systemctl disable --now avahi-daemon
```

## Audio (ALSA)

Le conteneur accède à la carte son via `devices: ["/dev/snd:/dev/snd"]`.
Choisissez le périphérique de sortie avec `ALSA_DEVICE` (`default`, `hw:0`,
`plughw:1,0`… — voir `aplay -l` sur l'hôte).

Le volume affiché dans le dashboard utilise `amixer` (contrôle `Master`).
Si votre carte n'expose pas de contrôle `Master`, le réglage de volume système
peut échouer — c'est propre au matériel, pas à Docker.

## Variables d'environnement principales

| Variable | Défaut | Rôle |
|---|---|---|
| `SECRET_KEY` | `change-me` | Clé secrète Flask (à changer) |
| `FLASK_PORT` | `5000` | Port d'écoute du dashboard |
| `FLASK_SSL` | `false` | HTTPS auto-signé (requis pour la capture micro navigateur) |
| `ALSA_DEVICE` | `default` | Périphérique ALSA de sortie |
| `AIRPLAY_NAME` | `Enceinte AirPlay` | Nom annoncé en AirPlay |
| `SPOTIFY_NAME` | `Enceinte Spotify` | Nom annoncé en Spotify Connect |
| `SPOTIFY_BITRATE` | `320` | Débit librespot (96/160/320) |
| `SPOTIFY_AUTOSTART` / `AIRPLAY_AUTOSTART` | `true` | Démarrage auto du service |
| `LIBRESPOT_EXTRA_ARGS` | — | Arguments librespot additionnels |

La liste complète est dans `.env.docker.example`.

## Piloter les services à la main (debug)

```bash
docker compose exec enceinte-dashboard supervisorctl status
docker compose exec enceinte-dashboard supervisorctl restart librespot
docker compose exec enceinte-dashboard supervisorctl stop shairport-sync
```

## Sans Docker Compose

```bash
docker build -t dashboard-enceinte .

docker run -d --name dashboard-enceinte \
  --restart unless-stopped \
  --network host \
  --device /dev/snd:/dev/snd \
  -e SECRET_KEY=change-me \
  -e AIRPLAY_NAME="Enceinte Salon" \
  -e SPOTIFY_NAME="Enceinte Salon" \
  -e ALSA_DEVICE=default \
  dashboard-enceinte
```

## Notes

- **librespot** provient du dépôt apt *raspotify* (binaire multi-arch, sans
  compilation Rust).
- **shairport-sync** est la version Debian (AirPlay classique) : les métadonnées
  du titre en cours sont transmises au dashboard via le pipe
  `/tmp/shairport-sync-metadata`.
- Le spectre audio « serveur » (numpy/sounddevice) nécessite un périphérique de
  **capture** ALSA (loopback) ; il reste optionnel et n'empêche pas le reste de
  fonctionner s'il est absent.
