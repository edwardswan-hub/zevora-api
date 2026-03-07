import json
import os
import secrets
import string
import subprocess
from datetime import datetime, timedelta
from typing import Optional

import httpx
import psutil
from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# --- 基础配置 ---
SECRET_KEY = os.getenv("SECRET_KEY", "15884417321aaaaA")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./zevora.db")

# 用户指定管理员账号
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "Julian")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "15884417321aa")

# 用户指定 SiliconFlow 参数（仍支持环境变量覆盖）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-umjtlylioalvwivtwmfuewigndyxgdyrullstjuytotprbfj")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "Qwen/Qwen3-8B")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token", auto_error=False)

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI(title="Zevora API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SaveRequest(BaseModel):
    filename: str
    content: str


class AIRequest(BaseModel):
    prompt: str


class VisitorCreateRequest(BaseModel):
    username: str
    password: Optional[str] = None


class RegisterRequest(BaseModel):
    username: str
    password: str


class SiteSettingsUpdateRequest(BaseModel):
    allow_guest_ai: Optional[bool] = None
    allow_guest_messages: Optional[bool] = None


def hash_password(password: str) -> str:
    safe_password = password[:72] if len(password) > 72 else password
    return pwd_context.hash(safe_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    safe_password = plain_password[:72] if len(plain_password) > 72 else plain_password
    try:
        return pwd_context.verify(safe_password, hashed_password)
    except Exception:
        return False


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def random_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def get_site_settings(session: AsyncSession) -> dict:
    result = await session.execute(text("SELECT value FROM site_settings WHERE key='global'"))
    row = result.fetchone()
    if not row:
        settings = {"allow_guest_ai": False, "allow_guest_messages": False}
        await session.execute(
            text("INSERT INTO site_settings (key, value) VALUES ('global', :value)"),
            {"value": json.dumps(settings)},
        )
        await session.commit()
        return settings
    return json.loads(row[0])


async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: Optional[str] = payload.get("sub")
        role: Optional[str] = payload.get("role")
        if not username or not role:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"username": username, "role": role}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_optional_user(token: str = Depends(optional_oauth2_scheme)):
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role")
        if not username or not role:
            return None
        return {"username": username, "role": role}
    except JWTError:
        return None


def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return current_user


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    role TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'self',
                    created_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    content TEXT NOT NULL,
                    author TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS site_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
        )

        # 兼容老表结构
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN source TEXT NOT NULL DEFAULT 'self'"))
        except Exception:
            pass
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"))
        except Exception:
            pass

    async with AsyncSessionLocal() as session:
        existing_admin = await session.execute(text("SELECT id FROM users WHERE username=:username"), {"username": ADMIN_USERNAME})
        if not existing_admin.fetchone():
            await session.execute(
                text("INSERT INTO users (username, hashed_password, role, enabled, source, created_at) VALUES (:u, :p, 'admin', 1, 'system', :c)"),
                {"u": ADMIN_USERNAME, "p": hash_password(ADMIN_PASSWORD), "c": datetime.utcnow().isoformat()},
            )
            await session.commit()
        else:
            # 确保管理员密码符合用户当前要求
            await session.execute(
                text("UPDATE users SET hashed_password=:p, role='admin', enabled=1 WHERE username=:u"),
                {"u": ADMIN_USERNAME, "p": hash_password(ADMIN_PASSWORD)},
            )
            await session.commit()

        await get_site_settings(session)


@app.post("/api/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT username, hashed_password, role, enabled FROM users WHERE username=:username"),
            {"username": form_data.username},
        )
        user = result.fetchone()
        if not user:
            raise HTTPException(status_code=400, detail="Incorrect credentials")

        username, hashed_password, role, enabled = user
        if not enabled or not verify_password(form_data.password, hashed_password):
            raise HTTPException(status_code=400, detail="Incorrect credentials")

    token = create_access_token(data={"sub": username, "role": role})
    return {"access_token": token, "token_type": "bearer", "role": role, "username": username}


@app.post("/api/register")
async def register(data: RegisterRequest):
    username = data.username.strip()
    if len(username) < 3 or len(data.password) < 6:
        raise HTTPException(status_code=400, detail="Username min 3 chars, password min 6 chars")

    if username.lower() == ADMIN_USERNAME.lower():
        raise HTTPException(status_code=400, detail="Cannot register as admin username")

    async with AsyncSessionLocal() as session:
        exists = await session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username})
        if exists.fetchone():
            raise HTTPException(status_code=400, detail="Username already exists")

        await session.execute(
            text("INSERT INTO users (username, hashed_password, role, enabled, source, created_at) VALUES (:u, :p, 'visitor', 1, 'self', :c)"),
            {"u": username, "p": hash_password(data.password), "c": datetime.utcnow().isoformat()},
        )
        await session.commit()

    return {"success": True, "username": username, "role": "visitor"}


