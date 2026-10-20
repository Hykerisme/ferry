#!/bin/bash
P=/tmp/ferry-it/probe
L=/tmp/ferry-it/work/.ferry/x.lock
mkdir -p "$(dirname "$L")"
rm -f "$L"
"$P" lock --path "$L" < /dev/null
echo "normal=$?"
sleep 3 | "$P" lock --path "$L" > /tmp/ferry-it/holder.out &
HOLDER=$!
sleep 0.5
"$P" lock --path "$L" < /dev/null
echo "busy=$?"
wait "$HOLDER"
echo "holder_output: $(cat /tmp/ferry-it/holder.out)"
"$P" lock --path "$L" < /dev/null
echo "after_release=$?"
