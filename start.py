"""
自媒体运营自动化平台 - 启动脚本
"""
import os
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent
BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIR = BASE_DIR / "frontend"

# 将 backend 目录加入路径
sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

# 导入后端 app
sys.path.insert(0, str(BACKEND_DIR))
from main import app

# 挂载前端静态文件
@app.get("/ui")
@app.get("/ui/{path:path}")
async def serve_frontend(path: str = ""):
    index_file = FRONTEND_DIR / "index.html"
    return FileResponse(str(index_file))

# 根路径重定向到 UI
from fastapi.responses import RedirectResponse

@app.get("/web")
async def web_ui():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("🚀 自媒体运营自动化平台启动中...")
    print("=" * 60)
    print(f"📁 工作目录: {BASE_DIR}")
    print(f"🌐 访问地址: http://0.0.0.0:8000")
    print(f"📖 API 文档: http://0.0.0.0:8000/docs")
    print(f"🖥️  Web 界面: http://0.0.0.0:8000/web")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False, log_level="info")
