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

# --- Boucle ALSA + CamillaDSP (egaliseur) -----------------------------------
# Les sources ecrivent dans la boucle, CamillaDSP y lit, egalise, et alimente
# le DAC. Le DAC n'a ainsi qu'un seul client.
# --- Sortie jack 3,5 mm du Raspberry Pi ------------------------------------
# L'audio integre est desactive par defaut des qu'une carte I2S est declaree.
# On le reactive pour pouvoir choisir entre le DAC et le jack, sachant que
# cette sortie est generee en PWM : nettement plus bruitee qu'un DAC I2S.
BOOT_CONFIG=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    [ -f "$candidate" ] && BOOT_CONFIG="$candidate" && break
done
if [ -n "$BOOT_CONFIG" ]; then
    # ATTENTION : sur Raspberry Pi, un "dtparam=" place APRES une ligne
    # "dtoverlay=" est applique a cet overlay, pas au device-tree de base.
    # Ajoute en fin de fichier, dtparam=audio=on est donc sans aucun effet.
    # Il doit imperativement preceder le premier dtoverlay.
    if python3 - "$BOOT_CONFIG" <<'PYEOF'
import re, sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)

param = re.compile(r"^\s*dtparam=audio=", re.I)
overlay = re.compile(r"^\s*dtoverlay=", re.I)

first_overlay = next((i for i, l in enumerate(lines) if overlay.match(l)), len(lines))
existing = [i for i, l in enumerate(lines) if param.match(l)]

# Deja correctement place et actif : rien a faire.
if any(i < first_overlay and l.strip().lower() == "dtparam=audio=on"
       for i, l in ((j, lines[j]) for j in existing)):
    sys.exit(1)

for i in reversed(existing):
    del lines[i]
    if i < first_overlay:
        first_overlay -= 1

lines.insert(first_overlay, "# Sortie analogique integree (jack 3,5 mm).\ndtparam=audio=on\n")
open(path, "w", encoding="utf-8").write("".join(lines))
sys.exit(0)
PYEOF
    then
        echo "==> Sortie jack activee (dtparam=audio=on place avant les overlays)"
        NEEDS_REBOOT=1
    fi

    # Le pilote n'expose la sortie casque que si on la lui demande.
    if [ ! -f /etc/modprobe.d/enceinte-headphones.conf ]; then
        echo "options snd_bcm2835 enable_headphones=1" > /etc/modprobe.d/enceinte-headphones.conf
        NEEDS_REBOOT=1
    fi
fi

echo "==> Boucle ALSA (snd_aloop)"
install -m 0644 "${DEPLOY_DIR}/aloop.conf" /etc/modprobe.d/enceinte-aloop.conf
echo "snd_aloop" > /etc/modules-load.d/enceinte-aloop.conf
if ! grep -q "^Loopback" /proc/asound/cards 2>/dev/null; then
    modprobe -r snd_aloop 2>/dev/null || true
    modprobe snd_aloop || {
        echo "Impossible de charger snd_aloop." >&2
        exit 1
    }
fi
grep -q "Loopback" /proc/asound/cards || {
    echo "La boucle ALSA n'apparait pas dans /proc/asound/cards." >&2
    exit 1
}

if [ ! -x /usr/local/bin/camilladsp ]; then
    echo "==> Installation de CamillaDSP"
    CAMILLA_ARCH="$(dpkg --print-architecture)"
    case "$CAMILLA_ARCH" in
        arm64) CAMILLA_ASSET="camilladsp-linux-aarch64.tar.gz" ;;
        armhf) CAMILLA_ASSET="camilladsp-linux-armv7.tar.gz" ;;
        amd64) CAMILLA_ASSET="camilladsp-linux-amd64.tar.gz" ;;
        *) echo "Architecture ${CAMILLA_ARCH} non geree pour CamillaDSP." >&2; exit 1 ;;
    esac
    CAMILLA_URL="$(curl -fsSL https://api.github.com/repos/HEnquist/camilladsp/releases/latest \
        | grep -o "https://[^\"]*${CAMILLA_ASSET}" | head -n 1)"
    test -n "$CAMILLA_URL"
    TMP_TGZ="$(mktemp)"
    curl -fsSL "$CAMILLA_URL" -o "$TMP_TGZ"
    tar -xzf "$TMP_TGZ" -C /usr/local/bin camilladsp
    rm -f "$TMP_TGZ"
    chmod 0755 /usr/local/bin/camilladsp
