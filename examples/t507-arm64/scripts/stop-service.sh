#!/bin/sh
# Runs on the target before artifacts are transferred.
# Repeated execution must be safe: never fail because the service is absent.
case "${XB_TARGET_INIT:-none}" in
    systemd)
        systemctl stop demo 2>/dev/null || true
        ;;
    sysvinit)
        service demo stop 2>/dev/null || true
        ;;
    *)
        echo "no init system detected; nothing to stop"
        ;;
esac
echo "pre-deploy cleanup done"
