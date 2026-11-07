"""Onde v1 : le protocole qui rend l'enceinte pilotable par Domus (voir ONDE.md).

Routes sous /onde/v1 :

  GET  /hello    identite et fonctions de l'enceinte (toujours publique) ;
  GET  /state    etat normalise (volume, lecture, source, piste...) ;
  POST /command  une commande {"cmd": ..., "value": ...} ;
  GET  /events   flux SSE : l'etat complet a chaque changement ;
  GET  /cover    pochette de la piste en cours.

Ce module ne pilote rien lui-meme : il traduit vers les fonctions du dashboard
(app/views.py), qui restent la seule facon de toucher au materiel. L'etat Onde
est donc toujours le meme que celui affiche par le dashboard.
"""

import hashlib
import hmac
import json
import os
import queue
import secrets
import threading

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app import airplay, outputs, views
from app.config import ONDE_ID_FILE, ONDE_KEY

VERSION = 1
FEATURES = ["volume", "mute", "transport", "source", "metadata", "cover", "events"]
TRANSPORT = ("play", "pause", "toggle", "next", "previous")

# Le flux temps reel relit l'etat a ce rythme tant qu'un client ecoute : c'est
# ce qui fait remonter les changements venus d'ailleurs (application Spotify,
# iPhone en AirPlay). Les commandes recues ici, ou faites depuis le dashboard,
# reveillent la boucle aussitot.
_WATCH_SECONDS = 2.0
_PING_SECONDS = 15.0

bp = Blueprint("onde", __name__, url_prefix="/onde/v1")


class OndeError(Exception):
    STATUS = {
        "bad_request": 400, "unknown_command": 400, "unauthorized": 401,
        "unsupported": 409, "unavailable": 409, "failed": 502,
    }

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@bp.errorhandler(OndeError)
def _onde_error(exc):
    body = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    return jsonify(body), OndeError.STATUS.get(exc.code, 400)


# ── Identite ────────────────────────────────────────────────────────────────

def _load_id():
    """Identifiant stable de l'enceinte, tire une fois puis conserve.

    Il est derive de /etc/machine-id quand il existe : reinstaller le dashboard
    ne change donc pas l'identite de l'enceinte pour Domus.
    """
    try:
        with open(ONDE_ID_FILE, "r", encoding="utf-8") as handle:
            value = json.load(handle).get("id")
        if isinstance(value, str) and value:
            return value
    except (OSError, ValueError, AttributeError):
        pass

    try:
        with open("/etc/machine-id", "r", encoding="utf-8") as handle:
            seed = handle.read().strip()
    except OSError:
        seed = ""
    value = hashlib.sha256(f"onde:{seed}".encode()).hexdigest()[:16] if seed else secrets.token_hex(8)

    try:
        directory = os.path.dirname(ONDE_ID_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{ONDE_ID_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"id": value}, handle)
        os.replace(tmp, ONDE_ID_FILE)
    except OSError:
        # Non enregistre : l'identifiant derive de machine-id reste le meme
        # au prochain demarrage, seul le repli aleatoire changerait.
        pass
    return value


SPEAKER_ID = _load_id()


def _model():
    # La sortie choisie (HiFiBerry, jack, HDMI), pas la premiere carte venue.
    dac = (outputs.active_output() or {}).get("label") or views._get_dac_name()
    return f"Raspberry Pi · {dac}" if dac else "Raspberry Pi"


# ── Etat normalise ──────────────────────────────────────────────────────────

def _spotify_track(meta):
    if not meta.get("title"):
        return None
    cover = meta.get("cover_url")
    return {
        "title": meta.get("title"),
        "artist": meta.get("artist"),
        "album": meta.get("album"),
        "duration_ms": meta.get("duration_ms"),
        # Pochette hebergee par Spotify : URL absolue, deja unique par image.
        "cover": cover if isinstance(cover, str) and cover.startswith("https://") else None,
    }


def _airplay_track(md):
    if not md.get("title"):
        return None
    # La pochette est servie par /cover ; la cle change avec la piste.
    key = hashlib.sha1(f"{md.get('trackid')}|{md.get('title')}|{md.get('art_url')}".encode()).hexdigest()[:10]
    return {
        "title": md.get("title"),
        "artist": md.get("artist"),
        "album": md.get("album"),
        "duration_ms": None,
        "cover": f"cover?k={key}" if md.get("art_url") else None,
    }


