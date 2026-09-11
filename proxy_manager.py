"""
Modul Manajemen & Integrasi Proxy Cerdas (Proxy Manager)
Dioptimalkan untuk HypeProxy API (Official Integration) & Bright Data ISP / SuperProxy.

Fitur Unggulan:
1. HypeProxy API Integration (16 Endpoint Lengkap):
   - Sinkronisasi otomatis daftar proxy dari HypeProxy API (X-API-Key).
   - Format koneksi otomatis via `proxy.hypeproxy.site` (207.241.173.98).
   - Rotasi IP instan on-demand (`POST /api/proxies/:id/rotate`) saat proxy gagal/selesai digunakan.
   - Manajemen region/negara dinamis (`PATCH /api/proxies/:id/region`).
   - Monitoring profil, saldo, daftar order, dan perpanjangan masa aktif proxy.
2. Bright Data Dynamic Country Targeting:
   - Mendeteksi proxy Bright Data (brd.superproxy.io, lum-superproxy.io).
   - Mengubah parameter negara (`-country-{code}`) secara dinamis saat runtime sesuai
     negara profil akun atau target pembaca.
3. HTTP/2 vs HTTP/1.1 Forward Tunnel Safeguard:
   - Secara otomatis menyetel `http2=False` saat melalui proxy untuk mencegah timeout/hang
     pada koneksi HTTP CONNECT tunnel.
4. Health Check & Geo Diagnostics:
   - Menguji konektivitas ke geo diagnostics serta API target (https://api.quarterfull.io).
"""

import concurrent.futures
import json
import logging
import os
import random
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse, urlunparse

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

logger = logging.getLogger("ProxyManager")
console = Console()


# 23 Negara Resmi Quarterfull yang Terverifikasi di API /api/v1/service-countries & Memiliki IP Aktif
SUPPORTED_QUARTERFULL_COUNTRIES: Dict[str, Dict[str, str]] = {
    "US": {"name": "United States", "timezone": "America/New_York", "lang": "en-US,en;q=0.9"},
    "KR": {"name": "South Korea", "timezone": "Asia/Seoul", "lang": "ko-KR,ko;q=0.9"},
    "JP": {"name": "Japan", "timezone": "Asia/Tokyo", "lang": "ja-JP,ja;q=0.9"},
    "ID": {"name": "Indonesia", "timezone": "Asia/Jakarta", "lang": "id"},
    "GB": {"name": "United Kingdom", "timezone": "Europe/London", "lang": "en-GB,en;q=0.9"},
    "AU": {"name": "Australia", "timezone": "Australia/Sydney", "lang": "en-AU,en;q=0.9"},
    "CA": {"name": "Canada", "timezone": "America/Toronto", "lang": "en-CA,en;q=0.9"},
    "SG": {"name": "Singapore", "timezone": "Asia/Singapore", "lang": "en-SG,en;q=0.9"},
    "MY": {"name": "Malaysia", "timezone": "Asia/Kuala_Lumpur", "lang": "ms-MY,ms;q=0.9,en;q=0.8"},
    "JP": {"name": "Japan", "timezone": "Asia/Tokyo", "lang": "ja-JP,ja;q=0.9"},
    "IN": {"name": "India", "timezone": "Asia/Kolkata", "lang": "en-IN,en;q=0.9,hi;q=0.8"},
    "DE": {"name": "Germany", "timezone": "Europe/Berlin", "lang": "de-DE,de;q=0.9"},
    "FR": {"name": "France", "timezone": "Europe/Paris", "lang": "fr-FR,fr;q=0.9"},
    "ES": {"name": "Spain", "timezone": "Europe/Madrid", "lang": "es-ES,es;q=0.9"},
    "IT": {"name": "Italy", "timezone": "Europe/Rome", "lang": "it-IT,it;q=0.9"},
    "NL": {"name": "Netherlands", "timezone": "Europe/Amsterdam", "lang": "nl-NL,nl;q=0.9"},
    "SE": {"name": "Sweden", "timezone": "Europe/Stockholm", "lang": "sv-SE,sv;q=0.9"},
    "NO": {"name": "Norway", "timezone": "Europe/Oslo", "lang": "no-NO,no;q=0.9"},
    "DK": {"name": "Denmark", "timezone": "Europe/Copenhagen", "lang": "da-DK,da;q=0.9"},
    "PL": {"name": "Poland", "timezone": "Europe/Warsaw", "lang": "pl-PL,pl;q=0.9"},
    "CZ": {"name": "Czech Republic", "timezone": "Europe/Prague", "lang": "cs-CZ,cs;q=0.9"},
    "TR": {"name": "Turkey", "timezone": "Europe/Istanbul", "lang": "tr-TR,tr;q=0.9"},
    "BR": {"name": "Brazil", "timezone": "America/Sao_Paulo", "lang": "pt-BR,pt;q=0.9"},
    "AR": {"name": "Argentina", "timezone": "America/Argentina/Buenos_Aires", "lang": "es-AR,es;q=0.9"},
    "CO": {"name": "Colombia", "timezone": "America/Bogota", "lang": "es-CO,es;q=0.9"},
}

BRIGHTDATA_SUPPORTED_COUNTRIES = set(SUPPORTED_QUARTERFULL_COUNTRIES.keys())


def get_weighted_royalty_country(candidate_countries: Optional[List[str]] = None) -> str:
    """
    Memilih kode negara dengan pembobotan prioritas royalti tinggi (Tier 1):
    - US (Amerika Serikat): ~20% - 25% (2 dari 10 sesi)
    - KR (Korea Selatan): ~15% (1-2 dari 10 sesi)
    - JP (Jepang): ~15% (1-2 dari 10 sesi)
    - Sisanya (~45% - 50%): didistribusikan merata ke negara-negara lain.
    """
    if candidate_countries:
        pool = list(candidate_countries)
    else:
        pool = list(SUPPORTED_QUARTERFULL_COUNTRIES.keys())

    tier1_weights = {
        "US": 25.0,
        "KR": 15.0,
        "JP": 15.0,
    }
    present_tier1 = [c for c in tier1_weights if c in pool]
    sum_tier1 = sum(tier1_weights[c] for c in present_tier1)
    remaining_pool = [c for c in pool if c not in tier1_weights]

    if not remaining_pool:
        weights = [tier1_weights.get(c, 1.0) for c in pool]
    else:
        rem_weight = max(0.1, (100.0 - sum_tier1) / len(remaining_pool))
        weights = [tier1_weights.get(c, rem_weight) for c in pool]

    return random.choices(pool, weights=weights, k=1)[0]


