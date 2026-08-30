#!/bin/bash
# Pair the ZK-1002 amp (advertises as BT-WUZHI) for the dino service user.
set -euo pipefail

NAME="${1:-BT-WUZHI}"

echo "Installing Bluetooth + BlueALSA..."
sudo apt update
sudo apt install -y bluez bluez-alsa-utils

sudo usermod -aG bluetooth dino
sudo systemctl enable --now bluetooth

sudo mkdir -p /etc/systemd/system/bluealsa.service.d
sudo tee /etc/systemd/system/bluealsa.service.d/override.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/bluealsa -p a2dp-source
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now bluealsa || sudo systemctl restart bluealsa || true

echo "Scanning 15s for $NAME ..."
sudo bluetoothctl --timeout 8 power on || true
sudo bluetoothctl --timeout 8 pairable on || true
sudo bluetoothctl --timeout 8 agent NoInputNoOutput || true
sudo bluetoothctl --timeout 8 default-agent || true
sudo bluetoothctl --timeout 15 scan on || true

MAC="$(bluetoothctl devices | awk -v n="$NAME" 'index($0, n){print $2; exit}')"
if [[ -z "${MAC}" ]]; then
  echo "Device $NAME not found. Nearby devices:"
  bluetoothctl devices
  exit 1
fi

echo "Found $NAME at $MAC"
sudo bluetoothctl --timeout 20 pair "$MAC" || true
sudo bluetoothctl trust "$MAC"
sudo bluetoothctl connect "$MAC"

sudo tee /etc/asound.conf >/dev/null <<EOF
defaults.bluealsa.interface "hci0"
defaults.bluealsa.device "$MAC"
defaults.bluealsa.profile "a2dp"
EOF

CFG=/opt/dino-media-player/config.yaml
if [[ -f "$CFG" ]]; then
  sudo python3 - "$CFG" "$MAC" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
mac = sys.argv[2]
text = p.read_text()
if "bluetooth_mac:" in text:
    import re
    text = re.sub(r'(bluetooth_mac:\s*)("[^"]*"|\S+)', rf'\1"{mac}"', text, count=1)
    p.write_text(text)
    print("Updated", p, "bluetooth_mac", mac)
else:
    print("Add bluetooth_mac to config.yaml manually:", mac)
PY
fi

echo
echo "Test (amp volume up, BT connected):"
echo "  sudo -u dino aplay -D bluealsa /usr/share/sounds/alsa/Front_Center.wav"
echo "Then: sudo systemctl restart dino-media-player"
