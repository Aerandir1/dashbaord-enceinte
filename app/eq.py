"""Egaliseur parametrique reel, applique par CamillaDSP.

Le dashboard detient la liste des bandes ; CamillaDSP execute les biquads.
A chaque modification :

  1. on relit la configuration courante de CamillaDSP (GetConfigJson), ce qui
     evite de coder en dur la section "devices" -- elle reste celle qui est
     reellement deployee ;
  2. on y remplace uniquement "filters" et "pipeline" ;
  3. on la renvoie (SetConfigJson) : l'effet est immediat, sans coupure ;
  4. on l'ecrit aussi sur disque, sinon l'egaliseur serait perdu au
     redemarrage de CamillaDSP.
"""

import json
import os
import threading

from app.config import (
    CAMILLADSP_CONFIG_FILE,
    CAMILLADSP_WS_URL,
    EQ_STATE_FILE,
)

try:
    import websocket  # websocket-client
except Exception:  # pragma: no cover
    websocket = None

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


# Types de filtres exposes, tels que CamillaDSP les nomme.
# "gain" n'a de sens que pour les trois premiers ; les autres sont des filtres
# de coupure, ou seuls la frequence et le Q comptent.
BAND_TYPES_WITH_GAIN = ("Peaking", "Lowshelf", "Highshelf")
BAND_TYPES_WITHOUT_GAIN = ("Highpass", "Lowpass", "Notch")
BAND_TYPES = BAND_TYPES_WITH_GAIN + BAND_TYPES_WITHOUT_GAIN

FREQ_MIN, FREQ_MAX = 20.0, 20000.0
GAIN_MIN, GAIN_MAX = -24.0, 24.0
Q_MIN, Q_MAX = 0.1, 10.0
PREAMP_MIN, PREAMP_MAX = -24.0, 12.0

# Trois bandes au demarrage, comme un Pro-Q vierge : l'utilisateur en ajoute.
DEFAULT_BANDS = [
    {"id": "b1", "type": "Lowshelf", "freq": 100.0, "gain": 0.0, "q": 0.7, "enabled": True},
    {"id": "b2", "type": "Peaking", "freq": 1000.0, "gain": 0.0, "q": 1.0, "enabled": True},
    {"id": "b3", "type": "Highshelf", "freq": 8000.0, "gain": 0.0, "q": 0.7, "enabled": True},
]

_LOCK = threading.Lock()


def _clamp(value, low, high):
    return max(low, min(high, value))


def sanitize_band(raw, index=0):
    """Normalise une bande venant du client. Retourne None si inexploitable."""
    if not isinstance(raw, dict):
        return None

    band_type = str(raw.get("type", "Peaking"))
    if band_type not in BAND_TYPES:
        band_type = "Peaking"

    try:
        freq = _clamp(float(raw.get("freq", 1000.0)), FREQ_MIN, FREQ_MAX)
        gain = _clamp(float(raw.get("gain", 0.0)), GAIN_MIN, GAIN_MAX)
        q = _clamp(float(raw.get("q", 1.0)), Q_MIN, Q_MAX)
    except (TypeError, ValueError):
        return None

    if band_type not in BAND_TYPES_WITH_GAIN:
        gain = 0.0

    return {
        "id": str(raw.get("id") or f"b{index + 1}"),
        "type": band_type,
        "freq": round(freq, 2),
        "gain": round(gain, 2),
        "q": round(q, 3),
        "enabled": bool(raw.get("enabled", True)),
    }


def sanitize_bands(raw_bands):
    if not isinstance(raw_bands, list):
        return []
    bands = []
    seen = set()
    for index, raw in enumerate(raw_bands[:24]):
        band = sanitize_band(raw, index)
        if band is None:
            continue
        # Les identifiants alimentent les noms de filtres CamillaDSP : ils
        # doivent rester uniques.
        while band["id"] in seen:
            band["id"] += "_"
        seen.add(band["id"])
        bands.append(band)
    return bands


def auto_preamp_db(bands):
    """Attenuation evitant l'ecretage quand des bandes amplifient.

    Un boost de +6 dB fait saturer un signal deja proche du maximum : on
    compense en abaissant le niveau d'autant.
    """
    boosts = [b["gain"] for b in bands if b["enabled"] and b["gain"] > 0]
    if not boosts:
        return 0.0
    return round(-max(boosts), 2)


# ── Etat persistant ─────────────────────────────────────────────────────────

def _default_state():
    return {"bands": [dict(b) for b in DEFAULT_BANDS], "preamp": 0.0, "auto_preamp": True}


