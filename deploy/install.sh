#!/usr/bin/env bash
#
# Installation native (sans Docker) de l'enceinte :
#   - shairport-sync (AirPlay)   -> service systemd, ordonnancement temps reel
#   - librespot (Spotify Connect)-> service systemd, PAS de temps reel
#   - dashboard web              -> service systemd
#
# Idempotent : relancer le script met simplement a jour l'installation.
# Usage :  sudo ./deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${APP_DIR}/deploy"
ENV_FILE="/etc/default/enceinte"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ce script doit etre lance avec sudo." >&2
    exit 1
fi

# Utilisateur non-root qui fera tourner le dashboard et librespot.
RUN_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
if [ "$RUN_USER" = "root" ]; then
    echo "Impossible de determiner un utilisateur non-root. Lancez : sudo ./deploy/install.sh" >&2
    exit 1
fi

echo "==> Installation dans ${APP_DIR} pour l'utilisateur ${RUN_USER}"

# --- 1. Arret de l'ancienne pile Docker si elle tourne -----------------------
if command -v docker >/dev/null 2>&1; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^dashboard-enceinte$'; then
        echo "==> Arret du conteneur Docker (il occupe la carte son et le mDNS)"
        (cd "$APP_DIR" && docker compose down 2>/dev/null) || docker rm -f dashboard-enceinte
    fi
fi

# --- 2. Paquets systeme ------------------------------------------------------
echo "==> Installation des paquets systeme"
apt-get update -qq
apt-get install -y --no-install-recommends \
    shairport-sync \
    avahi-daemon libnss-mdns \
    alsa-utils \
    python3-venv python3-dev \
    curl ca-certificates \
    libasound2 libpulse0

# --- 3. librespot ------------------------------------------------------------
# Le depot raspotify ne publie pas de paquet "librespot" autonome : on extrait
# le binaire du paquet raspotify, sans installer sa dependance systemd/config.
if [ ! -x /usr/local/bin/librespot ]; then
    echo "==> Installation du binaire librespot"
    ARCH="$(dpkg --print-architecture)"
    PKGS_URL="https://dtcooper.github.io/raspotify/dists/raspotify/main/binary-${ARCH}/Packages"
    DEB_PATH="$(curl -fsSL "$PKGS_URL" | awk '/^Package: raspotify$/{f=1} f&&/^Filename:/{print $2; exit}')"
    if [ -z "$DEB_PATH" ]; then
        echo "Impossible de localiser le paquet raspotify pour l'architecture ${ARCH}." >&2
        exit 1
    fi
    TMP_DEB="$(mktemp)"
    curl -fsSL "https://dtcooper.github.io/raspotify/${DEB_PATH}" -o "$TMP_DEB"
    dpkg-deb --fsys-tarfile "$TMP_DEB" | tar -x -C /tmp ./usr/bin/librespot
    install -m 0755 /tmp/usr/bin/librespot /usr/local/bin/librespot
    rm -rf "$TMP_DEB" /tmp/usr
    /usr/local/bin/librespot --version
else
    echo "==> librespot deja present (/usr/local/bin/librespot)"
fi

install -d -o "$RUN_USER" -g "$RUN_USER" /var/cache/librespot

# Hook de metadonnees : librespot l'appelle a chaque changement de piste et il
# ecrit le titre en cours pour le dashboard.
install -m 0755 "${DEPLOY_DIR}/librespot-event.sh" /usr/local/bin/librespot-event.sh

# --- 4. Environnement Python -------------------------------------------------
echo "==> Environnement Python"
if [ ! -d "${APP_DIR}/.venv" ]; then
    sudo -u "$RUN_USER" python3 -m venv "${APP_DIR}/.venv"
fi
sudo -u "$RUN_USER" "${APP_DIR}/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "${APP_DIR}/.venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"

# --- 5. Fichier d'environnement ---------------------------------------------
# On ne l'ecrase jamais : il contient les reglages personnalises.
if [ ! -f "$ENV_FILE" ]; then
    echo "==> Creation de ${ENV_FILE}"
    install -m 0644 "${DEPLOY_DIR}/enceinte.env" "$ENV_FILE"
    # Cle secrete Flask aleatoire.
    SECRET="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${SECRET}/" "$ENV_FILE"
else
    # On ne touche pas aux valeurs existantes, mais une mise a jour du projet
    # peut introduire de nouvelles cles : on ajoute uniquement celles-la.
    echo "==> ${ENV_FILE} existe deja : ajout des cles manquantes uniquement"
    while IFS= read -r line; do
        case "$line" in ''|\#*) continue ;; esac
        key="${line%%=*}"
        if ! grep -q "^${key}=" "$ENV_FILE"; then
            printf '%s\n' "$line" >> "$ENV_FILE"
            echo "    + ${key}"
        fi
    done < "${DEPLOY_DIR}/enceinte.env"
fi

