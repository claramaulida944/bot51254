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

    # ── Pool Nama Per Negara ─────────────────────────────────────────────
    NAMES_BY_COUNTRY: Dict[str, Dict[str, list]] = {
        "ID": {
            "first": [
                "Andi", "Budi", "Cahya", "Deni", "Eka", "Fajar", "Galih", "Hendra",
                "Irfan", "Joko", "Kiki", "Lukman", "Mega", "Nisa", "Oky", "Putri",
                "Rina", "Sari", "Tono", "Udin", "Vina", "Wati", "Yuni", "Zahra",
                "Ayu", "Dewi", "Fitri", "Gita", "Hani", "Indah", "Lina", "Maya",
                "Nana", "Puja", "Rara", "Sinta", "Tia", "Wulan", "Agus", "Arif",
                "Bayu", "Dika", "Farel", "Gilang", "Hari", "Ilham", "Kurnia", "Rizky",
            ],
            "last": [
                "Pratama", "Saputra", "Wijaya", "Kusuma", "Santoso", "Hidayat",
                "Lestari", "Nugroho", "Permana", "Setiawan", "Ramadhan", "Utami",
                "Wahyudi", "Kurniawan", "Firmansyah", "Handoko", "Suryadi", "Wibowo",
                "Hartono", "Purnomo", "Rahayu", "Susanto", "Gunawan", "Mulyani",
            ],
        },
        "KR": {
            "first": [
                "민준", "서준", "도윤", "예준", "시우", "하준", "주원", "지호",
                "지후", "준서", "지우", "현우", "건우", "우진", "선우", "서연",
                "서윤", "지우", "하은", "하윤", "민서", "지유", "윤서", "채원",
                "수아", "지아", "은서", "다은", "소율", "예은",
            ],
            "last": [
                "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
                "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍",
            ],
            # Romanisasi untuk nickname (API mungkin butuh latin)
            "first_roman": [
                "Minjun", "Seojun", "Doyun", "Yejun", "Siwoo", "Hajun", "Juwon", "Jiho",
                "Jihu", "Junseo", "Jiwoo", "Hyunwoo", "Gunwoo", "Woojin", "Sunwoo",
                "Seoyeon", "Seoyun", "Jiwoo", "Haeun", "Hayun", "Minseo", "Jiyu",
                "Yunseo", "Chaewon", "Sua", "Jia", "Eunseo", "Daeun", "Soyul", "Yeeun",
                "Minji", "Sohee", "Yuna", "Dohyun", "Taehyung", "Jihoon",
            ],
            "last_roman": [
                "Kim", "Lee", "Park", "Choi", "Jung", "Kang", "Cho", "Yoon",
                "Jang", "Lim", "Han", "Oh", "Seo", "Shin", "Kwon", "Hwang",
                "Ahn", "Song", "Ryu", "Hong",
            ],
        },
        "JP": {
            "first": [
                "Haruto", "Yuto", "Sota", "Hinata", "Riku", "Minato", "Aoi",
                "Yui", "Hana", "Kokona", "Mei", "Sakura", "Rin", "Mio", "Akari",
                "Kaito", "Ren", "Sora", "Hayato", "Kenta", "Ryo", "Shun", "Yuma",
                "Nana", "Saki", "Yuki", "Ayaka", "Miyu", "Kanon", "Koharu",
            ],
            "last": [
                "Sato", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito",
                "Yamamoto", "Nakamura", "Kobayashi", "Kato", "Yoshida", "Yamada",
                "Sasaki", "Yamaguchi", "Matsumoto", "Inoue", "Kimura", "Hayashi",
                "Shimizu", "Yamazaki",
            ],
        },
        "US": {
            "first": [
                "James", "Robert", "John", "Michael", "David", "William", "Richard",
                "Joseph", "Thomas", "Christopher", "Mary", "Patricia", "Jennifer",
                "Linda", "Emily", "Emma", "Olivia", "Sophia", "Ava", "Isabella",
                "Liam", "Noah", "Oliver", "Elijah", "Lucas", "Mason", "Logan",
                "Ethan", "Aiden", "Jackson", "Chloe", "Mia", "Harper", "Ella",
            ],
            "last": [
                "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
                "Davis", "Rodriguez", "Martinez", "Anderson", "Taylor", "Thomas",
                "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris",
                "Clark", "Lewis", "Robinson", "Walker", "Hall",
            ],
        },
        "GB": {
            "first": [
                "Oliver", "George", "Harry", "Jack", "Noah", "Leo", "Arthur",
                "Oscar", "Charlie", "Muhammad", "Amelia", "Olivia", "Isla", "Ava",
                "Emily", "Sophia", "Grace", "Mia", "Poppy", "Ella", "Alfie",
                "Thomas", "Henry", "William", "James", "Freddie", "Archie", "Teddy",
            ],
            "last": [
                "Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson",
                "Davies", "Robinson", "Wright", "Thompson", "Evans", "Walker", "White",
                "Roberts", "Green", "Hall", "Wood", "Jackson", "Clarke",
            ],
        },
        "DE": {
            "first": [
                "Lukas", "Leon", "Finn", "Elias", "Jonas", "Ben", "Paul", "Noah",
                "Luca", "Maximilian", "Emma", "Mia", "Hannah", "Sofia", "Lina",
                "Emilia", "Anna", "Marie", "Lea", "Lena", "Felix", "Moritz",
                "Julian", "Tim", "Niklas", "Jan", "Tom", "David", "Max", "Erik",
            ],
            "last": [
                "Mueller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer",
                "Wagner", "Becker", "Schulz", "Hoffmann", "Koch", "Richter",
                "Wolf", "Schaefer", "Bauer", "Klein", "Zimmermann", "Braun",
                "Hartmann", "Lang",
            ],
        },
    }

    # Fallback: pool global kalau country tidak dikenal
    FIRST_NAMES = [
        "Alex", "Jordan", "Taylor", "Morgan", "Sam", "Chris", "Robin", "Max",
        "Leo", "Mia", "Ava", "Noah", "Liam", "Emma", "Ella", "Jack",
    ]
    LAST_NAMES = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis",
        "Lee", "Park", "Kim", "Garcia", "Silva", "Martin", "Taylor",
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
    def _get_name_pair(cls, country: str = "ID") -> Tuple[str, str]:
        """Ambil pasangan first_name & last_name yang cocok dengan negara."""
        pool = cls.NAMES_BY_COUNTRY.get(country)
        if not pool:
            return random.choice(cls.FIRST_NAMES), random.choice(cls.LAST_NAMES)

        if country == "KR":
            fn = random.choice(pool.get("first_roman", pool["first"]))
            ln = random.choice(pool.get("last_roman", pool["last"]))
        else:
            fn = random.choice(pool["first"])
            ln = random.choice(pool["last"])
        return fn, ln

    @classmethod
    def generate_nickname(cls, country: str = "ID") -> str:
        """
        Menghasilkan nickname realistis yang cocok dengan budaya negara.
        Variasi style layaknya user sungguhan di app novel.
        """
        fn, ln = cls._get_name_pair(country)

        # Gaya nickname bervariasi tergantung budaya
        if country == "KR":
            styles = [
                f"{fn}_{ln}",               # Minjun_Kim
                f"{fn}{random.randint(0, 99):02d}",  # Seojun07
                f"{fn}.{ln}",               # Hajun.Park
                f"{ln}{fn}",                # KimJiho
                f"{fn}{random.randint(90, 2009)}",  # Yeeun1998
                # Kadang pakai hangeul asli
                lambda: f"{random.choice(cls.NAMES_BY_COUNTRY['KR']['last'])}{random.choice(cls.NAMES_BY_COUNTRY['KR']['first'])}",
            ]
        elif country == "JP":
            styles = [
                f"{fn}_{ln}",               # Haruto_Sato
                f"{fn}{random.randint(0, 99):02d}",  # Yui03
                f"{ln}{fn}",                # TanakaRen
                f"{fn}.{ln[:4]}",           # Sakura.Suzu
                f"{fn}{random.randint(90, 2008)}",  # Kaito1999
            ]
        elif country == "ID":
            styles = [
                f"{fn}{random.randint(0, 99):02d}",  # Rina42
                f"{fn}_{ln[:4]}",           # Budi_Prat
                f"{fn} {ln}",               # Sari Lestari
                f"{fn}.{ln}",               # Cahya.Wijaya
                f"{fn}{random.choice(['_', ''])}{random.randint(90, 2008)}",  # Dewi_2001
                f"{fn.lower()}{ln.lower()[:3]}{random.randint(1, 99)}",  # fajarwij12
            ]
        elif country == "DE":
            styles = [
                f"{fn}_{ln[:5]}",           # Lukas_Muell
                f"{fn}{random.randint(0, 99):02d}",  # Emma14
                f"{fn}.{ln}",               # Leon.Schmidt
                f"{fn}{random.randint(90, 2007)}",  # Felix1997
                f"{fn.lower()}{random.randint(1, 999)}",  # moritz37
            ]
        else:  # US, GB, dan lainnya
            styles = [
                f"{fn}_{ln[:4]}",           # Oliver_Smit
                f"{fn}{random.randint(0, 99):02d}",  # Emily23
                f"{fn}.{ln}",               # James.Brown
                f"{fn}{random.choice(['_', ''])}{random.randint(90, 2008)}",  # Liam2001
                f"{fn.lower()}{ln.lower()[:3]}{random.randint(1, 99)}",  # noahjoh8
                f"{fn} {ln[:1]}.",           # Olivia S.
            ]

        pick = random.choice(styles)
        # Jika pick adalah lambda (untuk hangeul), panggil dulu
        if callable(pick):
            pick = pick()
        return pick

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
    def generate_profile(cls, country_code: str = "ID", email: Optional[str] = None) -> AccountProfile:
        country = country_code if country_code in COUNTRY_METADATA else "ID"
        meta = COUNTRY_METADATA[country]

        fn, ln = cls._get_name_pair(country)
        nick = cls.generate_nickname(country)

        # Email realistis dengan variasi numerik/separator jika tidak disuplai
        if not email:
            sep = random.choice([".", "_", "", "-"])
            salt = secrets.token_hex(random.randint(2, 4))
            domain = random.choice(cls.POPULAR_EMAIL_DOMAINS)
            email = f"{fn.lower()}{sep}{ln.lower()}_{salt}@{domain}"
        else:
            email = email.strip().lower()

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
