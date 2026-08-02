#!/usr/bin/env bash
# Telltale's API: the bus, composition, the gate. Serves no HTML.
source "$(dirname "$0")/_env.sh"
exec uv run --project "$APP" telltale-server
