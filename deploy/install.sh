#!/usr/bin/env bash
#
# Installation native (sans Docker) de l'enceinte :
#   - shairport-sync (AirPlay 2) -> compile, service systemd, temps reel
#     (+ nqptp, l'horloge exigee par l'AirPlay 2)
#   - librespot (Spotify Connect)-> service systemd, PAS de temps reel
#     (ou go-librespot, EXPERIMENTAL : volume synchronise avec l'appli Spotify)
#   - dashboard web              -> service systemd
#
# Idempotent : relancer le script met simplement a jour l'installation.
# Usage :  sudo ./deploy/install.sh [--spotify=librespot|go-librespot]
#   Sans --spotify, le script pose la question (ou garde le choix precedent
#   s'il n'est pas lance dans un terminal).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${APP_DIR}/deploy"
ENV_FILE="/etc/default/enceinte"

# go-librespot : version figee, une mise a jour reste un choix delibere.
GO_LIBRESPOT_VERSION="${GO_LIBRESPOT_VERSION:-v0.10.5}"
# shairport-sync (AirPlay 2) et son horloge nqptp, compiles depuis les sources :
# versions figees elles aussi.
SHAIRPORT_SYNC_VERSION="${SHAIRPORT_SYNC_VERSION:-5.6.3}"
NQPTP_VERSION="${NQPTP_VERSION:-1.2.9}"

SPOTIFY_BACKEND_ARG=""
for arg in "$@"; do
    case "$arg" in
        --spotify=*) SPOTIFY_BACKEND_ARG="${arg#--spotify=}" ;;
        -h|--help)
            sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Option inconnue : ${arg} (voir --help)" >&2; exit 1 ;;
    esac
done

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

# Lecture d'une valeur du fichier d'environnement (vide s'il n'existe pas).
# On ne source PAS le fichier : systemd accepte "NOM=Enceinte Salon" sans
# guillemets, mais le shell y verrait la commande "Salon".
read_env() {
    local key="$1" default="$2" val
    val="$(sed -n "s/^[[:space:]]*${key}=//p" "$ENV_FILE" 2>/dev/null | tail -n 1)"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    printf '%s' "${val:-$default}"
}

# --- 0. Choix de l'implementation Spotify Connect ---------------------------
# Priorite : --spotify=, puis SPOTIFY_BACKEND dans l'environnement
# (sudo SPOTIFY_BACKEND=go-librespot ./deploy/install.sh), puis la question
# en mode interactif, puis le choix de l'installation precedente.
PREVIOUS_BACKEND="$(read_env SPOTIFY_BACKEND librespot)"
SPOTIFY_BACKEND="${SPOTIFY_BACKEND_ARG:-${SPOTIFY_BACKEND:-}}"
if [ -z "$SPOTIFY_BACKEND" ] && [ -t 0 ]; then
    case "$PREVIOUS_BACKEND" in go-librespot) default_choice=2 ;; *) default_choice=1 ;; esac
    echo
    echo "Implementation Spotify Connect :"
    echo "  1) librespot     -- eprouvee ; volume Spotify independant du potar du dashboard"
    echo "  2) go-librespot  -- EXPERIMENTAL ; potar du dashboard et volume Spotify synchronises"
    read -r -p "Choix [${default_choice}] : " choice
    case "${choice:-$default_choice}" in
        1) SPOTIFY_BACKEND="librespot" ;;
        2) SPOTIFY_BACKEND="go-librespot" ;;
        *) echo "Choix invalide : ${choice}" >&2; exit 1 ;;
    esac
fi
SPOTIFY_BACKEND="${SPOTIFY_BACKEND:-$PREVIOUS_BACKEND}"
case "$SPOTIFY_BACKEND" in
    librespot|go-librespot) ;;
    *) echo "SPOTIFY_BACKEND invalide : ${SPOTIFY_BACKEND} (librespot ou go-librespot)" >&2; exit 1 ;;
esac
echo "==> Spotify Connect : ${SPOTIFY_BACKEND}"

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
    avahi-daemon libnss-mdns \
    alsa-utils \
    python3-venv python3-dev \
    curl ca-certificates \
    libasound2 libpulse0

