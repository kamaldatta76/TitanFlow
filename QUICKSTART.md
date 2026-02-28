# TitanFlow Quickstart

## Local Run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config/titanflow.yaml ./config.local.yaml
export TITANFLOW_CONFIG=./config.local.yaml

python -m titanflow.main
```

## Notes
- Secrets should be provided via environment variables, not committed files.
- Default API listens on `http://localhost:8800`.
