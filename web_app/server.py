"""
RinaraDev Automation — Next-Gen Novel Automation Platform
Backend FastAPI Server (Fresh Build)
- Autentikasi Pengguna & Dompet Saldo (Register/Login/Session)
- Auto-inspect metadata novel (cover, author, total chapters)
- Multi-Terminal Bot Orchestrator (1 - 5 Sesi bersamaan via Proxy Pool)
- Real-time SSE Live Console Streaming
- Integrasi QRIS Otomatis (WijayaPay) & Top-up Manual
- Admin Dashboard Panel
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Setup sys.path
BASE_DIR = Path(__file__).parent
STEALTH_DIR = BASE_DIR.parent / "stealth_bot"
for p in [str(BASE_DIR), str(STEALTH_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from database import db_session, get_db_connection, init_database, sync_initial_assets
from proxy_pool import ProxyPoolManager
from account_pool import AccountPoolManager
from auth import AuthManager
from bot_bridge import BotBridge, WebTask, ACTIVE_TASKS
from wijayapay_client import default_wijayapay_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("RinaraServer")

app = FastAPI(title="Rinara Growth Platform", version="5.0")

# In-memory session and rate limiting
ADMIN_SESSIONS: Dict[str, float] = {}
RATE_LIMIT_BUCKET: Dict[str, List[float]] = {}

def get_admin_pin() -> str:
    return os.getenv("ADMIN_PIN", "100401naraA!").strip()

def check_rate_limit(key: str, max_requests: int, window_seconds: float) -> bool:
    now = time.time()
    timestamps = RATE_LIMIT_BUCKET.setdefault(key, [])
    RATE_LIMIT_BUCKET[key] = [t for t in timestamps if now - t < window_seconds]
    if len(RATE_LIMIT_BUCKET[key]) >= max_requests:
        return False
    RATE_LIMIT_BUCKET[key].append(now)
    return True

# Startup lifecycle
@app.on_event("startup")
async def on_startup():
    try:
        init_database()
        sync_initial_assets()
        # Sinkronkan proxy & bebaskan proxy yang sebelumnya tertahan di status BUSY
        from proxy_pool import ProxyPoolManager
        ProxyPoolManager.sync_from_files()
        with db_session() as conn:
            conn.cursor().execute("UPDATE proxies SET status = 'IDLE', active_task_id = NULL, active_worker_id = NULL WHERE status = 'BUSY';")
        logger.info("Server RinaraDev siap! Database dan aset bot telah disinkronkan.")
    except Exception as e:
        logger.error(f"Error sinkronisasi database: {e}")

# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Mount Static Files
static_dir = BASE_DIR / "static"
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

RATE_PER_VALID_READER = 450  # Rp 450 per pembaca valid tamat 25 bab


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================
class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=24)
    email: str = Field(..., min_length=5, max_length=120)
    password: str = Field(..., min_length=8, max_length=100)
    confirm_password: Optional[str] = Field(None, max_length=100)


class UserLoginRequest(BaseModel):
    identifier: str = Field(..., min_length=3, max_length=120)
    password: str = Field(..., min_length=1, max_length=100)


class TaskStartRequest(BaseModel):
    novel_url: str = Field(..., min_length=5, max_length=500)
    target_readers: int = Field(default=10, ge=1, le=5000)
    concurrent_terminals: int = Field(default=1, ge=1, le=10)
    max_chapters: int = Field(default=25, ge=1, le=100)
    reading_delay: float = Field(default=140.0, ge=1.0)
    mode: str = "valid"  # "valid" atau "guest"
    addon_guest_conversion: bool = False  # Addon konversi tamu ke member terdaftar OTP


class AdminSettingsUpdateRequest(BaseModel):
    price_valid_reader: Optional[int] = None
    price_guest_reader: Optional[int] = None
    addon_price_per_terminal: Optional[int] = None
    addon_price_guest_conversion: Optional[int] = None
    min_deposit: Optional[int] = None
    reading_delay_seconds: Optional[int] = None
    max_concurrent_terminals: Optional[int] = None


class PaymentCreateRequest(BaseModel):
    nominal: int = Field(..., ge=5000, le=5000000)
    payment_method: str = "QRIS"


class AdminLoginRequest(BaseModel):
    pin: str


class AdminTopupRequest(BaseModel):
    user_id: int = Field(..., ge=1)
    amount: int = Field(..., ge=100, le=50000000)
    note: str = "Top up manual via Admin"


def extract_token(request: Request) -> Optional[str]:
    token = request.headers.get("x-session-token") or request.cookies.get("session_token")
    if not token:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr[7:].strip()
    return token.strip() if token else None


def resolve_current_user(request: Request) -> Optional[Dict[str, Any]]:
    token = extract_token(request)
    if not token:
        return None
    return AuthManager.get_user_by_session(token)


# =============================================================================
# FRONTEND HTML ROUTE
# =============================================================================
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = BASE_DIR / "templates" / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Error: templates/index.html tidak ditemukan</h1>", status_code=404)
    with open(index_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# =============================================================================
# AUTHENTICATION API
# =============================================================================
@app.post("/api/auth/register")
async def api_register(req: UserRegisterRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"reg_{client_ip}", max_requests=5, window_seconds=300):
        return JSONResponse(status_code=429, content={"ok": False, "error": "Terlalu banyak permintaan pendaftaran. Coba lagi dalam 5 menit."})
    ok, msg, user = AuthManager.register(req.username, req.email, req.password, req.confirm_password)
    if not ok:
        return JSONResponse(status_code=400, content={"ok": False, "error": msg})
    return {"ok": True, "message": msg, "user": user, "token": user.get("session_token")}


@app.post("/api/auth/login")
async def api_login(req: UserLoginRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"login_{client_ip}", max_requests=10, window_seconds=120):
        return JSONResponse(status_code=429, content={"ok": False, "error": "Terlalu banyak percobaan login. Coba lagi dalam 2 menit."})
    ok, msg, user = AuthManager.login(req.identifier, req.password, client_ip=client_ip)
    if not ok:
        return JSONResponse(status_code=401, content={"ok": False, "error": msg})
    return {"ok": True, "message": msg, "user": user, "token": user.get("session_token")}


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    token = extract_token(request)
    if token:
        AuthManager.logout(token)
    return {"ok": True, "message": "Berhasil logout."}


@app.get("/api/auth/me")
async def api_get_me(request: Request):
    user = resolve_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Belum masuk akun."})
    return {"ok": True, "user": user}


@app.get("/api/auth/transactions")
async def api_get_transactions(request: Request):
    user = resolve_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Belum masuk akun."})
    txs = AuthManager.get_user_transactions(user["id"], limit=50)
    return {"ok": True, "transactions": txs}


# =============================================================================
# NOVEL INSPECTION API
# =============================================================================
@app.get("/api/novel-info")
async def api_novel_info(url: str):
    if not url or not url.strip():
        raise HTTPException(status_code=400, detail="URL atau ID Novel tidak boleh kosong.")
    res = await BotBridge.get_novel_info(url.strip())
    return res


# =============================================================================
# TASK EXECUTION & SSE STREAM
# =============================================================================
@app.post("/api/tasks/start")
async def api_start_task(req: TaskStartRequest, request: Request):
    user = resolve_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Silakan login ke akun Anda terlebih dahulu."})

    from database import get_setting
    price_valid = int(get_setting("price_valid_reader", 450))
    price_guest = int(get_setting("price_guest_reader", 50))
    addon_rate = int(get_setting("addon_price_per_terminal", 500))
    addon_conversion_rate = int(get_setting("addon_price_guest_conversion", 150))
    max_terms = int(get_setting("max_concurrent_terminals", 5))

    mode = "guest" if req.mode in ("guest", "guest_fast") else "valid"
    addon_conversion = bool(req.addon_guest_conversion and mode == "guest")
    base_rate = price_guest if mode == "guest" else price_valid
    rate_per_reader = base_rate + (addon_conversion_rate if addon_conversion else 0)
    
    concurrent_terms = max(1, min(req.concurrent_terminals, max_terms))
    addon_fee = (concurrent_terms - 1) * addon_rate if concurrent_terms > 1 else 0

    required_initial = addon_fee + rate_per_reader
    if user["balance"] < required_initial:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": f"Saldo tidak mencukupi! Minimal saldo Rp {required_initial:,} (Addon Sesi: Rp {addon_fee:,} + 1 Pembaca: Rp {rate_per_reader:,}). Saldo Anda: Rp {user['balance']:,}.",
                "balance": user["balance"],
                "required": required_initial,
            }
        )

    # Resolusi ID Novel
    novel_id = BotBridge.extract_novel_id(req.novel_url)
    if not novel_id:
        return JSONResponse(status_code=400, content={"ok": False, "error": "URL atau ID Novel tidak valid (16 karakter)."})

    novel_info = await BotBridge.get_novel_info(novel_id)
    if not novel_info.get("ok"):
        return JSONResponse(status_code=400, content={"ok": False, "error": novel_info.get("error") or "Gagal memverifikasi informasi novel."})

    is_adult = bool(novel_info.get("is_adult_only", False))
    if is_adult and mode == "guest":
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "Novel ini memiliki batasan 18+ (Dewasa). Quarterfull mewajibkan akun terverifikasi untuk membaca novel 18+, sehingga Mode Tamu (Guest) tidak dapat digunakan. Silakan gunakan Mode Akun Valid (25 Bab)."
            }
        )

    novel_title = novel_info.get("title", f"Novel #{novel_id}")
    author_id = novel_info.get("author_id", "")

    task_id = f"task_{int(time.time()*1000)}_{secrets.token_hex(3)}"

    # Potong biaya addon terminal jika ada slot tambahan
    if addon_fee > 0:
        AuthManager.deduct_balance(
            user_id=user["id"],
            amount=addon_fee,
            description=f"Biaya Addon {concurrent_terms} Sesi Terminal untuk '{novel_title[:30]}'",
            reference_id=task_id
        )

    task = WebTask(
        task_id=task_id,
        user_id=user["id"],
        novel_id=novel_id,
        novel_title=novel_title,
        author_id=author_id,
        mode=mode,
        target_readers=req.target_readers,
        concurrent_terminals=concurrent_terms,
        max_chapters=req.max_chapters,
        reading_delay=req.reading_delay,
        rate_per_reader=rate_per_reader,
        addon_fee=addon_fee,
        addon_guest_conversion=addon_conversion,
    )

    asyncio.create_task(BotBridge.run_task_lifecycle(task))

    return {
        "ok": True,
        "task_id": task_id,
        "message": f"Tugas dimulai ({concurrent_terms} Sesi Terminal simultan, Mode: {mode}).",
        "novel_title": novel_title,
        "concurrent_terminals": concurrent_terms,
        "addon_fee": addon_fee,
        "rate_per_reader": rate_per_reader,
        "addon_guest_conversion": addon_conversion,
    }


@app.get("/api/tasks/stream/{task_id}")
async def api_stream_task(task_id: str):
    entry = ACTIVE_TASKS.get(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Tugas tidak ditemukan atau sudah selesai.")

    task: WebTask = entry["task"]
    client_queue = task.subscribe()

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'task_id': task_id, 'is_running': task.is_running})}\n\n"
            for old_log in list(task.logs_history):
                yield f"data: {json.dumps(old_log)}\n\n"
            yield f"data: {json.dumps({'type': 'stats', 'stats': task.stats})}\n\n"

            if not task.is_running:
                yield f"data: {json.dumps({'type': 'done', 'stats': task.stats})}\n\n"
                return

            while task.is_running or not client_queue.empty():
                try:
                    event = await asyncio.wait_for(client_queue.get(), timeout=1.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "done":
                        break
                except asyncio.TimeoutError:
                    yield f": keepalive {time.time()}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            task.unsubscribe(client_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/tasks/stop/{task_id}")
async def api_stop_task(task_id: str, request: Request):
    user = resolve_current_user(request)
    entry = ACTIVE_TASKS.get(task_id)
    if not entry:
        return {"ok": False, "error": "Tugas tidak ditemukan atau sudah berhenti."}

    task: WebTask = entry["task"]
    # Validasi pemilik tugas
    if user and task.user_id != user["id"] and user["role"] != "admin":
        return JSONResponse(status_code=403, content={"ok": False, "error": "Bukan pemilik tugas ini."})

    await task.emit_log("🛑 Tugas dihentikan oleh user. Melepaskan seluruh sesi dan proxy...", "warn")
    task.cancel()
    await task.emit_done()
    return {"ok": True, "message": "Tugas berhasil dihentikan dan semua proxy telah di-release."}


@app.post("/api/tasks/pause/{task_id}")
async def api_pause_task(task_id: str, request: Request):
    user = resolve_current_user(request)
    entry = ACTIVE_TASKS.get(task_id)
    if not entry:
        return {"ok": False, "error": "Tugas tidak ditemukan atau sudah berhenti."}

    task: WebTask = entry["task"]
    if user and task.user_id != user["id"] and user["role"] != "admin":
        return JSONResponse(status_code=403, content={"ok": False, "error": "Bukan pemilik tugas ini."})

    task.pause()
    await task.emit_stats()
    return {"ok": True, "message": "Tugas berhasil dijeda (paused).", "is_paused": True}


@app.post("/api/tasks/resume/{task_id}")
async def api_resume_task(task_id: str, request: Request):
    user = resolve_current_user(request)
    entry = ACTIVE_TASKS.get(task_id)
    if not entry:
        return {"ok": False, "error": "Tugas tidak ditemukan atau sudah berhenti."}

    task: WebTask = entry["task"]
    if user and task.user_id != user["id"] and user["role"] != "admin":
        return JSONResponse(status_code=403, content={"ok": False, "error": "Bukan pemilik tugas ini."})

    task.resume()
    await task.emit_stats()
    return {"ok": True, "message": "Tugas berhasil dilanjutkan kembali (resumed).", "is_paused": False}


@app.get("/api/tasks/active")
async def api_get_active_task(request: Request):
    user = resolve_current_user(request)
    if not user:
        return {"ok": True, "active": False}

    for tid, entry in list(ACTIVE_TASKS.items()):
        task: WebTask = entry.get("task")
        if task and task.user_id == user["id"] and task.is_running:
            return {
                "ok": True,
                "active": True,
                "task_id": task.task_id,
                "novel_id": task.novel_id,
                "novel_title": task.novel_title,
                "author_id": task.author_id,
                "mode": task.mode,
                "concurrent_terminals": task.concurrent_terminals,
                "target_readers": task.target_readers,
                "rate_per_reader": task.rate_per_reader,
                "addon_guest_conversion": task.addon_guest_conversion,
                "is_paused": task.is_paused,
                "stats": task.stats,
            }
    return {"ok": True, "active": False}


@app.get("/api/tasks/history")
async def api_get_task_history(request: Request):
    user = resolve_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Belum login."})

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT task_id, novel_id, novel_title, target_readers, completed_readers,
               concurrent_terminals, status, created_at, updated_at
        FROM tasks
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 30;
        """, (user["id"],))
        rows = cursor.fetchall()
        return {"ok": True, "tasks": [dict(r) for r in rows]}


