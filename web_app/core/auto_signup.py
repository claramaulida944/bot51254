"""
Modul Registrasi Otomatis & Generator Profil Entropi Tinggi (High Entropy)
Katalog 50 Negara Dunia untuk API Toodat / Quarterfull.

Fitur Utama:
1. HighEntropyProfileGenerator:
   - Katalog 50 negara lengkap (ID, US, KR, JP, GB, CA, AU, DE, FR, IT, ES, BR, MX, SG, MY,
     PH, TH, VN, IN, NL, PL, TR, RU, UA, SE, NO, DK, FI, CZ, HU, RO, GR, PT, AR, CL, CO,
     PE, NZ, ZA, SA, AE, EG, TW, HK, AT, CH, BE, IE, IL, PK).
   - Pemetaan presisi: Faker locale, IANA Timezone, Accept-Language header.
   - Normalisasi ASCII / transliterasi fonetik & fallback username Latin bersih untuk script non-Latin.
   - Format email ber-entropi tinggi (> 1 Miliar kombinasi) bebas tabrakan karakter.
   - Password kuat (12-16 char CSPRNG secrets), usia 18-36 tahun, gender proporsional.
2. UserAgentGenerator:
   - Mode 1: OkHttp Android native (okhttp/4.12.0, 4.11.0, 4.10.0, 4.9.3).
   - Mode 2: Android Dalvik & Mobile WebView dengan database perangkat nyata (Samsung, Pixel, Xiaomi).
3. RegistrationRunner:
   - Integrasi koneksi HTTP/2 ke POST /api/auth/signup.
   - Sinkronisasi header fingerprint dan parameter negara ke payload signup.
   - Dukungan pemilihan negara spesifik atau mode 'RANDOM' / 'ALL' (campuran 50 negara).
   - Persistensi akun terdaftar ke format JSON Lines pada `akun.txt`.
"""

import argparse
import concurrent.futures
import json
import logging
import os
import random
import re
import secrets
import string
import sys
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from faker import Faker
import httpx

from session_manager import IdentifierGenerator
from proxy_manager import (
    ProxyManager,
    ProxyInfo,
    default_proxy_manager,
    is_dead_or_proxy_error,
    sanitize_proxy_url,
    get_weighted_royalty_country,
)

# Konfigurasi logging
logger = logging.getLogger("AutoSignup")

# Tabel pemetaan fonetik Romanisasi Hangul (Revised Romanization of Korean)
HANGUL_CHOSUNG: List[str] = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s",
    "ss", "", "j", "jj", "ch", "k", "t", "p", "h"
]
HANGUL_JUNGSUNG: List[str] = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa",
    "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"
]
HANGUL_JONGSUNG: List[str] = [
    "", "g", "kk", "ks", "n", "nj", "nh", "d", "l", "lg",
    "lm", "lb", "ls", "lt", "lp", "lh", "m", "b", "bs", "s",
    "ss", "ng", "j", "ch", "k", "t", "p", "h"
]

# Katalog 50 Negara Lengkap & Pemetaan Parameter
COUNTRY_CONFIG: Dict[str, Dict[str, str]] = {
    "ID": {"locale": "id_ID", "timezone": "Asia/Jakarta", "lang": "id"},
    "US": {"locale": "en_US", "timezone": "America/New_York", "lang": "en-US,en;q=0.9"},
    "KR": {"locale": "ko_KR", "timezone": "Asia/Seoul", "lang": "ko-KR,ko;q=0.9"},
    "JP": {"locale": "ja_JP", "timezone": "Asia/Tokyo", "lang": "ja-JP,ja;q=0.9"},
    "GB": {"locale": "en_GB", "timezone": "Europe/London", "lang": "en-GB,en;q=0.9"},
    "CA": {"locale": "en_CA", "timezone": "America/Toronto", "lang": "en-CA,en;q=0.9"},
    "AU": {"locale": "en_AU", "timezone": "Australia/Sydney", "lang": "en-AU,en;q=0.9"},
    "DE": {"locale": "de_DE", "timezone": "Europe/Berlin", "lang": "de-DE,de;q=0.9"},
    "FR": {"locale": "fr_FR", "timezone": "Europe/Paris", "lang": "fr-FR,fr;q=0.9"},
    "IT": {"locale": "it_IT", "timezone": "Europe/Rome", "lang": "it-IT,it;q=0.9"},
    "ES": {"locale": "es_ES", "timezone": "Europe/Madrid", "lang": "es-ES,es;q=0.9"},
    "BR": {"locale": "pt_BR", "timezone": "America/Sao_Paulo", "lang": "pt-BR,pt;q=0.9"},
    "MX": {"locale": "es_MX", "timezone": "America/Mexico_City", "lang": "es-MX,es;q=0.9"},
    "SG": {"locale": "en_SG", "timezone": "Asia/Singapore", "lang": "en-SG,en;q=0.9"},
    "MY": {"locale": "ms_MY", "timezone": "Asia/Kuala_Lumpur", "lang": "ms-MY,ms;q=0.9"},
    "PH": {"locale": "fil_PH", "timezone": "Asia/Manila", "lang": "fil-PH,fil;q=0.9,en;q=0.8"},
    "TH": {"locale": "th_TH", "timezone": "Asia/Bangkok", "lang": "th-TH,th;q=0.9"},
    "VN": {"locale": "vi_VN", "timezone": "Asia/Ho_Chi_Minh", "lang": "vi-VN,vi;q=0.9"},
    "IN": {"locale": "en_IN", "timezone": "Asia/Kolkata", "lang": "en-IN,en;q=0.9,hi;q=0.8"},
    "NL": {"locale": "nl_NL", "timezone": "Europe/Amsterdam", "lang": "nl-NL,nl;q=0.9"},
    "PL": {"locale": "pl_PL", "timezone": "Europe/Warsaw", "lang": "pl-PL,pl;q=0.9"},
    "TR": {"locale": "tr_TR", "timezone": "Europe/Istanbul", "lang": "tr-TR,tr;q=0.9"},
    "RU": {"locale": "ru_RU", "timezone": "Europe/Moscow", "lang": "ru-RU,ru;q=0.9"},
    "UA": {"locale": "uk_UA", "timezone": "Europe/Kyiv", "lang": "uk-UA,uk;q=0.9"},
    "SE": {"locale": "sv_SE", "timezone": "Europe/Stockholm", "lang": "sv-SE,sv;q=0.9"},
    "NO": {"locale": "no_NO", "timezone": "Europe/Oslo", "lang": "no-NO,no;q=0.9"},
    "DK": {"locale": "da_DK", "timezone": "Europe/Copenhagen", "lang": "da-DK,da;q=0.9"},
    "FI": {"locale": "fi_FI", "timezone": "Europe/Helsinki", "lang": "fi-FI,fi;q=0.9"},
    "CZ": {"locale": "cs_CZ", "timezone": "Europe/Prague", "lang": "cs-CZ,cs;q=0.9"},
    "HU": {"locale": "hu_HU", "timezone": "Europe/Budapest", "lang": "hu-HU,hu;q=0.9"},
    "RO": {"locale": "ro_RO", "timezone": "Europe/Bucharest", "lang": "ro-RO,ro;q=0.9"},
    "GR": {"locale": "el_GR", "timezone": "Europe/Athens", "lang": "el-GR,el;q=0.9"},
    "PT": {"locale": "pt_PT", "timezone": "Europe/Lisbon", "lang": "pt-PT,pt;q=0.9"},
    "AR": {"locale": "es_AR", "timezone": "America/Argentina/Buenos_Aires", "lang": "es-AR,es;q=0.9"},
    "CL": {"locale": "es_CL", "timezone": "America/Santiago", "lang": "es-CL,es;q=0.9"},
    "CO": {"locale": "es_CO", "timezone": "America/Bogota", "lang": "es-CO,es;q=0.9"},
    "PE": {"locale": "es_PE", "timezone": "America/Lima", "lang": "es-PE,es;q=0.9"},
    "NZ": {"locale": "en_NZ", "timezone": "Pacific/Auckland", "lang": "en-NZ,en;q=0.9"},
    "ZA": {"locale": "en_ZA", "timezone": "Africa/Johannesburg", "lang": "en-ZA,en;q=0.9"},
    "SA": {"locale": "ar_SA", "timezone": "Asia/Riyadh", "lang": "ar-SA,ar;q=0.9"},
    "AE": {"locale": "ar_AE", "timezone": "Asia/Dubai", "lang": "ar-AE,ar;q=0.9,en;q=0.8"},
    "EG": {"locale": "ar_EG", "timezone": "Africa/Cairo", "lang": "ar-EG,ar;q=0.9"},
    "TW": {"locale": "zh_TW", "timezone": "Asia/Taipei", "lang": "zh-TW,zh;q=0.9"},
    "HK": {"locale": "zh_HK", "timezone": "Asia/Hong_Kong", "lang": "zh-HK,zh;q=0.9,en;q=0.8"},
    "AT": {"locale": "de_AT", "timezone": "Europe/Vienna", "lang": "de-AT,de;q=0.9"},
    "CH": {"locale": "de_CH", "timezone": "Europe/Zurich", "lang": "de-CH,de;q=0.9"},
    "BE": {"locale": "nl_BE", "timezone": "Europe/Brussels", "lang": "nl-BE,nl;q=0.9,fr;q=0.8"},
    "IE": {"locale": "en_IE", "timezone": "Europe/Dublin", "lang": "en-IE,en;q=0.9"},
    "IL": {"locale": "he_IL", "timezone": "Asia/Jerusalem", "lang": "he-IL,he;q=0.9"},
    "PK": {"locale": "en_PK", "timezone": "Asia/Karachi", "lang": "en-PK,en;q=0.9,ur;q=0.8"},
}

