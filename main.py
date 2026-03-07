import json
import os
import secrets
import string
import subprocess
from datetime import datetime, timedelta
from typing import Optional

import urllib.error
import urllib.request
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

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "Julian")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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


# --- 模型 ---
class SaveRequest(BaseModel):
    filename: str
    content: str


class AIRequest(BaseModel):
    prompt: str


class VisitorCreateRequest(BaseModel):
    username: str


class SiteSettingsUpdateRequest(BaseModel):
    allow_guest_ai: Optional[bool] = None
    allow_guest_messages: Optional[bool] = None


class RegisterRequest(BaseModel):
    username: str
    password: str


# --- 工具函数 ---
def assert_no_merge_markers(paths: list[str]):
    marker_prefixes = ("<<<<<<< ", "=======", ">>>>>>> ")
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if any(line.startswith(prefix) for prefix in marker_prefixes):
                    raise RuntimeError(f"Merge markers detected in {path}:{lineno}")


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


# --- 初始化 ---
@app.on_event("startup")
async def startup():
    assert_no_merge_markers(["main.py", "index.html"])
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    role TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
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

    async with AsyncSessionLocal() as session:
        existing_admin = await session.execute(text("SELECT id FROM users WHERE username=:username"), {"username": ADMIN_USERNAME})
        if not existing_admin.fetchone():
            await session.execute(
                text("INSERT INTO users (username, hashed_password, role, enabled) VALUES (:u, :p, 'admin', 1)"),
                {"u": ADMIN_USERNAME, "p": hash_password(ADMIN_PASSWORD)},
            )
            await session.commit()

        await get_site_settings(session)


# --- 认证 ---
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
    password = data.password

    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if username.lower() == ADMIN_USERNAME.lower():
        raise HTTPException(status_code=400, detail="Reserved username")

    async with AsyncSessionLocal() as session:
        exists = await session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username})
        if exists.fetchone():
            raise HTTPException(status_code=400, detail="Username already exists")

        await session.execute(
            text("INSERT INTO users (username, hashed_password, role, enabled) VALUES (:u, :p, 'visitor', 1)"),
            {"u": username, "p": hash_password(password)},
        )
        await session.commit()

    return {"success": True, "username": username, "role": "visitor"}


# --- 留言系统 ---
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


# --- AI 聊天接口 ---
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

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{OPENAI_BASE_URL}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"AI upstream error: {error_body}")
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"AI upstream unavailable: {exc.reason}")

    data = json.loads(raw)
    content = data["choices"][0]["message"]["content"]
    return {"reply": content}


# --- Admin 通道 ---
@app.get("/api/admin/overview")
async def admin_overview(current_user: dict = Depends(require_admin)):
    async with AsyncSessionLocal() as session:
        user_count = (await session.execute(text("SELECT COUNT(*) FROM users"))).scalar()
        msg_count = (await session.execute(text("SELECT COUNT(*) FROM messages"))).scalar()
        settings = await get_site_settings(session)

    return {
        "admin": current_user["username"],
        "users": user_count,
        "messages": msg_count,
        "settings": settings,
    }


@app.post("/api/admin/visitors")
async def create_visitor(data: VisitorCreateRequest, current_user: dict = Depends(require_admin)):
    password = random_password()

    async with AsyncSessionLocal() as session:
        exists = await session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": data.username})
        if exists.fetchone():
            raise HTTPException(status_code=400, detail="Username already exists")

        await session.execute(
            text("INSERT INTO users (username, hashed_password, role, enabled) VALUES (:u, :p, 'visitor', 1)"),
            {"u": data.username, "p": hash_password(password)},
        )
        await session.commit()

    return {"username": data.username, "password": password, "role": "visitor", "created_by": current_user["username"]}


@app.get("/api/admin/visitors")
async def list_visitors(current_user: dict = Depends(require_admin)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT username, role, enabled FROM users WHERE role='visitor' ORDER BY username")
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


# --- 旧功能兼容：运维/编辑器 ---
def _memory_used_percent() -> float:
    total_kb = 0
    available_kb = 0
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                available_kb = int(line.split()[1])
            if total_kb and available_kb:
                break

    if total_kb == 0:
        return 0.0
    return round((total_kb - available_kb) * 100 / total_kb, 2)


@app.get("/api/sys/stats")
async def get_sys_stats(current_user: dict = Depends(require_admin)):
    load1, _, _ = os.getloadavg()
    cpu_estimate = round((load1 / max(os.cpu_count() or 1, 1)) * 100, 2)
    return {
        "cpu": cpu_estimate,
        "ram": _memory_used_percent(),
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
