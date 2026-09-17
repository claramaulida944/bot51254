"""
FastAPI Web Server untuk Bot Toodat / Quarterfull
Menyediakan REST API & Server-Sent Events (SSE) untuk:
- Verifikasi akses token berlimit saldo Rupiah
- Autentikasi dan dashboard Owner (Generate Token 10rb, 20rb, 50rb)
- Input link novel mandiri dengan auto-inspect info novel
- Live streaming log terminal eksekusi bot & update saldo real-time
- Call-to-Action WhatsApp Owner (wa.me/6287734343023)
"""

import asyncio
import json
import logging
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("RinaraDevServer")

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Setup sys.path
BASE_DIR = Path(__file__).parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from token_manager import TokenManager, OWNER_WHATSAPP, TOKEN_PRESETS
from bot_bridge import BotBridge, WebTask, ACTIVE_TASKS
from wijayapay_client import default_wijayapay_client

app = FastAPI(title="RinaraDev Automation Platform", version="3.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Owner Master Key (dapat disesuaikan)
ADMIN_SECRET_PIN = "100401naraA!"


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================
class TokenVerifyRequest(BaseModel):
    token: str


class TaskStartRequest(BaseModel):
    token: str
    novel_url: str
    mode: str = "full_auto"  # full_auto, member_read, guest_read, like_only, bookmark_only, follow_only
    accounts_count: int = 5
    guest_count: int = 10
    max_chapters: int = 5
    reading_delay: float = 6.0
    country: str = "RANDOM"  # RANDOM or specific country code (ID, US, JP, etc.)


class AdminLoginRequest(BaseModel):
    pin: str


class AdminCreateTokenRequest(BaseModel):
    amount: int = 20000
    label: str = "Client Token"
    expiry_days: Optional[int] = None
    notes: str = ""


class AdminTopupRequest(BaseModel):
    amount: int = 10000
    note: str = "Top up manual via Owner"


class AdminStatusRequest(BaseModel):
    status: str  # active, suspended, revoked


class AdminGenerateAccountsRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50)
    country: str = "RANDOM"
    ua_mode: str = "okhttp"


class ClientGenerateAccountsRequest(BaseModel):
    token: str
    count: int = Field(default=1, ge=1, le=50)
    country: str = "RANDOM"
    ua_mode: str = "okhttp"


class PaymentCreateRequest(BaseModel):
    order_type: str = "new_token"  # new_token or topup
    nominal: int = Field(..., ge=1000, le=10000000)
    token_code: Optional[str] = None
    payment_code: str = "QRIS"
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None


TRIAL_IP_TIMESTAMPS: Dict[str, float] = {}
TRIAL_NOVEL_TIMESTAMPS: Dict[str, float] = {}


class FreeTrialStartRequest(BaseModel):
    novel_url: str


class AdminUpdatePricingRequest(BaseModel):
    rates: Optional[Dict[str, Any]] = None
    packages: Optional[List[Dict[str, Any]]] = None
    owner_wa: Optional[str] = None
    free_trial: Optional[Dict[str, Any]] = None


