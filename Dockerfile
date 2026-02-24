FROM python:3.11-slim
WORKDIR /app
# 安装所有需要的依赖
# 增加 python-jose 和 passlib 用于加密和 Token 生成
RUN pip install --no-cache-dir fastapi uvicorn sqlalchemy asyncpg python-jose[cryptography] passlib[bcrypt]
RUN pip install --no-cache-dir fastapi uvicorn sqlalchemy asyncpg psutil httpx
# 拷贝所有文件 (main.py, index.html)
COPY . .
# 启动
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

