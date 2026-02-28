"""HTTP server that fetches telemetry via AF_UNIX socket."""

from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from titanflow.v03.telemetry_bridge import fetch_unix_snapshot


class TelemetryHTTPBridge(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, socket_path: str):
        super().__init__(server_address, RequestHandlerClass)
        self.socket_path = socket_path


class TelemetryBridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/status"):
            self.send_response(404)
            self.end_headers()
            return
        payload = asyncio.run(fetch_unix_snapshot(self.server.socket_path))
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return
