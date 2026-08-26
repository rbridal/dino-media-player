# Dino Media Player

Lightweight MQTT-controlled media player for Raspberry Pi (headless).

Designed for the outdoor Jurassic Park Dino setup. Plays local audio files through the 3.5mm jack and is controlled by Home Assistant.

Install path: `/opt/dino-media-player`  
Service user: `dino` / group `dino` (also in `audio`)

## Features
- Plays local audio files (mp3, wav, flac, etc.)
- Controlled via MQTT (play / pause / resume / stop / set source)
- Publishes current state and available sources
- Designed for Raspberry Pi OS Trixie (headless)
- Runs as a systemd service under `dino`

## Hardware
- Raspberry Pi 4B
- Speakers connected to 3.5mm audio jack
- Placed outdoors near the Dino

## Requirements
- Raspberry Pi OS (Trixie or later)
- `mpv`
- Python 3.11+
- MQTT broker reachable from the Pi (Home Assistant Mosquitto is ideal)
- Dedicated `dino` user (do not run as your login account)

## Create the service user

```bash
sudo groupadd --system dino
sudo useradd --system \
  --gid dino \
  --groups audio \
  --home-dir /opt/dino-media-player \
  --create-home \
  --shell /usr/sbin/nologin \
  --comment "Dino media player service" \
  dino
```

## Quick Start

Run these as your admin login (`rbridal`), not as `dino`.

```bash
sudo apt update
sudo apt install -y mpv python3-pip python3-venv git

# Force analog audio output
sudo raspi-config nonint do_audio 1

# Clone into /opt (home dir may already exist from useradd)
sudo mkdir -p /opt/dino-media-player
sudo chown dino:dino /opt/dino-media-player

sudo -u dino git clone https://github.com/rbridal/dino-media-player.git /opt/dino-media-player
cd /opt/dino-media-player

sudo -u dino mkdir -p media
# copy audio files into /opt/dino-media-player/media/
# sudo cp /path/to/jurassic_park_theme.mp3 /opt/dino-media-player/media/
# sudo chown dino:dino /opt/dino-media-player/media/*

sudo -u dino python3 -m venv /opt/dino-media-player/venv
sudo -u dino /opt/dino-media-player/venv/bin/pip install -r /opt/dino-media-player/requirements.txt

sudo -u dino cp /opt/dino-media-player/config.example.yaml /opt/dino-media-player/config.yaml
sudo -u dino nano /opt/dino-media-player/config.yaml

sudo cp /opt/dino-media-player/dino-media-player.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dino-media-player
sudo systemctl status dino-media-player --no-pager
```

## MQTT Topics (default)

**Commands** (subscribe):
- `dino/player/command` → JSON `{"action": "play|pause|resume|stop|set_source", "source": "filename.mp3"}`

**State** (publish):
- `dino/player/state` → `playing` / `paused` / `stopped` / `idle`
- `dino/player/source` → current filename
- `dino/player/sources` → JSON list of available files
- `dino/player/available` → `online` / `offline`

## License
MIT