# =============================================================================
# SYSTEM STATUS & POOL STATS
# =============================================================================
@app.get("/api/proxy-pool/status")
async def api_proxy_pool_status():
    return {
        "ok": True,
        "summary": ProxyPoolManager.get_summary(),
        "proxies": ProxyPoolManager.list_proxies()[:20],
    }


@app.get("/api/stats/overview")
async def api_stats_overview():
    p_sum = ProxyPoolManager.get_summary()
    a_sum = AccountPoolManager.get_summary()
    active_tasks_count = len([t for t in ACTIVE_TASKS.values() if t["task"].is_running])
    return {
        "ok": True,
        "proxies": p_sum,
        "accounts": a_sum,
        "active_tasks": active_tasks_count,
        "rate_per_reader": RATE_PER_VALID_READER,
    }


# =============================================================================
# WIJAYAPAY QRIS PAYMENT GATEWAY
# =============================================================================
@app.get("/api/payment/channels")
async def api_get_payment_channels():
    """Mengambil daftar saluran pembayaran aktif dari gateway WijayaPay."""
    try:
        raw_channels = default_wijayapay_client.get_payment_methods()
        if not raw_channels:
            raw_channels = [
                {"code": "QRIS", "name": "QRIS (Semua Bank & e-Wallet)", "fee": 170},
                {"code": "BCAVA", "name": "BCA Virtual Account", "fee": 4700},
                {"code": "MANDIRIVA", "name": "Mandiri Virtual Account", "fee": 4700},
                {"code": "BRIVA", "name": "BRI Virtual Account", "fee": 4700},
                {"code": "BNIVA", "name": "BNI Virtual Account", "fee": 4700},
                {"code": "BSIVA", "name": "BSI Virtual Account", "fee": 4700},
                {"code": "CIMBVA", "name": "CIMB Niaga Virtual Account", "fee": 4700},
                {"code": "PERMATAVA", "name": "Permata Virtual Account", "fee": 4700},
                {"code": "ALFAMART", "name": "Alfamart / Alfamidi Retail", "fee": 3000},
                {"code": "INDOMARET", "name": "Indomaret Retail", "fee": 3000},
            ]
        
        channels = []
        for ch in raw_channels:
            code = (ch.get("code") or ch.get("payment_code") or "").upper().strip()
            name = ch.get("name") or ch.get("payment_name") or code
            fee = ch.get("fee") or ch.get("total_fee") or 0
            
            cat = "va"
            if "QRIS" in code:
                cat = "qris"
            elif "ALFA" in code or "INDO" in code:
                cat = "retail"
            
            channels.append({
                "code": code,
                "name": name,
                "category": cat,
                "fee": fee,
            })
        return {"ok": True, "channels": channels}
    except Exception as e:
        logger.error(f"Error fetching payment channels: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/api/payment/create")
