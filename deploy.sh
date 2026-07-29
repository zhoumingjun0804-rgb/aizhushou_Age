#!/usr/bin/env bash
# 服务器一键部署：检查环境 → 安装 PM2 → 创建 venv → PM2 守护启动
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python3"
ENV_FILE="$ROOT_DIR/.env"
ECOSYSTEM_FILE="$ROOT_DIR/ecosystem.config.cjs"
APP_NAME="${PM2_APP_NAME:-aizhushou-age}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
MIN_NODE_MAJOR=18
REMOTE_DIR="/home/xiaoA"
REMOTE_DIR_HLL="/home/xiaoA-hll"
REMOTE_APP_NAME_HLL="aizhushou-hll"
REMOTE_PORT_HLL="8629"
REMOTE_HINT_PORT=""
PYTHON_INSTALL_PREFIX="/opt/aizhushou-python"

# 设为 1 时跳过 Node/PM2 自动安装（仅做 Python 环境与 PM2 启动）
SKIP_NODE_INSTALL="${SKIP_NODE_INSTALL:-0}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[deploy]${NC} $*"; }
ok()    { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[deploy]${NC} $*"; }
fail()  { echo -e "${RED}[deploy]${NC} $*" >&2; exit 1; }

usage() {
  cat <<EOF
用法: $0 [命令]

命令:
  deploy   完整部署（默认）：检查环境、安装依赖、PM2 启动/重载
  start    启动 PM2 进程（需已 deploy 过）
  stop     停止 PM2 进程
  restart  重启 PM2 进程
  status   查看 PM2 状态与健康检查
  logs     查看 PM2 日志（Ctrl+C 退出）
  update   git pull + 更新依赖 + PM2 重载
  setup    仅安装 Python 依赖并生成 PM2 配置，不启动
  remote       上传到测试服务器并在远端 deploy（小灯塔，/home/xiaoA）
  remote sync  仅 rsync 到测试服务器，不在远端启动
  remote-hll       画啦啦实例：/home/xiaoA-hll，PORT=8629，PM2=aizhushou-hll
  remote-hll sync  仅 rsync 画啦啦目录并写 PORT=8629，不重启

环境变量:
  PM2_APP_NAME=名称     PM2 进程名（默认 aizhushou-age）
  SKIP_NODE_INSTALL=1   跳过 Node/PM2 自动安装
  GIT_BRANCH=main       update 时拉取的分支
  TEST_SERVICE_URL / TEST_ACCOUNT / TEST_PASSWORD  remote 部署目标（见 .env）

示例:
  chmod +x deploy.sh
  ./deploy.sh              # 本机部署
  ./deploy.sh remote       # 小灯塔 → /home/xiaoA
  ./deploy.sh remote-hll   # 画啦啦 → /home/xiaoA-hll:8629
  ./deploy.sh update       # 代码更新后重载
  pm2 startup && pm2 save  # 开机自启（deploy 成功后会提示）
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

load_nvm() {
  local nvm_dir="${NVM_DIR:-$HOME/.nvm}"
  if [ -s "$nvm_dir/nvm.sh" ]; then
    # shellcheck disable=SC1091
    source "$nvm_dir/nvm.sh"
  fi
}

version_ge() {
  # version_ge 3 10  → 当前 Python/Node 主版本.minor >= 3.10
  local major="$1" minor="$2"
  local cur_major="$3" cur_minor="$4"
  if [ "$cur_major" -gt "$major" ]; then return 0; fi
  if [ "$cur_major" -eq "$major" ] && [ "$cur_minor" -ge "$minor" ]; then return 0; fi
  return 1
}

is_centos7() {
  [ -f /etc/centos-release ] && grep -q ' 7' /etc/centos-release 2>/dev/null
}

python_eval_ok() {
  local code="$1"
  "$PYTHON_BIN" -c "$code" >/dev/null 2>&1
}

trim_value() {
  local v="$1"
  v="${v#"${v%%[![:space:]]*}"}"
  v="${v%"${v##*[![:space:]]}"}"
  v="${v#\"}"; v="${v%\"}"
  v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}

load_port() {
  PORT="${PORT:-8000}"
  if [ -f "$ENV_FILE" ]; then
    local env_port
    env_port="$(grep -E '^[[:space:]]*PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
    env_port="$(trim_value "$env_port")"
    if [ -n "$env_port" ] && [[ "$env_port" =~ ^[0-9]+$ ]]; then
      PORT="$env_port"
    fi
  fi
  export PORT
}

load_env_var() {
  local key="$1" val=""
  [ -f "$ENV_FILE" ] || return 1
  val="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  val="$(trim_value "$val")"
  [ -n "$val" ] || return 1
  printf '%s' "$val"
}

load_remote_config() {
  [ -f "$ENV_FILE" ] || fail "未找到 $ENV_FILE，请先配置 TEST_SERVICE_URL / TEST_ACCOUNT / TEST_PASSWORD"

  TEST_SERVICE_URL="$(load_env_var TEST_SERVICE_URL || true)"
  TEST_ACCOUNT="$(load_env_var TEST_ACCOUNT || true)"
  TEST_PASSWORD="$(load_env_var TEST_PASSWORD || true)"

  [ -n "$TEST_SERVICE_URL" ] || fail ".env 缺少 TEST_SERVICE_URL"
  [ -n "$TEST_ACCOUNT" ] || fail ".env 缺少 TEST_ACCOUNT"
  [ -n "$TEST_PASSWORD" ] || fail ".env 缺少 TEST_PASSWORD"

  TEST_SERVICE_URL="${TEST_SERVICE_URL#http://}"
  TEST_SERVICE_URL="${TEST_SERVICE_URL#https://}"
  TEST_SERVICE_URL="${TEST_SERVICE_URL%%/*}"

  REMOTE_SSH="${TEST_ACCOUNT}@${TEST_SERVICE_URL}"
  export TEST_PASSWORD
}

ensure_remote_tools() {
  need_cmd ssh || fail "未找到 ssh"
  need_cmd rsync || fail "未找到 rsync，请安装（macOS: brew install rsync；Ubuntu: apt install rsync）"
  if ! need_cmd sshpass; then
    fail "未找到 sshpass（remote 部署需要密码登录）。安装: brew install hudochenkov/sshpass/sshpass 或 apt install sshpass"
  fi
}

remote_ssh_opts() {
  echo "-o StrictHostKeyChecking=accept-new -o PreferredAuthentications=password -o PubkeyAuthentication=no"
}

remote_ssh() {
  # shellcheck disable=SC2046
  SSHPASS="$TEST_PASSWORD" sshpass -e ssh $(remote_ssh_opts) "$REMOTE_SSH" "$@"
}

remote_rsync() {
  info "同步到 ${REMOTE_SSH}:${REMOTE_DIR}/ ..."
  local ssh_cmd="ssh $(remote_ssh_opts)"
  SSHPASS="$TEST_PASSWORD" sshpass -e rsync -avz \
    --exclude 'backend/.venv/' \
    --exclude '.git/' \
    --exclude 'uploads/' \
    --exclude 'outputs/' \
    --exclude 'history.json' \
    --exclude 'logs/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.dev-server.pid' \
    --exclude '.dev-server.log' \
    --exclude 'ecosystem.config.cjs' \
    --exclude '.DS_Store' \
    -e "$ssh_cmd" \
    "$ROOT_DIR/" "${REMOTE_SSH}:${REMOTE_DIR}/"
}

remote_prepare_dir() {
  info "远端创建目录 ${REMOTE_DIR} ..."
  remote_ssh "mkdir -p '${REMOTE_DIR}'"
}

remote_chmod_scripts() {
  remote_ssh "chmod +x '${REMOTE_DIR}/deploy.sh' '${REMOTE_DIR}/start.sh' '${REMOTE_DIR}/dev.sh' 2>/dev/null || chmod +x '${REMOTE_DIR}/deploy.sh'"
}

remote_run_deploy() {
  info "远端执行 deploy.sh（PM2: ${APP_NAME}）..."
  remote_ssh "cd '${REMOTE_DIR}' && PM2_APP_NAME='${APP_NAME}' ./deploy.sh"
}

remote_set_port() {
  local port="$1"
  info "远端设置 PORT=${port}（仅改 ${REMOTE_DIR}/.env）..."
  remote_ssh "cd '${REMOTE_DIR}' && \
    if [ -f .env ]; then
      if grep -qE '^[[:space:]]*PORT=' .env; then
        sed -i.bak -E 's/^[[:space:]]*PORT=.*/PORT=${port}/' .env && rm -f .env.bak
      else
        printf '\\nPORT=%s\\n' '${port}' >> .env
      fi
    else
      printf 'PORT=%s\\n' '${port}' > .env
    fi"
}

remote_health_hint() {
  if [ -n "${REMOTE_HINT_PORT}" ]; then
    PORT="${REMOTE_HINT_PORT}"
  else
    load_port
  fi
  ok "远端部署完成，访问: http://${TEST_SERVICE_URL}:${PORT}/"
}

apply_remote_hll_profile() {
  REMOTE_DIR="${REMOTE_DIR_HLL}"
  APP_NAME="${REMOTE_APP_NAME_HLL}"
  REMOTE_HINT_PORT="${REMOTE_PORT_HLL}"
}

cmd_remote_sync() {
  load_remote_config
  ensure_remote_tools
  remote_prepare_dir
  remote_rsync
  remote_chmod_scripts
  if [ -n "${REMOTE_HINT_PORT}" ]; then
    remote_set_port "${REMOTE_HINT_PORT}"
  fi
  ok "同步完成（未在远端启动服务）"
}

cmd_remote_deploy() {
  load_remote_config
  info "========== 远程部署 → ${REMOTE_SSH}:${REMOTE_DIR} =========="
  ensure_remote_tools
  ok "目标: ${REMOTE_SSH}:${REMOTE_DIR}（PM2: ${APP_NAME}）"
  remote_prepare_dir
  remote_rsync
  remote_chmod_scripts
  if [ -n "${REMOTE_HINT_PORT}" ]; then
    remote_set_port "${REMOTE_HINT_PORT}"
  fi
  remote_run_deploy
  remote_health_hint
}

cmd_remote() {
  local sub="${1:-deploy}"
  case "$sub" in
    sync)       cmd_remote_sync ;;
    deploy|"")  cmd_remote_deploy ;;
    *)          fail "未知 remote 子命令: $sub（可用: remote / remote sync）" ;;
  esac
}

