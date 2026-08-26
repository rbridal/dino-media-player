#!/usr/bin/env python3
"""
Dino Media Player - MQTT controlled local audio player for Raspberry Pi
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import paho.mqtt.client as mqtt
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("dino-player")


class DinoPlayer:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.media_dir = Path(self.cfg.get("media_dir", "./media")).resolve()
        self.topic_prefix = self.cfg["mqtt"].get("topic_prefix", "dino/player")
        self.volume = self.cfg.get("volume", 80)

        self.mpv_process: Optional[subprocess.Popen] = None
        self.current_source: Optional[str] = None
        self.state = "stopped"  # stopped | playing | paused

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        if self.cfg["mqtt"].get("username"):
            self.client.username_pw_set(
                self.cfg["mqtt"]["username"],
                self.cfg["mqtt"].get("password", "")
            )

        self._running = True

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        log.info(f"Connected to MQTT broker (rc={reason_code})")
        client.subscribe(f"{self.topic_prefix}/command")
        self._publish_availability("online")
        self._publish_sources()
        self._publish_state()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            action = payload.get("action", "").lower()
            source = payload.get("source")

            log.info(f"Command received: {action} source={source}")

            if action == "play":
                self.play(source)
            elif action == "pause":
                self.pause()
            elif action == "resume":
                self.resume()
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
        files = []
        for ext in ("*.mp3", "*.wav", "*.flac", "*.ogg", "*.m4a"):
            files.extend([p.name for p in self.media_dir.glob(ext)])
        return sorted(files)

    def _publish(self, topic_suffix: str, payload: str, retain: bool = True):
        topic = f"{self.topic_prefix}/{topic_suffix}"
        self.client.publish(topic, payload, retain=retain)

    def _publish_availability(self, status: str):
        self._publish("available", status)

    def _publish_sources(self):
        sources = self.get_sources()
        self._publish("sources", json.dumps(sources))
        log.info(f"Available sources: {sources}")

    def _publish_state(self):
        self._publish("state", self.state)
        self._publish("source", self.current_source or "")

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
            log.error(f"File not found: {filepath}")
            return

        self.stop()  # ensure clean start

        cmd = [
            "mpv",
            "--no-video",
            "--really-quiet",
            f"--volume={self.volume}",
            str(filepath)
        ]

        # Force analog if needed
        audio_device = self.cfg.get("audio_device")
        if audio_device:
            cmd.insert(1, f"--audio-device={audio_device}")

        log.info(f"Playing: {filepath}")
        self.mpv_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        self.state = "playing"
        self._publish_state()

        # Monitor process in background
        def monitor():
            if self.mpv_process:
                self.mpv_process.wait()
                if self.state == "playing":
                    self.state = "stopped"
                    self._publish_state()
                    log.info("Playback finished")

        import threading
        threading.Thread(target=monitor, daemon=True).start()

    def pause(self):
        if self.mpv_process and self.state == "playing":
            # mpv doesn't support pause via simple process signal easily
            # For simplicity we stop and remember position later if needed
            # Better approach: use mpv JSON IPC in a future version
            self.stop()
            self.state = "paused"
            self._publish_state()
            log.info("Paused (stopped for now)")

    def resume(self):
        if self.state == "paused" and self.current_source:
            self.play(self.current_source)

    def stop(self):
        if self.mpv_process:
            self.mpv_process.terminate()
            try:
                self.mpv_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.mpv_process.kill()
            self.mpv_process = None
        self.state = "stopped"
        self._publish_state()
        log.info("Stopped")

    def run(self):
        host = self.cfg["mqtt"]["host"]
        port = self.cfg["mqtt"].get("port", 1883)

        log.info(f"Connecting to MQTT {host}:{port}")
        self.client.connect(host, port, 60)
        self.client.loop_start()

        # Keep alive + re-publish sources occasionally
        try:
            while self._running:
                time.sleep(30)
                self._publish_sources()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        log.info("Shutting down...")
        self._running = False
        self.stop()
        self._publish_availability("offline")
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
