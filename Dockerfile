FROM python:3.11-slim

WORKDIR /app

# 🛡️ 给容器发个发报机（安装 docker 客户端）
RUN apt-get update && apt-get install -y docker.io && rm -rf /var/lib/apt/lists/*

# 安装依赖
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    sqlalchemy \
    asyncpg \
    python-jose[cryptography] \
    passlib[bcrypt] \
    psutil \
    httpx \
    python-multipart

# 拷贝项目文件
COPY . .

# 启动服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
