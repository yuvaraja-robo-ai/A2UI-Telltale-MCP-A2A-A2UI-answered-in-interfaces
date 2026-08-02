#!/usr/bin/env bash
# Stop whatever ops/up.sh started, by port rather than by name — a pattern kill
# is one typo away from taking this shell with it.
source "$(dirname "$0")/_env.sh"
# `uv run` wraps the server in a child process, so killing the pid that holds
# the port can leave the wrapper's child still bound. Re-check and kill again a
# couple of times rather than reporting a stop that did not happen.
for port in "$TELLTALE_CLIENT_PORT" "$TELLTALE_API_PORT" "$S14CODE_PORT" "$TELLTALE_GATEWAY_PORT"; do
  stopped=""
  for _ in 1 2 3; do
    pid="$(port_pid "$port")"
    [ -z "$pid" ] && break
    kill "$pid" 2>/dev/null || true
    stopped="$pid"
    sleep 1
  done
  if [ -n "$(port_pid "$port")" ]; then
    echo "still listening on :$port — check it by hand"
  elif [ -n "$stopped" ]; then
    echo "stopped :$port"
  fi
done
