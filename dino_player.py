#!/usr/bin/env python3
"""Dino Media Player - MQTT controlled local audio player for Raspberry Pi."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
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
BT_RETRY_SECONDS = 20
IDLE_SOURCES = {"", "none", "off", "stop"}

DEFAULT_OUTPUTS = {
    "analog": {
        "label": "3.5mm jack",
        "audio_output": "alsa",
        "audio_device": "alsa/plughw:2,0",
    },
    "bluetooth": {
        "label": "BT-WUZHI",
        "audio_output": "alsa",
        "audio_device": "alsa/bluealsa",
        "bluetooth_name": "BT-WUZHI",
    },
}


class DinoPlayer:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        with open(self.config_path) as f:
            self.cfg = yaml.safe_load(f) or {}

        self.media_dir = Path(self.cfg.get("media_dir", "./media")).resolve()
        self.topic_prefix = self.cfg["mqtt"].get("topic_prefix", "dino/player")
        self.volume_file = self.config_path.with_name("volume.state")
        self.output_file = self.config_path.with_name("output.state")
        self.volume = self._load_volume()
        self.outputs = self.cfg.get("outputs") or DEFAULT_OUTPUTS
        self.output_key = self._load_output_key()
        self.downmix_mono = bool(self.cfg.get("downmix_mono", True))

        self.mpv_process: Optional[subprocess.Popen] = None
        self.current_source: Optional[str] = None
        self.state = "stopped"
        self.position = 0.0
        self.duration = 0.0
        self.bt_status = "unknown"
        self._last_sources: List[str] = []
        self._last_bt_status = ""
        self._last_bt_try = 0.0
        self._stop_requested = False

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.will_set(f"{self.topic_prefix}/available", "offline", qos=1, retain=True)

        if self.cfg["mqtt"].get("username"):
            self.client.username_pw_set(
                self.cfg["mqtt"]["username"],
                self.cfg["mqtt"].get("password", ""),
            )

        self._running = True

    def _clamp_volume(self, value) -> int:
        try:
            vol = int(round(float(value)))
        except (TypeError, ValueError):
            return int(self.cfg.get("volume", 80))
        return max(0, min(100, vol))

    def _load_volume(self) -> int:
        if self.volume_file.exists():
            try:
                return self._clamp_volume(self.volume_file.read_text().strip())
            except OSError:
                pass
        return self._clamp_volume(self.cfg.get("volume", 80))

    def _save_volume(self) -> None:
        try:
            self.volume_file.write_text(str(self.volume))
        except OSError as exc:
            log.warning(f"Could not persist volume: {exc}")

    def _load_output_key(self) -> str:
        if self.output_file.exists():
            try:
                key = self.output_file.read_text().strip()
                if key in self.outputs:
                    return key
            except OSError:
                pass
        key = str(self.cfg.get("output", "analog"))
        return key if key in self.outputs else next(iter(self.outputs))

    def _save_output_key(self) -> None:
        try:
            self.output_file.write_text(self.output_key)
        except OSError as exc:
            log.warning(f"Could not persist output: {exc}")

    def _output_cfg(self) -> dict:
        return self.outputs.get(self.output_key) or {}

    def _output_label(self, key: Optional[str] = None) -> str:
        cfg = self.outputs.get(key or self.output_key) or {}
        return str(cfg.get("label") or key or self.output_key)

    def _key_from_label(self, value: str) -> Optional[str]:
        if value in self.outputs:
            return value
        for key, cfg in self.outputs.items():
            if str(cfg.get("label")) == value:
                return key
        return None

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        log.info(f"Connected to MQTT broker (rc={reason_code})")
        client.subscribe(f"{self.topic_prefix}/command")
        self._publish_availability("online")
        self._publish_sources(force=True)
        self._publish_outputs()
        self._publish_state()
        self._refresh_link(force_publish=True)
        self._publish_heartbeat()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            action = payload.get("action", "").lower()
            source = payload.get("source")
            log.info(
                f"Command received: {action} source={source} "
                f"volume={payload.get('volume')} output={payload.get('output')}"
            )
            if payload.get("volume") is not None and action in (
                "play",
                "set_source",
                "set_volume",
            ):
                apply = action == "set_volume" or self.state == "playing"
                self.set_volume(payload.get("volume"), apply=apply)
            if action == "play":
                if payload.get("output"):
                    self.set_output(payload.get("output"), persist=True)
                self.play(source)
            elif action == "stop":
                self.stop()
            elif action == "set_source":
                if payload.get("output"):
                    self.set_output(payload.get("output"), persist=True)
                if source is None or str(source).strip().lower() in IDLE_SOURCES:
                    self.stop()
                else:
                    self.play(str(source).strip())
            elif action == "set_volume":
                pass
            elif action == "set_output":
                self.set_output(payload.get("output") or "")
            elif action == "reconnect":
                self._refresh_link(force_reconnect=True, force_publish=True)
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

    def _publish_heartbeat(self):
        self._publish("heartbeat", datetime.now(timezone.utc).isoformat(), retain=True)

    def _publish_sources(self, force: bool = False):
        sources = self.get_sources()
        if force or sources != self._last_sources:
            self._last_sources = sources
            self._publish("sources", json.dumps(sources))
            log.info(f"Available sources: {sources}")

    def _publish_outputs(self):
        labels = [self._output_label(k) for k in self.outputs]
        self._publish("outputs", json.dumps(labels))
        self._publish("output", self._output_label())

    def _publish_state(self):
        self._publish("state", self.state)
        self._publish("source", self.current_source or "")
        self._publish("position", f"{self.position:.1f}")
        self._publish("duration", f"{self.duration:.1f}")
        self._publish("volume", str(self.volume))
        self._publish("output", self._output_label())

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

    def set_volume(self, value, apply: bool = True) -> None:
        self.volume = self._clamp_volume(value)
        self._save_volume()
        if apply and self.state == "playing":
            result = self._mpv_cmd(["set_property", "volume", self.volume])
            if result and result.get("error") not in (None, "success"):
                log.warning(f"mpv volume set failed: {result}")
        self._publish("volume", str(self.volume))
        log.info(f"Volume set to {self.volume}")

    def set_output(self, value: str, persist: bool = True) -> None:
        key = self._key_from_label(str(value).strip())
        if not key:
            log.warning(f"Unknown output: {value}")
            return
        if key == self.output_key:
            self._publish_outputs()
            return
        was_playing = self.state == "playing"
        source = self.current_source
        if was_playing:
            self.stop(publish=False, clear_source=False)
        self.output_key = key
        if persist:
            self._save_output_key()
        log.info(f"Output set to {self._output_label()} ({key})")
        self._publish_outputs()
        self._refresh_link(force_reconnect=self._is_bluetooth_output(), force_publish=True)
        if was_playing:
            self.play(source)

    def _resolve_mac(self, name: str) -> Optional[str]:
        try:
            out = subprocess.check_output(["bluetoothctl", "devices"], text=True, timeout=8)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning(f"bluetoothctl devices failed: {exc}")
            return None
        for line in out.splitlines():
            if name in line:
                parts = line.split()
                if len(parts) >= 2 and re.match(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", parts[1]):
                    return parts[1]
        return None

    def _bt_mac(self) -> Optional[str]:
        cfg = self._output_cfg()
        mac = (cfg.get("bluetooth_mac") or "").strip()
        name = (cfg.get("bluetooth_name") or "").strip()
        if mac:
            return mac
        if name:
            return self._resolve_mac(name)
        return None

    def _is_bluetooth_output(self) -> bool:
        cfg = self._output_cfg()
        return bool(cfg.get("bluetooth_name") or "bluealsa" in str(cfg.get("audio_device") or ""))

    def _bt_connected(self) -> bool:
        mac = self._bt_mac()
        if not mac:
            return False
        try:
            out = subprocess.check_output(
                ["bluetoothctl", "info", mac], text=True, timeout=8
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return any(line.strip() == "Connected: yes" for line in out.splitlines())

    def _audio_device(self) -> tuple[str, str]:
        cfg = self._output_cfg()
        ao = cfg.get("audio_output") or self.cfg.get("audio_output") or "alsa"
        device = cfg.get("audio_device") or self.cfg.get("audio_device") or ""
        mac = self._bt_mac() or ""
        if mac and "bluealsa" in str(device) and "DEV=" not in str(device):
            device = f"alsa/bluealsa:DEV={mac},PROFILE=a2dp"
        return ao, device

    def _ensure_bluetooth(self) -> bool:
        if not self._is_bluetooth_output():
            return True
        mac = self._bt_mac()
        name = (self._output_cfg().get("bluetooth_name") or "").strip()
        if not mac:
            log.warning("Bluetooth MAC unknown; pair with scripts/pair-bt-wuzhi.sh")
            return False
        if self._bt_connected():
            return True
        log.info(f"Connecting Bluetooth {name or mac} ({mac})")
        try:
            result = subprocess.run(
                ["bluetoothctl", "connect", mac],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                log.warning(f"bluetoothctl connect: {result.stdout} {result.stderr}")
            else:
                log.info("Bluetooth connected")
            time.sleep(1.0)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning(f"Bluetooth connect failed: {exc}")
            return False
        return self._bt_connected()

    def _refresh_link(self, force_reconnect: bool = False, force_publish: bool = False) -> None:
        if not self._is_bluetooth_output():
            status = "not_required"
        elif force_reconnect or not self._bt_connected():
            now = time.time()
            if force_reconnect or now - self._last_bt_try >= BT_RETRY_SECONDS:
                self._last_bt_try = now
                status = "reconnecting"
                self._publish_bt(status)
                status = "connected" if self._ensure_bluetooth() else "disconnected"
            else:
                status = "disconnected"
        else:
            status = "connected"
        self.bt_status = status
        self._publish_bt(status, force=force_publish)

    def _publish_bt(self, status: str, force: bool = False) -> None:
        if not force and status == self._last_bt_status:
            return
        self._last_bt_status = status
        self._publish("bluetooth", status)
        connected = "on" if status == "connected" else "off"
        if status == "not_required":
            connected = "n/a"
        self._publish("bluetooth_connected", connected)
        if status != "not_required":
            log.info(f"Bluetooth status: {status}")

    def play(self, source: Optional[str] = None):
        if source and str(source).strip().lower() not in IDLE_SOURCES:
            self.current_source = str(source).strip()
        if not self.current_source:
            log.error("No media selected")
            return

        filepath = self.media_dir / self.current_source
        if not filepath.exists():
            self._publish_sources(force=True)
            log.error(f"File not found: {filepath}")
            return

        self.stop(publish=False, clear_source=False)
        if self._is_bluetooth_output():
            self._refresh_link(force_reconnect=True, force_publish=True)

        try:
            os.unlink(IPC_PATH)
        except FileNotFoundError:
            pass

        ao, audio_device = self._audio_device()
        cmd = [
            "mpv",
            "--no-video",
            "--really-quiet",
            f"--ao={ao}",
            f"--volume={self.volume}",
            f"--input-ipc-server={IPC_PATH}",
        ]
        if self.downmix_mono:
            cmd.append("--audio-channels=mono")
        if audio_device:
            cmd.append(f"--audio-device={audio_device}")
        cmd.append(str(filepath))

        log.info(
            f"Playing: {filepath} volume={self.volume} "
            f"output={self._output_label()} device={audio_device or ao}"
        )
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
            self.current_source = None
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

    def stop(self, publish: bool = True, clear_source: bool = True):
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
        if clear_source:
            self.current_source = None
        if publish:
            self._publish_state()
            log.info("Stopped")

    def run(self):
        host = self.cfg["mqtt"]["host"]
        port = self.cfg["mqtt"].get("port", 1883)
        log.info(f"Connecting to MQTT {host}:{port}")
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()

        try:
            while self._running:
                time.sleep(SCAN_SECONDS)
                self._publish_sources()
                self._publish_heartbeat()
                self._refresh_link()
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