# --- 2b. shairport-sync AirPlay 2 + nqptp ------------------------------------
# Le paquet Debian de shairport-sync n'est compile que pour l'AirPlay 1 :
# l'emetteur y impose une latence d'environ 2 s (lecture, pause, morceau
# suivant). En AirPlay 2 ("buffered audio"), le son part presque tout de suite,
# mais il faut compiler shairport-sync et faire tourner nqptp (horloge PTP,
# ports UDP 319 et 320). Les fichiers .version evitent de recompiler (15 a
# 30 min sur un Pi 3) a chaque installation.
SPS_VERSION_FILE=/usr/local/lib/shairport-sync.version
NQPTP_VERSION_FILE=/usr/local/lib/nqptp.version
NEED_SPS=0
NEED_NQPTP=0
if [ ! -x /usr/local/bin/shairport-sync ] \
    || [ "$(cat "$SPS_VERSION_FILE" 2>/dev/null)" != "$SHAIRPORT_SYNC_VERSION" ]; then
    NEED_SPS=1
fi
if [ ! -x /usr/local/bin/nqptp ] \
    || [ "$(cat "$NQPTP_VERSION_FILE" 2>/dev/null)" != "$NQPTP_VERSION" ]; then
    NEED_NQPTP=1
fi

if [ "$NEED_SPS" = 1 ] || [ "$NEED_NQPTP" = 1 ]; then
    echo "==> Outils de compilation pour shairport-sync / nqptp"
    # Liste du BUILD.md de shairport-sync 5.x ; libglib2.0-dev fournit
    # gdbus-codegen (interface MPRIS lue par le dashboard), systemd-dev
    # l'emplacement des unites systemd (requis sur Debian 13).
    apt-get install -y --no-install-recommends \
        build-essential git autoconf automake libtool pkgconf \
        libpopt-dev libconfig-dev libasound2-dev libavahi-client-dev \
        libssl-dev libsoxr-dev libplist-dev libsodium-dev uuid-dev \
        libgcrypt20-dev xxd libplist-utils \
        libavutil-dev libavcodec-dev libavformat-dev \
        libglib2.0-dev systemd-dev
    BUILD_ROOT=/usr/local/src/enceinte
    install -d "$BUILD_ROOT"
    # 2 taches : le Pi 3 n'a que 1 Go de RAM.
    BUILD_JOBS=2
fi

if [ "$NEED_NQPTP" = 1 ]; then
    echo "==> Compilation de nqptp ${NQPTP_VERSION}"
    rm -rf "${BUILD_ROOT}/nqptp"
    git clone -q --depth 1 --branch "$NQPTP_VERSION" \
        https://github.com/mikebrady/nqptp.git "${BUILD_ROOT}/nqptp"
    (
        cd "${BUILD_ROOT}/nqptp"
        autoreconf -fi
        ./configure --with-systemd-startup
        make -j"$BUILD_JOBS"
        make install
    )
    install -d /usr/local/lib
    printf '%s\n' "$NQPTP_VERSION" > "$NQPTP_VERSION_FILE"
else
    echo "==> nqptp ${NQPTP_VERSION} deja present"
fi

if [ "$NEED_SPS" = 1 ]; then
    # Le paquet Debian doit partir : son unite systemd empecherait "make
    # install" de poser celle d'amont (qui lance /usr/local/bin), et deux
    # binaires shairport-sync se marcheraient dessus. La purge retire aussi
    # /etc/shairport-sync.conf, regenere plus bas (etape 6).
    if dpkg -s shairport-sync >/dev/null 2>&1; then
        echo "==> Retrait du paquet Debian shairport-sync (AirPlay 1 seulement)"
        systemctl stop shairport-sync >/dev/null 2>&1 || true
        apt-get purge -y shairport-sync
    fi
    echo "==> Compilation de shairport-sync ${SHAIRPORT_SYNC_VERSION} (AirPlay 2, 15 a 30 min)"
    rm -rf "${BUILD_ROOT}/shairport-sync"
    git clone -q --depth 1 --branch "$SHAIRPORT_SYNC_VERSION" \
        https://github.com/mikebrady/shairport-sync.git "${BUILD_ROOT}/shairport-sync"
    (
        cd "${BUILD_ROOT}/shairport-sync"
        autoreconf -fi
        ./configure --sysconfdir=/etc --with-alsa --with-soxr --with-avahi \
            --with-ssl=openssl --with-systemd-startup --with-airplay-2 \
            --with-metadata --with-metadata-pipe --with-mpris-interface
        make -j"$BUILD_JOBS"
        make install
    )
    printf '%s\n' "$SHAIRPORT_SYNC_VERSION" > "$SPS_VERSION_FILE"
