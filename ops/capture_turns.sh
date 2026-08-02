#!/usr/bin/env bash
# Three model-composed turns, end to end. Needs the gateway and the framework up:
#   ops/up.sh && ops/capture_turns.sh        -> proofs/turns/
source "$(dirname "$0")/_env.sh"
cd "$ROOT"
exec uv run --project "$APP" python ops/capture_turns.py "$@"
