#!/bin/sh

pid=999

while true; do
	kill -0 $pid 2>/dev/null || ./mail-mirror.py &
	pid=$!
	sleep 60
done