# =============================================================================
# FRONTEND ROUTES
# =============================================================================
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = BASE_DIR / "templates" / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Index HTML tidak ditemukan</h1>", status_code=404)
    with open(index_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/admin", response_class=HTMLResponse)
@app.get("/owner-portal", response_class=HTMLResponse)
async def serve_admin():
    """Rute rahasia khusus Owner (tersembunyi dari publik)."""
    admin_file = BASE_DIR / "templates" / "admin.html"
    if not admin_file.exists():
        return HTMLResponse("<h1>Admin Panel HTML tidak ditemukan</h1>", status_code=404)
    with open(admin_file, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# =============================================================================
# CLIENT / USER BOT RUNNER ENDPOINTS
# =============================================================================
@app.get("/api/pricing")
async def get_pricing():
    """Mengembalikan konfigurasi dinamis: tarif per item, paket harga, dan kontak WhatsApp Owner."""
    cfg = TokenManager.get_pricing_config()
    rates = cfg.get("rates", {})
    raw_packages = cfg.get("packages", [])
    owner_wa = cfg.get("owner_wa", OWNER_WHATSAPP)
    vr_rate = rates.get("valid_reader", 500)
    gr_rate = rates.get("guest_reader", 50)

    enriched_packages = []
    for idx, p in enumerate(raw_packages):
        price = int(p.get("price", 10000))
        vr_sess = price // vr_rate if vr_rate > 0 else 0
        gr_sess = price // gr_rate if gr_rate > 0 else 0
        is_pop = bool(p.get("popular") or p.get("is_featured") or idx == 1)
        enriched_packages.append({
            "id": p.get("id") or f"pkg_{price}",
            "name": str(p.get("name", f"Paket Rp {price:,}")).strip(),
            "price": price,
            "features": p.get("features", []),
            "popular": is_pop,
            "is_featured": is_pop,
            "sessions_label": f"{vr_sess} Sesi Member / {gr_sess} Tamu",
        })

    return {
        "ok": True,
        "owner_whatsapp": owner_wa,
        "owner_wa_url": f"https://wa.me/{owner_wa}",
        "rates": {
            "valid_reader": vr_rate,
            "guest_reader_10": gr_rate * 10,
            "guest_reader_1": gr_rate,
            "guest_reader": gr_rate,
            "account_generator": int(rates.get("account_generator", 50)),
            "like": rates.get("like", 100),
            "bookmark": rates.get("bookmark", 100),
            "follow": rates.get("follow", 100),
        },
        "packages": enriched_packages,
        "presets": enriched_packages,
        "free_trial": cfg.get("free_trial", {}),
    }


@app.post("/api/verify-token")
async def verify_token(req: TokenVerifyRequest):
    """Memverifikasi keabsahan token pengguna dan mengembalikan saldo terkini."""
    token_code = req.token.strip()
    is_valid, msg, token = TokenManager.verify_token(token_code, min_required_balance=50)
    if not is_valid:
        wa_url = TokenManager.get_whatsapp_url(token_code, 20000)
        return {
            "ok": False,
            "error": msg,
            "wa_url": wa_url,
            "token": token,
        }

    return {
        "ok": True,
        "message": msg,
        "token": token.get("token"),
        "label": token.get("label"),
        "current_balance": token.get("current_balance", 0),
        "total_spent": token.get("total_spent", 0),
        "status": token.get("status"),
        "expires_at": token.get("expires_at"),
        "stats": token.get("stats", {}),
    }


@app.get("/api/novel-info")
async def get_novel_info(url: str):
    """Mengekstrak dan memeriksa metadata novel (judul, author, cover, readable chapters)."""
    if not url or not url.strip():
        raise HTTPException(status_code=400, detail="URL atau ID Novel tidak boleh kosong")
    res = await BotBridge.get_novel_info(url.strip())
    return res


@app.post("/api/tasks/start")
async def start_task(req: TaskStartRequest):
    """Memvalidasi token & saldo, lalu memulai background task bot."""
    token_code = req.token.strip()
    
    # 1. Tentukan batas minimum saldo dinamis sesuai mode
    rates = TokenManager.get_pricing_config().get("rates", {})
    vr_rate = rates.get("valid_reader", 500)
    gr_rate = rates.get("guest_reader", 50)
    like_rate = rates.get("like", 100)
    if req.mode in ("full_auto", "member_read"):
        min_bal = vr_rate
    elif req.mode in ("like_only", "bookmark_only", "follow_only"):
        min_bal = like_rate
    else:
        min_bal = gr_rate
    is_valid, msg, token = TokenManager.verify_token(token_code, min_required_balance=min_bal)
    if not is_valid:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": msg,
                "wa_url": TokenManager.get_whatsapp_url(token_code, 20000),
            }
        )

    # 2. Resolusi ID novel
    novel_id = req.novel_url.strip()
    import re
    query_hash = re.search(r"[?&]hashId=([a-zA-Z0-9]{16})", novel_id)
    if query_hash:
        novel_id = query_hash.group(1)
    else:
        match = re.search(r"([a-zA-Z0-9]{16})", novel_id)
        if match:
            novel_id = match.group(1)

    task_id = f"task_{int(time.time()*1000)}_{secrets.token_hex(3)}"
    config = {
        "accounts_count": req.accounts_count,
        "guest_count": req.guest_count,
        "max_chapters": req.max_chapters,
        "reading_delay": req.reading_delay,
        "country": (req.country or "RANDOM").upper().strip(),
    }

    task = WebTask(task_id=task_id, token_code=token_code, novel_id=novel_id, mode=req.mode, config=config)
    
    # Jalankan lifecycle di background asyncio
    asyncio.create_task(BotBridge.run_task_lifecycle(task))

    return {
        "ok": True,
        "task_id": task_id,
        "message": "Tugas berhasil dimulai. Sambungkan ke stream log via SSE.",
    }


