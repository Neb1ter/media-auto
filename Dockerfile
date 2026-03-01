# 使用官方 Python 镜像
FROM python:3.11-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 创建并设置工作目录
WORKDIR /app

# 安装系统依赖（用于 Playwright）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Playwright 浏览器依赖
RUN playwright install --with-deps chromium

# 复制项目代码
COPY . .

# 创建数据库目录
RUN mkdir -p /app/db

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "start.py"]