# Fallback untuk locale Faker yang tidak secara bawaan disertakan oleh paket faker
FAKER_LOCALE_FALLBACKS: Dict[str, str] = {
    "en_SG": "en_US",
    "ms_MY": "id_ID",
    "es_PE": "es_ES",
    "en_ZA": "en_US",
    "zh_HK": "zh_TW",
}


@dataclass
class AccountProfile:
    """Struktur data representasi profil akun yang siap didaftarkan."""
    email: str
    password: str
    birth_date: str
    gender: str
    country: str
    timezone: str
    accept_language: str
    first_name: str
    last_name: str


class HighEntropyProfileGenerator:
    """
    Generator identitas profil akun dengan entropi tinggi untuk katalog 50 negara,
    dengan transliterasi fonetik dan fallback username Latin bersih.
    """

    # Distribusi 20+ domain email populer & terpercaya
    GLOBAL_EMAIL_DOMAINS: List[Tuple[str, float]] = [
        ("gmail.com", 0.28),
        ("yahoo.com", 0.12),
        ("outlook.com", 0.12),
        ("hotmail.com", 0.10),
        ("icloud.com", 0.08),
        ("proton.me", 0.05),
        ("protonmail.com", 0.04),
        ("zoho.com", 0.04),
        ("mail.com", 0.03),
        ("gmx.com", 0.03),
        ("gmx.net", 0.02),
        ("fastmail.com", 0.02),
        ("live.com", 0.02),
        ("msn.com", 0.01),
        ("yandex.com", 0.01),
        ("aol.com", 0.01),
        ("tutanota.com", 0.01),
        ("tutamail.com", 0.01),
    ]

    # Domain email regional spesifik per negara
    COUNTRY_SPECIFIC_DOMAINS: Dict[str, List[str]] = {
        "ID": ["yahoo.co.id", "gmail.com"],
        "GB": ["yahoo.co.uk", "outlook.co.uk", "virginmedia.com"],
        "DE": ["gmx.de", "web.de", "yahoo.de", "t-online.de"],
        "FR": ["orange.fr", "free.fr", "laposte.net", "sfr.fr", "yahoo.fr"],
        "IT": ["libero.it", "virgilio.it", "tiscali.it", "yahoo.it"],
        "ES": ["yahoo.es"],
        "BR": ["uol.com.br", "bol.com.br", "terra.com.br", "yahoo.com.br"],
        "JP": ["yahoo.co.jp"],
        "KR": ["naver.com", "daum.net", "kakao.com"],
        "PL": ["wp.pl", "onet.pl", "interia.pl"],
        "RU": ["mail.ru", "yandex.ru", "rambler.ru", "bk.ru"],
        "UA": ["ukr.net", "i.ua"],
    }

    # Separator pemisah nama & variasi
    SEPARATORS: List[str] = [".", "_", "", "-"]

    # Kumpulan kata awalan & akhiran realistis untuk membedakan email
    WORDS_PREFIX: List[str] = [
        "the", "real", "my", "im", "iam", "hey", "go", "pro", "mr", "ms",
        "official", "just", "its", "hi", "all", "true", "live", "vip"
    ]
    WORDS_SUFFIX: List[str] = [
        "dev", "app", "hub", "mail", "box", "net", "web", "zone", "star", "sky",
        "life", "play", "core", "run", "one", "lab", "peak", "fox", "wave", "link",
        "post", "flow", "cloud", "sync", "code", "work", "tech", "site", "base", "space"
    ]

    # Karakter simbol aman untuk password
    PASSWORD_SPECIAL_CHARS: str = "!@#$%^&*"

    def __init__(self) -> None:
        """Inisialisasi cache Faker untuk efisiensi memori."""
        self._faker_cache: Dict[str, Faker] = {}
        # Faker universal untuk fallback nama Latin pada script non-Latin murni
        self._universal_faker: Faker = Faker("en_US")
        self._lock = threading.Lock()

    def _get_faker(self, country_code: str) -> Faker:
        """Mengambil atau menginisialisasi Faker instance untuk negara tertentu secara thread-safe."""
        with self._lock:
            if country_code not in self._faker_cache:
                target_locale = COUNTRY_CONFIG[country_code]["locale"]
                try:
                    self._faker_cache[country_code] = Faker(target_locale)
                except Exception:
                    fallback_locale = FAKER_LOCALE_FALLBACKS.get(target_locale, "en_US")
                    self._faker_cache[country_code] = Faker(fallback_locale)
            return self._faker_cache[country_code]

    @staticmethod
    def romanize_korean(text: str) -> str:
        """
        Mengonversi karakter Hangul Korea ke huruf alfabet Latin (a-z)
        menggunakan aturan Revised Romanization of Korean.
        """
        romanized: List[str] = []
        for ch in text:
            code = ord(ch)
            if 0xAC00 <= code <= 0xD7A3:
                s_idx = code - 0xAC00
                cho = s_idx // (21 * 28)
                jung = (s_idx % (21 * 28)) // 28
                jong = s_idx % 28
                romanized.append(
                    HANGUL_CHOSUNG[cho] + HANGUL_JUNGSUNG[jung] + HANGUL_JONGSUNG[jong]
                )
            elif ch.isascii():
                romanized.append(ch)
        return "".join(romanized)

    def clean_or_fallback_name(self, name: str, country_code: str, is_first: bool = True) -> str:
        """
        Membersihkan nama untuk bagian username email:
        1. Menangani transliterasi khusus (misal Korea Hangul).
        2. Menormalisasi huruf beraksen Latin via NFKD (é -> e, ü -> u, ñ -> n, ç -> c).
        3. Jika menghasilkan string non-Latin murni (Cyrillic, Arabic, Kanji, Thai, Hebrew),
           gunakan fallback generator nama Latin universal (en_US).
        4. Menjamin hanya berisi [a-z0-9] dan minimal 2 karakter.
        """
        processed_name = name

        # Transliterasi khusus Korea
        if country_code == "KR":
            processed_name = self.romanize_korean(processed_name)

        # Normalisasi karakter beraksen Latin (NFKD)
        normalized = (
            unicodedata.normalize("NFKD", processed_name)
            .encode("ascii", "ignore")
            .decode("ascii")
        )

        # Hanya ambil a-z dan 0-9
        cleaned = re.sub(r"[^a-z0-9]", "", normalized.lower())

        # Jika nama hasil sanitasi terlalu pendek atau kosong (kasus non-Latin murni)
        if len(cleaned) < 2:
            if is_first:
                fallback_name = self._universal_faker.first_name()
            else:
                fallback_name = self._universal_faker.last_name()
            cleaned = re.sub(r"[^a-z0-9]", "", fallback_name.lower())

        return cleaned or ("user" if is_first else "reader")

    @classmethod
    def generate_entropy(cls) -> str:
        """
        Menghasilkan komponen entropi dinamis dengan 5 strategi acak:
        1. Alphanumeric 4-7 karakter acak (cth: 7k3m9a)
        2. Token hex 4-6 karakter + angka acak (cth: f3a842)
        3. Angka acak 3-6 digit (cth: 94821)
        4. Kata kunci tematik + angka (cth: dev492)
        5. Base36 string 5-7 karakter (cth: z9b8c1)
        """
        mode = secrets.randbelow(5)
        if mode == 0:
            chars = string.ascii_lowercase + string.digits
            return "".join(secrets.choice(chars) for _ in range(secrets.choice([4, 5, 6, 7])))
        elif mode == 1:
            hex_part = secrets.token_hex(secrets.choice([2, 3]))
            digits = "".join(secrets.choice(string.digits) for _ in range(secrets.choice([2, 3])))
            return f"{hex_part}{digits}"
        elif mode == 2:
            return "".join(secrets.choice(string.digits) for _ in range(secrets.choice([3, 4, 5, 6])))
        elif mode == 3:
            word = secrets.choice(cls.WORDS_SUFFIX)
            digits = "".join(secrets.choice(string.digits) for _ in range(secrets.choice([2, 3, 4])))
            return f"{word}{digits}"
        else:
            b36 = "0123456789abcdefghijklmnopqrstuvwxyz"
            return "".join(secrets.choice(b36) for _ in range(secrets.choice([5, 6, 7])))

    @classmethod
    def generate_unique_hash(cls) -> str:
        """Kompatibilitas mundur: memanggil generate_entropy()."""
        return cls.generate_entropy()

    @classmethod
    def generate_password(cls, length: int = 14) -> str:
        """
        Menghasilkan password kuat 12-16 karakter dengan pustaka `secrets`,
        menjamin terpenuhinya huruf besar, huruf kecil, angka, dan simbol.
        """
        if length < 12:
            length = 12
        elif length > 16:
            length = 16

        req_upper = secrets.choice(string.ascii_uppercase)
        req_lower = secrets.choice(string.ascii_lowercase)
        req_digit = secrets.choice(string.digits)
        req_punct = secrets.choice(cls.PASSWORD_SPECIAL_CHARS)

        all_allowed = (
            string.ascii_letters + string.digits + cls.PASSWORD_SPECIAL_CHARS
        )
        remaining = [secrets.choice(all_allowed) for _ in range(length - 4)]

        full_list = [req_upper, req_lower, req_digit, req_punct] + remaining
        secrets.SystemRandom().shuffle(full_list)
        return "".join(full_list)

    @classmethod
    def generate_birth_date(cls, min_age: int = 19, max_age: int = 36) -> str:
        """
        Menghasilkan tanggal lahir format YYYY-MM-DD dengan jaminan usia >= 18 tahun penuh
        dari tanggal hari ini (rentang tahun kelahiran 1990 - 2007).
        """
        today = date.today()
        # Batas atas: minimal 18.5 tahun ke belakang agar selalu memenuhi kriteria usia server
        end_date = today - timedelta(days=int(18.5 * 365.25))
        start_date = today - timedelta(days=int(max_age * 365.25))

        delta_days = (end_date - start_date).days
        random_days = secrets.randbelow(delta_days + 1)
        birth = start_date + timedelta(days=random_days)
        return birth.strftime("%Y-%m-%d")

    @classmethod
    def generate_gender(cls) -> str:
        """Mengembalikan gender terdistribusi proporsional."""
        genders = ["male", "female", "prefer_not_to_say"]
        weights = [0.45, 0.45, 0.10]
        return random.choices(genders, weights=weights, k=1)[0]

    def _choose_email_domain(self, country_code: str) -> str:
        """Memilih domain email dengan probabilitas cerdas antara penyedia global & lokal."""
        # 25% kemungkinan menggunakan domain lokal jika tersedia untuk negara tersebut
        if country_code in self.COUNTRY_SPECIFIC_DOMAINS and secrets.randbelow(100) < 25:
            return secrets.choice(self.COUNTRY_SPECIFIC_DOMAINS[country_code])

        domains, weights = zip(*self.GLOBAL_EMAIL_DOMAINS)
        return random.choices(domains, weights=weights, k=1)[0]

    def generate_email_address(
        self,
        first_name_clean: str,
        last_name_clean: str,
        country_code: str,
        birth_year: int,
    ) -> str:
        """
        Menghasilkan alamat email ber-entropi tinggi dengan 12 pola kombinasi manusiawi
        yang sangat variatif untuk mencegah tabrakan nama akun yang telah terdaftar.
        """
        f = first_name_clean
        l = last_name_clean
        fi = f[0] if f else "u"
        li = l[0] if l else "r"
        sep = secrets.choice(self.SEPARATORS)
        sep2 = secrets.choice(self.SEPARATORS)
        entropy = self.generate_entropy()
        domain = self._choose_email_domain(country_code)

        year_short = str(birth_year)[2:]
        year_full = str(birth_year)
        chosen_year = secrets.choice([year_short, year_full, ""])

        # 12 Pola Struktur Email yang Berbeda
        pattern_mode = secrets.randbelow(12)

        if pattern_mode == 0:
            # Pola standar: first.last_hash
            local_part = f"{f}{sep}{l}{sep2}{entropy}"
        elif pattern_mode == 1:
            # Pola nama dibalik: last.first_hash
            local_part = f"{l}{sep}{f}{sep2}{entropy}"
        elif pattern_mode == 2:
            # Inisial depan + nama belakang: j.smith_hash
            local_part = f"{fi}{sep}{l}{sep2}{entropy}"
        elif pattern_mode == 3:
            # Nama depan + inisial belakang: john.s_hash
            local_part = f"{f}{sep}{li}{sep2}{entropy}"
        elif pattern_mode == 4:
            # Mengandung tahun lahir + entropy singkat: maria.silva98_k3
            local_part = f"{f}{sep}{l}{chosen_year}{sep2}{entropy[:4]}"
        elif pattern_mode == 5:
            # Prefix kata + nama depan: real.john_hash
            pfx = secrets.choice(self.WORDS_PREFIX)
            local_part = f"{pfx}{sep}{f}{sep2}{entropy}"
        elif pattern_mode == 6:
            # Suffix kata tematik: john.doe_dev42
            sfx = secrets.choice(self.WORDS_SUFFIX)
            local_part = f"{f}{sep}{l}{sep2}{sfx}{entropy[:3]}"
        elif pattern_mode == 7:
            # Nama depan + nomor unik 4 digit + entropy: kevin8492_x7
            num = secrets.randbelow(9000) + 1000
            local_part = f"{f}{num}{sep}{entropy[:4]}"
        elif pattern_mode == 8:
            # Nama depan + entropy panjang saja: sarah_9k2m4x8a
            local_part = f"{f}{sep}{entropy}"
        elif pattern_mode == 9:
            # Nama belakang + entropy panjang: smith_7m4b2a9
            local_part = f"{l}{sep}{entropy}"
        elif pattern_mode == 10:
            # Pasangan inisial + kata + angka: jd_web941a
            w = secrets.choice(self.WORDS_SUFFIX)
            local_part = f"{fi}{li}{sep}{w}{entropy[:4]}"
        else:
            # Triple segmen: first.box.last_82
            mid = secrets.choice(self.WORDS_SUFFIX)
            local_part = f"{f}{sep}{mid}{sep2}{l}{entropy[:3]}"

        # Bersihkan jika ada tanda pemisah bertumpuk di awal/akhir
        local_part = re.sub(r"[._\-]{2,}", "_", local_part).strip("._-")
        if len(local_part) < 4:
            local_part = f"user_{local_part}_{entropy[:4]}"

        return f"{local_part}@{domain}"

    def generate_profile(
        self,
        country_code: Optional[str] = None,
        existing_emails: Optional[set] = None,
    ) -> AccountProfile:
        """
        Menghasilkan satu profil akun unik ber-entropi tinggi dari katalog 50 negara,
        dengan jaminan variasi email masif dan tidak bertabrakan dengan daftar lokal.

        :param country_code: Kode 2-huruf negara (cth: 'ID', 'JP', 'US') atau 'RANDOM'.
        :param existing_emails: Set email yang sudah terdaftar untuk dideduplikasi lokal.
        """
        all_countries = list(COUNTRY_CONFIG.keys())

        if not country_code or country_code.upper() in ("RANDOM", "ALL"):
            selected_country = get_weighted_royalty_country(all_countries)
        else:
            selected_country = country_code.upper()
            if selected_country not in COUNTRY_CONFIG:
                logger.warning(
                    "Kode negara '%s' tidak ditemukan dalam katalog. Menggunakan negara acak berbobot royalti.",
                    selected_country,
                )
                selected_country = get_weighted_royalty_country(all_countries)

        cfg = COUNTRY_CONFIG[selected_country]
        faker_instance = self._get_faker(selected_country)

        # Nama asli sesuai locale lokal
        first_name = faker_instance.first_name()
        last_name = faker_instance.last_name()

        # Pembersihan atau fallback nama Latin untuk username email
        first_name_clean = self.clean_or_fallback_name(
            first_name, selected_country, is_first=True
        )
        last_name_clean = self.clean_or_fallback_name(
            last_name, selected_country, is_first=False
        )

        # Tanggal lahir & gender
        birth_date = self.generate_birth_date()
        gender = self.generate_gender()
        birth_year = int(birth_date.split("-")[0])

        # Generate email dengan jaminan variasi dan deduplikasi terhadap daftar lokal
        email = ""
        for _ in range(15):
            candidate_email = self.generate_email_address(
                first_name_clean, last_name_clean, selected_country, birth_year
            )
            if not existing_emails or candidate_email.lower() not in existing_emails:
                email = candidate_email
                break

        if not email:
            # Fallback darurat bila tabrakan ekstrem
            fallback_hash = secrets.token_hex(6)
            domain = self._choose_email_domain(selected_country)
            email = f"{first_name_clean}_{fallback_hash}@{domain}"

        # Password 12-16 karakter
        pw_len = secrets.choice(range(12, 17))
        password = self.generate_password(length=pw_len)

        return AccountProfile(
            email=email,
            password=password,
            birth_date=birth_date,
            gender=gender,
            country=selected_country,
            timezone=cfg["timezone"],
            accept_language=cfg["lang"],
            first_name=first_name,
            last_name=last_name,
        )


