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