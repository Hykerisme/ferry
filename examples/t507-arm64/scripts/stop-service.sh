#!/bin/sh
# Runs on the deploy node before artifacts are transferred.
# Repeated execution must be safe.
if [ -d /run/systemd/system ]; then
    systemctl stop demo 2>/dev/null || true
elif [ -x /sbin/init ]; then
    service demo stop 2>/dev/null || true
else
    echo "no init system detected; nothing to stop"
fi
echo "pre-deploy cleanup done"
