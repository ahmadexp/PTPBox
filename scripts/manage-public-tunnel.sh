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

# Probes with python3 rather than curl. The agent is written in python3 so the
# interpreter is always present, whereas a minimal appliance image may ship no
# curl at all, which silently turned every probe into "cannot reach".
probe_access() {
  python3 - "$PROBE_URL" <<'PYPROBE'
import json, sys, urllib.error, urllib.request
url = sys.argv[1].rstrip("/") + "/api/access"
try:
    with urllib.request.urlopen(url, timeout=10) as response:
        status, body = response.status, response.read(4096).decode("utf-8", "replace")
except urllib.error.HTTPError as error:
    status, body = error.code, ""
except Exception:
    print("000 unreachable"); raise SystemExit
if status != 200:
    print(f"{status} gated"); raise SystemExit
try:
    payload = json.loads(body)
except ValueError:
    print("200 notjson"); raise SystemExit
print("200 " + ("protected" if payload.get("tokens_configured") else "open"))
PYPROBE
}

require_cloudflared() {
  [[ -x "$CLOUDFLARED" ]] || die "cloudflared is not installed. Run: sudo $0 install"
}

# --- the guard -----------------------------------------------------------------
# Refuses to publish an appliance that cannot tell one caller from another.
preflight() {
  local result status kind
  result="$(probe_access)"
  status="${result%% *}"
  kind="${result##* }"

  case "$status/$kind" in
    401/gated|403/gated)
      note "preflight: agent demands a token (HTTP $status), access control is live"
      ;;
    200/protected)
      note "preflight: agent reports access tokens configured"
      ;;
    200/open)
      die "The agent is running but NO access tokens are configured, so every
caller would be anonymous and could stop the cascade. Add tokens first:

  sudo python3 /opt/ptpbox-web/agent/ptpbox_agent.py --generate-token   # per person
  sudoedit /etc/ptpbox/tokens.json      # {\"operator\":[\"...\"],\"viewer\":[\"...\"]}
  sudo systemctl restart ptpbox-agent" ;;
    200/notjson)
      die "$PROBE_URL/api/access answered 200 but not JSON, so this agent predates
token access and has none. Deploy the current agent first:
  cd ~/ptpbox-main && sudo PTPBOX_USER=user bash scripts/install-host.sh" ;;
    000/*)
      die "Cannot reach $PROBE_URL/api/access. Is the agent running?" ;;
    *)
      die "$PROBE_URL/api/access returned HTTP $status; refusing to publish." ;;
  esac

  if [[ "$PROBE_URL" == http://127.0.0.1:* ]]; then
    note "preflight: reminder, bind the agent to 127.0.0.1 so the tunnel is the"
    note "           only route in"
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
    result="$(probe_access)"
    case "$result" in
      401*|403*) note "agent access: protected (${result%% *})" ;;
      "200 protected") note "agent access: protected (tokens configured)" ;;
      "200 open") note "agent access: OPEN - anyone reaching it can control the cascade" ;;
      "200 notjson") note "agent access: OPEN - agent predates token access" ;;
      *) note "agent access: unreachable" ;;
    esac
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
