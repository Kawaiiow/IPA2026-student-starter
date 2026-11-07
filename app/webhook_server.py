"""Part 4 student implementation."""

import json

import logging

import os

import requests

from concurrent.futures import ThreadPoolExecutor

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path

from tempfile import TemporaryDirectory, NamedTemporaryFile

from app.integration import process_request

from app.webex_client import WEBEX_API_BASE, WebexClient

logger = logging.getLogger(__name__)

class WebexEventHandler:
    def __init__(self, client, bot_person_id, processor=None):
        self.client = client
        self.bot_person_id = bot_person_id
        self.processor = processor or process_request

    def handle_event(self, event):
        if not isinstance(event, dict):
            return None

        if event.get("resource") != "messages" or event.get("event") != "created":
            return None

        data = event.get("data")
        if not isinstance(data, dict) or "id" not in data:
            return None

        # Filter out self-events before network lookup
        if data.get("personId") == self.bot_person_id:
            return None

        # Fetch message from Webex API
        try:
            message = self.client.get_message(data["id"])
        except Exception as e:
            logger.error("Failed to fetch message %s: %s", data["id"], e)
            return None

        # Filter out self-authored messages
        if message.get("personId") == self.bot_person_id:
            return None

        room_id = message.get("roomId")
        mentioned_people = message.get("mentionedPeople", [])
        files = message.get("files", [])

        # Multiple attachments
        if len(files) > 1:
            reply = {"status": "error", "result": "multiple_attachments"}
            if room_id:
                self.client.send_message(room_id, json.dumps(reply))
            return reply

        # No attachment
        if len(files) == 0:
            reply = self.processor(mentioned_people, None, self.bot_person_id)
            if room_id:
                self.client.send_message(room_id, json.dumps(reply))
            return reply

        # Exactly 1 attachment
        temp_path = None
        try:
            content = self.client.download_file(files[0])
            with NamedTemporaryFile(delete=False, suffix=".yaml") as tmp:
                tmp.write(content)
                temp_path = Path(tmp.name)

            reply = self.processor(mentioned_people, temp_path, self.bot_person_id)
            if room_id:
                self.client.send_message(room_id, json.dumps(reply))
            return reply
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

def make_http_handler(submit_event):
    class WebhookHandler(BaseHTTPRequestHandler):
        def _send_json(self, status_code, body_dict):
            payload = json.dumps(body_dict).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/health":
                self._send_json(200, {"status": "ok", "result": "healthy"})
            else:
                self._send_json(404, {"status": "error", "result": "not_found"})

        def do_POST(self):
            if self.path == "/webhook":
                length_str = self.headers.get("Content-Length")
                if not length_str:
                    self._send_json(400, {"status": "error", "result": "invalid_event"})
                    return

                try:
                    content_length = int(length_str)
                except ValueError:
                    self._send_json(400, {"status": "error", "result": "invalid_event"})
                    return

                if content_length < 1 or content_length > 1_000_000:
                    self._send_json(400, {"status": "error", "result": "invalid_event"})
                    return

                body = self.rfile.read(content_length)
                try:
                    event = json.loads(body.decode("utf-8"))
                    if not isinstance(event, dict):
                        raise ValueError()
                except Exception:
                    self._send_json(400, {"status": "error", "result": "invalid_event"})
                    return

                submit_event(event)
                self._send_json(200, {"status": "ok", "result": "accepted"})
            else:
                self._send_json(404, {"status": "error", "result": "not_found"})

        def log_message(self, format, *args):
            # Suppress noisy HTTP request logging
            pass

    return WebhookHandler

def main():
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("WEBEX_BOT_TOKEN")
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8000"))

    client = WebexClient(token=token)

    # Resolve Bot Person ID using GET /v1/people/me
    resp = requests.get(f"{WEBEX_API_BASE}/people/me", headers=client.headers, timeout=10)
    resp.raise_for_status()
    bot_person_id = resp.json()["id"]
    logger.info("Bot initialized: ID=%s", bot_person_id)

    event_handler = WebexEventHandler(client, bot_person_id)
    executor = ThreadPoolExecutor(max_workers=1)

    def submit_event(evt):
        executor.submit(event_handler.handle_event, evt)

    handler_cls = make_http_handler(submit_event)
    server = ThreadingHTTPServer((host, port), handler_cls)
    logger.info("Listening on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        executor.shutdown(wait=False)

if __name__ == "__main__":
    main()
