#!/usr/bin/env python3
"""Dino Media Player - MQTT controlled local audio player for Raspberry Pi."""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

import paho.mqtt.client as mqtt
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dino-player")

IPC_PATH = "/tmp/dino-mpv.sock"
SCAN_SECONDS = 5
STATUS_SECONDS = 1


class DinoPlayer:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.media_dir = Path(self.cfg.get("media_dir", "./media")).resolve()
        self.topic_prefix = self.cfg["mqtt"].get("topic_prefix", "dino/player")
        self.volume = self.cfg.get("volume", 80)

        self.mpv_process: Optional[subprocess.Popen] = None
        self.current_source: Optional[str] = None
        self.state = "stopped"
        self.position = 0.0
        self.duration = 0.0
        self._last_sources: List[str] = []
        self._stop_requested = False

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        if self.cfg["mqtt"].get("username"):
            self.client.username_pw_set(
                self.cfg["mqtt"]["username"],
                self.cfg["mqtt"].get("password", ""),
            )

        self._running = True

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        log.info(f"Connected to MQTT broker (rc={reason_code})")
        client.subscribe(f"{self.topic_prefix}/command")
        self._publish_availability("online")
        self._publish_sources(force=True)
        self._publish_state()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            action = payload.get("action", "").lower()
            source = payload.get("source")
            log.info(f"Command received: {action} source={source}")
            if action == "play":
                self.play(source)
            elif action == "stop":
                self.stop()
            elif action == "set_source":
                if source:
                    self.current_source = source
                    self._publish_state()
            else:
                log.warning(f"Unknown action: {action}")
        except Exception as e:
            log.error(f"Failed to process message: {e}")

    def get_sources(self) -> List[str]:
        if not self.media_dir.exists():
            return []
        files: List[str] = []
        for ext in ("*.mp3", "*.wav", "*.flac", "*.ogg", "*.m4a"):
            files.extend([p.name for p in self.media_dir.glob(ext)])
        return sorted(files)

    def _publish(self, topic_suffix: str, payload: str, retain: bool = True):
        self.client.publish(f"{self.topic_prefix}/{topic_suffix}", payload, retain=retain)

    def _publish_availability(self, status: str):
        self._publish("available", status)

    def _publish_sources(self, force: bool = False):
        sources = self.get_sources()
        if force or sources != self._last_sources:
            self._last_sources = sources
            self._publish("sources", json.dumps(sources))
            log.info(f"Available sources: {sources}")

    def _publish_state(self):
        self._publish("state", self.state)
        self._publish("source", self.current_source or "")
        self._publish("position", f"{self.position:.1f}")
        self._publish("duration", f"{self.duration:.1f}")

    def _mpv_cmd(self, command: list) -> Optional[dict]:
        if not os.path.exists(IPC_PATH):
            return None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.4)
            sock.connect(IPC_PATH)
            sock.sendall((json.dumps({"command": command}) + "\n").encode())
            data = sock.recv(4096).decode().strip()
            sock.close()
            if data:
                return json.loads(data.splitlines()[0])
        except OSError:
            return None
        except json.JSONDecodeError:
            return None
        return None

    def play(self, source: Optional[str] = None):
        if source:
            self.current_source = source
        if not self.current_source:
            sources = self.get_sources()
            if not sources:
                log.error("No media files found")
                return
            self.current_source = sources[0]

        filepath = self.media_dir / self.current_source
        if not filepath.exists():
            self._publish_sources(force=True)
            log.error(f"File not found: {filepath}")
            return

        self.stop(publish=False)

        try:
            os.unlink(IPC_PATH)
        except FileNotFoundError:
            pass

        ao = self.cfg.get("audio_output") or "alsa"
        cmd = [
            "mpv",
            "--no-video",
            "--really-quiet",
            f"--ao={ao}",
            f"--volume={self.volume}",
            f"--input-ipc-server={IPC_PATH}",
            str(filepath),
        ]
        audio_device = self.cfg.get("audio_device") or ""
        if audio_device:
            cmd.insert(3, f"--audio-device={audio_device}")

        log.info(f"Playing: {filepath}")
        self._stop_requested = False
        self.mpv_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.state = "playing"
        self.position = 0.0
        self._publish_state()
        threading.Thread(target=self._monitor_mpv, daemon=True).start()
        threading.Thread(target=self._poll_status, daemon=True).start()

    def _monitor_mpv(self):
        proc = self.mpv_process
        if not proc:
            return
        _, err = proc.communicate()
        code = proc.returncode
        if err and code not in (0, 4):
            for line in err.strip().splitlines():
                log.warning(f"mpv: {line}")
        if self.state == "playing":
            self.state = "stopped"
            self.position = 0.0
            self._publish_state()
            if code in (0, None) and not self._stop_requested:
                log.info("Playback finished")
            elif self._stop_requested or code == 4:
                log.info("Playback stopped")
            else:
                log.error(f"mpv exited with code {code}")

    def _poll_status(self):
        while self.state == "playing" and self._running:
            time.sleep(STATUS_SECONDS)
            dur = self._mpv_cmd(["get_property", "duration"])
            pos = self._mpv_cmd(["get_property", "time-pos"])
            if dur and dur.get("error") == "success" and dur.get("data") is not None:
                self.duration = float(dur["data"])
            if pos and pos.get("error") == "success" and pos.get("data") is not None:
                self.position = float(pos["data"])
            if self.state == "playing":
                self._publish("position", f"{self.position:.1f}")
                self._publish("duration", f"{self.duration:.1f}")

    def stop(self, publish: bool = True):
        self._stop_requested = True
        if self.mpv_process:
            self.mpv_process.terminate()
            try:
                self.mpv_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.mpv_process.kill()
            self.mpv_process = None
        self.state = "stopped"
        self.position = 0.0
        if publish:
            self._publish_state()
            log.info("Stopped")

    def run(self):
        host = self.cfg["mqtt"]["host"]
        port = self.cfg["mqtt"].get("port", 1883)
        log.info(f"Connecting to MQTT {host}:{port}")
        self.client.connect(host, port, 60)
        self.client.loop_start()

        try:
            while self._running:
                time.sleep(SCAN_SECONDS)
                self._publish_sources()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        if not self._running:
            return
        log.info("Shutting down...")
        self._running = False
        self.stop()
        self._publish_availability("offline")
        self.client.loop_start if False else None
        self.client.loop_stop()
        self.client.disconnect()


def main():
    config = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    player = DinoPlayer(config)

    def signal_handler(sig, frame):
        player.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    player.run()


if __name__ == "__main__":
    main()
