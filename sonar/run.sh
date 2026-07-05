#!/usr/bin/env bash
# One-command live run: start the sensor + viewer, open the browser.
cd "$(dirname "$0")"
python3 sensor.py & S=$!
python3 -m http.server 8123 --directory web & H=$!
trap "kill $S $H 2>/dev/null" EXIT
sleep 1
echo "Live viewer -> http://localhost:8123/   (walk the forward cone)"
command -v xdg-open >/dev/null && xdg-open "http://localhost:8123/" >/dev/null 2>&1 || true
wait
