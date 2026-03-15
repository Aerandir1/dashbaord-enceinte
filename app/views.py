from datetime import datetime, timezone

from flask import jsonify, render_template, request

from app import app


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


def _clamp(value, low, high):
    return max(low, min(high, value))


def _touch_state():
    SPEAKER_STATE["updated_at"] = datetime.now(timezone.utc).isoformat()


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

    if "online" in payload:
        SPEAKER_STATE["services"][service]["online"] = bool(payload["online"])

    action = payload.get("action")
    if action == "toggle":
        current = SPEAKER_STATE["services"][service]["online"]
        SPEAKER_STATE["services"][service]["online"] = not current

    if payload.get("select") or action == "select":
        if not SPEAKER_STATE["services"][service]["online"]:
            return jsonify({"error": "Service hors ligne"}), 400
        SPEAKER_STATE["active_service"] = service

    if (
        SPEAKER_STATE.get("active_service")
        and not SPEAKER_STATE["services"][SPEAKER_STATE["active_service"]]["online"]
    ):
        fallback = next(
            (key for key, value in SPEAKER_STATE["services"].items() if value["online"]),
            None,
        )
        SPEAKER_STATE["active_service"] = fallback

    _touch_state()
    return jsonify(_public_state())

