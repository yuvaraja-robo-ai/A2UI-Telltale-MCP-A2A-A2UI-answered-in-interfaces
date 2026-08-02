# Shared settings for every runner. Source this; do not run it.
#
# Ports, in the order things start:
#   8112  local model gateway (GLC contract) -> Ollama
#   8113  S14Code, the framework (HTTP)
#   8114  S14Code's A2A gRPC listener — the framework takes this one itself,
#         so nothing else may claim it (S13_A2A_GRPC_PORT)
#   8120  Telltale API
#   8121  Telltale client
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRAMEWORK="$ROOT/S14Code"
APP="$ROOT/telltale"

export TELLTALE_OLLAMA="${TELLTALE_OLLAMA:-http://192.168.32.2:11434}"
export TELLTALE_OLLAMA_MODEL="${TELLTALE_OLLAMA_MODEL:-gemma4:latest}"
export TELLTALE_GATEWAY_PORT="${TELLTALE_GATEWAY_PORT:-8112}"
export S14CODE_PORT="${S14CODE_PORT:-8113}"
export TELLTALE_API_PORT="${TELLTALE_API_PORT:-8120}"
export TELLTALE_CLIENT_PORT="${TELLTALE_CLIENT_PORT:-8121}"
export TELLTALE_API="${TELLTALE_API:-http://127.0.0.1:$TELLTALE_API_PORT}"
export TELLTALE_S14CODE="${TELLTALE_S14CODE:-http://127.0.0.1:$S14CODE_PORT}"

# The framework's only seam to a model: it posts to {GLC_BASE_URL}/v1/chat.
export GLC_BASE_URL="${GLC_BASE_URL:-http://127.0.0.1:$TELLTALE_GATEWAY_PORT}"
export S13_GATEWAY_PROVIDER="${S13_GATEWAY_PROVIDER:-ollama}"
export S13_DATA_DIR="${S13_DATA_DIR:-$ROOT/.runtime/s13data}"
mkdir -p "$S13_DATA_DIR"

# Always succeeds: an empty answer means nothing is listening, which is a fact,
# not an error. Without the `|| true` the failing grep trips `set -e` and takes
# the whole script with it — down.sh died at the first free port and stopped
# nothing, while reporting nothing.
port_pid() { ss -lptn "sport = :$1" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1 || true; }
wait_for() {  # wait_for <url> <name> [seconds]
  local url="$1" name="$2" limit="${3:-30}" i=0
  until curl -sf -m 2 "$url" >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -ge "$limit" ] && { echo "$name did not come up at $url"; return 1; }
    sleep 1
  done
  echo "$name up: $url"
}