async def api_create_payment(req: PaymentCreateRequest, request: Request):
    user = resolve_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Silakan login untuk melakukan top-up."})

    nominal = req.nominal
    payment_method = (req.payment_method or "QRIS").strip().upper()
    ref_id = f"TOPUP-{user['id']}-{int(time.time())}-{secrets.token_hex(2)}"
    cust_name = user["username"]
    cust_email = user["email"]

    try:
        res = default_wijayapay_client.create_transaction(
            ref_id=ref_id,
            nominal=nominal,
            payment_code=payment_method,
            customer_name=cust_name,
            customer_email=cust_email,
        )

        if not res.get("ok"):
            err_msg = res.get("error") or f"Gagal membuat tagihan {payment_method} ke WijayaPay."
            return JSONResponse(status_code=400, content={"ok": False, "error": err_msg})

        tx_data = res.get("data", {})
        qr_image = tx_data.get("qr_image") or tx_data.get("qr_url") or tx_data.get("checkout_url")
        qr_string = tx_data.get("qr_string") or ""
        nomor_va = tx_data.get("nomor_va") or ""
        nomor_pembayaran = tx_data.get("nomor_pembayaran") or tx_data.get("kode_pembayaran") or ""
        total_bayar = int(tx_data.get("total_bayar") or nominal)
        total_fee = int(tx_data.get("total_fee") or 0)
        expired_at = tx_data.get("expired") or tx_data.get("expired_time")
        tutorial = tx_data.get("tutorial_pembayaran") or ""
        payment_name = tx_data.get("payment_name") or tx_data.get("payment_method") or payment_method

        return {
            "ok": True,
            "ref_id": ref_id,
            "nominal": nominal,
            "total_bayar": total_bayar,
            "total_fee": total_fee,
            "payment_method": payment_method,
            "payment_name": payment_name,
            "qr_image": qr_image,
            "qr_string": qr_string,
            "nomor_va": nomor_va,
            "nomor_pembayaran": nomor_pembayaran,
            "tutorial": tutorial,
            "expired_at": expired_at,
        }
    except Exception as e:
        logger.error(f"Error invoice payment: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Kesalahan gateway pembayaran: {str(e)}"})


