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
import re
from typing import Any, Callable, Dict, List, Optional


def extract_base_username(email_addr: str) -> str:
    """Mengekstrak username dasar tanpa dot trick atau suffix plus tag."""
    user = email_addr.split("@")[0].lower()
    user = re.sub(r"\+.*", "", user)
    user = user.replace(".", "")
    return user


def format_natural_email(email_addr: str) -> str:
    """Merapikan email agar memiliki format titik yang wajar (maksimal 1 titik alami)."""
    email_addr = email_addr.strip().lower()
    if "@gmail.com" in email_addr:
        user_part = email_addr.split("@")[0].replace(".", "")
        KNOWN_SPLITS = {
            "alacatarik177": "alaca.tarik177",
            "mansurkurtaran5": "mansur.kurtaran5",
            "yavashuseyin15": "yavas.huseyin15",
            "balatcemre": "balat.cemre",
        }
        if user_part in KNOWN_SPLITS:
            return f"{KNOWN_SPLITS[user_part]}@gmail.com"

        m = re.match(r"^([a-z]+)(\d+)$", user_part)
        if m:
            letters, numbers = m.group(1), m.group(2)
            if len(letters) >= 6:
                mid = len(letters) // 2
                return f"{letters[:mid]}.{letters[mid:]}{numbers}@gmail.com"
            else:
                return f"{letters}.{numbers}@gmail.com"
        elif len(user_part) >= 6:
            mid = len(user_part) // 2
            return f"{user_part[:mid]}.{user_part[mid:]}@gmail.com"
        else:
            return f"{user_part}@gmail.com"
    return email_addr

