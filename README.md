# Dino Media Player

Lightweight MQTT-controlled media player for Raspberry Pi (headless).

Designed for the outdoor Jurassic Park Dino setup. Plays local audio files through the 3.5mm jack and is controlled by Home Assistant.

## Features
- Plays local audio files (mp3, wav, flac, etc.)
- Controlled via MQTT (play / pause / resume / stop / set source)
- Publishes current state and available sources
- Designed for Raspberry Pi OS Trixie (headless)
- Runs as a systemd service

## Hardware
- Raspberry Pi 4B
- Speakers connected to 3.5mm audio jack
- Placed outdoors near the Dino

## Requirements
- Raspberry Pi OS (Trixie or later)
- `mpv`
- Python 3.11+
- MQTT broker reachable from the Pi (Home Assistant Mosquitto is ideal)

## Quick Start

```bash
sudo apt update
sudo apt install -y mpv python3-pip python3-venv git

# Force analog audio output
sudo raspi-config nonint do_audio 1

# Clone
cd /home/pi
git clone https://github.com/rbridal/dino-media-player.git
cd dino-media-player

# Create media folder and drop your audio files here
mkdir -p media
# (copy jurassic_park_theme.mp3 into media/)

# Setup Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Edit config
cp config.example.yaml config.yaml
nano config.yaml   # set MQTT host, credentials, etc.

# Install as service
sudo cp dino-media-player.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dino-media-player
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