def sanitize_proxy_url(raw_proxy: str) -> Optional[str]:
    """
    Membersihkan dan menormalisasi URL proxy dari format kotor seperti:
    - socks5://40.160.136.215:1080:United States -> socks5://40.160.136.215:1080
    - http://user:pass@proxy.hypeproxy.site:1043 -> http://user:pass@proxy.hypeproxy.site:1043
    - 190.238.231.65:1994 -> http://190.238.231.65:1994
    - ip:port:user:pass -> http://user:pass@ip:port
    - user:pass:ip:port -> http://user:pass@ip:port
    """
    raw = (raw_proxy or "").strip()
    if not raw or raw.startswith("#"):
        return None

    scheme = "http"
    if "://" in raw:
        parts = raw.split("://", 1)
        scheme = parts[0].lower()
        remainder = parts[1]
    else:
        remainder = raw

    # Jangan dukung protokol tidak kompatibel httpx
    if scheme in ("socks4", "socks4a"):
        return None

    if "@" in remainder:
        auth, host_part = remainder.rsplit("@", 1)
        # Pisahkan host:port dari trailing fragment atau tag
        host_tokens = host_part.split("#")[0].split("?")[0]
        hp_tokens = host_tokens.split(":")
        host = hp_tokens[0].strip()
        if len(hp_tokens) > 1 and hp_tokens[1].isdigit() and 1 <= int(hp_tokens[1]) <= 65535:
            return f"{scheme}://{auth}@{host}:{hp_tokens[1]}"
        return None

    tokens = remainder.split(":")
    if len(tokens) == 2:
        host, port_str = tokens[0].strip(), tokens[1].strip()
        if port_str.isdigit() and 1 <= int(port_str) <= 65535:
            return f"{scheme}://{host}:{port_str}"
    elif len(tokens) >= 3:
        # Format ip:port:country atau ip:port:user:pass
        if tokens[1].isdigit() and 1 <= int(tokens[1]) <= 65535:
            host = tokens[0].strip()
            port = tokens[1].strip()
            if len(tokens) == 4 and not any(" " in t for t in tokens[2:]):
                user, pwd = tokens[2].strip(), tokens[3].strip()
                return f"{scheme}://{user}:{pwd}@{host}:{port}"
            return f"{scheme}://{host}:{port}"
        # Format user:pass:ip:port
        elif tokens[-1].isdigit() and 1 <= int(tokens[-1]) <= 65535:
            port = tokens[-1].strip()
            host = tokens[-2].strip()
            if len(tokens) >= 4:
                user = tokens[0].strip()
                pwd = ":".join(tokens[1:-2]).strip()
                return f"{scheme}://{user}:{pwd}@{host}:{port}"
            return f"{scheme}://{host}:{port}"

    return None


class ProxyInfo:
    """Menganalisis, mengidentifikasi, dan memformat URL proxy."""

    def __init__(self, raw_url: str):
        cleaned = sanitize_proxy_url(raw_url)
        self.raw_url = cleaned if cleaned else raw_url.strip()
        self.is_brightdata: bool = False
        self.is_hypeproxy: bool = False
        self.proxy_id: Optional[int] = None
        self.scheme: str = "http"
        self.username: str = ""
        self.password: str = ""
        self.host: str = ""
        self.port: int = 80
        self.customer: str = ""
        self.zone: str = ""
        self.current_country: Optional[str] = None
        self._parse()

    def _parse(self) -> None:
        if not self.raw_url:
            return

        url_to_parse = self.raw_url.strip()
        if "://" not in url_to_parse:
            url_to_parse = "http://" + url_to_parse

        try:
            clean_for_parse = url_to_parse.replace("[", "%5B").replace("]", "%5D")
            parsed = urlparse(clean_for_parse)
            self.scheme = parsed.scheme or "http"
            self.host = parsed.hostname or ""
            self.port = parsed.port or (44445 if "superproxy.io" in self.host else 80)
            self.username = (parsed.username or "").replace("%5B", "[").replace("%5D", "]")
            self.password = (parsed.password or "").replace("%5B", "[").replace("%5D", "]")
        except Exception:
            m = re.search(r"^(?P<scheme>[a-zA-Z0-9]+)://(?:(?P<user>[^:]+)(?::(?P<pass>[^@]*))?@)?(?P<host>[^:]+)(?::(?P<port>\d+))?", url_to_parse)
            if m:
                self.scheme = m.group("scheme") or "http"
                self.username = m.group("user") or ""
                self.password = m.group("pass") or ""
                self.host = m.group("host") or ""
                self.port = int(m.group("port")) if m.group("port") else (44445 if "superproxy.io" in self.host else 80)

        # 1. Deteksi Bright Data
        if "superproxy.io" in self.host or "brd-customer" in self.username or "lum-customer" in self.username:
            self.is_brightdata = True
            m_cust = re.search(r"(?:brd|lum)-customer-([^-_]+)", self.username)
            if m_cust:
                self.customer = m_cust.group(1)
            m_zone = re.search(r"-zone-([^-:]+)", self.username)
            if m_zone:
                self.zone = m_zone.group(1)
            m_country = re.search(r"-country-([a-zA-Z]{2})", self.username)
            if m_country:
                self.current_country = m_country.group(1).upper()

        # 2. Deteksi HypeProxy
        elif "hypeproxy.site" in self.host or "207.241.173.98" in self.host:
            self.is_hypeproxy = True
            # Ekstraksi ID proxy dari pola user{ID} (contoh: user41 -> 41)
            m_uid = re.search(r"^user(\d+)$", self.username, re.IGNORECASE)
            if m_uid:
                try:
                    self.proxy_id = int(m_uid.group(1))
                except Exception:
                    self.proxy_id = None
            else:
                # Fallback: jika username angka murni atau ada pola id di query
                m_num = re.search(r"(\d+)", self.username)
                if m_num:
                    try:
                        self.proxy_id = int(m_num.group(1))
                    except Exception:
                        self.proxy_id = None

    def format_for_country(
        self,
        country_code: Optional[str] = None,
        session_id: Optional[str] = None,
        use_default_if_none: bool = True,
    ) -> str:
        """
        Menghasilkan URL proxy yang ditargetkan ke negara tertentu.
        Jika ini adalah proxy Bright Data, parameter `-country-xx` akan disesuaikan.
        Jika ini adalah proxy HypeProxy, region dapat disesuaikan melalui API HypeProxy.
        """
        if not self.is_brightdata:
            return self.raw_url

        new_username = self.username

        if not country_code or str(country_code).upper() in ("RANDOM", "ALL", "AUTO"):
            country_code = random.choice(list(BRIGHTDATA_SUPPORTED_COUNTRIES))

        target_cc = str(country_code).upper().strip()
        if target_cc not in BRIGHTDATA_SUPPORTED_COUNTRIES:
            logger.debug("Negara %s tidak tersedia di pool ISP, fallback ke acak", target_cc)
            target_cc = random.choice(list(BRIGHTDATA_SUPPORTED_COUNTRIES))
        target_cc = target_cc.lower()

        if "-country-" in new_username:
            new_username = re.sub(r"-country-[a-zA-Z0-9]+", f"-country-{target_cc}", new_username)
        else:
            if "-zone-" in new_username:
                new_username = re.sub(r"(-zone-[^-:]+)", rf"\1-country-{target_cc}", new_username)
            else:
                new_username += f"-country-{target_cc}"

        if not session_id:
            session_id = f"sess_{secrets.token_hex(4)}"

        clean_session = re.sub(r"[^a-zA-Z0-9_-]", "", str(session_id))
        if "-session-" in new_username:
            new_username = re.sub(r"-session-[a-zA-Z0-9_-]+", f"-session-{clean_session}", new_username)
        else:
            new_username += f"-session-{clean_session}"

        auth_part = f"{new_username}:{self.password}@" if self.password else f"{new_username}@"
        return f"{self.scheme}://{auth_part}{self.host}:{self.port}"

    def get_base_url(self) -> str:
        """Mengembalikan URL proxy murni tanpa flag -country- atau -session- (fallback zone)."""
        if not self.is_brightdata:
            return self.raw_url
        clean_user = re.sub(r"-country-[a-zA-Z0-9]+", "", self.username)
        clean_user = re.sub(r"-session-[a-zA-Z0-9_-]+", "", clean_user)
        auth_part = f"{clean_user}:{self.password}@" if self.password else f"{clean_user}@"
        return f"{self.scheme}://{auth_part}{self.host}:{self.port}"

    def get_masked_url(self) -> str:
        """Mengembalikan URL proxy yang kredensialnya disensor untuk logging aman."""
        if not self.raw_url:
            return "None"
        clean = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", self.raw_url)
        return clean