class UserAgentGenerator:
    """
    Generator User-Agent Android dinamis yang realistis untuk menyamarkan fingerprint
    bot selaras dengan lingkungan aplikasi mobile native.
    """

    OKHTTP_VERSIONS: List[Tuple[str, float]] = [
        ("4.12.0", 0.60),  # Versi resmi aplikasi Toodat 3.0.52
        ("4.11.0", 0.20),
        ("4.10.0", 0.15),
        ("4.9.3", 0.05),
    ]

    # Database internal perangkat Android nyata & build ID realistis
    REAL_DEVICES: List[Dict[str, Any]] = [
        # Samsung Galaxy Series
        {"brand": "Samsung", "model": "SM-S928B", "name": "Galaxy S24 Ultra", "os": "15", "build": "AP2A.240805.005"},
        {"brand": "Samsung", "model": "SM-S918B", "name": "Galaxy S23 Ultra", "os": "14", "build": "UP1A.231005.007"},
        {"brand": "Samsung", "model": "SM-F946B", "name": "Galaxy Z Fold 5", "os": "14", "build": "UP1A.231005.007"},
        {"brand": "Samsung", "model": "SM-A546B", "name": "Galaxy A54 5G", "os": "13", "build": "TP1A.220624.014"},
        # Google Pixel Series
        {"brand": "Google", "model": "Pixel 9", "name": "Pixel 9", "os": "15", "build": "AD1A.240530.047"},
        {"brand": "Google", "model": "Pixel 8 Pro", "name": "Pixel 8 Pro", "os": "14", "build": "UD1A.230803.041"},
        {"brand": "Google", "model": "Pixel 8", "name": "Pixel 8", "os": "14", "build": "UD1A.230803.022"},
        {"brand": "Google", "model": "Pixel 7 Pro", "name": "Pixel 7 Pro", "os": "13", "build": "TQ3A.230901.001"},
        # Xiaomi / Redmi Series
        {"brand": "Xiaomi", "model": "23127PN0CG", "name": "Xiaomi 14", "os": "14", "build": "UKQ1.230804.001"},
        {"brand": "Xiaomi", "model": "2210132G", "name": "Xiaomi 13 Pro", "os": "13", "build": "TKQ1.221114.001"},
        {"brand": "Xiaomi", "model": "2312DRA50G", "name": "Redmi Note 13 Pro", "os": "13", "build": "TP1A.220624.014"},
    ]

    CHROME_VERSIONS: List[str] = [
        "124.0.6367.82",
        "126.0.6478.122",
        "128.0.6613.88",
        "130.0.6723.58",
        "131.0.6778.39",
    ]

    @classmethod
    def get_random_okhttp_ua(cls) -> str:
        """Mengembalikan User-Agent OkHttp Android (Mode 1 - Default bawaan aplikasi)."""
        versions, weights = zip(*cls.OKHTTP_VERSIONS)
        version = random.choices(versions, weights=weights, k=1)[0]
        return f"okhttp/{version}"

    @classmethod
    def get_random_mobile_ua(cls, style: str = "dalvik") -> str:
        """
        Mengembalikan User-Agent Android lengkap (Mode 2 - Dalvik / Mobile WebView).

        :param style: 'dalvik' untuk format VM Dalvik atau 'webview' untuk browser view.
        """
        device = secrets.choice(cls.REAL_DEVICES)
        os_version = device["os"]
        model = device["model"]
        build_id = device["build"]

        if style.lower() == "webview":
            chrome_ver = secrets.choice(cls.CHROME_VERSIONS)
            return (
                f"Mozilla/5.0 (Linux; Android {os_version}; {model} Build/{build_id}; wv) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/{chrome_ver} Mobile Safari/537.36"
            )
        else:
            return f"Dalvik/2.1.0 (Linux; U; Android {os_version}; {model} Build/{build_id})"

    @classmethod
    def get_random_ua(cls, mode: str = "okhttp") -> str:
        """
        Fungsi pemanggil terpadu untuk mendapatkan User-Agent berdasarkan mode yang diinginkan.
        """
        if mode == "okhttp":
            return cls.get_random_okhttp_ua()
        elif mode == "dalvik":
            return cls.get_random_mobile_ua(style="dalvik")
        elif mode == "webview":
            return cls.get_random_mobile_ua(style="webview")
        else:
            return cls.get_random_okhttp_ua()

    @classmethod
    def get_random_device(cls) -> Dict[str, Any]:
        """Mengembalikan metadata perangkat acak dari database internal."""
        return dict(secrets.choice(cls.REAL_DEVICES))


