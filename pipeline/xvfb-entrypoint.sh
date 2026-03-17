#!/bin/bash
# Start a virtual X display so Playwright can run in non-headless mode inside Docker.
# Removes stale lock file in case the container was killed abruptly.
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x1024x24 -ac &
export DISPLAY=:99
sleep 1
# Delegate to the original Airflow entrypoint (dumb-init → /entrypoint → airflow <command>)
exec /usr/bin/dumb-init -- /entrypoint "$@"
