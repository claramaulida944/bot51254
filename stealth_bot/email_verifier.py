"""
Modul Layanan Verifikasi Email Otomatis (Temp.tf Email Verifier).
Menggunakan provider temp.tf untuk menghasilkan email organik (Gmail Dot Trick, Outlook, Hotmail)
dan mengekstrak kode OTP verifikasi 6-digit secara otomatis tanpa captcha atau biaya.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("EmailVerifier")


class TempTfVerifier:
    """Client untuk berinteraksi dengan API temp.tf guna mendapatkan email dan kode OTP."""

    BASE_URL = "https://temp.tf/api"

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://temp.tf/",
            "Accept": "application/json",
        }

    async def get_email(
        self,
        provider: str = "gmail",
        use_dot: bool = True,
        use_plus: bool = False,
    ) -> Optional[str]:
        """
        Mengambil alamat email acak dari temp.tf.
        Pilihan provider: 'gmail', 'outlook', 'hotmail', 'high.edu.pl'.
        Untuk Gmail: use_dot=True & use_plus=False menghasilkan format @gmail.com asli (sangat organik).
        """
        params = {
            "providers": provider,
            "dot": "1" if use_dot else "0",
            "plus": "1" if use_plus else "0",
        }

        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            try:
                resp = await client.get(f"{self.BASE_URL}/account", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    email = data.get("email")
                    if email and isinstance(email, str):
                        return email.strip().lower()
                logger.warning("Gagal mendapatkan email dari temp.tf: HTTP %s (%s)", resp.status_code, resp.text[:100])
            except Exception as exc:
                logger.error("Error koneksi ke temp.tf /api/account: %s", exc)

        return None

    async def poll_for_otp(
        self,
        email: str,
        timeout_sec: int = 120,
        interval_sec: int = 4,
        log_callback: Optional[Any] = None,
    ) -> Optional[str]:
        """
        Melakukan polling berkala ke temp.tf inbox untuk mencari kode OTP 6-digit.
        Khusus memfilter email resmi dari Quarterfull dan mengekstrak kode (termasuk format angka berspasi di HTML).
        Mengembalikan kode OTP jika ditemukan, atau None jika timeout.
        """
        log_fn = log_callback or (lambda msg: None)
        email = email.strip().lower()
        start_time = asyncio.get_event_loop().time()

        log_fn(f"[cyan]Menunggu email kode verifikasi masuk untuk [bold]{email}[/] (Timeout: {timeout_sec}s)...[/]")

        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            while (asyncio.get_event_loop().time() - start_time) < timeout_sec:
                try:
                    resp = await client.post(
                        f"{self.BASE_URL}/check",
                        json={"email": email, "wait": False},
                    )
                    if resp.status_code == 200:
                        res_json = resp.json()
                        messages: List[Dict[str, Any]] = res_json.get("data", []) if isinstance(res_json, dict) else []

                        # 1. Prioritaskan email resmi dari Quarterfull
                        qf_messages = [
                            m for m in messages
                            if "quarterfull" in str(m.get("from", "")).lower()
                            or "quarterfull" in str(m.get("subject", "")).lower()
                            or "quarterfull" in str(m.get("body", "")).lower()
                        ]

                        candidate_messages = qf_messages if qf_messages else messages

                        for msg in candidate_messages:
                            # Ambil seluruh konten: body (HTML), text, dan subject
                            body_html = msg.get("body", "") or ""
                            subject = msg.get("subject", "") or ""
                            text_body = msg.get("text", "") or ""
                            combined = f"{subject}\n{text_body}\n{body_html}"

                            # Quarterfull memformat OTP dalam HTML seperti: <div ...>0 6 4 6 6 3</div> atau 064663
                            match = re.search(r"(?:>|\b)((?:\d\s*){6})(?:<|\b)", combined)
                            if match:
                                raw_match = match.group(1)
                                code = re.sub(r"\s+", "", raw_match)
                                if len(code) == 6 and code.isdigit():
                                    log_fn(f"[bold green]✓ Kode OTP Berhasil Ditemukan:[/] [bold yellow]{code}[/]")
                                    return code

                    elif resp.status_code == 429:
                        log_fn("[yellow]Rate limit temp.tf, menunggu sebentar...[/]")
                except Exception as exc:
                    logger.debug("Polling temp.tf gagal: %s", exc)

                await asyncio.sleep(interval_sec)

        log_fn(f"[red]Waktu habis ({timeout_sec}s): Kode verifikasi belum diterima.[/]")
        return None
