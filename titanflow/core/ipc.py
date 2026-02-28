"""IPC server for Core <-> modules."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from titanflow.core.auth import AuthManager
from titanflow.core.database_broker import DatabaseBroker
from titanflow.core.http_proxy import HttpProxy
from titanflow.core.llm_broker import LLMBroker, Priority
from titanflow.core.audit import AuditLogger
from titanflow.core.module_supervisor import ModuleSupervisor

logger = logging.getLogger("titanflow.ipc")


class PermissionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _response_ok(req_id: str, result: dict) -> dict:
    return {"id": req_id, "status": "ok", "result": result}


def _response_err(req_id: str, code: str, message: str) -> dict:
    return {"id": req_id, "status": "error", "error": {"code": code, "message": message}}


class IPCServer:
    def __init__(
        self,
        auth: AuthManager,
        llm: LLMBroker,
        db: DatabaseBroker,
        http_proxy: HttpProxy,
        audit: AuditLogger,
        supervisor: ModuleSupervisor,
    ) -> None:
        self.auth = auth
        self.llm = llm
        self.db = db
        self.http_proxy = http_proxy
        self.audit = audit
        self.supervisor = supervisor
        self._http_windows: dict[str, list[float]] = {}

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        module_id: str | None = None
        session_id: str | None = None
        peer = writer.get_extra_info("peername")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                msg = json.loads(line.decode())
                req_id = msg.get("id", "")
                method = msg.get("method", "")
                params = msg.get("params", {})
                started = time.monotonic()

                try:
                    if method == "auth.register":
                        module_id = msg.get("module")
                        token = msg.get("token", "")
                        if not module_id or not self.auth.validate_token(module_id, token):
                            raise PermissionError("PERMISSION_DENIED", "Invalid module token")
                        session = self.auth.register_session(module_id)
                        session_id = session.session_id
                        self.supervisor.module_connected(module_id)
                        response = _response_ok(req_id, {
                            "session_id": session_id,
                            "granted_permissions": list(session.permissions.keys()),
                            "denied_permissions": [],
                        })
                    else:
                        session_id = msg.get("session_id", "")
                        session = self.auth.get_session(session_id)
                        if not session:
                            raise PermissionError("UNAUTHORIZED", "Invalid session")
                        module_id = session.module_id
                        self.supervisor.module_heartbeat(module_id)
                        response = await self._dispatch(session, req_id, method, params)
                except PermissionError as e:
                    response = _response_err(req_id, e.code, e.message)
                except Exception as e:
                    logger.exception("IPC error")
                    response = _response_err(req_id, "INTERNAL_ERROR", str(e))

                await self._audit_request(module_id, method, params, response, started)
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
        finally:
            if module_id:
                await self.supervisor.module_disconnected(module_id)
            writer.close()
            await writer.wait_closed()


    def _check_http_rate(self, module_id: str, limit: int) -> bool:
        now = time.monotonic()
        window = self._http_windows.setdefault(module_id, [])
        window[:] = [t for t in window if t > now - 60]
        if len(window) >= limit:
            return False
        window.append(now)
        return True

    async def _audit_request(
        self,
        module_id: str | None,
        method: str,
        params: dict[str, Any],
        response: dict,
        started: float,
    ) -> None:
        details: dict[str, Any] = {}
        if method.startswith("db."):
            details["table"] = params.get("table")
        elif method == "llm.generate":
            details["model"] = params.get("model")
        elif method == "http.request":
            details["url"] = params.get("url", "")
            details["http_method"] = params.get("method", "GET")
        elif method == "auth.register":
            details["module"] = module_id
        if response.get("status") == "error":
            err = response.get("error", {})
            details["error_code"] = err.get("code")
        duration_ms = int((time.monotonic() - started) * 1000)
        await self.audit.log(
            "ipc",
            module_id=module_id or "unknown",
            method=method,
            status=response.get("status", "error"),
            details=details,
            duration_ms=duration_ms,
        )

    async def _dispatch(self, session, req_id: str, method: str, params: dict[str, Any]) -> dict:
        perms = session.permissions
        module_id = session.module_id

        if method == "llm.generate":
            llm_perm = perms.get("llm", {})
            if not llm_perm.get("enabled"):
                raise PermissionError("PERMISSION_DENIED", "LLM not permitted")
            model = params.get("model")
            allowed_models = llm_perm.get("models", [])
            if model and allowed_models and model not in allowed_models:
                raise PermissionError("PERMISSION_DENIED", "Model not allowed")
            priority_name = llm_perm.get("priority", "module")
            priority = {
                "chat": Priority.CHAT,
                "module": Priority.MODULE,
                "research": Priority.RESEARCH,
            }.get(priority_name, Priority.MODULE)
            text = await self.llm.generate(
                params.get("prompt", ""),
                model=model,
                max_tokens=params.get("max_tokens", 1024),
                priority=priority,
            )
            return _response_ok(req_id, {"text": text})

        if method.startswith("db."):
            db_perm = perms.get("database", {})
            if not db_perm.get("enabled"):
                raise PermissionError("PERMISSION_DENIED", "DB not permitted")
            table = params.get("table")
            table_perms = {t["name"]: t["access"] for t in db_perm.get("tables", [])}
            if table not in table_perms:
                raise PermissionError("PERMISSION_DENIED", "Table not allowed")

            if method == "db.query":
                sql = params.get("query", "")
                rows = await self.db.query(table, sql, params.get("params", []), max_rows=db_perm.get("max_rows_per_query"))
                return _response_ok(req_id, {"rows": rows})
            if method == "db.insert":
                if table_perms[table] != "readwrite":
                    raise PermissionError("PERMISSION_DENIED", "Insert not allowed")
                row_id = await self.db.insert(table, params.get("data", {}))
                return _response_ok(req_id, {"row_id": row_id})
            if method == "db.update":
                if table_perms[table] != "readwrite":
                    raise PermissionError("PERMISSION_DENIED", "Update not allowed")
                count = await self.db.update(table, params.get("data", {}), params.get("where", "1=0"), params.get("params", []))
                return _response_ok(req_id, {"updated": count})

        if method == "http.request":
            http_perm = perms.get("http_outbound", {})
            if not http_perm.get("enabled"):
                raise PermissionError("PERMISSION_DENIED", "HTTP not permitted")
            url = params.get("url", "")
            allowed = http_perm.get("allowed_domains", [])
            if not self.http_proxy.validate_domain(url, allowed):
                raise PermissionError("PERMISSION_DENIED", "Domain not allowed")
            limit = http_perm.get("max_requests_per_minute", 60)
            if not self._check_http_rate(module_id, limit):
                raise PermissionError("RATE_LIMITED", "HTTP rate limit exceeded")
            result = await self.http_proxy.request(url, params.get("method", "GET"), params.get("headers", {}), params.get("body"))
            return _response_ok(req_id, result)

        if method == "audit.log":
            await self.audit.log("ipc", module_id=module_id, method=method, status="ok", details=params)
            return _response_ok(req_id, {"ok": True})

        if method == "health.pong":
            return _response_ok(req_id, {"ok": True})

        raise PermissionError("PERMISSION_DENIED", "Unknown or forbidden method")


async def start_ipc_server(socket_path: str, handler: IPCServer) -> asyncio.AbstractServer:
    import os
    import pathlib

    sock_path = pathlib.Path(socket_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        os.unlink(sock_path)

    server = await asyncio.start_unix_server(handler.handle_client, path=socket_path)
    logger.info("IPC server listening on %s", socket_path)
    return server
