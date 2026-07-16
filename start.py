"""Zenith v2 — Main Entry Point
Launches the FastAPI backend server and opens the default browser.
"""

import sys
import os
import logging
import webbrowser
import threading
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# 确保项目根目录在 sys.path
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    url = f"http://localhost:{port}"

    # 首次运行检查：无 config.yaml 则从模板创建
    config_file = PROJECT_DIR / "config" / "config.yaml"
    config_example = PROJECT_DIR / "config" / "config.yaml.example"
    is_first_run = not config_file.exists()
    if is_first_run and config_example.exists():
        import shutil
        shutil.copy(config_example, config_file)
        print("=" * 60)
        print("  ✨ 首次运行 — 已创建 config.yaml")
        print("  ⚠️  请编辑 config.yaml 填入你的 API Key")
        print("  配置引导页面将在浏览器中打开")
        print("=" * 60)
        print()

    print("=" * 60)
    print("  Zenith v2 — 本地智能助手")
    print("=" * 60)
    print(f"  后端地址: {url}")
    print(f"  API 文档: {url}/docs")
    print()
    print("  数据完全存储在本地，不会上传到任何云端服务器。")
    print("  你的 API Key 和对话数据仅保存在本机。")
    print("=" * 60)
    print()

    # 延迟 2 秒后自动打开默认浏览器
    def _open_browser():
        import time
        time.sleep(2)
        webbrowser.open(url)
        print(f"  >> 已打开默认浏览器: {url}")

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
