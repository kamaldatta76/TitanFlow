"""Run telemetry HTTP bridge (sidecar)."""

from __future__ import annotations

import logging

from titanflow.v03.telemetry_http_bridge import TelemetryHTTPBridge, TelemetryBridgeHandler

logging.basicConfig(level=logging.INFO)


def main() -> None:
    socket_path = "/run/titanflow-core/telemetry.sock"
    server = TelemetryHTTPBridge(("0.0.0.0", 19100), TelemetryBridgeHandler, socket_path)
    server.serve_forever()


if __name__ == "__main__":
    main()
