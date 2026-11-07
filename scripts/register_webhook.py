#!/usr/bin/env python3
"""
Register or update Webex webhooks with Cloudflare Tunnel URL.

This script:
1. Obtains the Cloudflare quick tunnel URL from the 'tunnel' service in compose.yaml
   (or from CLI argument --url / TUNNEL_URL environment variable).
2. Verifies reachability of the webhook service via {TUNNEL_URL}/health.
3. Authenticates with Webex using WEBEX_BOT_TOKEN (loaded from .env or environment).
4. Locates the two required Webex rooms:
   - 'IPA2026' (Manual test room)
   - 'IPA2026 Exam Room' (Grading room)
5. Registers or updates 'messages/created' webhooks for both rooms pointing to
   {TUNNEL_URL}/webhook.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

def load_env(env_path: Path) -> None:
    """Load key-value pairs from a .env file into os.environ if not already set."""
    if not env_path.is_file():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val


def get_tunnel_url_from_docker(timeout: int = 30) -> str | None:
    """
    Poll 'docker compose logs tunnel' to extract the Cloudflare trycloudflare.com URL.
    """
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    start_time = time.time()

    print("Checking Docker container logs for Cloudflare Tunnel URL...")
    while time.time() - start_time < timeout:
        for cmd in [
            ["docker", "compose", "--profile", "tunnel", "logs", "--no-color", "tunnel"],
            ["docker", "compose", "logs", "--no-color", "tunnel"],
        ]:
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0 and proc.stdout:
                    matches = url_pattern.findall(proc.stdout)
                    if matches:
                        return matches[-1]
            except Exception:
                pass
        time.sleep(2)

    return None


def webex_api(method: str, path: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    """Perform a Webex REST API call."""
    url = f"https://webexapis.com/v1{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
            return resp.status, json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, {"message": err_body}
    except Exception as e:
        return 0, {"message": str(e)}


def check_health(tunnel_url: str) -> bool:
    """Check if {tunnel_url}/health is reachable and returns HTTP 200."""
    health_url = f"{tunnel_url.rstrip('/')}/health"
    try:
        req = urllib.request.Request(health_url, headers={"User-Agent": "webhook-registrar"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("result") == "healthy"
    except Exception:
        return False
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Register or update Webex webhooks with Cloudflare Tunnel URL"
    )
    parser.add_argument(
        "--url",
        dest="tunnel_url",
        help="Manually specify tunnel URL (e.g., https://xyz.trycloudflare.com)",
    )
    parser.add_argument(
        "--token",
        dest="bot_token",
        help="Webex Bot Token (defaults to WEBEX_BOT_TOKEN in .env or environment)",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Automatically start docker compose tunnel service if not running",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List current Webex rooms and registered webhooks, then exit",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete existing IPA2026 webhooks, then exit",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds when waiting for tunnel URL from logs (default: 30)",
    )

    args = parser.parse_args()

    # 1. Load .env
    project_root = Path(__file__).resolve().parents[1]
    load_env(project_root / ".env")
    load_env(Path.cwd() / ".env")

    token = args.bot_token or os.environ.get("WEBEX_BOT_TOKEN")
    if not token:
        print("[!] Error: WEBEX_BOT_TOKEN is not set.")
        print("    Please set WEBEX_BOT_TOKEN in your .env file or environment.")
        sys.exit(1)

    # 2. Verify Bot Identity
    status, bot_info = webex_api("GET", "/people/me", token)
    if status != 200:
        print(f"[!] Error authenticating with Webex (HTTP {status}): {bot_info.get('message')}")
        print("    Please verify your WEBEX_BOT_TOKEN.")
        sys.exit(1)

    bot_name = bot_info.get("displayName", "Unknown")
    bot_emails = bot_info.get("emails", ["Unknown"])
    print(f"[+] Authenticated as Bot: '{bot_name}' ({bot_emails[0]})")

    # 3. Fetch Rooms
    status, rooms_data = webex_api("GET", "/rooms?max=100", token)
    if status != 200:
        print(f"[!] Error fetching rooms (HTTP {status}): {rooms_data.get('message')}")
        sys.exit(1)

    rooms = rooms_data.get("items", [])
    room_map = {}
    for r in rooms:
        title = r.get("title", "").strip()
        room_map[title.lower()] = r

    # 4. Fetch Existing Webhooks
    status, webhooks_data = webex_api("GET", "/webhooks", token)
    if status != 200:
        print(f"[!] Error fetching webhooks (HTTP {status}): {webhooks_data.get('message')}")
        sys.exit(1)

    existing_webhooks = webhooks_data.get("items", [])

    # If --list requested
    if args.list:
        print("\n--- Available Webex Rooms ---")
        for r in rooms:
            print(f" - [{r.get('id')}] {r.get('title')}")
        print("\n--- Registered Webhooks ---")
        if not existing_webhooks:
            print(" (None)")
        for w in existing_webhooks:
            print(
                f" - [{w.get('id')}] {w.get('name')}: "
                f"targetUrl={w.get('targetUrl')} (filter={w.get('filter')})"
            )
        return

    # If --delete requested
    if args.delete:
        print("\n--- Deleting IPA2026 Webhooks ---")
        deleted = 0
        for w in existing_webhooks:
            name = w.get("name", "")
            if name in ("IPA2026-Manual", "IPA2026-Exam") or "IPA2026" in name:
                w_id = w.get("id")
                del_status, _ = webex_api("DELETE", f"/webhooks/{w_id}", token)
                if del_status in (200, 204):
                    print(f"[-] Deleted webhook: {name} ({w_id})")
                    deleted += 1
                else:
                    print(f"[!] Failed to delete webhook: {name} ({w_id})")
        print(f"[+] Total deleted: {deleted}")
        return

    # 5. Determine Tunnel URL
    tunnel_url = args.tunnel_url or os.environ.get("TUNNEL_URL")

    if not tunnel_url:
        if args.start:
            print("[+] Starting Docker Compose tunnel service...")
            subprocess.run(
                ["docker", "compose", "--profile", "tunnel", "up", "-d"],
                cwd=project_root,
                check=False,
            )

        tunnel_url = get_tunnel_url_from_docker(timeout=args.timeout)

    if not tunnel_url:
        print("\n[!] Could not automatically detect Cloudflare Tunnel URL.")
        print("    Make sure your tunnel service is running:")
        print("        docker compose --profile tunnel up -d")
        print("    Or provide the URL explicitly:")
        print("        python scripts/register_webhook.py --url https://<subdomain>.trycloudflare.com")
        sys.exit(1)

    tunnel_url = tunnel_url.rstrip("/")
    target_url = f"{tunnel_url}/webhook"
    print(f"\n[+] Using Tunnel URL: {tunnel_url}")
    print(f"[+] Webhook Target URL: {target_url}")

    # 6. Verify Tunnel Health
    print("[*] Testing reachability via {tunnel_url}/health...")
    if check_health(tunnel_url):
        print("    ✓ Tunnel is reachable and webhook service is healthy!")
    else:
        print("    [!] Warning: {tunnel_url}/health did not respond with 'healthy'.")
        print("        Please ensure 'webhook' container is running (docker compose up -d webhook).")
        print("        Continuing with webhook registration...")

    # 7. Identify Rooms
    target_rooms = [
        ("IPA2026", "IPA2026-Manual"),
        ("IPA2026 Exam Room", "IPA2026-Exam"),
    ]

    print("\n--- Registering Webhooks ---")
    for room_title, webhook_name in target_rooms:
        matched_room = room_map.get(room_title.lower())
        if not matched_room:
            # Fallback search
            for r in rooms:
                if room_title.lower() in r.get("title", "").lower():
                    matched_room = r
                    break

        if not matched_room:
            print(f"[!] Room '{room_title}' not found in bot's accessible rooms.")
            print(f"    Please make sure the bot '{bot_emails[0]}' is added to the '{room_title}' room!")
            continue

        room_id = matched_room.get("id")
        room_filter = f"roomId={room_id}"

        # Check existing webhook for this room or name
        found_hook = None
        for w in existing_webhooks:
            if w.get("filter") == room_filter or w.get("name") == webhook_name:
                found_hook = w
                break

        if found_hook:
            hook_id = found_hook.get("id")
            current_target = found_hook.get("targetUrl")
            if current_target == target_url:
                print(f"[=] Webhook '{webhook_name}' is already up-to-date (ID: {hook_id})")
            else:
                # Update existing webhook
                update_payload = {"name": webhook_name, "targetUrl": target_url}
                up_status, up_res = webex_api("PUT", f"/webhooks/{hook_id}", token, update_payload)
                if up_status == 200:
                    print(f"[✓] Updated webhook '{webhook_name}' to {target_url} (ID: {hook_id})")
                else:
                    # If PUT fails, delete and recreate
                    webex_api("DELETE", f"/webhooks/{hook_id}", token)
                    create_payload = {
                        "name": webhook_name,
                        "targetUrl": target_url,
                        "resource": "messages",
                        "event": "created",
                        "filter": room_filter,
                    }
                    cr_status, cr_res = webex_api("POST", "/webhooks", token, create_payload)
                    if cr_status == 200:
                        print(f"[✓] Re-created webhook '{webhook_name}' (ID: {cr_res.get('id')})")
                    else:
                        print(f"[!] Failed to update webhook '{webhook_name}': {cr_res.get('message')}")
        else:
            # Create new webhook
            create_payload = {
                "name": webhook_name,
                "targetUrl": target_url,
                "resource": "messages",
                "event": "created",
                "filter": room_filter,
            }
            cr_status, cr_res = webex_api("POST", "/webhooks", token, create_payload)
            if cr_status == 200:
                print(f"[✓] Created webhook '{webhook_name}' (ID: {cr_res.get('id')})")
            else:
                print(f"[!] Failed to create webhook '{webhook_name}': {cr_res.get('message')}")

    print("\n[+] Webhook registration completed!")


if __name__ == "__main__":
    main()