cmd_remote_hll() {
  local sub="${1:-deploy}"
  apply_remote_hll_profile
  case "$sub" in
    sync)       cmd_remote_sync ;;
    deploy|"")  cmd_remote_deploy ;;
    *)          fail "未知 remote-hll 子命令: $sub（可用: remote-hll / remote-hll sync）" ;;
  esac
}

find_system_python() {
  local candidates=(
    "${PYTHON_INSTALL_PREFIX}/bin/python3"
    /opt/rh/rh-python311/root/usr/bin/python3
    python3.14 python3.13 python3.12 python3.11 python3.10 python3
  )
  local bin
  for bin in "${candidates[@]}"; do
    if [ -x "$bin" ] 2>/dev/null || need_cmd "$bin"; then
      echo "$bin"
      return 0
    fi
  done
  return 1
}

install_python_ius_el7() {
  [ -f /etc/centos-release ] || return 1
  grep -q ' 7' /etc/centos-release 2>/dev/null || return 1
  [ "$(id -u)" -eq 0 ] || return 1
  need_cmd yum || return 1
  info "CentOS 7：尝试 IUS 源安装 python310 ..."
  if [ ! -f /etc/yum.repos.d/ius.repo ]; then
    curl -fsSL -o /tmp/ius-release.rpm https://repo.ius.io/ius-release-el7.rpm || return 1
    yum install -y /tmp/ius-release.rpm 2>/dev/null || return 1
  fi
  yum install -y python310 python310-pip 2>/dev/null || return 1
  need_cmd python3.10
}

