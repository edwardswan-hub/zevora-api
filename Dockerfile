FROM python:3.11-slim

WORKDIR /app

# 一次性装好，减少 layer
RUN pip install --no-cache-dir fastapi uvicorn sqlalchemy asyncpg

# 把当前目录下所有东西（含 index.html）都拷进去
COPY . .

# 必须监听 0.0.0.0，否则宿主机 Nginx 找不着它
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
