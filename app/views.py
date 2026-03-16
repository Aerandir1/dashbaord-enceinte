import json
import os
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone

from flask import Response, jsonify, render_template, request, stream_with_context

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None

from app import app
from app.config import (
    LIBRESPOT_SERVICE,
    LIBRESPOT_SYSTEMD_USER,
    LIBRESPOT_USE_SUDO,
    SHAIRPORT_SYNC_SERVICE,
    SHAIRPORT_SYNC_SYSTEMD_USER,
    SHAIRPORT_SYNC_USE_SUDO,
)


PLAYLIST = [
    {"title": "Morning Vibes", "artist": "Lina Gray"},
    {"title": "Deep Focus", "artist": "Neon Coast"},
    {"title": "Night Drive", "artist": "Polar Echo"},
]

EQ_PRESETS = {
    "flat": {"60Hz": 0, "230Hz": 0, "910Hz": 0, "3.6kHz": 0, "14kHz": 0},
    "bass_boost": {"60Hz": 6, "230Hz": 3, "910Hz": 0, "3.6kHz": -1, "14kHz": -2},
    "vocal": {"60Hz": -2, "230Hz": 1, "910Hz": 4, "3.6kHz": 3, "14kHz": 1},
    "treble_boost": {"60Hz": -2, "230Hz": -1, "910Hz": 1, "3.6kHz": 4, "14kHz": 6},
}

SPEAKER_STATE = {
    "device_name": "Enceinte Salon",
    "room": "Salon",
    "power": True,
    "is_playing": True,
    "volume": 42,
    "muted": False,
    "battery": 78,
    "wifi_strength": 4,
    "firmware": "v1.0.0",
    "track_index": 0,
    "services": {
        "spotify": {"name": "Spotify", "online": True},
        "airplay": {"name": "AirPlay", "online": True},
    },
    "active_service": "spotify",
    "eq_preset": "flat",
    "eq_bands": EQ_PRESETS["flat"].copy(),
    "updated_at": datetime.now(timezone.utc).isoformat(),
}

# Clients abonnés au flux temps réel (SSE)
_SUBSCRIBERS = []


class ServerSpectrum:
    def __init__(self):
        self._lock = threading.Lock()
        self._stream = None
        self._device = None
        self._samplerate = 48000
        self._fft_size = 2048
        self._bins_count = 96
        self._last_bins = [0.0] * self._bins_count
        self._last_error = None
        self._running = False

    def available(self):
        return np is not None and sd is not None

    def list_devices(self):
        if not self.available():
            return []
        devices = []
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                devices.append(
                    {
                        "id": idx,
                        "name": dev.get("name", f"device-{idx}"),
                        "samplerate": int(dev.get("default_samplerate", 48000)),
                    }
                )
        return devices

    def _process_block(self, mono):
        if len(mono) < 64:
            return

        window = np.hanning(len(mono))
        spectrum = np.fft.rfft(mono * window)
        magnitude = np.abs(spectrum)

        # Conversion dB puis normalisation [0..1]
        db = 20 * np.log10(magnitude + 1e-9)
        db_min, db_max = -90.0, -10.0
        norm = np.clip((db - db_min) / (db_max - db_min), 0.0, 1.0)

        freqs = np.fft.rfftfreq(len(mono), d=1.0 / self._samplerate)
        low, high = 20.0, min(20000.0, self._samplerate / 2)
        edges = np.logspace(np.log10(low), np.log10(high), self._bins_count + 1)

        bins = []
        for i in range(self._bins_count):
            mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
            if np.any(mask):
                bins.append(float(np.max(norm[mask])))
            else:
                bins.append(0.0)

        with self._lock:
            self._last_bins = bins

    def _callback(self, indata, frames, _time, status):
        if status:
            self._last_error = str(status)
        try:
            mono = np.mean(indata[:, :1], axis=1)
            self._process_block(mono)
        except Exception as exc:  # pragma: no cover
            self._last_error = str(exc)

    def start(self, device=None):
        if not self.available():
            return False, "Backend audio serveur indisponible (numpy/sounddevice manquant)."

        self.stop()

        try:
            if device in ("", None):
                device = None
            elif isinstance(device, str) and device.isdigit():
                device = int(device)

            if device is not None:
                info = sd.query_devices(device)
                self._samplerate = int(info.get("default_samplerate", 48000))
            else:
                default_in = sd.default.device[0]
                if default_in is not None and default_in >= 0:
                    info = sd.query_devices(default_in)
                    self._samplerate = int(info.get("default_samplerate", 48000))

            self._stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=self._samplerate,
                blocksize=self._fft_size,
                callback=self._callback,
            )
            self._stream.start()
            self._device = device
            self._running = True
            self._last_error = None
            return True, "ok"
        except Exception as exc:
            self._stream = None
            self._running = False
            self._last_error = str(exc)
            return False, str(exc)

    def stop(self):
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None
            self._running = False

    def snapshot(self):
        with self._lock:
            bins = list(self._last_bins)
        return {
            "running": self._running,
            "device": self._device,
            "bins": bins,
            "error": self._last_error,
        }