install_python_centos_scl() {
  [ -f /etc/centos-release ] || return 1
  [ "$(id -u)" -eq 0 ] || return 1
  need_cmd yum || return 1
  info "CentOS：尝试安装 rh-python311 (SCL)..."
  yum install -y centos-release-scl 2>/dev/null || true
  yum install -y rh-python311 rh-python311-python-pip 2>/dev/null || return 1
  [ -x /opt/rh/rh-python311/root/usr/bin/python3 ]
}

install_python_miniconda() {
  [ "$(id -u)" -eq 0 ] || return 1
  [ -x "${PYTHON_INSTALL_PREFIX}/bin/python3" ] && return 0
  need_cmd curl || return 1
  info "通过 Miniconda 安装 Python 3.12 → ${PYTHON_INSTALL_PREFIX} ..."
  local installer_url="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
  if [ -f /etc/centos-release ] && grep -q ' 7' /etc/centos-release 2>/dev/null; then
    installer_url="https://repo.anaconda.com/miniconda/Miniconda3-py310_4.12.0-Linux-x86_64.sh"
  fi
  curl -fsSL "$installer_url" -o /tmp/miniconda-aizhushou.sh \
    || return 1
  bash /tmp/miniconda-aizhushou.sh -b -p "${PYTHON_INSTALL_PREFIX}"
  rm -f /tmp/miniconda-aizhushou.sh
  if [ -x "${PYTHON_INSTALL_PREFIX}/bin/python3" ]; then
    local maj min
    maj="$("${PYTHON_INSTALL_PREFIX}/bin/python3" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)"
    min="$("${PYTHON_INSTALL_PREFIX}/bin/python3" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)"
    if version_ge "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" "$maj" "$min"; then
      return 0
    fi
  fi
  if [ -x "${PYTHON_INSTALL_PREFIX}/bin/conda" ]; then
    "${PYTHON_INSTALL_PREFIX}/bin/conda" install -y -q python=3.12 pip 2>/dev/null \
      || "${PYTHON_INSTALL_PREFIX}/bin/conda" install -y -q python=3.10 pip 2>/dev/null || true
  fi
  [ -x "${PYTHON_INSTALL_PREFIX}/bin/python3" ]
}

