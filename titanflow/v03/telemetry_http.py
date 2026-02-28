"""HTTP telemetry bridge scaffold (for gateway/sidecar)."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable


class TelemetryHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, snapshot_fn: Callable[[], dict]):
        super().__init__(server_address, RequestHandlerClass)
        self.snapshot_fn = snapshot_fn


class TelemetryHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/status"):
            self.send_response(404)
            self.end_headers()
            return
        payload = self.server.snapshot_fn()
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Silence default HTTP server logging
        return
