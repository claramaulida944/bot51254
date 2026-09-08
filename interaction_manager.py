"""
Interaction Manager - Auto Like, Auto Bookmark & Auto Followers Module
Toodat / Quarterfull Bot Suite.

Modul ini bertanggung jawab untuk melakukan automasi interaksi sosial pada platform:
1. Auto Like Novel (POST /api/v1/novels/{novel_id}/like)
2. Auto Bookmark / Add to Bookshelf (POST /api/v1/novels/{novel_id}/bookmark)
3. Auto Follower Akun Penulis / Kreator (PUT /api/v1/social/profiles/{author_id}/follow)

Semua aksi menggunakan akun terdaftar di `akun.txt` dengan otentikasi JWT Bearer token,
dukungan rotasi proxy via `proxies.txt`, dan jeda pacing natural untuk mencegah rate-limit.
"""

import asyncio
import json
import logging
import os
import random
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Konfigurasi UTF-8 console output untuk Windows
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
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from proxy_manager import ProxyManager, ProxyInfo, default_proxy_manager

console = Console(highlight=False)
logger = logging.getLogger("interaction_manager")


def load_accounts_from_file(filepath: str = "akun.txt") -> List[Dict[str, Any]]:
    """Membaca seluruh akun yang tersimpan dalam format JSON per baris."""
    accounts = []
    p = Path(filepath)
    if not p.exists():
        return accounts

    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("access_token"):
                    accounts.append(data)
            except json.JSONDecodeError:
                continue
    return accounts


def save_all_accounts_to_file(accounts: List[Dict[str, Any]], filepath: str = "akun.txt") -> None:
    """Menyimpan seluruh daftar akun ke berkas akun.txt."""
    with open(filepath, "w", encoding="utf-8") as f:
        for acc in accounts:
            f.write(json.dumps(acc, ensure_ascii=False) + "\n")


FIRST_NAMES = [
    "alice", "helen", "jacinto", "jozsef", "ina", "jennifer", "hansdetlef",
    "rebecca", "hyeonjeong", "virginia", "nichole", "jozef", "eloa", "stefan",
    "junhyeog", "laila", "robert", "sarah", "badawi", "naksh", "andres"
]


def clean_name_from_email(email: str) -> str:
    """Mengekstrak dan memformat nama manusia natural dari format email akun."""
    user_part = email.split("@")[0]
    # Hapus random hash di akhir (cth: _qxrg61, _pqwgvc18, _db8oer67)
    parts = user_part.split("_")
    if len(parts) > 1 and re.match(r"^[a-z0-9]{3,}$", parts[-1]) and any(c.isdigit() for c in parts[-1]):
        name_str = "_".join(parts[:-1])
    else:
        name_str = parts[0]

    if "_" in name_str or "." in name_str:
        tokens = re.split(r"[\._]", name_str)
        return " ".join(t.capitalize() for t in tokens if t)

    low = name_str.lower()
    for fn in sorted(FIRST_NAMES, key=len, reverse=True):
        if low.startswith(fn) and len(low) > len(fn):
            fn_part = fn.capitalize()
            ln_part = low[len(fn):].capitalize()
            return f"{fn_part} {ln_part}"

    return name_str.title()


def load_proxies_from_file(filepath: str = "proxies.txt") -> List[str]:
    """Membaca daftar proxy dari berkas."""
    proxies = []
    p = Path(filepath)
    if not p.exists():
        return proxies

    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                proxies.append(line)
    return proxies


