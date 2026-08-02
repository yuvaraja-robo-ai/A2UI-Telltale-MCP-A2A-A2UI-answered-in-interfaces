#!/usr/bin/env bash
# The submission rule, as a gate rather than a promise, across BOTH repositories:
#   "Secrets, .env, credentials, personal memory, and unrestricted paths never
#    enter the pull request. Synthetic identities are mandatory."
#
#   ops/check_no_secrets.sh          # exits non-zero on any finding
source "$(dirname "$0")/_env.sh"
fail=0

scan() {
  local repo="$1" name="$2"
  echo "=== $name ==="
  local found=0
  report(){ echo "  FAIL: $1"; found=1; fail=1; }

  git -C "$repo" ls-files | grep -E '(^|/)\.env($|\.)' | grep -v '\.env\.example$' \
    && report "a .env file is tracked"

  git -C "$repo" grep -nIE '/(home|Users)/[a-z0-9_.-]+' -- . ':!*.lock' \
    && report "an absolute home path is tracked"

  git -C "$repo" grep -nIE '(api[_-]?key|secret|token|password|passwd|bearer)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][A-Za-z0-9/+_-]{16,}' \
    -- . ':!*.lock' && report "a credential-shaped assignment is tracked"

  git -C "$repo" grep -nIE 'AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}' -- . \
    && report "a provider key is tracked"

  # A VIN is 17 characters and identifies one physical vehicle. The bench rig is
  # synthetic and must stay that way.
  git -C "$repo" grep -nIE '\b[A-HJ-NPR-Z0-9]{17}\b' -- '*.dbc' '*.py' \
    && report "something VIN-shaped is tracked"

  [ $found -eq 0 ] && echo "  clean"
}

scan "$FRAMEWORK" "S14Code (the pull request)"
scan "$APP" "telltale (the application)"
exit $fail
