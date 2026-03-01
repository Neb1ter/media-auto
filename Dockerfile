# 使用官方 Python 镜像
FROM python:3.11-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# 默认数据目录（Railway Volume 挂载点），可通过环境变量覆盖
ENV DATA_DIR=/data

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

# 创建持久化数据目录（Railway Volume 会挂载到此路径）
# 容器首次启动时若 Volume 未挂载，此目录作为回退存储
RUN mkdir -p /data && chmod 777 /data

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "start.py"]