SPECTRUM = ServerSpectrum()

SERVICE_BACKENDS = {
    "airplay": {
        "unit": SHAIRPORT_SYNC_SERVICE,
        "systemd_user": SHAIRPORT_SYNC_SYSTEMD_USER,
        "use_sudo": SHAIRPORT_SYNC_USE_SUDO,
        "label": "shairport-sync",
    },
    "spotify": {
        "unit": LIBRESPOT_SERVICE,
        "systemd_user": LIBRESPOT_SYSTEMD_USER,
        "use_sudo": LIBRESPOT_USE_SUDO,
        "label": "librespot",
    },
}


def _clamp(value, low, high):
    return max(low, min(high, value))


def _run_systemctl(*args, systemd_user=False, use_sudo=False):
    command = []
    if use_sudo:
        command.extend(["sudo", "-n"])
    command.append("systemctl")
    if systemd_user:
        command.append("--user")
    command.extend(args)

    env = os.environ.copy()
    if systemd_user:
        uid = os.getuid()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        return False, "", "La commande systemctl est introuvable."

    return completed.returncode == 0, (completed.stdout or "").strip(), (completed.stderr or "").strip()


def _get_service_backend(service):
    return SERVICE_BACKENDS.get(service)


def _get_service_status(service):
    backend = _get_service_backend(service)
    if not backend:
        return SPEAKER_STATE["services"][service]["online"], None

    ok, stdout, stderr = _run_systemctl(
        "is-active",
        backend["unit"],
        systemd_user=backend["systemd_user"],
        use_sudo=backend["use_sudo"],
    )
    status = stdout.lower()
    error = stderr or stdout

    if ok or status == "active":
        return True, None

    if status in {"inactive", "failed", "deactivating", "activating", "unknown"}:
        return False, None

    if "could not be found" in error.lower() or "not found" in error.lower():
        return False, None

    return False, error or f"Impossible de lire l'état du service {backend['unit']}."


def _set_service_online(service, online):
    backend = _get_service_backend(service)
    if not backend:
        SPEAKER_STATE["services"][service]["online"] = online
        return True, None

    action = "start" if online else "stop"
    ok, _stdout, stderr = _run_systemctl(
        action,
        backend["unit"],
        systemd_user=backend["systemd_user"],
        use_sudo=backend["use_sudo"],
    )
    if not ok:
        reason = stderr or f"La commande systemctl {action} a échoué."
        return False, f"Impossible de {'démarrer' if online else 'arrêter'} {backend['label']} : {reason}"

    # Un service peut passer brièvement à "active" avant de crasher.
    for _ in range(6):
        refreshed_online, error = _get_service_status(service)
        if error:
            return False, error
        if refreshed_online == online:
            return True, None
        time.sleep(0.3)

    return False, f"Le service {backend['unit']} n'a pas atteint l'état attendu."


def _sync_service_states():
    for service in SPEAKER_STATE["services"]:
        if service in SERVICE_BACKENDS:
            online, _error = _get_service_status(service)
            SPEAKER_STATE["services"][service]["online"] = online

    active_service_key = SPEAKER_STATE.get("active_service")
    if active_service_key and not SPEAKER_STATE["services"][active_service_key]["online"]:
        fallback = next(
            (key for key, value in SPEAKER_STATE["services"].items() if value["online"]),
            None,
        )
        SPEAKER_STATE["active_service"] = fallback