def build_state():
    """Etat Onde, sans le compteur de revision (ajoute par le veilleur)."""
    public = views._public_state()
    source = public.get("active_service")
    services = public.get("services") or {}

    track = None
    playback = "idle"
    can_transport = False

    if source == "airplay":
        md = airplay.metadata()
        track = _airplay_track(md)
        status = md.get("status")
        if status == "Playing":
            playback = "playing"
        elif status == "Paused" or track:
            playback = "paused"
        # MPRIS n'existe que pendant une session AirPlay : sans elle, il n'y a
        # rien a reprendre ni a passer.
        can_transport = bool(md.get("available"))
    elif source == "spotify":
        meta = views._get_spotify_metadata_snapshot()
        track = _spotify_track(meta)
        if track:
            # « playback » est ecrit par librespot-event.sh sur les evenements
            # playing / paused. Avant le premier, une piste chargee joue.
            playback = "paused" if meta.get("playback") == "paused" else "playing"

    wifi = public.get("wifi") or {}
    system = public.get("system") or {}
    signal = {}
    if wifi.get("signal_dbm") is not None:
        signal["wifi_dbm"] = wifi["signal_dbm"]
    if system.get("temperature_c") is not None:
        signal["temperature_c"] = system["temperature_c"]

    return {
        "name": public.get("device_name"),
        "room": public.get("room"),
        "playback": playback,
        "volume": int(public.get("volume") or 0),
        "muted": bool(public.get("muted")),
        "source": source,
        "sources": [
            {"id": key, "name": value.get("name", key), "active": key == source}
            for key, value in services.items()
        ],
        "controls": {name: can_transport for name in ("play", "pause", "next", "previous")},
        "track": track,
        "output": public.get("audio_output"),
        "signal": signal,
    }


class _Watcher:
    """Calcule l'etat une fois pour tous les clients du flux, et le diffuse
    quand il change. Ne tourne que tant qu'au moins un client ecoute."""

    def __init__(self):
        self._lock = threading.Lock()        # clients et fil du veilleur
        self._state_lock = threading.Lock()  # calcul de l'etat et revision
        self._clients = []
        self._kick = threading.Event()
        self._thread = None
        self._last_key = None
        self.rev = 0
        # Les commandes passees par le dashboard reveillent aussi la boucle.
        self._dashboard_queue = queue.Queue(maxsize=1)

    def current(self):
        """Etat a jour, avec sa revision (relu a chaque appel)."""
        with self._state_lock:
            state = build_state()
            # La sante (Wi-Fi, temperature) bouge en permanence : elle ne
            # compte pas comme un changement, sinon le flux ne s'arreterait
            # jamais d'emettre.
            key = json.dumps({k: v for k, v in state.items() if k != "signal"}, sort_keys=True)
            if key != self._last_key:
                self._last_key = key
                self.rev += 1
            return {"rev": self.rev, **state}

    def kick(self):
        self._kick.set()

    def subscribe(self):
        client = queue.Queue(maxsize=8)
        with self._lock:
            self._clients.append(client)
            if self._thread is None or not self._thread.is_alive():
                if self._dashboard_queue not in views._SUBSCRIBERS:
                    views._SUBSCRIBERS.append(self._dashboard_queue)
                self._thread = threading.Thread(target=self._run, name="onde-watch", daemon=True)
                self._thread.start()
        return client

    def unsubscribe(self, client):
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def _publish(self, state):
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(state)
            except queue.Full:
                # Client trop lent : on remplace son retard par l'etat le plus
                # recent, qui seul compte.
                try:
                    client.get_nowait()
                    client.put_nowait(state)
                except (queue.Empty, queue.Full):
                    pass

    def _run(self):
        published = None
        while True:
            with self._lock:
                if not self._clients:
                    if self._dashboard_queue in views._SUBSCRIBERS:
                        views._SUBSCRIBERS.remove(self._dashboard_queue)
                    self._thread = None
                    return
            try:
                state = self.current()
                if state["rev"] != published:
                    published = state["rev"]
                    self._publish(state)
            except Exception as exc:  # pragma: no cover - ne jamais tuer le flux
                print(f"[onde] lecture de l'etat impossible : {exc}")
            if self._kick.wait(_WATCH_SECONDS):
                self._kick.clear()
            try:
                self._dashboard_queue.get_nowait()
            except queue.Empty:
                pass


WATCHER = _Watcher()


# ── Cle d'acces (facultative) ───────────────────────────────────────────────

@bp.before_request
def _check_key():
    if not ONDE_KEY or request.endpoint == "onde.hello":
        return None
    header = request.headers.get("Authorization", "")
    given = header[7:] if header.startswith("Bearer ") else ""
    if not hmac.compare_digest(given.encode(), ONDE_KEY.encode()):
        raise OndeError("unauthorized", "Clé Onde absente ou incorrecte.")
    return None


