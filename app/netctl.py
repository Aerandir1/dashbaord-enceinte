"""Pilotage reseau et renommage, via le helper privilegie « enceinte-netctl ».

Le dashboard tourne sans droits root ; toute action systeme (changer un nom de
service, se connecter a un Wi-Fi, monter un point d'acces) passe par un unique
script root appele en « sudo -n ». Ce module ne fait que construire les appels
et interpreter les sorties -- il ne manipule jamais nmcli directement.

Aucun argument n'est passe a un shell : subprocess recoit une LISTE, donc un
SSID ou un mot de passe biscornu ne peut pas s'echapper en commande.
"""

import subprocess

from app.config import (
    NETCTL_BIN,
    NETCTL_USE_SUDO,
    HOTSPOT_SSID,
    HOTSPOT_PASSWORD,
)


class NetctlError(Exception):
    pass


def _run(args, timeout=30):
    command = ["sudo", "-n", NETCTL_BIN, *args] if NETCTL_USE_SUDO else [NETCTL_BIN, *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise NetctlError("Helper reseau introuvable (enceinte-netctl non installe ?).")
    except subprocess.TimeoutExpired:
        raise NetctlError("Le helper reseau n'a pas repondu a temps.")
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise NetctlError(message or f"echec de « {' '.join(args)} »")
    return result.stdout


def _split_terse(line):
    """Decoupe une ligne « nmcli -t » sur les ':' non echappes.

    nmcli echappe ':' et '\\' par un antislash ; un naif split(':') couperait
    au milieu d'un SSID contenant deux-points.
    """
    fields, buf, escaped = [], [], False
    for char in line:
        if escaped:
            buf.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    fields.append("".join(buf))
    return fields


# ── Renommage ───────────────────────────────────────────────────────────────

def set_name(name):
    """Propage le nom aux services (Spotify, AirPlay), au hostname/mDNS.

    Redemarre librespot et shairport-sync : une breve coupure est normale.
    """
    return _run(["set-name", name], timeout=45)


# ── Wi-Fi ─────────────────────────────────────────────────────────────────

def wifi_scan():
    """Reseaux visibles, dedupliques par SSID et tries par signal decroissant."""
    output = _run(["wifi-scan"], timeout=30)
    best = {}
    for raw in output.splitlines():
        if not raw.strip():
            continue
        parts = _split_terse(raw)
        if len(parts) < 4:
            continue
        in_use, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
        if not ssid:  # reseau masque : rien a proposer
            continue
        try:
            signal = int(signal)
        except ValueError:
            signal = 0
        current = best.get(ssid)
        if current is None or signal > current["signal"]:
            best[ssid] = {
                "ssid": ssid,
                "signal": signal,
                "secured": bool(security and security != "--"),
                "active": in_use.strip() == "*",
            }
    return sorted(best.values(), key=lambda n: (not n["active"], -n["signal"]))


def wifi_status():
    """SSID connecte (ou None) et etat de connectivite Internet."""
    output = _run(["wifi-status"], timeout=15)
    ssid = None
    state = None
    connectivity = None
    for line in output.splitlines():
        if line.startswith("CONNECTIVITY:"):
            connectivity = line.split(":", 1)[1].strip() or None
            continue
        parts = _split_terse(line)
        if len(parts) >= 3:
            state = parts[1] or None
            connection = parts[2] or None
            # « connected » -> le nom de connexion est le SSID actif.
            if state and state.startswith("connected") and connection and connection != "--":
                ssid = connection
    return {"ssid": ssid, "state": state, "connectivity": connectivity}


def wifi_connect(ssid, password=""):
    """Se connecte a un reseau. Leve NetctlError avec un message lisible."""
    if not ssid:
        raise NetctlError("SSID manquant.")
    return _run(["wifi-connect", ssid, password or ""], timeout=40)


def wifi_forget(ssid):
    return _run(["wifi-forget", ssid], timeout=15)


# ── Point d'acces (onboarding) ──────────────────────────────────────────────

def hotspot_start(ssid=None, password=None):
    return _run(
        ["hotspot-start", ssid or HOTSPOT_SSID, password if password is not None else HOTSPOT_PASSWORD],
        timeout=30,
    )


def hotspot_stop():
    return _run(["hotspot-stop"], timeout=30)


def hotspot_active():
    try:
        return _run(["hotspot-status"], timeout=10).strip() == "active"
    except NetctlError:
        return False