def load_state():
    try:
        with open(EQ_STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return _default_state()

    if not isinstance(data, dict):
        return _default_state()

    state = _default_state()
    state["bands"] = sanitize_bands(data.get("bands")) or state["bands"]
    try:
        state["preamp"] = _clamp(float(data.get("preamp", 0.0)), PREAMP_MIN, PREAMP_MAX)
    except (TypeError, ValueError):
        state["preamp"] = 0.0
    state["auto_preamp"] = bool(data.get("auto_preamp", True))
    return state


def save_state(state):
    directory = os.path.dirname(EQ_STATE_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{EQ_STATE_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, EQ_STATE_FILE)


# ── Traduction vers CamillaDSP ──────────────────────────────────────────────

def build_filters_and_pipeline(bands, preamp_db):
    """Traduit les bandes en section "filters" + "pipeline" de CamillaDSP."""
    filters = {
        "preamp": {
            "type": "Gain",
            "parameters": {"gain": float(preamp_db), "inverted": False},
        }
    }
    names = ["preamp"]

    for band in bands:
        if not band["enabled"]:
            continue
        name = f"band_{band['id']}"
        parameters = {
            "type": band["type"],
            "freq": band["freq"],
            "q": band["q"],
        }
        if band["type"] in BAND_TYPES_WITH_GAIN:
            parameters["gain"] = band["gain"]
        filters[name] = {"type": "Biquad", "parameters": parameters}
        names.append(name)

    pipeline = [{"type": "Filter", "channels": [0, 1], "names": names}]
    return filters, pipeline


# ── Dialogue avec CamillaDSP ────────────────────────────────────────────────

class CamillaError(Exception):
    pass


def _command(connection, payload):
    connection.send(json.dumps(payload))
    reply = json.loads(connection.recv())
    if not isinstance(reply, dict):
        raise CamillaError("Réponse inattendue de CamillaDSP")
    key = payload if isinstance(payload, str) else next(iter(payload))
    body = reply.get(key) or {}
    if body.get("result") != "Ok":
        raise CamillaError(body.get("value") or f"CamillaDSP a refusé « {key} »")
    return body.get("value")


def apply_state(state):
    """Applique l'egaliseur a chaud, puis le persiste.

    Retourne (True, None) ou (False, message d'erreur lisible).
    """
    if websocket is None:
        return False, "Le module websocket-client est absent : impossible de piloter CamillaDSP."

    bands = state["bands"]
    preamp = auto_preamp_db(bands) if state.get("auto_preamp", True) else state.get("preamp", 0.0)
    filters, pipeline = build_filters_and_pipeline(bands, preamp)

    with _LOCK:
        try:
            connection = websocket.create_connection(CAMILLADSP_WS_URL, timeout=4)
        except Exception as exc:
            return False, f"CamillaDSP est injoignable ({CAMILLADSP_WS_URL}) : {exc}"

        try:
            current = json.loads(_command(connection, "GetConfigJson"))
            # On ne touche pas a "devices" : la chaine audie deployee reste
            # la reference, on ne fait que remplacer le traitement.
            current["filters"] = filters
            current["pipeline"] = pipeline
            _command(connection, {"SetConfigJson": json.dumps(current)})
        except CamillaError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Échec de l'application de l'égaliseur : {exc}"
        finally:
            try:
                connection.close()
            except Exception:
                pass

    state["preamp_applied"] = preamp
    try:
        save_state(state)
        _persist_camilla_config(current)
    except OSError as exc:
        # L'egaliseur est actif, seule la persistance a echoue : on le signale
        # sans faire echouer l'operation.
        return True, f"Égaliseur appliqué, mais non enregistré ({exc})."

    return True, None


def _persist_camilla_config(config):
    """Ecrit la configuration sur disque pour survivre a un redemarrage."""
    if yaml is not None:
        content = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    else:
        # JSON est un YAML valide : repli acceptable si PyYAML manque.
        content = json.dumps(config, ensure_ascii=False, indent=2)

    tmp = f"{CAMILLADSP_CONFIG_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(tmp, CAMILLADSP_CONFIG_FILE)


def get_playback_device():
    """Peripherique de lecture actuellement ouvert par CamillaDSP."""
    if websocket is None:
        return None
    try:
        connection = websocket.create_connection(CAMILLADSP_WS_URL, timeout=3)
    except Exception:
        return None
    try:
        config = json.loads(_command(connection, "GetConfigJson"))
        return (config.get("devices", {}).get("playback", {}) or {}).get("device")
    except Exception:
        return None
    finally:
        try:
            connection.close()
        except Exception:
            pass


def set_playback_device(device, audio_format):
    """Fait basculer CamillaDSP sur une autre sortie physique.

    CamillaDSP referme le peripherique courant et ouvre le nouveau : une breve
    coupure du son est normale pendant la bascule.
    """
    if websocket is None:
        return False, "Le module websocket-client est absent : impossible de piloter CamillaDSP."

    with _LOCK:
        try:
            connection = websocket.create_connection(CAMILLADSP_WS_URL, timeout=5)
        except Exception as exc:
            return False, f"CamillaDSP est injoignable ({CAMILLADSP_WS_URL}) : {exc}"

        try:
            config = json.loads(_command(connection, "GetConfigJson"))
            playback = config.setdefault("devices", {}).setdefault("playback", {})
            playback["device"] = device
            if audio_format:
                playback["format"] = audio_format
            _command(connection, {"SetConfigJson": json.dumps(config)})
        except CamillaError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Impossible de changer de sortie : {exc}"
        finally:
            try:
                connection.close()
            except Exception:
                pass

    try:
        _persist_camilla_config(config)
    except OSError as exc:
        return True, f"Sortie changée, mais non enregistrée ({exc})."

    return True, None


def camilla_status():
    """Etat de CamillaDSP, pour que l'interface sache si l'EQ est operant."""
    if websocket is None:
        return {"available": False, "reason": "module websocket-client absent"}
    try:
        connection = websocket.create_connection(CAMILLADSP_WS_URL, timeout=2)
    except Exception:
        return {"available": False, "reason": "CamillaDSP injoignable"}
    try:
        state = _command(connection, "GetState")
        return {"available": True, "state": state}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    finally:
        try:
            connection.close()
        except Exception:
            pass
