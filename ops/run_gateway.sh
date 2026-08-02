#!/usr/bin/env bash
# The local model gateway. Speaks GLC's contract, forwards to Ollama.
#   ops/run_gateway.sh
source "$(dirname "$0")/_env.sh"
exec uv run --project "$APP" telltale-gateway
