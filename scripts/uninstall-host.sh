#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this uninstaller with sudo." >&2
  exit 1
fi

systemctl disable --now ptpbox-agent.service 2>/dev/null || true
rm -f /etc/systemd/system/ptpbox-agent.service
rm -f /etc/sudoers.d/ptpbox-web
rm -f /etc/tmpfiles.d/ptpbox.conf
rm -f /usr/local/sbin/ptpboxctl
rm -f /usr/local/sbin/ptpbox-kalman-servo
rm -f /etc/ptpbox/config.json
rm -rf /opt/ptpbox-web
systemctl daemon-reload

echo "PTPBox web services were removed."
echo "Topology, logs, experiment data, and the PTPBox source tree were preserved."
