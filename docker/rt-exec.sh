#!/bin/sh
# Lance la commande passée en argument avec une priorité temps réel
# (SCHED_FIFO) afin que le thread audio ne soit pas préempté par les pics CPU.
#
# Repli sûr : si les capacités manquent (conteneur lancé sans CAP_SYS_NICE ni
# ulimit rtprio), on démarre en ordonnancement normal plutôt que d'échouer.
set -eu

prio="${AUDIO_RT_PRIORITY:-45}"

if [ "$prio" != "0" ] && chrt -f "$prio" true 2>/dev/null; then
    exec chrt -f "$prio" "$@"
fi

echo "[warn] Priorite temps reel indisponible (CAP_SYS_NICE / ulimit rtprio) :" \
     "demarrage en ordonnancement normal, des craquements sont possibles." >&2
exec "$@"
