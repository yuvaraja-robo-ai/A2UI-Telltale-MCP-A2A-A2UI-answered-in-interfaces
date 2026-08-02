#!/usr/bin/env bash
# Both suites, framework first.
#
#   ops/test.sh                 # everything
#   ops/test.sh app -k gauge    # arguments after a repo name go to pytest
#
# The SocketCAN tests skip without a virtual bus:
#   sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0
source "$(dirname "$0")/_env.sh"
which="${1:-all}"; [ $# -gt 0 ] && shift || true

# cd into the repository, not just --project it: pytest collects from the
# working directory, so running from the project root would sweep up the OTHER
# repository's tests and fail on imports it was never meant to see.
suite() { ( cd "$1" && uv run pytest -q "${@:2}" ); }

# Both suites drive a real Chromium. Back to back on a machine that is also
# holding a language model and a desktop browser, the second one starts with
# very little memory and its iframe tests time out — a failure that says
# nothing about the code. Warn rather than guess, and let the previous
# browser's pages be reclaimed before starting the next.
settle() {
  sleep 5
  local free_mb; free_mb="$(free -m | awk '/^Mem:/{print $7}')"
  if [ "${free_mb:-9999}" -lt 1200 ]; then
    echo "  note: ${free_mb}MB available — browser tests may time out."
    echo "  they pass alone; close other browsers or stop the model to be sure."
  fi
}

case "$which" in
  framework) suite "$FRAMEWORK" "$@" ;;
  app)       suite "$APP" "$@" ;;
  all)
    # Both suites run whatever the first one does. Aborting on the first
    # failure hides the state of the other repository, which is exactly the
    # thing you wanted to know before deciding anything.
    rc=0
    echo "=== S14Code ==="
    suite "$FRAMEWORK" || rc=1
    settle
    echo; echo "=== telltale ==="
    suite "$APP" || rc=1
    exit $rc ;;
  *) echo "usage: ops/test.sh [all|framework|app] [pytest args]"; exit 2 ;;
esac