class TargetResolver:
    """Mendeteksi dan menyelesaikan target (Novel ID, Author ID, atau URL)."""

    BASE_URL = "https://api.quarterfull.io"

    @classmethod
    def clean_target(cls, raw_input: str) -> Tuple[str, str]:
        """
        Mengekstrak hash ID dari input string/URL dengan proteksi anti-double-paste
        dan dukungan URL web (query param hashId, /novel/, dsb).
        Mengembalikan tuple: (type: 'novel' | 'author' | 'unknown', hash_id)
        """
        raw = raw_input.strip()

        # Deteksi URL Author
        author_match = re.search(r"/(?:authors|author-profiles|social/profiles)/([a-zA-Z0-9_-]{10,24})", raw)
        if author_match:
            return "author", author_match.group(1)

        # Deteksi URL Novel (termasuk /novel/, /works/, query ?hashId=)
        query_hash = re.search(r"[?&]hashId=([a-zA-Z0-9]{16})", raw)
        if query_hash:
            return "novel", query_hash.group(1)

        novel_match = re.search(r"/(?:works|novel|novels|bookstore|read)/([a-zA-Z0-9_-]{10,24})", raw)
        if novel_match:
            return "novel", novel_match.group(1)

        # Jika langsung berupa Hash ID (tangani jika user tidak sengaja paste 2x)
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", raw)
        if len(clean_id) == 32 and clean_id[:16] == clean_id[16:]:
            clean_id = clean_id[:16]
        elif len(clean_id) > 16 and not ("/" in raw):
            m16 = re.search(r"([a-zA-Z0-9]{16})", clean_id)
            if m16:
                clean_id = m16.group(1)

        return "unknown", clean_id

    @classmethod
    def get_novel_details(cls, novel_id: str, proxy: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Mengambil metadata novel untuk verifikasi target."""
        url = f"{cls.BASE_URL}/api/v1/novels/{novel_id}"
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": "okhttp/4.12.0",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
        }
        try:
            with httpx.Client(http2=False if proxy else True, proxy=proxy, timeout=10.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal mengambil detail novel: {exc}")
        return None

    @classmethod
    def get_author_details(cls, author_id: str, proxy: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Mengambil metadata author profil dan relasi sosial."""
        url = f"{cls.BASE_URL}/api/v1/author-profiles/public/{author_id}/profile"
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": "okhttp/4.12.0",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
        }
        try:
            with httpx.Client(http2=False if proxy else True, proxy=proxy, timeout=10.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal mengambil detail author: {exc}")
        return None

    @classmethod
    def get_social_relationship(cls, author_id: str, token: str, proxy: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Mengambil status relasi sosial & jumlah followers akun."""
        url = f"{cls.BASE_URL}/api/v1/social/profiles/{author_id}/relationship"
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": "okhttp/4.12.0",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "authorization": f"Bearer {token}",
        }
        try:
            with httpx.Client(http2=False if proxy else True, proxy=proxy, timeout=10.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal mengambil status relasi: {exc}")
        return None


class SocialInteractionBot:
    """Eksekutor interaksi bot untuk Like, Bookmark, dan Follow."""

    BASE_URL = "https://api.quarterfull.io"

    def __init__(self, accounts: List[Dict[str, Any]], proxies: Optional[List[str]] = None):
        self.accounts = accounts
        if proxies is not None:
            self.proxy_manager = ProxyManager()
            self.proxy_manager.parsed_proxies = [ProxyInfo(p) for p in proxies]
        else:
            self.proxy_manager = default_proxy_manager
        self.proxies = [p.raw_url for p in self.proxy_manager.parsed_proxies]
        self.proxy_index = 0

    def _get_proxy(self, country_code: str = "ID") -> Optional[str]:
        """Mengambil proxy secara rotasi dengan targeting negara jika Bright Data dan IP unik per sesi."""
        if not self.proxy_manager.has_proxies:
            return None
        sess_id = f"soc_{secrets.token_hex(4)}"
        return self.proxy_manager.get_proxy(country_code=country_code, session_id=sess_id)

    def _build_headers(self, account: Dict[str, Any]) -> Dict[str, str]:
        """Menyusun header mobile fingerprint yang konsisten."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        country = account.get("country", "ID")
        return {
            "host": "api.quarterfull.io",
            "user-agent": account.get("user_agent", "okhttp/4.12.0"),
            "accept-encoding": "gzip",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "x-timezone": "Asia/Jakarta",
            "x-local-date": today,
            "x-user-country": country,
            "x-user-raw-country": country,
            "accept-language": "id",
            "x-device-id": account.get("device_id", "d24063e7-a5ff-4831-92b2-4e28c0123498"),
            "authorization": f"Bearer {account.get('access_token', '')}",
        }

    def _send_request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        json_body: Optional[Dict[str, Any]] = None,
        account_country: str = "ID",
        timeout: float = 12.0,
    ) -> httpx.Response:
        """Mengirim HTTP request melalui proxy yang sesuai dengan auto-fallback ke proxy negara lain jika bermasalah."""
        proxy = self._get_proxy(account_country)
        last_exc: Optional[Exception] = None

        try:
            with httpx.Client(http2=False if proxy else True, proxy=proxy, timeout=timeout) as client:
                if method.upper() == "POST":
                    resp = client.post(url, headers=headers, json=json_body)
                elif method.upper() == "PUT":
                    resp = client.put(url, headers=headers, json=json_body)
                elif method.upper() == "PATCH":
                    resp = client.patch(url, headers=headers, json=json_body)
                else:
                    resp = client.get(url, headers=headers)

                # Jika proxy mengembalikan respons HTTP 400 No IPs
                if resp.status_code == 400 and ("no ips" in resp.text.lower() or "selected country" in resp.text.lower()):
                    logger.warning("[Proxy 400] IP negara %s tidak tersedia di proxy. Mencoba proxy negara lain...", account_country)
                    for alt_cc in ["US", "ID", "GB", "DE", "JP", "FR"]:
                        if alt_cc.upper() == account_country.upper():
                            continue
                        alt_proxy = self._get_proxy(alt_cc)
                        try:
                            with httpx.Client(http2=False, proxy=alt_proxy, timeout=timeout) as alt_client:
                                if method.upper() == "POST":
                                    alt_resp = alt_client.post(url, headers=headers, json=json_body)
                                elif method.upper() == "PUT":
                                    alt_resp = alt_client.put(url, headers=headers, json=json_body)
                                elif method.upper() == "PATCH":
                                    alt_resp = alt_client.patch(url, headers=headers, json=json_body)
                                else:
                                    alt_resp = alt_client.get(url, headers=headers)
                                if alt_resp.status_code != 400 or "no ips" not in alt_resp.text.lower():
                                    return alt_resp
                        except Exception:
                            continue
                return resp
        except Exception as exc:
            last_exc = exc
            err_msg = str(exc)
            # Jika terjadi ProxyError 400 No IPs atau kegagalan proxy lainnya, rotasi ke negara lain
            if "no ips" in err_msg.lower() or "400" in err_msg or "proxy" in err_msg.lower():
                logger.warning(
                    "[Proxy Warning] Koneksi proxy negara %s gagal (%s). Mencoba ulang dengan proxy negara lain...",
                    account_country,
                    err_msg.strip(),
                )
                for alt_cc in ["US", "ID", "GB", "DE", "JP", "FR"]:
                    if alt_cc.upper() == account_country.upper():
                        continue
                    alt_proxy = self._get_proxy(alt_cc)
                    try:
                        with httpx.Client(http2=False, proxy=alt_proxy, timeout=timeout) as alt_client:
                            if method.upper() == "POST":
                                return alt_client.post(url, headers=headers, json=json_body)
                            elif method.upper() == "PUT":
                                return alt_client.put(url, headers=headers, json=json_body)
                            elif method.upper() == "PATCH":
                                return alt_client.patch(url, headers=headers, json=json_body)
                            else:
                                return alt_client.get(url, headers=headers)
                    except Exception as alt_err:
                        last_exc = alt_err
                        continue

            if last_exc:
                raise last_exc
            raise exc

    def get_account_novel_status(self, novel_id: str, account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Mengambil metadata status interaksi novel (is_liked, is_saved) spesifik untuk akun ini."""
        url = f"{self.BASE_URL}/api/v1/novels/{novel_id}"
        headers = self._build_headers(account)
        country = account.get("country", "ID")
        try:
            resp = self._send_request("GET", url, headers=headers, account_country=country)
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal mengambil status novel akun: {exc}")
        return None

    def get_account_author_relationship(self, author_id: str, account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Mengambil status relasi akun terhadap profil author (is_following)."""
        url = f"{self.BASE_URL}/api/v1/social/profiles/{author_id}/relationship"
        headers = self._build_headers(account)
        country = account.get("country", "ID")
        try:
            resp = self._send_request("GET", url, headers=headers, account_country=country)
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal memeriksa status follow akun: {exc}")
        return None

    def like_novel_single(self, novel_id: str, account: Dict[str, Any], check_first: bool = True) -> Dict[str, Any]:
        """
        Menyukai novel via satu akun.
        Jika check_first=True, memeriksa terlebih dahulu:
        - Jika akun sudah Like (is_liked=True), langsung SKIP tanpa melakukan toggle ulang.
        """
        headers = self._build_headers(account)
        country = account.get("country", "ID")

        if check_first:
            status_data = self.get_account_novel_status(novel_id, account)
            if status_data and status_data.get("is_liked") is True:
                return {
                    "status": "skipped",
                    "message": "Sudah Disukai (Liked) sebelumnya",
                    "is_liked": True,
                    "code": 200,
                }

        url = f"{self.BASE_URL}/api/v1/novels/{novel_id}/like"
        try:
            resp = self._send_request("POST", url, headers=headers, account_country=country)
            if resp.status_code == 200:
                data = resp.json()
                is_liked = data.get("is_liked", True)
                if not is_liked:
                    time.sleep(0.4)
                    resp2 = self._send_request("POST", url, headers=headers, account_country=country)
                    if resp2.status_code == 200:
                        data = resp2.json()

                return {"status": "success", "is_liked": data.get("is_liked", True), "code": 200}
            elif resp.status_code == 401:
                return {"status": "error", "message": "Token expired / tidak valid", "code": 401}
            elif resp.status_code == 429:
                return {"status": "error", "message": "Rate limited (HTTP 429)", "code": 429}
            else:
                return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:100]}", "code": resp.status_code}
        except Exception as exc:
            return {"status": "error", "message": str(exc), "code": 500}

    def bookmark_novel_single(self, novel_id: str, account: Dict[str, Any], check_first: bool = True) -> Dict[str, Any]:
        """
        Menambahkan novel ke rak / bookmark via satu akun.
        Jika check_first=True, memeriksa terlebih dahulu:
        - Jika akun sudah menyimpan (is_saved=True), langsung SKIP tanpa melakukan toggle ulang.
        """
        headers = self._build_headers(account)
        country = account.get("country", "ID")

        if check_first:
            status_data = self.get_account_novel_status(novel_id, account)
            if status_data and status_data.get("is_saved") is True:
                return {
                    "status": "skipped",
                    "message": "Sudah Disimpan / Bookmark sebelumnya",
                    "is_saved": True,
                    "code": 200,
                }

        url = f"{self.BASE_URL}/api/v1/novels/{novel_id}/bookmark"
        try:
            resp = self._send_request("POST", url, headers=headers, account_country=country)
            if resp.status_code == 200:
                data = resp.json()
                is_saved = data.get("is_saved", True)
                if not is_saved:
                    time.sleep(0.4)
                    resp2 = self._send_request("POST", url, headers=headers, account_country=country)
                    if resp2.status_code == 200:
                        data = resp2.json()

                return {"status": "success", "is_saved": data.get("is_saved", True), "code": 200}
            elif resp.status_code == 401:
                return {"status": "error", "message": "Token expired / tidak valid", "code": 401}
            elif resp.status_code == 429:
                return {"status": "error", "message": "Rate limited (HTTP 429)", "code": 429}
            else:
                return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:100]}", "code": resp.status_code}
        except Exception as exc:
            return {"status": "error", "message": str(exc), "code": 500}

    def follow_author_single(self, author_id: str, account: Dict[str, Any], check_first: bool = True) -> Dict[str, Any]:
        """
        Mengikuti (Follow) profil kreator/penulis via satu akun.
        Jika check_first=True, memeriksa terlebih dahulu:
        - Jika akun sudah follow (is_following=True), langsung SKIP tanpa melakukan toggle ulang.
        """
        headers = self._build_headers(account)
        country = account.get("country", "ID")

        if check_first:
            rel = self.get_account_author_relationship(author_id, account)
            if rel and rel.get("is_following") is True:
                return {
                    "status": "skipped",
                    "message": "Sudah Diikuti (Follow) sebelumnya",
                    "is_following": True,
                    "followers_count": rel.get("followers_count", 0),
                    "code": 200,
                }

        url = f"{self.BASE_URL}/api/v1/social/profiles/{author_id}/follow"
        try:
            resp = self._send_request("PUT", url, headers=headers, account_country=country)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "is_following": data.get("is_following", True),
                    "followers_count": data.get("followers_count", 0),
                    "code": 200,
                }
            elif resp.status_code == 404:
                return {"status": "error", "message": "Profil sosial tidak ditemukan", "code": 404}
            elif resp.status_code == 401:
                return {"status": "error", "message": "Token expired / tidak valid", "code": 401}
            elif resp.status_code == 429:
                return {"status": "error", "message": "Rate limited (HTTP 429)", "code": 429}
            else:
                return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:100]}", "code": resp.status_code}
        except Exception as exc:
            return {"status": "error", "message": str(exc), "code": 500}

    def update_profile_nickname(self, account: Dict[str, Any], new_nickname: str) -> Dict[str, Any]:
        """
        Mengubah nama pengguna (nickname) akun di API server.
        Endpoint: PATCH /api/auth/profile
        """
        url = f"{self.BASE_URL}/api/auth/profile"
        headers = self._build_headers(account)
        headers["content-type"] = "application/json"
        country = account.get("country", "ID")

        try:
            resp = self._send_request("PATCH", url, headers=headers, json_body={"nickname": new_nickname}, account_country=country)
            if resp.status_code == 200:
                data = resp.json()
                return {"status": "success", "nickname": data.get("nickname", new_nickname), "code": 200}
            elif resp.status_code == 401:
                return {"status": "error", "message": "Token expired / tidak valid", "code": 401}
            elif resp.status_code == 429:
                return {"status": "error", "message": "Rate limited (HTTP 429)", "code": 429}
            else:
                return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:100]}", "code": resp.status_code}
        except Exception as exc:
            return {"status": "error", "message": str(exc), "code": 500}

    def run_mass_interaction(
        self,
        action_type: str,
        target_id: str,
        target_title: str,
        count: int,
        pacing_delay: float = 1.5,
    ) -> Dict[str, int]:
        """
        Menjalankan aksi interaksi massal dengan antarmuka terminal yang rapi.
        action_type: 'like' | 'bookmark' | 'follow'
        """
        selected_accounts = self.accounts[:count]
        total = len(selected_accounts)
        success_count = 0
        skip_count = 0
        fail_count = 0

        action_names = {
            "like": ("Auto Like Novel", "[bold red]♥ LIKE[/]"),
            "bookmark": ("Auto Bookmark Novel", "[bold yellow]★ BOOKMARK[/]"),
            "follow": ("Auto Followers Akun", "[bold green]+ FOLLOW[/]"),
        }
        title, badge = action_names.get(action_type, ("Interaksi Sosial", action_type.upper()))

        console.print(f"\n[bold cyan]=== Memulai {title} ===[/]")
        console.print(f"Target: [bold yellow]{target_title}[/] (ID: [cyan]{target_id}[/])")
        console.print(f"Total Akun yang Diproses: [bold white]{total}[/] Akun\n")

        table = Table(title=f"[bold green]Log Eksekusi {title}[/]", border_style="cyan")
        table.add_column("No", style="dim", width=4)
        table.add_column("Akun / Email", style="bold white")
        table.add_column("Negara", style="cyan", width=8)
        table.add_column("Aksi", justify="center", width=14)
        table.add_column("Hasil / Status", style="bold")

        for idx, acc in enumerate(selected_accounts, start=1):
            email = acc.get("email", "-")
            country = acc.get("country", "ID")

            if action_type == "like":
                res = self.like_novel_single(target_id, acc, check_first=True)
            elif action_type == "bookmark":
                res = self.bookmark_novel_single(target_id, acc, check_first=True)
            elif action_type == "follow":
                res = self.follow_author_single(target_id, acc, check_first=True)
            else:
                res = {"status": "error", "message": "Aksi tidak dikenal"}

            # Jika gagal karena 400 No IPs in selected country, coba lagi pake proxy negara lain
            if res.get("status") not in ("success", "skipped"):
                err_msg = str(res.get("message", ""))
                if "No IPs" in err_msg or "400" in err_msg:
                    for alt_cc in ["US", "ID", "GB", "DE", "JP", "FR"]:
                        if alt_cc.upper() == country.upper():
                            continue
                        alt_acc = dict(acc)
                        alt_acc["country"] = alt_cc
                        if action_type == "like":
                            retry_res = self.like_novel_single(target_id, alt_acc, check_first=True)
                        elif action_type == "bookmark":
                            retry_res = self.bookmark_novel_single(target_id, alt_acc, check_first=True)
                        elif action_type == "follow":
                            retry_res = self.follow_author_single(target_id, alt_acc, check_first=True)
                        else:
                            retry_res = {"status": "error"}

                        if retry_res.get("status") in ("success", "skipped"):
                            res = retry_res
                            break

            if res.get("status") == "success":
                success_count += 1
                if action_type == "like":
                    status_text = "[bold green][OK] Berhasil Disukai (Liked)[/]"
                elif action_type == "bookmark":
                    status_text = "[bold green][OK] Tersimpan ke Rak (Saved)[/]"
                elif action_type == "follow":
                    f_count = res.get("followers_count", "")
                    count_info = f" (Total: {f_count})" if f_count != "" else ""
                    status_text = f"[bold green][OK] Berhasil Follow{count_info}[/]"
                else:
                    status_text = "[bold green][OK] Sukses[/]"
            elif res.get("status") == "skipped":
                skip_count += 1
                status_text = f"[bold yellow][SKIP] {res.get('message')}[/]"
            else:
                fail_count += 1
                status_text = f"[bold red][GAGAL] {res.get('message')}[/]"

            table.add_row(str(idx), email, country, badge, status_text)
            console.print(f"  [{idx}/{total}] {email} -> {status_text}")

            # Jeda pacing untuk keamanan anti-ban & rate-limit
            if idx < total:
                time.sleep(0.4 if self.proxies else pacing_delay)

        console.print()
        console.print(
            Panel(
                f"[bold green]Eksekusi Selesai![/]\n"
                f"• Berhasil Baru: [bold green]{success_count}[/] akun\n"
                f"• Dilewati (Sudah Aktif): [bold yellow]{skip_count}[/] akun\n"
                f"• Gagal: [bold red]{fail_count}[/] akun\n"
                f"• Total Akun Diproses: [bold cyan]{total}[/] akun\n"
                f"• Target ID: [cyan]{target_id}[/] ([yellow]{target_title}[/])",
                title=f"[bold cyan]Ringkasan {title}[/]",
                border_style="green",
            )
        )

        return {"success": success_count, "skipped": skip_count, "failed": fail_count, "total": total}


