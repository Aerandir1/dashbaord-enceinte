#!/usr/bin/env bash
# Point d'acces de configuration au demarrage (onboarding).
#
# OPTIONNEL : installe et active seulement si ENABLE_SETUP_HOTSPOT=true.
# Execute par enceinte-hotspot.service (donc en root : appelle directement le
# helper, sans sudo).
#
# Prudence volontaire : on attend que NetworkManager se stabilise, et on ne
# monte le point d'acces QUE si wlan0 n'est connecte a aucun reseau. Sinon on
# ne touche a rien -- pas question de couper un Wi-Fi qui marche.
set -euo pipefail

ENV_FILE="${ENCEINTE_ENV_FILE:-/etc/default/enceinte}"
WIFI_IFACE="wlan0"
HOTSPOT_SSID="Enceinte-Setup"
HOTSPOT_PASSWORD="enceinte-setup"

read_env() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1; }
if [ -f "$ENV_FILE" ]; then
    v="$(read_env HOTSPOT_SSID)";     [ -n "$v" ] && HOTSPOT_SSID="$v"
    v="$(read_env HOTSPOT_PASSWORD)"; [ -n "$v" ] && HOTSPOT_PASSWORD="$v"
fi

# Laisse a NetworkManager le temps de tenter une connexion connue (~60 s max).
for _ in $(seq 1 30); do
    state="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | sed -n "s/^${WIFI_IFACE}://p")"
    case "$state" in
        connected*)
            echo "Wi-Fi connecte ($state) : pas de point d'acces."
            exit 0
            ;;
    esac
    sleep 2
done

echo "Aucun Wi-Fi apres attente : montee du point d'acces « $HOTSPOT_SSID »."
exec /usr/local/bin/enceinte-netctl hotspot-start "$HOTSPOT_SSID" "$HOTSPOT_PASSWORD"
