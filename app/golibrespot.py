"""Pont go-librespot (EXPERIMENTAL) : volume jumeau dashboard <-> Spotify Connect.

Actif uniquement si SPOTIFY_BACKEND=go-librespot. librespot (le backend par
defaut) ne se pilote pas localement ; go-librespot expose une API HTTP et un
websocket d'evenements sur 127.0.0.1, ce qui rend la synchronisation possible.

Principe : go-librespot tourne en « external_volume ». Il n'applique AUCUN
volume au son, il se contente de tenir la valeur affichee dans l'appli Spotify.
Le seul etage de volume reste celui du dashboard (mixeur systeme, courbe
perceptive amixer -M), et les deux potars sont deux vues de la meme valeur :

  appli Spotify --(websocket "volume")--> pont --> mixeur systeme --> SSE
  potar dashboard --> mixeur systeme --> pont --(POST /player/volume)--> appli

Le pont alimente aussi les metadonnees Spotify (titre, pochette, lecture /
pause) a la place du hook --onevent de librespot.

Ce module n'importe pas app.views (import circulaire) : views lui passe ses
fonctions au demarrage via start().
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

try:
    import websocket  # websocket-client
except Exception:  # pragma: no cover
    websocket = None

log = logging.getLogger(__name__)

# Un changement venu du dashboard revient en echo par le websocket. Pendant
# cette fenetre, un evenement egal a la derniere valeur envoyee est ignore, et
# tout evenement est ignore pendant la courte fenetre qui suit un envoi (echo
# d'une valeur intermediaire pendant qu'on tourne le potar).
_ECHO_WINDOW_SECONDS = 1.5
_ECHO_GUARD_SECONDS = 0.4
_HTTP_TIMEOUT_SECONDS = 2.0
_RECONNECT_MIN_SECONDS = 2.0
_RECONNECT_MAX_SECONDS = 30.0


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class _LatestValueWorker:
    """Execute fn(valeur) en tache de fond, en ne gardant que la DERNIERE valeur.

    Faire glisser un curseur genere des dizaines de changements par seconde :
    seule la valeur finale compte, les intermediaires sont abandonnees.
    """

    def __init__(self, name, fn):
        self._fn = fn
        self._cond = threading.Condition()
        self._pending = None
        self._has_pending = False
        threading.Thread(target=self._run, name=name, daemon=True).start()

    def submit(self, value):
        with self._cond:
            self._pending = value
            self._has_pending = True
            self._cond.notify()

    def _run(self):
        while True:
            with self._cond:
                while not self._has_pending:
                    self._cond.wait()
                value = self._pending
                self._has_pending = False
            try:
                self._fn(value)
            except Exception:  # pragma: no cover - ne jamais tuer le worker
                log.exception("go-librespot : echec du traitement de %r", value)


class GoLibrespotBridge:
    def __init__(self, port):
        self._base = f"http://127.0.0.1:{int(port)}"
        self._ws_url = f"ws://127.0.0.1:{int(port)}/events"
        self._lock = threading.Lock()
        self._metadata = self._empty_metadata()
        self._volume_max = 100
        self._connected = False
        self._last_push = {"steps": None, "at": 0.0}

        self._apply_local_volume = None
        self._read_local_volume = None
        self._on_change = None

        self._push_worker = _LatestValueWorker("golibrespot-push", self._post_volume)
        self._apply_worker = None

    # ── Demarrage ────────────────────────────────────────────────────────
    def start(self, apply_local_volume, read_local_volume, on_change):
        """apply_local_volume(pct) : regle le mixeur SANS repropager.
        read_local_volume() -> pct|None : volume actuel du mixeur.
        on_change() : diffuse l'etat (SSE)."""
        self._apply_local_volume = apply_local_volume
        self._read_local_volume = read_local_volume
        self._on_change = on_change
        self._apply_worker = _LatestValueWorker("golibrespot-apply", self._apply_volume)

        if websocket is None:
            log.error("go-librespot : websocket-client absent, pont desactive")
            return
        threading.Thread(target=self._ws_loop, name="golibrespot-ws", daemon=True).start()

    # ── Etat expose au dashboard ─────────────────────────────────────────
    @staticmethod
    def _empty_metadata():
        return {"title": None, "artist": None, "album": None, "updated_at": None}

    def metadata_snapshot(self):
        with self._lock:
            return dict(self._metadata)

    def connected(self):
        return self._connected

    # ── Dashboard -> Spotify ─────────────────────────────────────────────
    def push_volume(self, percent):
        """Appele apres un changement de volume venu du dashboard / d'Onde."""
        if percent is None:
            return
        self._push_worker.submit(max(0, min(100, int(percent))))

    def _post_volume(self, percent):
        steps = self._percent_to_steps(percent)
        # Note AVANT l'envoi : l'echo peut arriver avant la reponse HTTP.
        self._last_push["steps"] = steps
        self._last_push["at"] = time.monotonic()
        try:
            self._request("POST", "/player/volume", {"volume": steps})
        except (OSError, ValueError) as exc:
            # go-librespot arrete (source AirPlay active) : rien a synchroniser.
            log.debug("go-librespot : volume non transmis (%s)", exc)

    # ── Spotify -> dashboard ─────────────────────────────────────────────
    def _on_volume_event(self, data):
        try:
            value = int(data.get("value"))
            vmax = int(data.get("max")) or 100
        except (TypeError, ValueError, AttributeError):
            return
        self._volume_max = vmax

        elapsed = time.monotonic() - self._last_push["at"]
        if elapsed < _ECHO_GUARD_SECONDS:
            return
        if elapsed < _ECHO_WINDOW_SECONDS and value == self._last_push["steps"]:
            return

        self._apply_worker.submit(self._steps_to_percent(value))

    def _apply_volume(self, percent):
        if self._apply_local_volume is not None:
            self._apply_local_volume(percent)

    def _percent_to_steps(self, percent):
        return int(round(percent * self._volume_max / 100.0))

    def _steps_to_percent(self, steps):
        if self._volume_max <= 0:
            return 0
        return max(0, min(100, int(round(steps * 100.0 / self._volume_max))))

    # ── Metadonnees ──────────────────────────────────────────────────────
    def _set_metadata(self, metadata):
        with self._lock:
            self._metadata = metadata
        self._notify()

    def _update_playback(self, playback):
        with self._lock:
            if not self._metadata.get("title"):
                return
            if self._metadata.get("playback") == playback:
                return
            self._metadata = dict(self._metadata, playback=playback, updated_at=_now_iso())
        self._notify()

    def _clear_metadata(self):
        with self._lock:
            if not self._metadata.get("title"):
                return
            self._metadata = self._empty_metadata()
        self._notify()

    @staticmethod
    def _track_to_metadata(track, playback, event):
        if not isinstance(track, dict) or not track.get("name"):
            return None
        artists = [a for a in (track.get("artist_names") or []) if a]
        return {
            "title": track.get("name"),
            "artist": ", ".join(artists) or None,
            "album": track.get("album_name") or None,
            "duration_ms": track.get("duration"),
            "uri": track.get("uri"),
            "event": event,
            "cover_url": track.get("album_cover_url") or None,
            "playback": playback,
            "updated_at": _now_iso(),
        }

    def _notify(self):
        if self._on_change:
            try:
                self._on_change()
            except Exception:  # pragma: no cover
                log.exception("go-librespot : echec de la diffusion d'etat")

    # ── Websocket ────────────────────────────────────────────────────────
    def _handle_event(self, kind, data):
        if kind == "volume":
            self._on_volume_event(data or {})
        elif kind == "metadata":
            playback = "playing"
            with self._lock:
                if self._metadata.get("playback") == "paused":
                    playback = "paused"
            meta = self._track_to_metadata(data, playback, kind)
            if meta:
                self._set_metadata(meta)
        elif kind == "playing":
            self._update_playback("playing")
        elif kind == "paused":
            self._update_playback("paused")
        elif kind in ("stopped", "inactive"):
            self._clear_metadata()

    def _on_connect(self):
        """Etat initial a la (re)connexion : metadonnees, echelle du volume,
        puis alignement de Spotify sur le mixeur (le son reel fait foi)."""
        try:
            status = self._request("GET", "/status")
        except (OSError, ValueError):
            status = None

        if isinstance(status, dict):
            try:
                self._volume_max = int(status.get("volume_steps") or 100)
            except (TypeError, ValueError):
                pass
            if status.get("stopped") or not status.get("track"):
                self._clear_metadata()
            else:
                playback = "paused" if status.get("paused") else "playing"
                meta = self._track_to_metadata(status.get("track"), playback, "status")
                if meta:
                    self._set_metadata(meta)

        if self._read_local_volume:
            self.push_volume(self._read_local_volume())

    def _ws_loop(self):
        delay = _RECONNECT_MIN_SECONDS
        while True:
            ws = None
            try:
                ws = websocket.create_connection(self._ws_url, timeout=10)
                # Pas de timeout en lecture : les evenements sont sporadiques.
                ws.settimeout(None)
                self._connected = True
                delay = _RECONNECT_MIN_SECONDS
                log.info("go-librespot : connecte a %s", self._ws_url)
                self._on_connect()
                while True:
                    raw = ws.recv()
                    if not raw:
                        break
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        self._handle_event(event.get("type"), event.get("data"))
            except Exception as exc:
                # go-librespot arrete (source AirPlay, redemarrage...) : normal.
                log.debug("go-librespot : websocket indisponible (%s)", exc)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
                if self._connected:
                    self._connected = False
                    self._clear_metadata()
            time.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_SECONDS)

    # ── HTTP ─────────────────────────────────────────────────────────────
    def _request(self, method, path, payload=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            body = resp.read()
        if not body:
            return None
        return json.loads(body.decode("utf-8"))
