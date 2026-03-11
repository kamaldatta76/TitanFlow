"""TitanOcta command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
from typing import Any

from .agent import TitanOctaAgentRuntime
from .installer import TitanOctaInstaller
from .local_governance import bootstrap_local_governance
from .management import run_management_server, start_management_server_detached
from .remote_token import RemoteAttachTokenManager
from .routing import TitanOctaRouter
from .tai import TAi
from .tier_guard import TierGuard

INSTALL_ROOT = Path(os.environ.get("TITANOCTA_INSTALL_ROOT", "~/.titanocta")).expanduser()
CONFIG_PATH = INSTALL_ROOT / "config.json"
DEFAULT_SECRET = "titanocta-dev-secret"
ASCII_LOGO = """\
  _______ _ _              ____      _        
 |__   __(_) |            / __ \\    | |       
    | |   _| |_ __ _ _ __| |  |  ___| |_ __ _ 
    | |  | | __/ _` | '__| |  | / __| __/ _` |
    | |  | | || (_| | |  | |__| \\__ \\ || (_| |
    |_|  |_|\\__\\__,_|_|   \\____/|___/\\__\\__,_|
"""


def _dist_version() -> str:
    for dist_name in ("titanocta", "titanflow"):
        try:
            return version(dist_name)
        except PackageNotFoundError:
            continue
    return "0.0.0-dev"


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


async def _ensure_installed() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return _load_config()
    installer = TitanOctaInstaller(install_root=str(INSTALL_ROOT))
    result = await installer.run()
    return result.to_dict()


async def _bootstrap_agent() -> tuple[TitanOctaAgentRuntime, dict[str, Any], Any]:
    config = await _ensure_installed()
    installer = TitanOctaInstaller(install_root=str(INSTALL_ROOT))
    if config.get("registration_status") != "registered" or (INSTALL_ROOT / "pending-registration.json").exists():
        config["registration_status"] = await installer.reconcile_constellation_registration(config)
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    start_management_server_detached(str(INSTALL_ROOT))
    runtime = await bootstrap_local_governance(str(INSTALL_ROOT))
    profile = config.get("profile", {})
    agent_name = config.get("agent_name", "Titan")
    agent = TitanOctaAgentRuntime(
        governance=runtime.governance,
        bus=runtime.bus,
        db=runtime.db,
        agent_id=agent_name.lower(),
        node_id=profile.get("hostname", "local"),
        tier_guard=TierGuard(config.get("tier", "free")),
        router=TitanOctaRouter(audit_log_path=str(INSTALL_ROOT / "routing-audit.jsonl")),
        tai=TAi(
            install_root=str(INSTALL_ROOT),
            tier=config.get("tier", "free"),
            current_model=config.get("active_model", "unknown"),
        ),
        model=config.get("active_model", "qwen2.5:7b"),
        provisioning_db_path=str(INSTALL_ROOT / "provisioning.sqlite"),
        provisioned_user_id=config.get("node_id"),
    )
    await agent.register_with_flow()
    return agent, config, runtime


async def _cmd_chat() -> int:
    try:
        agent, config, runtime = await _bootstrap_agent()
        print(ASCII_LOGO)
        print(
            f"Flow: GREEN  |  Agent: {config.get('agent_name', 'Titan')}  |  "
            f"Tier: {config.get('tier', 'free').upper()}  |  Model: {config.get('active_model', 'unknown')}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Flow: RED  |  {exc}")
        return 1

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                print()
                break
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break
            response = await agent.submit_user_message(user_input)
            print(f"ATLAS: {response}")
    finally:
        await runtime.db.close()
    return 0


async def _cmd_status() -> int:
    config = _load_config()
    if not config:
        print("Flow: RED  |  TitanOcta is not installed. Run the installer first.")
        return 1
    try:
        _agent, _config, runtime = await _bootstrap_agent()
        status = "GREEN"
        error = ""
    except Exception as exc:  # noqa: BLE001
        status = "RED"
        error = str(exc)
        runtime = None
    print(f"Version: {_dist_version()}")
    print(f"Flow: {status}")
    print(f"Tier: {config.get('tier', 'free').upper()}")
    print(f"Agent: {config.get('agent_name', 'Titan')}")
    print(f"Node: {config.get('node_id', 'unknown')}")
    print(f"Model: {config.get('active_model', 'unknown')}")
    print(f"Registration: {config.get('registration_status', 'pending')}")
    print(f"Attach: {config.get('attach_mode', 'local')}")
    print(f"Onboarding: {config.get('html_path', str(INSTALL_ROOT / 'onboarding.html'))}")
    if runtime is not None:
        await runtime.db.close()
    if error:
        print(f"Error: {error}")
        return 1
    return 0


def _cmd_attach() -> int:
    config = _load_config()
    secret = DEFAULT_SECRET
    if config:
        secret = config.get("attach_secret", DEFAULT_SECRET)
    token = input("Remote token: ").strip()
    if not token:
        print("No token provided.")
        return 1
    manager = RemoteAttachTokenManager(secret, audit_log_path=str(INSTALL_ROOT / "remote-token-audit.jsonl"))
    try:
        payload = manager.validate_token(token)
    except Exception as exc:  # noqa: BLE001
        print(f"Flow: RED  |  Attach failed: {exc}")
        return 1
    print(f"Flow: GREEN  |  Attach OK for {payload['subject']} on {payload['node_id']}")
    return 0


def _cmd_version() -> int:
    print(_dist_version())
    return 0


def _tai_for_config(config: dict[str, Any]) -> TAi:
    return TAi(
        install_root=str(INSTALL_ROOT),
        tier=config.get("tier", "free"),
        current_model=config.get("active_model", "unknown"),
    )


def _cmd_tai_check() -> int:
    config = _load_config()
    if not config:
        print("Flow: RED  |  TitanOcta is not installed. Run the installer first.")
        return 1
    tai = _tai_for_config(config)
    suggestion = tai.suggest()
    if suggestion is None:
        state = tai.status()
        print(f"TAi manual check complete. Score: {state.last_score:.2f}. No suggestion right now.")
        return 0
    print(suggestion)
    return 0


def _cmd_tai_status() -> int:
    config = _load_config()
    if not config:
        print("Flow: RED  |  TitanOcta is not installed. Run the installer first.")
        return 1
    state = _tai_for_config(config).status()
    print(f"Mode: {state.mode}")
    print(f"Model: {state.current_model}")
    print(f"Last score: {state.last_score:.2f}")
    print(f"Cooldown until: {state.cooldown_until or 'inactive'}")
    print(f"Last signal: {state.last_signal or 'none'}")
    print(f"Suggestion: {state.current_suggestion or 'none'}")
    return 0


def _cmd_tai_ignore() -> int:
    config = _load_config()
    if not config:
        print("Flow: RED  |  TitanOcta is not installed. Run the installer first.")
        return 1
    state = _tai_for_config(config).ignore()
    print(f"TAi suggestion dismissed. Cooldown until {state.cooldown_until}.")
    return 0


def _cmd_tai_auto() -> int:
    print("Auto mode requires TitanOcta Pro. titanocta.com")
    return 0


def _cmd_serve(host: str, port: int, install_root: str | None = None) -> int:
    root = install_root or str(INSTALL_ROOT)
    run_management_server(root, host=host, port=port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="titanocta")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("chat")
    sub.add_parser("status")
    sub.add_parser("attach")
    sub.add_parser("version")
    tai_parser = sub.add_parser("tai")
    tai_sub = tai_parser.add_subparsers(dest="tai_command", required=True)
    tai_sub.add_parser("check")
    tai_sub.add_parser("status")
    tai_sub.add_parser("ignore")
    tai_sub.add_parser("auto")
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--install-root", default=None)
    args = parser.parse_args(argv)

    if args.command == "chat":
        return asyncio.run(_cmd_chat())
    if args.command == "status":
        return asyncio.run(_cmd_status())
    if args.command == "attach":
        return _cmd_attach()
    if args.command == "version":
        return _cmd_version()
    if args.command == "tai":
        if args.tai_command == "check":
            return _cmd_tai_check()
        if args.tai_command == "status":
            return _cmd_tai_status()
        if args.tai_command == "ignore":
            return _cmd_tai_ignore()
        if args.tai_command == "auto":
            return _cmd_tai_auto()
    if args.command == "serve":
        return _cmd_serve(args.host, args.port, args.install_root)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
