"""Local management surface for TitanOcta."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from typing import Any

from .config import MANAGEMENT_HOST, MANAGEMENT_PORT


def titanocta_version() -> str:
    for dist_name in ("titanocta", "titanflow"):
        try:
            return version(dist_name)
        except PackageNotFoundError:
            continue
    return "0.0.0-dev"


def management_payload(install_root: str) -> dict[str, Any]:
    root = Path(install_root).expanduser()
    config_path = root / "config.json"
    config: dict[str, Any] = {}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    sqlite_version = sqlite3.sqlite_version
    return {
        "status": "ok",
        "version": titanocta_version(),
        "tier": config.get("tier", "free"),
        "active_model": config.get("active_model", "unknown"),
        "flow_status": config.get("health", {}).get("flow", "red"),
        "node_id": config.get("node_id", "unknown"),
        "sqlite_version": sqlite_version,
        "sqlite_wal_safe": _sqlite_wal_safe(sqlite_version),
    }


class _HealthHandler(BaseHTTPRequestHandler):
    install_root = Path("~/.titanocta").expanduser()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            payload = management_payload(str(self.install_root))
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/user/") and self.path.endswith("/tier"):
            self._send_json(
                501,
                {
                    "error": "not_implemented",
                    "endpoint": "GET /user/{id}/tier",
                    "phase": "phase_b",
                },
            )
            return

        if self.path != "/health":
            self.send_error(404, "Not Found")
            return

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/boost/request":
            self._send_json(
                501,
                {
                    "error": "not_implemented",
                    "endpoint": "POST /boost/request",
                    "phase": "phase_b",
                },
            )
            return
        self.send_error(404, "Not Found")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def is_management_alive(host: str = MANAGEMENT_HOST, port: int = MANAGEMENT_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def run_management_server(install_root: str, host: str = MANAGEMENT_HOST, port: int = MANAGEMENT_PORT) -> None:
    handler = type("TitanOctaHealthHandler", (_HealthHandler,), {"install_root": Path(install_root).expanduser()})
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def start_management_server_detached(
    install_root: str,
    *,
    host: str = MANAGEMENT_HOST,
    port: int = MANAGEMENT_PORT,
    python_executable: str | None = None,
) -> bool:
    if is_management_alive(host, port):
        return False
    env = os.environ.copy()
    env["TITANOCTA_INSTALL_ROOT"] = str(Path(install_root).expanduser())
    cmd = [
        python_executable or sys.executable,
        "-m",
        "titanocta.cli",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    subprocess.Popen(  # noqa: S603
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def _sqlite_wal_safe(version_str: str) -> bool:
    try:
        parts = tuple(int(x) for x in version_str.split("."))
    except Exception:  # noqa: BLE001
        return False
    return parts >= (3, 52, 0)