def prompt_user_quantity(available_count: int, action_label: str) -> int:
    """Meminta input jumlah akun yang diinginkan dengan validasi batas akun."""
    console.print(f"[dim]Total akun terdaftar yang tersedia saat ini: [bold yellow]{available_count}[/] akun.[/]")
    while True:
        try:
            val = IntPrompt.ask(
                f"[bold green]?[/] Berapa jumlah {action_label} yang ingin dijalankan? [1 - {available_count}]",
                default=available_count,
            )
            if 1 <= val <= available_count:
                return val
            console.print(f"[bold red]Nilai harus berada di antara 1 dan {available_count}![/]")
        except (ValueError, TypeError):
            console.print("[red]Harap masukkan angka bulat yang valid.[/]")


def run_auto_like_cli(preset_novel_id: Optional[str] = None) -> None:
    """Alur interaktif untuk fitur Auto Like Novel."""
    accounts = load_accounts_from_file("akun.txt")
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun.txt'. Harap buat akun terlebih dahulu lewat menu Auto Signup.[/]")
        return

    proxies = load_proxies_from_file("proxies.txt")
    bot = SocialInteractionBot(accounts, proxies)

    console.print("\n[bold cyan]>>> Modul Auto Like Novel (Mass Like via Akun Terdaftar)[/]\n")

    if not preset_novel_id:
        target_raw = Prompt.ask(
            "[bold green]?[/] Masukkan URL Novel atau Novel ID target (contoh: Py7LDdwpEQ8e1YKX)",
            default="Py7LDdwpEQ8e1YKX",
        ).strip()
    else:
        target_raw = preset_novel_id

    _, novel_id = TargetResolver.clean_target(target_raw)

    # Verifikasi novel
    with console.status("[bold cyan]Memeriksa data novel target di API Quarterfull...[/]"):
        novel_data = TargetResolver.get_novel_details(novel_id)

    novel_title = novel_data.get("title", f"Novel-{novel_id}") if novel_data else f"Novel ID: {novel_id}"
    author_info = novel_data.get("author", {}) if novel_data else {}
    author_name = author_info.get("pen_name", "Unknown")

    console.print(f"[bold green]Novel Ditemukan:[/] [bold yellow]{novel_title}[/] oleh [cyan]{author_name}[/] (ID: {novel_id})\n")

    count = prompt_user_quantity(len(accounts), "Like")
    bot.run_mass_interaction("like", novel_id, novel_title, count)


