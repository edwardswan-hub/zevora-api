FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn sqlalchemy asyncpg
COPY main.py /app/main.py
CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
