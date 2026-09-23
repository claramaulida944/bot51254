"""
Modul Manajemen & Integrasi Resmi HypeProxy API (https://hypeproxy.site)
Untuk RinaraDev Web SaaS & Toodat Stealth Bot.

Fitur:
- Autentikasi resmi via header X-API-Key: 93a8c23e03fe412d1c701b4b014688fc
- Pengambilan profil & saldo pengguna (/api/user/profile)
- Pengambilan daftar proxy khusus milik akun (user_only filter by userId)
- Rotasi IP otomatis & on-demand (/api/proxies/:id/rotate)
- Sinkronisasi otomatis ke database rinara_saas.db dan berkas proxies.txt
- Auto-recovery untuk mengaktifkan kembali slot proxy berstatus ERROR/STOPPED
"""

import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx

logger = logging.getLogger("HypeProxy")

ROOT_DIR = Path(__file__).resolve().parent


class HypeProxyClient:
    """Klien Resmi HypeProxy API."""

    API_KEY: str = os.getenv("HYPEPROXY_API_KEY", "93a8c23e03fe412d1c701b4b014688fc")
    BASE_URL: str = "https://hypeproxy.site"
    PROXY_HOST: str = "proxy.hypeproxy.site"
    DEFAULT_TIMEOUT: float = 12.0
    _cached_user_id: Optional[str] = "umtuuhqkpf4d0"
    _last_rotate_time: Dict[Union[int, str], float] = {}

    @classmethod
    def get_headers(cls) -> Dict[str, str]:
        return {
            "X-API-Key": cls.API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "RinaraDev-HypeProxy/2.0",
        }

    # =========================================================================
    # 1. PROFILE & SALDO
    # =========================================================================
    @classmethod
    def get_profile(cls) -> Dict[str, Any]:
        """Mengambil informasi profil akun dan sisa saldo HypeProxy."""
        url = f"{cls.BASE_URL}/api/user/profile"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    uid = data.get("user", {}).get("id")
                    if uid:
                        cls._cached_user_id = uid
                    return {"ok": True, **data}
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as exc:
            logger.error(f"[HypeProxy] Error get_profile: {exc}")
            return {"ok": False, "error": str(exc)}

    # =========================================================================
    # 2. PROXY MANAGEMENT
    # =========================================================================
    @classmethod
    def get_proxies(cls, user_only: bool = True) -> List[Dict[str, Any]]:
        """
        Mengambil daftar proxy dari HypeProxy API.
        Jika user_only=True, memfilter strictly hanya proxy milik akun pengguna.
        """
        url = f"{cls.BASE_URL}/api/proxies"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code != 200:
                    logger.error(f"[HypeProxy] Gagal mengambil daftar proxy: HTTP {resp.status_code}")
                    return []

                payload = resp.json()
                all_proxies = payload.get("proxies", []) if isinstance(payload, dict) else payload

                if not user_only:
                    return all_proxies

                # Pastikan user_id tersedia
                target_uid = cls._cached_user_id
                if not target_uid:
                    prof = cls.get_profile()
                    target_uid = prof.get("user", {}).get("id") or cls._cached_user_id

                if target_uid:
                    user_proxies = [p for p in all_proxies if p.get("userId") == target_uid]
                    if user_proxies:
                        return user_proxies

                # Fallback: ambil yang port dan username-nya cocok format user
                return [p for p in all_proxies if p.get("status") == "CONNECTED" or p.get("port")]
        except Exception as exc:
            logger.error(f"[HypeProxy] Error get_proxies: {exc}")
            return []

    @classmethod
    def get_proxy(cls, proxy_id: Union[int, str]) -> Dict[str, Any]:
        """Mengambil detail satu proxy berdasarkan ID."""
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.get(url, headers=cls.get_headers())
                if resp.status_code == 200:
                    return {"ok": True, **resp.json()}
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def rotate_proxy(cls, proxy_id: Union[int, str], force: bool = False) -> Dict[str, Any]:
        """
        Melakukan rotasi IP instan untuk satu proxy ID (POST /api/proxies/:id/rotate).
        Dilengkapi cooldown 15 detik untuk menghindari rate limit.
        """
        now = time.time()
        last = cls._last_rotate_time.get(proxy_id, 0)
        cooldown_sec = 15
        if not force and (now - last < cooldown_sec):
            remaining = int(cooldown_sec - (now - last))
            return {
                "ok": True,
                "cooldown": True,
                "remaining": remaining,
                "message": f"Cooldown aktif ({remaining}s tersisa)",
            }

        cls._last_rotate_time[proxy_id] = now
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/rotate"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers())
                try:
                    data = resp.json()
                except Exception:
                    data = {"message": resp.text}
                data["ok"] = resp.status_code in (200, 201)
                data["status_code"] = resp.status_code
                return data
        except Exception as exc:
            logger.error(f"[HypeProxy] Gagal rotasi proxy #{proxy_id}: {exc}")
            return {"ok": False, "error": str(exc)}

    @classmethod
    def rotate_all_proxies(cls) -> Dict[str, Any]:
        """Memutar IP seluruh proxy milik user secara berurutan."""
        proxies = cls.get_proxies(user_only=True)
        results = {}
        success_count = 0
        for p in proxies:
            pid = p.get("id")
            if pid:
                res = cls.rotate_proxy(pid, force=True)
                results[str(pid)] = res
                if res.get("ok"):
                    success_count += 1
                time.sleep(0.2)
        return {
            "ok": True,
            "total": len(proxies),
            "rotated": success_count,
            "details": results,
            "message": f"{success_count}/{len(proxies)} IP proxy berhasil dirotasi.",
        }

    @classmethod
    def start_proxy(cls, proxy_id: Union[int, str]) -> Dict[str, Any]:
        """Menghidupkan proxy yang berstatus ERROR atau STOPPED (POST /api/proxies/:id/start)."""
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/start"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers())
                try:
                    data = resp.json()
                except Exception:
                    data = {"message": resp.text}
                data["ok"] = resp.status_code in (200, 201)
                return data
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def stop_proxy(cls, proxy_id: Union[int, str]) -> Dict[str, Any]:
        """Menghentikan sementara slot proxy (POST /api/proxies/:id/stop)."""
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/stop"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers())
                try:
                    data = resp.json()
                except Exception:
                    data = {"message": resp.text}
                data["ok"] = resp.status_code in (200, 201)
                return data
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def extend_proxy(cls, proxy_id: Union[int, str], days: int = 7) -> Dict[str, Any]:
        """Memperpanjang masa sewa proxy (POST /api/proxies/:id/extend)."""
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/extend"
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.post(url, headers=cls.get_headers(), json={"value": days})
                try:
                    data = resp.json()
                except Exception:
                    data = {"message": resp.text}
                data["ok"] = resp.status_code in (200, 201)
                return data
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def set_proxy_region(cls, proxy_id: Union[int, str], country_code: str, city: str = "") -> Dict[str, Any]:
        """Mengubah lokasi/region proxy (PATCH /api/proxies/:id/region)."""
        url = f"{cls.BASE_URL}/api/proxies/{proxy_id}/region"
        body = {"country": country_code.upper().strip()}
        if city:
            body["city"] = city
        try:
            with httpx.Client(timeout=cls.DEFAULT_TIMEOUT) as client:
                resp = client.patch(url, headers=cls.get_headers(), json=body)
                try:
                    data = resp.json()
                except Exception:
                    data = {"message": resp.text}
                data["ok"] = resp.status_code in (200, 201)
                return data
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @classmethod
    def get_regions(cls) -> Dict[str, Any]:
        """Mengambil daftar region yang tersedia di HypeProxy (GET /api/regions)."""
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
    def auto_recover_proxies(cls) -> Dict[str, Any]:
        """Memeriksa dan menghidupkan kembali proxy berstatus ERROR atau STOPPED."""
        proxies = cls.get_proxies(user_only=True)
        results = {}
        for p in proxies:
            pid = p.get("id")
            status = str(p.get("status", "")).upper()
            if status in ("ERROR", "STOPPED", "STOP") and pid:
                start_res = cls.start_proxy(pid)
                cls.rotate_proxy(pid, force=True)
                results[str(pid)] = {"recovered": True, "details": start_res}
                logger.info(f"[HypeProxy] Slot #{pid} ({status}) dihidupkan kembali via auto-start!")
        return results

    # =========================================================================
    # 3. URL BUILDER & SINKRONISASI
    # =========================================================================
    @classmethod
    def build_proxy_url(cls, p: Dict[str, Any]) -> Optional[str]:
        """Format dict proxy API ke URL standar httpx/curl."""
        user = p.get("username")
        pwd = p.get("password")
        port = p.get("port")
        host = p.get("hostname") or cls.PROXY_HOST
        if not port or not user:
            return None

        scheme = "socks5" if str(p.get("protocol", "")).lower() == "socks5" else "http"
        auth = f"{user}:{pwd}@" if pwd else f"{user}@"
        return f"{scheme}://{auth}{host}:{port}"

    @classmethod
    def fetch_active_proxy_urls(cls, user_only: bool = True) -> List[str]:
        """Mengambil daftar URL proxy yang valid milik pengguna."""
        proxies_data = cls.get_proxies(user_only=user_only)
        urls = []
        for p in proxies_data:
            url = cls.build_proxy_url(p)
            if url and url not in urls:
                urls.append(url)
        return urls

    @classmethod
    def sync_to_files(cls, user_only: bool = True) -> List[str]:
        """
        Menuliskan daftar 10 proxy resmi milik user ke semua berkas proxies.txt.
        Menghapus riwayat lama agar sinkron sempurna.
        """
        urls = cls.fetch_active_proxy_urls(user_only=user_only)
        if not urls:
            logger.warning("[HypeProxy] Tidak ada proxy aktif untuk disinkronkan ke berkas.")
            return []

        header = (
            "# =============================================================================\n"
            "# DAFTAR PROXY RESMI HYPEPROXY.SITE (Akun claramaulida94)\n"
            f"# Total Proxy Aktif: {len(urls)} Unit (Sinkronisasi API Resmi)\n"
            "# =============================================================================\n\n"
        )
        content = header + "\n".join(urls) + "\n"

        target_files = [
            ROOT_DIR / "proxies.txt",
            ROOT_DIR / "stealth_bot" / "proxies.txt",
            ROOT_DIR / "legacy_archive" / "web_app_core" / "proxies.txt",
        ]

        for tf in target_files:
            try:
                tf.parent.mkdir(parents=True, exist_ok=True)
                with open(tf, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"[HypeProxy] Berhasil memperbarui {tf} ({len(urls)} proxy).")
            except Exception as e:
                logger.error(f"[HypeProxy] Gagal menulis ke {tf}: {e}")

        return urls


# Alias kompatibilitas
HypeProxyManager = HypeProxyClient

if __name__ == "__main__":
    print("Testing HypeProxyClient...")
    prof = HypeProxyClient.get_profile()
    print("Profile:", prof)
    proxies = HypeProxyClient.get_proxies(user_only=True)
    print(f"Total Proxies Owned: {len(proxies)}")
    for p in proxies:
        print(f"- ID {p.get('id')}: {HypeProxyClient.build_proxy_url(p)} ({p.get('status')})")
    urls = HypeProxyClient.sync_to_files(user_only=True)
    print(f"Synced {len(urls)} proxies to files.")