@app.get("/api/tasks/stream/{task_id}")
async def stream_task(task_id: str):
    """Server-Sent Events (SSE) streaming endpoint untuk realtime log dan progress dengan replay history saat refresh."""
    entry = ACTIVE_TASKS.get(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Tugas tidak ditemukan atau sudah selesai")

    task: WebTask = entry["task"]
    client_queue = task.subscribe()

    async def event_generator():
        try:
            # Kirim event koneksi awal
            yield f"data: {json.dumps({'type': 'connected', 'task_id': task_id, 'is_running': task.is_running})}\n\n"

            # Replay seluruh riwayat log agar saat browser di-refresh, log tidak hilang!
            for old_log in list(task.logs_history):
                yield f"data: {json.dumps(old_log)}\n\n"

            # Kirim metrik statistik terkini
            yield f"data: {json.dumps({'type': 'stats', 'stats': task.stats})}\n\n"

            # Jika tugas sudah selesai sebelum klien terhubung, kirim done
            if not task.is_running:
                yield f"data: {json.dumps({'type': 'done', 'stats': task.stats, 'wa_link': TokenManager.get_whatsapp_url(task.token_code, 20000)})}\n\n"
                return

            while task.is_running or not client_queue.empty():
                try:
                    event = await asyncio.wait_for(client_queue.get(), timeout=1.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "done":
                        break
                except asyncio.TimeoutError:
                    # Ping keep-alive
                    yield f": keepalive {time.time()}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            task.unsubscribe(client_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/tasks/active")
async def get_active_task(token: Optional[str] = None):
    """Mengecek apakah ada tugas yang sedang aktif berjalan di server untuk token ini."""
    if not token:
        return {"ok": True, "active": False}
    token_clean = token.strip().upper()
    for tid, entry in list(ACTIVE_TASKS.items()):
        task: WebTask = entry.get("task")
        if task and task.token_code.upper() == token_clean and task.is_running:
            return {
                "ok": True,
                "active": True,
                "task_id": task.task_id,
                "novel_id": task.novel_id,
                "mode": task.mode,
                "stats": task.stats,
                "novel_info": task.novel_info,
            }
    return {"ok": True, "active": False}


@app.get("/api/tasks/status/{task_id}")
async def get_task_status(task_id: str):
    """Mengecek status tugas bot tertentu."""
    entry = ACTIVE_TASKS.get(task_id)
    if not entry:
        return {"ok": False, "error": "Tugas tidak ditemukan"}
    task: WebTask = entry["task"]
    return {
        "ok": True,
        "task_id": task.task_id,
        "is_running": task.is_running,
        "stats": task.stats,
        "logs": task.logs_history[-30:],
        "novel_info": {
            "title": (task.novel_info or {}).get("title"),
            "author": (task.novel_info or {}).get("author"),
        },
    }


@app.post("/api/tasks/stop/{task_id}")
async def stop_task(task_id: str):
    """Menghentikan tugas bot yang sedang berjalan."""
    entry = ACTIVE_TASKS.get(task_id)
    if not entry:
        return {"ok": False, "error": "Tugas tidak ditemukan atau sudah berhenti"}

    task: WebTask = entry["task"]
    task.cancel()
    return {"ok": True, "message": "Perintah stop telah dikirim ke worker."}


# =============================================================================
# CLIENT ACCOUNT GENERATOR ENDPOINT (BERBAYAR DINAMIS)
# =============================================================================
@app.post("/api/accounts/generate")
async def client_generate_accounts(req: ClientGenerateAccountsRequest):
    """Pengguna men-generate akun baru menggunakan saldo token akses berbayar (tarif dinamis)."""
    token_code = req.token.strip().upper()
    is_valid, token_data, err_msg = TokenManager.verify_token(token_code)
    if not is_valid or not token_data:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": err_msg or "Token akses tidak valid atau tidak aktif."},
        )

    current_bal = token_data.get("current_balance", 0)
    pricing = TokenManager.get_pricing_config()
    rate_per_acc = pricing.get("rates", {}).get("account_generator", 50)

    if current_bal < rate_per_acc:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": f"Saldo tidak mencukupi! Minimal saldo Rp {rate_per_acc:,} untuk generate 1 akun. Saldo Anda: Rp {current_bal:,}.",
                "rate": rate_per_acc,
                "current_balance": current_bal,
            },
        )

    max_possible = current_bal // rate_per_acc
    actual_count = min(req.count, max_possible)
    if actual_count <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": f"Saldo tidak mencukupi untuk jumlah akun yang diminta (Dibutuhkan Rp {(req.count * rate_per_acc):,}, Saldo: Rp {current_bal:,}).",
                "rate": rate_per_acc,
                "current_balance": current_bal,
            },
        )

    try:
        from core.auto_signup import RegistrationRunner
    except ImportError:
        from auto_signup import RegistrationRunner

    core_akun_file = BASE_DIR / "core" / "akun.txt"
    root_akun_file = BASE_DIR.parent / "akun.txt"

    def _run_registration():
        runner = RegistrationRunner(accounts_file=str(core_akun_file))
        created = []
        errors = []
        for idx in range(1, actual_count + 1):
            try:
                res = runner.register_account(country_code=req.country, ua_mode=req.ua_mode)
                if res.get("status") == "success":
                    acc = res.get("account", {})
                    created.append(acc)
                    try:
                        line = json.dumps(acc, ensure_ascii=False)
                        with open(root_akun_file, "a", encoding="utf-8") as rf:
                            rf.write(line + "\n")
                    except Exception:
                        pass
                else:
                    errors.append(f"Akun #{idx}: {res.get('error', 'Gagal mendaftar')}")
            except Exception as e:
                errors.append(f"Akun #{idx}: {str(e)}")
        return created, errors

    created_accounts, errors = await asyncio.to_thread(_run_registration)
    created_count = len(created_accounts)

    # Potong saldo HANYA untuk akun yang berhasil dibuat!
    total_cost = created_count * rate_per_acc
    new_balance = current_bal
    if created_count > 0:
        ok_deduct, remaining, _ = TokenManager.deduct_balance(
            token=token_code,
            amount=total_cost,
            description=f"Generate {created_count} Akun ({req.country})",
            metadata={
                "action": "account_generator",
                "count": created_count,
                "rate": rate_per_acc,
                "country": req.country,
                "ua_mode": req.ua_mode,
            }
        )
        if ok_deduct:
            new_balance = remaining

    return {
        "ok": True,
        "requested": req.count,
        "created_count": created_count,
        "rate_per_account": rate_per_acc,
        "total_cost": total_cost,
        "remaining_balance": new_balance,
        "accounts": created_accounts,
        "errors": errors,
    }


