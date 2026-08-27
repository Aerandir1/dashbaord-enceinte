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
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
        supervisor \
        dbus \
        avahi-daemon libnss-mdns \
        shairport-sync \
        alsa-utils libasound2 libasound2-plugins \
        libportaudio2 \
    ; \
    # librespot : binaire "vanilla" distribué via le dépôt apt raspotify
    # (multi-arch : amd64 / arm64 / armhf), évite toute compilation Rust.
    curl -fsSL https://dtcooper.github.io/raspotify/key.asc \
        | gpg --dearmor -o /usr/share/keyrings/raspotify_key.gpg; \
    echo 'deb [signed-by=/usr/share/keyrings/raspotify_key.gpg] https://dtcooper.github.io/raspotify raspotify main' \
        > /etc/apt/sources.list.d/raspotify.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends librespot; \
    apt-get purge -y --auto-remove gnupg; \
    rm -rf /var/lib/apt/lists/*

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
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/librespot-run.sh

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
    SPOTIFY_NAME="Enceinte Spotify" \
    SPOTIFY_BITRATE=320 \
    AIRPLAY_NAME="Enceinte AirPlay" \
    ALSA_DEVICE=default

EXPOSE 5000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
