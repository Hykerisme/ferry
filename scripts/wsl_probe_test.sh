#!/bin/bash
set -e
P=/tmp/ferry-it/probe
W=/tmp/ferry-it/work
rm -rf "$W" /tmp/ferry-it/src /tmp/ferry-it/remote /tmp/ferry-it/got
mkdir -p "$W" /tmp/ferry-it/src/sub
echo hello > /tmp/ferry-it/src/sub/a.txt

echo "=== exec + result file ==="
cat > /tmp/ferry-it/req1.json <<'EOF'
{"v":1,"run_id":"t1","workdir":"/tmp/ferry-it/work","steps":[{"id":"s1","where":"build","kind":"shell","argv":["sh","-c","echo hello; printf 'ok=1\\n' > \"$FERRY_RESULT_FILE\""],"cwd":"/tmp/ferry-it/work","result":true}]}
EOF
"$P" exec < /tmp/ferry-it/req1.json

echo "=== put/get ==="
tar -C /tmp/ferry-it/src -cf - . | "$P" put --dest /tmp/ferry-it/remote
find /tmp/ferry-it/remote -type f -printf '%P\n'
"$P" get --root /tmp/ferry-it/remote > /tmp/ferry-it/out.tar
mkdir -p /tmp/ferry-it/got
tar -C /tmp/ferry-it/got -xf /tmp/ferry-it/out.tar
cat /tmp/ferry-it/got/sub/a.txt

echo "=== lock busy ==="
mkdir -p "$W/.ferry"
flock "$W/.ferry/build.lock" -c "sleep 3" &
sleep 0.3
printf '{"v":1,"run_id":"t2","workdir":"%s","lock":{"path":"%s/.ferry/build.lock"},"steps":[]}\n' "$W" "$W" > /tmp/ferry-it/req2.json
set +e
"$P" exec < /tmp/ferry-it/req2.json
echo "exit=$?"
set -e

echo "=== stale marker recovery ==="
mkdir -p "$W/build"
echo stale > "$W/build/old.o"
echo '{}' > "$W/.ferry/build.marker"
printf '{"v":1,"run_id":"t3","workdir":"%s","marker":{"path":"%s/.ferry/build.marker"},"clean_paths":["%s/build"],"steps":[{"id":"s1","where":"build","kind":"shell","argv":["sh","-c","echo built"],"cwd":"%s"}]}\n' "$W" "$W" "$W" "$W" > /tmp/ferry-it/req3.json
"$P" exec < /tmp/ferry-it/req3.json
test ! -d "$W/build" && echo "build dir cleaned"
test ! -f "$W/.ferry/build.marker" && echo "marker cleared after normal end"

echo "=== cancel retains marker ==="
printf '{"v":1,"run_id":"t4","workdir":"%s","marker":{"path":"%s/.ferry/build.marker"},"steps":[{"id":"s1","where":"build","kind":"shell","argv":["sleep","30"],"cwd":"%s"}]}\n' "$W" "$W" "$W" > /tmp/ferry-it/req4.json
"$P" exec < /tmp/ferry-it/req4.json > /tmp/ferry-it/events4.log & PID=$!
sleep 1
kill -TERM "$PID"
set +e
wait "$PID"
echo "probe_exit=$?"
set -e
test -f "$W/.ferry/build.marker" && echo "marker_retained_after_cancel"
tail -2 /tmp/ferry-it/events4.log