else
    echo "==> shairport-sync ${SHAIRPORT_SYNC_VERSION} deja present"
fi
/usr/local/bin/shairport-sync -V

# --- 3. librespot / go-librespot --------------------------------------------
if [ "$SPOTIFY_BACKEND" = "go-librespot" ]; then
    # Binaire officiel des releases GitHub ; il ne depend que de libasound2.
    # Le fichier .version permet de mettre a jour quand GO_LIBRESPOT_VERSION
    # change, sans retelecharger a chaque installation.
    GO_VERSION_FILE=/usr/local/lib/go-librespot.version
    if [ ! -x /usr/local/bin/go-librespot ] \
        || [ "$(cat "$GO_VERSION_FILE" 2>/dev/null)" != "$GO_LIBRESPOT_VERSION" ]; then
        echo "==> Installation de go-librespot ${GO_LIBRESPOT_VERSION} (experimental)"
        case "$(dpkg --print-architecture)" in
            arm64) GO_ASSET="go-librespot_linux_arm64.tar.gz" ;;
            # Build ARMv6 pour Raspberry Pi : fonctionne aussi sur Pi 2/3 en 32 bits.
            armhf) GO_ASSET="go-librespot_linux_armv6_rpi.tar.gz" ;;
            amd64) GO_ASSET="go-librespot_linux_x86_64.tar.gz" ;;
            *) echo "Architecture $(dpkg --print-architecture) non geree par go-librespot." >&2; exit 1 ;;
        esac
        GO_TMP="$(mktemp -d)"
        curl -fsSL "https://github.com/devgianlu/go-librespot/releases/download/${GO_LIBRESPOT_VERSION}/${GO_ASSET}" \
            -o "${GO_TMP}/go-librespot.tar.gz"
        tar -xzf "${GO_TMP}/go-librespot.tar.gz" -C "$GO_TMP" go-librespot
        install -m 0755 "${GO_TMP}/go-librespot" /usr/local/bin/go-librespot
        install -d /usr/local/lib
        printf '%s\n' "$GO_LIBRESPOT_VERSION" > "$GO_VERSION_FILE"
        rm -rf "$GO_TMP"
    else
        echo "==> go-librespot ${GO_LIBRESPOT_VERSION} deja present"
    fi
fi

# Le depot raspotify ne publie pas de paquet "librespot" autonome : on extrait
# le binaire du paquet raspotify, sans installer sa dependance systemd/config.
if [ "$SPOTIFY_BACKEND" != "librespot" ]; then
    :  # librespot n'est pas utilise.
elif [ ! -x /usr/local/bin/librespot ]; then
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

# VOLUME_MAX_PERCENT=45 etait le defaut d'une version qui bridait le volume
# (remplace par 100, sans plafond) : on retire ce plafond herite.
sed -i "s|^VOLUME_MAX_PERCENT=45$|VOLUME_MAX_PERCENT=100|" "$ENV_FILE"

# Choix de l'etape 0, relu par le dashboard (SPOTIFY_BACKEND) au demarrage.
if grep -q "^SPOTIFY_BACKEND=" "$ENV_FILE"; then
    sed -i "s|^SPOTIFY_BACKEND=.*|SPOTIFY_BACKEND=${SPOTIFY_BACKEND}|" "$ENV_FILE"
else
    printf 'SPOTIFY_BACKEND=%s\n' "$SPOTIFY_BACKEND" >> "$ENV_FILE"
fi

AIRPLAY_NAME="$(read_env AIRPLAY_NAME "$(read_env SPOTIFY_NAME Enceinte)")"
ALSA_DEVICE="$(read_env ALSA_DEVICE default)"
FLASK_PORT="$(read_env FLASK_PORT 5001)"

# Annonce mDNS du protocole Onde : Domus trouve l'enceinte tout seul (voir
# ONDE.md). Le port suit FLASK_PORT ; avahi recharge ses services a chaud.
install -d /etc/avahi/services
sed "s/@ONDE_PORT@/${FLASK_PORT}/" "${DEPLOY_DIR}/onde.service" > /etc/avahi/services/onde.service
chmod 0644 /etc/avahi/services/onde.service

