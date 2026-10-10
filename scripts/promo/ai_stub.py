"""A stand-in OpenAI-compatible endpoint for the demo's AI assistant.

The promo films the assistant answering a question, and a real model
would make the take slow, costly and different on every run. This local
server answers every chat completion with the same scripted reply, after a
short pause so the typing indicator has time to show.
"""

import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = """Here is what needs you this week:

- **Fix mobile navigation overlap** - urgent, due Sep 27
- **Design new landing page hero** - in progress with Sam
- **Set up newsletter signup API** - due Oct 4

The launch checklist is on track: DNS and staging are done, copy review is next."""


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - the http.server hook name
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        time.sleep(1.2)
        body = json.dumps(
            {
                "id": "chatcmpl-demo",
                "object": "chat.completion",
                "created": 0,
                "model": "demo",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": REPLY},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def ai_stub():
    """Serve the stub on a free port; yields its OpenAI base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
