#!/usr/bin/env bash
# 开发模式：后台常驻 + 改 .py 自动重启 + 改 HTML 刷新浏览器即可
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python3"
PID_FILE="$ROOT_DIR/.dev-server.pid"
LOG_FILE="$ROOT_DIR/.dev-server.log"

if [ ! -x "$PYTHON_BIN" ] || ! "$PYTHON_BIN" -V >/dev/null 2>&1; then
  echo "正在创建本地 Python 虚拟环境..."
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

"$PYTHON_BIN" -m pip install -q -r "$BACKEND_DIR/requirements.txt"

if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "已根据 .env.example 创建 .env"
fi

# 从 .env 读取 PORT（默认 8000）
load_port() {
  PORT="${PORT:-8000}"
  if [ -f "$ROOT_DIR/.env" ]; then
    local env_port
    env_port="$(grep -E '^[[:space:]]*PORT=' "$ROOT_DIR/.env" | tail -1 | sed 's/^[^=]*=//; s/[[:space:]"'\'']//g' || true)"
    if [ -n "$env_port" ] && [[ "$env_port" =~ ^[0-9]+$ ]]; then
      PORT="$env_port"
    fi
  fi
  export PORT
}

# 结束本项目残留的 app.py / dev_watch.py
stop_project_python() {
  local pids
  pids="$(pgrep -f "$BACKEND_DIR/app.py" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "停止本项目 app.py 进程..."
    kill $pids 2>/dev/null || true
  fi
  pids="$(pgrep -f "$BACKEND_DIR/dev_watch.py" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "停止本项目 dev_watch.py 进程..."
    kill $pids 2>/dev/null || true
  fi
}

# 停止其他 Cursor 项目里自动拉起的 dev_watch（避免抢走 8000）
stop_foreign_dev_watch() {
  local wp cwd
  for wp in $(pgrep -f 'dev_watch.py' 2>/dev/null || true); do
    cwd="$(lsof -p "$wp" 2>/dev/null | awk '/cwd/ {print $NF; exit}')"
    if [ -n "$cwd" ] && [[ "$cwd" != "$BACKEND_DIR" ]]; then
      echo "停止其他项目的 dev_watch (pid=$wp, cwd=$cwd)..."
      kill "$wp" 2>/dev/null || true
    fi
  done
}

# 释放 PORT ~ PORT+15：本项目 + 占用该端口的其他 app.py
free_dev_ports() {
  load_port
  local port pids pid cmd cwd
  stop_foreign_dev_watch
  for port in $(seq "$PORT" $((PORT + 15))); do
    pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
    [ -z "$pids" ] && continue
    for pid in $pids; do
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      cwd="$(lsof -p "$pid" 2>/dev/null | awk '/cwd/ {print $NF; exit}')"
      if [[ "$cmd" == *app.py* ]] || [[ "$cmd" == *dev_watch.py* ]]; then
        if [[ -z "$cwd" || "$cwd" == "$BACKEND_DIR" || "$cwd" == *"$ROOT_DIR"* ]]; then
          echo "释放端口 $port (pid=$pid)..."
        else
          echo "释放端口 $port：其他项目占用 (pid=$pid, cwd=$cwd)..."
        fi
        kill "$pid" 2>/dev/null || true
      fi
    done
  done
  sleep 0.5
  for port in $(seq "$PORT" $((PORT + 15))); do
    pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
    [ -z "$pids" ] && continue
    for pid in $pids; do
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *app.py* ]] || [[ "$cmd" == *dev_watch.py* ]]; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    done
  done
}

stop_old() {
  if [ -f "$PID_FILE" ]; then
    old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
      echo "停止旧开发进程 (pid=$old_pid)..."
      kill "$old_pid" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$PID_FILE"
  fi
  stop_project_python
  free_dev_ports
}

start_daemon() {
  stop_old
  load_port
  export DEV_RELOAD=1
  cd "$BACKEND_DIR"
  nohup "$PYTHON_BIN" dev_watch.py >>"$LOG_FILE" 2>&1 &
  sleep 2
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "开发服务已在后台运行 (watch pid=$(cat "$PID_FILE"))"
    echo "日志: $LOG_FILE"
    _port_start="${PORT:-8000}"
    _port_end=$((_port_start + 15))
    _app_port=""
    for p in $(seq "$_port_start" "$_port_end"); do
      _html="$(curl -s --connect-timeout 1 "http://127.0.0.1:$p/" 2>/dev/null || true)"
      if echo "$_html" | grep -q 'nav-tools'; then
        _app_port="$p"
        break
      fi
    done
    if [ -n "$_app_port" ]; then
      echo "访问（本项目）: http://localhost:${_app_port}"
      if [ "$_app_port" != "$_port_start" ]; then
        echo "提示: 端口 ${_port_start} 被其他旧项目占用，请用上方地址，或关闭 ai-design-modifier 后执行 ./dev.sh restart"
      fi
    else
      for p in $(seq "$_port_start" "$_port_end"); do
        if curl -s -o /dev/null --connect-timeout 1 "http://127.0.0.1:$p/" 2>/dev/null; then
          echo "访问: http://localhost:$p （若界面缺少开屏/裁切/GIF，请执行 ./dev.sh restart）"
          break
        fi
      done
    fi
    echo "改 backend/templates/index.html → 浏览器刷新即可"
    echo "改 backend/*.py → 自动重启（约 1 秒）"
  else
    echo "启动失败，请查看 $LOG_FILE"
    exit 1
  fi
}

case "${1:-}" in
  stop)
    stop_old
    echo "开发服务已停止（端口 ${PORT:-8000} 起已释放）"
    exit 0
    ;;
  daemon|start|"")
    start_daemon
    exit 0
    ;;
  fg)
    stop_old
    export DEV_RELOAD=1
    cd "$BACKEND_DIR"
    exec "$PYTHON_BIN" dev_watch.py
    ;;
  status)
    load_port
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "开发服务运行中 (watch pid=$(cat "$PID_FILE"))"
      echo "日志: $LOG_FILE"
      _port_start="${PORT:-8000}"
      _port_end=$((_port_start + 10))
      for p in $(seq "$_port_start" "$_port_end"); do
        if curl -s -o /dev/null --connect-timeout 1 "http://127.0.0.1:$p/" 2>/dev/null; then
          echo "访问: http://localhost:$p"
          break
        fi
      done
    else
      echo "开发服务未运行"
    fi
    exit 0
    ;;
  *)
    echo "用法: $0 [start|fg|stop|status]"
    echo "  start  后台开发模式（默认，改代码自动生效）"
    echo "  fg     前台开发模式"
    echo "  stop   停止后台开发服务"
    echo "  status 查看状态"
    exit 1
    ;;
esac
