import base64
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
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
    SHAIRPORT_METADATA_PIPE,
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
        "process_name": "shairport-sync",
    },
    "spotify": {
        "unit": LIBRESPOT_SERVICE,
        "systemd_user": LIBRESPOT_SYSTEMD_USER,
        "use_sudo": LIBRESPOT_USE_SUDO,
        "label": "librespot",
        "process_name": "librespot",
    },
}

_SYSTEM_VOLUME_BACKEND = None
_AIRPLAY_METADATA_LOCK = threading.Lock()
_AIRPLAY_METADATA = {
    "title": None,
    "artist": None,
    "album": None,
    "updated_at": None,
}
_AIRPLAY_REMOTE = {
    "client_ip": None,
    "dacp_port": None,
    "active_remote": None,
    "dacp_id": None,
    "updated_at": None,
}
_AIRPLAY_METADATA_TTL_SECONDS = 180

_META_FIELD_BY_CODE = {
    "minm": "title",  # item name
    "asar": "artist",  # song artist
    "asal": "album",  # song album
}


def _clamp(value, low, high):
    return max(low, min(high, value))


def _decode_meta_text(data):
    try:
        return data.decode("utf-8", errors="replace").replace("\x00", "").strip() or None
    except Exception:
        return None


def _update_airplay_metadata(field, value):
    with _AIRPLAY_METADATA_LOCK:
        _AIRPLAY_METADATA[field] = value
        _AIRPLAY_METADATA["updated_at"] = datetime.now(timezone.utc).isoformat()


def _clear_airplay_metadata():
    with _AIRPLAY_METADATA_LOCK:
        _AIRPLAY_METADATA["title"] = None
        _AIRPLAY_METADATA["artist"] = None
        _AIRPLAY_METADATA["album"] = None
        _AIRPLAY_METADATA["updated_at"] = datetime.now(timezone.utc).isoformat()


def _update_airplay_remote(field, value):
    with _AIRPLAY_METADATA_LOCK:
        _AIRPLAY_REMOTE[field] = value
        _AIRPLAY_REMOTE["updated_at"] = datetime.now(timezone.utc).isoformat()


def _clear_airplay_remote():
    with _AIRPLAY_METADATA_LOCK:
        _AIRPLAY_REMOTE["client_ip"] = None
        _AIRPLAY_REMOTE["dacp_port"] = None
        _AIRPLAY_REMOTE["active_remote"] = None
        _AIRPLAY_REMOTE["dacp_id"] = None
        _AIRPLAY_REMOTE["updated_at"] = datetime.now(timezone.utc).isoformat()


def _get_airplay_remote_snapshot():
    with _AIRPLAY_METADATA_LOCK:
        return dict(_AIRPLAY_REMOTE)


def _get_airplay_metadata_snapshot():
    with _AIRPLAY_METADATA_LOCK:
        return dict(_AIRPLAY_METADATA)


def _is_airplay_metadata_fresh(metadata):
    updated_at = metadata.get("updated_at")
    if not updated_at:
        return False

    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return False

    age_seconds = (datetime.now(timezone.utc) - updated).total_seconds()
    return age_seconds <= _AIRPLAY_METADATA_TTL_SECONDS


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining > 0:
        part = stream.read(remaining)
        if not part:
            return b""
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)


_KNOWN_FOURCC = {
    "core",
    "ssnc",
    "minm",
    "asar",
    "asal",
    "mdst",
    "mden",
    "pfls",
    "prgr",
    "pvol",
    "pbeg",
    "pend",
    "clip",
    "dapo",
    "acre",
    "daid",
    "disc",
    "aend",
}


def _decode_fourcc_hex(hex_value):
    try:
        raw = int(hex_value, 16)
    except Exception:
        return None

    candidates = []
    for byteorder in ("big", "little"):
        try:
            chunk = raw.to_bytes(4, byteorder=byteorder, signed=False)
        except OverflowError:
            continue
        if all(32 <= b <= 126 for b in chunk):
            candidates.append(chunk.decode("ascii", errors="ignore"))

    for candidate in candidates:
        if candidate in _KNOWN_FOURCC:
            return candidate

    return candidates[0] if candidates else None


