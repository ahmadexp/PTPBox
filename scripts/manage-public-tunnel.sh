#!/usr/bin/env bash
# Publish the PTPBox Observatory through a Cloudflare Tunnel.
#
# PTPBox is not a dashboard. The same origin that serves the UI accepts
# /api/control, /api/servo/control, /api/fault/control and /api/holdover/control,
# so an unauthenticated public URL hands strangers the ability to stop the
# cascade, switch servos, release holdover and inject faults on real hardware.
#
# Every mode therefore refuses to start until the agent reports that access
# tokens are configured. With tokens in place a Quick Tunnel URL is useless on its
# own, which is what makes a link safe to hand to someone: the token decides what
# they may do, and a viewer token cannot reach a clock.
#
#   install       fetch cloudflared (needs root)
#   preflight     check the agent is protected, and say what is missing
#   quick-start   temporary trycloudflare.com URL, for an internal preview only
#   quick-url     print the URL of a running quick tunnel
#   token-start   named tunnel from CLOUDFLARE_TUNNEL_TOKEN, for customer use
#   status        what is running
#   stop          stop whatever is running
#
# Quick Tunnels are development-only: the hostname is random, changes on every
# restart, and Cloudflare offers no uptime guarantee. Use token-start with a named
# tunnel plus Cloudflare Access for anything a customer will see.

set -euo pipefail

SERVICE_URL="${PTPBOX_SERVICE_URL:-http://127.0.0.1:8090}"
PROBE_URL="${PTPBOX_PROBE_URL:-$SERVICE_URL}"
RUN_DIR="${PTPBOX_TUNNEL_RUN_DIR:-$HOME/.ptpbox-tunnel}"
LOG_FILE="$RUN_DIR/cloudflared.log"
PID_FILE="$RUN_DIR/cloudflared.pid"
CLOUDFLARED="${CLOUDFLARED_BIN:-$(command -v cloudflared || echo /usr/local/bin/cloudflared)}"

die() { printf '%s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; }

require_cloudflared() {
  [[ -x "$CLOUDFLARED" ]] || die "cloudflared is not installed. Run: sudo $0 install"
}

# --- the guard -----------------------------------------------------------------
# Refuses to publish an appliance that cannot tell one caller from another.
preflight() {
  local response status body

  # Capture status and body together. -f is deliberately not used: a 401 is the
  # single most informative answer here, because it proves the gate is live and
  # demanding a token. Treating it as a failure would refuse exactly the
  # configuration we want.
  response="$(curl -sS -m 10 -w $'\n%{http_code}' "$PROBE_URL/api/access" 2>/dev/null || true)"
  status="${response##*$'\n'}"
  body="${response%$'\n'*}"

  case "$status" in
    401|403)
      note "preflight: agent demands a token (HTTP $status), access control is live"
      return 0
      ;;
    200) : ;;
    "")
      die "Cannot reach $PROBE_URL/api/access. Is the agent running?"
      ;;
    *)
      die "$PROBE_URL/api/access returned HTTP $status; refusing to publish."
      ;;
  esac

  # A 200 from an old agent is the SPA fallback, which is HTML.
  case "$body" in
    '{'*) : ;;
    *) die "$PROBE_URL/api/access returned HTTP 200 but not JSON, so this agent
predates token access and has none. Deploy the current agent first:
  cd ~/ptpbox-main && git pull && sudo PTPBOX_USER=user bash scripts/install-host.sh" ;;
  esac

  case "$(printf '%s' "$body" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("unreadable"); raise SystemExit
print("protected" if d.get("tokens_configured") else "open")
' 2>/dev/null || echo unreadable)" in
    protected) note "preflight: agent reports access tokens configured" ;;
    open) die "The agent is running but NO access tokens are configured, so every
caller would be anonymous and could stop the cascade. Add tokens first:

  sudo python3 /opt/ptpbox-web/agent/ptpbox_agent.py --generate-token   # per person
  sudoedit /etc/ptpbox/tokens.json      # {\"operator\":[\"...\"],\"viewer\":[\"...\"]}
  sudo systemctl restart ptpbox-agent" ;;
    *) die "Could not interpret $PROBE_URL/api/access; refusing to publish." ;;
  esac

  if [[ "$PROBE_URL" == http://127.0.0.1:* ]]; then
    note "preflight: reminder, set PTPBOX_BIND=127.0.0.1 in the agent unit so the"
    note "           tunnel is the only route in, then restart ptpbox-agent"
  fi
}

running_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid; pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && printf '%s' "$pid"
}

