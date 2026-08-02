#!/usr/bin/env bash
# Record the end-to-end demo. Starts its own servers; nothing needs to be up.
#   ops/record_demo.sh                      -> demo/telltale-embed-demo.mp4
#   ops/record_demo.sh --out demo/other.mp4
source "$(dirname "$0")/_env.sh"
cd "$ROOT"
exec uv run --project "$APP" python ops/record_demo.py "$@"