from .config import (
    ACCOUNTS_FILE,
    MIN_SIGNUP_INTERVAL_SEC,
    MAX_SIGNUP_INTERVAL_SEC,
)
from .client import StealthApiClient
from .email_verifier import TempTfVerifier
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
        country_code: str = "RANDOM",
        verify_email: bool = True,
        email_provider: str = "RANDOM",
        cancel_event: Optional[asyncio.Event] = None,
    ) -> int:
        """
        Mendaftarkan `total_count` akun baru dengan jeda acak (jittered interval)
        untuk menghindari pola 'Registration Clustering'.
        Mendukung verifikasi email otomatis via temp.tf (Gmail / Outlook / Hotmail / Edu)
        serta pemilihan negara dan provider secara acak proporsional.
        """
        mode_str = f"Verifikasi Email Organik (Random Provider)" if email_provider.upper() in ("RANDOM", "ALL", "AUTO") else f"Verifikasi Email ({email_provider})"
        country_str = "Random Global (KR/US/JP/GB/ID)" if country_code.upper() in ("RANDOM", "ALL", "AUTO") else country_code
        self.log(
            f"[bold cyan]Memulai pendaftaran {total_count} akun [{mode_str} | {country_str}] "
            f"dengan jeda anti-clustering ({int(MIN_SIGNUP_INTERVAL_SEC)}-{int(MAX_SIGNUP_INTERVAL_SEC)}s)...[/]"
        )
        success_count = 0
        verifier = TempTfVerifier() if verify_email else None

        # Muat daftar email dan base username yang sudah pernah terdaftar untuk mencegah duplikasi & daur ulang nama
        saved_accs = self.load_accounts()
        existing_emails = {str(a.get("email", "")).strip().lower() for a in saved_accs if a.get("email")}
        existing_base_users = {extract_base_username(e) for e in existing_emails if e}

        for i in range(1, total_count + 1):
            if cancel_event and cancel_event.is_set():
                self.log("[yellow]Pendaftaran dihentikan oleh pengguna.[/]")
                break

            # 1. Resolusi Negara per Akun (Otomatis Acak Bobot Tinggi atau Spesifik)
            if country_code.upper() in ("RANDOM", "ALL", "AUTO", ""):
                candidate_countries = ["KR", "US", "JP", "GB", "ID", "DE", "CA", "AU", "FR", "SG"]
                weights = [25, 25, 15, 10, 5, 5, 5, 5, 3, 2]  # Pasar utama KR & US
                current_country = random.choices(candidate_countries, weights=weights, k=1)[0]
            else:
                current_country = country_code.upper().strip()

            self.log(f"\n[cyan][{i}/{total_count}] Membuat profil & identitas perangkat baru ([yellow]{current_country}[/])...[/]")
            
            temp_email = None
            if verifier:
                # 2. Resolusi Provider Email Wajar (Gmail / Outlook / Hotmail - Bebas Edu & Bebas Daur Ulang Nama)
                if email_provider.upper() in ("RANDOM", "ALL", "AUTO", ""):
                    chosen_provider = random.choice(["gmail", "outlook", "hotmail"])
                else:
                    chosen_provider = email_provider.lower().strip()

                # Larang keras penggunaan edu
                if chosen_provider in ("edu", "high.edu.pl"):
                    chosen_provider = "outlook"

                provider_key = chosen_provider
                self.log(f"[dim]Mengambil email wajar dari temp.tf (provider: {chosen_provider})...[/]")
                use_dot = (provider_key == "gmail")
                use_plus = (provider_key != "gmail")
                
                # Coba ambil email dengan nama dasar yang benar-benar belum pernah terdaftar sama sekali
                for attempt_email in range(1, 15):
                    candidate = await verifier.get_email(provider=provider_key, use_dot=use_dot, use_plus=use_plus)
                    if candidate:
                        # Tolak jika domain edu
                        if "@high.edu.pl" in candidate or candidate.endswith(".edu"):
                            provider_key = random.choice(["gmail", "outlook", "hotmail"])
                            use_dot = (provider_key == "gmail")
                            use_plus = (provider_key != "gmail")
                            await asyncio.sleep(0.4)
                            continue

                        # Format email agar titik wajar & natural (maks 1 titik, bukan rentetan aneh)
                        candidate = format_natural_email(candidate)
                        base_u = extract_base_username(candidate)

                        # Tolak jika base user sudah pernah terdaftar (mencegah dot trick berulang dari nama yang sama)
                        if base_u in existing_base_users or candidate.lower() in existing_emails:
                            self.log(f"[dim yellow]Nama akun dasar '{base_u}' sudah pernah dipakai, meminta nama baru...[/]")
                            if email_provider.upper() in ("RANDOM", "ALL", "AUTO", ""):
                                chosen_provider = random.choice(["gmail", "outlook", "hotmail"])
                                provider_key = chosen_provider
                                use_dot = (provider_key == "gmail")
                                use_plus = (provider_key != "gmail")
                            await asyncio.sleep(0.5)
                            continue

                        temp_email = candidate.lower()
                        existing_emails.add(temp_email)
                        existing_base_users.add(base_u)
                        break
                    else:
                        if email_provider.upper() in ("RANDOM", "ALL", "AUTO", ""):
                            chosen_provider = random.choice(["gmail", "outlook", "hotmail"])
                            provider_key = chosen_provider
                            use_dot = (provider_key == "gmail")
                            use_plus = (provider_key != "gmail")
                        await asyncio.sleep(0.5)

                if temp_email:
                    self.log(f"[bold cyan]Email Wajar Didapat:[/] [green]{temp_email}[/] ([dim]{chosen_provider}[/])")
                else:
                    self.log("[yellow]Gagal mendapatkan email wajar dari temp.tf, fallback ke email sintetis.[/]")

            profile = ProfileGenerator.generate_profile(country_code=current_country, email=temp_email)

            # Ambil proxy unik untuk pendaftaran ini
            proxy = self.proxy_manager.pop_proxy(current_country)
            client = StealthApiClient(
                profile=profile,
                proxy_manager=self.proxy_manager,
                current_proxy=proxy,
            )

            ok, msg = await client.perform_organic_signup()
            if ok:
                is_verified = False
                if verifier and temp_email:
                    self.log("[cyan]Memicu pengiriman kode OTP verifikasi ke email...[/]")
                    send_ok, send_msg = await client.send_email_verification()
                    if send_ok:
                        otp_code = await verifier.poll_for_otp(
                            temp_email,
                            timeout_sec=120,
                            interval_sec=4,
                            log_callback=self.log,
                        )
                        if otp_code:
                            v_ok, v_msg = await client.verify_email_code(otp_code)
                            if v_ok:
                                is_verified = True
                                self.log(f"[bold green]✓ EMAIL TERVERIFIKASI RESMI:[/] {temp_email} (Kode: [yellow]{otp_code}[/])")
                            else:
                                self.log(f"[yellow]Verifikasi ditolak server: {v_msg}[/]")
                        else:
                            self.log("[yellow]Timeout: Kode OTP tidak diterima dalam 120 detik.[/]")
                    else:
                        self.log(f"[yellow]Gagal request OTP: {send_msg}[/]")

                if verifier and not is_verified:
                    self.log(f"[bold red]✗ Akun {profile.email} GAGAL diverifikasi (OTP timeout/ditolak). Akun TIDAK disimpan.[/]")
                else:
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
                        "created_at": profile.birth_date,
                        "is_email_verified": is_verified,
                    }
                    self.save_account(acc_data)
                    success_count += 1
                    verif_badge = "[bold green]VERIFIED[/]" if is_verified else "[dim yellow]UNVERIFIED[/]"
                    self.log(f"[bold green]✓ Berhasil Mendaftar:[/] {profile.email} [{verif_badge}] (Atribusi AppsFlyer OK)")
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