def run_auto_bookmark_cli(preset_novel_id: Optional[str] = None) -> None:
    """Alur interaktif untuk fitur Auto Bookmark Novel."""
    accounts = load_accounts_from_file("akun.txt")
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun.txt'. Harap buat akun terlebih dahulu lewat menu Auto Signup.[/]")
        return

    proxies = load_proxies_from_file("proxies.txt")
    bot = SocialInteractionBot(accounts, proxies)

    console.print("\n[bold cyan]>>> Modul Auto Bookmark Novel (Simpan ke Rak Buku / Library)[/]\n")

    if not preset_novel_id:
        target_raw = Prompt.ask(
            "[bold green]?[/] Masukkan URL Novel atau Novel ID target (contoh: Py7LDdwpEQ8e1YKX)",
            default="Py7LDdwpEQ8e1YKX",
        ).strip()
    else:
        target_raw = preset_novel_id

    _, novel_id = TargetResolver.clean_target(target_raw)

    with console.status("[bold cyan]Memeriksa data novel target di API Quarterfull...[/]"):
        novel_data = TargetResolver.get_novel_details(novel_id)

    novel_title = novel_data.get("title", f"Novel-{novel_id}") if novel_data else f"Novel ID: {novel_id}"
    author_info = novel_data.get("author", {}) if novel_data else {}
    author_name = author_info.get("pen_name", "Unknown")

    console.print(f"[bold green]Novel Ditemukan:[/] [bold yellow]{novel_title}[/] oleh [cyan]{author_name}[/] (ID: {novel_id})\n")

    count = prompt_user_quantity(len(accounts), "Bookmark")
    bot.run_mass_interaction("bookmark", novel_id, novel_title, count)


