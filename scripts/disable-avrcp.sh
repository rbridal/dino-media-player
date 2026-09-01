#!/bin/bash
# BT-WUZHI sends AVRCP pause about a second after A2DP starts, which mutes
# the amp. Disable the AVRCP plugin so it cannot suspend the transport.
set -euo pipefail
CONF=/etc/bluetooth/main.conf
mkdir -p /etc/bluetooth
if [[ -f "${CONF}" ]] && grep -q '^DisablePlugins' "${CONF}"; then
  sed -i 's/^DisablePlugins.*/DisablePlugins = avrcp,sap/' "${CONF}"
else
  if [[ -f "${CONF}" ]] && grep -q '^\[General\]' "${CONF}"; then
    sed -i '/^\[General\]/a DisablePlugins = avrcp,sap' "${CONF}"
  else
    printf '[General]\nDisablePlugins = avrcp,sap\n' >>"${CONF}"
  fi
fi
systemctl restart bluetooth
sleep 2
rfkill unblock bluetooth || true
hciconfig hci0 up || true
bluetoothctl connect D2:0E:11:F0:91:1D || true
echo "AVRCP plugin disabled. Reconnect BT-WUZHI if needed."
