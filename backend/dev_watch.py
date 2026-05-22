#!/usr/bin/env python3
"""开发模式：监听 backend 代码变更并自动重启 app.py。"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
PYTHON_BIN = BACKEND_DIR / ".venv" / "bin" / "python3"
APP_SCRIPT = BACKEND_DIR / "app.py"
PID_FILE = ROOT_DIR / ".dev-server.pid"
WATCH_EXTENSIONS = {".py", ".html", ".css", ".js"}
IGNORE_NAMES = {".venv", "__pycache__", ".git"}
TEMPLATE_DIR = BACKEND_DIR / "templates"


def _should_watch(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix not in WATCH_EXTENSIONS:
        return False
    return not any(part in IGNORE_NAMES for part in path.parts)


class AppReloader(FileSystemEventHandler):
    def __init__(self):
        self.proc = None
        self._restarting = False

    def start_app(self):
        env = os.environ.copy()
        env.setdefault("DEV_RELOAD", "1")
        self.proc = subprocess.Popen(
            [str(PYTHON_BIN), str(APP_SCRIPT)],
            cwd=str(BACKEND_DIR),
            env=env,
        )
        print(f"[dev] 已启动 app.py (pid={self.proc.pid})，DEV_RELOAD=1（改 HTML 刷新即可）")

    def stop_app(self):
        if not self.proc or self.proc.poll() is not None:
            return
        print(f"[dev] 正在重启 (终止 pid={self.proc.pid})...")
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)
        self.proc = None

    def restart(self, reason: str):
        if self._restarting:
            return
        self._restarting = True
        try:
            print(f"[dev] 检测到变更: {reason}")
            self.stop_app()
            time.sleep(0.3)
            self.start_app()
        finally:
            self._restarting = False

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not _should_watch(path):
            return
        rel = path.relative_to(BACKEND_DIR)
        if path.suffix == ".py":
            self.restart(str(rel))
        elif TEMPLATE_DIR in path.parents or path.parent == TEMPLATE_DIR:
            print(f"[dev] 前端文件已更新: {rel} → 浏览器刷新即可（无需重启）")

    def on_created(self, event):
        self.on_modified(event)


def main():
    if not PYTHON_BIN.is_file():
        print("请先执行 ./start.sh 或 ./dev.sh 创建虚拟环境", file=sys.stderr)
        sys.exit(1)

    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    reloader = AppReloader()
    reloader.start_app()

    observer = Observer()
    observer.schedule(reloader, str(BACKEND_DIR), recursive=True)
    observer.start()
    print(f"[dev] 监听 {BACKEND_DIR} 下 *.py 变更，Ctrl+C 停止")

    try:
        while True:
            if reloader.proc and reloader.proc.poll() is not None:
                code = reloader.proc.returncode
                print(f"[dev] app.py 已退出 (code={code})，3 秒后重新拉起...")
                time.sleep(3)
                reloader.start_app()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[dev] 停止中...")
    finally:
        observer.stop()
        observer.join()
        reloader.stop_app()
        if PID_FILE.exists():
            PID_FILE.unlink()


if __name__ == "__main__":
    main()
