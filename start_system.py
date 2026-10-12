"""一次启动目录、三个门户及总入口；数据库密码只在运行时使用。"""

import getpass
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
SERVICES = [("app.py", "CATALOG_PORT", 8000), ("student/server.py", "STUDENT_PORT", 8020),
            ("teacher/server.py", "TEACHER_PORT", 8030), ("administer/server.py", "ADMINISTER_PORT", 8040),
            ("portal.py", "PORTAL_PORT", 8010)]


def main():
    environment = os.environ.copy()
    if not environment.get("MYSQL_PASSWORD"):
        environment["MYSQL_PASSWORD"] = getpass.getpass("请输入 MySQL 数据库密码：")
    children = []
    try:
        for script, variable, default_port in SERVICES:
            port = int(environment.get(variable, default_port))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.3):
                    print(f"端口 {port} 已运行，沿用已有服务。", flush=True)
                    continue
            except OSError:
                pass
            children.append(subprocess.Popen([sys.executable, "-B", "-u", str(ROOT / script)],
                                             cwd=ROOT, env=environment))
        environment.pop("MYSQL_PASSWORD", None)
        print("总入口：http://127.0.0.1:" + str(os.environ.get("PORTAL_PORT", "8010")), flush=True)
        print("按 Ctrl+C 停止本次启动的服务。", flush=True)
        while children:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("有服务启动失败或已经退出，请检查上方日志及端口占用。")
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