# Kumpulan kode pasar resmi yang diterima backend Quarterfull (/api/v1/service-countries)
# Backend memvalidasi pasar English/Global dengan kode 'EN' (untuk US, GB, CA, AU, dsb).
OFFICIAL_SERVICE_MARKETS = {
    "AT", "BR", "DE", "DK", "EN", "ES", "SE", "FR", "HK", "ID", "IN", "IT",
    "NL", "NO", "FI", "PL", "HU", "PT", "CZ", "JP", "AR", "PE", "CL", "EC",
    "DO", "GT", "PA", "BO", "VE", "PY", "HN", "SV", "NI", "CO", "MX", "KR",
    "MY", "PH", "SG", "TH", "TR", "TW", "VN"
}


def resolve_signup_market_country(country_code: str) -> str:
    """
    Menyinkronkan kode negara dengan pasar pendaftaran yang valid pada backend.
    Negara yang memiliki pasar lokal menggunakan kode negaranya (cth: ID, JP, KR, DE, dll),
    sedangkan negara English / Global (US, GB, CA, AU, NZ, PK, SA, dsb) dipetakan ke 'EN'.
    """
    upper = country_code.upper()
    if upper in OFFICIAL_SERVICE_MARKETS:
        return upper
    return "EN"


def load_proxies_if_available(file_path: str = "proxies.txt") -> List[str]:
    """Membaca daftar proxy dari file jika tersedia dan membersihkan format kotor."""
    p = Path(file_path)
    if not p.exists():
        return []
    proxies = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = sanitize_proxy_url(line)
            if cleaned:
                proxies.append(cleaned)
    return proxies


