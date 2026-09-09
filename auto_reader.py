"""
Modul Auto Readers (Simulasi Pembaca Otomatis) Toodat / Quarterfull.

Modul ini menyediakan simulasi membaca novel digital bab per bab secara natural
untuk dua jenis entitas sekaligus:
1. Pengguna Terdaftar (Member): Menggunakan token autentikasi dari `akun.txt`
   dan mengirimkan event telemetri royalti view `POST /api/reading/v2/logs/post-view`.
2. Pembaca Tamu (Guest Reader): Menginisiasi sesi tamu resmi via
   `POST /api/guest-reading/session` dan mengirimkan heartbeat progres membaca
   `PUT /api/guest-reading/progress`.

Fitur Tambahan:
- Antarmuka Terminal Interaktif Modern dengan library `rich`.
- Concurrency control via `asyncio.Semaphore`.
- Dukungan rotasi proxy dari `proxies.txt` (opsional).
- Filter bab gratis (non-premium & terbit).
- Pelacakan progress real-time per worker.
"""

import asyncio
import json
import logging
import random
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

# Pastikan encoding stdout/stderr di Windows mendukung UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Column, Table
from rich.text import Text

from auto_signup import UserAgentGenerator
from session_manager import IdentifierGenerator
from proxy_manager import (
    ProxyManager,
    ProxyInfo,
    default_proxy_manager,
    SUPPORTED_QUARTERFULL_COUNTRIES,
)

# Konfigurasi logger dasar (level ERROR agar tidak merusak tata letak Rich Progress di konsol)
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("AutoReader")
logger.setLevel(logging.ERROR)
for _lib in ("httpx", "httpcore"):
    logging.getLogger(_lib).setLevel(logging.ERROR)
console = Console(highlight=False)


