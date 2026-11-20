"""Authorize this owner's Yandex app using the documented device-code flow.

python scripts/connect_yandex.py start
python scripts/connect_yandex.py poll  # waits until the owner approves, up to code expiry
The access token is written to .env and is never printed.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from dotenv import set_key

ROOT = Path(__file__).resolve().parents[1]
CLIENT_FILE = ROOT / "yandex-client.secrets.json"
STATE_FILE = ROOT / "data" / "yandex-device.json"


def credentials():
    data = json.loads(CLIENT_FILE.read_text(encoding="utf-8-sig"))
    if not data.get("client_id") or not data.get("client_secret"):
        raise ValueError("Fill client_id and client_secret in yandex-client.secrets.json first")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "poll"])
    args = parser.parse_args()
    app = credentials()
    with httpx.Client(timeout=30) as client:
        if args.action == "start":
            response = client.post("https://oauth.yandex.ru/device/code", data={"client_id": app["client_id"]})
            data = response.json()
            if response.status_code != 200:
                raise ValueError("Yandex rejected the app: " + data.get("error", "unknown"))
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({**data, "expires_at": time.time() + data["expires_in"]}), encoding="utf-8")
            print(json.dumps({"verification_url": data["verification_url"], "user_code": data["user_code"],
                              "expires_in": data["expires_in"]}))
            return
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        delay = max(5, state.get("interval", 5))
        while time.time() < state["expires_at"]:
            response = client.post("https://oauth.yandex.ru/token", auth=(app["client_id"], app["client_secret"]),
                                   data={"grant_type": "device_code", "code": state["device_code"]})
            data = response.json()
            if response.status_code == 200 and data.get("access_token"):
                token = data["access_token"]
                check = client.get("https://cloud-api.yandex.net/v1/disk/resources", params={"path": "app:/"},
                                   headers={"Authorization": f"OAuth {token}"})
                if check.status_code != 200:
                    raise ValueError("Token lacks access to the application folder")
                env_file = str(ROOT / ".env")
                set_key(env_file, "YANDEX_DISK_TOKEN", token)
                if data.get("refresh_token"):
                    set_key(env_file, "YANDEX_DISK_REFRESH_TOKEN", data["refresh_token"])
                set_key(env_file, "STORAGE_BACKEND", "yandex")
                STATE_FILE.unlink(missing_ok=True)
                print("Yandex Disk connected; token stored in .env (hidden)")
                return
            error = data.get("error", "unknown")
            if error == "slow_down":
                delay += 5
            elif error != "authorization_pending":
                raise ValueError("Yandex authorization failed: " + error)
            time.sleep(delay)
    raise ValueError("Authorization code expired; run start again")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # No request URLs, response bodies or credentials are included in errors.
        print(str(error) if isinstance(error, ValueError) else f"Connection failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