start_tunnel() {
  local label="$1"; shift
  mkdir -p "$RUN_DIR"
  if running_pid >/dev/null; then
    die "A tunnel is already running (pid $(running_pid)). Run: $0 stop"
  fi
  : > "$LOG_FILE"
  nohup "$CLOUDFLARED" "$@" >>"$LOG_FILE" 2>&1 &
  printf '%s' "$!" > "$PID_FILE"
  note "$label started (pid $(cat "$PID_FILE")); log: $LOG_FILE"
}

case "${1:-}" in
  install)
    [[ "$(id -u)" -eq 0 ]] || die "install needs root: sudo $0 install"
    arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
    case "$arch" in
      amd64|x86_64) pkg=amd64 ;;
      arm64|aarch64) pkg=arm64 ;;
      *) die "unsupported architecture: $arch" ;;
    esac
    tmp="$(mktemp -d)"
    note "fetching cloudflared ($pkg) from Cloudflare"
    curl -fsSL -o "$tmp/cloudflared" \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$pkg"
    install -m 0755 "$tmp/cloudflared" /usr/local/bin/cloudflared
    rm -rf "$tmp"
    /usr/local/bin/cloudflared --version
    ;;

  preflight)
    preflight
    note "preflight: ready to publish $SERVICE_URL"
    ;;

  quick-start)
    require_cloudflared
    preflight
    note ""
    note "NOTE: a Quick Tunnel hostname is random, changes on restart, and carries"
    note "      no uptime guarantee. Internal preview only; use token-start plus"
    note "      Cloudflare Access for a customer."
    start_tunnel "quick tunnel" tunnel --no-autoupdate --url "$SERVICE_URL"
    note "waiting for the hostname..."
    for _ in $(seq 1 40); do
      url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" 2>/dev/null | head -1 || true)"
      [[ -n "$url" ]] && break
      sleep 0.5
    done
    [[ -n "${url:-}" ]] || die "no hostname appeared; inspect $LOG_FILE"
    note ""
    note "Preview URL: $url"
    note "Append a token to make it usable, e.g. $url/?token=<viewer-token>"
    ;;

  quick-url)
    grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" 2>/dev/null | head -1 \
      || die "no quick tunnel URL in $LOG_FILE"
    ;;

  token-start)
    require_cloudflared
    preflight
    [[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]] \
      || die "set CLOUDFLARE_TUNNEL_TOKEN to the named tunnel's token first"
    start_tunnel "named tunnel" tunnel --no-autoupdate run --token "$CLOUDFLARE_TUNNEL_TOKEN"
    note "Put a Cloudflare Access policy on the hostname before sharing it."
    ;;

  status)
    if pid="$(running_pid)"; then
      note "cloudflared running (pid $pid)"
      grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" 2>/dev/null | head -1 || true
    else
      note "cloudflared is not running"
    fi
    code="$(curl -sS -m 10 -o /tmp/.ptpbox-access -w '%{http_code}' "$PROBE_URL/api/access" 2>/dev/null || true)"
    case "$code" in
      401|403) note "agent access: protected (HTTP $code)" ;;
      200) python3 -c 'import json,sys; d=json.load(open("/tmp/.ptpbox-access")); print("agent access:", "protected" if d.get("tokens_configured") else "OPEN")' 2>/dev/null || note "agent access: OPEN (no access route)" ;;
      *) note "agent access: unknown (HTTP ${code:-none})" ;;
    esac
    rm -f /tmp/.ptpbox-access
    ;;

  stop)
    if pid="$(running_pid)"; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
      kill -9 "$pid" 2>/dev/null || true
      rm -f "$PID_FILE"
      note "tunnel stopped"
    else
      note "nothing to stop"
    fi
    ;;

  ""|-h|--help|help) usage ;;
  *) die "unknown command: $1 (try --help)" ;;
esac
