"""Metadonnees et controle AirPlay via l'interface MPRIS de shairport-sync.

shairport-sync 4.x publie nativement, sur le bus D-Bus SYSTEME, les interfaces
« org.mpris.MediaPlayer2.* ». On les interroge avec « busctl --json » (aucune
dependance Python D-Bus a installer) :

  - metadonnees : titre / artiste / album / pochette (mpris:artUrl) ;
  - etat        : PlaybackStatus (Playing / Paused / Stopped) ;
  - controle    : Play / Pause / PlayPause / Next / Previous.

C'est l'approche PROPRE, prevue pour ca -- bien plus fiable que l'analyse du
tube de metadonnees et la resolution DACP par mDNS qui la precedaient.
"""

import json
import os
import subprocess
import threading
import time
from urllib.parse import unquote, urlparse

from app.config import SHAIRPORT_MPRIS_DEST, SHAIRPORT_MPRIS_PATH

PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"

# Petit cache : l'etat est lu a chaque rafraichissement du dashboard, inutile de
# lancer busctl (~50 ms) plus d'une fois par seconde.
_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "data": None}
_CACHE_TTL = 2.0

_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}

_EMPTY = {
    "title": None, "artist": None, "album": None,
    "art_url": None, "trackid": None,
    "status": None, "playing": False, "available": False,
}


class AirplayError(Exception):
    pass


def _busctl(*args, timeout=4):
    return subprocess.run(
        ["busctl", "--system", "--json=short", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _get_property(iface, prop, timeout=4):
    result = _busctl("get-property", SHAIRPORT_MPRIS_DEST, SHAIRPORT_MPRIS_PATH, iface, prop, timeout=timeout)
    if result.returncode != 0:
        raise AirplayError((result.stderr or "").strip() or "busctl a échoué")
    return json.loads(result.stdout).get("data")


def _read():
    try:
        status = _get_property(PLAYER_IFACE, "PlaybackStatus", timeout=2)
        meta = _get_property(PLAYER_IFACE, "Metadata", timeout=3) or {}
    except (AirplayError, ValueError, OSError, subprocess.SubprocessError):
        # Nom non possede (aucune session), busctl absent, sortie illisible... :
        # on renvoie « indisponible », le dashboard basculera sur son repli.
        return dict(_EMPTY)

    def field(key):
        # busctl --json=short encode chaque valeur en {"type": ..., "data": ...}.
        entry = meta.get(key) if isinstance(meta, dict) else None
        return entry.get("data") if isinstance(entry, dict) else None

    artist = field("xesam:artist")
    if isinstance(artist, list):
        artist = ", ".join(a for a in artist if a) or None

    return {
        "title": field("xesam:title"),
        "artist": artist,
        "album": field("xesam:album"),
        "art_url": field("mpris:artUrl"),
        "trackid": field("mpris:trackid"),
        "status": status,
        "playing": status == "Playing",
        "available": True,
    }


def metadata():
    """Metadonnees courantes (avec cache court)."""
    now = time.monotonic()
    with _LOCK:
        if _CACHE["data"] is not None and (now - _CACHE["at"]) < _CACHE_TTL:
            return dict(_CACHE["data"])
    data = _read()
    with _LOCK:
        _CACHE["at"] = time.monotonic()
        _CACHE["data"] = data
    return dict(data)


def command(action):
    """Pilote la lecture AirPlay. Retourne (True, None) ou (False, message)."""
    method = {
        "play": "Play", "pause": "Pause", "toggle": "PlayPause",
        "next": "Next", "previous": "Previous",
    }.get(action)
    if not method:
        return False, "Action AirPlay non supportée."

    try:
        result = _busctl("call", SHAIRPORT_MPRIS_DEST, SHAIRPORT_MPRIS_PATH, PLAYER_IFACE, method)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Contrôle AirPlay impossible : {exc}"

    if result.returncode != 0:
        # Le nom n'est pas possede sur le bus => aucune lecture AirPlay en cours.
        return False, "Contrôle AirPlay indisponible (aucune lecture AirPlay en cours)."

    with _LOCK:
        _CACHE["data"] = None  # relire un etat frais au prochain rafraichissement
    return True, None


def _art_url_fresh():
    """Chemin de la pochette lu a l'instant (sans cache).

    shairport ecrit le fichier de pochette juste APRES les metadonnees : lire
    frais evite de servir la pochette de la piste precedente pendant la
    transition.
    """
    try:
        meta = _get_property(PLAYER_IFACE, "Metadata", timeout=3) or {}
    except (AirplayError, ValueError, OSError, subprocess.SubprocessError):
        return None
    entry = meta.get("mpris:artUrl") if isinstance(meta, dict) else None
    return entry.get("data") if isinstance(entry, dict) else None


def cover_bytes():
    """Octets de la pochette courante + type MIME, ou (None, None).

    Le chemin provient de MPRIS (mpris:artUrl), JAMAIS du client : aucune
    traversee de repertoire possible.
    """
    art = _art_url_fresh() or ""
    if not art.startswith("file://"):
        return None, None
    path = unquote(urlparse(art).path)
    mime = _IMAGE_MIME.get(os.path.splitext(path)[1].lower(), "image/jpeg")
    try:
        with open(path, "rb") as handle:
            return handle.read(), mime
    except OSError:
        return None, None
