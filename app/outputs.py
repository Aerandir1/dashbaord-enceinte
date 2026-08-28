"""Choix de la sortie audio physique (HiFiBerry, jack 3,5 mm, HDMI...).

Les cartes sont designees par leur NOM ALSA et jamais par leur index :
ajouter ou retirer une carte renumerote les index, ce qui casserait
silencieusement la configuration.

Changer de sortie revient a demander a CamillaDSP d'ouvrir un autre
peripherique de lecture : il est le seul a ecrire sur le materiel.
"""

import json
import os
import re

from app.config import ACTIVE_OUTPUT_FILE

# Cartes qui ne sont pas des sorties d'enceinte : la boucle est notre propre
# tuyau interne, pas un peripherique d'ecoute.
_EXCLUDED_CARDS = {"Loopback"}

# Libelles lisibles pour le materiel connu ; a defaut on montre le nom ALSA.
_FRIENDLY_LABELS = (
    # ALSA abrege : la carte se decrit "HifiberryDacp", pas "dacplus".
    (re.compile(r"hifiberry.*dacp(lus)?adc", re.I), "HiFiBerry DAC+ ADC"),
    (re.compile(r"hifiberry.*dacp", re.I), "HiFiBerry DAC+"),
    (re.compile(r"hifiberry", re.I), "HiFiBerry"),
    (re.compile(r"headphones|bcm2835", re.I), "Jack 3,5 mm"),
    (re.compile(r"vc4.?hdmi|hdmi", re.I), "HDMI"),
)

# Le jack du Pi est un etage PWM : il n'accepte que du 16 bits, la ou un DAC
# I2S encaisse du 32. On laisse CamillaDSP sortir au plus juste pour chacun.
_FORMAT_BY_PATTERN = (
    (re.compile(r"headphones|bcm2835", re.I), "S16_LE"),
    (re.compile(r"vc4.?hdmi|hdmi", re.I), "S16_LE"),
)
_DEFAULT_FORMAT = "S32_LE"


def _label_for(card_id, description):
    haystack = f"{card_id} {description}"
    for pattern, label in _FRIENDLY_LABELS:
        if pattern.search(haystack):
            return label
    return description or card_id


def _format_for(card_id, description):
    haystack = f"{card_id} {description}"
    for pattern, fmt in _FORMAT_BY_PATTERN:
        if pattern.search(haystack):
            return fmt
    return _DEFAULT_FORMAT


def list_outputs():
    """Sorties disponibles, lues dans /proc/asound (aucun sous-processus)."""
    try:
        with open("/proc/asound/cards", "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return []

    outputs = []
    # Format : " 0 [sndrpihifiberry]: HifiberryDacp - snd_rpi_hifiberry_dacplus"
    for match in re.finditer(r"^\s*(\d+)\s+\[([^\]]+)\]\s*:\s*(.*)$", content, re.M):
        card_id = match.group(2).strip()
        description = match.group(3).split(" - ")[0].strip()
        if card_id in _EXCLUDED_CARDS:
            continue
        # Une carte sans peripherique de lecture ne peut pas etre une sortie.
        if not os.path.isdir(f"/proc/asound/{card_id}/pcm0p"):
            continue
        outputs.append(
            {
                "id": card_id,
                "label": _label_for(card_id, description),
                "device": f"hw:CARD={card_id},DEV=0",
                "format": _format_for(card_id, description),
            }
        )
    return outputs


def get_output(output_id):
    for output in list_outputs():
        if output["id"] == output_id:
            return output
    return None


def load_active_id():
    try:
        with open(ACTIVE_OUTPUT_FILE, "r", encoding="utf-8") as handle:
            value = json.load(handle).get("output")
    except (OSError, ValueError, AttributeError):
        value = None
    return value if isinstance(value, str) else None


def save_active_id(output_id):
    directory = os.path.dirname(ACTIVE_OUTPUT_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{ACTIVE_OUTPUT_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"output": output_id}, handle)
    os.replace(tmp, ACTIVE_OUTPUT_FILE)


def active_output(camilla_device=None):
    """Sortie active.

    On se fie d'abord au peripherique reellement ouvert par CamillaDSP, qui
    fait foi ; le fichier n'est qu'un repli au demarrage.
    """
    outputs = list_outputs()
    if not outputs:
        return None

    if camilla_device:
        match = re.search(r"CARD=([^,]+)", camilla_device)
        card_id = match.group(1) if match else camilla_device.split(":")[-1].split(",")[0]
        for output in outputs:
            if output["id"] == card_id:
                return output

    stored = load_active_id()
    for output in outputs:
        if output["id"] == stored:
            return output

    return outputs[0]