# =============================================================================
# OWNER / ADMIN PORTAL ENDPOINTS
# =============================================================================
@app.post("/api/admin/login")
async def admin_login(req: AdminLoginRequest):
    """Verifikasi PIN Owner untuk mengakses dashboard admin."""
    if req.pin.strip() == ADMIN_SECRET_PIN:
        return {"ok": True, "token": secrets.token_hex(16)}
    return JSONResponse(status_code=401, content={"ok": False, "error": "PIN Owner tidak valid!"})


@app.get("/api/admin/tokens")
async def admin_list_tokens(x_admin_pin: Optional[str] = Header(None)):
    """Mengambil seluruh daftar token yang pernah di-generate beserta riwayat saldo."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    tokens = TokenManager.list_tokens()
    return {"ok": True, "tokens": tokens, "total": len(tokens)}


@app.post("/api/admin/tokens")
async def admin_create_token(req: AdminCreateTokenRequest, x_admin_pin: Optional[str] = Header(None)):
    """Owner membuat token akses baru dengan saldo Rupiah tertentu (10rb, 20rb, 50rb, dll)."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    token_data = TokenManager.create_token(
        initial_balance=req.amount,
        label=req.label,
        expiry_days=req.expiry_days,
        notes=req.notes,
    )
    return {"ok": True, "token": token_data}


@app.post("/api/admin/tokens/{token_code}/topup")
async def admin_topup_token(token_code: str, req: AdminTopupRequest, x_admin_pin: Optional[str] = Header(None)):
    """Owner menambah saldo token client."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    ok, new_bal, msg = TokenManager.topup_balance(token_code, req.amount, req.note)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    return {"ok": True, "message": msg, "new_balance": new_bal}


@app.patch("/api/admin/tokens/{token_code}/status")
async def admin_update_status(token_code: str, req: AdminStatusRequest, x_admin_pin: Optional[str] = Header(None)):
    """Owner mengubah status token (active, suspended, revoked)."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    ok = TokenManager.update_token_status(token_code, req.status)
    if not ok:
        raise HTTPException(status_code=400, detail="Gagal memperbarui status token.")
    return {"ok": True, "message": f"Status token {token_code} berhasil diubah ke '{req.status}'."}


@app.get("/api/admin/stats")
async def admin_stats(x_admin_pin: Optional[str] = Header(None)):
    """Ringkasan statistik sistem untuk Owner."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    tokens = TokenManager.list_tokens()
    total_balance = sum(t.get("current_balance", 0) for t in tokens)
    total_spent = sum(t.get("total_spent", 0) for t in tokens)
    accounts = BotBridge.load_accounts()
    
    from proxy_manager import default_proxy_manager
    proxies_count = len(default_proxy_manager.parsed_proxies) if default_proxy_manager else 0

    return {
        "ok": True,
        "total_tokens": len(tokens),
        "total_active_balance": total_balance,
        "total_spent": total_spent,
        "total_accounts_available": len(accounts),
        "total_proxies_active": proxies_count,
        "active_running_tasks": len(ACTIVE_TASKS),
    }


@app.get("/api/admin/pricing")
async def admin_get_pricing(x_admin_pin: Optional[str] = Header(None)):
    """Mengambil konfigurasi tarif bot dan daftar paket harga untuk dikelola admin."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")
    config = TokenManager.get_pricing_config()
    return {"ok": True, "config": config}


