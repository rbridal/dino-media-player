# Dino Media Player

<p align="center">
  <img src="logo.svg" width="160" height="160" alt="Green Sinclair-style dinosaur on light gray">
</p>

Lightweight MQTT-controlled media player for Raspberry Pi (headless).

Designed for the outdoor Sinclair dino / Jurassic Park yard setup. Plays local audio files through the 3.5mm jack and is controlled by Home Assistant.

Companion integration: [ha-dino-media-player](https://github.com/rbridal/ha-dino-media-player).

Install path: `/opt/dino-media-player`  
Service user: `dino` / group `dino` (also in `audio`)

## Logo

Green Sinclair-style sauropod on a solid light gray background. Same mark is used in the Home Assistant integration brand assets.

- [`logo.svg`](logo.svg)
- [`brand/icon.svg`](brand/icon.svg)

## Features
- Plays local audio files (mp3, wav, flac, ogg, m4a)
- MQTT commands: `play`, `stop`, `set_source`
- Publishes state, current source, source list, position, duration, availability
- Rescans `/opt/dino-media-player/media` every 5 seconds (no restart to add files)
- systemd service under `dino`
- ALSA output to the Pi analog jack (`plughw:2,0` on a Pi 4)

## Hardware
- Raspberry Pi 4B (Trixie, headless)
- Speakers on the 3.5mm jack
- Outdoors near the dino statue

## Requirements
- Raspberry Pi OS (Trixie or later)
- `mpv`
- Python 3.11+
- MQTT broker reachable from the Pi (Home Assistant Mosquitto is ideal)
- Dedicated `dino` user (do not run as your login account)

## Create the service user

```bash
sudo groupadd --system dino
sudo mkdir -p /opt/dino-media-player
sudo useradd --system \
  --gid dino \
  --groups audio \
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
sudo apt install -y mpv python3-pip python3-venv git
sudo raspi-config nonint do_audio 1

sudo -u dino git clone https://github.com/rbridal/dino-media-player.git /opt/dino-media-player

sudo -u dino mkdir -p /opt/dino-media-player/media
# sudo cp /path/to/jurassic_park_theme.mp3 /opt/dino-media-player/media/
# sudo chown dino:dino /opt/dino-media-player/media/*

sudo -u dino python3 -m venv /opt/dino-media-player/venv
sudo -u dino /opt/dino-media-player/venv/bin/pip install -r /opt/dino-media-player/requirements.txt

sudo -u dino cp /opt/dino-media-player/config.example.yaml /opt/dino-media-player/config.yaml
sudo -u dino nano /opt/dino-media-player/config.yaml

sudo cp /opt/dino-media-player/dino-media-player.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dino-media-player
```

## MQTT topics (default prefix `dino/player`)

**Commands** (`.../command`) JSON:
- `{"action": "play", "source": "jurassic_park_theme.mp3"}`
- `{"action": "stop"}`
- `{"action": "set_source", "source": "jurassic_park_theme.mp3"}`

**State** (published, retained):
- `available` — `online` / `offline`
- `state` — `playing` / `stopped`
- `source` — current filename
- `sources` — JSON list of files in `media/`
- `position` — seconds into the track
- `duration` — track length in seconds

## Adding media

Copy files into `/opt/dino-media-player/media` as user `dino`. The service picks them up within about 5 seconds.

```bash
sudo cp ~/new_track.mp3 /opt/dino-media-player/media/
sudo chown dino:dino /opt/dino-media-player/media/new_track.mp3
```

## License
MIT
