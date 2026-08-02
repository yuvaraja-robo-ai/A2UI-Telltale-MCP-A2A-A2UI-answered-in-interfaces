#!/usr/bin/env bash
# Telltale's client: the start page, the host, the frame. Holds no data.
#   TELLTALE_CLIENT_ID=kiosk-2 ops/run_client.sh
source "$(dirname "$0")/_env.sh"
exec uv run --project "$APP" telltale-client
