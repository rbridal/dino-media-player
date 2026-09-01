#!/bin/bash
# Keep the A2DP transport up for 10s after mpv briefly closes the PCM.
# Cheap amps (ZK-1002 / BT-WUZHI) drop audio if the transport is torn down
# and immediately renegotiated.
set -euo pipefail

BLUEALSA_BIN="$(command -v bluealsad || command -v bluealsa)"
if [[ -z "${BLUEALSA_BIN}" ]]; then
  echo "bluealsa is not installed" >&2
  exit 1
fi

UNIT="bluealsa.service"
if ! systemctl list-unit-files "${UNIT}" --no-legend | grep -q .; then
  UNIT="bluealsad.service"
fi

mkdir -p /etc/systemd/system/${UNIT}.d
cat >/etc/systemd/system/${UNIT}.d/keepalive.conf <<EOF
[Service]
ExecStart=
ExecStart=${BLUEALSA_BIN} -S --keep-alive=10 -p a2dp-source
EOF

systemctl daemon-reload
systemctl restart "${UNIT}" || true
echo "Wrote /etc/systemd/system/${UNIT}.d/keepalive.conf"
systemctl cat "${UNIT}" | sed -n '/ExecStart/p'