@app.get("/api/payment/check/{ref_id}")
async def api_check_payment(ref_id: str, request: Request):
    user = resolve_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Belum login."})

    try:
        res = default_wijayapay_client.check_status(ref_id)
        if not res.get("ok"):
            return {"ok": False, "payment_status": "PENDING", "message": res.get("error") or "Menunggu pembayaran..."}

        is_paid = res.get("is_paid", False)
        status_str = (res.get("status") or "pending").upper().strip()
        data = res.get("data", {})

        if is_paid or status_str in ("PAID", "SUCCESS", "BERHASIL", "SETTLED"):
            with db_session() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM transactions WHERE reference_id = ? AND type = 'TOPUP';", (ref_id,))
                already_credited = cursor.fetchone() is not None

                if not already_credited:
                    amount = int(data.get("total_diterima") or data.get("nominal") or data.get("amount") or 0)
                    if amount <= 0:
                        # Fallback ambil dari ref_id TOPUP-{user_id}-{time}-{hex}
                        try:
                            amount = int(data.get("total_bayar") or 10000)
                        except Exception:
                            amount = 10000

                    AuthManager.add_balance(
                        user_id=user["id"],
                        amount=amount,
                        description=f"Top Up Saldo QRIS ({ref_id})",
                        reference_id=ref_id
                    )
                    logger.info(f"Top up QRIS berhasil untuk user {user['id']}: Rp {amount:,}")
            refreshed_user = AuthManager.get_user_by_session(request.headers.get("x-session-token", ""))
            new_bal = refreshed_user["balance"] if refreshed_user else user["balance"]
            return {"ok": True, "payment_status": "PAID", "message": "Pembayaran lunas! Saldo telah bertambah.", "new_balance": new_bal}

        return {"ok": True, "payment_status": status_str or "PENDING", "message": "Menunggu konfirmasi pembayaran..."}
    except Exception as e:
        logger.error(f"Error checking payment {ref_id}: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/api/payment/callback")