def _touch_state():
    SPEAKER_STATE["updated_at"] = datetime.now(timezone.utc).isoformat()
    _broadcast_state()


def _broadcast_state():
    payload = _public_state()
    dead = []
    for q in _SUBSCRIBERS:
        try:
            q.put_nowait(payload)
        except queue.Full:
            dead.append(q)

    if dead:
        for q in dead:
            if q in _SUBSCRIBERS:
                _SUBSCRIBERS.remove(q)


def _updated_since(iso_value):
    try:
        updated = datetime.fromisoformat(iso_value)
    except ValueError:
        return "à l'instant"

    now = datetime.now(timezone.utc)
    diff_seconds = int((now - updated).total_seconds())

    if diff_seconds < 60:
        return "à l'instant"

    diff_minutes = diff_seconds // 60
    if diff_minutes < 60:
        return f"il y a {diff_minutes} min"

    diff_hours = diff_minutes // 60
    return f"il y a {diff_hours} h"


def _public_state():
    _sync_service_states()
    track = PLAYLIST[SPEAKER_STATE["track_index"]]
    active_service_key = SPEAKER_STATE.get("active_service")
    active_service = SPEAKER_STATE["services"].get(active_service_key) if active_service_key else None
    return {
        **SPEAKER_STATE,
        "current_track": track["title"],
        "current_artist": track["artist"],
        "active_service_name": active_service["name"] if active_service else "Aucune",
        "updated_since": _updated_since(SPEAKER_STATE["updated_at"]),
    }


@app.route("/")
def index():
    return render_template("index.html", state=_public_state())


@app.route("/api/state", methods=["GET"])
def api_state():
    return jsonify(_public_state())


@app.route("/api/audio/devices", methods=["GET"])
def api_audio_devices():
    return jsonify(
        {
            "available": SPECTRUM.available(),
            "devices": SPECTRUM.list_devices(),
        }
    )


@app.route("/api/audio/start", methods=["POST"])
def api_audio_start():
    payload = request.json or {}
    ok, message = SPECTRUM.start(payload.get("device"))
    if not ok:
        return jsonify({"error": message}), 400
    return jsonify(SPECTRUM.snapshot())


@app.route("/api/audio/stop", methods=["POST"])
def api_audio_stop():
    SPECTRUM.stop()
    return jsonify(SPECTRUM.snapshot())


@app.route("/api/spectrum", methods=["GET"])
def api_spectrum():
    data = SPECTRUM.snapshot()
    data["available"] = SPECTRUM.available()
    return jsonify(data)


@app.route("/api/stream", methods=["GET"])
def api_stream():
    def event_stream():
        client_queue = queue.Queue(maxsize=10)
        _SUBSCRIBERS.append(client_queue)

        try:
            # Snapshot immédiat à la connexion
            yield f"event: state\ndata: {json.dumps(_public_state())}\n\n"

            while True:
                try:
                    state = client_queue.get(timeout=25)
                    yield f"event: state\ndata: {json.dumps(state)}\n\n"
                except queue.Empty:
                    # heartbeat pour garder la connexion active
                    yield ": ping\n\n"
        finally:
            if client_queue in _SUBSCRIBERS:
                _SUBSCRIBERS.remove(client_queue)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(stream_with_context(event_stream()), headers=headers, mimetype="text/event-stream")


@app.route("/api/power", methods=["POST"])
def api_power():
    action = (request.json or {}).get("action", "toggle")
    if action == "on":
        SPEAKER_STATE["power"] = True
    elif action == "off":
        SPEAKER_STATE["power"] = False
        SPEAKER_STATE["is_playing"] = False
    else:
        SPEAKER_STATE["power"] = not SPEAKER_STATE["power"]
        if not SPEAKER_STATE["power"]:
            SPEAKER_STATE["is_playing"] = False
    _touch_state()
    return jsonify(_public_state())