install_python_system() {
  [ "$(id -u)" -eq 0 ] || return 1
  info "Python 版本过低，尝试安装 Python 3.10+ ..."
  if need_cmd apt-get; then
    apt-get update -qq
    apt-get install -y -qq software-properties-common curl ca-certificates 2>/dev/null || true
    for pkg in python3.12 python3.12-venv python3.11 python3.11-venv python3.10 python3.10-venv; do
      apt-get install -y -qq "$pkg" 2>/dev/null || true
    done
    return 0
  fi
  if need_cmd dnf; then
    dnf install -y python3.11 python3.11-pip 2>/dev/null || dnf install -y python3 python3-pip 2>/dev/null || true
    return 0
  fi
  if need_cmd yum; then
    if [ -x "${PYTHON_INSTALL_PREFIX}/bin/python3" ]; then
      return 0
    fi
    install_python_centos_scl && return 0
    yum install -y python3.11 python3.11-pip 2>/dev/null || yum install -y python3 python3-pip 2>/dev/null || true
    return 0
  fi
  return 1
}

check_python() {
  info "检查 Python..."
  local sys_py
  sys_py="$(find_system_python)" || fail "未找到 python3。请安装 Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+（如 apt install python3.12 python3.12-venv）"

  local major minor ver
  major="$("$sys_py" -c 'import sys; print(sys.version_info.major)' 2>/dev/null)" || fail "无法运行 $sys_py"
  minor="$("$sys_py" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)"
  ver="$("$sys_py" -V 2>&1)"

  if ! version_ge "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" "$major" "$minor"; then
    install_python_system || true
    sys_py="$(find_system_python)" || true
    if [ -n "${sys_py:-}" ]; then
      major="$("$sys_py" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)"
      minor="$("$sys_py" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)"
      ver="$("$sys_py" -V 2>&1)"
    fi
    if ! version_ge "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" "$major" "$minor"; then
      install_python_miniconda || true
      sys_py="$(find_system_python)" || fail "安装 Python 后仍未找到 python3"
      major="$("$sys_py" -c 'import sys; print(sys.version_info.major)' 2>/dev/null)"
      minor="$("$sys_py" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)"
      ver="$("$sys_py" -V 2>&1)"
    fi
    if ! version_ge "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" "$major" "$minor"; then
      fail "需要 Python >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}，当前: $ver"
    fi
  fi
  ok "$ver ($sys_py)"
  SYSTEM_PYTHON="$sys_py"
}

