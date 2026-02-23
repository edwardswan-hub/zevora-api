import os
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_async_engine(DB_URL, pool_pre_ping=True)

app = FastAPI()

@app.get("/health")
async def health():
    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT 1 as ok;"))
        return {"ok": True, "db": ..., "from": "local-dev"}

@app.post("/notes")
async def create_note(msg: str):
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS notes (
                id SERIAL PRIMARY KEY,
                msg TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """))
        await conn.execute(text("INSERT INTO notes (msg) VALUES (:msg);"), {"msg": msg})
    return {"saved": True}

@app.get("/notes")
async def list_notes():
    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT id, msg, created_at FROM notes ORDER BY id DESC LIMIT 20;"))
        return {"items": [dict(x) for x in r.mappings().all()]}
