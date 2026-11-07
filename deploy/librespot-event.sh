#!/bin/sh
# Hook appele par librespot a chaque evenement de lecture (option --onevent).
#
# librespot expose les metadonnees de la piste dans l'environnement
# (PLAYER_EVENT, NAME, ARTISTS, ALBUM, DURATION_MS, URI...). On les ecrit en
# JSON dans un fichier que le dashboard relit pour afficher le titre en cours.
#
# Ce script est volontairement agnostique aux noms d'evenements : tout
# evenement portant un NAME rafraichit les metadonnees, et seuls les
# evenements de fin les effacent.
set -u

OUT="${LIBRESPOT_METADATA_FILE:-/run/enceinte/spotify-metadata.json}"

case "${PLAYER_EVENT:-}" in
    stopped|end_of_track|session_disconnected|unavailable)
        rm -f "$OUT"
        exit 0
        ;;
esac

# Lecture / pause : seul l'etat change, on le note dans le fichier existant
# (le protocole Onde en a besoin : la carte son, toujours ouverte par
# CamillaDSP, ne dit pas si la musique est en pause).
case "${PLAYER_EVENT:-}" in
    playing|paused|started)
        [ -f "$OUT" ] || exit 0
        python3 - "$OUT" "${PLAYER_EVENT}" <<'PY'
import json
import os
import sys
import tempfile

path, event = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, ValueError):
    sys.exit(0)
payload["playback"] = "paused" if event == "paused" else "playing"
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".spotify-metadata.")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
except BaseException:
    os.unlink(tmp)
    raise
PY
        exit 0
        ;;
esac

# Evenement sans metadonnees (volume_changed, position_correction...) :
# rien a publier, on conserve l'etat precedent.
[ -n "${NAME:-}" ] || exit 0

mkdir -p "$(dirname "$OUT")" 2>/dev/null || true

# python3 pour un echappement JSON correct (les titres contiennent des
# guillemets, des accents, des antislashs...).
python3 - "$OUT" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

path = sys.argv[1]

# librespot separe les artistes multiples par des sauts de ligne.
raw_artists = os.environ.get("ARTISTS", "")
artists = [a.strip() for a in raw_artists.replace(",", "\n").splitlines() if a.strip()]


def _int(name):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return None


# Pochettes (librespot >= 0.5) : URL i.scdn.co, une par ligne, de la plus
# grande a la plus petite selon les versions ; on garde la premiere.
covers = [c.strip() for c in os.environ.get("COVERS", "").splitlines() if c.strip()]

payload = {
    "title": os.environ.get("NAME") or None,
    "artist": ", ".join(artists) or None,
    "album": os.environ.get("ALBUM") or None,
    "duration_ms": _int("DURATION_MS"),
    "uri": os.environ.get("URI") or None,
    "event": os.environ.get("PLAYER_EVENT") or None,
    "cover_url": covers[0] if covers else None,
    "playback": "playing",
    "updated_at": datetime.now(timezone.utc).isoformat(),
}

# Ecriture atomique : le dashboard peut lire le fichier a tout moment.
directory = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(dir=directory, prefix=".spotify-metadata.")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
except BaseException:
    os.unlink(tmp)
    raise
PY
