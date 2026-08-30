import os


def _get_bool_env(name, default="false"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

# To generate a new secret key:
# >>> import random, string
# >>> "".join([random.choice(string.printable) for _ in range(24)])
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-on-raspberry")

D_APP_ID = int(os.getenv("D_APP_ID", "1200420960103822"))
SHAIRPORT_SYNC_SERVICE = os.getenv("SHAIRPORT_SYNC_SERVICE", "shairport-sync")
SHAIRPORT_SYNC_SYSTEMD_USER = _get_bool_env("SHAIRPORT_SYNC_SYSTEMD_USER")
SHAIRPORT_SYNC_USE_SUDO = _get_bool_env("SHAIRPORT_SYNC_USE_SUDO")
LIBRESPOT_SERVICE = os.getenv("LIBRESPOT_SERVICE", "librespot")
LIBRESPOT_SYSTEMD_USER = _get_bool_env("LIBRESPOT_SYSTEMD_USER", "true")
LIBRESPOT_USE_SUDO = _get_bool_env("LIBRESPOT_USE_SUDO")
SHAIRPORT_METADATA_PIPE = os.getenv("SHAIRPORT_METADATA_PIPE", "/tmp/shairport-sync-metadata")

# Egaliseur : CamillaDSP expose un WebSocket de pilotage sur la machine.
CAMILLADSP_WS_URL = os.getenv("CAMILLADSP_WS_URL", "ws://127.0.0.1:1234")
CAMILLADSP_CONFIG_FILE = os.getenv("CAMILLADSP_CONFIG_FILE", "/etc/camilladsp/config.yml")
# Bandes reglees par l'utilisateur, rechargees au demarrage du dashboard.
EQ_STATE_FILE = os.getenv("EQ_STATE_FILE", "/etc/camilladsp/eq-state.json")
# Sortie physique choisie (HiFiBerry, jack 3,5 mm, HDMI...).
ACTIVE_OUTPUT_FILE = os.getenv("ACTIVE_OUTPUT_FILE", "/etc/camilladsp/output.json")

# Metadonnees Spotify, ecrites par le hook --onevent de librespot
# (deploy/librespot-event.sh).
LIBRESPOT_METADATA_FILE = os.getenv(
    "LIBRESPOT_METADATA_FILE", "/run/enceinte/spotify-metadata.json"
)

# Gestionnaire de services utilisé pour piloter librespot / shairport-sync.
# "systemd" (Raspberry Pi natif) ou "supervisor" (conteneur Docker).
SERVICE_MANAGER = os.getenv("SERVICE_MANAGER", "systemd").strip().lower()

# Contrôle ALSA utilisé pour le volume. Vide = détection automatique : toutes
# les cartes n'exposent pas "Master" (ex. HiFiBerry DAC+ expose "Digital").
ALSA_MIXER_CONTROL = os.getenv("ALSA_MIXER_CONTROL", "").strip()
ALSA_MIXER_CARD = os.getenv("ALSA_MIXER_CARD", "").strip()
SUPERVISORCTL_BIN = os.getenv("SUPERVISORCTL_BIN", "supervisorctl")
SUPERVISORD_CONFIG = os.getenv("SUPERVISORD_CONFIG", "/etc/supervisor/supervisord.conf")

# --- Renommage + Wi-Fi + point d'acces (helper privilegie enceinte-netctl) ---
# Unique frontiere root du dashboard : renommer les services, se connecter a un
# Wi-Fi, monter un point d'acces. Appele en « sudo -n » (voir dashboard-sudoers).
NETCTL_BIN = os.getenv("NETCTL_BIN", "/usr/local/bin/enceinte-netctl")
NETCTL_USE_SUDO = _get_bool_env("NETCTL_USE_SUDO", "true")

# Nom d'affichage de l'enceinte, ecrit par le dashboard, relu au demarrage.
DEVICE_NAME_FILE = os.getenv("DEVICE_NAME_FILE", "/etc/camilladsp/device-name.json")

# Point d'acces de configuration (1er demarrage sans Wi-Fi connu).
HOTSPOT_SSID = os.getenv("HOTSPOT_SSID", "Enceinte-Setup")
HOTSPOT_PASSWORD = os.getenv("HOTSPOT_PASSWORD", "enceinte-setup")
# Marqueur pose par le helper quand le point d'acces est actif : sert au portail
# captif a savoir s'il doit rediriger les sondes de connectivite.
HOTSPOT_MARKER_FILE = os.getenv("HOTSPOT_MARKER_FILE", "/run/enceinte/hotspot-active")