pick_requirements_file() {
  if [ -f /etc/centos-release ] && grep -q ' 7' /etc/centos-release 2>/dev/null; then
    echo "$BACKEND_DIR/requirements-deploy.txt"
  elif [ -f "$BACKEND_DIR/requirements-deploy.txt" ] && [ "${USE_DEPLOY_REQUIREMENTS:-0}" = "1" ]; then
    echo "$BACKEND_DIR/requirements-deploy.txt"
  else
    echo "$BACKEND_DIR/requirements.txt"
  fi
}

install_deploy_extras() {
  local req_file
  req_file="$(pick_requirements_file)"
  [ "$req_file" = "$BACKEND_DIR/requirements-deploy.txt" ] || return 0

  info "安装附加依赖（rembg / Playwright）..."

  if python_eval_ok "from rembg import remove; import onnxruntime; import numpy"; then
    ok "rembg 依赖已存在"
  else
    info "安装 rembg 兼容依赖..."
    "$PYTHON_BIN" -m pip install -q \
      "numpy>=1.24,<2" \
      "greenlet>=3.1.1,<4" \
      "opencv-python-headless<4.10" \
      onnxruntime \
      rembg
    if python_eval_ok "from rembg import remove; import onnxruntime; import numpy"; then
      ok "rembg 已启用"
    else
      warn "rembg 安装后仍未通过验证，智能抠图将继续走现有回退逻辑"
    fi
  fi

  if is_centos7; then
    warn "CentOS 7 的 glibc 版本过旧，已跳过 Playwright 浏览器安装；网页参考抓取仅支持静态 HTTP 内容"
    return 0
  fi

  if python_eval_ok "from playwright.sync_api import sync_playwright"; then
    ok "Playwright Python 包已存在"
  else
    info "安装 Playwright Python 包..."
    "$PYTHON_BIN" -m pip install -q playwright || {
      warn "Playwright Python 包安装失败，网页参考抓取将回退为静态 HTTP 抓取"
      return 0
    }
  fi

  info "安装 Playwright Chromium ..."
  "$PYTHON_BIN" -m playwright install chromium || {
    warn "Playwright Chromium 安装失败，网页参考抓取将回退为静态 HTTP 抓取"
    return 0
  }
  "$PYTHON_BIN" -m playwright install-deps chromium >/dev/null 2>&1 || \
    "$PYTHON_BIN" -m playwright install-deps >/dev/null 2>&1 || true
  ok "Playwright 浏览器已安装"
}

setup_venv() {
  info "配置 Python 虚拟环境..."
  if [ ! -x "$PYTHON_BIN" ] || ! "$PYTHON_BIN" -V >/dev/null 2>&1; then
    warn "创建虚拟环境 $VENV_DIR"
    rm -rf "$VENV_DIR"
    "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
  fi

  local major minor
  major="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.major)')"
  minor="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.minor)')"
  if ! version_ge "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" "$major" "$minor"; then
    warn "虚拟环境 Python 版本过低，重建 venv..."
    rm -rf "$VENV_DIR"
    "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
  fi

  if ! "$SYSTEM_PYTHON" -c 'import venv' 2>/dev/null; then
    fail "缺少 Python venv 模块，请安装（如 apt install python3.12-venv）"
  fi

  info "升级 pip 并安装依赖..."
  local req_file
  req_file="$(pick_requirements_file)"
  [ "$req_file" = "$BACKEND_DIR/requirements-deploy.txt" ] && warn "使用精简依赖 $req_file（CentOS 7 / 生产部署）"
  "$PYTHON_BIN" -m pip install --upgrade pip -q
  "$PYTHON_BIN" -m pip install -r "$req_file" -q
  ok "Python 依赖已就绪"
}

ensure_env_file() {
  if [ ! -f "$ENV_FILE" ] && [ -f "$ROOT_DIR/.env.example" ]; then
    cp "$ROOT_DIR/.env.example" "$ENV_FILE"
    warn "已根据 .env.example 创建 .env，请填写 LOVART_ACCESS_KEY 等配置"
  elif [ ! -f "$ENV_FILE" ]; then
    warn "未找到 .env，服务可启动但生图等功能可能不可用"
  fi
}

