from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles # 备用：如果你以后有 CSS/JS 文件夹
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import os

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

@app.get("/api/messages")
async def get_messages():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT * FROM messages ORDER BY id DESC"))
        rows = result.mappings().all()
        return {"items": rows}

@app.post("/api/messages")
async def create_message(content: str):
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("INSERT INTO messages (content) VALUES (:content)"),
            {"content": content}
        )
        await session.commit()
        return {"success": True}

# 这里的顺序很重要：根路由放在最后
@app.get("/", response_class=HTMLResponse)
async def root():
    # 只要 index.html 在 Docker 里的 /app 目录下就能读到
    return FileResponse("index.html")
