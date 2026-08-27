# syntax=docker/dockerfile:1

# Image unique regroupant :
#   - le dashboard web Flask
#   - librespot (Spotify Connect)
#   - shairport-sync (AirPlay)
# Le tout orchestré par supervisord (pas de systemd dans le conteneur).
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# --- Paquets système : audio (ALSA), découverte mDNS (avahi), supervisor ---
# libpulse0 est requis car le binaire librespot (build raspotify) est lié à
# libpulse même lorsqu'on utilise la sortie ALSA.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl \
        supervisor \
        dbus \
        avahi-daemon libnss-mdns \
        shairport-sync \
        alsa-utils libasound2 libasound2-plugins libpulse0 \
        libportaudio2 \
    ; \
    rm -rf /var/lib/apt/lists/*

# --- librespot : on extrait uniquement le binaire du paquet raspotify ---
# Le paquet raspotify dépend de systemd (inutile ici) : on ne l'installe donc
# pas, on récupère seulement /usr/bin/librespot. L'architecture est déduite du
# conteneur (dpkg), donc l'image reste multi-arch (amd64 / arm64 / armhf …).
RUN set -eux; \
    rarch="$(dpkg --print-architecture)"; \
    pkgs="https://dtcooper.github.io/raspotify/dists/raspotify/main/binary-${rarch}/Packages"; \
    deb_path="$(curl -fsSL "$pkgs" | awk '/^Package: raspotify$/{f=1} f&&/^Filename:/{print $2; exit}')"; \
    test -n "$deb_path"; \
    curl -fsSL "https://dtcooper.github.io/raspotify/${deb_path}" -o /tmp/raspotify.deb; \
    dpkg-deb --fsys-tarfile /tmp/raspotify.deb | tar -x -C / ./usr/bin/librespot; \
    rm -f /tmp/raspotify.deb; \
    /usr/bin/librespot --version

WORKDIR /app

# Dépendances Python (roues préconstruites pour amd64/arm64)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif
COPY app ./app
COPY static ./static
COPY templates ./templates
COPY run.py ./run.py

# Configuration du conteneur
COPY docker/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/librespot-run.sh /usr/local/bin/librespot-run.sh
COPY docker/rt-exec.sh /usr/local/bin/rt-exec.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/librespot-run.sh /usr/local/bin/rt-exec.sh

# Valeurs par défaut : pilotage des services via supervisor dans le conteneur.
ENV SERVICE_MANAGER=supervisor \
    SUPERVISORD_CONFIG=/etc/supervisor/supervisord.conf \
    LIBRESPOT_SERVICE=librespot \
    LIBRESPOT_SYSTEMD_USER=false \
    LIBRESPOT_USE_SUDO=false \
    SHAIRPORT_SYNC_SERVICE=shairport-sync \
    SHAIRPORT_SYNC_SYSTEMD_USER=false \
    SHAIRPORT_SYNC_USE_SUDO=false \
    SHAIRPORT_METADATA_PIPE=/tmp/shairport-sync-metadata \
    SHAIRPORT_LOG_TO_SERVER_CONSOLE=false \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5000 \
    FLASK_DEBUG=false \
    FLASK_SSL=false \
    SPOTIFY_AUTOSTART=true \
    AIRPLAY_AUTOSTART=true \
    AUDIO_RT_PRIORITY=45 \
    STATE_CACHE_TTL_SECONDS=3.0 \
    SPOTIFY_NAME="Enceinte Spotify" \
    SPOTIFY_BITRATE=320 \
    AIRPLAY_NAME="Enceinte AirPlay" \
    ALSA_DEVICE=default

EXPOSE 5000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