# --- Peripherique ALSA partage (dmix) ---------------------------------------
# Un acces direct au materiel (hw:N) est EXCLUSIF : le premier des deux
# services qui ouvre la carte rend l'autre muet. On passe par dmix, cale sur
# 44100 Hz pour ne rien reechantillonner.
CARD_INDEX="$(printf '%s' "$ALSA_DEVICE" | sed -n 's/^\(plug\)\?hw:\([0-9]\{1,\}\).*/\2/p')"
CARD_INDEX="${CARD_INDEX:-0}"

# --- Carte du DAC, designee par NOM -----------------------------------------
# CamillaDSP et le controle de volume par defaut visent le DAC. On le repere
# par son NOM ALSA et jamais par un index : activer le jack integre du Pi ajoute
# une carte et renumerote les index (le jack peut alors rafler l'index 0), ce
# qui pointerait silencieusement la sortie sur le mauvais peripherique.
DAC_CARD="$(python3 - <<'PYEOF'
import os, re
try:
    content = open("/proc/asound/cards", encoding="utf-8").read()
except OSError:
    content = ""
entries = []
for m in re.finditer(r"^\s*(\d+)\s+\[([^\]]+)\]\s*:\s*(.*)$", content, re.M):
    cid = m.group(2).strip()
    if os.path.isdir(f"/proc/asound/{cid}/pcm0p"):
        entries.append((cid, f"{cid} {m.group(3)}"))
# 1) Un DAC I2S connu (HiFiBerry / pcm512x) en priorite.
for cid, hay in entries:
    if re.search(r"hifiberry|dacp|pcm512", hay, re.I):
        print(cid); raise SystemExit
# 2) Sinon la premiere sortie qui n'est ni la boucle, ni le jack, ni le HDMI.
for cid, hay in entries:
    if re.search(r"loopback|headphones|bcm2835|hdmi|vc4", hay, re.I):
        continue
    print(cid); raise SystemExit
# 3) Dernier recours : la premiere carte de sortie disponible.
if entries:
    print(entries[0][0])
PYEOF
)"
if [ -n "$DAC_CARD" ]; then
    PLAYBACK_DEVICE="hw:CARD=${DAC_CARD},DEV=0"
    CTL_CARD="$DAC_CARD"
else
    # Aucune carte detectee (rare) : on retombe sur l'index, faute de mieux.
    PLAYBACK_DEVICE="hw:${CARD_INDEX},0"
    CTL_CARD="$CARD_INDEX"
fi
echo "==> Sortie CamillaDSP (par nom) : ${PLAYBACK_DEVICE}"

case "$ALSA_DEVICE" in
    hw:*|plughw:*)
        echo "==> ALSA_DEVICE=${ALSA_DEVICE} donne un acces EXCLUSIF a la carte :"
        echo "    AirPlay et Spotify ne peuvent pas coexister. Bascule vers 'default'"
        echo "    (dmix 44100 Hz, sans reechantillonnage), carte ${CARD_INDEX}."
        sed -i "s|^ALSA_DEVICE=.*|ALSA_DEVICE=default|" "$ENV_FILE"
        ALSA_DEVICE="default"
        ;;
esac

echo "==> Configuration ALSA partagee (/etc/asound.conf, ctl -> ${CTL_CARD})"
# Marge fixe de go-librespot (voir asound.conf) : dB -> coefficient lineaire.
GO_LIBRESPOT_GAIN_DB="$(read_env GO_LIBRESPOT_GAIN_DB -26)"
GO_LIBRESPOT_GAIN="$(python3 -c 'import sys
try:
    db = float(sys.argv[1])
except ValueError:
    db = -26.0
db = max(-60.0, min(0.0, db))
print(f"{10 ** (db / 20):.6f}")' "$GO_LIBRESPOT_GAIN_DB")"
echo "==> Marge go-librespot : ${GO_LIBRESPOT_GAIN_DB} dB (x${GO_LIBRESPOT_GAIN})"
sed -e "s|@CTL_CARD@|${CTL_CARD}|g" \
    -e "s|@GO_LIBRESPOT_GAIN@|${GO_LIBRESPOT_GAIN}|g" \
    "${DEPLOY_DIR}/asound.conf" > /etc/asound.conf

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
    sed -e "s|@PLAYBACK_DEVICE@|${PLAYBACK_DEVICE}|g" "${DEPLOY_DIR}/camilladsp.yml" > "$CAMILLA_CONF"