# ── Routes ──────────────────────────────────────────────────────────────────

@bp.route("/hello", methods=["GET"])
def hello():
    state = views.SPEAKER_STATE
    return jsonify({
        "onde": VERSION,
        "id": SPEAKER_ID,
        "name": state.get("device_name"),
        "room": state.get("room"),
        "model": _model(),
        "software": "dashboard-enceinte",
        "auth": "key" if ONDE_KEY else "none",
        "features": FEATURES,
    })


@bp.route("/state", methods=["GET"])
def state():
    current = WATCHER.current()
    return jsonify(current)


def _int_value(payload, low, high):
    value = payload.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OndeError("bad_request", f"« value » doit être un nombre entre {low} et {high}.")
    if not low <= value <= high:
        raise OndeError("bad_request", f"« value » doit être entre {low} et {high}.")
    return int(round(value))


def _set_volume(volume=None, mute=None):
    views._sync_system_volume_state(force=True)
    target_volume = views.SPEAKER_STATE["volume"] if volume is None else volume
    target_mute = views.SPEAKER_STATE["muted"] if mute is None else mute
    if volume is not None and volume > 0 and mute is None:
        target_mute = False
    ok, error = views._set_system_volume(volume=target_volume, mute=target_mute)
    if not ok:
        raise OndeError("failed", error or "Impossible de régler le volume.")
    views._sync_system_volume_state(force=True)


def _transport(cmd):
    current = WATCHER.current()
    source = current["source"]
    if source is None:
        raise OndeError("unavailable", "Aucune source n'est active sur l'enceinte.")
    if source == "spotify":
        # librespot ne se pilote pas localement : c'est le client Spotify qui
        # tient la lecture. On le dit plutot que de faire semblant.
        raise OndeError("unsupported", "La lecture Spotify se pilote depuis l'application Spotify.")
    if not current["controls"].get("play"):
        raise OndeError("unsupported", "Aucune lecture AirPlay en cours à piloter.")
    ok, error = views._send_airplay_playback_command(cmd)
    if not ok:
        raise OndeError("failed", error or "L'émetteur AirPlay a refusé la commande.")


@bp.route("/command", methods=["POST"])
def command():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("cmd"), str):
        raise OndeError("bad_request", "Corps attendu : {\"cmd\": \"…\", \"value\": …}.")
    cmd = payload["cmd"]

    if cmd in TRANSPORT:
        _transport(cmd)
    elif cmd == "volume":
        _set_volume(volume=_int_value(payload, 0, 100))
    elif cmd == "volume_step":
        step = _int_value(payload, -100, 100)
        views._sync_system_volume_state(force=True)
        _set_volume(volume=views._clamp(views.SPEAKER_STATE["volume"] + step, 0, 100))
    elif cmd == "mute":
        if not isinstance(payload.get("value"), bool):
            raise OndeError("bad_request", "« value » doit être true ou false.")
        _set_volume(mute=payload["value"])
    elif cmd == "source":
        source = payload.get("value")
        if source is not None and source not in views.SPEAKER_STATE["services"]:
            raise OndeError("bad_request", f"Source inconnue : {source}.")
        ok, error = views._set_active_source(source)
        if not ok:
            raise OndeError("failed", error or "Impossible de changer de source.")
    else:
        raise OndeError("unknown_command", f"Commande inconnue : {cmd}.")

    # Le dashboard ouvert ailleurs se met a jour, et le flux Onde aussi.
    views._touch_state()
    WATCHER.kick()
    current = WATCHER.current()
    return jsonify({"ok": True, "state": current})


@bp.route("/events", methods=["GET"])
def events():
    def stream():
        client = WATCHER.subscribe()
        try:
            current = WATCHER.current()
            yield f"event: state\ndata: {json.dumps(current, ensure_ascii=False)}\n\n"
            last_rev = current["rev"]
            while True:
                try:
                    state = client.get(timeout=_PING_SECONDS)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                if state["rev"] != last_rev:
                    last_rev = state["rev"]
                    yield f"event: state\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
        finally:
            WATCHER.unsubscribe(client)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(stream()), headers=headers, mimetype="text/event-stream")


@bp.route("/cover", methods=["GET"])
def cover():
    # Spotify donne une URL absolue dans l'etat : seule la pochette AirPlay,
    # fichier local ecrit par shairport-sync, passe par ici.
    data, mime = airplay.cover_bytes()
    if not data:
        return "", 404
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response
