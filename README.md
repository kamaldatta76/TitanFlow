# TitanFlow

TitanFlow is an AI orchestration microkernel for running autonomous agents with memory, plugins, and safe tool access.

**Tagline:** Distributed intelligence. One organism.

## What You Get
- Model-agnostic LLM runtime (Ollama, OpenAI, Anthropic, any HTTP endpoint)
- Persistent memory (mem0) + optional grounding gate
- Plugin SDK: ToolPlugin, ModulePlugin, HookPlugin
- Safe tool loop with hard limits
- Telegram bot interface out of the box
- Configurable identity via `SOUL.md`
- Built-in health/ops surfaces

## Quick Start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config/titanflow.yaml ./config.local.yaml
export TITANFLOW_CONFIG=./config.local.yaml

python -m titanflow.main
```

## API
Once running (default `http://localhost:8800`):
- `GET /api/health` — health check
- `GET /api/status` — engine status
- `GET /api/modules` — modules list
- `GET /api/llm/health` — LLM connectivity
- `GET /api/jobs` — scheduled jobs

## Configuration
TitanFlow reads a YAML config file plus environment variables.
- Secrets use `${ENV_VAR}` and should be provided via environment or a secure service file.
- Example config: `config/titanflow.yaml`

## Plugins
Plugins are loaded from configured directories and can expose:
- Tools (LLM tool calls)
- Modules (background services)
- Hooks (event listeners)

## Safety
- Tool loop is bounded and rejects malformed calls.
- Optional grounding gate can refuse responses when no evidence exists.
- Secrets are never embedded in prompts.

## License
MIT