@app.post("/api/payment/callback/wijayapay")
async def api_payment_callback(request: Request):
    """Webhook callback otomatis dari server WijayaPay."""
    try:
        data = {}
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.body()
            if body:
                data = json.loads(body.decode("utf-8", errors="ignore"))
        else:
            try:
                form = await request.form()
                data = dict(form)
            except Exception:
                body = await request.body()
                if body:
                    data = json.loads(body.decode("utf-8", errors="ignore"))

        logger.info(f"Webhook WijayaPay diterima: {data}")

        # Support payload direct or nested inside data
        payload = data.get("data") if isinstance(data.get("data"), dict) else data

        ref_id = payload.get("ref_id") or payload.get("reference_id") or payload.get("merchant_ref") or data.get("ref_id") or data.get("merchant_ref")
        tx_status = str(payload.get("status") or data.get("status") or "").upper().strip()
        raw_nominal = payload.get("nominal") or payload.get("amount") or payload.get("total_amount") or data.get("nominal") or 0
        try:
            amount = int(float(raw_nominal))
        except (ValueError, TypeError):
            amount = 0

        signature = (
            request.headers.get("x-callback-signature")
            or request.headers.get("x-signature")
            or payload.get("signature")
            or data.get("signature", "")
        )

        # Verifikasi signature jika ref_id & signature disertakan
        if signature and ref_id and not default_wijayapay_client.verify_signature(ref_id, signature):
            logger.warning(f"Signature callback tidak cocok untuk ref {ref_id}")
            return JSONResponse(status_code=400, content={"status": False, "message": "Invalid signature"})

        if tx_status in ("PAID", "SUCCESS", "BERHASIL", "1", "SETTLED") and ref_id and amount > 0:
            # Ambil user_id dari ref_id "TOPUP-{user_id}-..."
            parts = str(ref_id).split("-")
            if len(parts) >= 2 and parts[1].isdigit():
                user_id = int(parts[1])
                with db_session() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM transactions WHERE reference_id = ? AND type = 'TOPUP';", (ref_id,))
                    if not cursor.fetchone():
                        AuthManager.add_balance(
                            user_id=user_id,
                            amount=amount,
                            description=f"Top Up Saldo Webhook QRIS ({ref_id})",
                            reference_id=ref_id
                        )
                        logger.info(f"[Webhook] Top up Rp {amount:,} berhasil untuk User ID {user_id}")

        return JSONResponse(status_code=200, content={"status": True, "message": "Callback processed"})
    except Exception as e:
        logger.error(f"Error webhook: {e}")
        return JSONResponse(status_code=500, content={"status": False, "message": str(e)})



