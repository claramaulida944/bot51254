"""
Klien API Asynchronous Resmi Android (Stealth API Client).
Menyediakan komunikasi HTTP lengkap dengan seluruh header otentik,
pelacakan siklus hidup akun (pre-check -> signup -> appsflyer attribution -> hydration),
serta pelaporan telemetri membaca resmi.
"""

import asyncio
import json
import logging
import random
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from .config import (
    BASE_URL,
    APP_VERSION,
    APP_VARIANT,
    PLATFORM,
    FRONTEND_CAPABILITY,
    MISSION_CAPABILITIES,
    BOOKSTORE_GENRE_GATE,
)
from .profile import AccountProfile, ProfileGenerator
from .proxy import StealthProxyManager, default_proxy_manager, COUNTRY_METADATA

logger = logging.getLogger("StealthClient")


class StealthApiClient:
    """Klien API tingkat tinggi yang mereplikasi perilaku native aplikasi Android."""

    def __init__(
        self,
        profile: Optional[AccountProfile] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        proxy_manager: Optional[StealthProxyManager] = None,
        current_proxy: Optional[str] = None,
        account_data: Optional[Dict[str, Any]] = None,
        save_account_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.profile = profile or ProfileGenerator.generate_profile()
        self.access_token = access_token or ""
        self.refresh_token = refresh_token or ""
        self.guest_id: Optional[str] = None
        self.guest_token: Optional[str] = None
        self.proxy_manager = proxy_manager or default_proxy_manager
        self.current_proxy = current_proxy
        self.account_data = account_data
        self.save_account_cb = save_account_cb
        self.session_id = ProfileGenerator.generate_session_id()
        self._client: Optional[httpx.AsyncClient] = None

    def get_headers(self) -> Dict[str, str]:
        """Membangun header lengkap selaras 100% dengan inspeksi traffic resmi Android."""
        meta = COUNTRY_METADATA.get(self.profile.country, COUNTRY_METADATA["ID"])
        try:
            if ZoneInfo is not None:
                local_date = datetime.now(ZoneInfo(self.profile.timezone)).strftime("%Y-%m-%d")
            else:
                local_date = datetime.now().strftime("%Y-%m-%d")
        except Exception:
            local_date = datetime.now().strftime("%Y-%m-%d")

        headers = {
            "user-agent": self.profile.user_agent,
            "x-platform": PLATFORM,
            "x-app-variant": APP_VARIANT,
            "x-app-version": APP_VERSION,
            "x-device-id": self.profile.device_id,
            "x-anonymous-id": self.profile.anonymous_id,
            "x-timezone": self.profile.timezone,
            "x-local-date": local_date,
            "x-user-country": meta["service_country"],
            "x-user-raw-country": meta["raw_country"],
            "x-frontend-capability": FRONTEND_CAPABILITY,
            "x-q-mission-capabilities": MISSION_CAPABILITIES,
            "x-bookstore-genre-gate": BOOKSTORE_GENRE_GATE,
            "accept-language": self.profile.accept_language,
            "content-type": "application/json",
            "accept": "application/json",
        }

        if self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"
        elif self.guest_token:
            headers["x-guest-token"] = self.guest_token
            headers["cookie"] = f"qf_guest_reader={self.guest_token}"

        return headers

    async def get_client(self) -> httpx.AsyncClient:
        """Membuat atau mengambil instance httpx.AsyncClient dengan proxy aktif."""
        if self._client is None or self._client.is_closed:
            if not self.current_proxy and self.proxy_manager.has_proxies:
                self.current_proxy = self.proxy_manager.pop_proxy(self.profile.country)

            client_kwargs: Dict[str, Any] = {
                "base_url": BASE_URL,
                "timeout": httpx.Timeout(30.0, connect=8.0, read=20.0),
                "headers": self.get_headers(),
                "http2": False if self.current_proxy else True,
            }
            if self.current_proxy:
                client_kwargs["proxy"] = self.current_proxy

            self._client = httpx.AsyncClient(**client_kwargs)

        return self._client

    async def rotate_proxy_if_needed(self, reason: Any = None):
        """Memutar proxy jika koneksi terhambat atau terkena rate limit."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

        if self.current_proxy:
            self.proxy_manager.mark_failed(self.current_proxy, reason)

        self.current_proxy = self.proxy_manager.pop_proxy(self.profile.country)
        logger.debug("Proxy diputar ke: %s", self.current_proxy)

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # SIKLUS HIDUP REGISTRASI AKUN RESMI (Bypass Fraud Detection)
    # =========================================================================

    async def check_email_availability(self) -> bool:
        """Langkah 1: Cek ketersediaan email sebelum registrasi (Perilaku wajib aplikasi)."""
        client = await self.get_client()
        try:
            resp = await client.post("/api/auth/email/check", json={"email": self.profile.email})
            return resp.status_code == 200
        except Exception:
            return True

    async def refresh_access_token(self) -> bool:
        """Memperbarui access_token menggunakan refresh_token."""
        if not self.refresh_token:
            return False
        try:
            headers = self.get_headers()
            headers.pop("authorization", None)
            kwargs: Dict[str, Any] = {
                "base_url": BASE_URL,
                "timeout": httpx.Timeout(15.0),
                "headers": headers,
                "http2": False if self.current_proxy else True,
            }
            if self.current_proxy:
                kwargs["proxy"] = self.current_proxy

            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.post(
                    "/api/auth/token/refresh",
                    json={"refresh_token": self.refresh_token},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    new_acc = data.get("access_token")
                    new_ref = data.get("refresh_token")
                    if new_acc:
                        self.access_token = new_acc
                        if new_ref:
                            self.refresh_token = new_ref
                        if self._client and not self._client.is_closed:
                            await self._client.aclose()
                        self._client = None
                        if self.account_data:
                            self.account_data["access_token"] = new_acc
                            if new_ref:
                                self.account_data["refresh_token"] = new_ref
                            if self.save_account_cb:
                                self.save_account_cb(self.account_data)
                        logger.info("Access token berhasil diperbarui otomatis via refresh_token.")
                        return True
        except Exception as exc:
            logger.debug("Gagal refresh_access_token: %s", exc)
        return False

    async def relogin_with_password(self) -> bool:
        """Melakukan login ulang penuh menggunakan email & password akun."""
        email = getattr(self.profile, "email", "") or (self.account_data.get("email", "") if self.account_data else "")
        password = getattr(self.profile, "password", "") or (self.account_data.get("password", "") if self.account_data else "")
        if not email or not password:
            return False
        try:
            headers = self.get_headers()
            headers.pop("authorization", None)
            kwargs: Dict[str, Any] = {
                "base_url": BASE_URL,
                "timeout": httpx.Timeout(15.0),
                "headers": headers,
                "http2": False if self.current_proxy else True,
            }
            if self.current_proxy:
                kwargs["proxy"] = self.current_proxy

            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.post(
                    "/api/auth/login",
                    json={"login_id": email, "password": password},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    new_acc = data.get("access_token")
                    new_ref = data.get("refresh_token")
                    if new_acc:
                        self.access_token = new_acc
                        if new_ref:
                            self.refresh_token = new_ref
                        if self._client and not self._client.is_closed:
                            await self._client.aclose()
                        self._client = None
                        if self.account_data:
                            self.account_data["access_token"] = new_acc
                            if new_ref:
                                self.account_data["refresh_token"] = new_ref
                            if self.save_account_cb:
                                self.save_account_cb(self.account_data)
                        logger.info("Login ulang berhasil untuk %s.", email)
                        return True
        except Exception as exc:
            logger.debug("Gagal relogin_with_password: %s", exc)
        return False

    async def ensure_active_session(self, max_attempts: int = 3) -> bool:
        """Memastikan token masih aktif; jika kedaluwarsa, lakukan refresh atau relogin otomatis dengan auto-rotate proxy."""
        for attempt in range(1, max_attempts + 1):
            if not self.access_token:
                ok = await self.relogin_with_password()
                if ok:
                    return True
                if attempt < max_attempts:
                    await self.rotate_proxy_if_needed("relogin without token failed")
                    continue
                return False

            try:
                client = await self.get_client()
                r = await client.get("/api/q/account")
                if r.status_code == 200:
                    return True
                if r.status_code in (401, 403):
                    logger.info("Token kedaluwarsa (HTTP %d). Memperbarui sesi...", r.status_code)
                    if await self.refresh_access_token():
                        return True
                    if await self.relogin_with_password():
                        return True
                    return False
                elif r.status_code in (429, 502, 503, 504):
                    await self.rotate_proxy_if_needed(f"HTTP {r.status_code}")
                    continue
            except Exception as exc:
                logger.debug("Koneksi gagal pada ensure_active_session (%s). Rotasi proxy (%d/%d)...", exc, attempt, max_attempts)
                await self.rotate_proxy_if_needed(exc)
                continue

        return False

    async def update_nickname(self, new_nickname: str) -> Tuple[bool, str]:
        """
        Mengubah nama pengguna (nickname) akun di API server (/api/auth/profile).
        """
        # Selalu pastikan sesi aktif (refresh token jika expired)
        try:
            ok = await self.ensure_active_session()
            if not ok:
                return False, "Gagal refresh token / login ulang"
        except Exception as exc:
            return False, f"Session error: {exc}"

        try:
            client = await self.get_client()
            resp = await client.patch(
                "/api/auth/profile",
                json={"nickname": new_nickname.strip()},
            )
            if resp.status_code == 200:
                data = resp.json()
                final_nick = data.get("nickname") or new_nickname.strip()
                self.profile.nickname = final_nick
                if self.account_data:
                    self.account_data["nickname"] = final_nick
                    if self.save_account_cb:
                        self.save_account_cb(self.account_data)
                return True, final_nick
            # Ambil detail error dari response body
            try:
                err_body = resp.json()
                err_msg = err_body.get("message") or err_body.get("detail") or resp.text[:120]
            except Exception:
                err_msg = resp.text[:120] if resp.text else "(empty response)"
            return False, f"HTTP {resp.status_code}: {err_msg}"
        except Exception as exc:
            return False, str(exc)

    async def claim_daily_q(self, log_func=None) -> Dict[str, Any]:
        """
        Mengklaim benefit / Q harian akun secara otomatis (1x per hari).
        - Menjalankan reader-report touch & bookstore exposure.
        - Memanggil /api/q/summary?fresh=true untuk membaca misi aktif.
        - Mengklaim misi 'attend' (Daily Attendance bernilai 1000 Q) dan misi selesai lainnya.
        - Menyimpan riwayat tanggal klaim dan saldo ke data akun.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        # Jika sudah klaim hari ini dan saldo > 0, skip
        if self.account_data and self.account_data.get("last_q_claim_date") == today and self.account_data.get("q_balance", 0) > 0:
            if log_func:
                log_func(f"[dim]Klaim Q harian sudah dilakukan hari ini ({today}) - Saldo: {self.account_data.get('q_balance', 0)} Q.[/]")
            return {"status": "already_claimed", "date": today, "balance": self.account_data.get("q_balance", 0)}

        ok = await self.ensure_active_session()
        if not ok:
            if log_func:
                log_func("[bold red]✗ Gagal verifikasi sesi / login akun[/]")
            return {"status": "error", "message": "Session inactive", "balance": 0, "earned": 0}

        client = await self.get_client()

        # 1. Pemicu telemetri kunjungan & exposure (persis aplikasi resmi)
        try:
            await client.post("/api/v1/reader-report/visits/touch")
        except Exception:
            pass

        try:
            await client.post("/api/v1/reader-report/exposure/claim", json={"surface": "bookstore"})
        except Exception:
            pass

        # 2. Ambil ringkasan misi Q aktif & saldo awal
        initial_balance = 0
        missions = []
        try:
            summary_r = await client.get("/api/q/summary?fresh=true")
            if summary_r.status_code == 200:
                s_data = summary_r.json()
                initial_balance = s_data.get("balance", 0)
                missions = s_data.get("missions", [])
            else:
                bal_r = await client.get("/api/q/account")
                if bal_r.status_code == 200:
                    initial_balance = bal_r.json().get("balance", 0)
        except Exception:
            pass

        # 3. Klaim misi Daily Attendance (attend = 1000 Q) dan misi lain yang sudah siap
        claimed_any = False
        granted_total = 0
        final_balance = initial_balance

        attend_mission = next((m for m in missions if m.get("id") == "attend"), None)
        # Jika belum diklaim atau attend_mission tidak terdeteksi spesifik, klaim attend
        if attend_mission is None or not attend_mission.get("claimed", False):
            try:
                claim_r = await client.post("/api/q/claim", json={"missionId": "attend"})
                if claim_r.status_code == 200:
                    c_data = claim_r.json()
                    granted = c_data.get("granted", 0)
                    granted_total += granted
                    final_balance = c_data.get("balance", final_balance + granted)
                    claimed_any = True
            except Exception as exc:
                logger.debug("Gagal klaim misi attend: %s", exc)

        # Cek dan klaim misi lain yang sudah selesai (progress >= goal)
        for m in missions:
            m_id = m.get("id")
            if m_id != "attend" and not m.get("claimed", False) and m.get("progress", 0) >= m.get("goal", 1):
                try:
                    c_r = await client.post("/api/q/claim", json={"missionId": m_id})
                    if c_r.status_code == 200:
                        c_data = c_r.json()
                        granted = c_data.get("granted", 0)
                        granted_total += granted
                        final_balance = c_data.get("balance", final_balance + granted)
                        claimed_any = True
                except Exception:
                    pass

        # 4. Sinkronisasi saldo akhir dari /api/q/account
        try:
            bal_r2 = await client.get("/api/q/account")
            if bal_r2.status_code == 200:
                final_balance = bal_r2.json().get("balance", final_balance)
        except Exception:
            pass

        if self.account_data:
            self.account_data["last_q_claim_date"] = today
            self.account_data["q_balance"] = final_balance
            if self.save_account_cb:
                self.save_account_cb(self.account_data)

        earned = max(granted_total, final_balance - initial_balance)
        if earned > 0 or claimed_any:
            msg = f"Klaim Q Sukses! Saldo: {final_balance} Q (+{earned})"
            if log_func:
                log_func(f"[bold green]🎁 {msg}[/]")
            return {"status": "success", "date": today, "balance": final_balance, "earned": earned}
        else:
            msg = f"Klaim Q hari ini sudah dilakukan sebelumnya. Saldo: {final_balance} Q (+0)"
            if log_func:
                log_func(f"[cyan]ℹ {msg}[/]")
            return {"status": "already_claimed", "date": today, "balance": final_balance, "earned": 0}

    async def perform_organic_signup(self) -> Tuple[bool, str]:
        """
        Menjalankan alur pendaftaran akun lengkap:
        1. Pre-check email availability.
        2. Kirim POST /api/auth/signup.
        3. Sinkronisasi atribusi AppsFlyer (POST /api/auth/signup-attribution).
        4. Hydration state awal akun (/api/q/account & categories).
        Dilengkapi multi-proxy auto-retry jika proxy pertama timeout / gagal connect.
        """
        meta = COUNTRY_METADATA.get(self.profile.country, COUNTRY_METADATA["ID"])
        payload = {
            "email": self.profile.email,
            "password": self.profile.password,
            "password_confirm": self.profile.password,
            "birth_date": self.profile.birth_date,
            "gender": self.profile.gender,
            "is_agree_terms": True,
            "meta_attribution": {"source_site": "appsflyer"},
            "skip_email_verification": True,
            "signup_market_country": meta["service_country"],
            "signup_market_source": "auto",
        }

        max_attempts = 5
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            client = await self.get_client()
            try:
                # 1. Pre-check
                await self.check_email_availability()
                await asyncio.sleep(random.uniform(0.3, 0.7))

                # 2. Signup
                resp = await client.post("/api/auth/signup", json=payload)
                if resp.status_code == 201:
                    data = resp.json()
                    self.access_token = data.get("access_token", "")
                    self.refresh_token = data.get("refresh_token", "")
                    user_info = data.get("user", {})
                    login_id = user_info.get("login_id") or self.profile.email

                    # Perbarui header client dengan access_token baru
                    client.headers["authorization"] = f"Bearer {self.access_token}"

                    # 3. [KRUSIAL] Sinkronisasi Atribusi AppsFlyer
                    await self._sync_signup_attribution()

                    # 4. [KRUSIAL] Hydration data awal pengguna
                    await self._hydrate_user_state()

                    if self.current_proxy:
                        self.proxy_manager.mark_used(self.current_proxy)

                    return True, "Registrasi & Atribusi AppsFlyer Sukses"

                elif resp.status_code in (403, 429):
                    last_error = f"Rate limit / Cloudflare (HTTP {resp.status_code})"
                    logger.warning("[Proxy 429/403] Percobaan %d/%d: %s. Memutar proxy...", attempt, max_attempts, last_error)
                    await self.rotate_proxy_if_needed(last_error)
                    if attempt < max_attempts:
                        await asyncio.sleep(0.5)
                        continue
                    return False, last_error

                else:
                    return False, f"Server menolak: HTTP {resp.status_code} ({resp.text[:80]})"

            except Exception as exc:
                err_msg = str(exc) or repr(exc)
                if "Proxy" in type(exc).__name__ or "407" in err_msg:
                    err_desc = f"Proxy Bermasalah ({type(exc).__name__}: {err_msg})"
                elif "Timeout" in type(exc).__name__:
                    err_desc = f"Proxy Timeout ({type(exc).__name__}: {err_msg})"
                else:
                    err_desc = f"{type(exc).__name__}: {err_msg}"

                last_error = err_desc
                logger.warning(
                    "[Proxy Error] Percobaan %d/%d via proxy gagal (%s). Mencoba slot proxy berikutnya...",
                    attempt,
                    max_attempts,
                    err_desc,
                )
                await self.rotate_proxy_if_needed(err_desc)
                if attempt < max_attempts:
                    await asyncio.sleep(0.5)
                    continue

        return False, f"Koneksi gagal setelah {max_attempts} proxy ({last_error})"

    async def _sync_signup_attribution(self):
        """Mengirimkan konfirmasi atribusi instalasi AppsFlyer ke server."""
        try:
            client = await self.get_client()
            await client.post(
                "/api/auth/signup-attribution",
                json={"meta_attribution": {"source_site": "appsflyer"}},
            )
        except Exception:
            pass

    async def _hydrate_user_state(self):
        """Memuat data dasar akun layaknya aplikasi yang baru pertama kali dibuka."""
        try:
            client = await self.get_client()
            # Panggil dompet, novel terakhir, dan kategori
            await client.get("/api/q/account")
            await asyncio.sleep(0.3)
            await client.get("/api/reading/novel-last-reads?limit=50")
            await asyncio.sleep(0.3)
            await client.get("/api/v1/categories")
            await client.post("/api/v1/reader-report/visits/touch")
            await asyncio.sleep(0.3)
            await client.get("/api/q/summary?fresh=true")
        except Exception:
            pass

    async def send_email_verification(self) -> Tuple[bool, str]:
        """
        Mengirim permintaan kode verifikasi ke email akun terdaftar.
        Backend otomatis membaca alamat email dari token JWT Bearer.
        Mendukung retry dengan rotasi proxy jika socket gagal.
        """
        if not self.access_token:
            return False, "Belum memiliki access_token"

        for attempt in range(1, 4):
            client = await self.get_client()
            try:
                resp = await client.post("/api/auth/email/send-verification")
                if resp.status_code == 200:
                    return True, "Kode verifikasi berhasil dikirim"
                elif resp.status_code in (403, 429):
                    await self.rotate_proxy_if_needed(f"HTTP {resp.status_code}")
                    await asyncio.sleep(0.5)
                    continue
                return False, f"Server menolak: HTTP {resp.status_code} ({resp.text[:80]})"
            except Exception as exc:
                await self.rotate_proxy_if_needed(str(exc))
                if attempt < 3:
                    await asyncio.sleep(0.5)
                    continue
                return False, f"Koneksi gagal: {exc}"
        return False, "Gagal request verifikasi email"

    async def verify_email_code(self, code: str) -> Tuple[bool, str]:
        """
        Mengirim kode OTP 6-digit untuk memverifikasi email akun.
        Mengubah status akun menjadi is_email_verified = True.
        Mendukung retry dengan rotasi proxy jika socket gagal.
        """
        if not self.access_token:
            return False, "Belum memiliki access_token"

        for attempt in range(1, 4):
            client = await self.get_client()
            try:
                resp = await client.post(
                    "/api/auth/email/verify",
                    json={"code": str(code).strip()},
                )
                if resp.status_code == 200:
                    return True, "Email berhasil diverifikasi!"
                elif resp.status_code in (403, 429):
                    await self.rotate_proxy_if_needed(f"HTTP {resp.status_code}")
                    await asyncio.sleep(0.5)
                    continue
                return False, f"Kode ditolak: HTTP {resp.status_code} ({resp.text[:80]})"
            except Exception as exc:
                await self.rotate_proxy_if_needed(str(exc))
                if attempt < 3:
                    await asyncio.sleep(0.5)
                    continue
                return False, f"Koneksi gagal: {exc}"
        return False, "Gagal verifikasi kode OTP"

    # =========================================================================
    # PEMBACAAN NOVEL & TELEMETRI AKTIF
    # =========================================================================

    async def get_novel_detail(self, novel_id: str) -> Optional[Dict[str, Any]]:
        """Mengambil metadata novel (sinopsis, cover, author) untuk alur discovery katalog."""
        for attempt in range(1, 3):
            try:
                client = await self.get_client()
                resp = await client.get(f"/api/v1/novels/{novel_id}")
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(0.5)
        return None

    async def get_novel_chapters(self, novel_id: str) -> List[Dict[str, Any]]:
        """Mengambil daftar bab novel dengan toleransi gangguan proxy."""
        for attempt in range(1, 4):
            try:
                client = await self.get_client()
                resp = await client.get(f"/api/v1/novels/{novel_id}/chapters?order=asc")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("chapters", []) or data.get("items", []) or []
            except Exception as exc:
                await self.rotate_proxy_if_needed(str(exc))
                if attempt < 3:
                    await asyncio.sleep(1.0)
        return []

    async def get_chapter_detail(self, novel_id: str, chapter_id: str) -> Optional[Dict[str, Any]]:
        """Mengambil narasi isi teks bab dengan toleransi gangguan proxy."""
        for attempt in range(1, 4):
            try:
                client = await self.get_client()
                resp = await client.get(f"/api/v1/novels/{novel_id}/chapters/{chapter_id}?rewarded_reader=true")
                if resp.status_code == 200:
                    return resp.json()
            except Exception as exc:
                await self.rotate_proxy_if_needed(str(exc))
                if attempt < 3:
                    await asyncio.sleep(1.0)
        return None

    async def send_reading_telemetry(
        self,
        novel_id: str,
        chapter_id: str,
        chapter_num: int,
        reading_time_sec: float,
        depth_percent: int = 100,
        completed: bool = True,
    ):
        """
        Mengirimkan telemetri progres membaca dan view bab resmi (Royalty Log):
        1. Member payout log (/api/reading/v2/logs/post-view).
        2. Heartbeat session (/api/reading/sessions/heartbeat).
        3. Internal analytics event (chapter_completed & reading_time_sec).
        """
        client = await self.get_client()
        logical_sess_id = f"rls_{ProfileGenerator.generate_base36(int(time.time()*1000))}"
        attr_sess_id = f"attr_{ProfileGenerator.generate_base36(int(time.time()*1000))}"
        entry_id = ProfileGenerator.generate_entry_id()

        # 1. Post-view Member Payout Log
        try:
            post_view_payload = {
                "source_event_id": f"member:{self.profile.device_id[:8]}:{novel_id}:{chapter_num}",
                "callback_contract": "telemetry_v2",
                "attribution": {
                    "novel_id": novel_id,
                    "chapter_id": chapter_id,
                    "chapter_num": chapter_num,
                    "active_reading_seconds": int(reading_time_sec),
                    "completed": completed,
                }
            }
            await client.post("/api/reading/v2/logs/post-view", json=post_view_payload)
        except Exception:
            pass

        # 2. Heartbeat Progress
        try:
            heartbeat_payload = {
                "novel_id": novel_id,
                "chapter_id": chapter_id,
                "reading_session_id": logical_sess_id,
                "elapsed_seconds": int(reading_time_sec),
                "depth_percent": depth_percent,
            }
            await client.post("/api/reading/sessions/heartbeat", json=heartbeat_payload)
        except Exception:
            pass

        # 3. Analytics Event (chapter_completed & reading_time_sec)
        try:
            now_iso = datetime.utcnow().isoformat() + "Z"
            meta = COUNTRY_METADATA.get(self.profile.country, COUNTRY_METADATA["ID"])
            events = [
                {
                    "event_type": "reading_time_sec",
                    "event_time": now_iso,
                    "device_id": self.profile.device_id,
                    "session_id": self.session_id,
                    "event_properties": {
                        "novel_id": novel_id,
                        "chapter_id": chapter_id,
                        "chapter_num": chapter_num,
                        "reading_time_sec": int(reading_time_sec),
                        "completed": completed,
                        "scroll_pct": depth_percent,
                        "logical_reading_session_id": logical_sess_id,
                        "market": meta["service_country"],
                        "platform": PLATFORM,
                    },
                },
                {
                    "event_type": "chapter_completed" if completed else "chapter_progressed",
                    "event_time": now_iso,
                    "device_id": self.profile.device_id,
                    "session_id": self.session_id,
                    "event_properties": {
                        "novel_id": novel_id,
                        "chapter_num": chapter_num,
                        "reading_time_sec": int(reading_time_sec),
                        "logical_reading_session_id": logical_sess_id,
                        "attribution_session_id": attr_sess_id,
                        "market": meta["service_country"],
                        "platform": PLATFORM,
                    },
                }
            ]
            await client.post("/api/v1/analytics/events", json={"events": events})
        except Exception:
            pass

    # =========================================================================
    # SIKLUS HIDUP SESI TAMU RESMI (GUEST READING SESSION)
    # =========================================================================

    async def init_guest_session(self) -> Tuple[bool, str]:
        """
        Inisialisasi sesi tamu resmi dengan alur Cold Start Android:
        1. Cek app-version & flags aplikasi layaknya user baru instal.
        2. Kirim POST /api/guest-reading/session.
        3. Simpan guest_id & guest_token ke client headers dan cookies.
        Dilengkapi multi-proxy auto-retry jika proxy pertama lambat / timeout.
        """
        for attempt in range(1, 4):
            client = await self.get_client()
            try:
                # 1. Cold Start: app-version & flags
                await client.get("/api/auth/app-version")
                await asyncio.sleep(0.3)
                await client.get("/api/auth/flags")
                await asyncio.sleep(0.3)
                await client.get(f"/api/auth/public-flags?anonymous_id={self.profile.anonymous_id}")
                await asyncio.sleep(0.4)

                # 2. Inisiasi Sesi Tamu Resmi
                resp = await client.post("/api/guest-reading/session", content=b"")
                if resp.status_code == 200:
                    data = resp.json()
                    self.guest_id = data.get("guest_id")
                    self.guest_token = data.get("guest_token") or resp.cookies.get("qf_guest_reader")
                    if self.guest_token:
                        client.headers["x-guest-token"] = self.guest_token
                        client.cookies.set("qf_guest_reader", self.guest_token, domain="api.quarterfull.io", path="/")
                        return True, "Sesi Tamu Berhasil Diinisiasi"
                elif resp.status_code in (403, 429, 502, 503):
                    await self.rotate_proxy_if_needed(f"HTTP {resp.status_code}")
                    await asyncio.sleep(1.0)
                    continue
                else:
                    return False, f"Server menolak sesi tamu: HTTP {resp.status_code}"
            except Exception as exc:
                err_msg = str(exc) or exc.__class__.__name__
                await self.rotate_proxy_if_needed(err_msg)
                if attempt < 3:
                    await asyncio.sleep(1.0)
                    continue
                return False, f"Error inisiasi sesi tamu ({err_msg})"
        return False, "Gagal inisiasi tamu setelah 3 percobaan proxy"

    async def send_guest_progress(
        self,
        novel_id: str,
        chapter_id: str,
        active_reading_seconds: float,
        scroll_percent: float = 1.0,
        completed: bool = True,
    ):
        """
        Mengirimkan heartbeat progres membaca tamu resmi:
        1. PUT /api/guest-reading/progress dengan active_reading_seconds & scroll_percent.
        2. Mengirim header x-guest-event-id yang sah.
        """
        client = await self.get_client()
        now_ms = int(time.time() * 1000)
        rnd = ProfileGenerator.generate_base36(now_ms)
        guest_event_id = f"guest-open:{chapter_id}:{now_ms}:{rnd}"

        headers = {
            "x-guest-event-id": guest_event_id,
        }
        if self.guest_token:
            headers["x-guest-token"] = self.guest_token

        payload = {
            "active_reading_seconds": int(active_reading_seconds),
            "scroll_percent": scroll_percent,
            "completed": completed,
        }
        try:
            await client.put("/api/guest-reading/progress", json=payload, headers=headers)
        except Exception:
            pass

    # =========================================================================
    # INTERAKSI SOSIAL ORGANIK (LIKE, BOOKMARK / RAK BUKU, FOLLOW PENULIS)
    # =========================================================================

    async def like_novel(self, novel_id: str) -> bool:
        """Menyukai (Like) novel target dengan otentikasi akun member."""
        if not self.access_token:
            return False
        try:
            client = await self.get_client()
            resp = await client.post(f"/api/v1/novels/{novel_id}/like")
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("is_liked", True):
                    await asyncio.sleep(0.4)
                    await client.post(f"/api/v1/novels/{novel_id}/like")
                return True
        except Exception:
            pass
        return False

    async def bookmark_novel(self, novel_id: str) -> bool:
        """Menyimpan novel ke rak buku / bookmark / pustaka akun member."""
        if not self.access_token:
            return False
        try:
            client = await self.get_client()
            resp = await client.post(f"/api/v1/novels/{novel_id}/bookmark")
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("is_saved", True):
                    await asyncio.sleep(0.4)
                    await client.post(f"/api/v1/novels/{novel_id}/bookmark")
                return True
        except Exception:
            pass
        return False

    async def follow_author(self, author_id: str) -> bool:
        """Mengikuti (Follow) akun profil penulis novel."""
        if not self.access_token or not author_id:
            return False
        try:
            client = await self.get_client()
            resp = await client.put(f"/api/v1/social/profiles/{author_id}/follow")
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("is_following", True):
                    await asyncio.sleep(0.4)
                    await client.put(f"/api/v1/social/profiles/{author_id}/follow")
                return True
            return resp.status_code in (200, 204)
        except Exception:
            pass
        return False

    async def maybe_engage_socially(
        self,
        novel_id: str,
        author_id: Optional[str] = None,
        log_func=None,
    ) -> Dict[str, bool]:
        """
        Simulasi pembaca organik yang menyukai cerita ini.
        ATURAN MUTLAK: Setiap akun cuma boleh Like, Follow, dan Simpen Buku TEPAT SATU KALI.
        Jika sudah pernah like / follow / simpen novel/author ini sebelumnya, akan di-skip otomatis.
        """
        if not self.access_token:
            return {"liked": False, "bookmarked": False, "followed": False}

        liked_novels = self.account_data.setdefault("liked_novels", []) if self.account_data else []
        bookmarked_novels = self.account_data.setdefault("bookmarked_novels", []) if self.account_data else []
        followed_authors = self.account_data.setdefault("followed_authors", []) if self.account_data else []

        results = {"liked": False, "bookmarked": False, "followed": False}
        actions_taken = []
        updated = False

        # Peluang pembaca menyukai novel ini (~70% pembaca yang menikmati bab)
        if random.random() < 0.70:
            want_all = random.random() < 0.25

            # Hanya izinkan jika BELUM PERNAH dilakukan sebelumnya
            can_bookmark = (novel_id not in bookmarked_novels)
            can_like = (novel_id not in liked_novels)
            can_follow = (author_id is not None) and (author_id not in followed_authors)

            want_bookmark = can_bookmark and (want_all or (random.random() < 0.60))
            want_like = can_like and (want_all or (random.random() < 0.55))
            want_follow = can_follow and (want_all or (random.random() < 0.40))

            # 1. Simpan ke Rak Buku (Bookmark / Simpan Cerita) - HANYA 1X
            if want_bookmark:
                await asyncio.sleep(random.uniform(1.2, 2.5))
                ok = await self.bookmark_novel(novel_id)
                if ok:
                    results["bookmarked"] = True
                    actions_taken.append("Simpan Cerita")
                    bookmarked_novels.append(novel_id)
                    updated = True

            # 2. Suka / Like Novel - HANYA 1X
            if want_like:
                await asyncio.sleep(random.uniform(1.2, 2.5))
                ok = await self.like_novel(novel_id)
                if ok:
                    results["liked"] = True
                    actions_taken.append("Like")
                    liked_novels.append(novel_id)
                    updated = True

            # 3. Follow Penulis - HANYA 1X
            if want_follow and author_id:
                await asyncio.sleep(random.uniform(1.2, 2.5))
                ok = await self.follow_author(author_id)
                if ok:
                    results["followed"] = True
                    actions_taken.append("Follow Penulis")
                    followed_authors.append(author_id)
                    updated = True

            if updated and self.save_account_cb and self.account_data:
                self.save_account_cb(self.account_data)

            if actions_taken and log_func:
                if len(actions_taken) == 3:
                    log_func(f"[bold magenta]♥ Reader SANGAT SUKA novel ini (Pertama kali & Ketiganya!):[/] [bold yellow]{' + '.join(actions_taken)}[/]")
                else:
                    log_func(f"[bold magenta]♥ Reader menyukai novel ini:[/] [bold yellow]{' + '.join(actions_taken)}[/]")

        return results

    # =========================================================================
    # FITUR REMAKE CERITA (READER REMIXES & CABANG CERITA)
    # =========================================================================

    async def check_remix_eligibility(self, novel_id: str) -> bool:
        """Memeriksa apakah novel target memenuhi kualifikasi untuk fitur Remix/Remake Cerita."""
        try:
            client = await self.get_client()
            resp = await client.get(f"/api/v1/studio-cursor/reader-remixes/eligibility/{novel_id}")
            if resp.status_code == 200:
                data = resp.json()
                return bool(data.get("eligible", False))
        except Exception:
            pass
        return False

    async def get_remix_roots(self, novel_id: str) -> List[Dict[str, Any]]:
        """Mengambil daftar akar seri (series roots) remix yang tersedia untuk novel."""
        try:
            client = await self.get_client()
            resp = await client.get(f"/api/v1/studio-cursor/reader-remixes/series/novel/{novel_id}/roots?limit=8")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("items", []) if isinstance(data, dict) else []
        except Exception:
            pass
        return []

    async def get_remix_episode(self, series_id: str, episode_num: int = 1) -> Optional[Dict[str, Any]]:
        """Mengambil data episode cerita hasil remix pembaca lain."""
        try:
            client = await self.get_client()
            resp = await client.get(f"/api/v1/studio-cursor/reader-remixes/series/{series_id}/episodes/{episode_num}")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    async def create_reader_remix(
        self,
        novel_id: str,
        chapter_id: str,
        mode: Optional[str] = None,
        log_func=None,
    ) -> Optional[Dict[str, Any]]:
        """
        Mengeksekusi pembuatan Remake Cerita (Reader Remix) oleh pembaca login / tamu mendaftar:
        1. Cek eligibilitas novel
        2. Ambil roots seri remix aktif
        3. Buat cabang seri (branch intervention)
        4. Generate prompt intervensi & kirim streaming turn
        5. Kirim choice event & PUT config
        """
        if not self.access_token:
            return None

        # 1. Cek eligibilitas
        eligible = await self.check_remix_eligibility(novel_id)
        if not eligible:
            return None

        # 2. Ambil roots series
        roots = await self.get_remix_roots(novel_id)
        if not roots:
            return None

        series_item = random.choice(roots)
        series_id = series_item.get("series_id")
        if not series_id:
            return None

        # Dapatkan source_session_id dari episode 1
        ep_data = await self.get_remix_episode(series_id, 1)
        source_session_id = ep_data.get("episode", {}).get("session_id") if ep_data else None

        # 3. Branch intervention
        client = await self.get_client()
        branch_payload = {
            "source_session_id": source_session_id,
            "chapter_hash_id": chapter_id,
        }
        try:
            b_resp = await client.post(
                f"/api/v1/studio-cursor/reader-remixes/series/{series_id}/branch-intervention",
                json=branch_payload,
            )
            if b_resp.status_code != 200:
                return None
            remix_session_id = b_resp.json().get("session", {}).get("id")
        except Exception:
            return None

        if not remix_session_id:
            return None

        # 4. Generate payload & stream message
        from .remake import RemakeModeGenerator
        remix_data = RemakeModeGenerator.generate(mode=mode)
        selected_mode = remix_data["mode"]
        stream_message = remix_data["stream_message"]

        # Hit & run stream turn
        try:
            await client.post(
                f"/api/v1/studio-cursor/reader-remixes/{remix_session_id}/turns/stream",
                json={
                    "message": stream_message,
                    "locale": "id",
                    "message_visibility": "visible",
                },
                timeout=10.0,
            )
        except Exception:
            pass

        # PUT config update jika ada
        if remix_data.get("put_endpoint") and remix_data.get("put_payload"):
            try:
                await client.put(
                    f"/api/v1/studio-cursor/reader-remixes/{remix_session_id}/{remix_data['put_endpoint']}",
                    json=remix_data["put_payload"],
                )
            except Exception:
                pass

        # Choice events (selection & exposure)
        try:
            await client.post(
                f"/api/v1/studio-cursor/reader-remixes/{remix_session_id}/choice-event",
                json={"event_type": "selection", "selected_mode": selected_mode},
            )
            await client.post(
                f"/api/v1/studio-cursor/reader-remixes/{remix_session_id}/choice-event",
                json={"event_type": "exposure"},
            )
        except Exception:
            pass

        if log_func:
            log_func(
                f"[bold magenta]⚡ Pembaca meremake cerita ini:[/] [bold yellow]{remix_data['title']}[/] "
                f"([dim]{remix_data['summary']}[/])"
            )

        return {
            "session_id": remix_session_id,
            "mode": selected_mode,
            "title": remix_data["title"],
            "summary": remix_data["summary"],
        }
