#!/usr/bin/env python3
"""
模拟 go2rtc 的小米登录流程获取 passToken (含短信/邮箱验证)。
完整复现 go2rtc cloud.go 中的 Login -> authStart -> sendTicket -> verify 流程。
用法: python3 scripts/get_xiaomi_token.py
"""
import getpass
import hashlib
import json
import os
import random
import re
import string
import argparse
import subprocess
import sys
import time
from pathlib import Path

import requests

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "go2rtc", "config.yml")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "go2rtc.log")
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
SID = "xiaomiio"
BASE = "https://account.xiaomi.com"


def rand_string(length):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def parse_response(text):
    return json.loads(text.replace("&&&START&&&", ""))


def find_cookie(response, name):
    for cookie in response.cookies:
        if cookie.name == name:
            return cookie.value
    return ""


def xiaomi_login(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'APP/com.xiaomi.mihome APPV/9.0.0 iosPassportSDK/4.7.2 iOS/18.0',
    })

    # === Step 1: 获取登录签名 ===
    print("[Step 1] 获取登录签名...")
    res1 = session.get(f"{BASE}/pass/serviceLogin?_json=true&sid={SID}")
    data1 = parse_response(res1.text)
    qs = data1.get("qs", "")
    sign = data1.get("_sign", "")
    sid = data1.get("sid", SID)
    callback = data1.get("callback", "")
    print(f"  OK (sid={sid})")

    # === Step 2: 提交登录 ===
    print("[Step 2] 提交登录请求...")
    post_data = {
        '_json': 'true',
        'hash': hashlib.md5(password.encode()).hexdigest().upper(),
        'sid': sid,
        'callback': callback,
        '_sign': sign,
        'qs': qs,
        'user': username,
    }
    device_id = rand_string(16)
    res2 = session.post(
        f"{BASE}/pass/serviceLoginAuth2",
        data=post_data,
        cookies={'deviceId': device_id}
    )
    data2 = parse_response(res2.text)

    result = data2.get('result')
    desc = data2.get('desc', data2.get('description', ''))
    print(f"  result={result}, desc={desc}")

    if result != 'ok':
        code = data2.get('code', 'N/A')
        print(f"\n登录失败: code={code}, desc={desc}")
        if code == 70016:
            print("  可能原因: 密码错误 / 账号锁定 / 需要先在米家APP中登录")
        return None

    # 如果有 location 直接完成
    location = data2.get('location', '')
    if location and data2.get('passToken'):
        print("  直接获取到 passToken!")
        return finish_auth(session, data2)

    # === Step 3: 安全身份验证 (notificationUrl) ===
    notification_url = data2.get('notificationUrl', '')
    if not notification_url:
        print("  错误: 无 location 也无 notificationUrl")
        return None

    print("[Step 3] 需要安全身份验证...")

    # 3a: 获取验证方式列表 (authStart -> identity/list)
    list_url = notification_url.replace(
        "/fe/service/identity/authStart", "/identity/list"
    )
    # 确保完整 URL
    if list_url.startswith("/"):
        list_url = BASE + list_url

    res3 = session.get(list_url)
    data3 = parse_response(res3.text)
    flag = data3.get("flag", 0)
    identity_session = find_cookie(res3, "identity_session")

    verify_name = {4: "Phone", 8: "Email"}.get(flag, "")
    print(f"  验证方式: flag={flag} ({verify_name or '未知'})")
    print(f"  identity_session: {'已获取' if identity_session else '未获取'}")

    if not verify_name:
        print(f"  错误: 不支持的验证方式 flag={flag}")
        print(f"  完整响应: {json.dumps(data3, ensure_ascii=False)}")
        return None

    # 3b: 获取掩码手机号/邮箱 (verifyPhone / verifyEmail)
    cookies_str = f"identity_session={identity_session}"
    verify_url = f"{BASE}/identity/auth/verify{verify_name}?_flag={flag}&_json=true"
    res4 = session.get(verify_url, cookies={"identity_session": identity_session})
    data4 = parse_response(res4.text)

    masked = data4.get('maskedPhone', data4.get('maskedEmail', ''))
    print(f"  验证目标: {masked}")

    # 3c: 发送验证码 (sendPhoneTicket / sendEmailTicket)
    print(f"  正在发送验证码到 {masked}...")
    send_url = f"{BASE}/identity/auth/send{verify_name}Ticket"
    res5 = session.post(
        send_url,
        data={'_json': 'true', 'icode': '', 'retry': '0'},
        cookies={"identity_session": identity_session}
    )
    data5 = parse_response(res5.text)
    print(f"  发送结果: code={data5.get('code', 'N/A')}")

    if data5.get('code', -1) != 0:
        print(f"  发送失败: {json.dumps(data5, ensure_ascii=False)}")
        return None

    # 3d: 等待用户输入验证码
    ticket = input(f"\n请输入收到的验证码: ").strip()

    # 3e: 提交验证码 (verifyPhone / verifyEmail)
    print("[Step 4] 提交验证码...")
    verify_submit_url = (
        f"{BASE}/identity/auth/verify{verify_name}"
        f"?_flag={flag}&ticket={ticket}&trust=false&_json=true"
    )
    res6 = session.post(
        verify_submit_url,
        cookies={"identity_session": identity_session}
    )
    data6 = parse_response(res6.text)
    print(f"  验证结果: {json.dumps({k:v for k,v in data6.items() if k != 'location'}, ensure_ascii=False)}")

    location = data6.get('location', '')
    if not location:
        print(f"  验证失败: 未获取到 location")
        print(f"  完整响应: {json.dumps(data6, ensure_ascii=False)}")
        return None

    # === Step 5: finishAuth - 跟随 location 获取 passToken ===
    print("[Step 5] 获取最终 token...")
    return finish_auth_from_location(session, location)


