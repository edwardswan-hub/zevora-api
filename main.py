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
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY, 
                title TEXT,
                content TEXT, 
                tags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

# 1. 消息列表 (从 Postgres 读取)
@app.get("/api/messages")
async def get_messages():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id, title, content, tags, created_at FROM messages ORDER BY id DESC"))
        rows = result.mappings().all()
        return [{"id": r['id'], "title": r['title'], "body": r['content'], "date": r['created_at'].strftime("%Y-%m-%d"), "tags": (r['tags'] or "VIBES").split(",")} for r in rows]

# 2. 发表新内容
@app.post("/api/messages")
async def create_message(data: dict):
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("INSERT INTO messages (title, content, tags) VALUES (:title, :content, :tags)"),
            {"title": data['title'], "content": data['body'], "tags": data['tags']}
        )
        await session.commit()
        return {"success": True}

# 3. AI 聊天
@app.post("/api/ai")
async def ask_ai(data: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={
                "model": "Qwen/Qwen2.5-7B-Instruct",
                "messages": [{"role": "system", "content": "你是一个懂Julian、聪明、冷静、俏皮的智能体。你习惯用中英混合回答，风格简短。"},
                             {"role": "user", "content": data['prompt']}]
            }
        )
        return response.json()

# 4. 系统负载
@app.get("/api/stats")
async def get_stats():
    return {"cpu": psutil.cpu_percent(), "ram": psutil.virtual_memory().percent}

@app.get("/")
async def root():
    return FileResponse("index.html")
