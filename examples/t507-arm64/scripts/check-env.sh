#!/bin/sh
# Runs on the build node after sync. Reports target facts back to Ferry.
echo "target arch=$XB_TARGET_ARCH os=$XB_TARGET_OS $XB_TARGET_OS_VER init=$XB_TARGET_INIT"

if [ -n "${FERRY_RESULT_FILE:-}" ]; then
    printf 'arch=%s init=%s\n' "$XB_TARGET_ARCH" "$XB_TARGET_INIT" > "$FERRY_RESULT_FILE"
fi
