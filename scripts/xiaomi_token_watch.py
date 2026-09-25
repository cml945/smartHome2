#!/usr/bin/env python3
"""Recover Xiaomi sessions after fresh 401s without changing credentials."""
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COOLDOWN = 3600
MAX_LOG_BYTES = 256 * 1024


def xiaomi_stream_names(streams, remembered=()):
    names = set()
    for name, stream in streams.items():
        producers = stream.get("producers") or []
        if any(str(p.get("url", "")).startswith("xiaomi://") or
               str(p.get("format_name", "")).startswith("xiaomi/") for p in producers):
            names.add(name)
        elif name in remembered and not any(p.get("url") or p.get("format_name") for p in producers):
            names.add(name)
    return names


def video_counters(stream):
    """Track receiver identity: reconnects and counter resets aren't progress."""
    return {
        (p.get("id"), r.get("id")): r.get("bytes", 0)
        for p in stream.get("producers") or []
        for r in p.get("receivers") or []
        if r.get("codec", {}).get("codec_type") == "video"
    }


def growing(before, after, names):
    result = set()
    for name in names:
        old = video_counters(before.get(name, {}))
        new = video_counters(after.get(name, {}))
        if any(key in old and value > old[key] for key, value in new.items()):
            result.add(name)
    return result


def auth_streams(text, names, error):
    """HTTP/UI authentication failures must not trigger a restart."""
    found = set()
    for line in text.splitlines():
        if '[rtsp]' not in line or error not in line or 'error="streams:' not in line:
            continue
        match = re.search(r'\bstream=(?:"([^"]+)"|(\S+))', line)
        if match:
            name = match.group(1) or match.group(2)
            if name in names:
                found.add(name)
    return found


