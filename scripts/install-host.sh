#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL_DIR=/opt/ptpbox-web
ETC_DIR=/etc/ptpbox
PTPBOX_USER_NAME=${PTPBOX_USER:-${SUDO_USER:-user}}

if ! id "$PTPBOX_USER_NAME" >/dev/null 2>&1; then
  echo "PTPBox operator account does not exist: $PTPBOX_USER_NAME" >&2
  exit 1
fi

PTPBOX_GROUP_NAME=$(id -gn "$PTPBOX_USER_NAME")
PTPBOX_USER_HOME=$(getent passwd "$PTPBOX_USER_NAME" | cut -d: -f6)
PTPBOX_ROOT_DIR=${PTPBOX_ROOT:-$PTPBOX_USER_HOME/PTPBox}

if [[ -z "$PTPBOX_USER_HOME" || "$PTPBOX_ROOT_DIR" != /* ]]; then
  echo "PTPBOX_ROOT must resolve to an absolute path." >&2
  exit 1
fi

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

sed \
  -e "s|@PTPBOX_USER@|$PTPBOX_USER_NAME|g" \
  -e "s|@PTPBOX_GROUP@|$PTPBOX_GROUP_NAME|g" \
  -e "s|@PTPBOX_ROOT@|$PTPBOX_ROOT_DIR|g" \
  "$SOURCE_DIR/agent/ptpbox-agent.service" > /etc/systemd/system/ptpbox-agent.service
chmod 0644 /etc/systemd/system/ptpbox-agent.service
install -d -o "$PTPBOX_USER_NAME" -g "$PTPBOX_GROUP_NAME" -m 0755 "$PTPBOX_ROOT_DIR/runtime"
ln -sfn "$PTPBOX_ROOT_DIR/runtime/config.json" "$ETC_DIR/config.json"

printf '%s\n' "$PTPBOX_USER_NAME ALL=(root) NOPASSWD: /usr/local/sbin/ptpboxctl start, /usr/local/sbin/ptpboxctl stop, /usr/local/sbin/ptpboxctl restart, /usr/local/sbin/ptpboxctl status" > /etc/sudoers.d/ptpbox-web
chmod 0440 /etc/sudoers.d/ptpbox-web
visudo -cf /etc/sudoers.d/ptpbox-web >/dev/null

# Replace the temporary unprivileged preview only after every install step above
# has succeeded, keeping downtime to the systemd handoff itself.
if runuser -u "$PTPBOX_USER_NAME" -- tmux has-session -t PTPBoxWeb 2>/dev/null; then
  runuser -u "$PTPBOX_USER_NAME" -- tmux kill-session -t PTPBoxWeb
fi

systemctl daemon-reload
systemctl enable --now ptpbox-agent.service

echo "PTPBox is available at http://$(hostname -I | awk '{print $1}'):8090"
echo "Operator: $PTPBOX_USER_NAME"
echo "PTP root: $PTPBOX_ROOT_DIR"