def run_auto_followers_cli(preset_author_id: Optional[str] = None) -> None:
    """Alur interaktif untuk fitur Auto Followers Akun Penulis/Kreator."""
    accounts = load_accounts_from_file("akun.txt")
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun.txt'. Harap buat akun terlebih dahulu lewat menu Auto Signup.[/]")
        return

    proxies = load_proxies_from_file("proxies.txt")
    bot = SocialInteractionBot(accounts, proxies)

    console.print("\n[bold cyan]>>> Modul Auto Followers Akun (Mass Follow Penulis / Kreator)[/]\n")

    if not preset_author_id:
        target_raw = Prompt.ask(
            "[bold green]?[/] Masukkan URL Penulis, Author ID (contoh: Yxk8mep482eMyJNj), atau URL/ID Novel",
            default="Yxk8mep482eMyJNj",
        ).strip()
    else:
        target_raw = preset_author_id

    target_type, extracted_id = TargetResolver.clean_target(target_raw)
    author_id = extracted_id
    author_name = "Penulis"

    with console.status("[bold cyan]Memeriksa target profil penulis di API Quarterfull...[/]"):
        # Jika pengguna memasukkan novel ID, kita ambil penulis dari novel tersebut!
        novel_check = TargetResolver.get_novel_details(extracted_id)
        if novel_check and novel_check.get("author"):
            author_info = novel_check["author"]
            author_id = author_info.get("hash_id", extracted_id)
            author_name = author_info.get("pen_name", "Author")
            console.print(f"[dim]Mendeteksi Penulis dari Novel '{novel_check.get('title')}': [bold yellow]{author_name}[/] ({author_id})[/]")
        else:
            # Cek langsung profil author
            author_data = TargetResolver.get_author_details(author_id)
            if author_data and author_data.get("author"):
                author_name = author_data["author"].get("pen_name", "Penulis")

        # Cek follower count awal
        rel_info = TargetResolver.get_social_relationship(author_id, accounts[0]["access_token"])
        initial_followers = rel_info.get("followers_count", "N/A") if rel_info else "N/A"

    console.print(f"[bold green]Profil Penulis:[/] [bold yellow]{author_name}[/] (Author ID: [cyan]{author_id}[/])")
    console.print(f"Followers Saat Ini: [bold cyan]{initial_followers}[/] Followers\n")

    count = prompt_user_quantity(len(accounts), "Followers")
    bot.run_mass_interaction("follow", author_id, author_name, count)


