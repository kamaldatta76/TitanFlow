# Contributing to TitanFlow

Thanks for your interest in improving TitanFlow. This project is designed to be safe, observable, and production‑friendly.

## Ground Rules
- Keep changes small and reviewable.
- Never commit secrets or real personal identifiers.
- Avoid hard‑coding IPs or private infrastructure details.
- Prefer configuration over code changes for deployment settings.

## Development
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Submitting Changes
1. Open a PR with a clear description and test notes.
2. Include updates to docs when behavior changes.
3. Keep logs and telemetry wiring compatible with the existing schema.

## Security
If you find a security issue, open a private issue or contact the maintainers.

Thank you for helping make TitanFlow reliable and safe.