def _parse_xml_metadata_item(item_text):
    type_match = re.search(r"<type>([0-9a-fA-F]+)</type>", item_text)
    code_match = re.search(r"<code>([0-9a-fA-F]+)</code>", item_text)
    if not type_match or not code_match:
        return None, None, b""

    item_type = _decode_fourcc_hex(type_match.group(1))
    item_code = _decode_fourcc_hex(code_match.group(1))

    payload = b""
    data_match = re.search(r"<data encoding=\"base64\">\s*(.*?)\s*</data>", item_text, flags=re.S)
    if data_match:
        encoded = "".join(data_match.group(1).split())
        if encoded:
            try:
                payload = base64.b64decode(encoded, validate=False)
            except Exception:
                payload = b""

    return item_type, item_code, payload


def _handle_airplay_metadata_item(item_type, item_code, payload):
    if item_type == "ssnc":
        text_payload = _decode_meta_text(payload)
        if item_code == "clip" and text_payload:
            _update_airplay_remote("client_ip", text_payload)
        elif item_code == "dapo" and text_payload and text_payload.isdigit():
            _update_airplay_remote("dacp_port", int(text_payload))
        elif item_code == "acre" and text_payload:
            _update_airplay_remote("active_remote", text_payload)
        elif item_code == "daid" and text_payload:
            _update_airplay_remote("dacp_id", text_payload)
        elif item_code in {"disc", "aend", "pend"}:
            _clear_airplay_remote()

    if item_code in _META_FIELD_BY_CODE:
        value = _decode_meta_text(payload)
        if value:
            _update_airplay_metadata(_META_FIELD_BY_CODE[item_code], value)
            _broadcast_state()
        return

    # Do not auto-clear on ssnc events here: many sources emit transitional
    # events quickly, which makes metadata flash and disappear.


def _send_airplay_playback_command(action):
    dacp_command = {
        "play": "play",
        "pause": "pause",
        "next": "nextitem",
        "previous": "previtem",
        "toggle": "playpause",
    }.get(action)

    if not dacp_command:
        return False, "Action playback AirPlay non supportee"

    remote = _get_airplay_remote_snapshot()
    client_ip = remote.get("client_ip")
    dacp_port = remote.get("dacp_port")
    active_remote = remote.get("active_remote")

    if not client_ip or not dacp_port or not active_remote:
        return (
            False,
            "Controle AirPlay indisponible: metadonnees DACP manquantes (lance une lecture AirPlay).",
        )

    host = client_ip
    if host and ":" in host and not host.startswith("["):
        host = f"[{host}]"

    url = f"http://{host}:{dacp_port}/ctrl-int/1/{dacp_command}"
    headers = {"Active-Remote": str(active_remote)}
    if remote.get("dacp_id"):
        headers["Client-Daap-Id"] = str(remote["dacp_id"])

    request_obj = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request_obj, timeout=2.0) as response:
            if 200 <= response.status < 300:
                return True, None
            return False, f"Commande AirPlay refusee (HTTP {response.status})"
    except urllib.error.URLError as exc:
        return False, f"Impossible de joindre la telecommande AirPlay ({url}): {exc}"
    except Exception as exc:
        return False, f"Commande AirPlay impossible: {exc}"


