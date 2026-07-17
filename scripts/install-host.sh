#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL_DIR=/opt/ptpbox-web
ETC_DIR=/etc/ptpbox

install -d -m 0755 "$INSTALL_DIR/agent" "$INSTALL_DIR/static" "$ETC_DIR" /var/log/ptpbox
install -m 0755 "$SOURCE_DIR/agent/ptpbox_agent.py" "$INSTALL_DIR/agent/ptpbox_agent.py"
install -m 0755 "$SOURCE_DIR/scripts/ptpboxctl.py" /usr/local/sbin/ptpboxctl
install -m 0644 "$SOURCE_DIR/agent/topology.json" "$ETC_DIR/topology.json"

if [[ -d "$SOURCE_DIR/dist-standalone" ]]; then
  cp -R "$SOURCE_DIR/dist-standalone/." "$INSTALL_DIR/static/"
else
  echo "dist-standalone is missing; run npm run build:standalone first." >&2
  exit 1
fi

install -m 0644 "$SOURCE_DIR/agent/ptpbox-agent.service" /etc/systemd/system/ptpbox-agent.service
install -d -o user -g user -m 0755 /home/user/PTPBox/runtime

cat > /etc/sudoers.d/ptpbox-web <<'EOF'
user ALL=(root) NOPASSWD: /usr/local/sbin/ptpboxctl start, /usr/local/sbin/ptpboxctl stop, /usr/local/sbin/ptpboxctl restart, /usr/local/sbin/ptpboxctl status
EOF
chmod 0440 /etc/sudoers.d/ptpbox-web
visudo -cf /etc/sudoers.d/ptpbox-web >/dev/null

# Replace the temporary unprivileged preview only after every install step above
# has succeeded, keeping downtime to the systemd handoff itself.
if runuser -u user -- tmux has-session -t PTPBoxWeb 2>/dev/null; then
  runuser -u user -- tmux kill-session -t PTPBoxWeb
fi

systemctl daemon-reload
systemctl enable --now ptpbox-agent.service

echo "PTPBox is available at http://$(hostname -I | awk '{print $1}'):8090"