ensure_runtime_dirs() {
  mkdir -p "$ROOT_DIR/uploads" "$ROOT_DIR/outputs"
}

install_node_via_nvm() {
  local nvm_dir="${NVM_DIR:-$HOME/.nvm}"
  if [ ! -s "$nvm_dir/nvm.sh" ]; then
    info "通过 nvm 安装 Node.js（用户目录，无需 root）..."
    export NVM_DIR="$nvm_dir"
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
  fi
  # shellcheck disable=SC1091
  source "$nvm_dir/nvm.sh"
  nvm install --lts
  nvm use --lts
}

install_node_system() {
  if need_cmd apt-get && [ "$(id -u)" -eq 0 ]; then
    info "通过 apt 安装 Node.js..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
    return 0
  fi
  if need_cmd yum && [ "$(id -u)" -eq 0 ]; then
    info "通过 yum 安装 Node.js..."
    curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
    yum install -y nodejs
    return 0
  fi
  return 1
}

ensure_node() {
  load_nvm
  if need_cmd node; then
    local major ver
    major="$(node -p 'process.versions.node.split(".")[0]')" || major=0
    ver="$(node -v 2>/dev/null || echo unknown)"
    if [ "$major" -lt "$MIN_NODE_MAJOR" ]; then
      warn "Node.js 版本偏低 ($ver)，建议 >= ${MIN_NODE_MAJOR}.x"
    else
      ok "Node.js $ver"
    fi
    return 0
  fi

  [ "$SKIP_NODE_INSTALL" = "1" ] && fail "未找到 Node.js，且 SKIP_NODE_INSTALL=1"

  warn "未检测到 Node.js，尝试自动安装..."
  if install_node_system; then
    ok "Node.js $(node -v) 已安装"
    return 0
  fi
  install_node_via_nvm
  ok "Node.js $(node -v) 已安装（nvm）"
}

ensure_pm2() {
  load_nvm
  if need_cmd pm2; then
    ok "PM2 $(pm2 -v 2>/dev/null || echo installed)"
    return 0
  fi

  [ "$SKIP_NODE_INSTALL" = "1" ] && fail "未找到 PM2，请先 npm install -g pm2"

  ensure_node
  need_cmd npm || fail "未找到 npm，无法安装 PM2"

  info "全局安装 PM2..."
  npm install -g pm2
  need_cmd pm2 || fail "PM2 安装失败"
  ok "PM2 $(pm2 -v) 已安装"
}

write_ecosystem() {
  info "生成 PM2 配置 $ECOSYSTEM_FILE"
  cat >"$ECOSYSTEM_FILE" <<EOF
module.exports = {
  apps: [{
    name: '${APP_NAME}',
    script: 'app.py',
    cwd: '${BACKEND_DIR}',
    interpreter: '${PYTHON_BIN}',
    env_file: '${ENV_FILE}',
    instances: 1,
    exec_mode: 'fork',
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 3000,
    merge_logs: true,
    log_date_format: 'YYYY-MM-DD HH:mm:ss',
    error_file: '${ROOT_DIR}/logs/pm2-error.log',
    out_file: '${ROOT_DIR}/logs/pm2-out.log',
    max_memory_restart: '1G',
    watch: false,
  }],
};
EOF
  mkdir -p "$ROOT_DIR/logs"
  ok "PM2 配置已写入"
}

pm2_start_or_reload() {
  need_cmd pm2 || fail "PM2 不可用"
  if pm2 describe "$APP_NAME" >/dev/null 2>&1; then
    info "重载 PM2 进程 $APP_NAME..."
    pm2 reload "$ECOSYSTEM_FILE" --update-env
  else
    info "启动 PM2 进程 $APP_NAME..."
    pm2 start "$ECOSYSTEM_FILE"
  fi
  pm2 save >/dev/null 2>&1 || true
}