@app.get("/api/messages")
async def get_messages():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT * FROM messages ORDER BY id DESC LIMIT 100"))
        return {"items": result.mappings().all()}


@app.post("/api/messages")
async def create_message(content: str = Body(..., embed=True), current_user: Optional[dict] = Depends(get_optional_user)):
    async with AsyncSessionLocal() as session:
        settings = await get_site_settings(session)

        if current_user is None and not settings.get("allow_guest_messages", False):
            raise HTTPException(status_code=403, detail="Guest message is disabled")

        author = current_user["username"] if current_user else "guest"
        await session.execute(
            text("INSERT INTO messages (content, author, created_at) VALUES (:content, :author, :created_at)"),
            {"content": content, "author": author, "created_at": datetime.utcnow().isoformat()},
        )
        await session.commit()
        return {"success": True}


@app.post("/api/ai/chat")
async def ai_chat(req: AIRequest, current_user: Optional[dict] = Depends(get_optional_user)):
    async with AsyncSessionLocal() as session:
        settings = await get_site_settings(session)

    if current_user is None and not settings.get("allow_guest_ai", False):
        raise HTTPException(status_code=403, detail="Guest AI is disabled")

    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a concise assistant for this personal site."},
            {"role": "user", "content": req.prompt},
        ],
        "temperature": 0.5,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{OPENAI_BASE_URL}/chat/completions", headers=headers, json=payload)

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"AI upstream error: {response.text}")

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return {"reply": content}


@app.get("/api/admin/overview")
async def admin_overview(current_user: dict = Depends(require_admin)):
    async with AsyncSessionLocal() as session:
        user_count = (await session.execute(text("SELECT COUNT(*) FROM users"))).scalar()
        visitor_count = (await session.execute(text("SELECT COUNT(*) FROM users WHERE role='visitor'"))).scalar()
        msg_count = (await session.execute(text("SELECT COUNT(*) FROM messages"))).scalar()
        settings = await get_site_settings(session)

    return {
        "admin": current_user["username"],
        "users": user_count,
        "visitors": visitor_count,
        "messages": msg_count,
        "settings": settings,
    }


@app.post("/api/admin/visitors")
async def create_visitor(data: VisitorCreateRequest, current_user: dict = Depends(require_admin)):
    username = data.username.strip()
    if username.lower() == ADMIN_USERNAME.lower():
        raise HTTPException(status_code=400, detail="Cannot create visitor with admin username")

    password = data.password.strip() if data.password else random_password()
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password min 6 chars")

    async with AsyncSessionLocal() as session:
        exists = await session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username})
        if exists.fetchone():
            raise HTTPException(status_code=400, detail="Username already exists")

        await session.execute(
            text("INSERT INTO users (username, hashed_password, role, enabled, source, created_at) VALUES (:u, :p, 'visitor', 1, 'invited', :c)"),
            {"u": username, "p": hash_password(password), "c": datetime.utcnow().isoformat()},
        )
        await session.commit()

    return {
        "username": username,
        "password": password,
        "role": "visitor",
        "source": "invited",
        "created_by": current_user["username"],
    }


@app.get("/api/admin/visitors")
async def list_visitors(current_user: dict = Depends(require_admin)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT username, role, enabled, source, created_at FROM users WHERE role='visitor' ORDER BY created_at DESC, username")
        )
        items = [dict(row._mapping) for row in result]
    return {"items": items}


@app.patch("/api/admin/settings")
async def update_settings(data: SiteSettingsUpdateRequest, current_user: dict = Depends(require_admin)):
    async with AsyncSessionLocal() as session:
        settings = await get_site_settings(session)

        if data.allow_guest_ai is not None:
            settings["allow_guest_ai"] = data.allow_guest_ai
        if data.allow_guest_messages is not None:
            settings["allow_guest_messages"] = data.allow_guest_messages

        await session.execute(
            text("UPDATE site_settings SET value=:value WHERE key='global'"),
            {"value": json.dumps(settings)},
        )
        await session.commit()

    return {"success": True, "settings": settings}


@app.get("/api/sys/stats")
async def get_sys_stats(current_user: dict = Depends(require_admin)):
    return {
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "uptime": subprocess.getoutput("uptime -p"),
        "docker": subprocess.getoutput("docker ps --format '{{.Names}}: {{.Status}}'"),
    }


@app.get("/api/editor/read")
async def read_code(filename: str, current_user: dict = Depends(require_admin)):
    path = os.path.join("/app", filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    return {"error": "File not found"}


@app.post("/api/editor/save")
async def save_code(data: SaveRequest, current_user: dict = Depends(require_admin)):
    path = os.path.join("/app", data.filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data.content)
    return {"success": True}


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()
