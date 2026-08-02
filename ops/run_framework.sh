#!/usr/bin/env bash
# S14Code, the framework, pointed at whatever gateway GLC_BASE_URL names.
#   ops/run_gateway.sh   must already be running (or set GLC_BASE_URL yourself)
source "$(dirname "$0")/_env.sh"
exec uv run --project "$FRAMEWORK" uvicorn s13code.main:app \
  --host 127.0.0.1 --port "$S14CODE_PORT" --log-level warning