else
    # La config survit aux reinstallations (elle porte l'EQ de l'utilisateur),
    # mais « camilladsp -c » ne valide que la SYNTAXE : une sortie designee par
    # index (hw:0,0) reste acceptee alors qu'elle peut viser la mauvaise carte
    # depuis l'ajout du jack. On migre donc toute sortie indexee vers le nom,
    # sans toucher aux filtres.
    #
    # Passage de la boucle en 32 bits : l'entree de CamillaDSP doit suivre le
    # dmix de /etc/asound.conf, sinon la boucle refuse le format. Fait par
    # simple remplacement de texte (sans PyYAML) dans le bloc "capture", pour
    # ne jamais laisser une ancienne config en 16 bits.
    python3 - "$CAMILLA_CONF" <<'PYEOF'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
m = re.search(r"(?ms)^(\s*)capture:\s*\n(.*?)(?=^\1\S|\Z)", text)
if m and re.search(r"format:\s*S16_LE", m.group(2)):
    block = re.sub(r"format:\s*S16_LE", "format: S32_LE", m.group(2), count=1)
    text = text[:m.start(2)] + block + text[m.end(2):]
    open(path, "w", encoding="utf-8").write(text)
    print("    entree CamillaDSP migree en S32_LE (boucle 32 bits)")
PYEOF
    python3 - "$CAMILLA_CONF" "$PLAYBACK_DEVICE" <<'PYEOF'
import re, sys
try:
    import yaml
except Exception:
    sys.exit(0)  # PyYAML absent : on n'y touche pas plutot que de risquer un degat.
path, device = sys.argv[1], sys.argv[2]
try:
    cfg = yaml.safe_load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)
if not isinstance(cfg, dict):
    sys.exit(0)
pb = (cfg.get("devices") or {}).get("playback") or {}
current = pb.get("device", "")
# Deja par nom : rien a faire.
if "CARD=" in current:
    sys.exit(0)
# Sortie indexee (hw:0,0, plughw:1,0...) -> on la remplace par le nom.
if re.match(r"^\s*(plug)?hw:\d", current):
    pb["device"] = device
    cfg["devices"]["playback"] = pb
    yaml.safe_dump(cfg, open(path, "w", encoding="utf-8"),
                   allow_unicode=True, sort_keys=False)
    print(f"    sortie CamillaDSP migree {current!r} -> {device!r}")
PYEOF
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
# Les deux implementations s'installent sous le MEME nom (librespot.service) :
# sudoers, Conflicts= de shairport-sync, helper de renommage et dashboard
# n'ont pas a savoir laquelle tourne.
if [ "$SPOTIFY_BACKEND" = "go-librespot" ]; then
    SPOTIFY_UNIT_SRC="${DEPLOY_DIR}/go-librespot.service"
    # config.yml est regenere a chaque installation ; state.json (identifiants
    # Spotify), a cote, n'est jamais touche.
    install -d -o "$RUN_USER" -g "$RUN_USER" -m 0750 /var/lib/go-librespot
    install -o "$RUN_USER" -g "$RUN_USER" -m 0640 \
        "${DEPLOY_DIR}/go-librespot.yml" /var/lib/go-librespot/config.yml
else
    SPOTIFY_UNIT_SRC="${DEPLOY_DIR}/librespot.service"
fi
# Un changement d'implementation doit arreter l'ancien processus avant que le
# nouveau ne demarre (meme nom d'unite, binaire different).
if [ "$SPOTIFY_BACKEND" != "$PREVIOUS_BACKEND" ]; then
    systemctl stop librespot >/dev/null 2>&1 || true
fi
sed -e "s|@RUN_USER@|${RUN_USER}|g" -e "s|@APP_DIR@|${APP_DIR}|g" \
    "$SPOTIFY_UNIT_SRC" > /etc/systemd/system/librespot.service
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

