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
    ALSA_MIXER_CARD,
    ALSA_MIXER_CONTROL,
    LIBRESPOT_METADATA_FILE,
    LIBRESPOT_SERVICE,
    LIBRESPOT_SYSTEMD_USER,
    LIBRESPOT_USE_SUDO,
    SERVICE_MANAGER,
    SHAIRPORT_METADATA_PIPE,
    SHAIRPORT_SYNC_SERVICE,
    SHAIRPORT_SYNC_SYSTEMD_USER,
    SHAIRPORT_SYNC_USE_SUDO,
    SUPERVISORCTL_BIN,
    SUPERVISORD_CONFIG,
)


EQ_PRESETS = {
    "flat": {"60Hz": 0, "230Hz": 0, "910Hz": 0, "3.6kHz": 0, "14kHz": 0},
    "bass_boost": {"60Hz": 6, "230Hz": 3, "910Hz": 0, "3.6kHz": -1, "14kHz": -2},
    "vocal": {"60Hz": -2, "230Hz": 1, "910Hz": 4, "3.6kHz": 3, "14kHz": 1},
    "treble_boost": {"60Hz": -2, "230Hz": -1, "910Hz": 1, "3.6kHz": 4, "14kHz": 6},
}

SPEAKER_STATE = {
    "device_name": "Enceinte Salon",
    "room": "Salon",
    "is_playing": False,
    "volume": 42,
    "muted": False,
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

# Durée de validité de l'état système (services + volume). Sans ce cache, chaque
# requête HTTP et chaque diffusion SSE relançait des sous-processus, provoquant
# des pics CPU qui coupaient l'audio (craquements) sur un Raspberry Pi.
_STATE_CACHE_TTL_SECONDS = float(os.getenv("STATE_CACHE_TTL_SECONDS", "3.0"))
_SERVICE_SYNC_AT = [0.0]
_VOLUME_SYNC_AT = [0.0]
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


# Metadonnees Spotify : ecrites par le hook --onevent de librespot, relues ici.
# Cache indexe sur la date de modification pour eviter de relire le fichier a
# chaque requete.
_SPOTIFY_METADATA_CACHE = {"mtime": None, "data": None}


def _get_spotify_metadata_snapshot():
    empty = {"title": None, "artist": None, "album": None, "updated_at": None}

    try:
        mtime = os.path.getmtime(LIBRESPOT_METADATA_FILE)
    except OSError:
        # Fichier absent : librespot est arrete ou aucune piste n'a ete jouee.
        _SPOTIFY_METADATA_CACHE["mtime"] = None
        _SPOTIFY_METADATA_CACHE["data"] = None
        return empty

    if _SPOTIFY_METADATA_CACHE["mtime"] == mtime and _SPOTIFY_METADATA_CACHE["data"]:
        return dict(_SPOTIFY_METADATA_CACHE["data"])

    try:
        with open(LIBRESPOT_METADATA_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        # Lecture concurrente d'une ecriture, ou fichier corrompu.
        return dict(_SPOTIFY_METADATA_CACHE["data"] or empty)

    if not isinstance(data, dict):
        return empty

    _SPOTIFY_METADATA_CACHE["mtime"] = mtime
    _SPOTIFY_METADATA_CACHE["data"] = data
    return dict(data)


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


def _run_supervisorctl(*args):
    command = [SUPERVISORCTL_BIN, "-c", SUPERVISORD_CONFIG, *args]
    return _run_command(command)


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


# Noms de contrôles ALSA testés, par ordre de préférence. "Master" n'existe pas
# sur toutes les cartes : un HiFiBerry DAC+ expose son volume via "Digital".
_ALSA_CONTROL_CANDIDATES = (
    "Master",
    "Digital",
    "PCM",
    "Speaker",
    "Headphone",
    "Analogue",
    "Playback",
)

_ALSA_MIXER_CONTROL_CACHE = None


def _amixer_command(*args):
    command = ["amixer"]
    if ALSA_MIXER_CARD:
        command.extend(["-c", ALSA_MIXER_CARD])
    command.extend(args)
    return command


def _alsa_control_has_volume(name):
    ok, stdout, _stderr = _run_command(_amixer_command("get", name))
    # Un contrôle utilisable expose un volume de lecture en pourcentage.
    return ok and "Playback" in stdout and "%]" in stdout


def _alsa_control_has_switch(name):
    ok, stdout, _stderr = _run_command(_amixer_command("get", name))
    return ok and "pswitch" in stdout.lower()


def _get_alsa_mixer_control():
    global _ALSA_MIXER_CONTROL_CACHE
    if _ALSA_MIXER_CONTROL_CACHE:
        return _ALSA_MIXER_CONTROL_CACHE

    if ALSA_MIXER_CONTROL:
        _ALSA_MIXER_CONTROL_CACHE = ALSA_MIXER_CONTROL
        return _ALSA_MIXER_CONTROL_CACHE

    ok, stdout, _stderr = _run_command(_amixer_command("scontrols"))
    if not ok:
        return None

    available = re.findall(r"Simple mixer control '([^']+)'", stdout)
    ordered = [name for name in _ALSA_CONTROL_CANDIDATES if name in available]
    ordered.extend(name for name in available if name not in ordered)

    for name in ordered:
        if _alsa_control_has_volume(name):
            _ALSA_MIXER_CONTROL_CACHE = name
            break

    return _ALSA_MIXER_CONTROL_CACHE


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
        control = _get_alsa_mixer_control()
        if not control:
            return None, None, "Aucun contrôle de volume ALSA détecté"

        ok, stdout, stderr = _run_command(_amixer_command("get", control))
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
        control = _get_alsa_mixer_control()
        if not control:
            return False, "Aucun contrôle de volume ALSA détecté"

        target_volume = _clamp(int(volume), 0, 100) if volume is not None else None

        # Certaines cartes (HiFiBerry DAC+ / "Digital") n'ont pas d'interrupteur
        # mute : on l'émule alors en descendant le volume à 0.
        if mute is not None and not _alsa_control_has_switch(control):
            if mute:
                target_volume = 0
            elif target_volume is None:
                target_volume = _clamp(int(SPEAKER_STATE["volume"]), 0, 100)
            mute = None

        if target_volume is not None:
            commands.append(_amixer_command("-q", "set", control, f"{target_volume}%"))
        if mute is not None:
            commands.append(_amixer_command("-q", "set", control, "mute" if mute else "unmute"))

    for command in commands:
        ok, _stdout, stderr = _run_command(command)
        if not ok:
            return False, stderr or "Échec de la commande volume système"

    return True, None


def _sync_system_volume_state(force=False):
    now = time.monotonic()
    if not force and (now - _VOLUME_SYNC_AT[0]) < _STATE_CACHE_TTL_SECONDS:
        return
    _VOLUME_SYNC_AT[0] = now

    volume, muted, _error = _read_system_volume()
    if volume is not None:
        SPEAKER_STATE["volume"] = volume
    if muted is not None:
        SPEAKER_STATE["muted"] = muted


# ── Donnees systeme reelles (reseau, audio, sante) ──────────────────────────
# Tout ce qui est lisible dans /proc ou /sys l'est directement : c'est
# quasi gratuit. Les rares commandes externes (nmcli, vcgencmd) sont mises en
# cache longuement, un dashboard qui relance des sous-processus en boucle
# ayant deja provoque des coupures audio par contention CPU.

_WIFI_SSID_CACHE = {"at": 0.0, "value": None}
_THROTTLED_CACHE = {"at": 0.0, "value": None}
_DAC_NAME_CACHE = [None]

_SSID_CACHE_TTL_SECONDS = 60.0
_THROTTLED_CACHE_TTL_SECONDS = 30.0


def _signal_bars(dbm):
    """Convertit une puissance en dBm en 1..5 barres (0 si inconnue)."""
    if dbm is None:
        return 0
    if dbm >= -50:
        return 5
    if dbm >= -60:
        return 4
    if dbm >= -67:
        return 3
    if dbm >= -75:
        return 2
    return 1


def _read_wifi_signal():
    """Interface et puissance du signal, lues dans /proc/net/wireless."""
    try:
        with open("/proc/net/wireless", "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return None, None

    # Les deux premieres lignes sont des en-tetes.
    for line in lines[2:]:
        if ":" not in line:
            continue
        interface, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 3:
            continue
        try:
            # "-31." -> -31
            level = int(float(fields[2].rstrip(".")))
        except ValueError:
            continue
        return interface.strip(), level

    return None, None


def _read_wifi_ssid():
    now = time.monotonic()
    if (now - _WIFI_SSID_CACHE["at"]) < _SSID_CACHE_TTL_SECONDS:
        return _WIFI_SSID_CACHE["value"]

    _WIFI_SSID_CACHE["at"] = now
    ssid = None

    if shutil.which("iwgetid"):
        ok, stdout, _stderr = _run_command(["iwgetid", "-r"])
        if ok and stdout:
            ssid = stdout.strip()

    if not ssid and shutil.which("nmcli"):
        ok, stdout, _stderr = _run_command(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"]
        )
        if ok:
            for line in stdout.splitlines():
                active, _, name = line.partition(":")
                if active.strip().lower() in {"yes", "oui"} and name.strip():
                    ssid = name.strip()
                    break

    _WIFI_SSID_CACHE["value"] = ssid
    return ssid


def _get_wifi_info():
    interface, dbm = _read_wifi_signal()
    return {
        "connected": dbm is not None,
        "interface": interface,
        "ssid": _read_wifi_ssid() if dbm is not None else None,
        "signal_dbm": dbm,
        "bars": _signal_bars(dbm),
    }


def _get_dac_name():
    """Nom de la carte son, lu une fois dans /proc/asound/cards."""
    if _DAC_NAME_CACHE[0] is not None:
        return _DAC_NAME_CACHE[0]

    card = str(ALSA_MIXER_CARD or "0")
    name = None
    try:
        with open("/proc/asound/cards", "r", encoding="utf-8") as handle:
            for line in handle:
                # Format : " 0 [sndrpihifiberry]: HifiberryDacp - snd_rpi_..."
                stripped = line.strip()
                if not stripped.startswith(card + " "):
                    continue
                _, _, after = stripped.partition("]:")
                label = after.strip()
                if label:
                    name = label.split(" - ")[0].strip() or label
                break
    except OSError:
        pass

    _DAC_NAME_CACHE[0] = name or "Inconnue"
    return _DAC_NAME_CACHE[0]


def _get_audio_stream_info():
    """Format reellement joue, lu dans /proc/asound (vide si silencieux)."""
    card = ALSA_MIXER_CARD or "0"
    path = f"/proc/asound/card{card}/pcm0p/sub0/hw_params"

    info = {"playing": False, "rate": None, "bits": None, "channels": None}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return info

    if "closed" in content:
        return info

    for line in content.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "rate":
            try:
                info["rate"] = int(value.split()[0])
            except (ValueError, IndexError):
                pass
        elif key == "channels":
            try:
                info["channels"] = int(value)
            except ValueError:
                pass
        elif key == "format":
            # "S16_LE" -> 16 bits, "S24_3LE" -> 24 bits
            digits = "".join(c for c in value if c.isdigit())
            if digits:
                try:
                    info["bits"] = int(digits[:2])
                except ValueError:
                    pass

    info["playing"] = info["rate"] is not None
    return info


def _read_throttled_state():
    now = time.monotonic()
    if (now - _THROTTLED_CACHE["at"]) < _THROTTLED_CACHE_TTL_SECONDS:
        return _THROTTLED_CACHE["value"]

    _THROTTLED_CACHE["at"] = now
    value = None

    if shutil.which("vcgencmd"):
        ok, stdout, _stderr = _run_command(["vcgencmd", "get_throttled"])
        if ok and "=" in stdout:
            try:
                value = int(stdout.split("=", 1)[1].strip(), 16)
            except ValueError:
                value = None

    _THROTTLED_CACHE["value"] = value
    return value


def _get_system_health():
    temperature = None
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as handle:
            temperature = round(int(handle.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass

    throttled = _read_throttled_state()
    power_ok = None
    power_label = "Indisponible"

    if throttled is not None:
        # Bit 0 : sous-tension en cours. Bit 16 : sous-tension survenue depuis
        # le demarrage. Bits 1/2 : frequence bridee / throttling en cours.
        if throttled == 0:
            power_ok, power_label = True, "OK"
        elif throttled & 0x1:
            power_ok, power_label = False, "Sous-tension"
        elif throttled & 0x4:
            power_ok, power_label = False, "Throttling thermique"
        elif throttled & 0x10000:
            power_ok, power_label = True, "OK (sous-tension passee)"
        else:
            power_ok, power_label = True, "OK (incident passe)"

    return {
        "temperature_c": temperature,
        "throttled_raw": None if throttled is None else f"0x{throttled:X}",
        "power_ok": power_ok,
        "power_label": power_label,
    }


def _get_service_backend(service):
    return SERVICE_BACKENDS.get(service)


def _get_service_status(service):
    backend = _get_service_backend(service)
    if not backend:
        return SPEAKER_STATE["services"][service]["online"], None

    if SERVICE_MANAGER == "supervisor":
        # supervisorctl status renvoie un code non nul quand le process est
        # arrêté : on se fie donc au libellé d'état, pas au code retour.
        _ok, stdout, stderr = _run_supervisorctl("status", backend["unit"])
        text = (stdout or stderr or "").strip()
        parts = text.split()
        state = parts[1].upper() if len(parts) >= 2 else ""
        return state == "RUNNING", None

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

    if SERVICE_MANAGER == "supervisor":
        # supervisorctl peut renvoyer un code non nul ("already started" /
        # "not running") : l'état réel est confirmé par la boucle de vérification.
        _run_supervisorctl(action, backend["unit"])
    else:
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
    # Inutile avec supervisor : "supervisorctl stop" arrête réellement le process
    # (et un pkill ferait redémarrer le programme si autorestart est actif).
    if not online and SERVICE_MANAGER != "supervisor":
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


def _supervisor_status_all():
    """État de toutes les unités en UN seul appel supervisorctl.

    Lancer supervisorctl coûte un démarrage d'interpréteur Python complet
    (~1,5 s sur un Pi 3) : en faire un par service à chaque rafraîchissement
    saturait le CPU et affamait le thread audio.
    """
    _ok, stdout, stderr = _run_supervisorctl("status")
    states = {}
    for line in (stdout or stderr or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            states[parts[0]] = parts[1].upper() == "RUNNING"
    return states


def _sync_service_states(force=False):
    now = time.monotonic()
    if not force and (now - _SERVICE_SYNC_AT[0]) < _STATE_CACHE_TTL_SECONDS:
        return
    _SERVICE_SYNC_AT[0] = now

    supervisor_states = _supervisor_status_all() if SERVICE_MANAGER == "supervisor" else None

    for service in SPEAKER_STATE["services"]:
        if service not in SERVICE_BACKENDS:
            continue
        if supervisor_states is not None:
            online = supervisor_states.get(SERVICE_BACKENDS[service]["unit"], False)
        else:
            online, _error = _get_service_status(service)
        SPEAKER_STATE["services"][service]["online"] = online

    # Une seule source peut tourner a la fois (exclusivite garantie par
    # systemd via Conflicts=, et reappliquee par _set_active_source). La source
    # active n'est donc pas un etat separe : c'est celle qui tourne reellement.
    running = [key for key, value in SPEAKER_STATE["services"].items() if value["online"]]
    SPEAKER_STATE["active_service"] = running[0] if running else None


def _set_active_source(source):
    """Rend une seule source active, en arretant systematiquement l'autre.

    L'exclusivite est indispensable : les deux services se disputent la carte
    son. Elle est declaree a systemd (Conflicts=), mais on l'applique aussi
    ici pour rester correct meme si les unites n'ont pas ete reinstallees.
    """
    # Arreter les autres sources D'ABORD : elles liberent ainsi la carte son
    # avant que la nouvelle ne tente de l'ouvrir.
    for key in SERVICE_BACKENDS:
        if key == source:
            continue
        if not SPEAKER_STATE["services"][key]["online"]:
            continue
        ok, error = _set_service_online(key, False)
        if not ok:
            return False, error
        if key == "airplay":
            _clear_airplay_metadata()
            _clear_airplay_remote()

    if source is not None:
        ok, error = _set_service_online(source, True)
        if not ok:
            return False, error

    _sync_service_states(force=True)
    return True, None


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

    # Aucune donnee inventee : sans metadonnees d'une source reelle, on
    # l'affiche explicitement plutot que d'afficher une piste fictive.
    current_track = "Aucune lecture"
    current_artist = "—"

    active_service_key = SPEAKER_STATE.get("active_service")
    spotify_metadata = _get_spotify_metadata_snapshot()

    if active_service_key == "airplay":
        metadata = _get_airplay_metadata_snapshot()
        if _is_airplay_metadata_fresh(metadata):
            current_track = metadata.get("title") or "Titre inconnu"
            current_artist = metadata.get("artist") or "Artiste inconnu"
        else:
            current_track = "En attente de metadonnees AirPlay"
            current_artist = "Demarre une lecture AirPlay"
    elif active_service_key == "spotify":
        if spotify_metadata.get("title"):
            current_track = spotify_metadata["title"]
            current_artist = spotify_metadata.get("artist") or "Artiste inconnu"
        else:
            current_track = "En attente de metadonnees Spotify"
            current_artist = "Demarre une lecture Spotify"

    audio_stream = _get_audio_stream_info()
    # L'etat de lecture reel : c'est le materiel qui fait foi, pas un booleen
    # que l'interface aurait bascule de son cote.
    SPEAKER_STATE["is_playing"] = audio_stream["playing"]

    active_service = SPEAKER_STATE["services"].get(active_service_key) if active_service_key else None
    return {
        **SPEAKER_STATE,
        "current_track": current_track,
        "current_artist": current_artist,
        "airplay_metadata": _get_airplay_metadata_snapshot(),
        "airplay_remote": _get_airplay_remote_snapshot(),
        "spotify_metadata": spotify_metadata,
        "wifi": _get_wifi_info(),
        "audio_output": _get_dac_name(),
        "audio_stream": audio_stream,
        "system": _get_system_health(),
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
        # Pas de "Connection: keep-alive" ici : c'est un en-tete hop-by-hop,
        # interdit a une application WSGI (PEP 3333). Le serveur de
        # developpement le tolerait, waitress leve une AssertionError. La
        # connexion persistante est de toute facon geree par HTTP/1.1.
        "X-Accel-Buffering": "no",
    }
    return Response(stream_with_context(event_stream()), headers=headers, mimetype="text/event-stream")


@app.route("/api/playback", methods=["POST"])
def api_playback():
    action = (request.json or {}).get("action", "toggle")

    active_service_key = SPEAKER_STATE.get("active_service")
    if not active_service_key:
        return jsonify({"error": "Aucune source audio active"}), 400

    if not SPEAKER_STATE["services"][active_service_key]["online"]:
        return jsonify({"error": "La source audio active est hors ligne"}), 400

    action = action if action in {"play", "pause", "next", "previous", "toggle"} else "toggle"

    if active_service_key == "airplay":
        # Commande reellement transmise a l'emetteur via DACP.
        ok, error = _send_airplay_playback_command(action)
        if not ok:
            return jsonify({"error": error}), 400
    else:
        # librespot ne se pilote pas depuis l'enceinte : c'est le client
        # Spotify qui controle la lecture. Autant le dire plutot que de faire
        # semblant en basculant un booleen local.
        return (
            jsonify({"error": "Le contrôle de lecture Spotify se fait depuis l'application Spotify."}),
            400,
        )

    # is_playing n'est plus force ici : il est deduit de l'etat reel de la
    # carte son dans _public_state().
    _touch_state()
    return jsonify(_public_state())


@app.route("/api/volume", methods=["POST"])
def api_volume():
    payload = request.json or {}

    # Lecture fraîche : le calcul d'un delta doit partir du volume réel.
    _sync_system_volume_state(force=True)

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

    _sync_system_volume_state(force=True)

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


@app.route("/api/source", methods=["POST"])
def api_source():
    """Selectionne LA source active. Choisir une source demarre son service et
    arrete l'autre : les deux ne peuvent jamais tourner en meme temps."""
    payload = request.json or {}
    source = payload.get("source")

    if source in ("", "none", "aucune", None):
        source = None
    elif source not in SPEAKER_STATE["services"]:
        return jsonify({"error": "Source inconnue"}), 400

    ok, error = _set_active_source(source)
    if not ok:
        return jsonify({"error": error}), 400

    _touch_state()
    return jsonify(_public_state())


_start_shairport_metadata_monitor()

