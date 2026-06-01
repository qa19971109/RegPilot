#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${REGPILOT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HOST="${REGPILOT_HOST:-0.0.0.0}"
PORT="${REGPILOT_PORT:-8766}"
PID_FILE="${REGPILOT_PID_FILE:-$PROJECT_DIR/logs/api.pid}"
LOG_FILE="${REGPILOT_LOG_FILE:-$PROJECT_DIR/logs/api.out}"

cd "$PROJECT_DIR"
mkdir -p logs

is_running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

start() {
  if is_running; then
    echo "RegPilot API already running: pid=$(cat "$PID_FILE")"
    return 0
  fi
  REGPILOT_HOST="$HOST" REGPILOT_PORT="$PORT" nohup scripts/api.sh > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "RegPilot API started: pid=$(cat "$PID_FILE") url=http://$HOST:$PORT log=$LOG_FILE"
}

stop() {
  if ! is_running; then
    echo "RegPilot API is not running"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "RegPilot API stopped"
      return 0
    fi
    sleep 0.5
  done
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "RegPilot API force stopped"
}

status() {
  if is_running; then
    echo "RegPilot API running: pid=$(cat "$PID_FILE") url=http://$HOST:$PORT log=$LOG_FILE"
  else
    echo "RegPilot API stopped"
    return 1
  fi
}

case "${1:-status}" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    stop
    start
    ;;
  status)
    status
    ;;
  logs)
    tail -f "$LOG_FILE"
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs}" >&2
    exit 2
    ;;
esac
