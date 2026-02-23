from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import os, httpx, psutil

DATABASE_URL = os.getenv("DATABASE_URL")
AI_API_KEY = "sk-umjtlylioalvwivtwmfuewigndyxgdyrullstjuytotprbfj"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"))

# 1. 消息列表
@app.get("/api/messages")
async def get_messages():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT * FROM messages ORDER BY id DESC LIMIT 10"))
        return {"items": result.mappings().all()}

# 2. AI 聊天 (SiliconFlow)
@app.post("/api/ai")
async def ask_ai(data: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={
                "model": "Qwen/Qwen2.5-7B-Instruct", # Qwen3 可能还没全量，先用 2.5 稳一手
                "messages": [{"role": "user", "content": data['prompt'] + " (请用极简、拽拽的GenZ语气回答)"}]
            }
        )
        return response.json()

# 3. 极简监控
@app.get("/api/stats")
async def get_stats():
    return {
        "cpu": f"{psutil.cpu_percent()}%",
        "ram": f"{psutil.virtual_memory().percent}%",
        "disk": f"{psutil.disk_usage('/').percent}%"
    }

@app.get("/")
async def root():
    return FileResponse("index.html")
