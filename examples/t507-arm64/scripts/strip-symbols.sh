#!/bin/sh
# Runs on the build node after the build command.
# Strips shipped binaries with the cross strip tool when available.
OUT="${XB_OUT:-build/out}"
STRIP="${CROSS_STRIP:-aarch64-linux-gnu-strip}"

if ! command -v "$STRIP" >/dev/null 2>&1; then
    echo "skip strip: $STRIP not found"
    exit 0
fi

find "$OUT" -type f \( -name '*.so*' -o -perm -111 \) -exec "$STRIP" {} \; 2>/dev/null || true
echo "stripped binaries under $OUT"
