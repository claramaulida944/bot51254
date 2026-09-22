"""
Modul Penjadwal Terdistribusi & Anti-Clustering (Stealth Scheduler).
Menjamin pendaftaran akun tidak memicu lonjakan serentak (Registration Clustering):
1. Menggunakan distribusi acak dengan jeda Poisson/Jitter (60 - 180 detik).
2. Memutar IP proxy pada setiap pendaftaran agar 1 akun = 1 IP unik.
3. Menyimpan hasil akun ke akun_stealth.txt dalam format JSON Lines.
"""

import asyncio
import json
import logging
import random
from typing import Any, Callable, Dict, List, Optional

from .config import (
    ACCOUNTS_FILE,
    MIN_SIGNUP_INTERVAL_SEC,
    MAX_SIGNUP_INTERVAL_SEC,
)
from .client import StealthApiClient
from .profile import AccountProfile, ProfileGenerator
from .proxy import StealthProxyManager, default_proxy_manager

logger = logging.getLogger("StealthScheduler")


class StealthScheduler:
    """Mengelola pembuatan akun terdistribusi dan audit keaktifan sesi."""

    def __init__(
        self,
        accounts_file=ACCOUNTS_FILE,
        proxy_manager: Optional[StealthProxyManager] = None,
        status_cb: Optional[Callable[[str], None]] = None,
    ):
        self.accounts_file = accounts_file
        self.proxy_manager = proxy_manager or default_proxy_manager
        self.status_cb = status_cb or (lambda msg: None)

    def log(self, text: str):
        self.status_cb(text)

    def load_accounts(self) -> List[Dict[str, Any]]:
        if not self.accounts_file.exists():
            return []
        accs = []
        with open(self.accounts_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        accs.append(json.loads(line))
                    except Exception:
                        pass
        return accs

    def save_account(self, acc_dict: Dict[str, Any]):
        with open(self.accounts_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(acc_dict, ensure_ascii=False) + "\n")

    async def register_spaced_accounts(
        self,
        total_count: int,
        country_code: str = "ID",
        cancel_event: Optional[asyncio.Event] = None,
    ) -> int:
        """
        Mendaftarkan `total_count` akun baru dengan jeda acak (jittered interval)
        untuk menghindari pola 'Registration Clustering'.
        """
        self.log(f"[bold cyan]Memulai pendaftaran {total_count} akun dengan jeda anti-clustering ({int(MIN_SIGNUP_INTERVAL_SEC)}-{int(MAX_SIGNUP_INTERVAL_SEC)}s)...[/]")
        success_count = 0

        for i in range(1, total_count + 1):
            if cancel_event and cancel_event.is_set():
                self.log("[yellow]Pendaftaran dihentikan oleh pengguna.[/]")
                break

            self.log(f"\n[cyan][{i}/{total_count}] Membuat profil & identitas perangkat baru ({country_code})...[/]")
            profile = ProfileGenerator.generate_profile(country_code=country_code)

            # Ambil proxy unik untuk pendaftaran ini
            proxy = self.proxy_manager.pop_proxy(country_code)
            client = StealthApiClient(
                profile=profile,
                proxy_manager=self.proxy_manager,
                current_proxy=proxy,
            )

            ok, msg = await client.perform_organic_signup()
            if ok:
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
                    "created_at": profile.birth_date,  # iso timestamp
                }
                self.save_account(acc_data)
                success_count += 1
                self.log(f"[bold green]✓ Berhasil Mendaftar: {profile.email} (Atribusi AppsFlyer Terverifikasi)[/]")
            else:
                self.log(f"[red]✗ Gagal: {msg}[/]")

            await client.close()

            # Jeda anti-clustering antar akun jika masih ada akun berikutnya
            if i < total_count:
                delay = random.uniform(MIN_SIGNUP_INTERVAL_SEC, MAX_SIGNUP_INTERVAL_SEC)
                self.log(f"[dim]Menunggu cooldown acak {int(delay)} detik sebelum akun berikutnya...[/]")
                await asyncio.sleep(delay)

        self.log(f"\n[bold green]Selesai! Berhasil membuat {success_count} akun baru berkualitas tinggi.[/]")
        return success_count
