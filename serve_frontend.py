"""
前端静态文件服务 (端口 8080)
"""
import http.server
import socketserver
import os

PORT = 8080
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)
    
    def log_message(self, format, *args):
        pass  # 静默日志

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"🌐 前端服务运行在 http://0.0.0.0:{PORT}")
        httpd.serve_forever()