@app.post("/api/admin/pricing")
async def admin_update_pricing(req: AdminUpdatePricingRequest, x_admin_pin: Optional[str] = Header(None)):
    """Admin memperbarui konfigurasi tarif bot, paket harga, atau nomor kontak WhatsApp, termasuk pengaturan free trial."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")
    new_cfg = TokenManager.update_pricing_config(
        rates=req.rates,
        packages=req.packages,
        owner_wa=req.owner_wa,
        free_trial=req.free_trial,
    )
    return {"ok": True, "message": "Konfigurasi tarif dan paket harga berhasil diperbarui!", "config": new_cfg}


# =============================================================================
# FREE TRIAL ENDPOINT (Tanpa Token)
# =============================================================================
@app.post("/api/free-trial/start")
async def start_free_trial(req: FreeTrialStartRequest, request: Request):
    """
    Menjalankan free trial terbatas tanpa token.
    IP-based cooldown sesuai konfigurasi admin (default 24 jam).
    Jumlah akun & fitur yang digunakan dikonfigurasi oleh admin.
    """
    cfg = TokenManager.get_pricing_config()
    trial_cfg = cfg.get("free_trial", {})

    # Cek apakah fitur free trial diaktifkan admin
    if not trial_cfg.get("enabled", True):
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": "Free trial sedang tidak tersedia. Silakan beli paket saldo untuk menggunakan layanan ini."},
        )

    # Deteksi IP pengguna
    forwarded_for = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP")
    client_ip = (forwarded_for.split(",")[0].strip() if forwarded_for else None) or str(request.client.host)

    # Cooldown check berdasarkan IP
    cooldown_hours = int(trial_cfg.get("cooldown_hours", 24))
    cooldown_seconds = cooldown_hours * 3600
    now_ts = time.time()

    last_ts = TRIAL_IP_TIMESTAMPS.get(client_ip, 0)
    elapsed = now_ts - last_ts
    if elapsed < cooldown_seconds:
        remaining_h = int((cooldown_seconds - elapsed) // 3600)
        remaining_m = int(((cooldown_seconds - elapsed) % 3600) // 60)
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": f"Free trial Anda sudah digunakan. Tunggu {remaining_h} jam {remaining_m} menit lagi, atau beli paket saldo untuk akses tanpa batas!",
                "cooldown_remaining_seconds": int(cooldown_seconds - elapsed),
                "can_buy": True,
            },
        )

    # Resolusi novel ID
    novel_url = req.novel_url.strip()
    import re
    novel_id = novel_url
    query_hash = re.search(r"[?&]hashId=([a-zA-Z0-9]{16})", novel_id)
    if query_hash:
        novel_id = query_hash.group(1)
    else:
        match = re.search(r"([a-zA-Z0-9]{16})", novel_id)
        if match:
            novel_id = match.group(1)

    # Catat timestamp IP sebelum tugas dimulai (agar tidak bisa double-click)
    TRIAL_IP_TIMESTAMPS[client_ip] = now_ts

    # Buat task ID khusus trial
    task_id = f"trial_{int(time.time()*1000)}_{secrets.token_hex(3)}"

    # Konfigurasi dari setting admin
    accounts_count = int(trial_cfg.get("accounts_count", 5))
    do_like = bool(trial_cfg.get("do_like", True))
    do_follow = bool(trial_cfg.get("do_follow", True))

    # Mode otomatis: jika like & follow aktif → full_auto, else guest_read
    mode = "full_auto" if (do_like or do_follow) else "guest_read"

    config = {
        "accounts_count": accounts_count,
        "guest_count": accounts_count,
        "max_chapters": 3,
        "reading_delay": 6.0,
        "country": "RANDOM",
        "is_free_trial": True,
        "do_like": do_like,
        "do_follow": do_follow,
    }

    task = WebTask(
        task_id=task_id,
        token_code="FREE_TRIAL",
        novel_id=novel_id,
        mode=mode,
        config=config,
    )

    asyncio.create_task(BotBridge.run_task_lifecycle(task))

    return {
        "ok": True,
        "task_id": task_id,
        "is_trial": True,
        "trial_accounts": accounts_count,
        "cooldown_hours": cooldown_hours,
        "message": f"Free trial dimulai! Menggunakan {accounts_count} akun tamu. Sambungkan ke SSE stream untuk melihat log.",
    }



@app.post("/api/admin/accounts/generate")
async def admin_generate_accounts(req: AdminGenerateAccountsRequest, x_admin_pin: Optional[str] = Header(None)):
    """Owner men-generate akun baru secara otomatis dengan targeting negara dan mode User-Agent."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    try:
        from core.auto_signup import RegistrationRunner
    except ImportError:
        from auto_signup import RegistrationRunner

    core_akun_file = BASE_DIR / "core" / "akun.txt"
    root_akun_file = BASE_DIR.parent / "akun.txt"

    def _execute_batch():
        runner = RegistrationRunner(accounts_file=str(core_akun_file))
        created_list = []
        errors_list = []
        for idx in range(1, req.count + 1):
            try:
                res = runner.register_account(country_code=req.country, ua_mode=req.ua_mode)
                if res.get("status") == "success":
                    acc = res.get("account", {})
                    created_list.append(acc)
                    # Sinkronkan ke akun.txt di root direktori jika berbeda
                    try:
                        line = json.dumps(acc, ensure_ascii=False)
                        with open(root_akun_file, "a", encoding="utf-8") as rf:
                            rf.write(line + "\n")
                    except Exception:
                        pass
                else:
                    errors_list.append(f"Akun #{idx}: " + str(res.get("error", "Gagal mendaftarkan akun")))
            except Exception as exc:
                errors_list.append(f"Akun #{idx}: " + str(exc))
        return created_list, errors_list

    created_accounts, errors = await asyncio.to_thread(_execute_batch)
    total_avail = len(BotBridge.load_accounts())

    return {
        "ok": True,
        "requested": req.count,
        "created_count": len(created_accounts),
        "accounts": created_accounts,
        "errors": errors,
        "total_accounts_available": total_avail,
    }