def finish_auth(session, data):
    """直接从 login 响应中获取 token (不需要验证的情况)"""
    user_id = str(data['userId'])
    pass_token = data['passToken']
    location = data.get('location', '')

    if location:
        res = session.get(location, allow_redirects=True)

    return {'user_id': user_id, 'pass_token': pass_token}


def finish_auth_from_location(session, location):
    """从 location URL 跟随重定向获取 token (go2rtc finishAuth)"""
    res = session.get(location, allow_redirects=False)

    user_id = ""
    pass_token = ""
    service_token = ""

    # 跟随重定向链，收集 cookies
    while res is not None:
        for cookie in res.cookies:
            if cookie.name == "userId":
                user_id = cookie.value
            elif cookie.name == "passToken":
                pass_token = cookie.value
            elif cookie.name == "serviceToken":
                service_token = cookie.value

        # 手动跟随重定向
        next_url = res.headers.get("Location", "")
        if next_url and res.status_code in (301, 302, 303, 307, 308):
            res = session.get(next_url, allow_redirects=False)
        else:
            break

    print(f"  userId:       {user_id}")
    print(f"  passToken:    {pass_token[:20]}..." if pass_token else "  passToken: (empty)")
    print(f"  serviceToken: {service_token[:20]}..." if service_token else "  serviceToken: (empty)")

    if not pass_token:
        print("  错误: 未获取到 passToken")
        return None

    return {'user_id': user_id, 'pass_token': pass_token}


def write_config(user_id, pass_token, auto_yes=False):
    config_path = os.path.abspath(CONFIG_PATH)
    if auto_yes:
        answer = "y"
    else:
        answer = input(f"\n是否自动写入 go2rtc 配置 ({config_path})? [y/N]: ").strip().lower()
    if answer != "y":
        print(f'\n手动添加到 go2rtc config.yml 的 xiaomi 段:')
        print(f'  "{user_id}": {pass_token}')
        return False

    try:
        with open(config_path, "r") as f:
            content = f.read()

        pattern = r'(xiaomi:\n)(\s+"' + re.escape(user_id) + r'":\s+.+\n)?'
        replacement = f'xiaomi:\n  "{user_id}": {pass_token}\n'
        new_content, count = re.subn(pattern, replacement, content)

        if count == 0:
            new_content = content.rstrip() + f'\n\nxiaomi:\n  "{user_id}": {pass_token}\n'

        # Remove template token entries that would otherwise linger after copying
        # config.example.yml to config.yml.
        new_content = re.sub(r'(?m)^\s+"YOUR_XIAOMI_USER_ID":\s+.+\n?', '', new_content)
        new_content, stream_warnings = update_xiaomi_streams(new_content, user_id, load_env_values())

        with open(config_path, "w") as f:
            f.write(new_content)

        print("已写入配置文件!")
        if stream_warnings:
            print("\n配置提醒：")
            for warning in stream_warnings:
                print(f"  - {warning}")
        return True
    except Exception as e:
        print(f"写入失败: {e}")
        print(f'\n手动添加到 go2rtc config.yml:\n  "{user_id}": {pass_token}')
        return False


def restart_go2rtc():
    print("\n正在重启 go2rtc...")
    subprocess.run(["launchctl", "stop", "com.go2rtc"], check=False)
    time.sleep(1)
    result = subprocess.run(["launchctl", "start", "com.go2rtc"], check=False)
    if result.returncode == 0:
        print("go2rtc 已重启")
        return True

    print("go2rtc 重启命令执行失败，请手动运行：")
    print("  launchctl stop com.go2rtc && launchctl start com.go2rtc")
    return False


