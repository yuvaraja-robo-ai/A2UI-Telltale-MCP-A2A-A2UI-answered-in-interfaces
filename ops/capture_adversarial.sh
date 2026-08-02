#!/usr/bin/env bash
# The attacks, before the fix and after it. Needs the gateway and the framework:
#   ops/up.sh && ops/capture_adversarial.sh    -> proofs/adversarial/
source "$(dirname "$0")/_env.sh"
cd "$ROOT"
exec uv run --project "$APP" python ops/capture_adversarial.py "$@"
