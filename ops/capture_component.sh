#!/usr/bin/env bash
# The unprompted-composition capture for the pull request: a model is offered
# the catalog and a data model, is never told which component to use, and what
# it reaches for is recorded.
#
#   ops/capture_component.sh                 -> proofs/composition/
#
# Run with S14Code's environment because it imports the framework's own prompt
# builders — the point is to send exactly what ships — but nothing is written
# into the fork. The pull request carries the result in its description; a
# general-purpose framework should not carry one machine's errand.
source "$(dirname "$0")/_env.sh"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-$TELLTALE_OLLAMA}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-$TELLTALE_OLLAMA_MODEL}"
cd "$ROOT"
exec uv run --project "$FRAMEWORK" python ops/capture_composition.py "$@"