def _shairport_metadata_worker(pipe_path):
    while True:
        try:
            if not os.path.exists(pipe_path):
                time.sleep(1.0)
                continue

            with open(pipe_path, "rb", buffering=0) as metadata_pipe:
                first = metadata_pipe.read(1)
                if not first:
                    time.sleep(0.2)
                    continue

                # Current shairport-sync builds usually emit XML/base64 metadata on the pipe.
                if first == b"<":
                    xml_buffer = first.decode("utf-8", errors="replace")
                    while True:
                        chunk = metadata_pipe.read(4096)
                        if not chunk:
                            break
                        xml_buffer += chunk.decode("utf-8", errors="replace")

                        while "</item>" in xml_buffer:
                            end_ix = xml_buffer.find("</item>") + len("</item>")
                            item_text = xml_buffer[:end_ix]
                            xml_buffer = xml_buffer[end_ix:]
                            item_type, item_code, payload = _parse_xml_metadata_item(item_text)
                            if item_type and item_code:
                                _handle_airplay_metadata_item(item_type, item_code, payload)
                else:
                    # Backward-compatible parser for binary metadata framing.
                    while True:
                        remainder = _read_exact(metadata_pipe, 15)
                        if not remainder:
                            break
                        header = first + remainder

                        item_type = header[:4].decode("ascii", errors="replace")
                        item_code = header[4:8].decode("ascii", errors="replace")
                        payload_length = struct.unpack(">Q", header[8:16])[0]
                        payload = _read_exact(metadata_pipe, payload_length)
                        if payload_length > 0 and not payload:
                            break

                        _handle_airplay_metadata_item(item_type, item_code, payload)
                        first = metadata_pipe.read(1)
                        if not first:
                            break

        except Exception:
            # Le flux metadata peut disparaître pendant les redémarrages de shairport-sync.
            pass

        time.sleep(0.5)


def _start_shairport_metadata_monitor():
    thread = threading.Thread(
        target=_shairport_metadata_worker,
        args=(SHAIRPORT_METADATA_PIPE,),
        daemon=True,
        name="shairport-metadata-monitor",
    )
    thread.start()


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


def _run_command(command):
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        return False, "", "Commande introuvable"

    return completed.returncode == 0, (completed.stdout or "").strip(), (completed.stderr or "").strip()


def _get_system_volume_backend():
    global _SYSTEM_VOLUME_BACKEND
    if _SYSTEM_VOLUME_BACKEND:
        return _SYSTEM_VOLUME_BACKEND

    for candidate in ("wpctl", "pactl", "amixer"):
        if shutil.which(candidate):
            _SYSTEM_VOLUME_BACKEND = candidate
            break

    return _SYSTEM_VOLUME_BACKEND