# Lecture des valeurs necessaires a la config shairport-sync.
# On ne source PAS le fichier : systemd accepte "NOM=Enceinte Salon" sans
# guillemets, mais le shell y verrait la commande "Salon".
read_env() {
    local key="$1" default="$2" val
    val="$(sed -n "s/^[[:space:]]*${key}=//p" "$ENV_FILE" | tail -n 1)"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    printf '%s' "${val:-$default}"
}

AIRPLAY_NAME="$(read_env AIRPLAY_NAME "$(read_env SPOTIFY_NAME Enceinte)")"
ALSA_DEVICE="$(read_env ALSA_DEVICE default)"
FLASK_PORT="$(read_env FLASK_PORT 5001)"

# --- Peripherique ALSA partage (dmix) ---------------------------------------
# Un acces direct au materiel (hw:N) est EXCLUSIF : le premier des deux
# services qui ouvre la carte rend l'autre muet. On passe par dmix, cale sur
# 44100 Hz pour ne rien reechantillonner.
CARD_INDEX="$(printf '%s' "$ALSA_DEVICE" | sed -n 's/^\(plug\)\?hw:\([0-9]\{1,\}\).*/\2/p')"
CARD_INDEX="${CARD_INDEX:-0}"

case "$ALSA_DEVICE" in
    hw:*|plughw:*)
        echo "==> ALSA_DEVICE=${ALSA_DEVICE} donne un acces EXCLUSIF a la carte :"
        echo "    AirPlay et Spotify ne peuvent pas coexister. Bascule vers 'default'"
        echo "    (dmix 44100 Hz, sans reechantillonnage), carte ${CARD_INDEX}."
        sed -i "s|^ALSA_DEVICE=.*|ALSA_DEVICE=default|" "$ENV_FILE"
        ALSA_DEVICE="default"
        ;;
esac

echo "==> Configuration ALSA partagee (/etc/asound.conf, carte ${CARD_INDEX})"
sed -e "s|@CARD_INDEX@|${CARD_INDEX}|g" "${DEPLOY_DIR}/asound.conf" > /etc/asound.conf

# --- 6. Configuration shairport-sync ----------------------------------------
echo "==> Configuration shairport-sync (sortie ${ALSA_DEVICE})"
sed -e "s|@AIRPLAY_NAME@|${AIRPLAY_NAME}|g" \
    -e "s|@ALSA_DEVICE@|${ALSA_DEVICE}|g" \
    "${DEPLOY_DIR}/shairport-sync.conf" > /etc/shairport-sync.conf

install -d /etc/systemd/system/shairport-sync.service.d
install -m 0644 "${DEPLOY_DIR}/shairport-sync-override.conf" \
    /etc/systemd/system/shairport-sync.service.d/override.conf

# --- 7. Unites systemd -------------------------------------------------------
echo "==> Installation des unites systemd"
sed -e "s|@RUN_USER@|${RUN_USER}|g" -e "s|@APP_DIR@|${APP_DIR}|g" \
    "${DEPLOY_DIR}/librespot.service" > /etc/systemd/system/librespot.service
sed -e "s|@RUN_USER@|${RUN_USER}|g" -e "s|@APP_DIR@|${APP_DIR}|g" \
    "${DEPLOY_DIR}/dashboard-enceinte.service" > /etc/systemd/system/dashboard-enceinte.service

# --- 8. Droits sudo du dashboard --------------------------------------------
echo "==> Droits sudo (limites aux deux services audio)"
sed -e "s|@RUN_USER@|${RUN_USER}|g" \
    "${DEPLOY_DIR}/dashboard-sudoers" > /etc/sudoers.d/dashboard-enceinte
chmod 0440 /etc/sudoers.d/dashboard-enceinte
# Un fichier sudoers invalide casserait sudo : on valide avant de continuer.
if ! visudo -cf /etc/sudoers.d/dashboard-enceinte >/dev/null; then
    rm -f /etc/sudoers.d/dashboard-enceinte
    echo "Fichier sudoers invalide, installation annulee." >&2
    exit 1
fi

# --- 9. Activation -----------------------------------------------------------
echo "==> Activation des services"
systemctl daemon-reload

failed=0
for unit in avahi-daemon shairport-sync librespot dashboard-enceinte; do
    systemctl enable "$unit" >/dev/null 2>&1 || true
    systemctl restart "$unit" || true
    sleep 1
    if systemctl is-active --quiet "$unit"; then
        echo "    OK  ${unit}"
    else
        failed=1
        echo "    ECHEC  ${unit} -- dernieres lignes du journal :"
        journalctl -u "$unit" -n 12 --no-pager -o cat 2>/dev/null | sed 's/^/        /'
    fi
done

echo
echo "===================================================================="
if [ "$failed" -eq 0 ]; then
    echo " Installation terminee : les trois services sont actifs."
else
    echo " Installation terminee AVEC DES ERREURS (voir ci-dessus)."
fi
echo
echo " Dashboard : http://$(hostname -I | awk '{print $1}'):${FLASK_PORT:-5001}"
echo " Config    : ${ENV_FILE}"
echo " Logs      : journalctl -fu dashboard-enceinte"
echo "===================================================================="
