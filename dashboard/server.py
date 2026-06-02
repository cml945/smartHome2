#!/usr/bin/env python3
"""Local smart home service dashboard.

The server intentionally uses only the Python standard library. It binds to
127.0.0.1 and exposes a small whitelist of service actions plus structured
health checks for the browser UI.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen


PROJECT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
ENV_FILE = PROJECT_DIR / ".env"
DOCKER_DIR = PROJECT_DIR / "docker"
COMPOSE_FILE = DOCKER_DIR / "docker-compose.yml"
OVERRIDE_FILE = DOCKER_DIR / "docker-compose.override.yml"
LOG_DIR = PROJECT_DIR / "logs"
GO2RTC_CONFIG = PROJECT_DIR / "go2rtc" / "config.yml"
TOKEN_STATE_FILE = LOG_DIR / "xiaomi-token-watch.state"

GO2RTC_LABEL = "com.go2rtc"
DETECTOR_LABEL = "com.frigate.detector"
TOKEN_WATCH_LABEL = "com.xiaomi-token-watch"

GO2RTC_PLIST = PROJECT_DIR / "go2rtc" / "com.go2rtc.plist"
TOKEN_WATCH_REPO_PLIST = PROJECT_DIR / "go2rtc" / "com.xiaomi-token-watch.plist"
DETECTOR_HOME_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.frigate.detector.plist"
TOKEN_WATCH_HOME_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.xiaomi-token-watch.plist"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
COMMAND_TIMEOUT = 45
STATUS_TIMEOUT = 4
OUTPUT_LIMIT = 16000


def now_ms() -> int:
    return int(time.time() * 1000)


def load_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not ENV_FILE.exists():
        return env

    for raw_line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


def compose_cmd(*args: str) -> List[str]:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    if OVERRIDE_FILE.exists():
        cmd.extend(["-f", str(OVERRIDE_FILE)])
    cmd.extend(args)
    return cmd


def run_cmd(
    cmd: List[str],
    timeout: int = COMMAND_TIMEOUT,
    cwd: Path = PROJECT_DIR,
) -> Tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return result.returncode, sanitize_output(result.stdout or "")
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        return 124, sanitize_output(f"{out}\n命令超时：{' '.join(cmd)}")
    except FileNotFoundError:
        return 127, f"命令不存在：{cmd[0]}"
    except Exception as exc:  # pragma: no cover - defensive for local ops.
        return 1, f"命令执行失败：{exc}"


def sanitize_output(text: str) -> str:
    text = text.replace("\r", "")
    patterns = [
        (r"(passToken:\s*)[^\s]+", r"\1***"),
        (r"(serviceToken:\s*)[^\s]+", r"\1***"),
        (r'("passToken"\s*:\s*")[^"]+', r"\1***"),
        (r"(HA_TOKEN=)[^\s]+", r"\1***"),
        (r"(MQTT_PASSWORD=)[^\s]+", r"\1***"),
        (r"(password['\"]?\s*[:=]\s*)[^\s,]+", r"\1***"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text[-OUTPUT_LIMIT:]


def status_item(
    key: str,
    name: str,
    state: str,
    detail: str,
    hint: str = "",
    url: str = "",
    group: str = "diagnostics",
) -> Dict[str, str]:
    return {
        "key": key,
        "name": name,
        "state": state,
        "detail": detail,
        "hint": hint,
        "url": url,
        "group": group,
    }


def http_status(url: str, timeout: int = STATUS_TIMEOUT, insecure_tls: bool = False) -> Tuple[bool, int, str]:
    try:
        req = Request(url, headers={"User-Agent": "smartHome2-dashboard/1.0"})
        context = ssl._create_unverified_context() if insecure_tls and url.startswith("https://") else None
        with urlopen(req, timeout=timeout, context=context) as response:
            return True, response.status, response.read(256).decode("utf-8", errors="replace")
    except Exception as exc:
        status = getattr(exc, "code", 0) or 0
        if status in (401, 403):
            return True, int(status), ""
        return False, int(status), str(exc)


def curl_http_status(url: str, timeout: int = STATUS_TIMEOUT, insecure_tls: bool = False) -> Tuple[bool, int, str]:
    cmd = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout)]
    if insecure_tls:
        cmd.append("-k")
    cmd.append(url)
    code, output = run_cmd(cmd, timeout=timeout + 2)
    status_text = output.strip().splitlines()[-1] if output.strip() else "0"
    try:
        status = int(status_text)
    except ValueError:
        status = 0
    return (status in (200, 401, 403), status, "")


def tcp_check(host: str, port: int, timeout: int = STATUS_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ping_check(host: str) -> bool:
    code, _ = run_cmd(["ping", "-c", "1", "-W", "2", host], timeout=4)
    return code == 0


def pgrep(pattern: str) -> Tuple[bool, str]:
    code, output = run_cmd(["pgrep", "-f", pattern], timeout=3)
    pids = ",".join([line.strip() for line in output.splitlines() if line.strip().isdigit()])
    return code == 0 and bool(pids), pids


def launchd_loaded(label: str) -> bool:
    uid = str(os.getuid())
    code, _ = run_cmd(["launchctl", "print", f"gui/{uid}/{label}"], timeout=3)
    if code == 0:
        return True
    code, output = run_cmd(["launchctl", "list"], timeout=4)
    return code == 0 and label in output


def docker_container_status(name: str) -> str:
    code, output = run_cmd(["docker", "inspect", "--format={{.State.Status}}", name], timeout=4)
    if code != 0:
        return ""
    return output.strip().splitlines()[-1] if output.strip() else ""


def detector_is_required() -> bool:
    config_path = PROJECT_DIR / "frigate" / "config" / "config.yml"
    if not config_path.exists():
        return True
    config = config_path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"type:\s*apple_silicon\b", config):
        return True
    if re.search(r"type:\s*cpu\b", config):
        return False
    return True


def collect_status() -> Dict[str, Any]:
    env = load_env()
    items: List[Dict[str, str]] = []

    frigate_port = int(env.get("FRIGATE_PORT", "8971") or "8971")
    go2rtc_port = int(env.get("FRIGATE_GO2RTC_PORT", "1984") or "1984")
    detector_port = int(env.get("DETECTOR_ZMQ_PORT", "5555") or "5555")
    mqtt_port = int(env.get("MQTT_PORT", "1883") or "1883")
    ha_ip = env.get("HA_IP", "192.168.1.200")
    storage = Path(env.get("FRIGATE_STORAGE_PATH", "/Users/Shared/frigate-storage"))
    frigate_ui_url = f"http://127.0.0.1:{frigate_port}"

    docker_code, docker_output = run_cmd(["docker", "info"], timeout=5)
    docker_ok = docker_code == 0
    items.append(status_item(
        "docker",
        "Docker daemon",
        "ok" if docker_ok else "fail",
        "Docker daemon 运行中" if docker_ok else "Docker daemon 未运行",
        "" if docker_ok else "启动 OrbStack 或 Docker Desktop 后再启动容器。",
    ))

    go2rtc_loaded = launchd_loaded(GO2RTC_LABEL)
    go2rtc_proc, go2rtc_pids = pgrep("go2rtc")
    go2rtc_api, go2rtc_status_code, _ = http_status(f"http://127.0.0.1:{go2rtc_port}")
    go2rtc_state = "ok" if go2rtc_api else ("warn" if go2rtc_loaded or go2rtc_proc else "fail")
    items.append(status_item(
        "go2rtc",
        "go2rtc",
        go2rtc_state,
        "WebUI 可访问" if go2rtc_api else ("launchd 已加载" if go2rtc_loaded else "未运行"),
        f"PID: {go2rtc_pids}" if go2rtc_pids else "可在界面中启动 go2rtc。",
        f"http://127.0.0.1:{go2rtc_port}",
        "services",
    ))

    detector_required = detector_is_required()
    detector_proc, detector_pids = pgrep("frigate.*detector|FrigateDetector|apple.*silicon.*detect")
    detector_loaded = launchd_loaded(DETECTOR_LABEL)
    detector_tcp = tcp_check("127.0.0.1", detector_port, timeout=2)
    if not detector_required:
        detector_state = "ok"
        detector_detail = "当前 Frigate 配置使用 CPU detector，未启用独立 Apple Silicon Detector"
        detector_hint = ""
    else:
        detector_state = "ok" if detector_proc or detector_tcp else ("warn" if detector_loaded else "fail")
        detector_detail = "ZeroMQ 端口监听中" if detector_tcp else ("进程运行中" if detector_proc else ("launchd 已加载但未确认进程" if detector_loaded else "未运行"))
        detector_hint = f"PID: {detector_pids}" if detector_pids else "未安装时需先运行 make detector-install。"
    items.append(status_item(
        "detector",
        "Apple Silicon Detector",
        detector_state,
        detector_detail,
        detector_hint,
        group="services",
    ))

    token_watch_loaded = launchd_loaded(TOKEN_WATCH_LABEL)
    token_state = read_token_state()
    token_recent_401 = recent_log_contains(LOG_DIR / "go2rtc.log", "401 Unauthorized", 300)
    if token_state == "xiaomi_401" or token_recent_401:
        token_status = "fail"
        token_detail = "检测到 401 Unauthorized，token 可能已过期"
        token_hint = "使用下方刷新小米 token 功能。"
    elif token_state == "go2rtc_down":
        token_status = "warn"
        token_detail = "token 监控发现 go2rtc API 不可达"
        token_hint = "先启动 go2rtc，再重新运行 token 监控。"
    else:
        token_status = "ok" if token_watch_loaded else "warn"
        token_detail = "监控已加载，未发现近期 401" if token_watch_loaded else "token 监控未加载"
        token_hint = "" if token_watch_loaded else "建议启动 token watch 定时监控。"
    items.append(status_item(
        "token-watch",
        "小米 token 监控",
        token_status,
        token_detail,
        token_hint,
        group="services",
    ))

    frigate_api, frigate_code, frigate_body = http_status(f"http://127.0.0.1:{frigate_port}/api/version")
    frigate_scheme = "HTTP"
    if not frigate_api:
        frigate_api, frigate_code, frigate_body = http_status(
            f"https://127.0.0.1:{frigate_port}/api/version",
            insecure_tls=True,
        )
        frigate_scheme = "HTTPS"
    if not frigate_api:
        frigate_api, frigate_code, frigate_body = curl_http_status(
            f"https://127.0.0.1:{frigate_port}/api/version",
            insecure_tls=True,
        )
        frigate_scheme = "HTTPS"
    if frigate_api and frigate_scheme == "HTTPS":
        frigate_ui_url = f"https://127.0.0.1:{frigate_port}"

    for container in ("frigate", "mosquitto"):
        status = docker_container_status(container) if docker_ok else ""
        items.append(status_item(
            container,
            container.capitalize() if container == "frigate" else "Mosquitto",
            "ok" if status == "running" else "fail",
            f"容器状态：{status}" if status else "容器未运行",
            "可在界面中启动 Docker Compose。" if not status else "",
            frigate_ui_url if container == "frigate" else "",
            "services",
        ))

    items.append(status_item(
        "frigate-api",
        "Frigate API",
        "ok" if frigate_api else "fail",
        f"API 可达 ({frigate_scheme} HTTP {frigate_code}){('，版本 ' + frigate_body.strip()) if frigate_body.strip() and frigate_code == 200 else ''}" if frigate_api else "API 无响应",
        "" if frigate_api else "确认 Frigate 容器和端口映射。",
        frigate_ui_url,
    ))

    items.append(status_item(
        "go2rtc-api",
        "go2rtc WebUI",
        "ok" if go2rtc_api else "fail",
        f"HTTP {go2rtc_status_code}" if go2rtc_api else "WebUI 无响应",
        "" if go2rtc_api else "确认 go2rtc 已启动。",
        f"http://127.0.0.1:{go2rtc_port}",
    ))

    mqtt_local_ok = tcp_check("127.0.0.1", mqtt_port, timeout=3)
    items.append(status_item(
        "mqtt",
        "MQTT Broker",
        "ok" if mqtt_local_ok else "fail",
        f"Docker Mosquitto 127.0.0.1:{mqtt_port} 可达" if mqtt_local_ok else f"Docker Mosquitto 127.0.0.1:{mqtt_port} 不可达",
        "" if mqtt_local_ok else "检查 Mosquitto 容器、端口映射和 HA MQTT broker 是否指向 Mac 局域网 IP。",
    ))

    ha_ok, ha_code, _ = http_status(f"http://{ha_ip}:8123/api/", timeout=5)
    items.append(status_item(
        "home-assistant",
        "Home Assistant",
        "ok" if ha_ok else "fail",
        f"API 可达 (HTTP {ha_code})" if ha_ok else f"http://{ha_ip}:8123/api/ 不可达",
        "" if ha_ok else "检查 UTM HAOS 是否运行、IP 是否正确。",
        f"http://{ha_ip}:8123",
    ))

    camera_items = collect_camera_status(env)
    items.extend(camera_items)

    if storage.exists():
        usage = shutil.disk_usage(str(storage))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        state = "warn" if usage.free / max(usage.total, 1) < 0.1 else "ok"
        detail = f"{storage} 可用，剩余 {free_gb:.1f}GB / {total_gb:.1f}GB"
    else:
        state = "warn"
        detail = f"{storage} 不存在"
    items.append(status_item(
        "storage",
        "Frigate 存储",
        state,
        detail,
        "运行 setup 或手动创建存储目录。" if state == "warn" else "",
    ))

    overall = summarize_overall(items)
    return {
        "generated_at": now_ms(),
        "project_dir": str(PROJECT_DIR),
        "overall": overall,
        "items": items,
        "token": {
            "state_file": token_state or "",
            "recent_401": token_recent_401,
            "config_exists": GO2RTC_CONFIG.exists(),
        },
        "docker_message": docker_output[-1000:] if not docker_ok else "",
    }


def collect_camera_status(env: Dict[str, str]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    camera_keys = sorted(k[:-3] for k in env if k.startswith("CAM_") and k.endswith("_IP"))
    for prefix in camera_keys:
        ip = env.get(f"{prefix}_IP", "").strip()
        if not ip or ip.endswith(".xxx"):
            continue
        ok = ping_check(ip)
        items.append(status_item(
            f"camera-{prefix.lower()}",
            prefix.replace("CAM_", "摄像头 ").replace("_", " "),
            "ok" if ok else "fail",
            f"{ip} 可达" if ok else f"{ip} 不可达",
            "" if ok else "检查摄像头电源、同网段和 IP 配置。",
        ))
    if not items:
        items.append(status_item(
            "camera-config",
            "摄像头配置",
            "warn",
            ".env 中未发现可检查的摄像头 IP",
            "配置 CAM_*_IP 后可显示每台摄像头连通性。",
        ))
    return items


def summarize_overall(items: Iterable[Dict[str, str]]) -> Dict[str, Any]:
    counts = {"ok": 0, "warn": 0, "fail": 0, "unknown": 0}
    for item in items:
        counts[item.get("state", "unknown")] = counts.get(item.get("state", "unknown"), 0) + 1
    if counts["fail"]:
        state = "fail"
        label = "需要处理"
    elif counts["warn"]:
        state = "warn"
        label = "有提醒"
    else:
        state = "ok"
        label = "运行正常"
    return {"state": state, "label": label, "counts": counts}


def read_token_state() -> str:
    if not TOKEN_STATE_FILE.exists():
        return ""
    return TOKEN_STATE_FILE.read_text(encoding="utf-8", errors="replace").strip()


def recent_log_contains(path: Path, needle: str, lines: int) -> bool:
    for line in tail_lines(path, lines):
        if needle in line:
            return True
    return False


def tail_lines(path: Path, lines: int = 80) -> List[str]:
    if not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                read_size = min(block, size)
                size -= read_size
                handle.seek(size)
                data = handle.read(read_size) + data
            text = data.decode("utf-8", errors="replace")
            return text.splitlines()[-lines:]
    except OSError as exc:
        return [f"读取日志失败：{exc}"]


class TokenRefreshSession:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: Optional[subprocess.Popen[str]] = None
        self.output = ""
        self.started_at = 0
        self.updated_at = 0
        self.exit_code: Optional[int] = None
        self.reader: Optional[threading.Thread] = None

    def start(self) -> Dict[str, Any]:
        with self.lock:
            if self.process and self.process.poll() is None:
                return {"ok": False, "message": "已有 token 刷新会话正在运行。"}

            cmd = [
                sys.executable,
                "-u",
                str(PROJECT_DIR / "scripts" / "get_xiaomi_token.py"),
                "--yes",
                "--restart",
                "--check",
            ]
            self.output = ""
            self.exit_code = None
            self.started_at = now_ms()
            self.updated_at = self.started_at
            try:
                self.process = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_DIR),
                    text=True,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                )
            except Exception as exc:
                self.process = None
                self.exit_code = 1
                self._append(f"启动刷新脚本失败：{exc}\n")
                return {"ok": False, "message": str(exc)}

            self.reader = threading.Thread(target=self._read_output, daemon=True)
            self.reader.start()
            return {"ok": True, "message": "token 刷新会话已启动。"}

    def _read_output(self) -> None:
        proc = self.process
        if not proc or not proc.stdout:
            return
        try:
            while True:
                chunk = proc.stdout.read(1)
                if not chunk:
                    break
                self._append(chunk)
        finally:
            code = proc.wait()
            with self.lock:
                self.exit_code = code
                self.updated_at = now_ms()

    def _append(self, text: str) -> None:
        with self.lock:
            self.output = sanitize_output(self.output + text)[-OUTPUT_LIMIT:]
            self.updated_at = now_ms()

    def send_input(self, value: str) -> Dict[str, Any]:
        with self.lock:
            if not self.process or self.process.poll() is not None or not self.process.stdin:
                return {"ok": False, "message": "没有正在等待输入的 token 刷新会话。"}
            try:
                self.process.stdin.write(value + "\n")
                self.process.stdin.flush()
                return {"ok": True, "message": "已发送输入。"}
            except Exception as exc:
                return {"ok": False, "message": f"发送输入失败：{exc}"}

    def stop(self) -> Dict[str, Any]:
        with self.lock:
            if not self.process or self.process.poll() is not None:
                return {"ok": False, "message": "没有正在运行的 token 刷新会话。"}
            self.process.send_signal(signal.SIGTERM)
            return {"ok": True, "message": "已请求停止 token 刷新会话。"}

    def status(self) -> Dict[str, Any]:
        with self.lock:
            running = bool(self.process and self.process.poll() is None)
            return {
                "running": running,
                "started_at": self.started_at,
                "updated_at": self.updated_at,
                "exit_code": self.exit_code,
                "output": self.output,
            }


TOKEN_SESSION = TokenRefreshSession()


def existing_plist(*paths: Path) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def command_sequence(action: str) -> List[List[str]]:
    token_watch_plist = existing_plist(TOKEN_WATCH_HOME_PLIST, TOKEN_WATCH_REPO_PLIST)
    detector_plist = existing_plist(DETECTOR_HOME_PLIST)

    sequences: Dict[str, List[List[str]]] = {
        "docker-start": [compose_cmd("up", "-d")],
        "docker-stop": [compose_cmd("down")],
        "docker-restart": [compose_cmd("restart")],
        "go2rtc-start": [["launchctl", "load", "-w", str(GO2RTC_PLIST)], ["launchctl", "start", GO2RTC_LABEL]],
        "go2rtc-stop": [["launchctl", "stop", GO2RTC_LABEL], ["launchctl", "unload", str(GO2RTC_PLIST)]],
        "go2rtc-restart": [["launchctl", "stop", GO2RTC_LABEL], ["launchctl", "start", GO2RTC_LABEL]],
        "token-watch-run": [["bash", str(PROJECT_DIR / "scripts" / "xiaomi-token-watch.sh")]],
    }

    if detector_plist:
        sequences["detector-start"] = [["launchctl", "load", "-w", str(detector_plist)], ["launchctl", "start", DETECTOR_LABEL]]
        sequences["detector-stop"] = [["launchctl", "stop", DETECTOR_LABEL], ["launchctl", "unload", str(detector_plist)]]
        sequences["detector-restart"] = [["launchctl", "stop", DETECTOR_LABEL], ["launchctl", "start", DETECTOR_LABEL]]
    else:
        sequences["detector-start"] = [["false"]]
        sequences["detector-stop"] = [["launchctl", "stop", DETECTOR_LABEL]]
        sequences["detector-restart"] = [["false"]]

    if token_watch_plist:
        sequences["token-watch-start"] = [["launchctl", "load", "-w", str(token_watch_plist)], ["launchctl", "start", TOKEN_WATCH_LABEL]]
        sequences["token-watch-stop"] = [["launchctl", "stop", TOKEN_WATCH_LABEL], ["launchctl", "unload", str(token_watch_plist)]]
        sequences["token-watch-restart"] = [["launchctl", "stop", TOKEN_WATCH_LABEL], ["launchctl", "start", TOKEN_WATCH_LABEL]]
    else:
        sequences["token-watch-start"] = [["false"]]
        sequences["token-watch-stop"] = [["launchctl", "stop", TOKEN_WATCH_LABEL]]
        sequences["token-watch-restart"] = [["false"]]

    sequences["start-all"] = (
        sequences["docker-start"]
        + sequences["go2rtc-start"]
        + sequences["detector-start"]
        + sequences["token-watch-start"]
    )
    sequences["stop-all"] = (
        sequences["token-watch-stop"]
        + sequences["detector-stop"]
        + sequences["go2rtc-stop"]
        + sequences["docker-stop"]
    )
    sequences["restart-all"] = sequences["stop-all"] + sequences["start-all"]

    if action not in sequences:
        raise KeyError(action)
    return sequences[action]


def run_action(action: str) -> Dict[str, Any]:
    try:
        commands = command_sequence(action)
    except KeyError:
        return {"ok": False, "action": action, "output": "未知动作。"}

    outputs = []
    success = True
    for cmd in commands:
        if cmd == ["false"]:
            success = False
            outputs.append("动作不可用：对应 plist 未安装或不存在。")
            continue
        code, output = run_cmd(cmd)
        outputs.append(f"$ {' '.join(cmd)}\n{output.strip() or '(无输出)'}")
        if code != 0:
            success = False
            outputs.append(f"返回码：{code}")
    return {"ok": success, "action": action, "output": "\n\n".join(outputs)[-OUTPUT_LIMIT:]}


LOG_FILES = {
    "go2rtc": LOG_DIR / "go2rtc.log",
    "go2rtc-error": LOG_DIR / "go2rtc.error.log",
    "token-watch": LOG_DIR / "xiaomi-token-watch.log",
    "token-watch-error": LOG_DIR / "xiaomi-token-watch.error.log",
}


class DashboardHandler(SimpleHTTPRequestHandler):
    server_version = "SmartHomeDashboard/1.0"

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        if parsed.path == "/":
            return str(STATIC_DIR / "index.html")
        if parsed.path.startswith("/static/"):
            relative = unquote(parsed.path.removeprefix("/static/")).lstrip("/")
            root = STATIC_DIR.resolve()
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return str(STATIC_DIR / "404")
            return str(candidate)
        return str(STATIC_DIR / "404")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[dashboard] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json(collect_status())
            return
        if parsed.path == "/api/token/status":
            self.send_json(TOKEN_SESSION.status())
            return
        if parsed.path == "/api/logs":
            params = parse_qs(parsed.query)
            name = params.get("name", ["go2rtc"])[0]
            lines = safe_int(params.get("lines", ["80"])[0], 80, 20, 300)
            path = LOG_FILES.get(name)
            if not path:
                self.send_json({"ok": False, "lines": ["未知日志。"]}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True, "name": name, "lines": [sanitize_output(line) for line in tail_lines(path, lines)]})
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/actions/"):
            action = parsed.path.rsplit("/", 1)[-1]
            self.send_json(run_action(action))
            return
        if parsed.path == "/api/token/start":
            self.send_json(TOKEN_SESSION.start())
            return
        if parsed.path == "/api/token/input":
            data = self.read_json()
            value = str(data.get("value", ""))
            self.send_json(TOKEN_SESSION.send_input(value))
            return
        if parsed.path == "/api/token/stop":
            self.send_json(TOKEN_SESSION.stop())
            return
        self.send_json({"ok": False, "message": "未知接口。"}, HTTPStatus.NOT_FOUND)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def send_json(self, data: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def safe_int(value: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def main() -> None:
    parser = argparse.ArgumentParser(description="smartHome2 本地 Web 控制台")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--check", action="store_true", help="输出结构化状态 JSON 后退出")
    args = parser.parse_args()

    if args.check:
        print(json.dumps(collect_status(), ensure_ascii=False, indent=2))
        return

    if args.host != "127.0.0.1":
        print("安全限制：控制台默认仅允许监听 127.0.0.1。", file=sys.stderr)
        sys.exit(2)

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"smartHome2 控制台已启动：http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止控制台...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
