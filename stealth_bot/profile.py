"""
Generator Profil Entropi Tinggi & Identitas Perangkat Android Otentik.
Menghasilkan fingerprint perangkat, nama realistis, locale, dan session identifier
yang selaras dengan format resmi aplikasi native Quarterfull / Toodat.
"""

import math
import random
import secrets
import string
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from .proxy import COUNTRY_METADATA


@dataclass
class AccountProfile:
    email: str
    password: str
    first_name: str
    last_name: str
    nickname: str
    birth_date: str
    gender: str
    country: str
    timezone: str
    accept_language: str
    device_id: str
    anonymous_id: str
    user_agent: str


class ProfileGenerator:
    """Menghasilkan profil akun baru dengan variasi tinggi dan sidik jari perangkat realistis."""

    POPULAR_EMAIL_DOMAINS = [
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
        "yandex.com", "protonmail.com", "zoho.com", "fastmail.com", "mail.com"
    ]

    FIRST_NAMES = [
        "Alex", "Jordan", "Taylor", "Morgan", "Sam", "Chris", "Pat", "Robin",
        "David", "Sarah", "Michael", "Emma", "Daniel", "Olivia", "James", "Sophia",
        "Budi", "Siti", "Agus", "Dewi", "Rian", "Putri", "Bayu", "Nur",
        "Kenji", "Yuki", "Haruto", "Yui", "Minho", "Jiwoo", "Seojun", "Sora"
    ]

    LAST_NAMES = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis",
        "Pratama", "Saputra", "Wijaya", "Kusuma", "Santoso", "Hidayat", "Lestari",
        "Tanaka", "Sato", "Suzuki", "Kim", "Lee", "Park", "Choi", "Garcia", "Silva"
    ]

    GENDERS = ["female", "male", "prefer_not_to_say"]

    @staticmethod
    def generate_base36(num: int) -> str:
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        if num == 0:
            return "0"
        res = []
        while num > 0:
            num, rem = divmod(num, 36)
            res.append(chars[rem])
        return "".join(reversed(res))

    @classmethod
    def generate_anonymous_id(cls) -> str:
        """Format resmi: anon_{timestamp_base36}_{random_base36} (cth: anon-mu0ly2iy-g7lfxkg2ws)."""
        now_ms = int(time.time() * 1000)
        ts_b36 = cls.generate_base36(now_ms)
        rnd_b36 = "".join(secrets.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(10))
        return f"anon-{ts_b36}-{rnd_b36}"

    @classmethod
    def generate_session_id(cls, prefix: str = "ses") -> str:
        """Format session ID resmi aplikasi."""
        now_ms = int(time.time() * 1000)
        rnd = "".join(secrets.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(11))
        return f"{now_ms}-{rnd}"

    @classmethod
    def generate_entry_id(cls) -> str:
        now_ms = int(time.time() * 1000)
        ts_b36 = cls.generate_base36(now_ms)
        rnd = "".join(secrets.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(8))
        return f"entry_{ts_b36}_{rnd}"

    @classmethod
    def generate_device_id(cls) -> str:
        """UUID v4 otentik."""
        return str(uuid.uuid4())

    @classmethod
    def generate_password(cls) -> str:
        """Password aman yang lolos validasi server (8-16 char, huruf, angka, simbol)."""
        letters = string.ascii_letters
        digits = string.digits
        symbols = "!@#$%^&*"
        core = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(digits),
            secrets.choice(symbols),
        ]
        remaining = [secrets.choice(letters + digits + symbols) for _ in range(8)]
        pwd_chars = core + remaining
        random.shuffle(pwd_chars)
        return "".join(pwd_chars)

    @classmethod
    def generate_profile(cls, country_code: str = "ID") -> AccountProfile:
        country = country_code if country_code in COUNTRY_METADATA else "ID"
        meta = COUNTRY_METADATA[country]

        fn = random.choice(cls.FIRST_NAMES)
        ln = random.choice(cls.LAST_NAMES)
        nick = f"{fn} {ln}".strip()

        # Email realistis dengan variasi numerik/separator
        sep = random.choice([".", "_", "", "-"])
        salt = secrets.token_hex(random.randint(2, 4))
        domain = random.choice(cls.POPULAR_EMAIL_DOMAINS)
        email = f"{fn.lower()}{sep}{ln.lower()}_{salt}@{domain}"

        # Usia 18-35 tahun
        birth_year = random.randint(1991, 2007)
        birth_month = random.randint(1, 12)
        birth_day = random.randint(1, 28)
        birth_date = f"{birth_year:04d}-{birth_month:02d}-{birth_day:02d}"

        # Device & Anonymous IDs
        device_id = cls.generate_device_id()
        anonymous_id = cls.generate_anonymous_id()

        # User-Agent resmi OkHttp
        user_agent = "okhttp/4.12.0"

        return AccountProfile(
            email=email,
            password=cls.generate_password(),
            first_name=fn,
            last_name=ln,
            nickname=nick,
            birth_date=birth_date,
            gender=random.choice(cls.GENDERS),
            country=country,
            timezone=meta["timezone"],
            accept_language=meta["lang"],
            device_id=device_id,
            anonymous_id=anonymous_id,
            user_agent=user_agent,
        )
