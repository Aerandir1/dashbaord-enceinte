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

# Gestionnaire de services utilisé pour piloter librespot / shairport-sync.
# "systemd" (Raspberry Pi natif) ou "supervisor" (conteneur Docker).
SERVICE_MANAGER = os.getenv("SERVICE_MANAGER", "systemd").strip().lower()
SUPERVISORCTL_BIN = os.getenv("SUPERVISORCTL_BIN", "supervisorctl")
SUPERVISORD_CONFIG = os.getenv("SUPERVISORD_CONFIG", "/etc/supervisor/supervisord.conf")