fi
/usr/local/bin/camilladsp --version

install -d /etc/camilladsp
# La configuration existante est conservee : le dashboard y ecrit les filtres,
# on ne veut pas effacer l'egaliseur regle par l'utilisateur. Mais une config
# invalide empeche CamillaDSP de demarrer et survivrait a toute reinstallation,
# donc on la remplace apres l'avoir sauvegardee.
CAMILLA_CONF=/etc/camilladsp/config.yml
if [ -f "$CAMILLA_CONF" ] && ! /usr/local/bin/camilladsp -c "$CAMILLA_CONF" >/dev/null 2>&1; then
    echo "    configuration existante invalide -> sauvegardee en config.yml.bak"
    mv -f "$CAMILLA_CONF" "${CAMILLA_CONF}.bak"
fi
if [ ! -f "$CAMILLA_CONF" ]; then
    sed -e "s|@CARD_INDEX@|${CARD_INDEX}|g" "${DEPLOY_DIR}/camilladsp.yml" > "$CAMILLA_CONF"
fi
# Refuse d'aller plus loin avec une configuration que CamillaDSP rejette.
/usr/local/bin/camilladsp -c "$CAMILLA_CONF" >/dev/null
chown -R "$RUN_USER" /etc/camilladsp
install -m 0644 /dev/null /var/log/camilladsp.log
chown "$RUN_USER" /var/log/camilladsp.log

sed -e "s|@RUN_USER@|${RUN_USER}|g" "${DEPLOY_DIR}/camilladsp.service" \
    > /etc/systemd/system/camilladsp.service

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

# Exclusivite : les deux sources se disputent la carte son et se declarent
# mutuellement Conflicts=. Les activer toutes les deux au demarrage
# provoquerait un conflit ; une seule demarre donc au boot, l'autre est lancee
# a la demande depuis le dashboard.
DEFAULT_SOURCE="$(read_env DEFAULT_SOURCE spotify)"
case "$DEFAULT_SOURCE" in
    airplay) SOURCE_UNIT="shairport-sync"; OTHER_UNIT="librespot" ;;
    *)       SOURCE_UNIT="librespot";      OTHER_UNIT="shairport-sync" ;;
esac
echo "    source au demarrage : ${DEFAULT_SOURCE} (${SOURCE_UNIT})"

systemctl disable "$OTHER_UNIT" >/dev/null 2>&1 || true
systemctl stop "$OTHER_UNIT" >/dev/null 2>&1 || true

# CamillaDSP doit demarrer AVANT la source : c'est lui qui consomme la boucle,
# sinon la source ecrit dans le vide.
failed=0
for unit in avahi-daemon camilladsp "$SOURCE_UNIT" dashboard-enceinte; do
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
if [ "${NEEDS_REBOOT:-0}" = "1" ]; then
    echo " ATTENTION : la sortie jack vient d'etre activee dans ${BOOT_CONFIG}."
    echo " Elle n'apparaitra dans le dashboard qu'APRES un redemarrage :"
    echo "     sudo reboot"
    echo
fi
echo " Dashboard : http://$(hostname -I | awk '{print $1}'):${FLASK_PORT:-5001}"
echo " Config    : ${ENV_FILE}"
echo " Logs      : journalctl -fu dashboard-enceinte"
echo "===================================================================="