@app.get("/api/admin/accounts")
async def admin_list_accounts(
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    x_admin_pin: Optional[str] = Header(None)
):
    """Mengambil daftar seluruh akun yang tersimpan beserta kredensial dan metadata."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    all_accounts = BotBridge.load_accounts()

    if search:
        s = search.strip().lower()
        filtered = []
        for acc in all_accounts:
            email = str(acc.get("email", "")).lower()
            uid = str(acc.get("user_id", ""))
            country = str(acc.get("country", "")).lower()
            nick = str(acc.get("nickname", "")).lower()
            if s in email or s in uid or s in country or s in nick:
                filtered.append(acc)
        total = len(filtered)
        accounts_slice = filtered[offset: offset + limit]
    else:
        total = len(all_accounts)
        accounts_slice = all_accounts[offset: offset + limit]

    sanitized = []
    for acc in accounts_slice:
        pwd = str(acc.get("password", ""))
        sanitized.append({
            "email": acc.get("email"),
            "user_id": acc.get("user_id"),
            "country": acc.get("country", "-"),
            "nickname": acc.get("nickname", "-"),
            "password": pwd,
            "has_token": bool(acc.get("access_token")),
            "created_at": acc.get("created_at", "-"),
        })

    return {
        "ok": True,
        "total": total,
        "accounts": sanitized,
    }


@app.get("/api/admin/accounts/download")
async def admin_download_accounts(pin: Optional[str] = None, x_admin_pin: Optional[str] = Header(None)):
    """Mengunduh berkas cadangan akun.txt langsung dari server."""
    auth_pin = x_admin_pin or pin
    if auth_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    core_akun_file = BASE_DIR / "core" / "akun.txt"
    if not core_akun_file.exists():
        core_akun_file = BASE_DIR.parent / "akun.txt"

    if not core_akun_file.exists():
        raise HTTPException(status_code=404, detail="File akun.txt tidak ditemukan.")

    return FileResponse(
        path=str(core_akun_file),
        filename="akun.txt",
        media_type="text/plain",
    )


# =============================================================================
# ADMIN PROXY MANAGEMENT & AUTO-SYNC
# =============================================================================
@app.post("/api/admin/proxies/sync")
async def admin_sync_proxies(x_admin_pin: Optional[str] = Header(None)):
    """Sinkronisasi proxy langsung dari HypeProxy API ke seluruh berkas proxies.txt dan reload ke memori."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    def _do_sync():
        from proxy_manager import HypeProxyClient, default_proxy_manager
        core_px = BASE_DIR / "core" / "proxies.txt"
        root_px = BASE_DIR.parent / "proxies.txt"

        urls = HypeProxyClient.sync_proxies_to_file(output_file=str(core_px), user_only=True)
        if root_px.exists() or True:
            try:
                HypeProxyClient.sync_proxies_to_file(output_file=str(root_px), user_only=True)
            except Exception:
                pass

        default_proxy_manager.load_proxies()
        return len(urls), urls

    try:
        count, urls = await asyncio.to_thread(_do_sync)
        return {
            "ok": True,
            "message": f"Berhasil menyinkronkan {count} node proxy aktif dari HypeProxy!",
            "count": count,
            "proxies_sample": [u.split("@")[-1] for u in urls[:5]] if urls else [],
        }
    except Exception as exc:
        return {"ok": False, "error": f"Gagal sinkronisasi proxy: {exc}"}