class RegistrationRunner:
    """
    Eksekutor pendaftaran akun otomatis ke endpoint POST /api/auth/signup
    dengan header fingerprint tersinkronisasi 50 negara, rotasi proxy,
    dan penanganan cerdas terhadap rate limit (HTTP 429).
    """

    BASE_URL: str = "https://api.quarterfull.io"
    SIGNUP_ENDPOINT: str = "/api/auth/signup"

    def __init__(
        self,
        accounts_file: str = "akun.txt",
        profile_generator: Optional[HighEntropyProfileGenerator] = None,
        ua_generator: Optional[UserAgentGenerator] = None,
        proxies: Optional[List[str]] = None,
        timeout: float = 8.0,
        max_retries_on_429: int = 3,
        retry_delay_429: float = 12.0,
    ) -> None:
        self.accounts_file: Path = Path(accounts_file)
        self.profile_gen: HighEntropyProfileGenerator = (
            profile_generator or HighEntropyProfileGenerator()
        )
        self.ua_gen: UserAgentGenerator = ua_generator or UserAgentGenerator()
        if proxies is not None:
            self.proxy_manager = ProxyManager()
            self.proxy_manager.parsed_proxies = [ProxyInfo(p) for p in proxies]
        else:
            self.proxy_manager = default_proxy_manager
        self.proxies: List[str] = [p.raw_url for p in self.proxy_manager.parsed_proxies]
        self._proxy_index: int = 0
        self.timeout: float = timeout
        self.max_retries_on_429: int = max_retries_on_429
        self.retry_delay_429: float = retry_delay_429
        self._file_lock = threading.Lock()

        # Muat daftar email lokal yang telah tersimpan untuk menghindari tabrakan
        self.existing_emails: set = set()
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                d = json.loads(line)
                                if "email" in d and d["email"]:
                                    self.existing_emails.add(str(d["email"]).strip().lower())
                            except Exception:
                                pass
            except Exception:
                pass

    def _get_next_proxy(self, country_code: Optional[str] = None) -> Optional[str]:
        """Mengambil proxy berikutnya secara satu kali pakai (pop_proxy) dengan targeting negara."""
        if not self.proxy_manager.has_proxies:
            return None
        sess_id = f"signup_{secrets.token_hex(4)}"
        return self.proxy_manager.pop_proxy(country_code=country_code, session_id=sess_id)

    def register_account(
        self,
        country_code: Optional[str] = "RANDOM",
        ua_mode: str = "okhttp",
        custom_device_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Menjalankan satu siklus registrasi akun lengkap:
        1. Generate profil akun ber-entropi tinggi sesuai negara terpilih atau acak.
        2. Generate User-Agent & Device ID unik.
        3. Bangun header HTTP selaras dengan locale, timezone, dan negara profil.
        4. Kirim request HTTP/2 ke /api/auth/signup dengan proteksi auto-retry pada HTTP 429.
        5. Simpan baris akun ke akun.txt jika sukses.

        :param country_code: Kode negara (contoh: 'ID', 'US', 'JP', dsb) atau 'RANDOM'.
        :return: Kamus data hasil registrasi akun.
        """
        profile: AccountProfile = self.profile_gen.generate_profile(
            country_code=country_code, existing_emails=self.existing_emails
        )
        user_agent: str = self.ua_gen.get_random_ua(mode=ua_mode)
        device_id: str = custom_device_id or IdentifierGenerator.generate_device_id()
        market_country: str = resolve_signup_market_country(profile.country)

        # Hitung tanggal lokal sesuai zona waktu negara yang bersangkutan
        try:
            if ZoneInfo is not None:
                current_date = datetime.now(ZoneInfo(profile.timezone)).strftime("%Y-%m-%d")
            else:
                current_date = datetime.now().strftime("%Y-%m-%d")
        except Exception:
            current_date = datetime.now().strftime("%Y-%m-%d")

        headers = {
            "host": "api.quarterfull.io",
            "user-agent": user_agent,
            "accept-encoding": "gzip",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "x-timezone": profile.timezone,
            "x-local-date": current_date,
            "x-user-country": profile.country,
            "x-user-raw-country": profile.country,
            "accept-language": profile.accept_language,
            "x-device-id": device_id,
            "content-type": "application/json",
            "accept": "application/json",
        }

        payload = {
            "email": profile.email,
            "password": profile.password,
            "password_confirm": profile.password,
            "birth_date": profile.birth_date,
            "gender": profile.gender,
            "is_agree_terms": True,
            "meta_attribution": {
                "source_site": "appsflyer"
            },
            "skip_email_verification": True,
            "signup_market_country": market_country,
            "signup_market_source": "auto",
        }

        logger.info(
            "Mendaftarkan akun: %s (Negara: %s | TZ: %s) | UA: %s | Device ID: %s...",
            profile.email,
            profile.country,
            profile.timezone,
            user_agent,
            device_id[:8],
        )

        # Mekanisme retry cerdas jika terkena rate limit (HTTP 429) atau tabrakan email (HTTP 400)
        last_error = ""
        max_attempts = max(self.max_retries_on_429, len(self.proxies) + 2, 8)
        for attempt in range(1, max_attempts + 1):
            proxy = self._get_next_proxy(country_code=profile.country)
            connect_to = min(3.5, self.timeout)
            client_kwargs: Dict[str, Any] = {
                "base_url": self.BASE_URL,
                "http2": False if proxy else True,
                "timeout": httpx.Timeout(self.timeout, connect=connect_to, read=self.timeout),
            }
            if proxy:
                client_kwargs["proxy"] = proxy

            with httpx.Client(**client_kwargs) as client:
                try:
                    response = client.post(self.SIGNUP_ENDPOINT, headers=headers, json=payload)

                    if response.status_code == 429:
                        last_error = response.text
                        if proxy:
                            # Picu rotasi IP pada proxy yang terkena limit
                            self.proxy_manager.mark_failed(proxy, "HTTP 429 Rate Limit")

                        if attempt < max_attempts:
                            if self.proxies:
                                logger.warning("[429] Rate limit IP terdeteksi, memutar proxy dan beralih ke slot berikutnya...")
                                time.sleep(random.uniform(1.0, 2.2))
                            else:
                                cooldown = self.retry_delay_429 * attempt
                                logger.warning("[429] Rate limit IP terdeteksi! Menunggu cooldown %.1fs sebelum retry...", cooldown)
                                time.sleep(cooldown)
                            continue

                    # Auto-recovery tabrakan email yang sudah terdaftar di backend
                    resp_text_lower = response.text.lower()
                    if response.status_code == 400 and (
                        "already registered" in resp_text_lower
                        or "already exists" in resp_text_lower
                        or "registered" in resp_text_lower
                    ):
                        logger.warning(
                            "[Email Tabrakan] Email '%s' sudah terdaftar di server. Membuat variasi baru dan mencoba lagi...",
                            profile.email,
                        )
                        self.existing_emails.add(profile.email.lower())
                        profile = self.profile_gen.generate_profile(
                            country_code=country_code, existing_emails=self.existing_emails
                        )
                        payload["email"] = profile.email
                        payload["password"] = profile.password
                        payload["password_confirm"] = profile.password
                        payload["birth_date"] = profile.birth_date
                        payload["gender"] = profile.gender
                        time.sleep(0.5)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    access_token = data.get("access_token", "")
                    refresh_token = data.get("refresh_token", "")
                    user_info = data.get("user", {})
                    user_id = user_info.get("id")

                    desired_nickname = f"{profile.first_name} {profile.last_name}".strip()

                    # Mengubah nama pengguna (nickname) default bot menjadi nama asli di server API
                    if access_token and desired_nickname:
                        try:
                            patch_headers = dict(headers)
                            patch_headers["authorization"] = f"Bearer {access_token}"
                            patch_headers["content-type"] = "application/json"
                            patch_resp = client.patch(
                                "/api/auth/profile",
                                headers=patch_headers,
                                json={"nickname": desired_nickname},
                            )
                            if patch_resp.status_code == 200:
                                logger.info("Nama profil berhasil diubah menjadi: '%s'", desired_nickname)
                        except Exception as patch_exc:
                            logger.warning("Gagal memperbarui nickname akun baru: %s", patch_exc)

                    account_record = {
                        "email": profile.email,
                        "password": profile.password,
                        "nickname": desired_nickname,
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "user_id": user_id,
                        "device_id": device_id,
                        "user_agent": user_agent,
                        "country": profile.country,
                        "created_at": datetime.now().isoformat(),
                    }

                    # Simpan akun ke file akun.txt (format 1 JSON per baris)
                    self._save_account_to_file(account_record)
                    if proxy:
                        self.proxy_manager.mark_used(proxy)

                    logger.info(
                        "Registrasi BERHASIL! User ID: %s | Email: %s | Negara: %s",
                        user_id,
                        profile.email,
                        profile.country,
                    )
                    return {
                        "status": "success",
                        "account": account_record,
                        "raw_response": data,
                    }

                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429 and attempt < self.max_retries_on_429:
                        cooldown = self.retry_delay_429 * attempt
                        time.sleep(cooldown)
                        continue

                    # Jika proxy mengembalikan status 400 No IPs in selected country
                    if exc.response.status_code == 400 and ("No IPs" in exc.response.text or "selected country" in exc.response.text):
                        logger.warning(
                            "[Proxy Warning] IP negara %s tidak tersedia di proxy (400 No IPs). Mencoba lagi menggunakan proxy negara lain...",
                            profile.country,
                        )
                        for alt_cc in ["US", "ID", "GB", "DE", "JP", "FR"]:
                            if alt_cc.upper() == profile.country.upper():
                                continue
                            alt_proxy = self.proxy_manager.get_proxy(country_code=alt_cc)
                            alt_kwargs = {
                                "base_url": self.BASE_URL,
                                "http2": False if alt_proxy else True,
                                "timeout": self.timeout,
                            }
                            if alt_proxy:
                                alt_kwargs["proxy"] = alt_proxy

                            try:
                                with httpx.Client(**alt_kwargs) as alt_client:
                                    alt_resp = alt_client.post(self.SIGNUP_ENDPOINT, headers=headers, json=payload)
                                    if alt_resp.status_code == 200:
                                        data = alt_resp.json()
                                        access_token = data.get("access_token", "")
                                        refresh_token = data.get("refresh_token", "")
                                        user_info = data.get("user", {})
                                        user_id = user_info.get("id")
                                        desired_nickname = f"{profile.first_name} {profile.last_name}".strip()
                                        if access_token and desired_nickname:
                                            try:
                                                patch_headers = dict(headers)
                                                patch_headers["authorization"] = f"Bearer {access_token}"
                                                patch_headers["content-type"] = "application/json"
                                                alt_client.patch(
                                                    "/api/auth/profile",
                                                    headers=patch_headers,
                                                    json={"nickname": desired_nickname},
                                                )
                                            except Exception:
                                                pass

                                        account_record = {
                                            "email": profile.email,
                                            "password": profile.password,
                                            "nickname": desired_nickname,
                                            "access_token": access_token,
                                            "refresh_token": refresh_token,
                                            "user_id": user_id,
                                            "device_id": device_id,
                                            "user_agent": user_agent,
                                            "country": profile.country,
                                            "created_at": datetime.now().isoformat(),
                                        }
                                        self._save_account_to_file(account_record)
                                        logger.info(
                                            "Registrasi BERHASIL via proxy alternatif %s! User ID: %s | Email: %s",
                                            alt_cc,
                                            user_id,
                                            profile.email,
                                        )
                                        return {
                                            "status": "success",
                                            "account": account_record,
                                            "raw_response": data,
                                        }
                            except Exception as alt_exc:
                                logger.debug("Proxy alternatif %s gagal: %s", alt_cc, alt_exc)
                                continue

                    logger.error(
                        "Registrasi GAGAL [HTTP %d]: %s",
                        exc.response.status_code,
                        exc.response.text,
                    )
                    return {
                        "status": "error",
                        "code": exc.response.status_code,
                        "error": exc.response.text,
                        "profile": asdict(profile),
                    }
                except Exception as exc:
                    if proxy and is_dead_or_proxy_error(exc):
                        self.proxy_manager.mark_failed(proxy, exc)
                        logger.warning(
                            "[Proxy Error] Proxy %s tidak merespon (%s). Mencoba slot proxy berikutnya...",
                            proxy,
                            exc,
                        )
                        time.sleep(0.3)
                        continue

                    logger.error("Terjadi exception pada pendaftaran: %s", exc)
                    return {
                        "status": "exception",
                        "error": str(exc),
                        "profile": asdict(profile),
                    }

        return {
            "status": "error",
            "code": 429,
            "error": last_error or "Too many requests. Cooldown habis.",
            "profile": asdict(profile),
        }

    def _save_account_to_file(self, account_record: Dict[str, Any]) -> None:
        """Menyimpan record akun secara thread-safe / append ke berkas JSON Lines."""
        line = json.dumps(account_record, ensure_ascii=False)
        with self._file_lock:
            with open(self.accounts_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if "email" in account_record and account_record["email"]:
                self.existing_emails.add(str(account_record["email"]).strip().lower())

    def register_batch_concurrent(
        self,
        total_count: int,
        concurrency: int = 5,
        country_code: str = "RANDOM",
        ua_mode: str = "okhttp",
        on_result: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Mendaftarkan banyak akun secara simultan / multi-session menggunakan ThreadPoolExecutor.
        :param total_count: Total akun yang ingin didaftarkan.
        :param concurrency: Jumlah thread / sesi paralel yang berjalan bersamaan.
        :param country_code: Kode negara (ID, US, JP, dll) atau 'RANDOM'.
        :param ua_mode: Mode UA ('okhttp', 'dalvik', 'webview').
        :param on_result: Callback opsional on_result(index, res_dict).
        :return: List berisi seluruh hasil pendaftaran akun.
        """
        concurrency = max(1, min(concurrency, total_count))
        results: List[Dict[str, Any]] = []

        def _worker_task(idx: int) -> Dict[str, Any]:
            # Jeda stagger awal acak agar thread tidak mengirim request di milidetik yang sama persis
            time.sleep(random.uniform(0.1, 0.5))
            res = self.register_account(country_code=country_code, ua_mode=ua_mode)
            # Smart retry jika terkena 429 (Rate Limit) atau 400 (No IPs)
            if res.get("status") != "success":
                err_text = str(res.get("error", ""))
                if "429" in err_text or "Too many requests" in err_text:
                    time.sleep(random.uniform(2.0, 4.0))
                    retry_res = self.register_account(country_code=country_code, ua_mode=ua_mode)
                    if retry_res.get("status") == "success":
                        res = retry_res
                elif "No IPs" in err_text or "400" in err_text:
                    retry_res = self.register_account(country_code="US", ua_mode=ua_mode)
                    if retry_res.get("status") == "success":
                        res = retry_res
            if on_result:
                try:
                    on_result(idx, res)
                except Exception:
                    pass
            return res

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_worker_task, i) for i in range(1, total_count + 1)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as exc:
                    results.append({"status": "error", "error": str(exc)})

        return results


