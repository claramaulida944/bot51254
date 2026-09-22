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
from typing import Any, Dict, List, Optional, Tuple

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
        profile: AccountProfile,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        proxy_manager: Optional[StealthProxyManager] = None,
        current_proxy: Optional[str] = None,
    ):
        self.profile = profile
        self.access_token = access_token or ""
        self.refresh_token = refresh_token or ""
        self.proxy_manager = proxy_manager or default_proxy_manager
        self.current_proxy = current_proxy
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

        return headers

    async def get_client(self) -> httpx.AsyncClient:
        """Membuat atau mengambil instance httpx.AsyncClient dengan proxy aktif."""
        if self._client is None or self._client.is_closed:
            if not self.current_proxy and self.proxy_manager.has_proxies:
                self.current_proxy = self.proxy_manager.pop_proxy(self.profile.country)

            client_kwargs: Dict[str, Any] = {
                "base_url": BASE_URL,
                "timeout": httpx.Timeout(12.0, connect=5.0),
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

    async def perform_organic_signup(self) -> Tuple[bool, str]:
        """
        Menjalankan alur pendaftaran akun lengkap:
        1. Pre-check email availability.
        2. Kirim POST /api/auth/signup.
        3. Sinkronisasi atribusi AppsFlyer (POST /api/auth/signup-attribution).
        4. Hydration state awal akun (/api/q/account & categories).
        """
        # 1. Pre-check
        await self.check_email_availability()
        await asyncio.sleep(random.uniform(0.6, 1.4))

        # 2. Signup
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

        client = await self.get_client()
        try:
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
                await self.rotate_proxy_if_needed(f"HTTP {resp.status_code}")
                return False, f"Rate limit / Cloudflare (HTTP {resp.status_code})"
            else:
                return False, f"Server menolak: HTTP {resp.status_code} ({resp.text[:80]})"
        except Exception as exc:
            await self.rotate_proxy_if_needed(str(exc))
            return False, f"Koneksi gagal: {exc}"

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
            await asyncio.sleep(0.3)
            await client.post("/api/auth/reader-onboarding/eligibility")
        except Exception:
            pass

    # =========================================================================
    # PEMBACAAN NOVEL & TELEMETRI AKTIF
    # =========================================================================

    async def get_novel_chapters(self, novel_id: str) -> List[Dict[str, Any]]:
        """Mengambil daftar bab novel."""
        client = await self.get_client()
        resp = await client.get(f"/api/v1/novels/{novel_id}/chapters?order=asc")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("chapters", []) or data.get("items", []) or []
        return []

    async def get_chapter_detail(self, novel_id: str, chapter_id: str) -> Optional[Dict[str, Any]]:
        """Mengambil narasi isi teks bab."""
        client = await self.get_client()
        resp = await client.get(f"/api/v1/novels/{novel_id}/chapters/{chapter_id}?rewarded_reader=true")
        if resp.status_code == 200:
            return resp.json()
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