class Monitor:
    def __init__(self, root=ROOT, sleep=time.sleep, now=time.time):
        self.logs = Path(root) / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.state_path = self.logs / "xiaomi-token-watch.json"
        self.alert_path = self.logs / "xiaomi-token-watch.state"
        self.log_path = self.logs / "go2rtc.log"
        self.sleep, self.now = sleep, now
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            self.state = json.loads(self.state_path.read_text())
            if not isinstance(self.state, dict):
                raise ValueError("invalid state")
        except FileNotFoundError:
            self.state = {}
        # Corrupt state is fatal rather than silently discarding the cooldown.

    def save(self):
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state))
        temp.replace(self.state_path)

    def log(self, message):
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n"
        with (self.logs / "xiaomi-token-watch.log").open("a") as output:
            output.write(line)
            # launchd redirects stdout to this same file; avoid duplicate entries.
            if not os.path.samestat(os.fstat(output.fileno()), os.fstat(sys.stdout.fileno())):
                print(line, end="", flush=True)

    def alert(self, state, message):
        previous = self.alert_path.read_text().strip() if self.alert_path.exists() else ""
        self.alert_path.write_text(state)
        if previous == state:
            return
        self.log(message)
        host, token = os.environ.get("HA_IP"), os.environ.get("HA_TOKEN")
        if not host or not token or token == "your_ha_long_lived_access_token":
            self.log("HA notify skipped: HA_IP/HA_TOKEN not configured")
            return
        request = urllib.request.Request(
            "http://" + host + ":8123/api/services/persistent_notification/create",
            data=json.dumps({"title": "小米摄像头监控", "message": message,
                             "notification_id": "xiaomi_token_watch"}).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=5):
                pass
        except Exception:
            self.log("HA notify failed")

    def clear_alert(self):
        if self.alert_path.exists():
            self.alert_path.unlink()
            self.log("Xiaomi token/go2rtc alert cleared")

    def streams(self):
        with self.opener.open("http://127.0.0.1:1984/api/streams", timeout=5) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            raise ValueError("invalid streams response")
        return data

    def read_new_log(self):
        try:
            with self.log_path.open("rb") as source:
                stat = os.fstat(source.fileno())
                cursor = self.state.get("cursor", {})
                offset = cursor.get("offset", 0)
                same = cursor.get("inode") == stat.st_ino and offset <= stat.st_size
                start = max(offset if same else 0, stat.st_size - MAX_LOG_BYTES)
                source.seek(start)
                raw = source.read(MAX_LOG_BYTES)
                # Leave an incomplete final line for the next check.
                complete = raw.rfind(b"\n") + 1
                self.state["cursor"] = {"inode": stat.st_ino, "offset": start + complete}
                raw = raw[:complete]
                if start and (not same or start != offset):
                    raw = raw.partition(b"\n")[2]
                lines = raw.decode(errors="replace").splitlines()
                # Ignore failures preceding a manual or automatic restart.
                for index in range(len(lines) - 1, -1, -1):
                    if "INF go2rtc platform=" in lines[index]:
                        lines = lines[index + 1:]
                        break
                if not same and self.now() - stat.st_mtime > 1200:
                    return ""
                return "\n".join(lines)
        except FileNotFoundError:
            return ""

    def restart(self):
        result = subprocess.run(
            ["/bin/launchctl", "kickstart", "-k", "gui/%s/com.go2rtc" % os.getuid()],
            capture_output=True, timeout=15,
        )
        return result.returncode == 0

    def run(self):
        try:
            before = self.streams()
        except Exception:
            self.alert("go2rtc_down", "go2rtc API 无法访问，请检查服务是否启动。")
            return
        # Active producers expose format_name, disconnected ones expose url.
        names = xiaomi_stream_names(before, self.state.get("xiaomi_streams", []))
        self.state["xiaomi_streams"] = sorted(names)
        text = self.read_new_log()
        fresh = auth_streams(text, names, "401 Unauthorized")
        denied = auth_streams(text, names, "permit deny")
        pending = set(self.state.get("pending", [])) & set(before)
        targets = fresh | pending
        if not targets:
            if denied:
                self.alert("xiaomi_permit_deny", "摄像头权限或配置异常：" + ", ".join(sorted(denied)))
            else:
                self.clear_alert()
            self.save()
            return

        self.sleep(3)
        try:
            after = self.streams()
        except Exception:
            after = {}
        targets -= growing(before, after, targets)
        self.state["pending"] = sorted(targets)
        self.save()
        if not targets:
            self.clear_alert()
            return

        # Persist BEFORE launchctl; even failed attempts are rate limited.
        # Only NEW 401s justify a restart. Network timeouts never do.
        if not (fresh & targets):
            self.alert("xiaomi_recovery_pending", "重启后的摄像头尚未确认恢复出流：" + ", ".join(sorted(targets)))
            return
        if self.now() - self.state.get("last_restart", 0) < COOLDOWN:
            self.alert("xiaomi_401", "自动恢复后仍出现 401，处于一小时重启冷却期。若持续失败，请手动刷新小米 token。")
            return
        self.state["last_restart"] = self.now()
        self.save()
        self.log("Fresh Xiaomi 401; restarting go2rtc once: " + ", ".join(sorted(targets)))
        try:
            restarted = self.restart()
        except (OSError, subprocess.TimeoutExpired):
            restarted = False
        if not restarted:
            self.alert("xiaomi_restart_failed", "go2rtc 自动重启失败，请检查 launchd 服务；一小时内不重复重启。")
            return

        self.sleep(15)
        try:
            first = self.streams()
            self.sleep(5)
            second = self.streams()
            recovered = growing(first, second, targets)
        except Exception:
            recovered = set()
        targets -= recovered
        self.state["pending"] = sorted(targets)
        # Keep the cursor: next check must see any new post-restart 401s.
        self.save()
        if recovered:
            self.log("Video receiving after automatic restart: " + ", ".join(sorted(recovered)))
        if targets:
            self.alert("xiaomi_recovery_pending", "go2rtc 已自动重启，以下摄像头尚未确认恢复出流：" + ", ".join(sorted(targets)))
        else:
            self.clear_alert()


def main():
    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    # Protect both scheduled and manual/dashboard runs. OS releases on exit.
    with (logs / "xiaomi-token-watch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        Monitor().run()


if __name__ == "__main__":
    main()