def print_safe(text: str) -> None:
    """Mencetak teks ke stdout dengan penanganan encoding aman untuk terminal Windows."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


if __name__ == "__main__":
    # Konfigurasi stdout UTF-8 pada konsol Windows
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Auto Signup & High Entropy Profile Generator 50 Negara (Toodat / Quarterfull)"
    )
    parser.add_argument(
        "--country",
        "-c",
        type=str,
        default=None,
        help="Kode 2-huruf negara (cth: ID, US, JP, DE) atau 'RANDOM' untuk acak 50 negara (default: RANDOM)",
    )
    parser.add_argument(
        "--count",
        "-n",
        type=int,
        default=1,
        help="Jumlah akun yang akan didaftarkan (default: 1)",
    )
    parser.add_argument(
        "--ua-mode",
        type=str,
        default="okhttp",
        choices=["okhttp", "dalvik", "webview"],
        help="Mode User-Agent (okhttp, dalvik, webview) [default: okhttp]",
    )
    parser.add_argument(
        "--concurrency",
        "-j",
        type=int,
        default=1,
        help="Jumlah sesi pendaftaran paralel / simultan (default: 1)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    print_safe("=" * 80)
    print_safe("TOODAT / QUARTERFULL - AUTO SIGNUP 50 NEGARA & HIGH ENTROPY GENERATOR")
    print_safe("=" * 80)

    # Interaksi prompt CLI jika --country tidak diberikan via argumen dan terminal interaktif
    selected_country = args.country
    if selected_country is None:
        if sys.stdin.isatty():
            try:
                user_input = input(
                    "Pilih kode negara (contoh: ID, US, JP, DE) atau ketik 'RANDOM' untuk campuran 50 negara [default RANDOM]: "
                ).strip()
                selected_country = user_input.upper() if user_input else "RANDOM"
            except (EOFError, KeyboardInterrupt):
                selected_country = "RANDOM"
        else:
            selected_country = "RANDOM"

    print_safe(f"\n[Konfigurasi Target: Negara = {selected_country} | Jumlah Akun = {args.count} | Sesi Simultan = {args.concurrency} | UA = {args.ua_mode}]")

    profile_gen = HighEntropyProfileGenerator()
    ua_gen = UserAgentGenerator()

    # 1. Demonstrasi Sampel Profil dari Beragam Negara
    print_safe("\n--- Sampel Profil dari 5 Negara Berbeda (Termasuk Script Non-Latin) ---")
    demo_countries = ["ID", "JP", "SA", "RU", "DE"]
    for code in demo_countries:
        p = profile_gen.generate_profile(country_code=code)
        print_safe(f"\n[{code} - {p.timezone}]")
        print_safe(f"  Nama Asli       : {p.first_name} {p.last_name}")
        print_safe(f"  Email Unik      : {p.email}")
        print_safe(f"  Password        : {p.password}")
        print_safe(f"  Tanggal Lahir   : {p.birth_date} | Gender: {p.gender}")
        print_safe(f"  Accept-Language : {p.accept_language}")

    # 2. Eksekusi Pendaftaran Akun ke Live API
    runner = RegistrationRunner(accounts_file="akun.txt", profile_generator=profile_gen, ua_generator=ua_gen)

    success_count = 0
    if args.concurrency > 1:
        print_safe(f"\n--- Menjalankan Pendaftaran {args.count} Akun ({args.concurrency} Sesi Paralel Simultan) ---")
        lock = threading.Lock()
        def on_res(idx: int, res: Dict[str, Any]) -> None:
            global success_count
            with lock:
                if res.get("status") == "success":
                    success_count += 1
                    acc = res["account"]
                    print_safe(f"  [OK] Akun #{idx}: {acc.get('email')} | Negara: {acc.get('country')} | User ID: {acc.get('user_id')}")
                else:
                    print_safe(f"  [GAGAL] Akun #{idx}: {res.get('error')}")

        runner.register_batch_concurrent(
            total_count=args.count,
            concurrency=args.concurrency,
            country_code=selected_country,
            ua_mode=args.ua_mode,
            on_result=on_res,
        )
    else:
        print_safe(f"\n--- Menjalankan Pendaftaran {args.count} Akun ke API Quarterfull ---")
        for i in range(1, args.count + 1):
            print_safe(f"\n[Akun #{i} / {args.count}]")
            result = runner.register_account(country_code=selected_country, ua_mode=args.ua_mode)

            if result.get("status") == "success":
                acc = result["account"]
                success_count += 1
                print_safe("  Status       : BERHASIL (201 Created)")
                print_safe(f"  User ID      : {acc.get('user_id')}")
                print_safe(f"  Email        : {acc.get('email')}")
                print_safe(f"  Password     : {acc.get('password')}")
                print_safe(f"  Negara       : {acc.get('country')}")
                print_safe(f"  Access Token : {acc.get('access_token')[:32]}...")
                print_safe(f"  Device ID    : {acc.get('device_id')}")
                print_safe(f"  User Agent   : {acc.get('user_agent')}")
            else:
                print_safe(f"  Status       : GAGAL ({result.get('error')})")

    print_safe("\n" + "=" * 80)
    print_safe(f"PROSES SELESAI: {success_count}/{args.count} Akun Berhasil Didaftarkan dan Disimpan ke 'akun.txt'")
    print_safe("=" * 80)