class HypeProxyClient:
    """
    Klien Resmi HypeProxy API untuk Bot Toodat / Quarterfull.
    Menangani seluruh 16 Endpoint API HypeProxy:
    - Autentikasi via header `X-API-Key: 93a8c23e03fe412d1c701b4b014688fc`
    - Proxy Host default: `proxy.hypeproxy.site` (207.241.173.98)
    - Rotasi IP instan on-demand (`POST /api/proxies/:id/rotate`) saat proxy gagal/selesai digunakan.
    - Manajemen region/negara dinamis (`PATCH /api/proxies/:id/region`).
    - Monitoring profil, saldo, daftar order, dan perpanjangan masa aktif proxy.
    """

    API_KEY: str = "93a8c23e03fe412d1c701b4b014688fc"
    BASE_URL: str = "https://hypeproxy.site"
    PROXY_HOST: str = "proxy.hypeproxy.site"
    DEFAULT_TIMEOUT: float = 12.0
    _cached_user_id: Optional[str] = None
    _last_rotate_time: Dict[Union[int, str], float] = {}

    @classmethod
    def get_headers(cls) -> Dict[str, str]:
        return {
            "X-API-Key": cls.API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "ToodatBot/3.0 (HypeProxy Integration)",
        }

    # =========================================================================
    # 1. PROFILE & BILLING
    # =========================================================================
    @classmethod
    def get_profile(cls) -> Dict[str, Any]:
        """[Endpoint 1/16] GET /api/user/profile — Mengambil informasi profil pengguna dan saldo."""
        url = f"{cls.BASE_URL}/api/user/profile"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    uid = data.get("user", {}).get("id")
                    if uid:
                        cls._cached_user_id = uid
                    return data
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def get_transactions(cls) -> Dict[str, Any]:
        """[Endpoint 2/16] GET /api/billing/transactions — Menampilkan riwayat transaksi pembayaran."""
        url = f"{cls.BASE_URL}/api/billing/transactions"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"ok": False, "status_code": resp.status_code, "data": []}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # =========================================================================
    # 2. PROXY MANAGEMENT
    # =========================================================================
    @classmethod
    def get_proxies(cls, user_only: bool = True) -> List[Dict[str, Any]]:
        """
        [Endpoint 3/16] GET /api/proxies — Menampilkan daftar proxy yang dimiliki.
        Jika `user_only`=True, hanya memfilter proxy milik user aktif (berdasarkan profile.userId).
        """
        url = f"{cls.BASE_URL}/api/proxies"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code != 200:
                    logger.error(f"[HypeProxy] Gagal ambil daftar proxy: HTTP {resp.status_code}")
                    return []

                payload = resp.json()
                all_proxies = payload.get("proxies", []) if isinstance(payload, dict) else payload

                if not user_only:
                    return all_proxies

                # Pastikan user_id tersedia
                if not cls._cached_user_id:
                    cls.get_profile()

                target_uid = cls._cached_user_id
                if target_uid:
                    user_proxies = [p for p in all_proxies if p.get("userId") == target_uid]
                    if user_proxies:
                        return user_proxies

                # Jika filter user kosong, kembalikan semua proxy aktif yang connected
                return [p for p in all_proxies if p.get("status") == "CONNECTED" or p.get("port")]
        except Exception as exc:
            logger.error(f"[HypeProxy] Exception get_proxies: {exc}")
            return []

    @classmethod
    def get_proxy(cls, proxy_id: Union[int, str]) -> Dict[str, Any]:
        """[Endpoint 4/16] GET /api/proxies/:id — Mengambil detail proxy berdasarkan ID."""
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"ok": False, "error": f"HTTP {resp.status_code}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def rotate_proxy(cls, proxy_id: Union[int, str]) -> Dict[str, Any]:
        """
        [Endpoint 5/16] POST /api/proxies/:id/rotate — Melakukan rotasi IP proxy secara instan.
        Dilengkapi cooldown 60 detik per slot agar tidak membebani server dan mencegah pemblokiran.
        """
        now = time.time()
        last = cls._last_rotate_time.get(proxy_id, 0)
        if now - last < 60:
            remaining = int(60 - (now - last))
            logger.debug(f"[HypeProxy] Slot #{proxy_id} masih dalam cooldown rotasi ({remaining}s tersisa). Lewati.")
            return {"ok": True, "cooldown": True, "remaining": remaining}

        cls._last_rotate_time[proxy_id] = now
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/rotate"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers())
                try:
                    data = resp.json()
                except Exception:
                    data = {"text": resp.text}
                data["status_code"] = resp.status_code
                data["success"] = resp.status_code in (200, 201)
                return data
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @classmethod
    def start_proxy(cls, proxy_id: Union[int, str]) -> Dict[str, Any]:
        """
        POST /api/proxies/:id/start — Menghidupkan kembali proxy yang statusnya ERROR atau Berhenti.
        Sama persis seperti menekan tombol 'Mulai' di web dashboard HypeProxy.
        """
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/start"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except Exception:
                        return {"ok": True, "message": "Slot started"}
                return {"ok": False, "status_code": resp.status_code, "text": resp.text}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def stop_proxy(cls, proxy_id: Union[int, str]) -> Dict[str, Any]:
        """
        POST /api/proxies/:id/stop — Menghentikan sementara slot proxy.
        Sama persis seperti menekan tombol 'Berhenti' di web dashboard HypeProxy.
        """
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/stop"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except Exception:
                        return {"ok": True, "message": "Slot stopped"}
                return {"ok": False, "status_code": resp.status_code, "text": resp.text}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def auto_recover_proxies(cls) -> Dict[str, Any]:
        """
        Memeriksa seluruh proxy milik user. Jika ditemukan status ERROR atau STOPPED,
        secara otomatis memanggil start_proxy(), memperbaiki region jika exhausted, dan rotasi IP.
        """
        proxies = cls.get_proxies(user_only=True)
        results = {}
        for p in proxies:
            pid = p.get("id")
            status = str(p.get("status", "")).upper()
            if status in ("ERROR", "STOPPED", "STOP") and pid:
                # 1. Hidupkan slot
                start_res = cls.start_proxy(pid)
                # 2. Jika IP negara habis (IPs Exhausted), alihkan ke AUTO
                if "exhausted" in str(p.get("lastError", "")).lower():
                    cls.set_proxy_region(pid, "AUTO")
                # 3. Picu rotasi IP baru
                cls.rotate_proxy(pid)
                results[str(pid)] = {"recovered": True, "details": start_res}
                logger.info(f"[HypeProxy] Slot #{pid} berstatus {status} berhasil di-recovery otomatis (Auto-Start)!")
        return results

    @classmethod
    def extend_proxy(cls, proxy_id: Union[int, str], days: int = 7) -> Dict[str, Any]:
        """
        [Endpoint 6/16] POST /api/proxies/:id/extend — Memperpanjang masa pakai proxy.
        Body format: {"value": 7}
        """
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/extend"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers(), json={"value": days})
                try:
                    data = resp.json()
                except Exception:
                    data = {"text": resp.text}
                data["status_code"] = resp.status_code
                return data
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # =========================================================================
    # 3. REGION / LOKASI
    # =========================================================================
    @classmethod
    def get_regions(cls) -> Dict[str, Any]:
        """[Endpoint 7/16] GET /api/regions — Mengambil daftar wilayah/lokasi yang tersedia."""
        url = f"{cls.BASE_URL}/api/regions"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"ok": False, "error": f"HTTP {resp.status_code}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def check_region(cls, code: str) -> Dict[str, Any]:
        """[Endpoint 8/16] GET /api/regions/:code — Memeriksa ketersediaan wilayah tertentu."""
        target_code = code.upper().strip()
        url = f"{cls.BASE_URL}/api/regions/{target_code}"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass

        # Fallback memeriksa dari cache/daftar master regions
        reg_data = cls.get_regions()
        data = reg_data.get("data", {})
        available_codes = set(data.get("codes", []))
        return {
            "ok": target_code in available_codes,
            "code": target_code,
            "available": target_code in available_codes,
        }

    @classmethod
    def set_proxy_region(cls, proxy_id: Union[int, str], country_code: str) -> Dict[str, Any]:
        """
        [Endpoint 9/16] PATCH /api/proxies/:id/region — Mengubah lokasi/wilayah proxy.
        Dilengkapi fallback otomatis ke master PATCH /api/proxies/:id jika endpoint subpath 404.
        """
        target_country = country_code.upper().strip()
        headers = cls.get_headers()

        # Coba endpoint dokumentasi: PATCH /api/proxies/:id/region
        url_region = f"{cls.BASE_URL}/api/proxies/{proxy_id}/region"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.patch(url_region, headers=headers, json={"country": target_country})
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass

        # Fallback ke master slot update: PATCH /api/proxies/:id
        url_master = f"{cls.BASE_URL}/api/proxies/{proxy_id}"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.patch(url_master, headers=headers, json={"country": target_country})
                try:
                    return resp.json()
                except Exception:
                    return {"status_code": resp.status_code, "text": resp.text}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # =========================================================================
    # 4. PROTOCOL & AUTENTIKASI PROXY
    # =========================================================================
    @classmethod
    def set_proxy_protocol(cls, proxy_id: Union[int, str], protocol: str = "http_https") -> Dict[str, Any]:
        """
        [Endpoint 10/16] PATCH /api/proxies/:id/protocol — Mengubah protokol proxy (http_https / socks5).
        """
        proto_clean = "socks5" if "sock" in protocol.lower() else "http_https"
        headers = cls.get_headers()

        url_proto = f"{cls.BASE_URL}/api/proxies/{proxy_id}/protocol"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.patch(url_proto, headers=headers, json={"proxyType": proto_clean})
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass

        # Fallback ke master slot update: PATCH /api/proxies/:id
        url_master = f"{cls.BASE_URL}/api/proxies/{proxy_id}"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.patch(url_master, headers=headers, json={"proxyType": proto_clean})
                try:
                    return resp.json()
                except Exception:
                    return {"status_code": resp.status_code, "text": resp.text}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def set_proxy_auth(cls, proxy_id: Union[int, str], auth_data: Dict[str, Any]) -> Dict[str, Any]:
        """[Endpoint 11/16] PATCH /api/proxies/:id/auth — Mengubah metode autentikasi proxy."""
        headers = cls.get_headers()
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/auth"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.patch(url, headers=headers, json=auth_data)
                if resp.status_code == 200:
                    return resp.json()
                # Fallback master slot update
                resp_master = client.patch(f"{cls.BASE_URL}/api/proxies/{proxy_id}", headers=headers, json=auth_data)
                try:
                    return resp_master.json()
                except Exception:
                    return {"status_code": resp_master.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # =========================================================================
    # 5. POOL / ROUTER
    # =========================================================================
    @classmethod
    def get_proxy_pools(cls, proxy_id: Union[int, str]) -> Dict[str, Any]:
        """[Endpoint 12/16] GET /api/proxies/:id/pools — Mengambil daftar pool yang tersedia untuk proxy."""
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/pools"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"ok": False, "status_code": resp.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def set_proxy_pool(cls, proxy_id: Union[int, str], pool_data: Dict[str, Any]) -> Dict[str, Any]:
        """[Endpoint 13/16] PATCH /api/proxies/:id/pool — Memindahkan/mengubah pool proxy."""
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/pool"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.patch(url, headers=cls.get_headers(), json=pool_data)
                if resp.status_code == 200:
                    return resp.json()
                return {"ok": False, "status_code": resp.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # =========================================================================
    # 6. ORDER, BILLING & PRODUCTS
    # =========================================================================
    @classmethod
    def get_products(cls) -> List[Dict[str, Any]]:
        """[Endpoint 14/16] GET /api/products — Mengambil daftar produk proxy yang dapat dibeli."""
        url = f"{cls.BASE_URL}/api/products"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, list) else data.get("products", [])
                return []
        except Exception as exc:
            return []

    @classmethod
    def get_orders(cls) -> Dict[str, Any]:
        """[Endpoint 15/16] GET /api/orders — Menampilkan riwayat pesanan pengguna."""
        url = f"{cls.BASE_URL}/api/orders"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"ok": False, "data": []}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def create_order(
        cls,
        product_id: str,
        quantity: int = 1,
        country: str = "AUTO",
        method: str = "qris",
    ) -> Dict[str, Any]:
        """[Endpoint 16/16] POST /api/orders — Membuat pesanan proxy baru."""
        url = f"{cls.BASE_URL}/api/orders"
        payload = {
            "productId": product_id,
            "quantity": quantity,
            "country": country,
            "method": method,
        }
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers(), json=payload)
                try:
                    return resp.json()
                except Exception:
                    return {"status_code": resp.status_code, "text": resp.text}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # =========================================================================
    # 7. UTILITIES & SINKRONISASI
    # =========================================================================
    @classmethod
    def build_proxy_url(cls, p: Dict[str, Any]) -> Optional[str]:
        """Mengubah dict proxy dari HypeProxy API menjadi URL standar httpx."""
        user = p.get("username")
        pwd = p.get("password")
        port = p.get("port")
        if not port or not user:
            return None

        scheme = "socks5" if p.get("proxyType") == "socks5" else "http"
        auth_part = f"{user}:{pwd}@" if pwd else f"{user}@"
        return f"{scheme}://{auth_part}{cls.PROXY_HOST}:{port}"

    @classmethod
    def fetch_active_proxy_urls(cls, user_only: bool = True) -> List[str]:
        """Mengambil seluruh proxy aktif dan memformatnya menjadi daftar URL valid."""
        proxies_data = cls.get_proxies(user_only=user_only)
        urls: List[str] = []
        for p in proxies_data:
            url = cls.build_proxy_url(p)
            if url and url not in urls:
                urls.append(url)
        return urls

    @classmethod
    def sync_proxies_to_file(
        cls,
        output_file: str = "proxies.txt",
        user_only: bool = True,
        verbose: bool = False,
    ) -> List[str]:
        """
        Mengambil proxy aktif dari HypeProxy API dan menyimpannya langsung ke proxies.txt.
        Menggantikan alur lama scraping proxy gratisan.
        """
        if verbose:
            console.print("\n[bold cyan]>>> Mengambil Daftar Proxy Aktif dari HypeProxy API...[/]")

        urls = cls.fetch_active_proxy_urls(user_only=user_only)
        if not urls and user_only:
            # Jika proxy user kosong, ambil dari pool seluruh proxy aktif yang tersedia
            urls = cls.fetch_active_proxy_urls(user_only=False)

        if not urls:
            if verbose:
                console.print("[red]⚠️ Gagal mendapatkan proxy dari HypeProxy API. Periksa API Key atau saldo akun.[/]")
            return []

        # Tulis ke berkas
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("# =============================================================================\n")
                f.write("# DAFTAR PROXY BOT TOODAT / QUARTERFULL\n")
                f.write("# Diperbarui secara otomatis melalui HypeProxy API (https://hypeproxy.site)\n")
                f.write(f"# Total Proxy Aktif: {len(urls)}\n")
                f.write("# =============================================================================\n\n")
                for u in urls:
                    f.write(f"{u}\n")

            if verbose:
                console.print(f"[bold green][OK] Berhasil menyinkronkan {len(urls)} proxy HypeProxy ke '{output_file}'![/]")
        except Exception as exc:
            logger.error(f"Gagal menulis {output_file}: {exc}")

        return urls

    @classmethod
    def rotate_all_user_proxies(cls) -> Dict[str, Any]:
        """Melakukan rotasi IP serentak ke seluruh slot proxy milik user."""
        proxies = cls.get_proxies(user_only=True)
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            fut_map = {
                executor.submit(cls.rotate_proxy, p["id"]): p["id"]
                for p in proxies
                if p.get("id")
            }
            for fut in concurrent.futures.as_completed(fut_map):
                pid = fut_map[fut]
                try:
                    res = fut.result()
                    results[str(pid)] = res.get("success", True)
                except Exception as e:
                    results[str(pid)] = False
        return results


