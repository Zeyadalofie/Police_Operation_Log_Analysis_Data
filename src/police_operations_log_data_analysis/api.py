"""FastAPI backend for the personal operations analysis dashboard."""
from __future__ import annotations

import hashlib, hmac, json, os, re, secrets, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from .connection import ai_config_for_user, public_connection_details
from .storage import drive_file_metadata, s3_report_key
from .upload import read_dataset, summarize

ROOT = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

DB_PATH = Path(os.getenv("AUTH_DATABASE", str(ROOT / "users.sqlite3")))
app = FastAPI(title="مرصد API", version="1.0")
origins = list(dict.fromkeys([x.strip() for x in os.getenv("FRONTEND_ORIGINS", "").split(",") if x.strip()] + ["http://localhost:5173", "http://127.0.0.1:5173"]))
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "development-only-change-this"), same_site=os.getenv("SESSION_SAME_SITE", "lax"), https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true")

def db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE COLLATE NOCASE, name TEXT NOT NULL, password_hash TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS social_accounts(provider TEXT NOT NULL, subject TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id), PRIMARY KEY(provider, subject));
        CREATE TABLE IF NOT EXISTS connections(user_id INTEGER PRIMARY KEY, ai TEXT, drive TEXT, s3 TEXT, active_storage TEXT DEFAULT 'local');
        CREATE TABLE IF NOT EXISTS reports(user_id INTEGER PRIMARY KEY, data TEXT NOT NULL, filename TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)

init_db()

oauth = OAuth()
if os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"):
    oauth.register(name="google", client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID"), client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"), server_metadata_url="https://accounts.google.com/.well-known/openid-configuration", client_kwargs={"scope":"openid email profile"})
if os.getenv("APPLE_CLIENT_ID") and os.getenv("APPLE_CLIENT_SECRET"):
    oauth.register(name="apple", client_id=os.getenv("APPLE_CLIENT_ID"), client_secret=os.getenv("APPLE_CLIENT_SECRET"), server_metadata_url="https://appleid.apple.com/.well-known/openid-configuration", client_kwargs={"scope":"name email","token_endpoint_auth_method":"client_secret_post"})

def cipher():
    key = os.getenv("CREDENTIALS_ENCRYPTION_KEY", "")
    if not key: raise HTTPException(503, "أضف CREDENTIALS_ENCRYPTION_KEY إلى ملف .env")
    try: return Fernet(key.encode())
    except Exception: raise HTTPException(503, "مفتاح تشفير بيانات الاتصال غير صالح")

def seal(value: Any): return cipher().encrypt(json.dumps(value).encode()).decode() if value else None
def open_secret(value: str | None):
    if not value: return None
    try: return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError): raise HTTPException(500, "تعذر فك تشفير بيانات الاتصال")

def current_user(request: Request):
    uid = request.session.get("uid") or request.session.get("user_id")
    if not uid: raise HTTPException(401, "سجّل الدخول أولاً")
    with db() as c: user = c.execute("SELECT id,email,name FROM users WHERE id=?", (uid,)).fetchone()
    if not user: request.session.clear(); raise HTTPException(401, "الجلسة غير صالحة")
    return dict(user)

def password_hash(password: str):
    if len(password) < 8: raise HTTPException(400, "كلمة المرور يجب أن تكون 8 أحرف على الأقل")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return salt.hex() + ":" + digest.hex()

def verify_password(password: str, stored: str):
    salt_hex, digest = stored.split(":", 1)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000).hex()
    return hmac.compare_digest(actual, digest)

class Credentials(BaseModel): email: str; password: str; name: str = ""
class AIConfig(BaseModel): provider: str; base_url: str = ""; model: str; api_key: str = ""
class S3Config(BaseModel): endpoint_url: str; bucket: str; region: str = "us-east-1"; access_key: str; secret_key: str
class ActiveStorage(BaseModel): provider: str
class ChartRequest(BaseModel): kind: str; category: str; metric: str = "count"; prompt: str = ""

@app.get("/api/health")
def health(): return {"ok": True, "service": "مرصد API"}

