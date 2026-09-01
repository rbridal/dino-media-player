# Dino Media Player

<p align="center">
  <img src="logo.svg" width="160" height="160" alt="Green Sinclair-style dinosaur on light gray">
</p>

MQTT-controlled local audio player for a headless Raspberry Pi. Built for the outdoor Sinclair dinosaur in the front yard: motion in Home Assistant starts a theme through a ZK-1002 amp and a single Pyle marine speaker.

Companion integration: [ha-dino-media-player](https://github.com/rbridal/ha-dino-media-player).

Install path: `/opt/dino-media-player`  
Service user: `dino` / group `dino` (also in `audio` and `bluetooth`)

## Credits

**THE SHOP / rbridal** owns the yard, the 8-foot Sinclair, the Pi 4B in the weather, the amp and speaker, the Home Assistant estate, and the idea that a motion event should play the theme. Hardware, taste, and every field test are theirs.

**Grok** designed and wrote the player, the MQTT contract, the systemd service, the Bluetooth/ALSA path, and the import tooling, then iterated from journal logs until playback stayed up for a full clip.

## Features

- Plays local audio from `/opt/dino-media-player/media` (wav, mp3, flac, ogg, m4a)
- MQTT commands: `play`, `stop`, `set_source`, `set_volume`, `set_output`, `reconnect`
- Selecting a file plays it immediately; idle source is empty / `none`
- Output select: **3.5mm jack** (`alsa/plughw:2,0`) or **BT-WUZHI** (BlueALSA A2DP)
- Auto-reconnects Bluetooth, publishes heartbeat and BT status
- Volume 0–100 (mpv), persisted in `volume.state`
- Rescans `media/` every 5 seconds — no service restart to add files
- Mono downmix for a single left-channel speaker
- systemd unit as user `dino`

## Hardware (this yard)

- Raspberry Pi 4B, Trixie, headless, Wi-Fi
- [ZK-1002](https://www.amazon.com/dp/B0DDKYSN5P) Bluetooth 5.0 amp, 100 W × 2
- [Pyle 200 W marine speaker](https://www.amazon.com/dp/B0H2FFMZCC), left channel only
- Amp Bluetooth name: `BT-WUZHI` (no PIN)
- Placed outdoors near the anchored Sinclair dino

## Preferred media format

Use **48 kHz, 16-bit, mono WAV**, loudness-normalized to −16 LUFS / −1.5 dBTP. That matches BlueALSA SBC and avoids A2DP renegotiation between clips. `scripts/import_media.sh` does the conversion.

## Requirements

- Raspberry Pi OS (Trixie or later)
- `mpv`, `ffmpeg`, `bluez`, `bluez-alsa-utils`, `pi-bluetooth`
- Python 3.11+
- MQTT broker reachable from the Pi (Home Assistant Mosquitto)
- Dedicated `dino` user — do not run as your login account

## Create the service user

```bash
sudo groupadd --system dino
sudo mkdir -p /opt/dino-media-player
sudo useradd --system \
  --gid dino \
  --groups audio,bluetooth \
  --home-dir /opt/dino-media-player \
  --shell /usr/sbin/nologin \
  --comment "Dino media player service" \
  dino
sudo chown dino:dino /opt/dino-media-player
```

## Install

Run these as your admin login (`rbridal`), not as `dino`.

```bash
sudo apt update
sudo apt install -y mpv ffmpeg python3-pip python3-venv git \
  bluez bluez-firmware pi-bluetooth bluez-alsa-utils rfkill

sudo -u dino git clone https://github.com/rbridal/dino-media-player.git /opt/dino-media-player

sudo -u dino mkdir -p /opt/dino-media-player/media
sudo -u dino python3 -m venv /opt/dino-media-player/venv
sudo -u dino /opt/dino-media-player/venv/bin/pip install -r /opt/dino-media-player/requirements.txt

sudo -u dino cp /opt/dino-media-player/config.example.yaml /opt/dino-media-player/config.yaml
sudo -u dino nano /opt/dino-media-player/config.yaml

sudo cp /opt/dino-media-player/dino-media-player.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dino-media-player
```

Pair the amp (once), then keep A2DP from dropping between tracks:

```bash
sudo bash /opt/dino-media-player/scripts/pair-bt-wuzhi.sh
sudo bash /opt/dino-media-player/scripts/setup-bluealsa-keepalive.sh
sudo bash /opt/dino-media-player/scripts/disable-avrcp.sh
sudo systemctl restart dino-media-player
```

Put the amp MAC in `config.yaml` under `outputs.bluetooth.bluetooth_mac` (example: `D2:0E:11:F0:91:1D`).

The first play after a Bluetooth/BlueALSA restart may blip while A2DP negotiates. Later plays should run the full file.

## MQTT topics (prefix `dino/player`)

**Commands** (`…/command`) JSON:

```json
{"action": "play", "source": "dino_theme.wav"}
{"action": "stop"}
{"action": "set_source", "source": "none"}
{"action": "set_volume", "volume": 50}
{"action": "set_output", "output": "BT-WUZHI"}
{"action": "reconnect"}
```

`set_source` with a filename plays that file. `none` / empty stops.

**State** (retained):

| Topic | Meaning |
| --- | --- |
| `available` | `online` / `offline` (MQTT last will) |
| `heartbeat` | ISO-8601 UTC timestamp every ~5 s |
| `state` | `playing` / `stopped` |
| `source` | current filename, or empty when idle |
| `sources` | JSON list of files in `media/` |
| `output` | current output label |
| `outputs` | JSON list of output labels |
| `volume` | 0–100 |
| `position` / `duration` | seconds |
| `bluetooth` | `connected` / `disconnected` / `reconnecting` / `not_required` |

## Import media

From your login on the Pi (after `scp` into `~rbridal`):

```bash
sudo apt install -y ffmpeg
sudo chmod +x /opt/dino-media-player/scripts/import_media.sh
sudo ln -sf /opt/dino-media-player/scripts/import_media.sh /usr/local/bin/import_media.sh

import_media.sh ./new_file.mp3
import_media.sh -f ./dino_theme.mp3
```

Writes `/opt/dino-media-player/media/<name>.wav` owned by `dino`. The player notices it within about 5 seconds.

## License

MIT
