#!/bin/sh
# Runs on the target after install-service.sh.
case "${XB_TARGET_INIT:-none}" in
    systemd)
        systemctl restart demo 2>/dev/null || echo "demo.service is not registered yet"
        ;;
    sysvinit)
        service demo restart 2>/dev/null || echo "demo init script is not registered yet"
        ;;
    *)
        echo "no init system detected; start the app manually"
        ;;
esac
