"""Loopback-only streaming protocol fixture for the Copilot browser suite."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from time import sleep


@contextmanager
def simulated_provider():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            body = json.loads(raw)
            if (
                not body.get("stream")
                or body.get("model") != "local-browser-fixture"
                or any(key in raw for key in (b"FICTIONAL-DEMO", b"speed_kmh", b"vehicle_code"))
            ):
                self.send_error(400)
                return
            planning = bool(body.get("tools"))
            requests.append("plan" if planning else "interpretation")
            if len(requests) == 1:
                self.send_error(429)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()

            def chunk(delta, finish=None):
                choice = dict(index=0, delta=delta, finish_reason=finish)
                obj = dict(
                    id="local-fixture",
                    object="chat.completion.chunk",
                    created=1,
                    model="local-browser-fixture",
                    choices=[choice],
                )
                self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
                self.wfile.flush()

            try:
                if planning:
                    fn = dict(name="statistics", arguments='{"axis":"lateral"}')
                    chunk(dict(tool_calls=[dict(index=0, id="test-call", type="function", function=fn)]))
                    chunk({}, "tool_calls")
                else:
                    for section, field in (("summary", "rms"), ("observations", "peak_absolute")):
                        claim = dict(
                            section=section, evidence_id="metric.accel.lateral." + field, passage_id=""
                        )
                        chunk(dict(content=json.dumps(claim) + "\n"))
                        sleep(1.6)
                    chunk({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", requests
        finally:
            server.shutdown()
            thread.join()
