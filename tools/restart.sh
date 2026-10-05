#!/bin/sh
# restart the canvas server on 127.0.0.1:8766
cd "$(dirname "$0")/.." || exit 1
# whatever holds the port goes (on macOS the process shows up as "Python server.py", not "python3 server.py")
pids=$(lsof -ti tcp:8766 2>/dev/null)
[ -n "$pids" ] && kill $pids
sleep 0.5
nohup python3 server.py > /tmp/canvas-server.log 2>&1 < /dev/null &
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8766/?src=transformer.content.json"