def check_go2rtc_log(before_size=0):
    log_path = os.path.abspath(LOG_PATH)
    time.sleep(8)

    if not os.path.exists(log_path):
        print(f"未找到 go2rtc 日志：{log_path}")
        return False

    with open(log_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(min(before_size, size))
        new_log = f.read().decode("utf-8", errors="replace")

    lines = [line for line in new_log.splitlines() if line.strip()]
    recent = "\n".join(lines[-30:])

    if "401 Unauthorized" in new_log:
        print("\n刷新后仍检测到 401 Unauthorized：")
        print(recent)
        return False

    print("\n刷新后未检测到新的 401 Unauthorized。")
    if recent:
        print("最近 go2rtc 日志：")
        print(recent)
    return True


def current_log_size():
    log_path = os.path.abspath(LOG_PATH)
    if not os.path.exists(log_path):
        return 0
    return os.path.getsize(log_path)


def load_env_values():
    env_path = Path(ENV_PATH)
    values = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def camera_prefix_to_stream(prefix):
    name = prefix.removeprefix("CAM_").lower()
    return f"{name}_cam"


def update_xiaomi_streams(content, user_id, env_values):
    warnings = []

    # Stream URLs must reference the same userId that owns the passToken.
    content = re.sub(r"xiaomi://[^:@/\s]+:", f"xiaomi://{user_id}:", content)

    camera_prefixes = sorted(
        key[:-3] for key in env_values
        if key.startswith("CAM_") and key.endswith("_IP") and env_values.get(key)
    )
    for prefix in camera_prefixes:
        stream_name = camera_prefix_to_stream(prefix)
        ip = env_values.get(f"{prefix}_IP", "")
        did = env_values.get(f"{prefix}_DID", "")
        model = env_values.get(f"{prefix}_MODEL", "")
        if not all([ip, did, model]):
            warnings.append(f"{stream_name}: .env 缺少 {prefix}_IP/DID/MODEL，未自动更新")
            continue

        url = f"xiaomi://{user_id}:cn@{ip}?did={did}&model={model}"
        pattern = (
            r"(?m)^(\s{4}-\s+)xiaomi://[^\n]*"
            r"(?=(?:\n\s{2}[A-Za-z0-9_-]+:|\n\s*$|\Z))"
        )

        def replace_stream(match):
            before = content[:match.start()]
            last_stream = None
            for stream_match in re.finditer(r"(?m)^\s{2}([A-Za-z0-9_-]+):\s*$", before):
                last_stream = stream_match.group(1)
            if last_stream == stream_name:
                return match.group(1) + url
            return match.group(0)

        content = re.sub(pattern, replace_stream, content)

    for stream_name, url in re.findall(r"(?m)^\s{2}([A-Za-z0-9_-]+):\s*\n\s{4}-\s+(xiaomi://[^\n]+)", content):
        if any(token in url for token in ("YOUR_XIAOMI_USER_ID", "CAMERA_IP", "DEVICE_ID", "CAMERA_MODEL")):
            warnings.append(f"{stream_name}: 仍包含占位符，请在 go2rtc/config.yml 中补齐摄像头 IP/DID/model")

    return content, warnings


def main():
    parser = argparse.ArgumentParser(description="刷新小米 passToken 并可自动重启 go2rtc")
    parser.add_argument("--yes", "-y", action="store_true", help="自动写入 go2rtc/config.yml")
    parser.add_argument("--restart", action="store_true", help="写入后自动重启 go2rtc")
    parser.add_argument("--check", action="store_true", help="重启后检查是否仍出现新的 401")
    args = parser.parse_args()

    username = input("小米账号 (手机号或邮箱): ").strip()
    password = getpass.getpass("密码: ")

    print(f"\n正在登录小米云端 (用户: {username})...\n")
    result = xiaomi_login(username, password)

    if not result:
        sys.exit(1)

    user_id = result['user_id']
    pass_token = result['pass_token']

    print(f"\n{'='*40}")
    print(f"登录成功!")
    print(f"User ID:   {user_id}")
    print(f"passToken: {pass_token[:30]}...")
    print(f"{'='*40}")

    before_size = current_log_size()
    wrote = write_config(user_id, pass_token, auto_yes=args.yes)

    if args.restart and wrote:
        restart_go2rtc()
        if args.check:
            check_go2rtc_log(before_size)
    elif wrote:
        print("\n下一步: 重启 go2rtc")
        print("  launchctl stop com.go2rtc && launchctl start com.go2rtc")


if __name__ == "__main__":
    main()