class NovelTargetResolver:
    """Menguraikan URL atau ID Novel dan mengambil daftar bab yang valid untuk dibaca."""

    BASE_URL: str = "https://api.quarterfull.io"
    NOVEL_ID_REGEX = re.compile(r"([a-zA-Z0-9]{16})")

    @classmethod
    def extract_novel_id(cls, raw_input: str) -> Optional[str]:
        """Mengekstrak 16-karakter hash ID dari string teks atau URL novel."""
        if not raw_input:
            return None
        query_hash = re.search(r"[?&]hashId=([a-zA-Z0-9]{16})", raw_input)
        if query_hash:
            return query_hash.group(1)
        match = cls.NOVEL_ID_REGEX.search(raw_input.strip())
        return match.group(1) if match else None

    @classmethod
    async def fetch_novel_details(cls, novel_id: str, proxy: Optional[str] = None) -> Dict[str, Any]:
        """Mengambil metadata novel (judul, deskripsi, penulis) dengan dukungan origin detection."""
        url = f"{cls.BASE_URL}/api/v1/novels/{novel_id}"
        client_kwargs: Dict[str, Any] = {"headers": {"user-agent": "okhttp/4.12.0"}, "timeout": 20.0}
        if proxy:
            client_kwargs["proxy"] = proxy
            client_kwargs["http2"] = False
        else:
            client_kwargs["http2"] = True

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        # Deteksi jika novel aslinya berasal dari luar negeri (misal EN) atau berstatus terjemahan (is_translated=True),
        # di mana server Quarterfull saat diakses dari IP lokal Indonesia hanya menyajikan edisi terjemahan
        # dengan bab terbatas (misal cuma 6 bab vs 10 bab aslinya).
        origin = str(data.get("origin_country", "ID")).upper()
        if (origin == "EN" or data.get("is_translated")) and not proxy and default_proxy_manager and default_proxy_manager.has_proxies:
            en_countries = ["GB", "AU", "CA", "US", "NZ", "IE"]
            target_cc = random.choice(en_countries) if origin == "EN" else origin
            origin_proxy = default_proxy_manager.get_proxy(country_code=target_cc)
            if origin_proxy:
                try:
                    async with httpx.AsyncClient(proxy=origin_proxy, http2=False, timeout=20.0, headers={"user-agent": "okhttp/4.12.0"}) as p_client:
                        p_resp = await p_client.get(url)
                        if p_resp.status_code == 200:
                            p_data = p_resp.json()
                            orig_count = p_data.get("stats", {}).get("chapter_count", 0)
                            curr_count = data.get("stats", {}).get("chapter_count", 0)
                            if orig_count >= curr_count:
                                data = p_data
                except Exception as e:
                    logger.debug("Gagal fetch novel details via origin proxy: %s", e)

        return data

    @classmethod
    async def fetch_readable_chapters(
        cls,
        novel_id: str,
        origin_country: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Mengambil daftar bab novel secara dinamis dengan dukungan paginasi kursor (cursor),
        memfilter hanya bab yang sudah terbit dan non-premium (gratis), lalu mengurutkannya.
        Mendukung rotasi proxy negara asal (acak GB, AU, CA, US, NZ, IE untuk novel EN) agar semua bab asli terdeteksi.
        """
        target_proxy = proxy
        if not target_proxy and default_proxy_manager and default_proxy_manager.has_proxies:
            if origin_country and origin_country.upper() == "EN":
                en_countries = ["GB", "AU", "CA", "US", "NZ", "IE"]
                target_proxy = default_proxy_manager.get_proxy(country_code=random.choice(en_countries))
            elif origin_country and origin_country.upper() != "ID":
                target_proxy = default_proxy_manager.get_proxy(country_code=origin_country)

        client_kwargs: Dict[str, Any] = {"headers": {"user-agent": "okhttp/4.12.0"}, "timeout": 25.0}
        if target_proxy:
            client_kwargs["proxy"] = target_proxy
            client_kwargs["http2"] = False
        else:
            client_kwargs["http2"] = True

        async def _query_chapters(kwargs_map: Dict[str, Any]) -> List[Dict[str, Any]]:
            readable_list = []
            cur = None
            async with httpx.AsyncClient(**kwargs_map) as client:
                while True:
                    url = f"{cls.BASE_URL}/api/v1/novels/{novel_id}/chapters?order=asc&include_read_progress=false"
                    if cur:
                        url += f"&cursor={cur}"
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()

                    items = data.get("items", [])
                    for ch in items:
                        if ch.get("is_published", True) and not ch.get("is_premium", False):
                            readable_list.append(ch)

                    cur = data.get("next_cursor")
                    if not cur:
                        break
            readable_list.sort(key=lambda x: x.get("chapter_num", 0))
            return readable_list

        try:
            return await _query_chapters(client_kwargs)
        except Exception as exc:
            # Jika menggunakan proxy dan gagal (misal 407 Account is suspended / timeout / connection error),
            # lakukan fallback otomatis ke direct connection (tanpa proxy)
            if target_proxy:
                logger.warning("Fetch bab via proxy gagal (%s). Otomatis beralih ke Direct Connection...", exc)
                direct_kwargs: Dict[str, Any] = {"headers": {"user-agent": "okhttp/4.12.0"}, "timeout": 25.0, "http2": True}
                return await _query_chapters(direct_kwargs)
            raise


def is_proxy_error(exc: Exception) -> bool:
    """Mendeteksi apakah exception merupakan kegagalan proxy/koneksi jaringan tunnel."""
    err_str = str(exc).lower()
    return (
        isinstance(exc, (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, ValueError))
        or "407" in err_str
        or "proxy" in err_str
        or "socks" in err_str
        or "socksio" in err_str
        or "scheme" in err_str
        or "tunnel" in err_str
        or "connection reset" in err_str
        or "remote end closed" in err_str
        or "bad gateway" in err_str
        or "502" in err_str
        or "503" in err_str
        or "504" in err_str
    )


class BaseReaderSession:
    """Basis client HTTP/2 untuk sesi pembaca (Member maupun Guest)."""

    BASE_URL: str = "https://api.quarterfull.io"

    def __init__(
        self,
        worker_id: str,
        device_id: Optional[str] = None,
        user_agent: Optional[str] = None,
        country: str = "ID",
        timezone_str: Optional[str] = None,
        proxy: Optional[str] = None,
        proxy_manager: Optional[ProxyManager] = None,
        timeout: float = 30.0,
    ) -> None:
        self.worker_id: str = worker_id
        self.device_id: str = device_id or IdentifierGenerator.generate_device_id()
        self.user_agent: str = user_agent or UserAgentGenerator.get_random_okhttp_ua()

        # Validasi negara resmi Quarterfull (hanya yang terverifikasi dan didukung API)
        c_upper = str(country).upper().strip()
        if c_upper not in SUPPORTED_QUARTERFULL_COUNTRIES:
            c_upper = random.choice(list(SUPPORTED_QUARTERFULL_COUNTRIES.keys()))
        self.country = c_upper
        cfg = SUPPORTED_QUARTERFULL_COUNTRIES[self.country]

        self.timezone: str = timezone_str or cfg["timezone"]
        self.lang: str = cfg["lang"]
        self.proxy: Optional[str] = proxy
        self.proxy_manager: ProxyManager = proxy_manager or default_proxy_manager
        self.timeout: float = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def rotate_bad_proxy(self) -> Optional[str]:
        """Menghapus proxy bermasalah dari daftar aktif dan beralih ke proxy pengganti."""
        if self.proxy and self.proxy_manager:
            logger.info("[%s] Mengeliminasi proxy bermasalah: %s", self.worker_id, self.proxy[:45])
            self.proxy_manager.remove_bad_proxy(self.proxy)
            self.proxy = self.proxy_manager.get_proxy(country_code=self.country)
        if self._client and not self._client.is_closed:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = None
        return self.proxy

    def _get_current_local_date(self) -> str:
        """Mengembalikan format YYYY-MM-DD sesuai zona waktu yang dikonfigurasi."""
        try:
            if ZoneInfo is not None:
                now = datetime.now(ZoneInfo(self.timezone))
            else:
                now = datetime.now()
            return now.strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def build_base_headers(self) -> Dict[str, str]:
        """Menyusun fingerprint header HTTP standar aplikasi Toodat Android dengan identitas negara resmi."""
        return {
            "host": "api.quarterfull.io",
            "user-agent": self.user_agent,
            "accept-encoding": "gzip",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "x-timezone": self.timezone,
            "x-local-date": self._get_current_local_date(),
            "accept-language": self.lang,
            "x-user-country": self.country,
            "x-user-raw-country": self.country,
            "x-device-id": self.device_id,
            "accept": "application/json",
        }

    async def get_client(self) -> httpx.AsyncClient:
        """Menginisialisasi httpx.AsyncClient dengan dukungan HTTP/2 (atau HTTP/1.1 jika via proxy)."""
        for _ in range(5):
            if self._client is None or self._client.is_closed:
                # Sanitasi proxy: jika socks4 atau format rusak, eliminasi dan rotasi
                if self.proxy and (self.proxy.startswith("socks4://") or "://" not in self.proxy):
                    await self.rotate_bad_proxy()
                    continue

                kwargs: Dict[str, Any] = {
                    "base_url": self.BASE_URL,
                    "http2": False if self.proxy else True,
                    "headers": self.build_base_headers(),
                    "timeout": httpx.Timeout(self.timeout),
                }
                if self.proxy:
                    kwargs["proxy"] = self.proxy

                try:
                    self._client = httpx.AsyncClient(**kwargs)
                    return self._client
                except Exception as exc:
                    logger.warning(
                        "[%s] Gagal membuat HTTP client dengan proxy %s (%s). Mengeliminasi & rotasi...",
                        self.worker_id, self.proxy, exc
                    )
                    await self.rotate_bad_proxy()
                    continue
            else:
                return self._client

        # Fallback aman jika 5 proxy berturut-turut gagal inisialisasi
        direct_kwargs: Dict[str, Any] = {
            "base_url": self.BASE_URL,
            "http2": True,
            "headers": self.build_base_headers(),
            "timeout": httpx.Timeout(self.timeout),
        }
        self._client = httpx.AsyncClient(**direct_kwargs)
        return self._client

    def detach_client(self) -> Optional[httpx.AsyncClient]:
        """Melepas kepemilikan HTTP client agar dapat dilanjutkan oleh background task tanpa tertutup."""
        client = self._client
        self._client = None
        return client

    async def close(self) -> None:
        """Menutup koneksi client HTTP jika belum dilepas."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


class GuestReaderSession(BaseReaderSession):
    """Sesi pembaca Tamu (Guest Reader) dengan inisialisasi sesi resmi dan heartbeat baca."""

    def __init__(
        self,
        worker_id: str,
        country: str = "ID",
        proxy: Optional[str] = None,
        proxy_manager: Optional[ProxyManager] = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            device_id=IdentifierGenerator.generate_device_id(),
            user_agent=UserAgentGenerator.get_random_okhttp_ua(),
            country=country,
            proxy=proxy,
            proxy_manager=proxy_manager,
            timeout=timeout,
        )
        self.guest_id: Optional[str] = None
        self.guest_token: Optional[str] = None

    async def init_guest_session(self, max_retries: int = 3) -> bool:
        """Memanggil POST /api/guest-reading/session dengan auto-retry proxy jika proxy gagal."""
        for attempt in range(max_retries):
            client = await self.get_client()
            try:
                resp = await client.post("/api/guest-reading/session", content=b"")
                resp.raise_for_status()
                data = resp.json()
                self.guest_id = data.get("guest_id")
                self.guest_token = data.get("guest_token")

                if self.guest_token:
                    client.headers["x-guest-token"] = self.guest_token
                    client.cookies.set("qf_guest_reader", self.guest_token, domain="api.quarterfull.io", path="/")
                    return True
            except Exception as exc:
                if is_proxy_error(exc) and self.proxy:
                    logger.warning(
                        "[%s] Proxy gagal saat inisialisasi sesi (%s), eliminasi & ganti proxy (coba %d/%d)...",
                        self.worker_id, exc, attempt + 1, max_retries
                    )
                    await self.rotate_bad_proxy()
                    continue
                if attempt == max_retries - 1:
                    logger.error("[%s] Gagal inisialisasi guest session: %s", self.worker_id, exc)
                await asyncio.sleep(0.5)
        return False

    async def read_chapter(
        self,
        novel_id: str,
        chapter: Dict[str, Any],
        reading_delay_sec: float,
    ) -> Tuple[bool, str]:
        """
        Membaca satu bab novel sebagai tamu secara dinamis:
        1. GET /api/v1/novels/{novel_id}/chapters/{chapter_id}?rewarded_reader=true
        2. Kirim progress scrolling bertahap tiap ~3 detik
        3. PUT /api/guest-reading/progress
        """
        client = await self.get_client()
        ch_id = chapter.get("hash_id", "")
        ch_num = chapter.get("chapter_num", 1)

        # 1. Fetch teks bab secara dinamis dengan auto-fallback negara
        event_id = IdentifierGenerator.generate_guest_event_id(ch_id)
        get_headers = {"x-guest-event-id": event_id}

        char_count = 0
        ch_url = f"/api/v1/novels/{novel_id}/chapters/{ch_id}?rewarded_reader=true"
        try:
            get_resp = await client.get(ch_url, headers=get_headers)
            # Smart fallback jika terjadi 404 (misal akibat novel asing EN vs ID)
            if get_resp.status_code == 404:
                alt_headers = dict(get_headers)
                alt_headers["x-user-country"] = "US" if self.country != "US" else "ID"
                alt_headers["x-user-raw-country"] = alt_headers["x-user-country"]
                get_resp = await client.get(ch_url, headers=alt_headers)

            get_resp.raise_for_status()
            ch_data = get_resp.json()
            content = ch_data.get("content", "")
            char_count = len(content)
        except Exception as exc:
            if is_proxy_error(exc) and self.proxy:
                logger.warning("[%s] Proxy error (%s), mengeliminasi dan mencoba proxy baru...", self.worker_id, exc)
                await self.rotate_bad_proxy()
                return await self.read_chapter(novel_id, chapter, reading_delay_sec)
            return False, f"GET Chapter Gagal: {exc}"

        # 2. Simulasi jeda baca natural & heartbeat berkala (tiap ~3 detik)
        read_delay = max(4.0, reading_delay_sec)
        step_interval = random.uniform(2.5, 3.5)
        num_steps = max(3, int(read_delay / step_interval))
        step_time = read_delay / num_steps

        grs_session_id = IdentifierGenerator.generate_session_id("grs")
        attr_session_id = IdentifierGenerator.generate_session_id("attr", random_len=8)

        # Hitung target dwell manusiawi (3 hingga 7 menit per bab / 180s - 420s)
        target_dwell = random.uniform(180.0, 420.0)
        chapter["char_count"] = char_count

        for step in range(1, num_steps + 1):
            await asyncio.sleep(step_time)
            is_last = (step == num_steps)
            current_progress = 0.98 if is_last else min(0.95, round(step / num_steps, 2))
            # Skalakan active reading seconds secara proporsional menuju 3-7 menit
            step_active_sec = (step / num_steps) * target_dwell

            progress_payload = {
                "chapter_hash_id": ch_id,
                "progress": current_progress,
                "active_reading_seconds": round(step_active_sec, 2),
                "completed": is_last,
                "reading_session_id": grs_session_id,
                "session_active_reading_seconds": round(step_active_sec, 2),
                "session_ended": is_last,
                "session_end_reason": "chapter_change" if is_last else None,
                "platform": "android",
                "entry_source": "chapter_route",
                "attribution_session_id": attr_session_id,
                "chapter_number": ch_num,
                "content_type": "novel",
                "content_character_count": char_count,
                "read_mode": "scroll",
            }
            try:
                put_resp = await client.put("/api/guest-reading/progress", json=progress_payload)
                if is_last:
                    put_resp.raise_for_status()
            except Exception as exc:
                if is_last:
                    return False, f"PUT Progress Gagal: {exc}"

        return True, "200 OK (Heartbeat Berkala Selesai)"


class MemberReaderSession(BaseReaderSession):
    """Sesi pembaca Member (Registered User) dengan Bearer Token dan Post-View Royalti."""

    def __init__(
        self,
        worker_id: str,
        account_data: Dict[str, Any],
        novel_title: str = "Novel",
        proxy: Optional[str] = None,
        proxy_manager: Optional[ProxyManager] = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            device_id=account_data.get("device_id") or IdentifierGenerator.generate_device_id(),
            user_agent=account_data.get("user_agent") or UserAgentGenerator.get_random_okhttp_ua(),
            country=account_data.get("country", "ID"),
            timezone_str=None,
            proxy=proxy,
            proxy_manager=proxy_manager,
            timeout=timeout,
        )
        self.account: Dict[str, Any] = account_data
        self.access_token: str = account_data.get("access_token", "")
        self.user_id: Any = account_data.get("user_id", 0)
        self.email: str = account_data.get("email", "unknown@user.com")
        self.novel_title: str = novel_title

    def build_base_headers(self) -> Dict[str, str]:
        headers = super().build_base_headers()
        headers["authorization"] = f"Bearer {self.access_token}"
        headers["content-type"] = "application/json"
        return headers

    def _save_refreshed_account(self, file_path: str = "akun.txt") -> None:
        """Menyimpan pembaruan access_token & refresh_token ke berkas akun.txt."""
        try:
            if not os.path.exists(file_path):
                return
            lines = []
            updated = False
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        data = json.loads(line_str)
                        if data.get("email") == self.email:
                            data["access_token"] = self.access_token
                            if self.account.get("refresh_token"):
                                data["refresh_token"] = self.account["refresh_token"]
                            lines.append(json.dumps(data, ensure_ascii=False))
                            updated = True
                            continue
                    except Exception:
                        pass
                    lines.append(line_str)
            if updated:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
        except Exception as exc:
            logger.debug("[%s] Gagal menyimpan token baru ke %s: %s", self.worker_id, file_path, exc)

    async def refresh_access_token(self) -> bool:
        """Memperbarui access_token member via POST /api/auth/token/refresh."""
        refresh_token = self.account.get("refresh_token")
        if not refresh_token:
            return False

        refresh_headers = {
            "user-agent": self.user_agent,
            "x-device-id": self.device_id,
            "content-type": "application/json",
            "accept": "application/json",
        }

        # Coba via proxy terlebih dahulu; jika proxy mati/timeout, fallback ke Direct Connection
        candidates = [self.proxy, None] if self.proxy else [None]
        for p in candidates:
            try:
                kwargs: Dict[str, Any] = {
                    "base_url": self.BASE_URL,
                    "timeout": httpx.Timeout(10.0),
                    "headers": refresh_headers,
                    "http2": False if p else True,
                }
                if p:
                    kwargs["proxy"] = p

                async with httpx.AsyncClient(**kwargs) as refresh_client:
                    resp = await refresh_client.post(
                        "/api/auth/token/refresh",
                        json={"refresh_token": refresh_token},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        new_access = data.get("access_token")
                        new_refresh = data.get("refresh_token")
                        if new_access:
                            self.access_token = new_access
                            self.account["access_token"] = new_access
                            if new_refresh:
                                self.account["refresh_token"] = new_refresh
                            if self._client and not self._client.is_closed:
                                self._client.headers["authorization"] = f"Bearer {new_access}"
                            self._save_refreshed_account()
                            logger.info("[%s] Token member berhasil diperbarui otomatis!", self.worker_id)
                            return True
                    elif resp.status_code == 401:
                        # Refresh token expired, harus login ulang penuh
                        return False
            except Exception as exc:
                if p and is_proxy_error(exc):
                    await self.rotate_bad_proxy()
                continue

        return False

    async def login_with_password(self) -> bool:
        """Melakukan login ulang penuh ke POST /api/auth/login menggunakan email & password."""
        password = self.account.get("password")
        if not self.email or not password:
            return False

        login_headers = {
            "user-agent": self.user_agent,
            "x-device-id": self.device_id,
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "content-type": "application/json",
            "accept": "application/json",
        }

        # Coba via proxy terlebih dahulu; jika proxy mati/timeout, fallback ke Direct Connection
        candidates = [self.proxy, None] if self.proxy else [None]
        for p in candidates:
            try:
                kwargs: Dict[str, Any] = {
                    "base_url": self.BASE_URL,
                    "timeout": httpx.Timeout(12.0),
                    "headers": login_headers,
                    "http2": False if p else True,
                }
                if p:
                    kwargs["proxy"] = p

                async with httpx.AsyncClient(**kwargs) as login_client:
                    resp = await login_client.post(
                        "/api/auth/login",
                        json={"login_id": self.email, "password": password},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        new_access = data.get("access_token")
                        new_refresh = data.get("refresh_token")
                        if new_access:
                            self.access_token = new_access
                            self.account["access_token"] = new_access
                            if new_refresh:
                                self.account["refresh_token"] = new_refresh
                            if self._client and not self._client.is_closed:
                                self._client.headers["authorization"] = f"Bearer {new_access}"
                            self._save_refreshed_account()
                            logger.info("[%s] Akun sesi habis berhasil Login Ulang secara otomatis!", self.worker_id)
                            return True
                    elif resp.status_code == 401:
                        # Password salah pada server
                        return False
            except Exception as exc:
                if p and is_proxy_error(exc):
                    logger.debug("[%s] Login via proxy gagal (%s), beralih ke jalur cadangan...", self.worker_id, exc)
                    await self.rotate_bad_proxy()
                continue

        return False

    async def ensure_valid_session(self) -> bool:
        """Memverifikasi keaktifan sesi token member; auto-refresh atau re-login bila kedaluwarsa."""
        if not self.access_token:
            if await self.refresh_access_token():
                return True
            return await self.login_with_password()

        client = await self.get_client()
        try:
            resp = await client.get("/api/auth/profile")
            if resp.status_code == 200:
                return True
            if resp.status_code == 401:
                if await self.refresh_access_token():
                    return True
                return await self.login_with_password()
        except Exception as exc:
            if is_proxy_error(exc) and self.proxy:
                await self.rotate_bad_proxy()
            if await self.refresh_access_token():
                return True
            return await self.login_with_password()
        return True

    async def get_read_chapter_ids(self, novel_id: str) -> set:
        """
        Mengambil daftar ID bab (dan nomor bab) yang sudah pernah dibaca oleh akun member ini.
        Endpoint: GET /api/v1/novels/{novel_id}/chapters?order=asc&include_read_progress=true
        Mengembalikan set yang berisi hash_id dan chapter_num dari bab yang sudah dibaca (is_read=True atau reading_progress >= 0.95).
        """
        read_set = set()
        client = await self.get_client()
        url = f"/api/v1/novels/{novel_id}/chapters"
        params = {
            "order": "asc",
            "page": 1,
            "per_page": 100,
            "include_read_progress": "true",
        }
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                for item in items:
                    is_read = item.get("is_read", False)
                    prog = float(item.get("reading_progress", 0.0) or 0.0)
                    if is_read or prog >= 0.95:
                        h_id = item.get("hash_id")
                        c_num = item.get("chapter_num")
                        if h_id:
                            read_set.add(h_id)
                        if c_num is not None:
                            read_set.add(c_num)
        except Exception as exc:
            logger.debug("[%s] Gagal fetch riwayat baca bab: %s", self.worker_id, exc)
        return read_set

    async def read_chapter(
        self,
        novel_id: str,
        chapter: Dict[str, Any],
        reading_delay_sec: float,
    ) -> Tuple[bool, str]:
        """
        Membaca satu bab novel sebagai Member:
        1. GET /api/v1/novels/{novel_id}/chapters/{chapter_id}
        2. Kirim progress scrolling bertahap tiap ~3 detik via POST /api/reading/progress
        3. POST /api/reading/v2/logs/post-view saat tuntas 100%
        """
        client = await self.get_client()
        ch_id = chapter.get("hash_id", "")
        ch_num = chapter.get("chapter_num", 1)
        ch_title = chapter.get("title", f"Bab {ch_num}")

        # 1. Mengambil konten bab secara dinamis dengan smart fallback unauthenticated
        ch_url = f"/api/v1/novels/{novel_id}/chapters/{ch_id}"
        try:
            get_resp = await client.get(ch_url)
            # Backend Quarterfull mengisolasi katalog antar pasar (market).
            # Jika novel asing mengembalikan 404 terhadap token regional, ambil konten publiknya secara universal.
            if get_resp.status_code == 404:
                clean_kwargs: Dict[str, Any] = {
                    "base_url": self.BASE_URL,
                    "timeout": httpx.Timeout(self.timeout),
                    "headers": {
                        "user-agent": self.user_agent,
                        "x-device-id": self.device_id,
                        "accept": "application/json",
                    },
                }
                if self.proxy:
                    clean_kwargs["proxy"] = self.proxy

                async with httpx.AsyncClient(**clean_kwargs) as clean_client:
                    get_resp = await clean_client.get(ch_url)

            get_resp.raise_for_status()
        except Exception as exc:
            if is_proxy_error(exc) and self.proxy:
                logger.warning("[%s] Proxy error (%s), mengeliminasi dan mencoba proxy baru...", self.worker_id, exc)
                await self.rotate_bad_proxy()
                return await self.read_chapter(novel_id, chapter, reading_delay_sec)
            return False, f"GET Chapter Gagal: {exc}"

        # 2. Simulasi jeda baca natural & heartbeat progress scrolling berkala (tiap ~3 detik)
        read_delay = max(4.0, reading_delay_sec)
        step_interval = random.uniform(2.5, 3.5)
        num_steps = max(3, int(read_delay / step_interval))
        step_time = read_delay / num_steps

        for step in range(1, num_steps + 1):
            await asyncio.sleep(step_time)
            current_pct = min(1.0, round(step / num_steps, 2))
            try:
                prog_payload = {
                    "novel_id": novel_id,
                    "chapter_id": ch_id,
                    "scroll_percent": current_pct,
                    "reading_progress": current_pct,
                    "read_mode": "scroll",
                }
                prog_resp = await client.post("/api/reading/progress", json=prog_payload)
                if prog_resp.status_code == 401:
                    refreshed = await self.refresh_access_token() or await self.login_with_password()
                    if refreshed:
                        await client.post("/api/reading/progress", json=prog_payload)
            except Exception as prog_exc:
                logger.debug("[%s] Gagal update reading progress bab: %s", self.worker_id, prog_exc)

        # 3. Kirim Heartbeat Sesi Membaca Member dengan Dwell Realistis (3-7 menit = 180s - 420s)
        target_dwell = random.uniform(180.0, 420.0)
        reading_session_id = f"reading:{novel_id}:{ch_id}:{int(time.time()*1000)}:{secrets.token_hex(4)}"
        heartbeat_payload = {
            "session_id": reading_session_id,
            "novel_id": novel_id,
            "chapter_id": ch_id,
            "active_seconds": int(target_dwell),
            "scroll_percent": 1.0,
            "reading_progress": 1.0,
            "chapter_num": ch_num,
            "novel_title": self.novel_title,
            "chapter_title": ch_title,
            "source": "chapter_route",
            "ended": True,
            "completed": True,
        }
        try:
            hb_resp = await client.post("/api/reading/sessions/heartbeat", json=heartbeat_payload)
            if hb_resp.status_code == 401:
                refreshed = await self.refresh_access_token() or await self.login_with_password()
                if refreshed:
                    await client.post("/api/reading/sessions/heartbeat", json=heartbeat_payload)
        except Exception as hb_exc:
            logger.debug("[%s] Heartbeat member bab %d: %s", self.worker_id, ch_num, hb_exc)

        # 4. Kirim Post-View Royalti Telemetri (Setelah tuntas 100%)
        now_iso = datetime.now(timezone.utc).isoformat()
        year_month = datetime.now().strftime("%Y-%m")

        post_view_payload = {
            "novel_hash_id": novel_id,
            "post_hash_id": ch_id,
            "post_title": ch_title,
            "novel_title": self.novel_title,
            "attribution": {
                "attribution_session_id": IdentifierGenerator.generate_session_id("attr", random_len=8),
                "work_id": novel_id,
                "first_touch": {
                    "discovery_method": "direct",
                    "source_screen": "chapter_route",
                    "touched_at": now_iso,
                    "entry_event_id": IdentifierGenerator.generate_session_id("entry", random_len=8),
                },
                "last_touch": {
                    "discovery_method": "direct",
                    "source_screen": "chapter_route",
                    "touched_at": now_iso,
                    "entry_event_id": IdentifierGenerator.generate_session_id("entry", random_len=8),
                },
            },
            "callback_contract": "telemetry_v2",
            "source_event_id": f"member:{self.user_id}:{ch_id}:{year_month}",
        }

        try:
            post_resp = await client.post("/api/reading/v2/logs/post-view", json=post_view_payload)
            if post_resp.status_code == 401:
                refreshed = await self.refresh_access_token() or await self.login_with_password()
                if refreshed:
                    post_resp = await client.post("/api/reading/v2/logs/post-view", json=post_view_payload)
            post_resp.raise_for_status()
            return True, "200 OK (Heartbeat Dwell & Post-View)"
        except Exception as exc:
            return False, f"Post-View Log Gagal: {exc}"


class ReadingSimulationOrchestrator:
    """Mengatur pembagian tugas, konkurensi Semaphore, dan visualisasi Rich Progress."""

    def __init__(
        self,
        novel_id: str,
        novel_title: str,
        chapters: List[Dict[str, Any]],
        member_accounts: List[Dict[str, Any]],
        guest_count: int,
        max_concurrency: int = 5,
        proxies: Optional[List[str]] = None,
        base_delay_per_chapter: float = 8.0,
        origin_country: str = "ID",
        skip_already_read: bool = True,
        inter_chapter_delay: float = 3.0,
    ) -> None:
        self.novel_id: str = novel_id
        self.novel_title: str = novel_title
        self.chapters: List[Dict[str, Any]] = chapters
        self.member_accounts: List[Dict[str, Any]] = member_accounts
        self.guest_count: int = guest_count
        self.max_concurrency: int = max(1, max_concurrency)
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(self.max_concurrency)
        self.origin_country: str = origin_country or "ID"
        self.skip_already_read: bool = skip_already_read
        self.inter_chapter_delay: float = max(0.0, inter_chapter_delay)
        if proxies is not None:
            self.proxy_manager = ProxyManager()
            self.proxy_manager.parsed_proxies = [ProxyInfo(p) for p in proxies]
        else:
            self.proxy_manager = default_proxy_manager
        self.proxies: List[str] = [p.raw_url for p in self.proxy_manager.parsed_proxies]
        self.base_delay: float = base_delay_per_chapter
        self.total_readers: int = len(member_accounts) + guest_count
        self.background_tasks: set = set()
        self.results: List[Dict[str, Any]] = []

    def _get_proxy_for_worker(self, country_code: str = "ID", session_id: Optional[str] = None) -> Optional[str]:
        if not self.proxy_manager.has_proxies:
            return None
        return self.proxy_manager.get_proxy(country_code=country_code, session_id=session_id)

    async def _background_dwell_pulser(
        self,
        client: Optional[httpx.AsyncClient],
        worker_id: str,
        is_guest: bool,
        novel_id: str,
        chapters: List[Dict[str, Any]],
        session_info: Dict[str, Any],
    ) -> None:
        """
        Background task yang terus mengirim pulse event dan telemetry membaca selama 3-7 menit di latar belakang,
        sehingga sesi pembaca berikutnya dapat langsung dieksekusi tanpa tertahan di foreground CLI.
        """
        if not client or client.is_closed:
            return

        total_duration = random.uniform(180.0, 420.0)  # 3 - 7 menit (180s - 420s)
        start_time = time.time()
        logger.info(
            "[%s BG] Memulai background dwell telemetry selama %.1f detik (%.1f menit)",
            worker_id,
            total_duration,
            total_duration / 60,
        )

        try:
            pulse_count = 0
            while (time.time() - start_time) < total_duration:
                # Interval antar pulse ~35 - 50 detik
                sleep_sec = random.uniform(35.0, 50.0)
                remaining = total_duration - (time.time() - start_time)
                if remaining <= 0:
                    break
                await asyncio.sleep(min(sleep_sec, remaining))

                pulse_count += 1
                elapsed = time.time() - start_time
                is_final_pulse = (elapsed >= total_duration - 10.0)

                for ch in chapters:
                    ch_id = ch.get("hash_id", "")
                    ch_num = ch.get("chapter_num", 1)
                    ch_title = ch.get("title", f"Bab {ch_num}")

                    current_dwell = min(total_duration, random.uniform(180.0, 420.0) + elapsed)

                    if is_guest:
                        payload = {
                            "chapter_hash_id": ch_id,
                            "progress": 1.0,
                            "active_reading_seconds": round(current_dwell, 2),
                            "completed": True,
                            "reading_session_id": IdentifierGenerator.generate_session_id("grs"),
                            "session_active_reading_seconds": round(current_dwell, 2),
                            "session_ended": is_final_pulse,
                            "session_end_reason": "completed" if is_final_pulse else None,
                            "platform": "android",
                            "entry_source": "chapter_route",
                            "attribution_session_id": IdentifierGenerator.generate_session_id("attr", random_len=8),
                            "chapter_number": ch_num,
                            "content_type": "novel",
                            "content_character_count": ch.get("char_count", 1500),
                            "read_mode": "scroll",
                        }
                        try:
                            await client.put("/api/guest-reading/progress", json=payload)
                        except Exception as p_err:
                            logger.debug("[%s BG] Gagal pulse guest progress: %s", worker_id, p_err)
                    else:
                        hb_payload = {
                            "session_id": f"reading:{novel_id}:{ch_id}:{int(time.time()*1000)}:{secrets.token_hex(4)}",
                            "novel_id": novel_id,
                            "chapter_id": ch_id,
                            "active_seconds": int(current_dwell),
                            "scroll_percent": 1.0,
                            "reading_progress": 1.0,
                            "chapter_num": ch_num,
                            "novel_title": self.novel_title,
                            "chapter_title": ch_title,
                            "source": "chapter_route",
                            "ended": is_final_pulse,
                            "completed": True,
                        }
                        try:
                            await client.post("/api/reading/sessions/heartbeat", json=hb_payload)
                        except Exception as hb_err:
                            logger.debug("[%s BG] Gagal pulse member heartbeat: %s", worker_id, hb_err)

                        try:
                            await client.post(
                                "/api/reading/progress",
                                json={
                                    "novel_id": novel_id,
                                    "chapter_id": ch_id,
                                    "scroll_percent": 1.0,
                                    "reading_progress": 1.0,
                                    "read_mode": "scroll",
                                },
                            )
                        except Exception:
                            pass

                logger.debug("[%s BG] Pulse ke-%d sukses (elapsed: %.1fs)", worker_id, pulse_count, elapsed)

            logger.info("[%s BG] Selesai siklus background dwell (total %.1fs)", worker_id, time.time() - start_time)

        except asyncio.CancelledError:
            pass
        except Exception as main_err:
            logger.debug("[%s BG] Error background dwell pulser: %s", worker_id, main_err)
        finally:
            if client and not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass

    async def _inter_chapter_pause(
        self,
        session: BaseReaderSession,
        worker_id: str,
        task_id: TaskID,
        progress: Progress,
        chapter: Dict[str, Any],
        next_chapter_num: int,
        pause_seconds: float,
        is_guest: bool,
    ) -> None:
        """
        Jeda istirahat antar-bab sesuai durasi yang ditentukan pengguna.
        Heartbeat dan reading progress sudah dikirim bersamaan saat membaca bab.
        """
        if pause_seconds <= 0:
            return
        ch_num = chapter.get("chapter_num", 1)
        progress.update(
            task_id,
            description=f"[cyan]Jeda Bab {ch_num}->{next_chapter_num} ({pause_seconds:.1f}s)[/]",
        )
        await asyncio.sleep(pause_seconds)

    async def _run_guest_worker(
        self,
        worker_idx: int,
        progress: Progress,
        overall_task: TaskID,
        slot_queue: asyncio.Queue,
    ) -> None:
        worker_id = f"Guest-{worker_idx:02d}"
        pool_countries = list(SUPPORTED_QUARTERFULL_COUNTRIES.keys())
        proxy_cc = random.choice(pool_countries)
        
        sess_key = f"guest_{worker_idx}_{int(time.time()*1000)}_{random.randint(1000, 9999)}"
        proxy = self._get_proxy_for_worker(country_code=proxy_cc, session_id=sess_key)
        session = GuestReaderSession(worker_id=worker_id, country=proxy_cc, proxy=proxy, proxy_manager=self.proxy_manager)

        async with self.semaphore:
            slot_idx, task_id = await slot_queue.get()
            try:
                progress.reset(task_id, total=len(self.chapters))
                progress.update(
                    task_id,
                    role=f"[cyan]{worker_id}[/]",
                    description="[dim]Inisialisasi...[/]",
                )
                ok = await session.init_guest_session()
                if not ok:
                    progress.update(task_id, description="[red]Gagal Inisialisasi[/]")
                    self.results.append({
                        "worker_id": worker_id,
                        "type": "Guest",
                        "ident": "Guest",
                        "country": proxy_cc,
                        "chapters_read": 0,
                        "status": "[red]Gagal[/]",
                    })
                    await session.close()
                    return

                ident = f"Guest:{session.guest_id[:8]}" if session.guest_id else "Guest"
                total_chs = len(self.chapters)
                ch_read_count = 0
                for ch_idx, ch in enumerate(self.chapters, start=1):
                    ch_num = ch.get("chapter_num", 1)
                    progress.update(
                        task_id,
                        description=f"[yellow]Bab {ch_num} ({ch_idx}/{total_chs})[/]",
                    )

                    read_sec = random.uniform(self.base_delay * 0.8, self.base_delay * 1.3)
                    success, status_msg = await session.read_chapter(self.novel_id, ch, read_sec)

                    if success:
                        ch_read_count += 1
                        progress.advance(task_id, 1)
                    else:
                        logger.warning("[%s] Bab %d: %s", worker_id, ch_num, status_msg)

                    if ch_idx < total_chs and success and self.inter_chapter_delay > 0:
                        next_ch_num = self.chapters[ch_idx].get("chapter_num", ch_num + 1)
                        await self._inter_chapter_pause(
                            session=session,
                            worker_id=worker_id,
                            task_id=task_id,
                            progress=progress,
                            chapter=ch,
                            next_chapter_num=next_ch_num,
                            pause_seconds=self.inter_chapter_delay,
                            is_guest=True,
                        )

                progress.update(task_id, description="[bold green]Selesai [OK][/]")
                self.results.append({
                    "worker_id": worker_id,
                    "type": "Guest",
                    "ident": ident,
                    "country": proxy_cc,
                    "chapters_read": ch_read_count,
                    "status": "[green]Sukses[/]",
                })

                detached_client = session.detach_client()
                if detached_client:
                    bg_task = asyncio.create_task(
                        self._background_dwell_pulser(
                            client=detached_client,
                            worker_id=worker_id,
                            is_guest=True,
                            novel_id=self.novel_id,
                            chapters=self.chapters,
                            session_info={
                                "guest_id": session.guest_id,
                                "guest_token": session.guest_token,
                            },
                        )
                    )
                    self.background_tasks.add(bg_task)
                    bg_task.add_done_callback(self.background_tasks.discard)

                await session.close()
            except Exception as exc:
                logger.error("[%s] Terjadi error pada guest worker: %s", worker_id, exc)
                progress.update(task_id, description=f"[bold red]Error: {str(exc)[:15]}[/]")
                self.results.append({
                    "worker_id": worker_id,
                    "type": "Guest",
                    "ident": ident,
                    "country": proxy_cc,
                    "chapters_read": ch_read_count,
                    "status": f"[red]Error: {str(exc)[:15]}[/]",
                })
                try:
                    await session.close()
                except Exception:
                    pass
            finally:
                progress.advance(overall_task, 1)
                slot_queue.put_nowait((slot_idx, task_id))

    async def _run_member_worker(
        self,
        worker_idx: int,
        account: Dict[str, Any],
        progress: Progress,
        overall_task: TaskID,
        slot_queue: asyncio.Queue,
    ) -> None:
        """Menjalankan satu sesi pembaca Member melalui seluruh bab."""
        worker_id = f"Member-{worker_idx:02d}"
        email = account.get("email", "user")
        short_email = email.split("@")[0][:12]
        raw_cc = str(account.get("country", "ID")).upper().strip()
        acc_country = raw_cc if raw_cc in SUPPORTED_QUARTERFULL_COUNTRIES else "ID"

        sess_key = f"member_{worker_idx}_{int(time.time()*1000)}_{random.randint(1000, 9999)}"
        proxy = self._get_proxy_for_worker(country_code=acc_country, session_id=sess_key)
        session = MemberReaderSession(
            worker_id=worker_id,
            account_data=account,
            novel_title=self.novel_title,
            proxy=proxy,
            proxy_manager=self.proxy_manager,
        )

        async with self.semaphore:
            slot_idx, task_id = await slot_queue.get()
            try:
                progress.reset(task_id, total=len(self.chapters))
                progress.update(
                    task_id,
                    role=f"[magenta]{worker_id}[/]",
                    description="[dim]Cek sesi token...[/]",
                )
                is_valid = await session.ensure_valid_session()
                if not is_valid:
                    progress.update(
                        task_id,
                        description="[bold red]Sesi Expired (Skip)[/]",
                    )
                    self.results.append({
                        "worker_id": worker_id,
                        "type": "Member",
                        "ident": short_email,
                        "country": acc_country,
                        "chapters_read": 0,
                        "status": "[red]Expired[/]",
                    })
                    await asyncio.sleep(1.0)
                    await session.close()
                    return

                read_ids = set()
                if self.skip_already_read:
                    progress.update(task_id, description="[dim]Cek riwayat baca bab...[/]")
                    read_ids = await session.get_read_chapter_ids(self.novel_id)

                total_chs = len(self.chapters)
                ch_read_count = 0
                for ch_idx, ch in enumerate(self.chapters, start=1):
                    ch_num = ch.get("chapter_num", 1)
                    ch_id = ch.get("hash_id", "")

                    # Cek apakah bab ini sudah pernah dibaca oleh akun ini
                    if self.skip_already_read and (ch_id in read_ids or ch_num in read_ids):
                        progress.update(
                            task_id,
                            description=f"[dim]Bab {ch_num} (Skip - Sudah Dibaca)[/]",
                        )
                        progress.advance(task_id, 1)
                        await asyncio.sleep(0.3)
                        continue

                    progress.update(
                        task_id,
                        description=f"[yellow]Bab {ch_num} ({ch_idx}/{total_chs})[/]",
                    )

                    read_sec = random.uniform(self.base_delay * 0.8, self.base_delay * 1.3)
                    success, status_msg = await session.read_chapter(self.novel_id, ch, read_sec)

                    if success:
                        ch_read_count += 1
                        progress.advance(task_id, 1)
                    else:
                        logger.warning("[%s] Bab %d: %s", worker_id, ch_num, status_msg)

                    if ch_idx < total_chs and success and self.inter_chapter_delay > 0:
                        next_ch_num = self.chapters[ch_idx].get("chapter_num", ch_num + 1)
                        await self._inter_chapter_pause(
                            session=session,
                            worker_id=worker_id,
                            task_id=task_id,
                            progress=progress,
                            chapter=ch,
                            next_chapter_num=next_ch_num,
                            pause_seconds=self.inter_chapter_delay,
                            is_guest=False,
                        )

                progress.update(task_id, description="[bold green]Selesai [OK][/]")
                self.results.append({
                    "worker_id": worker_id,
                    "type": "Member",
                    "ident": short_email,
                    "country": acc_country,
                    "chapters_read": ch_read_count,
                    "status": "[green]Sukses[/]",
                })

                detached_client = session.detach_client()
                if detached_client:
                    bg_task = asyncio.create_task(
                        self._background_dwell_pulser(
                            client=detached_client,
                            worker_id=worker_id,
                            is_guest=False,
                            novel_id=self.novel_id,
                            chapters=self.chapters,
                            session_info={
                                "user_id": session.user_id,
                                "email": session.email,
                            },
                        )
                    )
                    self.background_tasks.add(bg_task)
                    bg_task.add_done_callback(self.background_tasks.discard)

                await session.close()
            except Exception as exc:
                logger.error("[%s] Terjadi error pada member worker: %s", worker_id, exc)
                progress.update(task_id, description=f"[bold red]Error: {str(exc)[:15]}[/]")
                self.results.append({
                    "worker_id": worker_id,
                    "type": "Member",
                    "ident": short_email,
                    "country": acc_country,
                    "chapters_read": ch_read_count,
                    "status": f"[red]Error: {str(exc)[:15]}[/]",
                })
                try:
                    await session.close()
                except Exception:
                    pass
            finally:
                progress.advance(overall_task, 1)
                slot_queue.put_nowait((slot_idx, task_id))

    async def run(self) -> None:
        """Mengeksekusi seluruh antrean reader dengan monitor visual Progress Rich dinamis."""
        # Selalu pastikan menarik proxy baru sebelum mulai membaca
        self.proxy_manager.ensure_fresh_proxies()

        total_chapters = len(self.chapters)
        if total_chapters == 0:
            console.print("[red]Tidak ada bab gratis yang dapat dibaca pada novel ini.[/]")
            return

        total_tasks = len(self.member_accounts) + self.guest_count
        num_slots = min(self.max_concurrency, total_tasks)
        self.results = []

        console.print(
            Panel(
                f"[bold cyan]Target Novel:[/] [bold yellow]{self.novel_title}[/] (ID: [cyan]{self.novel_id}[/])\n"
                f"[bold cyan]Total Reader:[/] [bold white]{total_tasks}[/] Sesi ({len(self.member_accounts)} Member + {self.guest_count} Guest)\n"
                f"[bold cyan]Bab Tersedia:[/] [bold white]{total_chapters}[/] Bab Gratis  |  "
                f"[bold cyan]Konkurensi:[/] [bold green]{num_slots}[/] Slot Paralel",
                title="[bold green]Memulai Auto Readers Simulator[/]",
                border_style="cyan",
            )
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.fields[role]}[/]", justify="left", table_column=Column(no_wrap=True)),
            TextColumn("{task.description}", justify="left", table_column=Column(no_wrap=True)),
            BarColumn(bar_width=16),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            refresh_per_second=4,
        ) as progress:
            overall_task = progress.add_task(
                description="[dim]Memproses antrean reader...[/]",
                total=total_tasks,
                role="[bold yellow]★ TOTAL[/]",
            )
            slot_queue: asyncio.Queue = asyncio.Queue()
            for s_idx in range(1, num_slots + 1):
                tid = progress.add_task(
                    description="[dim]Menunggu antrean...[/]",
                    total=total_chapters,
                    role=f"Slot-{s_idx:02d}",
                )
                slot_queue.put_nowait((s_idx, tid))

            tasks = []
            for idx, acc in enumerate(self.member_accounts, start=1):
                tasks.append(self._run_member_worker(idx, acc, progress, overall_task, slot_queue))

            for idx in range(1, self.guest_count + 1):
                tasks.append(self._run_guest_worker(idx, progress, overall_task, slot_queue))

            await asyncio.gather(*tasks)

        # Cetak Tabel Laporan Rapi di Akhir
        report_table = Table(
            title=f"[bold green]Laporan Sesi Auto Reader ({self.novel_title})[/]",
            border_style="cyan",
        )
        report_table.add_column("No", style="dim", width=4)
        report_table.add_column("Tipe", style="bold", width=8)
        report_table.add_column("Identitas / Akun", style="bold white")
        report_table.add_column("Negara", style="cyan", width=8)
        report_table.add_column("Bab Dibaca", justify="center", width=12)
        report_table.add_column("Status", style="bold")

        for r_idx, r in enumerate(self.results, start=1):
            report_table.add_row(
                str(r_idx),
                r["type"],
                r["ident"],
                r["country"],
                f"{r['chapters_read']}/{total_chapters}",
                r["status"],
            )

        console.print(report_table)


def load_accounts_from_file(file_path: str = "akun.txt") -> List[Dict[str, Any]]:
    """Membaca daftar akun terdaftar dari file JSON Lines."""
    accounts = []
    p = Path(file_path)
    if not p.exists():
        return []

    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                if data.get("access_token"):
                    accounts.append(data)
            except Exception:
                continue
    return accounts


def load_proxies_from_file(file_path: str = "proxies.txt") -> List[str]:
    """Membaca daftar proxy dari file teks jika ada."""
    p = Path(file_path)
    if not p.exists():
        return []

    proxies = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            px = line.strip()
            if px and not px.startswith("#"):
                # Normalisasi prefix http jika belum ada
                if not (px.startswith("http://") or px.startswith("https://") or px.startswith("socks5://")):
                    px = f"http://{px}"
                proxies.append(px)
    return proxies


def render_banner() -> None:
    """Menampilkan banner judul aplikasi."""
    banner_text = Text()
    banner_text.append("=== TOODAT / QUARTERFULL ===\n", style="bold cyan")
    banner_text.append("AUTO READERS & STREAM VIEW SIMULATOR\n", style="bold yellow")
    banner_text.append("Simulasi Pembaca Alami (Member Royalti & Guest Heartbeat) - HTTP/2 Engine", style="dim")

    console.print(
        Panel(
            banner_text,
            border_style="bright_blue",
            padding=(1, 2),
        )
    )


def parse_chapter_selection(input_str: str, chapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Mem-parsing input teks pengguna menjadi daftar bab yang dipilih.
    Mendukung format:
    - Satu bab: "10" atau "bab 10"
    - Rentang: "10-25", "10 - 25", "10 to 25", "10 s/d 25", "10 sampai 25"
    - Daftar: "1, 3, 5, 10-15"
    - Gabungan koma/spasi/titik dua
    Mengembalikan daftar bab yang cocok dan berurutan sesuai urutan bab asli.
    """
    if not input_str or not chapters:
        return []

    cleaned = input_str.strip().lower()
    # Bersihkan kata 'bab' atau 'chapter'
    cleaned = re.sub(r"\b(bab|chapter|ch)\b", "", cleaned, flags=re.IGNORECASE)

    # Ganti kata-kata penghubung rentang dengan '-'
    for sep in [" sampai ", " s/d ", " to ", " sd ", ":"]:
        cleaned = cleaned.replace(sep, "-")

    # Rapikan spasi di sekitar '-'
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)

    target_nums = set()
    # Pisahkan berdasarkan koma, titik koma, atau spasi
    tokens = re.split(r"[,;\s]+", cleaned)
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start = int(parts[0])
                end = int(parts[1])
                for n in range(min(start, end), max(start, end) + 1):
                    target_nums.add(n)
        elif token.isdigit():
            target_nums.add(int(token))

    # Cocokkan dengan chapters yang tersedia dan pertahankan urutan aslinya
    selected = []
    for idx, ch in enumerate(chapters, start=1):
        c_num = ch.get("chapter_num")
        num = c_num if c_num is not None else idx
        if num in target_nums:
            selected.append(ch)

    return selected


def format_chapters_summary(selected_chapters: List[Dict[str, Any]]) -> str:
    """Memformat ringkasan bab yang dipilih untuk tampilan tabel ringkasan."""
    if not selected_chapters:
        return "0 Bab (Dilewati / Tidak Membaca)"

    nums = [ch.get("chapter_num", i + 1) for i, ch in enumerate(selected_chapters)]
    total = len(selected_chapters)

    if total == 1:
        return f"1 Bab (Bab {nums[0]})"

    # Periksa apakah berurutan (kontigu)
    is_consecutive = all(nums[i] + 1 == nums[i + 1] for i in range(len(nums) - 1))
    if is_consecutive:
        return f"{total} Bab (Bab {nums[0]} s/d Bab {nums[-1]})"

    # Jika acak / terpilih sebagian
    if total <= 5:
        joined = ", ".join(f"Bab {n}" for n in nums)
        return f"{total} Bab ({joined})"
    else:
        preview = ", ".join(str(n) for n in nums[:5])
        return f"{total} Bab (Bab {preview}, ... s/d Bab {nums[-1]})"


def prompt_chapter_selection(
    chapters: List[Dict[str, Any]],
    console: Console,
    allow_skip: bool = False,
) -> List[Dict[str, Any]]:
    """
    Antarmuka prompt interaktif untuk memilih bab novel.
    Mendukung:
    1. Semua bab gratis
    2. Satu bab tertentu (contoh: bab 10)
    3. Rentang bab tertentu (contoh: bab 10-25)
    4. Beberapa bab kustom (contoh: 1, 3, 5, 10-15)
    5. N bab pertama (contoh: 3 bab pertama)
    6. Lewati membaca (jika allow_skip=True)
    """
    if not chapters:
        return []

    min_ch = chapters[0].get("chapter_num", 1)
    max_ch = chapters[-1].get("chapter_num", len(chapters))
    total_avail = len(chapters)

    console.print("\n[bold cyan]Pilihan Mode Bab yang Akan Dibaca:[/]")
    console.print(f"  [bold green][1][/] Baca [bold yellow]Semua Bab Gratis[/] yang tersedia (Total: [cyan]{total_avail}[/] Bab, No. {min_ch} - {max_ch}) [bold green][Default][/]")
    console.print("  [bold green][2][/] Pilih [bold yellow]Satu Bab Tertentu[/] saja (Contoh: hanya membaca Bab 10)")
    console.print("  [bold green][3][/] Pilih [bold yellow]Rentang Bab Tertentu[/] (Contoh: Bab 10 sampai 25 / [white]10-25[/])")
    console.print("  [bold green][4][/] Pilih [bold yellow]Beberapa Bab / Kustom[/] (Contoh: [white]1, 3, 5, 10-15[/])")
    console.print("  [bold green][5][/] Baca [bold yellow]N Bab Pertama[/] saja (Contoh: 3 bab pertama)")
    if allow_skip:
        console.print("  [bold green][6][/] [dim]Lewati Membaca (Hanya jalankan Like, Bookmark/Simpan, & Follow)[/]")

    choices = ["1", "2", "3", "4", "5"]
    if allow_skip:
        choices.append("6")

    while True:
        chapter_mode = Prompt.ask(
            "[bold green]?[/] Pilih mode bab",
            choices=choices,
            default="1",
        )

        # Mode 1: Semua bab
        if chapter_mode == "1":
            return list(chapters)

        # Mode 2: Satu bab tertentu
        elif chapter_mode == "2":
            while True:
                single_input = Prompt.ask(
                    f"[bold green]?[/] Masukkan nomor bab yang ingin dibaca [contoh: {min_ch} - {max_ch}]",
                    default=str(min_ch),
                ).strip()
                selected = parse_chapter_selection(single_input, chapters)
                if selected:
                    ch_info = selected[0]
                    c_num = ch_info.get("chapter_num", single_input)
                    c_title = ch_info.get("title", f"Bab {c_num}")
                    console.print(f"  [bold green][OK][/] Terpilih: [bold yellow]Bab {c_num} - {c_title}[/]")
                    return selected
                console.print(f"  [bold red][!][/] Bab '{single_input}' tidak ditemukan di daftar bab gratis (Tersedia: {min_ch} s/d {max_ch}). Silakan coba lagi.")

        # Mode 3: Rentang bab
        elif chapter_mode == "3":
            while True:
                range_input = Prompt.ask(
                    f"[bold green]?[/] Masukkan rentang bab [contoh: 10-25] (Tersedia: {min_ch} s/d {max_ch})",
                    default=f"{min_ch}-{min(min_ch + 2, max_ch)}",
                ).strip()
                selected = parse_chapter_selection(range_input, chapters)
                if selected:
                    start_n = selected[0].get("chapter_num")
                    end_n = selected[-1].get("chapter_num")
                    if len(selected) == 1:
                        console.print(f"  [bold green][OK][/] Terpilih 1 bab: [bold yellow]Bab {start_n}[/]")
                    else:
                        console.print(f"  [bold green][OK][/] Terpilih [bold cyan]{len(selected)}[/] bab: [bold yellow]Bab {start_n} s/d Bab {end_n}[/]")
                    return selected
                console.print(f"  [bold red][!][/] Rentang '{range_input}' tidak menghasilkan bab gratis yang cocok. Silakan coba lagi.")

        # Mode 4: Beberapa bab kustom
        elif chapter_mode == "4":
            while True:
                custom_input = Prompt.ask(
                    "[bold green]?[/] Masukkan daftar bab yang ingin dibaca [contoh: 1, 3, 5, 10-15]",
                ).strip()
                selected = parse_chapter_selection(custom_input, chapters)
                if selected:
                    nums_preview = ", ".join(str(ch.get("chapter_num")) for ch in selected[:8])
                    more_str = "..." if len(selected) > 8 else ""
                    console.print(f"  [bold green][OK][/] Terpilih [bold cyan]{len(selected)}[/] bab: [bold yellow]Bab {nums_preview}{more_str}[/]")
                    return selected
                console.print(f"  [bold red][!][/] Input '{custom_input}' tidak cocok dengan bab yang tersedia. Silakan coba lagi.")

        # Mode 5: N bab pertama
        elif chapter_mode == "5":
            n_first = IntPrompt.ask(
                f"[bold green]?[/] Berapa bab pertama yang ingin dibaca? [1 - {total_avail}]",
                default=min(3, total_avail),
            )
            n_first = max(1, min(n_first, total_avail))
            selected = chapters[:n_first]
            console.print(f"  [bold green][OK][/] Terpilih [bold cyan]{len(selected)}[/] bab pertama (Bab {selected[0].get('chapter_num')} s/d {selected[-1].get('chapter_num')})")
            return selected

        # Mode 6: Lewati membaca
        elif chapter_mode == "6" and allow_skip:
            console.print("  [yellow]Membaca novel dilewati. Hanya menjalankan aksi interaksi.[/]")
            return []


async def main_async(preset_novel_id: Optional[str] = None) -> None:
    """Fungsi utama antarmuka interaktif CLI Auto Readers."""
    render_banner()

    # 1. Ringkasan Status Sistem
    accounts = load_accounts_from_file("akun.txt")
    proxies = load_proxies_from_file("proxies.txt")

    status_table = Table(title="[bold green]Status Sumber Daya Sistem[/]", border_style="dim")
    status_table.add_column("Komponen", style="cyan")
    status_table.add_column("Jumlah", style="bold yellow")
    status_table.add_column("Keterangan", style="dim")

    status_table.add_row(
        "Akun Terdaftar (akun.txt)",
        str(len(accounts)),
        "Siap untuk simulasi Member Reader" if accounts else "Belum ada akun, silakan buat via auto_signup.py",
    )
    if default_proxy_manager.is_brightdata:
        proxy_count_str = "Bright Data"
        proxy_desc_str = "Dynamic IP Rotation (1 Sesi = 1 IP Baru Berbeda & Negara Acak)"
        proxy_summary_str = "Bright Data SuperProxy (Rotasi 1 IP Baru per Sesi & Multi-Negara)"
    elif proxies:
        proxy_count_str = str(len(proxies))
        proxy_desc_str = f"{len(proxies)} IP aktif termuat"
        proxy_summary_str = f"{len(proxies)} Proxy Standar"
    else:
        proxy_count_str = "0"
        proxy_desc_str = "Koneksi Langsung / Direct Connection"
        proxy_summary_str = "Direct (Tanpa Proxy)"

    status_table.add_row(
        "Daftar Proxy (proxies.txt)",
        proxy_count_str,
        proxy_desc_str,
    )

    console.print(status_table)
    console.print()

    # 2. Interaksi Input Pengguna
    novel_id = NovelTargetResolver.extract_novel_id(preset_novel_id) if preset_novel_id else None
    while not novel_id:
        raw_novel_input = Prompt.ask(
            "[bold green]?[/] Masukkan URL atau Novel ID target (contoh: Py7LDdwpEQ8e1YKX)",
            default="Py7LDdwpEQ8e1YKX",
        )
        novel_id = NovelTargetResolver.extract_novel_id(raw_novel_input)
        if novel_id:
            break
        console.print("[red]ID Novel tidak valid! Format harus berupa 16-karakter hash ID.[/]")

    # Ambil detail novel dan bab
    with console.status("[bold cyan]Mengambil detail metadata novel dan bab...[/]"):
        try:
            novel_info = await NovelTargetResolver.fetch_novel_details(novel_id)
            novel_title = novel_info.get("title", f"Novel {novel_id}")
            origin_country = novel_info.get("origin_country", "ID")
            chapters = await NovelTargetResolver.fetch_readable_chapters(novel_id, origin_country=origin_country)
        except Exception as exc:
            console.print(f"[bold red]Gagal mengambil informasi novel:[/] {exc}")
            return

    console.print(f"[bold green][OK][/] Novel Ditemukan: [bold yellow]{novel_title}[/] (Total Bab: [bold cyan]{len(chapters)}[/] | Origin: [bold magenta]{origin_country}[/])")

    if len(chapters) == 0:
        console.print("[red]Novel ini tidak memiliki bab gratis untuk dibaca.[/]")
        return

    # Input jumlah reader member
    max_member_available = len(accounts)
    member_count = 0
    if max_member_available > 0:
        member_count = IntPrompt.ask(
            f"[bold green]?[/] Jumlah Reader Login (Member) yang diinginkan [0 - {max_member_available}]",
            default=min(2, max_member_available),
        )
        if member_count > max_member_available:
            console.print(f"[yellow]Disesuaikan ke jumlah stok maksimum: {max_member_available}[/]")
            member_count = max_member_available
    else:
        console.print("[dim]Lewati reader login karena belum ada data di akun.txt.[/]")

    # Input jumlah reader tamu
    guest_count = IntPrompt.ask(
        "[bold green]?[/] Jumlah Guest Reader yang diinginkan",
        default=2,
    )

    if member_count == 0 and guest_count == 0:
        console.print("[yellow]Jumlah reader adalah 0. Tugas dibatalkan.[/]")
        return

    # Input concurrency
    max_workers = IntPrompt.ask(
        "[bold green]?[/] Jumlah Sesi Konkuren / Worker bersamaan",
        default=min(4, member_count + guest_count),
    )

    # Input mode bab (Fleksibel: Semua, Satu Bab, Rentang 10-25, Beberapa Bab Kustom, N Pertama)
    selected_chapters = prompt_chapter_selection(chapters, console, allow_skip=False)
    if not selected_chapters:
        console.print("[yellow]Tidak ada bab yang dipilih untuk dibaca. Tugas dibatalkan.[/]")
        return

    # Opsi Lewatkan bab yang sudah pernah dibaca (khusus akun member)
    skip_already_read = True
    if member_count > 0:
        skip_already_read = Confirm.ask(
            "[bold green]?[/] Lewatkan bab yang sudah pernah dibaca oleh akun member? (Auto-skip)",
            default=True,
        )

    # Opsi Jeda Antar-Bab yang fleksibel
    inter_chapter_delay = float(
        Prompt.ask(
            "[bold green]?[/] Waktu jeda istirahat antar-bab dalam detik (rekomendasi: 2 - 5)",
            default="3.0",
        )
    )

    # 3. Ringkasan Tugas & Konfirmasi Eksekusi
    summary_table = Table(title="[bold yellow]Rencana Tugas Simulasi Membaca[/]", border_style="cyan")
    summary_table.add_column("Parameter", style="cyan")
    summary_table.add_column("Konfigurasi", style="bold green")

    summary_table.add_row("Target Novel", f"{novel_title} ({novel_id})")
    summary_table.add_row("Jumlah Bab per Reader", format_chapters_summary(selected_chapters))
    summary_table.add_row("Jeda Antar-Bab", f"{inter_chapter_delay:.1f} Detik")
    if member_count > 0:
        summary_table.add_row(
            "Auto-Skip Bab Terbaca",
            "[green]Aktif (Lewati bab yang sudah dibaca)[/]" if skip_already_read else "[yellow]Nonaktif (Baca ulang semua)[/]",
        )
    summary_table.add_row("Member Readers (Login)", f"{member_count} Akun")
    summary_table.add_row("Guest Readers (Tamu)", f"{guest_count} Sesi")
    summary_table.add_row("Total Sesi Reader", f"{member_count + guest_count} Readers")
    summary_table.add_row("Batas Konkurensi", f"{max_workers} Worker Paralel")
    summary_table.add_row("Proxy Digunakan", proxy_summary_str)

    console.print("\n", summary_table, "\n")

    confirm = Confirm.ask("[bold green]?[/] Mulai eksekusi simulasi membaca sekarang?", default=True)
    if not confirm:
        console.print("[yellow]Simulasi dibatalkan oleh pengguna.[/]")
        return

    console.print("\n[bold green]Memulai simulasi membaca... Harap tunggu hingga seluruh reader selesai.[/]\n")

    # Ambil akun yang akan dipakai (pastikan unik)
    chosen_accounts = accounts[:member_count]

    # Jalankan Orchestrator
    orchestrator = ReadingSimulationOrchestrator(
        novel_id=novel_id,
        novel_title=novel_title,
        chapters=selected_chapters,
        member_accounts=chosen_accounts,
        guest_count=guest_count,
        max_concurrency=max_workers,
        proxies=proxies,
        base_delay_per_chapter=4.0,  # 4 detik simulasi per bab untuk efisiensi
        origin_country=novel_info.get("origin_country", "ID") or "ID",
        skip_already_read=skip_already_read,
        inter_chapter_delay=inter_chapter_delay,
    )

    start_time = time.time()
    await orchestrator.run()
    elapsed = time.time() - start_time

    console.print(
        Panel(
            f"[bold green][OK] SELURUH SESI PEMBACA SELESAI DENGAN SUKSES![/]\n"
            f"[dim]Total Waktu Eksekusi: {elapsed:.2f} detik | Total Reader: {member_count + guest_count}[/]",
            border_style="green",
        )
    )

    active_bg = len(orchestrator.background_tasks)
    if active_bg > 0:
        console.print(
            f"\n[bold cyan][BG TELEMETRY][/] [yellow]{active_bg} sesi background dwell pulser sedang aktif berjalan di latar belakang (3-7 menit).[/]\n"
            f"[dim]Metrik dwell 3-7 menit per bab sudah tercatat secara langsung di Quarterfull, dan background task terus memperbarui telemetry.[/]"
        )
        try:
            wait_bg = Confirm.ask(
                "[bold green]?[/] Apakah Anda ingin menunggu seluruh background dwell pulser selesai sempurna?",
                default=False,
            )
            if wait_bg:
                with console.status("[bold cyan]Menunggu background dwell pulser (Tekan Ctrl+C jika ingin langsung keluar)...[/]"):
                    try:
                        await asyncio.gather(*list(orchestrator.background_tasks), return_exceptions=True)
                    except (KeyboardInterrupt, asyncio.CancelledError):
                        console.print("[yellow]Menutup background task dan keluar...[/]")
        except (KeyboardInterrupt, EOFError):
            pass


def main() -> None:
    """Entry point CLI."""
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Operasi dibatalkan atau input dihentikan oleh pengguna.[/]")
    except Exception as exc:
        console.print(f"\n[bold red]Terjadi kesalahan sistem:[/] {exc}")


if __name__ == "__main__":
    main()