@app.get("/admin", response_class=HTMLResponse)
async def serve_admin():
    admin_file = BASE_DIR / "templates" / "admin.html"
    if not admin_file.exists():
        return HTMLResponse("<h1>Error: templates/admin.html tidak ditemukan</h1>", status_code=404)
    with open(admin_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


def verify_admin_session(request: Request) -> bool:
    token = request.headers.get("x-admin-token") or ""
    if not token:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr[7:].strip()
    if not token:
        return False
    exp = ADMIN_SESSIONS.get(token)
    if not exp or exp < time.time():
        ADMIN_SESSIONS.pop(token, None)
        return False
    return True


# =============================================================================
# ADMIN PORTAL API
# =============================================================================
class AdminAddProxyRequest(BaseModel):
    proxy_url: str


class AdminProxyIdRequest(BaseModel):
    proxy_id: int


class AdminRawTextRequest(BaseModel):
    raw_text: str


class AdminHypeProxyRotateRequest(BaseModel):
    proxy_id: Optional[Union[int, str]] = None


class AdminHypeProxyStartRequest(BaseModel):
    proxy_id: Union[int, str]


class AdminHypeProxyExtendRequest(BaseModel):
    proxy_id: Union[int, str]
    days: int = 7


class AdminResetUserPasswordRequest(BaseModel):
    user_id: int
    new_password: str = "Rinara123!"


class AdminDeleteUserRequest(BaseModel):
    user_id: int


class AdminBotRegisterRequest(BaseModel):
    count: int = 5
    country: str = "RANDOM"
    verify_email: bool = True
    email_provider: str = "RANDOM"


@app.post("/api/admin/login")
async def api_admin_login(req: AdminLoginRequest):
    if req.pin.strip() == get_admin_pin():
        admin_token = secrets.token_hex(32)
        ADMIN_SESSIONS[admin_token] = time.time() + 7200  # valid 2 jam
        return {"ok": True, "admin_token": admin_token, "expires_in": 7200}
    return JSONResponse(status_code=401, content={"ok": False, "error": "Master PIN Admin tidak valid!"})


@app.get("/api/admin/overview")
async def api_admin_overview(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid atau telah kedaluwarsa."})

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS total_users, COALESCE(SUM(balance), 0) AS total_balance FROM users;")
        u_data = dict(cursor.fetchone())

        cursor.execute("SELECT COALESCE(SUM(amount), 0) AS gross_topup FROM transactions WHERE type = 'TOPUP';")
        gross_topup = cursor.fetchone()["gross_topup"]

        cursor.execute("SELECT COALESCE(SUM(amount), 0) AS total_spent FROM transactions WHERE type = 'USAGE';")
        total_spent = cursor.fetchone()["total_spent"]

        cursor.execute("SELECT COUNT(*) AS total_tasks, COALESCE(SUM(completed_readers), 0) AS total_readers FROM tasks;")
        t_data = dict(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN status='IDLE' THEN 1 ELSE 0 END) AS idle, SUM(CASE WHEN status='BUSY' THEN 1 ELSE 0 END) AS busy, SUM(CASE WHEN status='DEAD' THEN 1 ELSE 0 END) AS dead FROM proxies;")
        p_data = dict(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) AS total_acc, COALESCE(SUM(q_balance), 0) AS total_coins FROM bot_accounts;")
        a_data = dict(cursor.fetchone())

        return {
            "ok": True,
            "users": u_data,
            "financial": {
                "gross_topup": gross_topup,
                "total_spent": total_spent,
                "outstanding_balance": u_data["total_balance"]
            },
            "tasks": t_data,
            "proxies": p_data,
            "accounts": a_data,
            "active_tasks_count": len(ACTIVE_TASKS)
        }


@app.get("/api/admin/users")
async def api_admin_get_users(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid atau telah kedaluwarsa."})

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, email, balance, role, created_at, last_login_at FROM users ORDER BY id DESC;")
        return {"ok": True, "users": [dict(r) for r in cursor.fetchall()]}


@app.post("/api/admin/topup-user")
async def api_admin_topup_user(req: AdminTopupRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid atau telah kedaluwarsa."})

    if req.amount == 0 or abs(req.amount) > 50000000:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nominal topup tidak valid (Rp 100 - Rp 50.000.000)."})

    if req.amount > 0:
        ok = AuthManager.add_balance(req.user_id, req.amount, req.note, reference_id="ADMIN_MANUAL")
    else:
        ok = AuthManager.deduct_balance(req.user_id, abs(req.amount), req.note, reference_id="ADMIN_DEDUCT")

    if ok:
        return {"ok": True, "message": f"Berhasil memproses saldo Rp {abs(req.amount):,} untuk User #{req.user_id}."}
    return JSONResponse(status_code=400, content={"ok": False, "error": "Gagal memproses saldo. Pastikan User ID terdaftar dan saldo cukup."})


@app.post("/api/admin/reset-user-password")
async def api_admin_reset_user_password(req: AdminResetUserPasswordRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid atau telah kedaluwarsa."})
    if len(req.new_password) < 6:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Kata sandi baru minimal 6 karakter."})
    from database import hash_password
    pwd_hash, pwd_salt = hash_password(req.new_password)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?;", (pwd_hash, pwd_salt, req.user_id))
        if cursor.rowcount > 0:
            return {"ok": True, "message": f"Kata sandi User #{req.user_id} berhasil diubah ke '{req.new_password}'!"}
        return JSONResponse(status_code=404, content={"ok": False, "error": f"User #{req.user_id} tidak ditemukan."})


@app.post("/api/admin/delete-user")
async def api_admin_delete_user(req: AdminDeleteUserRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid atau telah kedaluwarsa."})
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?;", (req.user_id,))
        if cursor.rowcount > 0:
            return {"ok": True, "message": f"User #{req.user_id} berhasil dihapus."}
        return JSONResponse(status_code=404, content={"ok": False, "error": "User tidak ditemukan."})



@app.get("/api/admin/proxies")
async def api_admin_get_proxies(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, proxy_url, status, current_worker_id, failed_count, last_checked_at FROM proxies ORDER BY id ASC;")
        return {"ok": True, "proxies": [dict(r) for r in cursor.fetchall()]}


@app.post("/api/admin/proxy/add")
async def api_admin_add_proxy(req: AdminAddProxyRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    url = req.proxy_url.strip()
    if not url:
        return JSONResponse(status_code=400, content={"ok": False, "error": "URL proxy tidak boleh kosong."})
    with db_session() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO proxies (proxy_url, status) VALUES (?, 'IDLE');", (url,))
            return {"ok": True, "message": "Proxy berhasil ditambahkan ke pool."}
        except Exception as e:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"Gagal menambahkan proxy: {e}"})


@app.post("/api/admin/proxy/reset")
async def api_admin_reset_proxy(req: AdminProxyIdRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE proxies SET status = 'IDLE', failed_count = 0, current_worker_id = NULL, current_task_id = NULL WHERE id = ?;", (req.proxy_id,))
        return {"ok": True, "message": "Status proxy berhasil di-reset ke IDLE."}


@app.post("/api/admin/proxy/delete")
async def api_admin_delete_proxy(req: AdminProxyIdRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM proxies WHERE id = ?;", (req.proxy_id,))
        return {"ok": True, "message": "Proxy berhasil dihapus dari pool."}


@app.post("/api/admin/proxy/sync-file")
async def api_admin_proxy_sync_file(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    try:
        res = ProxyPoolManager.sync_from_hypeproxy(wipe_stale=True)
        return res
    except Exception as e:
        res = ProxyPoolManager.sync_from_files()
        return {
            "ok": True,
            "message": f"Sinkronisasi berkas berhasil! {res['added']} proxy baru ditambahkan. Total armada: {res['total']} proxy ({res['idle']} siap).",
            "stats": res,
        }


@app.post("/api/admin/proxy/import")
async def api_admin_proxy_import(req: AdminRawTextRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    res = ProxyPoolManager.import_proxies(req.raw_text)
    return {
        "ok": True,
        "message": f"Berhasil mengimpor {res['added']} proxy baru. Total armada: {res['total']} proxy ({res['idle']} siap).",
        "stats": res,
    }


# =============================================================================
# HYPEPROXY CLOUD API INTEGRATION
# =============================================================================
@app.get("/api/admin/hypeproxy/status")
async def api_admin_hypeproxy_status(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    from proxy_manager import HypeProxyClient
    prof = HypeProxyClient.get_profile()
    my_proxies = HypeProxyClient.get_proxies(user_only=True)
    summary = ProxyPoolManager.get_summary()
    return {
        "ok": True,
        "profile": prof.get("user", {}),
        "proxies": my_proxies,
        "total_owned": len(my_proxies),
        "pool_summary": summary,
    }


@app.post("/api/admin/hypeproxy/sync")
async def api_admin_hypeproxy_sync(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    res = ProxyPoolManager.sync_from_hypeproxy(wipe_stale=True)
    return res


@app.post("/api/admin/hypeproxy/rotate")
async def api_admin_hypeproxy_rotate(req: Optional[AdminHypeProxyRotateRequest] = None, request: Request = None):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    from proxy_manager import HypeProxyClient
    pid = req.proxy_id if req else None
    if pid is not None and str(pid).strip() and str(pid).lower() not in ("all", "0", ""):
        res = HypeProxyClient.rotate_proxy(pid, force=True)
        return {
            "ok": res.get("ok", False),
            "message": res.get("message") or f"Rotasi IP slot #{pid} berhasil diinisiasi.",
            "details": res
        }
    else:
        res = HypeProxyClient.rotate_all_proxies()
        return res


@app.post("/api/admin/hypeproxy/start")
async def api_admin_hypeproxy_start(req: AdminHypeProxyStartRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    from proxy_manager import HypeProxyClient
    res = HypeProxyClient.start_proxy(req.proxy_id)
    return res


@app.post("/api/admin/hypeproxy/extend")
async def api_admin_hypeproxy_extend(req: AdminHypeProxyExtendRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    from proxy_manager import HypeProxyClient
    res = HypeProxyClient.extend_proxy(req.proxy_id, days=req.days)
    return res



ADMIN_BOT_REGISTRATION: Dict[str, Any] = {
    "running": False,
    "total_target": 0,
    "completed": 0,
    "success_count": 0,
    "failed_count": 0,
    "logs": [],
    "cancel_event": None,
}


async def run_admin_bot_registration(count: int, country: str, verify_email: bool, email_provider: str):
    import random
    from stealth_bot.client import StealthApiClient
    from stealth_bot.profile import ProfileGenerator
    from stealth_bot.email_verifier import TempTfVerifier
    from stealth_bot.proxy import pick_weighted_country
    from account_pool import AccountPoolManager
    from proxy_pool import ProxyPoolManager

    ADMIN_BOT_REGISTRATION["running"] = True
    ADMIN_BOT_REGISTRATION["total_target"] = count
    ADMIN_BOT_REGISTRATION["completed"] = 0
    ADMIN_BOT_REGISTRATION["success_count"] = 0
    ADMIN_BOT_REGISTRATION["failed_count"] = 0
    ADMIN_BOT_REGISTRATION["logs"] = []
    cancel_ev = asyncio.Event()
    ADMIN_BOT_REGISTRATION["cancel_event"] = cancel_ev

    def add_log(msg: str, level: str = "info"):
        t_str = datetime.now().strftime("%H:%M:%S")
        ADMIN_BOT_REGISTRATION["logs"].append({"time": t_str, "message": msg, "level": level})
        if len(ADMIN_BOT_REGISTRATION["logs"]) > 200:
            ADMIN_BOT_REGISTRATION["logs"].pop(0)

    add_log(f"Memulai registrasi {count} akun baru [Mode: {'Verifikasi OTP Temp.tf' if verify_email else 'Direct Signup'} | Provider: {email_provider} | Negara: {country}]", "info")

    verifier = TempTfVerifier() if verify_email else None

    # Load existing emails to avoid duplicates
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM bot_accounts;")
        existing_emails = {r["email"].lower() for r in cursor.fetchall()}

    for i in range(1, count + 1):
        if cancel_ev.is_set():
            add_log("Pendaftaran dihentikan oleh admin.", "warn")
            break

        c_country = pick_weighted_country() if country.upper() in ("RANDOM", "ALL", "") else country.upper()
        add_log(f"[{i}/{count}] Membuat profil & identitas perangkat Android baru ({c_country})...", "info")

        temp_email = None
        if verifier:
            prov = email_provider.lower()
            if prov in ("random", "all", ""):
                prov = random.choice(["gmail", "outlook", "hotmail"])
            
            add_log(f"[{i}/{count}] Mengambil email dari temp.tf (provider: {prov})...", "info")
            for _ in range(12):
                c_mail = await verifier.get_email(provider=prov, use_dot=(prov == "gmail"), use_plus=(prov != "gmail"))
                if c_mail and c_mail.lower() not in existing_emails:
                    temp_email = c_mail.lower()
                    existing_emails.add(temp_email)
                    break
                await asyncio.sleep(0.4)

            if temp_email:
                add_log(f"[{i}/{count}] Email didapat: {temp_email}", "success")
            else:
                add_log(f"[{i}/{count}] Gagal ambil email temp.tf, fallback email sintetis.", "warn")

        profile = ProfileGenerator.generate_profile(country_code=c_country, email=temp_email)
        
        # Ambil proxy dari pool
        proxy_url = ProxyPoolManager.acquire_proxy(task_id="ADMIN_REGISTER", worker_id=f"REG_{i}")

        client = StealthApiClient(
            profile=profile,
            current_proxy=proxy_url,
        )

        try:
            add_log(f"[{i}/{count}] Mengirim registrasi & atribusi AppsFlyer...", "info")
            ok, msg = await client.perform_organic_signup()
            if ok:
                is_verified = False
                if verifier and temp_email:
                    add_log(f"[{i}/{count}] Mengirim permintaan kode verifikasi OTP...", "info")
                    send_ok, send_msg = await client.send_email_verification()
                    if send_ok:
                        add_log(f"[{i}/{count}] Menunggu OTP masuk di temp.tf...", "info")
                        otp_code = await verifier.poll_for_otp(temp_email, timeout_sec=90, interval_sec=4)
                        if otp_code:
                            v_ok, v_msg = await client.verify_email_code(otp_code)
                            if v_ok:
                                is_verified = True
                                add_log(f"[{i}/{count}] ✓ Berhasil diverifikasi resmi dengan kode OTP: {otp_code}!", "success")
                            else:
                                add_log(f"[{i}/{count}] Kode OTP ditolak: {v_msg}", "warn")
                        else:
                            add_log(f"[{i}/{count}] Timeout: OTP tidak diterima dalam 90 detik.", "warn")
                    else:
                        add_log(f"[{i}/{count}] Gagal request kirim OTP: {send_msg}", "warn")

                # Claim daily Q coin
                claim_res = await client.claim_daily_q()
                q_bal = claim_res.get("balance", 0)

                acc_data = {
                    "email": profile.email,
                    "password": profile.password,
                    "nickname": profile.nickname,
                    "access_token": client.access_token,
                    "refresh_token": client.refresh_token,
                    "country": profile.country,
                    "device_id": profile.device_id,
                    "anonymous_id": profile.anonymous_id,
                    "user_agent": profile.user_agent,
                    "q_balance": q_bal,
                    "is_email_verified": is_verified,
                }
                AccountPoolManager.add_or_update_account(acc_data)
                ADMIN_BOT_REGISTRATION["success_count"] += 1
                add_log(f"[{i}/{count}] ✅ SUKSES! Akun {profile.email} tersimpan (Koin: {q_bal} Q).", "success")
            else:
                ADMIN_BOT_REGISTRATION["failed_count"] += 1
                add_log(f"[{i}/{count}] ❌ Gagal mendaftar: {msg}", "error")

        except Exception as e:
            ADMIN_BOT_REGISTRATION["failed_count"] += 1
            add_log(f"[{i}/{count}] Error pendaftaran: {e}", "error")
        finally:
            if proxy_url:
                ProxyPoolManager.release_proxy(proxy_url)
            await client.close()

        ADMIN_BOT_REGISTRATION["completed"] = i
        if i < count and not cancel_ev.is_set():
            delay = random.uniform(3.0, 7.0)
            add_log(f"Jeda anti-clustering wajar {int(delay)} detik...", "info")
            await asyncio.sleep(delay)

    ADMIN_BOT_REGISTRATION["running"] = False
    add_log(f"Selesai! Berhasil membuat {ADMIN_BOT_REGISTRATION['success_count']} akun baru berkualitas tinggi.", "success")


@app.post("/api/admin/bot-accounts/register")
async def api_admin_bot_accounts_register(req: AdminBotRegisterRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    if ADMIN_BOT_REGISTRATION["running"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Proses pendaftaran akun masih sedang berjalan."})

    target_count = max(1, min(50, req.count))
    asyncio.create_task(run_admin_bot_registration(
        count=target_count,
        country=req.country,
        verify_email=req.verify_email,
        email_provider=req.email_provider
    ))
    return {
        "ok": True,
        "message": f"Memulai registrasi {target_count} akun baru di latar belakang...",
        "target": target_count
    }


@app.get("/api/admin/bot-accounts/register-status")
async def api_admin_bot_accounts_register_status(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    return {
        "ok": True,
        "running": ADMIN_BOT_REGISTRATION["running"],
        "total_target": ADMIN_BOT_REGISTRATION["total_target"],
        "completed": ADMIN_BOT_REGISTRATION["completed"],
        "success_count": ADMIN_BOT_REGISTRATION["success_count"],
        "failed_count": ADMIN_BOT_REGISTRATION["failed_count"],
        "logs": ADMIN_BOT_REGISTRATION["logs"],
    }


@app.post("/api/admin/bot-accounts/stop-register")
async def api_admin_bot_accounts_stop_register(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    if ADMIN_BOT_REGISTRATION["cancel_event"]:
        ADMIN_BOT_REGISTRATION["cancel_event"].set()
        return {"ok": True, "message": "Perintah penghentian registrasi dikirim."}
    return {"ok": False, "error": "Tidak ada registrasi yang sedang berjalan."}


@app.post("/api/admin/bot-accounts/sync-file")
async def api_admin_bot_accounts_sync_file(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    res = AccountPoolManager.sync_from_file()
    return {
        "ok": True,
        "message": f"Sinkronisasi berhasil! {res['added']} akun baru dimasukkan ke database. Total akun aktif: {res['total']}.",
        "stats": res,
    }


@app.post("/api/admin/bot-accounts/import")
async def api_admin_bot_accounts_import(req: AdminRawTextRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    res = AccountPoolManager.import_accounts(req.raw_text)
    return {
        "ok": True,
        "message": f"Berhasil mengimpor {res['added']} akun baru ke database. Total akun: {res['total']}.",
        "stats": res,
    }


@app.get("/api/admin/accounts")
async def api_admin_get_accounts(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT a.id, a.email, a.status, a.q_balance, a.daily_read_seconds, a.last_read_date,
               (SELECT COUNT(*) FROM account_history h WHERE h.bot_email = a.email) as total_novels_read
        FROM bot_accounts a
        ORDER BY a.id ASC;
        """)
        return {"ok": True, "accounts": [dict(r) for r in cursor.fetchall()]}


@app.get("/api/admin/tasks")
async def api_admin_get_tasks(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid"})
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT t.task_id, t.novel_title, t.target_readers, t.completed_readers, t.concurrent_terminals, t.status, t.created_at,
               u.username as user_name
        FROM tasks t
        LEFT JOIN users u ON t.user_id = u.id
        ORDER BY t.created_at DESC LIMIT 50;
        """)
        return {"ok": True, "tasks": [dict(r) for r in cursor.fetchall()]}


# =============================================================================
# SYSTEM SETTINGS & DYNAMIC TARIFFS
# =============================================================================
@app.get("/api/settings")
async def api_get_public_settings():
    from database import get_all_settings
    settings = get_all_settings()
    return {
        "ok": True,
        "settings": settings,
        "price_valid_reader": int(settings.get("price_valid_reader", 450)),
        "price_guest_reader": int(settings.get("price_guest_reader", 50)),
        "addon_price_per_terminal": int(settings.get("addon_price_per_terminal", 500)),
        "addon_price_guest_conversion": int(settings.get("addon_price_guest_conversion", 150)),
        "min_deposit": int(settings.get("min_deposit", 5000)),
        "reading_delay_seconds": int(settings.get("reading_delay_seconds", 140)),
        "max_concurrent_terminals": int(settings.get("max_concurrent_terminals", 5)),
    }


@app.get("/api/admin/settings")
async def api_admin_get_settings(request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid atau telah kedaluwarsa."})
    from database import get_all_settings
    return {"ok": True, "settings": get_all_settings()}


@app.post("/api/admin/settings")
async def api_admin_update_settings(req: AdminSettingsUpdateRequest, request: Request):
    if not verify_admin_session(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Sesi Admin tidak valid atau telah kedaluwarsa."})
    from database import update_setting, get_all_settings
    data = req.dict(exclude_unset=True)
    for k, v in data.items():
        if v is not None:
            update_setting(k, v)
    return {
        "ok": True,
        "message": "Konfigurasi tarif dan parameter sistem berhasil disimpan!",
        "settings": get_all_settings(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
