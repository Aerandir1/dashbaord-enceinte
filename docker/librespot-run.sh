#!/bin/sh
# Lancement de librespot (Spotify Connect) avec sortie ALSA.
# Les paramètres sont pilotés par variables d'environnement.
set -eu

exec librespot \
    --name "${SPOTIFY_NAME:-Enceinte Spotify}" \
    --bitrate "${SPOTIFY_BITRATE:-320}" \
    --backend alsa \
    --device "${ALSA_DEVICE:-default}" \
    ${LIBRESPOT_EXTRA_ARGS:-}
