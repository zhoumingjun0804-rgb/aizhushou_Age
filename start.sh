#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python3"

if [ ! -x "$PYTHON_BIN" ] || ! "$PYTHON_BIN" -V >/dev/null 2>&1; then
  echo "正在创建本地 Python 虚拟环境..."
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

PY_MAJOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.minor)')"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  echo "错误: 需要 Python 3.10 或更高版本，当前为 $("$PYTHON_BIN" -V 2>&1)"
  echo "请升级系统 python3（或安装 Homebrew / pyenv 的 Python 3.12+），然后删除 backend/.venv 再运行 ./start.sh"
  exit 1
fi

echo "正在升级 pip..."
"$PYTHON_BIN" -m pip install --upgrade pip

"$PYTHON_BIN" -m pip install -r "$BACKEND_DIR/requirements.txt"

if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "已根据 .env.example 创建 .env，请按需填写 API Key 和 DREAMINA_BIN"
fi

cd "$BACKEND_DIR"
exec "$PYTHON_BIN" app.py