def _read_system_volume():
    backend = _get_system_volume_backend()
    if backend == "wpctl":
        ok, stdout, stderr = _run_command(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        if not ok:
            return None, None, stderr

        match = re.search(r"Volume:\s*([0-9]*\.?[0-9]+)", stdout)
        if not match:
            return None, None, "Sortie wpctl inattendue"

        volume = _clamp(int(round(float(match.group(1)) * 100)), 0, 100)
        muted = "[MUTED]" in stdout
        return volume, muted, None

    if backend == "pactl":
        ok_volume, stdout_volume, stderr_volume = _run_command(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        ok_mute, stdout_mute, stderr_mute = _run_command(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        if not ok_volume or not ok_mute:
            return None, None, stderr_volume or stderr_mute

        match = re.search(r"(\d+)%", stdout_volume)
        if not match:
            return None, None, "Sortie pactl inattendue"

        volume = _clamp(int(match.group(1)), 0, 100)
        muted = "yes" in stdout_mute.lower()
        return volume, muted, None

    if backend == "amixer":
        ok, stdout, stderr = _run_command(["amixer", "get", "Master"])
        if not ok:
            return None, None, stderr

        vol_match = re.findall(r"\[(\d+)%\]", stdout)
        mute_match = re.findall(r"\[(on|off)\]", stdout)
        if not vol_match:
            return None, None, "Sortie amixer inattendue"

        volume = _clamp(int(vol_match[-1]), 0, 100)
        muted = bool(mute_match) and mute_match[-1] == "off"
        return volume, muted, None

    return None, None, "Aucun backend volume système disponible"


def _set_system_volume(volume=None, mute=None):
    backend = _get_system_volume_backend()
    if not backend:
        return False, "Aucun backend volume système disponible"

    commands = []
    if backend == "wpctl":
        if volume is not None:
            commands.append(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{_clamp(int(volume), 0, 100) / 100:.3f}"])
        if mute is not None:
            commands.append(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"])
    elif backend == "pactl":
        if volume is not None:
            commands.append(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{_clamp(int(volume), 0, 100)}%"])
        if mute is not None:
            commands.append(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
    elif backend == "amixer":
        if volume is not None:
            commands.append(["amixer", "-q", "set", "Master", f"{_clamp(int(volume), 0, 100)}%"])
        if mute is not None:
            commands.append(["amixer", "-q", "set", "Master", "mute" if mute else "unmute"])

    for command in commands:
        ok, _stdout, stderr = _run_command(command)
        if not ok:
            return False, stderr or "Échec de la commande volume système"

    return True, None


def _sync_system_volume_state():
    volume, muted, _error = _read_system_volume()
    if volume is not None:
        SPEAKER_STATE["volume"] = volume
    if muted is not None:
        SPEAKER_STATE["muted"] = muted


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

    # Some systemd setups keep the process alive briefly; force-stop as a fallback.
    if not online:
        process_name = backend.get("process_name")
        if process_name:
            kill_command = ["pkill", "-TERM", "-x", process_name]
            if backend["use_sudo"]:
                kill_command = ["sudo", "-n", *kill_command]
            _run_command(kill_command)

            for _ in range(6):
                refreshed_online, error = _get_service_status(service)
                if error:
                    return False, error
                if not refreshed_online:
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
    _sync_system_volume_state()

    track = PLAYLIST[SPEAKER_STATE["track_index"]]
    current_track = track["title"]
    current_artist = track["artist"]

    active_service_key = SPEAKER_STATE.get("active_service")
    if active_service_key == "airplay":
        metadata = _get_airplay_metadata_snapshot()
        if _is_airplay_metadata_fresh(metadata):
            current_track = metadata.get("title") or "Titre inconnu"
            current_artist = metadata.get("artist") or "Artiste inconnu"
        else:
            current_track = "En attente de metadonnees AirPlay"
            current_artist = "Demarre une lecture AirPlay"

    active_service_key = SPEAKER_STATE.get("active_service")
    active_service = SPEAKER_STATE["services"].get(active_service_key) if active_service_key else None
    return {
        **SPEAKER_STATE,
        "current_track": current_track,
        "current_artist": current_artist,
        "airplay_metadata": _get_airplay_metadata_snapshot(),
        "airplay_remote": _get_airplay_remote_snapshot(),
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

    action = action if action in {"play", "pause", "next", "previous", "toggle"} else "toggle"

    if active_service_key == "airplay":
        ok, error = _send_airplay_playback_command(action)
        if not ok:
            return jsonify({"error": error}), 400

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

    _sync_system_volume_state()

    target_volume = SPEAKER_STATE["volume"]
    target_mute = SPEAKER_STATE["muted"]

    if "mute" in payload:
        target_mute = bool(payload["mute"])

    if "volume" in payload:
        target_volume = _clamp(int(payload["volume"]), 0, 100)
        if target_volume > 0:
            target_mute = False

    if "delta" in payload:
        target_volume = _clamp(target_volume + int(payload["delta"]), 0, 100)
        if target_volume > 0:
            target_mute = False

    ok, error = _set_system_volume(volume=target_volume, mute=target_mute)
    if not ok:
        return jsonify({"error": error or "Impossible de piloter le volume système"}), 400

    _sync_system_volume_state()

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

            if service == "airplay" and not desired_online:
                _clear_airplay_metadata()
                _clear_airplay_remote()
                SPEAKER_STATE["is_playing"] = False
                if SPEAKER_STATE.get("active_service") == "airplay":
                    SPEAKER_STATE["active_service"] = None

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


_start_shairport_metadata_monitor()

