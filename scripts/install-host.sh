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

missing_commands=()
for required in ip nsenter ethtool ptp4l pmc phc_ctl ts2phc tc; do
  command -v "$required" >/dev/null 2>&1 || missing_commands+=("$required")
done
if (( ${#missing_commands[@]} )); then
  echo "Missing runtime dependencies: ${missing_commands[*]}" >&2
  echo "Install linuxptp, iproute2, and ethtool before running this installer." >&2
  exit 1
fi

# The observation service opens /dev/ptp* read-only through this conventional
# udev-owned group. SupplementaryGroups applies it only to the service.
getent group clock >/dev/null || groupadd --system clock

PTPBOX_GROUP_NAME=$(id -gn "$PTPBOX_USER_NAME")
PTPBOX_USER_HOME=$(getent passwd "$PTPBOX_USER_NAME" | cut -d: -f6)
PTPBOX_ROOT_DIR=${PTPBOX_ROOT:-$PTPBOX_USER_HOME/PTPBox}

if [[ -z "$PTPBOX_USER_HOME" || "$PTPBOX_ROOT_DIR" != /* ]]; then
  echo "PTPBOX_ROOT must resolve to an absolute path." >&2
  exit 1
fi

install -d -m 0755 "$INSTALL_DIR/agent" "$INSTALL_DIR/static" "$ETC_DIR" /etc/linuxptp /run/netns /run/ptpbox /var/log/ptpbox
install -m 0755 "$SOURCE_DIR/agent/ptpbox_agent.py" "$INSTALL_DIR/agent/ptpbox_agent.py"
install -m 0755 "$SOURCE_DIR/agent/ptpbox_phc_collector.py" "$INSTALL_DIR/agent/ptpbox_phc_collector.py"
install -m 0644 "$SOURCE_DIR/agent/ptpbox_phc_store.py" "$INSTALL_DIR/agent/ptpbox_phc_store.py"
install -m 0644 "$SOURCE_DIR/agent/ptpbox_research.py" "$INSTALL_DIR/agent/ptpbox_research.py"
install -m 0644 "$SOURCE_DIR/agent/ptpbox_system.py" "$INSTALL_DIR/agent/ptpbox_system.py"
install -m 0644 "$SOURCE_DIR/agent/ptpbox_thermal.py" "$INSTALL_DIR/agent/ptpbox_thermal.py"
# Every module the agent imports must be installed beside it. Missing one turns
# into a systemd crash loop that takes the whole web surface down, so check here
# instead of after the restart.
missing_modules=()
while read -r module; do
  [ -f "$INSTALL_DIR/agent/$module.py" ] || missing_modules+=("$module")
done < <(grep -hoE '^(import|from) ptpbox_[a-z_]+' "$SOURCE_DIR/agent/ptpbox_agent.py" | awk '{print $2}' | sort -u)
if [ ${#missing_modules[@]} -gt 0 ]; then
  echo "install incomplete: ptpbox_agent imports ${missing_modules[*]} but they were not installed" >&2
  exit 1
fi
install -m 0755 "$SOURCE_DIR/scripts/ptpboxctl.py" /usr/local/sbin/ptpboxctl
install -m 0755 "$SOURCE_DIR/scripts/ptpbox_kalman_servo.py" /usr/local/sbin/ptpbox-kalman-servo
install -m 0755 "$SOURCE_DIR/scripts/ptpbox_event_monitor.py" /usr/local/sbin/ptpbox-event-monitor
install -m 0755 "$SOURCE_DIR/scripts/ptpbox_pps_compare.py" /usr/local/sbin/ptpbox-pps-compare
install -m 0644 "$SOURCE_DIR/agent/topology.json" "$ETC_DIR/topology.json"
sed -e "s|@PTPBOX_GROUP@|$PTPBOX_GROUP_NAME|g" \
  "$SOURCE_DIR/agent/ptpbox-tmpfiles.conf" > /etc/tmpfiles.d/ptpbox.conf
chmod 0644 /etc/tmpfiles.d/ptpbox.conf
systemd-tmpfiles --create /etc/tmpfiles.d/ptpbox.conf

# Ubuntu confines ptp4l with AppArmor. Multi-PHC boundary clocks need the
# JBOD clock-switch notification socket, while one host-wide filesystem needs
# a distinct management socket for every namespace. Keep the distribution
# profile intact and add a narrowly scoped local include when AppArmor exists.
if [[ -f /etc/apparmor.d/usr.sbin.ptp4l && -d /etc/apparmor.d/local ]]; then
  install -m 0644 "$SOURCE_DIR/agent/apparmor-ptpbox-ptp4l" /etc/apparmor.d/local/ptpbox-ptp4l
  touch /etc/apparmor.d/local/usr.sbin.ptp4l
  if ! grep -Fq 'include if exists <local/ptpbox-ptp4l>' /etc/apparmor.d/local/usr.sbin.ptp4l; then
    printf '\ninclude if exists <local/ptpbox-ptp4l>\n' >> /etc/apparmor.d/local/usr.sbin.ptp4l
  fi
  if command -v apparmor_parser >/dev/null 2>&1; then
    apparmor_parser -r /etc/apparmor.d/usr.sbin.ptp4l
  fi
fi

if [[ -d "$SOURCE_DIR/dist-standalone" ]]; then
  # Asset filenames are content-hashed, so copying without clearing leaves every
  # previously installed bundle behind. A browser holding cached HTML then keeps
  # loading a stale bundle indefinitely, because the file it names still exists,
  # and the UI silently stays several versions old. Replace the tree instead.
  rm -rf "$INSTALL_DIR/static/assets"
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
sed \
  -e "s|@PTPBOX_USER@|$PTPBOX_USER_NAME|g" \
  -e "s|@PTPBOX_GROUP@|$PTPBOX_GROUP_NAME|g" \
  -e "s|@PTPBOX_ROOT@|$PTPBOX_ROOT_DIR|g" \
  "$SOURCE_DIR/agent/ptpbox-phc-collector.service" > /etc/systemd/system/ptpbox-phc-collector.service
install -m 0644 "$SOURCE_DIR/agent/ptpbox-cascade.service" /etc/systemd/system/ptpbox-cascade.service
chmod 0644 /etc/systemd/system/ptpbox-agent.service
chmod 0644 /etc/systemd/system/ptpbox-phc-collector.service
chown "$PTPBOX_USER_NAME:$PTPBOX_GROUP_NAME" /run/ptpbox
install -d -o "$PTPBOX_USER_NAME" -g "$PTPBOX_GROUP_NAME" -m 0755 "$PTPBOX_ROOT_DIR/runtime"
ln -sfn "$PTPBOX_ROOT_DIR/runtime/config.json" "$ETC_DIR/config.json"
ln -sfn "$PTPBOX_ROOT_DIR/runtime/servo-request.json" "$ETC_DIR/servo-request.json"
ln -sfn "$PTPBOX_ROOT_DIR/runtime/fault-request.json" "$ETC_DIR/fault-request.json"
ln -sfn "$PTPBOX_ROOT_DIR/runtime/identification-request.json" "$ETC_DIR/identification-request.json"

printf '%s\n' "$PTPBOX_USER_NAME ALL=(root) NOPASSWD: /usr/local/sbin/ptpboxctl start, /usr/local/sbin/ptpboxctl stop, /usr/local/sbin/ptpboxctl restart, /usr/local/sbin/ptpboxctl status, /usr/local/sbin/ptpboxctl servo, /usr/local/sbin/ptpboxctl fault, /usr/local/sbin/ptpboxctl identify" > /etc/sudoers.d/ptpbox-web
chmod 0440 /etc/sudoers.d/ptpbox-web
visudo -cf /etc/sudoers.d/ptpbox-web >/dev/null

# Replace the temporary unprivileged preview only after every install step above
# has succeeded, keeping downtime to the systemd handoff itself.
if runuser -u "$PTPBOX_USER_NAME" -- tmux has-session -t PTPBoxWeb 2>/dev/null; then
  runuser -u "$PTPBOX_USER_NAME" -- tmux kill-session -t PTPBoxWeb
fi

systemctl daemon-reload
systemctl enable ptpbox-phc-collector.service ptpbox-agent.service

# Bring the timing cascade up at boot. Without this the namespaces and ptp4l
# instances are gone after a reboot, the collector has no PHC to read, and the
# raw graphs stay empty until an operator runs "ptpboxctl start" by hand.
# Set PTPBOX_AUTOSTART_CASCADE=0 to install the unit without enabling it, for
# hosts where moving NICs into namespaces unattended is not wanted.
if [ "${PTPBOX_AUTOSTART_CASCADE:-1}" = "1" ]; then
  systemctl enable ptpbox-cascade.service
  echo "Cascade autostart: enabled (PTPBOX_AUTOSTART_CASCADE=0 to opt out)"
else
  systemctl disable ptpbox-cascade.service 2>/dev/null || true
  echo "Cascade autostart: installed but disabled"
fi
systemctl restart ptpbox-phc-collector.service
systemctl restart ptpbox-agent.service

echo "PTPBox is available at http://$(hostname -I | awk '{print $1}'):8090"
echo "Operator: $PTPBOX_USER_NAME"
echo "PTP root: $PTPBOX_ROOT_DIR"
