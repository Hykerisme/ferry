#!/bin/sh
# Runs on the deploy node after install-service.sh.
if [ -d /run/systemd/system ]; then
    systemctl restart demo 2>/dev/null || echo "demo.service is not registered yet"
elif [ -x /sbin/init ]; then
    service demo restart 2>/dev/null || echo "demo init script is not registered yet"
else
    echo "no init system detected; start the app manually"
fi