@app.post("/api/auth/register")
def register(body: Credentials, request: Request):
    email = body.email.strip().lower()
    if "@" not in email: raise HTTPException(400, "أدخل بريداً إلكترونياً صحيحاً")
    try:
        with db() as c:
            cur = c.execute("INSERT INTO users(email,name,password_hash,created_at) VALUES(?,?,?,?)", (email, body.name.strip() or email.split("@")[0], password_hash(body.password), datetime.now(timezone.utc).isoformat()))
            uid = cur.lastrowid; c.execute("INSERT OR IGNORE INTO connections(user_id) VALUES(?)", (uid,))
    except sqlite3.IntegrityError: raise HTTPException(409, "هذا البريد مسجل مسبقاً")
    request.session["uid"] = uid; request.session["user_id"] = uid
    return {"id": uid, "email": email, "name": body.name.strip() or email.split("@")[0]}

@app.post("/api/auth/login")
def login(body: Credentials, request: Request):
    with db() as c: row = c.execute("SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)).fetchone()
    if not row or not row["password_hash"] or not verify_password(body.password, row["password_hash"]): raise HTTPException(401, "البريد أو كلمة المرور غير صحيحة")
    request.session["uid"] = row["id"]; request.session["user_id"] = row["id"]
    return {"id": row["id"], "email": row["email"], "name": row["name"]}

@app.get("/api/auth/me")
def me(request: Request): return current_user(request)

@app.post("/api/auth/logout")
def logout(request: Request): request.session.clear(); return {"ok": True}

@app.get("/api/auth/providers")
def providers(): return {"google": bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")), "apple": bool(os.getenv("APPLE_CLIENT_ID") and os.getenv("APPLE_CLIENT_SECRET"))}

def social_user(email: str, name: str, request: Request):
    email=email.strip().lower()
    if not email or "@" not in email: raise HTTPException(400,"لم يرسل مزود تسجيل الدخول بريداً إلكترونياً صالحاً")
    with db() as c:
        row=c.execute("SELECT id,email,name FROM users WHERE email=?",(email,)).fetchone()
        if row: uid=row["id"]
        else:
            cur=c.execute("INSERT INTO users(email,name,password_hash,created_at) VALUES(?,?,NULL,?)",(email,name.strip() or email.split("@")[0],datetime.now(timezone.utc).isoformat())); uid=cur.lastrowid
        c.execute("INSERT OR IGNORE INTO connections(user_id) VALUES(?)",(uid,))
        row=c.execute("SELECT id,email,name FROM users WHERE id=?",(uid,)).fetchone()
    request.session.clear(); request.session["uid"]=uid; request.session["user_id"]=uid
    return dict(row)

@app.get("/api/auth/google/start")
async def google_login_start(request: Request):
    if not (os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")): raise HTTPException(503,"تسجيل Google يحتاج إعداد OAuth في ملف .env")
    return await oauth.google.authorize_redirect(request,os.getenv("GOOGLE_LOGIN_REDIRECT_URI","http://localhost:8000/api/auth/google/callback"))

@app.get("/api/auth/google/callback")
async def google_login_callback(request: Request):
    if not (os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")): raise HTTPException(503,"تسجيل Google غير مفعّل")
    token=await oauth.google.authorize_access_token(request); info=token.get("userinfo") or await oauth.google.userinfo(token=token)
    social_user(info.get("email",""),info.get("name",info.get("email","")),request)
    return RedirectResponse(os.getenv("FRONTEND_URL","http://localhost:5173"))

@app.get("/api/auth/apple/start")
async def apple_login_start(request: Request):
    if not (os.getenv("APPLE_CLIENT_ID") and os.getenv("APPLE_CLIENT_SECRET")): raise HTTPException(503,"تسجيل Apple يحتاج إعداد OAuth في ملف .env")
    return await oauth.apple.authorize_redirect(request,os.getenv("APPLE_LOGIN_REDIRECT_URI","http://localhost:8000/api/auth/apple/callback"),response_mode="form_post")

@app.post("/api/auth/apple/callback")
async def apple_login_callback(request: Request):
    if not (os.getenv("APPLE_CLIENT_ID") and os.getenv("APPLE_CLIENT_SECRET")): raise HTTPException(503,"تسجيل Apple غير مفعّل")
    token=await oauth.apple.authorize_access_token(request); info=token.get("userinfo") or {}
    social_user(info.get("email",""),info.get("name",{}).get("firstName","") if isinstance(info.get("name"),dict) else info.get("email",""),request)
    return RedirectResponse(os.getenv("FRONTEND_URL","http://localhost:5173"))

def save_report(user_id, filename, rows):
    with db() as c: c.execute("INSERT INTO reports(user_id,data,filename,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data,filename=excluded.filename,updated_at=excluded.updated_at", (user_id,json.dumps(rows,ensure_ascii=False,default=str),filename,datetime.now(timezone.utc).isoformat()))

@app.post("/api/data/upload")
async def upload(request: Request, file: UploadFile=File(...)):
    user=current_user(request); rows=read_dataset(file.filename or "",await file.read()); save_report(user["id"],file.filename or "data",rows)
    return {"filename":file.filename,"summary":summarize(rows)}

@app.get("/api/data/current")
def current_data(request: Request):
    user=current_user(request)
    with db() as c: row=c.execute("SELECT * FROM reports WHERE user_id=?",(user["id"],)).fetchone()
    if not row: return {"filename":None,"summary":None}
    return {"filename":row["filename"],"summary":summarize(json.loads(row["data"]))}

@app.post("/api/assistant/chart")
async def chart(body: ChartRequest,request: Request):
    user=current_user(request)
    with db() as c: r=c.execute("SELECT data FROM reports WHERE user_id=?",(user["id"],)).fetchone()
    if not r: raise HTTPException(400,"ارفع ملفاً أولاً")
    rows=json.loads(r["data"]); counts={}
    for row in rows:
        key=str(row.get(body.category,"غير محدد"))
        if body.metric=="count": counts[key]=counts.get(key,0)+1
        else:
            try: counts[key]=counts.get(key,0)+float(str(row.get(body.metric,0)).replace(",",""))
            except (ValueError,TypeError): pass
    values=sorted(counts.items(),key=lambda x:-x[1])[:30]
    title="تحليل "+body.category
    return {"title":title,"type":"pie" if body.kind=="pie" else "bar","category":body.category,"metric":body.metric,"data":[{"name":k,"value":round(v,2)} for k,v in values]}

def get_ai_config(uid):
    return ai_config_for_user(uid, db, open_secret)


@app.post("/api/assistant/summary")
async def ai_summary(request: Request):
    user = current_user(request)
    with db() as c:
        report = c.execute("SELECT data FROM reports WHERE user_id=?", (user["id"],)).fetchone()
    if not report:
        raise HTTPException(400, "ارفع ملف بيانات أولاً")
    config = get_ai_config(user["id"])
    if not config:
        raise HTTPException(409, "اربط نموذج الذكاء الاصطناعي من صفحة التكاملات أولاً")

    summary = summarize(json.loads(report["data"]))
    compact_columns = []
    for column in summary["columns"]:
        item = {key: column[key] for key in ("name", "type", "missing", "unique", "sum", "average") if key in column}
        if column["type"] == "category":
            item["top"] = column["top"][:5]
        compact_columns.append(item)
    prompt = (
        "أنت مساعد تحليل بيانات. اختر من 2 إلى 4 أعمدة فقط تستحق إبرازها في ملخص مبسط. "
        "اعتمد على الإحصاءات المجمعة فقط، وراعِ نسبة القيم المفقودة والتنوع والقيم المتكررة. "
        "تجنب أعمدة المعرّفات والرموز الفريدة مثل operation_id؛ لا تصلح للرسم. "
        "اختر chart_column من فئة وصفية مثل المدينة أو الحالة أو النوع، فيها قيم متكررة فعلاً. استبعد التاريخ والوقت والمعرّفات والرموز، ولا تختر عموداً تتكرر فيه كل قيمة مرة واحدة. "
        "لا تخترع أرقاماً ولا تعِد كتابة البيانات. أعد JSON صالحاً فقط بهذا الشكل: "
        '{"title":"عنوان عربي قصير","columns":[{"name":"اسم عمود مطابق تماماً","reason":"سبب عربي قصير"}],"chart_column":"اسم عمود تصنيفي"}. '
        "عدد الصفوف: " + str(summary["rows"]) + ". إحصاءات الأعمدة: " + json.dumps(compact_columns, ensure_ascii=False)
    )
    headers = {"Authorization": "Bearer " + (config.get("api_key") or "ollama"), "Content-Type": "application/json"}
    request_body = {
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        # Ollama and most OpenAI-compatible APIs support JSON mode, reducing malformed replies.
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
            response = await client.post(
                config["base_url"].rstrip("/") + "/chat/completions",
                headers=headers,
                json=request_body,
            )
            # Some compatible services do not implement JSON mode; retry without it on a 400.
            if response.status_code == 400:
                request_body.pop("response_format", None)
                response = await client.post(
                    config["base_url"].rstrip("/") + "/chat/completions",
                    headers=headers,
                    json=request_body,
                )
        if not response.is_success:
            raise HTTPException(502, "رفض مزود النموذج الطلب. تحقق من رابط API واسم النموذج وإعداداته")
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise ValueError("محتوى الرد ليس نصاً")
        content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE).strip()
        try:
            spec = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", content)
            if not match:
                raise ValueError("لم يرجع النموذج JSON صالحاً")
            spec = json.loads(match.group(0))
        if not isinstance(spec, dict):
            raise ValueError("صيغة الملخص غير متوقعة")
    except HTTPException:
        raise
    except httpx.ConnectError as exc:
        raise HTTPException(502, "تعذر الوصول إلى النموذج. شغّل Ollama وتأكد أن الرابط مثل http://localhost:11434/v1") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "استغرق النموذج وقتاً طويلاً. تحقق من تشغيل النموذج ثم أعد المحاولة") from exc
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "وصل رد من النموذج لكن تعذر فهمه. تحقق من اسم النموذج ثم أعد المحاولة") from exc
    except Exception as exc:
        raise HTTPException(502, "تعذر إنشاء الملخص الذكي. تحقق من رابط النموذج واتصاله ثم أعد المحاولة") from exc

    by_name = {column["name"]: column for column in summary["columns"]}
    highlights = []
    for selected in spec.get("columns", []):
        if not isinstance(selected, dict):
            continue
        name = selected.get("name")
        if name not in by_name or any(item["column"] == name for item in highlights):
            continue
        column = by_name[name]
        highlights.append({
            "column": name,
            "reason": str(selected.get("reason", "اختاره النموذج ضمن أبرز أعمدة الملف"))[:180],
            "type": column["type"],
            "missing": column["missing"],
            "unique": column["unique"],
            "sum": column.get("sum"),
            "average": column.get("average"),
            "top": column.get("top", [])[:3],
        })
        if len(highlights) == 4:
            break
    if not highlights:
        raise HTTPException(502, "لم يتمكن النموذج من اختيار أعمدة من الملف. أعد المحاولة")

    def usable_chart_column(column):
        name = column["name"].strip().lower()
        identifier = bool(re.search(r"(?:^|[_\-\s])(id|uuid|identifier|code|number|رقم|معرف|رمز)(?:$|[_\-\s])", name) or re.search(r"(?:id|uuid)$", name))
        date_field = bool(re.search(r"(?:^|[_\-\s])(date|time|timestamp|day|month|year|تاريخ|وقت)(?:$|[_\-\s])", name))
        return column["type"] == "category" and not identifier and not date_field and column["unique"] < summary["rows"] and any(value["count"] > 1 for value in column["top"])

    chart_name = spec.get("chart_column")
    chart_column = by_name.get(chart_name) if isinstance(chart_name, str) else None
    if not chart_column or not usable_chart_column(chart_column):
        candidates = [column for column in summary["columns"] if usable_chart_column(column)]
        chart_column = max(candidates, key=lambda column: max((value["count"] for value in column["top"]), default=0)) if candidates else None
    return {"title": str(spec.get("title", "أبرز ما في بياناتك"))[:100], "highlights": highlights, "chart_column": chart_column["name"] if chart_column else None}

@app.get("/api/connections")
def get_connections(request: Request):
    user=current_user(request)
    with db() as c: row=c.execute("SELECT * FROM connections WHERE user_id=?",(user["id"],)).fetchone()
    return public_connection_details(row, open_secret)

@app.put("/api/connections/ai")
def put_ai(body: AIConfig,request: Request):
    uid=current_user(request)["id"]; provider=body.provider.lower()
    if provider not in ("ollama","openai","openai-compatible"): raise HTTPException(400,"نوع مزود الذكاء الاصطناعي غير مدعوم")
    base=body.base_url.strip().rstrip("/") or ("http://localhost:11434/v1" if provider=="ollama" else "https://api.openai.com/v1")
    if not body.model.strip(): raise HTTPException(400,"اكتب اسم النموذج")
    if provider!="ollama" and not body.api_key.strip(): raise HTTPException(400,"مفتاح API مطلوب لهذا المزود")
    value={"provider":provider,"base_url":base,"model":body.model.strip(),"api_key":body.api_key.strip() or "ollama"}
    with db() as c: c.execute("INSERT INTO connections(user_id,ai) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET ai=excluded.ai",(uid,seal(value)))
    return {"ok":True}

@app.delete("/api/connections/ai")
def delete_ai(request: Request):
    uid=current_user(request)["id"]
    with db() as c: c.execute("UPDATE connections SET ai=NULL WHERE user_id=?",(uid,))
    return {"ok":True}

@app.put("/api/connections/s3")
def put_s3(body:S3Config,request:Request):
    uid=current_user(request)["id"]; value=body.model_dump()
    with db() as c: c.execute("INSERT INTO connections(user_id,s3) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET s3=excluded.s3",(uid,seal(value)))
    return {"ok":True}

@app.delete("/api/connections/s3")
def delete_s3(request:Request):
    uid=current_user(request)["id"]
    with db() as c: c.execute("UPDATE connections SET s3=NULL,active_storage=CASE WHEN active_storage='s3' THEN 'local' ELSE active_storage END WHERE user_id=?",(uid,))
    return {"ok":True}

@app.put("/api/connections/storage")
def set_storage(body:ActiveStorage,request:Request):
    uid=current_user(request)["id"]
    if body.provider not in ("local","google_drive","s3"): raise HTTPException(400,"خيار تخزين غير صالح")
    with db() as c:
        row=c.execute("SELECT drive,s3 FROM connections WHERE user_id=?",(uid,)).fetchone()
        if body.provider=="google_drive" and not row["drive"]: raise HTTPException(400,"اربط Google Drive أولاً")
        if body.provider=="s3" and not row["s3"]: raise HTTPException(400,"اربط التخزين السحابي أولاً")
        c.execute("UPDATE connections SET active_storage=? WHERE user_id=?",(body.provider,uid))
    return {"ok":True}

@app.get("/api/connections/google-drive/start")
def drive_start(request:Request):
    uid=current_user(request)["id"]; client=os.getenv("GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_OAUTH_CLIENT_ID"); secret=os.getenv("GOOGLE_CLIENT_SECRET") or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client or not secret: raise HTTPException(503,"أضف بيانات Google OAuth إلى .env")
    redirect=os.getenv("GOOGLE_REDIRECT_URI","http://localhost:8000/api/connections/google-drive/callback"); state=secrets.token_urlsafe(24); request.session["drive_state"]=state; request.session["drive_uid"]=uid
    params={"client_id":client,"redirect_uri":redirect,"response_type":"code","scope":"openid email https://www.googleapis.com/auth/drive.file","access_type":"offline","prompt":"consent","state":state}
    return {"url":"https://accounts.google.com/o/oauth2/v2/auth?"+urlencode(params)}

@app.get("/api/connections/google-drive/callback")
async def drive_callback(request:Request,code:str|None=None,state:str|None=None,error:str|None=None):
    if error or not code or state!=request.session.get("drive_state"): raise HTTPException(400,"تعذر إكمال ربط Google Drive")
    client=os.getenv("GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_OAUTH_CLIENT_ID"); secret=os.getenv("GOOGLE_CLIENT_SECRET") or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"); redirect=os.getenv("GOOGLE_REDIRECT_URI","http://localhost:8000/api/connections/google-drive/callback")
    async with httpx.AsyncClient(timeout=25) as h:
        token=(await h.post("https://oauth2.googleapis.com/token",data={"code":code,"client_id":client,"client_secret":secret,"redirect_uri":redirect,"grant_type":"authorization_code"})).json()
        if "access_token" not in token: raise HTTPException(502,"لم يتمكن Google من إصدار رمز الوصول")
        info=(await h.get("https://www.googleapis.com/oauth2/v2/userinfo",headers={"Authorization":"Bearer "+token["access_token"]})).json()
    uid=request.session.pop("drive_uid",None)
    with db() as c:
        row=c.execute("SELECT drive FROM connections WHERE user_id=?",(uid,)).fetchone(); old=open_secret(row["drive"]) if row and row["drive"] else {}
        value={**(old or {}),"email":info.get("email"),"access_token":token["access_token"],"refresh_token":token.get("refresh_token") or (old or {}).get("refresh_token"),"expires_at":datetime.now(timezone.utc).timestamp()+token.get("expires_in",3600)}
        c.execute("UPDATE connections SET drive=? WHERE user_id=?",(seal(value),uid))
    return RedirectResponse((os.getenv("FRONTEND_URL","http://localhost:5173")+"/#settings?drive=connected"))

async def drive_access_token(creds):
    if creds.get("expires_at",0)>datetime.now(timezone.utc).timestamp()+60: return creds["access_token"],creds
    async with httpx.AsyncClient(timeout=25) as h:
        r=await h.post("https://oauth2.googleapis.com/token",data={"client_id":os.getenv("GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_OAUTH_CLIENT_ID"),"client_secret":os.getenv("GOOGLE_CLIENT_SECRET") or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),"refresh_token":creds.get("refresh_token"),"grant_type":"refresh_token"})
    data=r.json()
    if "access_token" not in data: raise HTTPException(502,"انتهى اتصال Google Drive، أعد الربط")
    creds.update({"access_token":data["access_token"],"expires_at":datetime.now(timezone.utc).timestamp()+data.get("expires_in",3600)})
    return creds["access_token"],creds

@app.delete("/api/connections/google-drive")
def delete_drive(request:Request):
    uid=current_user(request)["id"]
    with db() as c: c.execute("UPDATE connections SET drive=NULL,active_storage=CASE WHEN active_storage='google_drive' THEN 'local' ELSE active_storage END WHERE user_id=?",(uid,))
    return {"ok":True}

@app.post("/api/data/save-cloud")
async def save_cloud(request:Request):
    user=current_user(request)
    with db() as c:
        con=c.execute("SELECT * FROM connections WHERE user_id=?",(user["id"],)).fetchone(); report=c.execute("SELECT filename,data FROM reports WHERE user_id=?",(user["id"],)).fetchone()
    if not report: raise HTTPException(400,"لا توجد بيانات لحفظها")
    if con["active_storage"]=="s3":
        cfg=open_secret(con["s3"])
        if not cfg: raise HTTPException(400,"اربط S3 أولاً")
        import boto3
        client=boto3.client("s3",endpoint_url=cfg["endpoint_url"] or None,region_name=cfg["region"],aws_access_key_id=cfg["access_key"],aws_secret_access_key=cfg["secret_key"])
        key=s3_report_key(user["id"], report["filename"])
        try: client.put_object(Bucket=cfg["bucket"],Key=key,Body=report["data"].encode(),ContentType="application/json")
        except Exception as e: raise HTTPException(502,"فشل رفع الملف إلى S3: "+str(e)[:200])
        return {"ok":True,"provider":"s3","key":key}
    if con["active_storage"]=="google_drive":
        creds=open_secret(con["drive"])
        if not creds: raise HTTPException(400,"اربط Google Drive أولاً")
        token,creds=await drive_access_token(creds)
        with db() as c: c.execute("UPDATE connections SET drive=? WHERE user_id=?",(seal(creds),user["id"]))
        metadata=drive_file_metadata(report["filename"])
        async with httpx.AsyncClient(timeout=45) as h:
            res=await h.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name",headers={"Authorization":"Bearer "+token},files={"metadata":(None,metadata,"application/json"),"file":(Path(report["filename"]).name,report["data"],"application/json")})
        if not res.is_success: raise HTTPException(502,"فشل رفع الملف إلى Google Drive")
        return {"ok":True,"provider":"google_drive",**res.json()}
    raise HTTPException(400,"اختر Google Drive أو S3 كتخزين نشط")

def run():
    import uvicorn
    uvicorn.run("police_operations_log_data_analysis.api:app",host="127.0.0.1",port=8000,reload=True)
