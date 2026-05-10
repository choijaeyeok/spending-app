import threading
import time
import subprocess
import sys
import os
import socket
import traceback

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.log")

def log(msg):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")


def is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=1):
            return True
    except OSError:
        return False


def run_streamlit():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log("Streamlit 시작 중...")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run",
         os.path.join(script_dir, "app.py"),
         "--server.headless=true",
         "--server.port=8501",
         "--server.address=localhost"],
        cwd=script_dir,
    )


if __name__ == "__main__":
    try:
        log("launcher 시작")
        import webview
        log("webview import 성공")

        t = threading.Thread(target=run_streamlit, daemon=True)
        t.start()

        log("포트 대기 중...")
        for _ in range(60):
            if is_port_open(8501):
                break
            time.sleep(0.5)

        if is_port_open(8501):
            log("포트 8501 열림 - 창 생성 시작")
            webview.create_window(
                "자취생 소비 관리",
                "http://localhost:8501",
                width=1280,
                height=800,
                min_size=(800, 600),
            )
            webview.start()
            log("webview 종료")
        else:
            log("오류: 포트 8501이 열리지 않음")
    except Exception as e:
        log(f"오류 발생: {e}\n{traceback.format_exc()}")
