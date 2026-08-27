#!/bin/sh
# Point d'entrée du conteneur : prépare l'environnement (dbus, avahi, ALSA,
# config shairport-sync générée depuis les variables d'env) puis lance la
# commande fournie (supervisord par défaut).
set -eu

# Répertoires runtime nécessaires à dbus / avahi / supervisor / librespot
mkdir -p /var/run/dbus /run/avahi-daemon /var/log/supervisor /var/cache/librespot

# machine-id requis par dbus et avahi
if [ ! -s /etc/machine-id ]; then
    dbus-uuidgen --ensure=/etc/machine-id || true
fi
dbus-uuidgen --ensure || true

# avahi tourne sous l'utilisateur "avahi" et a besoin de son répertoire runtime
if id avahi >/dev/null 2>&1; then
    chown avahi:avahi /run/avahi-daemon 2>/dev/null || true
fi

# Génération de la configuration shairport-sync à partir des variables d'env.
# Le pipe de métadonnées est lu par le dashboard (SHAIRPORT_METADATA_PIPE).
cat > /etc/shairport-sync.conf <<EOF
general = {
  name = "${AIRPLAY_NAME:-Enceinte AirPlay}";
  output_backend = "alsa";
};

alsa = {
  output_device = "${ALSA_DEVICE:-default}";
};

metadata = {
  enabled = "yes";
  include_cover_art = "no";
  pipe_name = "${SHAIRPORT_METADATA_PIPE:-/tmp/shairport-sync-metadata}";
  pipe_timeout = 5000;
};
EOF

exec "$@"
