#!/usr/bin/env bash
# Register the EverestQuant "eiq" MCP server into Claude Code (or any agent that
# reads ~/.claude.json), so your agent can drive the tournament tools directly.
#
#   curl -sL https://everesteer.ai/install-claude-mcp.sh | bash
#
# Closed beta: paste onboarding's "Copy setup command" first (it exports your
# EIQ_API_KEY + Cloudflare Access service token); this script reads those from
# the environment and prompts via the terminal for anything missing.
set -euo pipefail

NAME="eiq"
BASE_URL="${EIQ_BASE_URL:-https://staging.everesteer.ai}"

say() { printf '%s\n' "$*" >&2; }

# 1. Tooling.
command -v claude >/dev/null 2>&1 || {
  say "Claude Code CLI not found — install it first: https://docs.claude.com/claude-code"
  exit 1
}
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { say "Python 3 not found."; exit 1; }

# 2. SDK + MCP extra (no-op if already importable).
if ! "$PY" -c "import everestapi.mcp" >/dev/null 2>&1; then
  say "Installing everestapi[mcp]…"
  "$PY" -m pip install --quiet "everestapi[mcp]>=0.2.1"
fi

# 3. Credentials: take from env, prompt for missing (secrets never echoed).
# Under `curl | bash`, stdin is the script — read from the terminal explicitly.
need() {  # need VAR_NAME "Prompt label"
  local var="$1" label="$2" val
  eval "val=\${$var:-}"
  if [ -z "$val" ]; then
    [ -e /dev/tty ] || { say "Set \$$var (paste onboarding's 'Copy setup command') and re-run."; exit 1; }
    printf '%s: ' "$label" >&2
    read -rs val </dev/tty; say ""
    [ -n "$val" ] || { say "Empty $var — aborting."; exit 1; }
    eval "$var=\$val"
  fi
}

need EIQ_API_KEY "EIQ API key (eiq_…)"
ENV_ARGS=( --env "EIQ_API_KEY=$EIQ_API_KEY" --env "EIQ_BASE_URL=$BASE_URL" )

# Staging sits behind Cloudflare Access — the service token is required there.
case "$BASE_URL" in
  *staging*)
    need CF_ACCESS_CLIENT_ID "Cloudflare Access Client ID"
    need CF_ACCESS_CLIENT_SECRET "Cloudflare Access Client Secret"
    ENV_ARGS+=( --env "CF_ACCESS_CLIENT_ID=$CF_ACCESS_CLIENT_ID" \
                --env "CF_ACCESS_CLIENT_SECRET=$CF_ACCESS_CLIENT_SECRET" )
    ;;
esac

# 4. Register, idempotently (re-running updates the entry).
claude mcp remove "$NAME" --scope user >/dev/null 2>&1 || true
claude mcp add "$NAME" --scope user "${ENV_ARGS[@]}" -- "$PY" -m everestapi.mcp

say ""
say "✅ '$NAME' MCP registered (user scope, base $BASE_URL)."
say "   Restart Claude Code, then ask it to use the EIQ tools."
say "   Research skills load from himalayas/agents/skills/ (see AGENTS.md)."
