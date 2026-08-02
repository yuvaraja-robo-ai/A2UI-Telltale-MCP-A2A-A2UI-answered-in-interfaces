#!/usr/bin/env bash
# Everything, in dependency order, in the background.
#
#   ops/up.sh            # then open http://127.0.0.1:8115
#   ops/down.sh          # stop it all
#
# The framework and the model gateway are only needed for the model-composed
# turns. The deterministic dashboard runs on the API and client alone.
source "$(dirname "$0")/_env.sh"
LOGS="$ROOT/.runtime/logs"; mkdir -p "$LOGS"

start() {  # start <name> <script> <healthurl>
  local name="$1" script="$2" url="$3"
  if [ -n "$(port_pid "${4:-0}")" ]; then echo "$name already listening"; return; fi
  setsid "$(dirname "$0")/$script" > "$LOGS/$name.log" 2>&1 &
  wait_for "$url" "$name" 60 || { echo "--- $name log ---"; tail -20 "$LOGS/$name.log"; return 1; }
}

start gateway   run_gateway.sh   "http://127.0.0.1:$TELLTALE_GATEWAY_PORT/healthz"      "$TELLTALE_GATEWAY_PORT"
start framework run_framework.sh "http://127.0.0.1:$S14CODE_PORT/healthz"               "$S14CODE_PORT"
start api       run_server.sh    "http://127.0.0.1:$TELLTALE_API_PORT/docs"             "$TELLTALE_API_PORT"
start client    run_client.sh    "http://127.0.0.1:$TELLTALE_CLIENT_PORT/"              "$TELLTALE_CLIENT_PORT"

echo
echo "open  http://127.0.0.1:$TELLTALE_CLIENT_PORT"
echo "logs  $LOGS"
