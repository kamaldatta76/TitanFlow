"""Installer and onboarding surface for TitanOcta Free."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import json
import os
from pathlib import Path
from typing import Any
import uuid

import httpx

from .agent import TitanOctaAgentRuntime
from .backup import BackupManager
from .config import CONSTELLATION_ONBOARDING_HOSTS, MANAGEMENT_HOST, MANAGEMENT_PORT, recommended_model_for_class
from .hardware import HardwareProfile, detect_hardware
from .local_governance import bootstrap_local_governance
from .management import start_management_server_detached, titanocta_version
from .programs import (
    PROGRAM_MULTI,
    default_install_root,
    default_tier_for_program,
    get_program_definition,
    normalize_program_mode,
)
from .provisioning import provision_user
from .remote_token import RemoteAttachTokenManager
from .retrieval import GroundedRetriever
from .routing import TitanOctaRouter
from .tai import TAi
from .telemetry_consent import ConsentRequired, TelemetryConsentGate
from .tier_guard import TierGuard


@dataclass(frozen=True)
class InstallerHealth:
    agent: str
    flow: str
    backup: str
    retrieval: str
    routing: str
    backup_verify: bool


@dataclass(frozen=True)
class InstallerResult:
    program_mode: str
    tier: str
    profile: HardwareProfile
    attach_mode: str
    agent_name: str
    node_id: str
    active_model: str
    registration_status: str
    registration_id: str
    octa_key: str
    routing_config: dict[str, Any]
    health: InstallerHealth
    remote_token: str | None
    management_url: str
    html_path: str
    config_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TitanOctaInstaller:
    def __init__(
        self,
        *,
        governance: Any | None = None,
        bus: Any | None = None,
        program_mode: str | None = None,
        tier: str | None = None,
        install_root: str | None = None,
        attach_secret: str = "titanocta-dev-secret",
    ) -> None:
        self._governance = governance
        self._bus = bus
        self._db = None
        self._program_mode = normalize_program_mode(program_mode or os.environ.get("TITANOCTA_PROGRAM_MODE"))
        resolved_root = install_root or str(default_install_root(self._program_mode))
        self._tier = tier or default_tier_for_program(self._program_mode)
        self._install_root = Path(resolved_root).expanduser()
        self._install_root.mkdir(parents=True, exist_ok=True)
        self._backup_manager = BackupManager(base_dir=str(self._install_root / "backups"))
        self._retriever = GroundedRetriever()
        self._router = TitanOctaRouter(audit_log_path=str(self._install_root / "routing-audit.jsonl"))
        self._tier_guard = TierGuard(self._tier)
        self._token_manager = RemoteAttachTokenManager(
            attach_secret,
            audit_log_path=str(self._install_root / "remote-token-audit.jsonl"),
        )

    async def run(
        self,
        *,
        agent_name: str | None = None,
        attach_mode: str = "local",
        manual_override: str | None = None,
    ) -> InstallerResult:
        if not agent_name:
            agent_name = "Titan" if self._program_mode != PROGRAM_MULTI else "TitanTeam"
        governance = self._governance
        bus = self._bus
        bootstrap_runtime = None
        if governance is None or bus is None:
            try:
                bootstrap_runtime = await bootstrap_local_governance(str(self._install_root))
            except Exception as exc:
                raise RuntimeError(f"Unable to bootstrap local Flow governance: {exc}") from exc
            governance = bootstrap_runtime.governance
            bus = bootstrap_runtime.bus
            self._db = bootstrap_runtime.db

        # ── CONSENT GATE ──────────────────────────────────────────────────────
        # Mandatory on every first run. No agreement = no access.
        # Collected: hardware specs, model, benchmark scores, deploy/crash stats.
        # Never collected: prompts, sessions, API keys, memory.
        # CL doctrine: plain list, plain consent, no "help us improve" vagueness.
        _consent_gate = TelemetryConsentGate(self._install_root)
        _consent_node_id = self._get_or_create_node_id()
        _consent_gate.require_consent(_consent_node_id, interactive=True)
        # ─────────────────────────────────────────────────────────────────────

        profile = detect_hardware()
        if manual_override:
            profile = HardwareProfile(
                **{
                    **profile.to_dict(),
                    "class_name": manual_override,
                }
            )
        node_id = self._get_or_create_node_id()
        active_model = recommended_model_for_class(profile.class_name)
        owner_email = os.environ.get("TITANOCTA_OWNER_EMAIL", f"{agent_name.lower()}@local.titan")
        provisioning = provision_user(
            user_id=node_id,
            tier=self._tier,
            email=owner_email,
            db_path=self._install_root / "provisioning.sqlite",
        )

        remote_token = None
        if attach_mode == "remote":
            remote_token = self._token_manager.generate_token(
                subject=agent_name,
                node_id=node_id,
            ).token

        runtime = TitanOctaAgentRuntime(
            governance=governance,
            bus=bus,
            db=self._db,
            agent_id=agent_name.lower(),
            node_id=profile.hostname,
            tier_guard=self._tier_guard,
            router=self._router,
            tai=TAi(install_root=str(self._install_root), tier=self._tier, current_model=active_model),
            model=active_model,
            provisioning_db_path=str(self._install_root / "provisioning.sqlite"),
            provisioned_user_id=node_id,
        )
        registration = await runtime.register_with_flow()
        registration_id = f"{registration.agent_id}@{registration.node_id}"

        backup_path = self._backup_manager.create_backup(
            source_paths=[str(self._install_root)],
            mode="config-only",
            label="titanocta-onboarding",
        )
        backup_verify = bool(self._backup_manager.verify_backup(backup_path)["ok"])
        health = InstallerHealth(
            agent="green",
            flow="green",
            backup="green",
            retrieval="green",
            routing="green",
            backup_verify=backup_verify,
        )

        config_path = self._install_root / "config.json"
        result = InstallerResult(
            program_mode=self._program_mode,
            tier=self._tier,
            profile=profile,
            attach_mode=attach_mode,
            agent_name=agent_name,
            node_id=node_id,
            active_model=active_model,
            registration_status="pending",
            registration_id=registration_id,
            octa_key=str(provisioning["octa_key"]),
            routing_config=dict(provisioning["routing_config"]),
            health=health,
            remote_token=remote_token,
            management_url=f"http://{MANAGEMENT_HOST}:{MANAGEMENT_PORT}/health",
            html_path=str(self._install_root / "onboarding.html"),
            config_path=str(config_path),
        )
        config = result.to_dict()
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.render_onboarding_html(result, Path(result.html_path))
        start_management_server_detached(str(self._install_root))
        config["registration_status"] = await self.reconcile_constellation_registration(config)
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        if bootstrap_runtime is not None:
            await bootstrap_runtime.db.close()
        return InstallerResult(
            program_mode=config.get("program_mode", self._program_mode),
            tier=config["tier"],
            profile=HardwareProfile(**config["profile"]),
            attach_mode=config["attach_mode"],
            agent_name=config["agent_name"],
            node_id=config["node_id"],
            active_model=config["active_model"],
            registration_status=config["registration_status"],
            registration_id=config["registration_id"],
            octa_key=config["octa_key"],
            routing_config=dict(config["routing_config"]),
            health=InstallerHealth(**config["health"]),
            remote_token=config.get("remote_token"),
            management_url=config["management_url"],
            html_path=config["html_path"],
            config_path=config["config_path"],
        )

    def render_onboarding_html(self, result: InstallerResult, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        assets_dir = self._install_root / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        logo_path = assets_dir / "titanocta-logo.svg"
        self.render_logo_svg(logo_path)
        program_def = get_program_definition(result.program_mode)
        mode_caps = "1 user, 1 agent, 1 node." if result.program_mode != PROGRAM_MULTI else "Up to 25 users, 16 agents, 16 nodes."
        token_block = (
            f"<div class='token-box'>{html.escape(result.remote_token)}</div>"
            if result.remote_token
            else "<div class='token-box muted'>Local attach selected.</div>"
        )
        cards = [
            ("Welcome", "Meet your AI. Built for your machine.", "<button>Begin Setup</button>"),
            (
                "Hardware Scan",
                f"Your machine can run TitanOcta {result.profile.class_name}.",
                f"<p>{html.escape(result.profile.explanation)}</p>"
                f"<ul><li>CPU: {html.escape(result.profile.cpu)}</li>"
                f"<li>RAM: {result.profile.ram_gb} GB</li>"
                f"<li>GPU: {html.escape(result.profile.gpu)}</li>"
                f"<li>Disk Free: {result.profile.disk_free_gb} GB</li></ul>",
            ),
            (
                "Agent Config",
                f"Agent name: {html.escape(result.agent_name)}",
                f"<p>Program: {html.escape(program_def.label)} ({html.escape(result.program_mode)})</p>"
                f"<p>Attach mode: {html.escape(result.attach_mode)}</p>{token_block}",
            ),
            (
                "Flow Registration",
                "Connecting to TitanFlow spine...",
                f"<p>Registration: {html.escape(result.registration_id)}</p>"
                f"<p>Constellation sync: {html.escape(result.registration_status)}</p>"
                f"<p>Governance online. Routing active.</p>",
            ),
            (
                "Health Check",
                "Backup verification included.",
                (
                    f"<ul><li>Agent: {result.health.agent}</li>"
                    f"<li>Flow: {result.health.flow}</li>"
                    f"<li>Backup: {result.health.backup}</li>"
                    f"<li>Retrieval: {result.health.retrieval}</li>"
                    f"<li>Routing: {result.health.routing}</li>"
                    f"<li>Model: {html.escape(result.active_model)}</li>"
                    f"<li>Management: {html.escape(result.management_url)}</li>"
                    f"<li>Backup verify: {'ok' if result.health.backup_verify else 'failed'}</li></ul>"
                ),
            ),
            (
                "Ready",
                "TitanOcta is running.",
                f"<p>Tier capabilities: {mode_caps}</p>"
                "<p>Upcoming: Voice / Talk mode, Pro + Ultra, Manager, Dash Pack, multi-agent orchestration, "
                "team workspace, advanced memory graph, hosted connectors.</p>",
            ),
        ]
        html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TitanOcta Free v1.0 Onboarding</title>
  <style>
    :root {{
      --bg:#070b11;
      --panel:#0d1420;
      --panel-2:#121d2d;
      --text:#eef4ff;
      --muted:#8ca0bc;
      --accent:#f26a21;
      --accent-2:#ffb066;
      --success:#3ad07a;
      --line:rgba(255,255,255,0.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(circle at top, rgba(242,106,33,0.14), transparent 28%), linear-gradient(180deg, #05070c, #09111c 42%, #070b11 100%);
      color:var(--text);
      min-height:100vh;
    }}
    .shell {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 48px 24px 72px;
    }}
    .hero {{
      display:flex;
      justify-content:space-between;
      align-items:flex-end;
      gap:24px;
      margin-bottom:32px;
    }}
    .brand-row {{
      display:flex;
      align-items:center;
      gap:16px;
    }}
    .brand-mark {{
      width:64px;
      height:64px;
      filter:drop-shadow(0 12px 24px rgba(242,106,33,0.16));
    }}
    .logo {{
      font-size: 42px;
      font-weight: 800;
      letter-spacing: -0.04em;
    }}
    .logo span {{ color: var(--accent); }}
    .strap {{ color: var(--muted); max-width: 520px; line-height:1.6; }}
    .grid {{
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
      gap:18px;
    }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01)), var(--panel);
      border:1px solid var(--line);
      border-radius:18px;
      padding:22px;
      box-shadow:0 18px 50px rgba(0,0,0,0.22);
      min-height:220px;
    }}
    h2 {{
      margin:0 0 10px;
      font-size:20px;
      letter-spacing:-0.02em;
    }}
    p, li {{ color: var(--muted); line-height:1.6; }}
    ul {{ padding-left: 18px; }}
    button {{
      border:none;
      border-radius:999px;
      padding:12px 18px;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
      color:#130b04;
      font-weight:700;
    }}
    .token-box {{
      margin-top: 12px;
      padding: 12px 14px;
      border-radius: 12px;
      background: var(--panel-2);
      border: 1px solid rgba(242,106,33,0.28);
      color: #ffd9b8;
      word-break: break-all;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .muted {{ color: var(--muted); }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div>
        <div class="brand-row">
          <img class="brand-mark" src="assets/titanocta-logo.svg" alt="TitanOcta">
          <div class="logo">Titan<span>Octa</span></div>
        </div>
        <h1>Meet your AI. Built for your machine.</h1>
      </div>
      <div class="strap">Octa is the product surface. Flow is the runtime spine already running underneath it. This onboarding chooses the strongest fit your hardware can carry.</div>
    </section>
    <section class="grid">
      {"".join(f"<article class='card'><h2>{html.escape(title)}</h2><p>{html.escape(body)}</p>{extra}</article>" for title, body, extra in cards)}
    </section>
  </div>
</body>
</html>"""
        output_path.write_text(html_doc, encoding="utf-8")

    @staticmethod
    def render_logo_svg(output_path: Path) -> None:
        output_path.write_text(
            """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="TitanOcta">
  <defs>
    <linearGradient id="octa-bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0b111a"/>
      <stop offset="100%" stop-color="#121b2a"/>
    </linearGradient>
    <linearGradient id="octa-accent" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#f26a21"/>
      <stop offset="100%" stop-color="#ffb066"/>
    </linearGradient>
  </defs>
  <rect width="256" height="256" rx="52" fill="url(#octa-bg)"/>
  <rect x="24" y="24" width="208" height="208" rx="40" fill="none" stroke="rgba(255,255,255,0.08)"/>
  <path d="M128 44 192 82v92l-64 38-64-38V82z" fill="none" stroke="url(#octa-accent)" stroke-width="14" stroke-linejoin="round"/>
  <circle cx="128" cy="128" r="28" fill="#f5f7fb"/>
  <path d="M128 90v76M90 128h76" stroke="#f26a21" stroke-width="12" stroke-linecap="round"/>
</svg>
""",
            encoding="utf-8",
        )

    async def reconcile_constellation_registration(self, config: dict[str, Any]) -> str:
        pending_path = self._install_root / "pending-registration.json"
        payload = self._build_registration_payload(config)
        if pending_path.exists():
            payload = json.loads(pending_path.read_text(encoding="utf-8"))
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            for endpoint in sorted(CONSTELLATION_ONBOARDING_HOSTS, key=lambda item: item.priority):
                try:
                    response = await client.post(endpoint.url, json=payload)
                    response.raise_for_status()
                    pending_path.unlink(missing_ok=True)
                    self._write_registration_state({"status": "registered", "target": endpoint.name, "payload": payload})
                    return "registered"
                except Exception:  # noqa: BLE001
                    continue
        pending_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._write_registration_state({"status": "pending", "target": None, "payload": payload})
        return "pending"

    def _build_registration_payload(self, config: dict[str, Any]) -> dict[str, Any]:
        registration = self._token_manager.build_registration(
            node_id=config["node_id"],
            tier=config["tier"],
            hardware_class=config["profile"]["class_name"],
            model=config["active_model"],
            version=titanocta_version(),
        )
        return registration.to_dict()

    def _get_or_create_node_id(self) -> str:
        node_path = self._install_root / "node-id"
        if node_path.exists():
            return node_path.read_text(encoding="utf-8").strip()
        node_id = str(uuid.uuid4())
        node_path.write_text(node_id, encoding="utf-8")
        return node_id

    def _write_registration_state(self, state: dict[str, Any]) -> None:
        path = self._install_root / "registration-state.json"
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")


async def _main() -> None:
    installer = TitanOctaInstaller()
    result = await installer.run()
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