class ProxyManager:
    """Manajer Proxy Tunggal untuk seluruh Bot dengan Auto-Replenish & Auto-Rotate."""

    def __init__(
        self,
        proxy_file: str = "proxies.txt",
        auto_replenish: bool = True,
        min_replenish_threshold: int = 2,
        verbose: bool = False,
    ):
        self.proxy_file = Path(proxy_file)
        self.raw_proxies: List[str] = []
        self.parsed_proxies: List[ProxyInfo] = []
        self._current_index: int = 0
        self._lock = threading.RLock()
        self._auto_replenish: bool = auto_replenish
        self._min_replenish_threshold: int = min_replenish_threshold
        self.verbose: bool = verbose
        self.load_proxies()

    def _write_to_file(self, proxy_list: List[str]) -> None:
        """Menulis daftar proxy ke proxies.txt secara aman."""
        try:
            with open(self.proxy_file, "w", encoding="utf-8") as f:
                f.write("# =============================================================================\n")
                f.write("# DAFTAR PROXY BOT TOODAT / QUARTERFULL\n")
                f.write("# Diperbarui secara otomatis melalui HypeProxy API (https://hypeproxy.site)\n")
                f.write(f"# Total Proxy: {len(proxy_list)}\n")
                f.write("# =============================================================================\n\n")
                for px in proxy_list:
                    clean = px.strip()
                    if clean:
                        f.write(f"{clean}\n")
        except Exception as exc:
            logger.error(f"Gagal menulis file {self.proxy_file}: {exc}")

    def load_proxies(self) -> int:
        """Membaca proxies.txt dan mem-parsing seluruh entri aktif secara thread-safe."""
        with self._lock:
            self.raw_proxies.clear()
            self.parsed_proxies.clear()

            if not self.proxy_file.exists():
                return 0

            with open(self.proxy_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    if any(p in line.lower() for p in ["[replace", "<replace", "[password]", "<password>"]):
                        logger.warning(
                            f"⚠️ Baris di {self.proxy_file.name} masih berupa placeholder: '{line}'"
                        )
                        continue

                    try:
                        p_info = ProxyInfo(line)
                        if p_info.host:
                            self.raw_proxies.append(p_info.raw_url)
                            self.parsed_proxies.append(p_info)
                    except Exception as exc:
                        logger.warning(f"⚠️ Gagal membaca format proxy '{line}': {exc}")

            return len(self.parsed_proxies)

    @property
    def has_proxies(self) -> bool:
        with self._lock:
            return len(self.parsed_proxies) > 0

    @property
    def is_brightdata(self) -> bool:
        with self._lock:
            return any(p.is_brightdata for p in self.parsed_proxies)

    @property
    def is_hypeproxy(self) -> bool:
        with self._lock:
            return any(p.is_hypeproxy for p in self.parsed_proxies)

    def ensure_proxies(self, min_count: int = 2, target_count: int = 10) -> int:
        """
        Memastikan ketersediaan proxy aktif minimal `min_count`.
        Jika proxy kosong atau kurang dari `min_count` dan bukan Bright Data,
        secara otomatis menyinkronkan proxy aktif dari HypeProxy API.
        """
        with self._lock:
            if self.is_brightdata or (self.is_hypeproxy and len(self.parsed_proxies) > 0):
                if self.is_hypeproxy:
                    # Otomatis pulihkan di background jika ada slot yang berstatus ERROR atau STOPPED di dashboard
                    threading.Thread(target=HypeProxyClient.auto_recover_proxies, daemon=True).start()
                return len(self.parsed_proxies)

            if len(self.parsed_proxies) >= min_count:
                return len(self.parsed_proxies)

            if self.verbose:
                console.print(
                    f"\n[bold magenta][HYPEPROXY-SYNC] Sinkronisasi proxy aktif dari HypeProxy API...[/]"
                )
            try:
                HypeProxyClient.sync_proxies_to_file(
                    output_file=str(self.proxy_file),
                    user_only=True,
                    verbose=self.verbose,
                )
                self.load_proxies()
                if self.verbose:
                    console.print(
                        f"[bold green][HYPEPROXY-SYNC] Selesai! {len(self.parsed_proxies)} proxy HypeProxy siap digunakan.[/]\n"
                    )
                return len(self.parsed_proxies)
            except Exception as exc:
                logger.error(f"[HypeProxy-Sync] Gagal sinkronisasi proxy otomatis: {exc}")
                return len(self.parsed_proxies)

    def remove_proxy(self, proxy_url: Optional[str], reason: str = "failed") -> bool:
        """
        Menangani proxy saat gagal (failed) atau selesai digunakan (used).
        - Pada HypeProxy: slot tidak dihapus, melainkan memicu rotasi IP instan via API!
        - Pada Bright Data: gateway tidak dihapus karena bersifat dynamic session.
        - Pada Legacy Free Proxy: dihapus dari antrean file.
        """
        if not proxy_url:
            return False

        with self._lock:
            target_raw = proxy_url.strip()
            p_found: Optional[ProxyInfo] = None

            try:
                target_parsed = urlparse(target_raw if "://" in target_raw else f"http://{target_raw}")
                target_host = target_parsed.hostname
                target_port = target_parsed.port
            except Exception:
                target_host = None
                target_port = None

            for p in self.parsed_proxies:
                if p.raw_url == target_raw:
                    p_found = p
                    break
                if target_host and p.host == target_host and (target_port is None or p.port == target_port):
                    p_found = p
                    break

            if not p_found:
                return False

            masked = p_found.get_masked_url()

            # 1. Penanganan Khusus HypeProxy: AUTO-START (MULAI) & ROTASI IP SAAT GAGAL / ERROR
            if p_found.is_hypeproxy:
                if reason == "failed" and p_found.proxy_id:
                    def _recover_slot():
                        # 1. Pastikan slot menyala jika sebelumnya berstatus ERROR / Berhenti (menekan tombol 'Mulai')
                        HypeProxyClient.start_proxy(p_found.proxy_id)
                        # 2. Picu rotasi IP instan di thread terpisah
                        HypeProxyClient.rotate_proxy(p_found.proxy_id)

                    threading.Thread(target=_recover_slot, daemon=True).start()
                    logger.warning(f"[HypeProxy] Slot #{p_found.proxy_id} mengalami limit/error -> Otomatis MEMULAI ULANG (Auto-Start) & rotasi IP.")
                    if self.verbose:
                        console.print(f"[dim yellow][HYPEPROXY-RECOVER] Slot #{p_found.proxy_id} otomatis dihidupkan ulang (Auto-Start) & rotasi IP baru![/]")
                elif reason == "used":
                    logger.debug(f"[HypeProxy] Slot #{p_found.proxy_id} selesai digunakan.")
                return True

            # 2. Penanganan Bright Data
            if p_found.is_brightdata and reason == "used":
                return False

            # 3. Penanganan Free Proxy Biasa (Non-HypeProxy)
            if p_found in self.parsed_proxies:
                self.parsed_proxies.remove(p_found)
            if p_found.raw_url in self.raw_proxies:
                self.raw_proxies.remove(p_found.raw_url)

            self._write_to_file([p.raw_url for p in self.parsed_proxies])
            sisa = len(self.parsed_proxies)

            if reason == "failed":
                logger.warning(f"[-] [ProxyManager] Proxy dihapus: {masked} (Sisa: {sisa})")
            elif reason == "used":
                logger.info(f"[+] [ProxyManager] Proxy selesai dipakai: {masked} (Sisa: {sisa})")

            if self._auto_replenish and not self.is_brightdata and sisa < self._min_replenish_threshold:
                threading.Thread(target=self.ensure_proxies, args=(self._min_replenish_threshold, 10), daemon=True).start()

            return True

    def mark_failed(self, proxy_url: Optional[str], error: Optional[Any] = None) -> bool:
        """Menandai proxy yang gagal konek / error. Pada HypeProxy memicu rotasi IP instan."""
        return self.remove_proxy(proxy_url, reason="failed")

    def mark_used(self, proxy_url: Optional[str]) -> bool:
        """Menandai proxy yang selesai digunakan."""
        return self.remove_proxy(proxy_url, reason="used")

    def pop_proxy(
        self,
        country_code: Optional[str] = None,
        session_id: Optional[str] = None,
        auto_replenish: bool = True,
    ) -> Optional[str]:
        """
        Mengambil proxy untuk satu sesi kerja secara instan:
        - HypeProxy dan Bright Data berputar terus menerus (round-robin) antar 8 slot.
        - Langsung mengembalikan URL proxy tanpa latensi tambahan.
        """
        with self._lock:
            if not self.parsed_proxies and auto_replenish and not self.is_brightdata:
                self.ensure_proxies(min_count=1, target_count=10)

            if not self.parsed_proxies:
                return None

            # HypeProxy dan Bright Data berputar terus menerus (round-robin)
            if self.is_brightdata or self.is_hypeproxy:
                proxy_info = self.parsed_proxies[self._current_index % len(self.parsed_proxies)]
                self._current_index += 1

                if proxy_info.is_brightdata:
                    return proxy_info.format_for_country(country_code=country_code, session_id=session_id)

                # Untuk HypeProxy: Langsung return URL proxy tanpa beban API tambahan
                return proxy_info.raw_url

            # Legacy free proxy (single-use)
            proxy_info = self.parsed_proxies.pop(0)
            if proxy_info.raw_url in self.raw_proxies:
                self.raw_proxies.remove(proxy_info.raw_url)
            self._write_to_file([p.raw_url for p in self.parsed_proxies])

            if auto_replenish and len(self.parsed_proxies) < self._min_replenish_threshold:
                threading.Thread(target=self.ensure_proxies, daemon=True).start()

            return proxy_info.raw_url

    def get_proxy(
        self,
        country_code: Optional[str] = None,
        session_id: Optional[str] = None,
        auto_replenish: bool = True,
    ) -> Optional[str]:
        """Mengambil proxy berikutnya secara round-robin."""
        with self._lock:
            if not self.parsed_proxies and auto_replenish and not self.is_brightdata:
                self.ensure_proxies(min_count=1)

            if not self.parsed_proxies:
                return None

            proxy_info = self.parsed_proxies[self._current_index % len(self.parsed_proxies)]
            self._current_index += 1

            if proxy_info.is_brightdata:
                return proxy_info.format_for_country(country_code=country_code, session_id=session_id)

            return proxy_info.raw_url

    def get_base_fallback_proxy(self) -> Optional[str]:
        """Mengambil base proxy tanpa targeting negara spesifik."""
        with self._lock:
            if not self.parsed_proxies:
                return None
            return self.parsed_proxies[0].get_base_url()

    def get_alternate_proxy(self, failed_country: Optional[str] = None) -> Optional[str]:
        """Mengambil proxy dari slot lain secara dinamis."""
        with self._lock:
            if not self.parsed_proxies:
                return None
            # Ambil slot acak dari daftar
            p = random.choice(self.parsed_proxies)
            if p.is_brightdata:
                candidates = ["ID", "GB", "DE", "JP", "FR", "AU", "CA", "SG", "NL", "ES", "IT", "KR", "US"]
                random.shuffle(candidates)
                cc = [c for c in candidates if c != failed_country][0]
                return p.format_for_country(country_code=cc, session_id=f"alt_{secrets.token_hex(4)}")
            elif p.is_hypeproxy and p.proxy_id:
                # Picu rotasi pada slot ini agar exit IP berganti
                threading.Thread(target=HypeProxyClient.rotate_proxy, args=(p.proxy_id,), daemon=True).start()
                return p.raw_url
            return p.raw_url

    @staticmethod
    def get_client_kwargs(
        proxy: Optional[str] = None,
        base_url: Optional[str] = "https://api.quarterfull.io",
        timeout: float = 15.0,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Membuat dictionary argumen standar untuk httpx.Client atau httpx.AsyncClient.
        PENTING: Jika `proxy` aktif, `http2` disetel False agar tunnel HTTP CONNECT stabil tanpa hang.
        Jika direct (tanpa proxy), `http2` disetel True sesuai standar bot.
        """
        kwargs: Dict[str, Any] = {
            "http2": False if proxy else True,
            "timeout": httpx.Timeout(timeout),
        }
        if base_url:
            kwargs["base_url"] = base_url
        if headers:
            kwargs["headers"] = headers
        if proxy:
            kwargs["proxy"] = proxy

        return kwargs

    def test_proxy(
        self,
        proxy_url: Optional[str] = None,
        country_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Menguji koneksi proxy ke Geo Diagnostics & Quarterfull API."""
        target_proxy = proxy_url or self.get_proxy(country_code=country_code)

        result: Dict[str, Any] = {
            "proxy": target_proxy,
            "success": False,
            "ip": None,
            "country": None,
            "region": None,
            "city": None,
            "asn": None,
            "target_api_ok": False,
            "latency_ms": 0.0,
            "error": None,
        }

        if not target_proxy:
            result["error"] = "Tidak ada proxy yang dikonfigurasi"
            return result

        start_time = time.time()
        try:
            with httpx.Client(proxy=target_proxy, http2=False, timeout=8.0) as client:
                try:
                    ip_resp = client.get("https://api.ipify.org?format=json", timeout=4.0)
                    if ip_resp.status_code == 200:
                        result["ip"] = ip_resp.json().get("ip")
                except Exception:
                    pass

                # Cek Endpoint Quarterfull API
                api_resp = client.get("https://api.quarterfull.io/api/auth/app-version", timeout=5.0)
                if api_resp.status_code == 200:
                    result["target_api_ok"] = True
                    result["success"] = True
                else:
                    result["error"] = f"Target API HTTP {api_resp.status_code}"

            latency = (time.time() - start_time) * 1000.0
            result["latency_ms"] = round(latency, 1)

        except Exception as exc:
            result["error"] = str(exc)
            result["latency_ms"] = round((time.time() - start_time) * 1000.0, 1)

        return result

    def save_proxies(self, proxy_list: List[str]) -> None:
        """Menyimpan daftar proxy ke berkas dan memuat ulang instance."""
        with self._lock:
            self._write_to_file(proxy_list)
            self.load_proxies()

    def clear_proxies(self) -> None:
        """Mengosongkan daftar proxy agar bot menggunakan Direct Connection."""
        with self._lock:
            self._write_to_file([])
            self.load_proxies()


class FreeProxyScraper:
    """
    Kelas pembungkus kompatibilitas mundur (backward-compatibility alias).
    Mengarahkan seluruh pemanggilan fungsi lama (scrape_fast, scrape_and_update)
    secara otomatis ke HypeProxyClient.
    """

    @classmethod
    def scrape_fast(
        cls,
        target_count: int = 1000,
        output_file: str = "proxies.txt",
        append: bool = False,
        verbose: bool = False,
    ) -> List[str]:
        return HypeProxyClient.sync_proxies_to_file(output_file=output_file, verbose=verbose)

    @classmethod
    def scrape_and_update(
        cls,
        target_count: int = 50,
        output_file: str = "proxies.txt",
        max_workers: int = 80,
        show_table: bool = True,
    ) -> List[str]:
        return HypeProxyClient.sync_proxies_to_file(output_file=output_file, verbose=show_table)


def is_dead_or_proxy_error(exc: Optional[Any]) -> bool:
    """Mengecek secara komprehensif apakah suatu exception diakibatkan oleh proxy mati / gagal koneksi."""
    if exc is None:
        return False
    if isinstance(exc, (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError)):
        return True
    err_str = str(exc).lower()
    dead_signals = (
        "proxy",
        "407",
        "socks",
        "connection reset",
        "connection refused",
        "actively refused",
        "tunnel",
        "502",
        "503",
        "504",
        "timed out",
        "timeout",
        "connect call failed",
        "remote host closed",
        "broken pipe",
        "no ips",
        "unreachable",
        "handshake failed",
    )
    return any(sig in err_str for sig in dead_signals)


# Instance singleton global
default_proxy_manager = ProxyManager("proxies.txt")


def get_global_proxy_manager() -> ProxyManager:
    """Mengambil instance global ProxyManager."""
    return default_proxy_manager


def test_proxy_cli() -> None:
    """Antarmuka CLI interaktif untuk manajemen, rotasi, profil, dan diagnostik proxy HypeProxy."""
    while True:
        console.print("\n[bold cyan]=== PUSAT MANAJEMEN & INTEGRASI PROXY (HYPEPROXY & BOT TOODAT) ===[/]\n")

        mgr = ProxyManager("proxies.txt")
        if mgr.has_proxies:
            p_info = mgr.parsed_proxies[0]
            if p_info.is_brightdata:
                tipe_str = "Bright Data SuperProxy (ISP)"
            elif p_info.is_hypeproxy:
                tipe_str = "HypeProxy API (Official Integration)"
            else:
                tipe_str = "Standard HTTP / SOCKS5 Proxy"

            console.print(f"Status: [bold green]{len(mgr.parsed_proxies)} Proxy Terdaftar[/] | Tipe: [bold yellow]{tipe_str}[/]")
            console.print(f"Sample: [dim]{mgr.parsed_proxies[0].get_masked_url()}[/]\n")
        console.print(
            Panel(
                "[bold green]✓ SISTEM PROXY 100% FULL AUTO-PILOT SUDAH AKTIF![/]\n"
                "[dim]Anda [bold white]TIDAK PERLU[/] melakukan rotasi IP atau ganti negara manual di menu ini.[/]\n\n"
                "• [bold cyan]Rotasi IP Otomatis:[/] Bot memutar IP sendiri tiap kali sesi selesai atau kena rate limit.\n"
                "• [bold cyan]Ganti Negara Otomatis:[/] Proxy otomatis berubah lokasi mengikuti negara akun target.\n"
                "• [bold cyan]Load Balancing 8 Slot:[/] Ke-8 proxy Anda dibagi bergiliran (Round-Robin) ke tiap thread.\n\n"
                "[italic yellow]Menu manual di bawah ini HANYA opsi diagnostik jika Anda ingin cek saldo / cek order HypeProxy.[/]",
                title="[bold green]STATUS OTOMATISASI HYPEPROXY[/]",
                border_style="green",
                padding=(0, 1),
            )
        )

        console.print("Menu Diagnostik & Manual (Opsional):")
        console.print("  [1] [bold green]One-Click Refresh & Rotasi Semua 8 Proxy[/]")
        console.print("  [2] [bold yellow]Rotasi IP Manual[/] (Semua Slot / Berdasarkan ID)")
        console.print("  [3] [bold cyan]Cek Profil & Saldo HypeProxy[/]")
        console.print("  [4] [bold white]Cek Riwayat Pesanan & Order HypeProxy[/]")
        console.print("  [5] [bold blue]Ubah Negara / Region Proxy Manual[/]")
        console.print("  [6] [bold magenta]Uji Diagnostik Seluruh Proxy di proxies.txt[/]")
        console.print("  [7] [bold white]Input / Tambah Proxy Manual ke proxies.txt[/]")
        console.print("  [8] [bold yellow]Perpanjang Masa Aktif Proxy Slot (Extend)[/]")
        console.print("  [9] [bold red]Kosongkan proxies.txt[/] (Gunakan Direct Connection)")
        console.print("  [0] Kembali ke Menu Utama\n")

        try:
            from rich.prompt import Prompt, IntPrompt
            choice = Prompt.ask("[bold green]?[/] Pilihan Anda", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], default="1")
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "0":
            break

        elif choice == "1":
            # One-Click Refresh & Rotasi Semua 8 Proxy
            console.print("\n[bold cyan]>>> Menyinkronkan daftar proxy dan merotasi IP seluruh 8 slot...[/]")
            HypeProxyClient.sync_proxies_to_file(output_file="proxies.txt", verbose=True)
            HypeProxyClient.rotate_all_user_proxies()
            default_proxy_manager.load_proxies()
            console.print("[bold green]✓ Seluruh 8 Slot Proxy HypeProxy berhasil disinkronkan & dirotasi IP baru![/]\n")

        elif choice == "2":
            # Rotasi IP Proxy
            console.print("\nPilih Mode Rotasi IP:")
            console.print("  [1] Rotasi SEMUA slot proxy aktif")
            console.print("  [2] Rotasi SATU slot proxy tertentu (input ID)")
            sub_choice = Prompt.ask("Pilihan", choices=["1", "2"], default="1")

            if sub_choice == "1":
                console.print("[yellow]Memproses rotasi IP seluruh slot proxy...[/]")
                res = HypeProxyClient.rotate_all_user_proxies()
                console.print(f"[bold green]Selesai! {len(res)} slot telah dipicu untuk rotasi IP.[/]")
            else:
                pid = Prompt.ask("Masukkan ID Proxy yang ingin dirotasi (contoh: 41)").strip()
                if pid:
                    res = HypeProxyClient.rotate_proxy(pid)
                    if res.get("success", False):
                        console.print(f"[bold green][OK] Rotasi IP untuk Slot #{pid} berhasil diinisiasi: {res.get('message', 'Sukses')}[/]")
                    else:
                        console.print(f"[bold yellow]Respon rotasi Slot #{pid}: {res}[/]")

        elif choice == "3":
            # Profil & Saldo HypeProxy
            console.print("\n[cyan]Mengambil profil pengguna HypeProxy...[/]")
            prof = HypeProxyClient.get_profile()
            if prof.get("ok"):
                u = prof.get("user", {})
                t = Table(title="Profil Akun HypeProxy")
                t.add_column("Parameter", style="bold cyan")
                t.add_column("Nilai", style="bold green")
                t.add_row("Username", u.get("username", "-"))
                t.add_row("Nama", u.get("firstname", "-"))
                t.add_row("Email", u.get("email", "-"))
                t.add_row("User ID", u.get("id", "-"))
                t.add_row("Saldo (Balance)", f"Rp {u.get('balance', 0):,}")
                t.add_row("Default Country", u.get("defaultCountry", "AUTO"))
                t.add_row("Terdaftar Sejak", str(u.get("registeredAt", "-"))[:10])
                console.print(t)
            else:
                console.print(f"[red]Gagal mengambil profil: {prof.get('error')}[/]")

        elif choice == "4":
            # Riwayat Pesanan
            console.print("\n[cyan]Mengambil riwayat order HypeProxy...[/]")
            orders_data = HypeProxyClient.get_orders()
            orders = orders_data.get("data", []) if isinstance(orders_data, dict) else []
            if orders:
                t = Table(title=f"Riwayat Pesanan HypeProxy ({len(orders)} Pesanan)")
                t.add_column("Order ID", style="dim")
                t.add_column("Produk", style="bold cyan")
                t.add_column("Tier", style="yellow")
                t.add_column("Jumlah", style="green")
                t.add_column("Total Harga", style="bold white")
                t.add_column("Status", style="bold green")
                t.add_column("Proxy IDs", style="dim")

                for o in orders:
                    p_ids = ", ".join(o.get("proxyIds", []))
                    t.add_row(
                        str(o.get("id", "-")),
                        str(o.get("productId", "-")),
                        str(o.get("tierLabel", "-")),
                        str(o.get("quantity", 1)),
                        f"Rp {o.get('totalPrice', 0):,}",
                        str(o.get("status", "-")).upper(),
                        p_ids[:30] + ("..." if len(p_ids) > 30 else ""),
                    )
                console.print(t)
            else:
                console.print("[yellow]Tidak ada data riwayat pesanan.[/]")

        elif choice == "5":
            # Ubah Negara / Region
            pid = Prompt.ask("Masukkan ID Proxy yang ingin diubah negaranya (contoh: 41)").strip()
            cc = Prompt.ask("Masukkan Kode Negara 2 huruf (contoh: ID, US, SG, JP, KR, DE)").strip().upper()
            if pid and cc:
                res = HypeProxyClient.set_proxy_region(pid, cc)
                console.print(f"[bold green]Hasil update region slot #{pid} -> {cc}:[/] {res}")

        elif choice == "6":
            # Uji Diagnostik Seluruh Proxy
            if not mgr.has_proxies:
                console.print("[yellow]File 'proxies.txt' kosong. Tidak ada proxy yang dapat diuji.[/]")
                continue

            test_count = min(len(mgr.parsed_proxies), 15)
            console.print(f"\n[cyan]Menguji konektivitas {test_count} proxy pertama ke target API...[/]")
            table = Table(title="Hasil Diagnostik Proxy")
            table.add_column("No", style="dim", width=4)
            table.add_column("Proxy URL", style="bold cyan")
            table.add_column("Exit IP", style="bold yellow")
            table.add_column("Target API", style="bold")
            table.add_column("Latency", style="yellow")
            table.add_column("Status", style="dim")

            for idx, p_item in enumerate(mgr.parsed_proxies[:test_count], start=1):
                res = mgr.test_proxy(proxy_url=p_item.raw_url)
                if res["success"]:
                    table.add_row(
                        str(idx),
                        p_item.get_masked_url(),
                        res.get("ip") or "-",
                        "[bold green]200 OK[/]",
                        f"{res['latency_ms']} ms",
                        "[green]Aktif[/]",
                    )
                else:
                    table.add_row(
                        str(idx),
                        p_item.get_masked_url(),
                        "-",
                        "[bold red]FAIL[/]",
                        f"{res['latency_ms']} ms",
                        str(res.get("error", "Error"))[:25],
                    )

            console.print(table)

        elif choice == "7":
            # Input manual
            manual_proxy = Prompt.ask("[bold green]?[/] Masukkan URL Proxy (contoh: http://ip:port atau socks5://ip:port)").strip()
            if manual_proxy:
                current_lines = [p.raw_url for p in mgr.parsed_proxies]
                if manual_proxy not in current_lines:
                    current_lines.insert(0, manual_proxy)
                    mgr.save_proxies(current_lines)
                    default_proxy_manager.load_proxies()
                    console.print(f"[bold green][OK] Proxy '{manual_proxy}' berhasil ditambahkan ke 'proxies.txt'![/]")
                else:
                    console.print("[yellow]Proxy tersebut sudah ada di proxies.txt.[/]")

        elif choice == "8":
            # Perpanjang masa aktif proxy
            pid = Prompt.ask("Masukkan ID Proxy yang ingin diperpanjang (contoh: 41)").strip()
            try:
                days = IntPrompt.ask("Berapa hari perpanjangan?", default=7)
            except Exception:
                days = 7
            if pid:
                res = HypeProxyClient.extend_proxy(pid, days=days)
                console.print(f"[bold green]Hasil perpanjangan slot #{pid} ({days} hari):[/] {res}")

        elif choice == "9":
            # Kosongkan proxies.txt
            mgr.clear_proxies()
            default_proxy_manager.load_proxies()
            console.print("[bold green][OK] 'proxies.txt' berhasil dikosongkan. Bot akan menggunakan Direct Connection.[/]")


if __name__ == "__main__":
    import sys

    if "--sync" in sys.argv or "--scrape" in sys.argv or "--free" in sys.argv:
        HypeProxyClient.sync_proxies_to_file(output_file="proxies.txt", verbose=True)
        default_proxy_manager.load_proxies()
    elif "--rotate" in sys.argv:
        print("Memutar IP seluruh proxy...")
        res = HypeProxyClient.rotate_all_user_proxies()
        print("Hasil rotasi:", res)
    elif "--profile" in sys.argv:
        prof = HypeProxyClient.get_profile()
        print("Profil HypeProxy:", json.dumps(prof, indent=2))
    elif "--clear" in sys.argv:
        default_proxy_manager.clear_proxies()
        print("[OK] proxies.txt cleared. Using Direct Connection.")
    elif "--check" in sys.argv:
        test_proxy_cli()
    else:
        test_proxy_cli()