def run_sync_nicknames_cli(preset_count: Optional[int] = None) -> None:
    """
    Alur interaktif untuk mengubah nama pengguna (nickname) semua akun terdaftar
    di server API Quarterfull agar sesuai dengan nama akun asli di akun.txt.
    """
    accounts = load_accounts_from_file("akun.txt")
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun.txt'. Harap buat akun terlebih dahulu lewat menu Auto Signup.[/]")
        return

    proxies = load_proxies_from_file("proxies.txt")
    bot = SocialInteractionBot(accounts, proxies)

    console.print("\n[bold cyan]>>> Modul Pembaruan Nama Pengguna (Nickname Synchronizer)[/]\n")
    console.print("[dim]Fitur ini mengubah nama default bot (cth: warm-star-96) di server menjadi nama asli yang sesuai akun.txt.[/]\n")

    if preset_count is None:
        count = prompt_user_quantity(len(accounts), "Akun yang ingin diubah namanya")
    else:
        count = min(preset_count, len(accounts))

    selected_accounts = accounts[:count]
    table_preview = Table(title=f"[bold green]Daftar Target Pembaruan Nama ({count} Akun)[/]", border_style="cyan")
    table_preview.add_column("No", style="dim", width=4)
    table_preview.add_column("Email Akun", style="bold white")
    table_preview.add_column("Nama Asli Target", style="bold yellow")
    table_preview.add_column("Negara", style="cyan", width=8)

    for idx, acc in enumerate(selected_accounts, start=1):
        target_name = acc.get("nickname") or clean_name_from_email(acc.get("email", ""))
        table_preview.add_row(str(idx), acc.get("email", "-"), target_name, acc.get("country", "-"))

    console.print(table_preview)
    console.print()

    confirm = Confirm.ask(f"[bold green]?[/] Mulai perbarui nama {count} akun di server API sekarang?", default=True)
    if not confirm:
        console.print("[yellow]Operasi pembaruan nama dibatalkan.[/]")
        return

    success_count = 0
    fail_count = 0

    with console.status("[bold cyan]Memperbarui nama pengguna di API Quarterfull...[/]"):
        for idx, acc in enumerate(selected_accounts, start=1):
            target_name = acc.get("nickname") or clean_name_from_email(acc.get("email", ""))
            res = bot.update_profile_nickname(acc, target_name)
            if res["status"] == "success":
                acc["nickname"] = target_name
                success_count += 1
                console.print(f"  [{idx}/{count}] [bold green][OK][/] {acc.get('email')} -> [bold yellow]'{target_name}'[/]")
            else:
                fail_count += 1
                console.print(f"  [{idx}/{count}] [bold red][GAGAL][/] {acc.get('email')} -> {res.get('message')}")

            # Jeda pacing aman
            if idx < count:
                time.sleep(0.4 if proxies else 1.2)

    # Simpan kembali akun.txt dengan nickname yang sudah terupdate
    save_all_accounts_to_file(accounts, "akun.txt")

    console.print()
    console.print(
        Panel(
            f"[bold green]Pembaruan Nama Selesai![/]\n"
            f"• Berhasil Diubah: [bold green]{success_count}[/] akun\n"
            f"• Gagal: [bold red]{fail_count}[/] akun\n"
            f"• Berkas [yellow]akun.txt[/] telah diperbarui dengan kolom nickname.",
            title="[bold cyan]Ringkasan Pembaruan Profil Akun[/]",
            border_style="green",
        )
    )


if __name__ == "__main__":
    console.print("[bold cyan]=== Tester Interaksi Sosial Toodat ===[/]")
    console.print("[1] Auto Like Novel")
    console.print("[2] Auto Bookmark Novel")
    console.print("[3] Auto Followers Akun")
    console.print("[4] Ubah Nama Pengguna (Nickname) Sesuai akun.txt")
    pilih = Prompt.ask("Pilih fitur", choices=["1", "2", "3", "4"], default="1")
    if pilih == "1":
        run_auto_like_cli()
    elif pilih == "2":
        run_auto_bookmark_cli()
    elif pilih == "3":
        run_auto_followers_cli()
    elif pilih == "4":
        run_sync_nicknames_cli()