@app.route("/api/playback", methods=["POST"])
def api_playback():
    action = (request.json or {}).get("action", "toggle")

    if not SPEAKER_STATE["power"]:
        return jsonify({"error": "Enceinte éteinte"}), 400

    active_service_key = SPEAKER_STATE.get("active_service")
    if not active_service_key:
        return jsonify({"error": "Aucune source audio active"}), 400

    if not SPEAKER_STATE["services"][active_service_key]["online"]:
        return jsonify({"error": "La source audio active est hors ligne"}), 400

    if action == "play":
        SPEAKER_STATE["is_playing"] = True
    elif action == "pause":
        SPEAKER_STATE["is_playing"] = False
    elif action == "next":
        SPEAKER_STATE["track_index"] = (SPEAKER_STATE["track_index"] + 1) % len(PLAYLIST)
        SPEAKER_STATE["is_playing"] = True
    elif action == "previous":
        SPEAKER_STATE["track_index"] = (SPEAKER_STATE["track_index"] - 1) % len(PLAYLIST)
        SPEAKER_STATE["is_playing"] = True
    else:
        SPEAKER_STATE["is_playing"] = not SPEAKER_STATE["is_playing"]

    _touch_state()
    return jsonify(_public_state())


@app.route("/api/volume", methods=["POST"])
def api_volume():
    payload = request.json or {}

    if "mute" in payload:
        SPEAKER_STATE["muted"] = bool(payload["mute"])

    if "volume" in payload:
        SPEAKER_STATE["volume"] = _clamp(int(payload["volume"]), 0, 100)
        if SPEAKER_STATE["volume"] > 0:
            SPEAKER_STATE["muted"] = False

    if "delta" in payload:
        SPEAKER_STATE["volume"] = _clamp(SPEAKER_STATE["volume"] + int(payload["delta"]), 0, 100)
        if SPEAKER_STATE["volume"] > 0:
            SPEAKER_STATE["muted"] = False

    _touch_state()
    return jsonify(_public_state())


@app.route("/api/eq", methods=["POST"])
def api_eq():
    payload = request.json or {}

    preset = payload.get("preset")
    if preset:
        if preset not in EQ_PRESETS:
            return jsonify({"error": "Preset EQ invalide"}), 400
        SPEAKER_STATE["eq_preset"] = preset
        SPEAKER_STATE["eq_bands"] = EQ_PRESETS[preset].copy()

    bands = payload.get("bands")
    if isinstance(bands, dict):
        for band_name, band_value in bands.items():
            if band_name in SPEAKER_STATE["eq_bands"]:
                SPEAKER_STATE["eq_bands"][band_name] = _clamp(int(band_value), -12, 12)
        SPEAKER_STATE["eq_preset"] = "custom"

    band_name = payload.get("band")
    if band_name in SPEAKER_STATE["eq_bands"] and "gain" in payload:
        SPEAKER_STATE["eq_bands"][band_name] = _clamp(int(payload["gain"]), -12, 12)
        SPEAKER_STATE["eq_preset"] = "custom"

    _touch_state()
    return jsonify(_public_state())


@app.route("/api/services", methods=["POST"])
def api_services():
    payload = request.json or {}
    service = payload.get("service")

    if service not in SPEAKER_STATE["services"]:
        return jsonify({"error": "Service inconnu"}), 400

    action = payload.get("action")
    desired_online = None
    if "online" in payload:
        desired_online = bool(payload["online"])
    elif action == "toggle":
        _sync_service_states()
        desired_online = not SPEAKER_STATE["services"][service]["online"]

    if desired_online is not None:
        if service in SERVICE_BACKENDS:
            ok, error = _set_service_online(service, desired_online)
            if not ok:
                return jsonify({"error": error}), 400
            _sync_service_states()
        else:
            SPEAKER_STATE["services"][service]["online"] = desired_online

    if payload.get("select") or action == "select":
        _sync_service_states()
        if not SPEAKER_STATE["services"][service]["online"]:
            return jsonify({"error": "Service hors ligne"}), 400
        SPEAKER_STATE["active_service"] = service

    _sync_service_states()

    _touch_state()
    return jsonify(_public_state())

