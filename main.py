import os
import psutil
import subprocess
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from pydantic import BaseModel

# --- 配置 ---
SECRET_KEY = "15884417321aaaaA" 
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
USERNAME = "Julian"
HASHED_PASSWORD = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGGa31S." 

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# ⚠️ 注意这里：路径统一为 /api/token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token")

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI()

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 工具函数 ---
# 🛡️ 终极防弹版密码验证，超长密码直接截断，防暴毙！
def verify_password(plain_password, hashed_password):
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

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username != USERNAME:
            raise HTTPException(status_code=401)
        return username
    except JWTError:
        raise HTTPException(status_code=401)

# --- 数据库初始化 ---
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, content TEXT NOT NULL)"))

# --- 核心路由 ---

# ⚠️ 登录接口：路径改为 /api/token，匹配前端和 Nginx
@app.post("/api/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username != USERNAME or not verify_password(form_data.password, HASHED_PASSWORD):
        raise HTTPException(status_code=400, detail="Incorrect credentials")
    return {"access_token": create_access_token(data={"sub": form_data.username}), "token_type": "bearer"}

# 获取留言
@app.get("/api/messages")
async def get_messages():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT * FROM messages ORDER BY id DESC"))
        return {"items": result.mappings().all()}

# 发送留言 (受保护)
@app.post("/api/messages")
async def create_message(content: str, current_user: str = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        await session.execute(text("INSERT INTO messages (content) VALUES (:content)"), {"content": content})
        await session.commit()
        return {"success": True}

# --- 控制中心特供接口 ---

# 系统状态监控
@app.get("/api/sys/stats")
async def get_sys_stats(current_user: str = Depends(get_current_user)):
    return {
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "uptime": subprocess.getoutput("uptime -p"),
        "docker": subprocess.getoutput("docker ps --format '{{.Names}}: {{.Status}}'")
    }

# 读取代码
@app.get("/api/editor/read")
async def read_code(filename: str, current_user: str = Depends(get_current_user)):
    path = os.path.join("/app", filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    return {"error": "File not found"}

# 保存代码
class SaveRequest(BaseModel):
    filename: str
    content: str

@app.post("/api/editor/save")
async def save_code(data: SaveRequest, current_user: str = Depends(get_current_user)):
    path = os.path.join("/app", data.filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data.content)
    return {"success": True}

# 根路径返回 HTML
@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()
