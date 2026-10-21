#!/bin/sh
# Runs on the build node before the workspace is synced.
# Use this for environment checks so a bad toolchain fails before the transfer.
echo "build host: $(uname -s) $(uname -m)"

if [ -n "${FERRY_RESULT_FILE:-}" ]; then
    printf 'arch=%s\n' "$(uname -m)" > "$FERRY_RESULT_FILE"
fi