health_check() {
  load_port
  local url="http://127.0.0.1:${PORT}/"
  local i port found=""
  for i in $(seq 0 31); do
    port=$((PORT + i))
    if curl -sf --connect-timeout 2 "http://127.0.0.1:${port}/" >/dev/null 2>&1; then
      found="$port"
      break
    fi
  done
  if [ -n "$found" ]; then
    ok "服务健康检查通过: http://127.0.0.1:${found}/"
    if [ "$found" != "$PORT" ]; then
      warn "配置端口 ${PORT} 被占用，服务实际监听 ${found}，建议在 .env 中调整 PORT 或释放端口"
    fi
  else
    warn "健康检查未通过（${PORT}–$((PORT + 31))），请执行: $0 logs"
  fi
}

print_summary() {
  load_port
  local ip=""
  ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  [ -z "$ip" ] && ip="$(ip route get 1 2>/dev/null | awk '{print $7; exit}' || true)"
  echo ""
  ok "部署完成"
  echo "  本地访问: http://127.0.0.1:${PORT}/"
  [ -n "$ip" ] && echo "  局域网/公网: http://${ip}:${PORT}/ （需安全组/防火墙放行端口）"
  echo "  PM2 管理:"
  echo "    $0 status | logs | restart | stop | update"
  echo "    pm2 monit"
  echo ""
  warn "开机自启（可选，需 root 或 sudo）:"
  echo "    pm2 startup"
  echo "    pm2 save"
  if [ -f "$ENV_FILE" ] && ! grep -qE '^[[:space:]]*LOVART_ACCESS_KEY=.+' "$ENV_FILE"; then
    echo ""
    warn "请在 $ENV_FILE 中配置 LOVART_ACCESS_KEY / LOVART_SECRET_KEY"
  fi
}

cmd_setup() {
  check_python
  setup_venv
  install_deploy_extras
  ensure_env_file
  ensure_runtime_dirs
  write_ecosystem
}

cmd_deploy() {
  info "========== AI 视觉设计助手 · 一键部署 =========="
  ensure_pm2
  cmd_setup
  pm2_start_or_reload
  sleep 2
  health_check
  print_summary
}

cmd_update() {
  info "更新代码与依赖..."
  if [ -d "$ROOT_DIR/.git" ]; then
    local branch="${GIT_BRANCH:-main}"
    git -C "$ROOT_DIR" fetch origin "$branch" 2>/dev/null || git -C "$ROOT_DIR" fetch origin
    git -C "$ROOT_DIR" pull --ff-only origin "$branch" 2>/dev/null || git -C "$ROOT_DIR" pull --ff-only
  else
    warn "非 git 仓库，跳过 git pull"
  fi
  ensure_pm2
  cmd_setup
  pm2_start_or_reload
  sleep 2
  health_check
  ok "更新完成"
}

cmd_start() {
  ensure_pm2
  [ -f "$ECOSYSTEM_FILE" ] || write_ecosystem
  pm2_start_or_reload
  sleep 1
  health_check
}

cmd_stop() {
  need_cmd pm2 || fail "PM2 未安装"
  pm2 stop "$APP_NAME" 2>/dev/null || warn "进程 $APP_NAME 未运行"
  ok "已停止"
}

cmd_restart() {
  ensure_pm2
  pm2 restart "$APP_NAME" --update-env 2>/dev/null || cmd_start
  sleep 2
  health_check
}

cmd_status() {
  if need_cmd pm2; then
    pm2 describe "$APP_NAME" 2>/dev/null || pm2 list
  else
    warn "PM2 未安装"
  fi
  health_check
}

cmd_logs() {
  ensure_pm2
  pm2 logs "$APP_NAME" --lines 100
}

main() {
  local action="${1:-deploy}"
  case "$action" in
    deploy|"")   cmd_deploy ;;
    setup)       cmd_setup; ok "环境就绪（未启动 PM2）" ;;
    start)       cmd_start ;;
    stop)        cmd_stop ;;
    restart)     cmd_restart ;;
    status)      cmd_status ;;
    logs)        cmd_logs ;;
    update)      cmd_update ;;
    remote)      cmd_remote "${2:-deploy}" ;;
    remote-hll)  cmd_remote_hll "${2:-deploy}" ;;
    -h|--help|help) usage ;;
    *)           fail "未知命令: $action"; usage ;;
  esac
}

main "${1:-deploy}"