@app.get("/api/admin/proxies/status")
async def admin_get_proxies_status(x_admin_pin: Optional[str] = Header(None)):
    """Mengambil status koneksi proxy, daftar slot HypeProxy, dan saldo akun HypeProxy."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")

    def _fetch_status():
        from proxy_manager import HypeProxyClient, default_proxy_manager
        default_proxy_manager.reload_if_modified()
        profile = HypeProxyClient.get_profile()
        proxies = HypeProxyClient.get_proxies(user_only=True)
        local_count = len(default_proxy_manager.parsed_proxies) if default_proxy_manager else 0
        return profile, proxies, local_count

    try:
        profile, proxies, local_count = await asyncio.to_thread(_fetch_status)
        u_info = profile.get("user", {}) if isinstance(profile, dict) else {}
        return {
            "ok": True,
            "hypeproxy_connected": bool(profile.get("ok")),
            "username": u_info.get("username", "-"),
            "email": u_info.get("email", "-"),
            "balance": u_info.get("balance", 0),
            "api_slots_count": len(proxies),
            "local_proxies_count": local_count,
            "slots": [
                {
                    "id": p.get("id"),
                    "port": p.get("port"),
                    "status": p.get("status"),
                    "country": p.get("country"),
                    "exitIp": p.get("exitIp"),
                    "tierLabel": p.get("tierLabel"),
                }
                for p in proxies
            ],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# =============================================================================
# WIJAYAPAY PAYMENT GATEWAY & AUTOMATION ENDPOINTS
# =============================================================================
@app.get("/api/payment/channels")
async def get_payment_channels():
    """Mengambil daftar metode pembayaran yang aktif dari WijayaPay."""
    try:
        channels = await asyncio.to_thread(default_wijayapay_client.get_payment_methods)
        return {"ok": True, "channels": channels}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "channels": []}


@app.post("/api/payment/create")
async def create_payment(req: PaymentCreateRequest):
    """
    Membuat transaksi pembayaran otomatis via WijayaPay (QRIS atau Virtual Account).
    order_type: 'new_token' atau 'topup'.
    """
    order_type = (req.order_type or "new_token").lower().strip()
    if order_type not in ("new_token", "topup"):
        raise HTTPException(status_code=400, detail="Tipe pesanan harus 'new_token' atau 'topup'.")

    token_code = (req.token_code or "").strip().upper() if req.token_code else None
    if order_type == "topup":
        if not token_code:
            raise HTTPException(status_code=400, detail="Kode token diperlukan untuk top up.")
        t_data = TokenManager.get_token(token_code)
        if not t_data:
            raise HTTPException(status_code=404, detail=f"Token '{token_code}' tidak ditemukan.")

    # Unique Reference ID
    ref_id = f"ORD-{int(time.time())}-{secrets.token_hex(3).upper()}"
    payment_code = (req.payment_code or "QRIS").upper().strip()

    # Resolve dynamic package name
    pkg_name = None
    cfg = TokenManager.get_pricing_config()
    for p in cfg.get("packages", []):
        if int(p.get("price", 0)) == int(req.nominal):
            pkg_name = str(p.get("name")).strip()
            break

    if order_type == "topup":
        desc = f"Topup Saldo Token {token_code} - Rp {req.nominal:,}"
    else:
        pkg_label = pkg_name or "Token Akses Bot"
        desc = f"Beli {pkg_label} - Rp {req.nominal:,}"

    try:
        result = await asyncio.to_thread(
            default_wijayapay_client.create_transaction,
            ref_id=ref_id,
            nominal=req.nominal,
            payment_code=payment_code,
            customer_name=req.customer_name or f"User-{ref_id[-6:]}",
            customer_email=req.customer_email or f"user.{ref_id[-6:].lower()}@rinara.dev",
            customer_phone=req.customer_phone or "081234567890",
            keterangan=desc,
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Gagal menghubungi gateway WijayaPay: {exc}"})

    if not result.get("ok"):
        raw_info = result.get("raw") or {}
        err_msg = result.get("error") or result.get("message")
        if not err_msg and isinstance(raw_info, dict):
            err_msg = raw_info.get("message") or raw_info.get("error")
        if not err_msg:
            err_msg = "Gagal membuat transaksi di WijayaPay."

        if "whitelist" in str(err_msg).lower():
            err_msg = f"{err_msg}. Silakan tambahkan IP tersebut ke menu Whitelist IP di Dashboard WijayaPay."

        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": str(err_msg),
                "raw": raw_info,
            }
        )

    w_data = result.get("data") or {}

    # Record payment to local database
    payment_record = TokenManager.record_payment(
        ref_id=ref_id,
        order_type=order_type,
        nominal=req.nominal,
        payment_code=payment_code,
        gateway_data=w_data,
        token_code=token_code,
    )

    return {
        "ok": True,
        "ref_id": ref_id,
        "order_type": order_type,
        "nominal": req.nominal,
        "payment_code": payment_code,
        "token_code": token_code,
        "checkout": {
            "trx_id": payment_record.get("trx_reference"),
            "total_bayar": payment_record.get("total_bayar"),
            "fee": w_data.get("fee", 0),
            "qr_link": payment_record.get("qr_link"),
            "qr_string": payment_record.get("qr_string"),
            "nomor_va": payment_record.get("nomor_va"),
            "expired": payment_record.get("expired"),
            "status": payment_record.get("status", "pending"),
            "tutorial": payment_record.get("tutorial") or [],
        }
    }


@app.get("/api/payment/check/{ref_id}")
async def check_payment(ref_id: str):
    """
    Cek status pembayaran ref_id.
    Jika di gateway sudah lunas, langsung otomatis memicu fulfillment token (generate token baru / top-up saldo).
    """
    payment = TokenManager.get_payment(ref_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Data transaksi tidak ditemukan.")

    # Jika sudah lunas secara lokal
    if payment.get("status") == "paid":
        return {
            "ok": True,
            "ref_id": ref_id,
            "status": "paid",
            "is_paid": True,
            "generated_token": payment.get("generated_token"),
            "target_token": payment.get("target_token"),
            "order_type": payment.get("order_type"),
            "nominal": payment.get("nominal"),
            "message": "Pembayaran lunas! Token telah aktif.",
        }

    # Jika masih pending, query ke WijayaPay secara live
    try:
        w_status = await asyncio.to_thread(default_wijayapay_client.check_status, ref_id=ref_id)
        if w_status.get("ok"):
            status_str = str(w_status.get("status", "")).lower()
            if status_str in ("paid", "success", "berhasil"):
                trx_ref = w_status.get("data", {}).get("trx_id")
                fulfilled, ful_data, msg = TokenManager.fulfill_payment(ref_id, trx_reference=trx_ref)
                return {
                    "ok": True,
                    "ref_id": ref_id,
                    "status": "paid",
                    "is_paid": True,
                    "generated_token": (ful_data or {}).get("generated_token"),
                    "target_token": (ful_data or {}).get("target_token"),
                    "order_type": (ful_data or {}).get("order_type"),
                    "nominal": (ful_data or {}).get("nominal"),
                    "message": msg or "Pembayaran lunas! Token telah aktif.",
                }
            elif status_str in ("expired", "kadaluarsa", "failed", "gagal"):
                TokenManager.update_payment_status(ref_id, "expired")
                return {
                    "ok": True,
                    "ref_id": ref_id,
                    "status": status_str,
                    "is_paid": False,
                    "message": "Transaksi telah kadaluarsa.",
                }
    except Exception:
        pass

    return {
        "ok": True,
        "ref_id": ref_id,
        "status": payment.get("status", "pending"),
        "is_paid": False,
        "total_bayar": payment.get("total_bayar", payment.get("nominal")),
        "message": "Menunggu pembayaran oleh pengguna...",
    }


@app.post("/api/payment/callback/wijayapay")
async def wijayapay_webhook_callback(
    request: Request,
    x_callback_signature: Optional[str] = Header(None, alias="X-Callback-Signature"),
):
    """
    Webhook Endpoint untuk menerima notifikasi pembayaran otomatis dari WijayaPay.
    Memverifikasi X-Callback-Signature dan langsung melakukan automasi aktivasi/top-up token.
    """
    try:
        raw_body = await request.body()
        data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        form = await request.form()
        data = dict(form)

    ref_id = data.get("ref_id") or data.get("order_id")
    if not ref_id:
        return JSONResponse(status_code=400, content={"ok": False, "message": "ref_id tidak ditemukan"})

    # Validasi signature jika header disediakan
    if x_callback_signature:
        is_valid_sig = default_wijayapay_client.verify_callback_signature(ref_id, x_callback_signature)
        if not is_valid_sig:
            return JSONResponse(status_code=403, content={"ok": False, "message": "Invalid callback signature"})

    status = str(data.get("status", "")).lower()
    trx_ref = data.get("trx_id") or data.get("reference")

    if status in ("paid", "success", "berhasil"):
        ok, ful_data, msg = TokenManager.fulfill_payment(ref_id, trx_reference=trx_ref)
        return {"success": True, "message": msg, "fulfillment": ful_data}
    elif status in ("expired", "failed", "gagal"):
        TokenManager.update_payment_status(ref_id, "expired")
        return {"success": True, "message": f"Status updated to {status}"}

    return {"success": True, "message": "Notification received"}


@app.get("/api/admin/payments")
async def admin_list_payments(x_admin_pin: Optional[str] = Header(None)):
    """Owner melihat riwayat transaksi pembayaran WijayaPay."""
    if x_admin_pin != ADMIN_SECRET_PIN:
        raise HTTPException(status_code=401, detail="Unauthorized: PIN Owner dibutuhkan.")
    payments = TokenManager.list_payments(limit=50)
    return {"ok": True, "payments": payments}


@app.on_event("startup")
async def start_periodic_proxy_sync():
    """Memeriksa secara otomatis setiap 3 menit apakah owner baru membeli proxy di HypeProxy."""
    asyncio.create_task(_background_proxy_sync_loop())


async def _background_proxy_sync_loop():
    while True:
        try:
            await asyncio.sleep(180)  # Cek setiap 3 menit
            def _check():
                from proxy_manager import HypeProxyClient, default_proxy_manager
                urls = HypeProxyClient.fetch_active_proxy_urls(user_only=True)
                if urls and len(urls) != len(default_proxy_manager.parsed_proxies):
                    logger.info(f"[Auto-Sync] Perubahan slot HypeProxy terdeteksi ({len(default_proxy_manager.parsed_proxies)} -> {len(urls)}). Menyinkronkan...")
                    core_px = BASE_DIR / "core" / "proxies.txt"
                    root_px = BASE_DIR.parent / "proxies.txt"
                    HypeProxyClient.sync_proxies_to_file(output_file=str(core_px), user_only=True)
                    if root_px.exists() or True:
                        try:
                            HypeProxyClient.sync_proxies_to_file(output_file=str(root_px), user_only=True)
                        except Exception:
                            pass
                    default_proxy_manager.load_proxies()
            await asyncio.to_thread(_check)
        except Exception as exc:
            logger.debug(f"Background proxy sync exception: {exc}")