# --- 8b. Helper reseau privilegie (renommage + Wi-Fi + point d'acces) -------
# root:root 0755 est CRITIQUE : c'est la seule frontiere root du dashboard.
# S'il etait modifiable par l'utilisateur du dashboard, toute la limitation de
# droits tomberait.
echo "==> Helper reseau (enceinte-netctl)"
install -o root -g root -m 0755 "${DEPLOY_DIR}/enceinte-netctl" /usr/local/bin/enceinte-netctl
install -o root -g root -m 0755 "${DEPLOY_DIR}/enceinte-hotspot.sh" /usr/local/bin/enceinte-hotspot.sh
# Le fichier d'affichage du nom est ecrit par le dashboard : dossier accessible.
install -d -o "$RUN_USER" /etc/camilladsp

# Point d'acces de configuration au demarrage -- OPTIONNEL.
ENABLE_SETUP_HOTSPOT="$(read_env ENABLE_SETUP_HOTSPOT false)"
install -m 0644 "${DEPLOY_DIR}/enceinte-hotspot.service" \
    /etc/systemd/system/enceinte-hotspot.service
case "$ENABLE_SETUP_HOTSPOT" in
    1|true|yes|on|TRUE|True|YES|ON)
        echo "==> Point d'acces de configuration au demarrage : ACTIVE"
        install -d /etc/NetworkManager/dnsmasq-shared.d
        install -m 0644 "${DEPLOY_DIR}/dnsmasq-captive.conf" \
            /etc/NetworkManager/dnsmasq-shared.d/enceinte-captive.conf
        systemctl enable enceinte-hotspot.service >/dev/null 2>&1 || true
        echo "    (portail automatique complet : requiert ENABLE_NGINX=true)"
        ;;
    *)
        echo "==> Point d'acces de configuration au demarrage : desactive"
        echo "    (ENABLE_SETUP_HOTSPOT=true dans ${ENV_FILE} pour l'activer)"
        systemctl disable enceinte-hotspot.service >/dev/null 2>&1 || true
        rm -f /etc/NetworkManager/dnsmasq-shared.d/enceinte-captive.conf
        ;;
esac

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
# nqptp tourne en permanence : shairport-sync peut etre lance a tout moment
# depuis le dashboard, et l'AirPlay 2 ne demarre pas sans son horloge.
for unit in avahi-daemon nqptp camilladsp "$SOURCE_UNIT" dashboard-enceinte; do
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

# --- Reverse proxy nginx (ecran de chargement au boot) -- optionnel ---------
# Sert un ecran de chargement statique INSTANTANE tant que waitress demarre,
# puis proxifie vers le dashboard. waitress continue d'ecouter sur son port :
# l'acces direct reste possible, nginx ajoute juste le port 80 + l'ecran.
ENABLE_NGINX="$(read_env ENABLE_NGINX false)"
NGINX_URL=""
case "$ENABLE_NGINX" in
    1|true|yes|on|TRUE|True|YES|ON)
        echo "==> nginx : reverse proxy + ecran de chargement au demarrage"
        apt-get install -y nginx
        install -d /var/www/enceinte
        install -m 0644 "${DEPLOY_DIR}/splash.html" /var/www/enceinte/splash.html
        install -m 0644 "${DEPLOY_DIR}/nginx-enceinte.conf" \
            /etc/nginx/sites-available/enceinte
        ln -sf /etc/nginx/sites-available/enceinte /etc/nginx/sites-enabled/enceinte
        # Le site par defaut ecoute aussi en 80 (default_server) : conflit.
        rm -f /etc/nginx/sites-enabled/default
        if nginx -t; then
            systemctl enable nginx >/dev/null 2>&1 || true
            systemctl reload nginx 2>/dev/null || systemctl restart nginx || true
            NGINX_URL="http://$(hostname -I | awk '{print $1}')/"
            echo "    OK  nginx -> ${NGINX_URL}"
        else
            failed=1
            echo "    ECHEC  configuration nginx invalide (voir ci-dessus)."
        fi
        ;;
    *)
        echo "==> nginx desactive (ENABLE_NGINX=false)."
        echo "    Pour l'ecran de chargement au boot : ENABLE_NGINX=true dans ${ENV_FILE}."
        ;;
esac

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
if [ -n "$NGINX_URL" ]; then
    echo "     ou via nginx (ecran de chargement au boot) : ${NGINX_URL}"
fi
echo " Config    : ${ENV_FILE}"
echo " Logs      : journalctl -fu dashboard-enceinte"
echo "===================================================================="
