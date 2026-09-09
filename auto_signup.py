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
import json
import logging
import random
import re
import secrets
import string
import sys
import unicodedata
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from faker import Faker
import httpx

from session_manager import IdentifierGenerator
from proxy_manager import ProxyManager, ProxyInfo, default_proxy_manager

# Konfigurasi logging
logger = logging.getLogger("AutoSignup")

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
        or "malformed reply" in err_str
        or "connection reset" in err_str
        or "remote end closed" in err_str
        or "bad gateway" in err_str
        or "502" in err_str
        or "503" in err_str
        or "504" in err_str
        or "no ips" in err_str
    )


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

    # Distribusi domain email populer
    EMAIL_DOMAINS: List[Tuple[str, float]] = [
        ("gmail.com", 0.40),
        ("yahoo.com", 0.15),
        ("outlook.com", 0.15),
        ("hotmail.com", 0.12),
        ("icloud.com", 0.10),
        ("proton.me", 0.08),
    ]

    # Separator nama untuk format email
    SEPARATORS: List[str] = [".", "_", ""]

    # Karakter simbol aman untuk password
    PASSWORD_SPECIAL_CHARS: str = "!@#$%^&*"

    def __init__(self) -> None:
        """Inisialisasi cache Faker untuk efisiensi memori."""
        self._faker_cache: Dict[str, Faker] = {}
        # Faker universal untuk fallback nama Latin pada script non-Latin murni
        self._universal_faker: Faker = Faker("en_US")

    def _get_faker(self, country_code: str) -> Faker:
        """Mengambil atau menginisialisasi Faker instance untuk negara tertentu."""
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
    def generate_unique_hash(cls) -> str:
        """
        Menghasilkan komponen hash unik:
        Kombinasi 4-6 karakter acak (hex/base36) + 2-3 digit angka acak.
        Total variasi kombinasi > 1 Milyar kemungkinan.
        """
        hash_length = secrets.choice([4, 5, 6])
        b36_chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        rand_prefix = "".join(secrets.choice(b36_chars) for _ in range(hash_length))

        num_digits = secrets.choice([2, 3])
        rand_digits = "".join(secrets.choice(string.digits) for _ in range(num_digits))

        return f"{rand_prefix}{rand_digits}"

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

    def generate_profile(self, country_code: Optional[str] = None) -> AccountProfile:
        """
        Menghasilkan satu profil akun unik ber-entropi tinggi dari katalog 50 negara.

        :param country_code: Kode 2-huruf negara (cth: 'ID', 'JP', 'US').
                             Jika None, 'RANDOM', atau 'ALL', dipilih secara acak dari 50 negara.
        """
        all_countries = list(COUNTRY_CONFIG.keys())

        if not country_code or country_code.upper() in ("RANDOM", "ALL"):
            selected_country = secrets.choice(all_countries)
        else:
            selected_country = country_code.upper()
            if selected_country not in COUNTRY_CONFIG:
                logger.warning(
                    "Kode negara '%s' tidak ditemukan dalam katalog. Menggunakan negara acak.",
                    selected_country,
                )
                selected_country = secrets.choice(all_countries)

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

        # Buat email dengan formula: {first_name_clean}{separator}{last_name_clean}_{unique_hash}@{domain}
        separator = secrets.choice(self.SEPARATORS)
        unique_hash = self.generate_unique_hash()

        domains, domain_weights = zip(*self.EMAIL_DOMAINS)
        domain = random.choices(domains, weights=domain_weights, k=1)[0]

        email = f"{first_name_clean}{separator}{last_name_clean}_{unique_hash}@{domain}"

        # Password 12-16 karakter
        pw_len = secrets.choice(range(12, 17))
        password = self.generate_password(length=pw_len)

        # Tanggal lahir & gender
        birth_date = self.generate_birth_date()
        gender = self.generate_gender()

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
    """Membaca daftar proxy dari file jika tersedia."""
    p = Path(file_path)
    if not p.exists():
        return []
    proxies = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            px = line.strip()
            if px and not px.startswith("#"):
                if not (px.startswith("http://") or px.startswith("https://") or px.startswith("socks5://")):
                    px = f"http://{px}"
                proxies.append(px)
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
        timeout: float = 6.0,
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

    def _get_next_proxy(self, country_code: Optional[str] = None) -> Optional[str]:
        """Mengambil proxy berikutnya. Jika Bright Data, disesuaikan ke country_code dengan IP unik per sesi."""
        if not self.proxy_manager.has_proxies:
            return None
        sess_id = f"signup_{secrets.token_hex(4)}"
        return self.proxy_manager.get_proxy(country_code=country_code, session_id=sess_id)

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
        # Pastikan proxy segar selalu tersedia sebelum registrasi akun
        self.proxy_manager.ensure_fresh_proxies()

        profile: AccountProfile = self.profile_gen.generate_profile(country_code=country_code)
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

        # Mekanisme retry cerdas: coba proxy kandidat (maks 5 proxy), jika proxy bermasalah langsung prune & coba proxy lain.
        # Jika semua proxy gagal atau terkena proxy auth/socks/malformed, fallback ke koneksi direct.
        max_proxy_attempts = 5 if self.proxies else 1
        proxy_candidates: List[Optional[str]] = []
        for _ in range(max_proxy_attempts):
            p = self._get_next_proxy(country_code=profile.country)
            if p and p not in proxy_candidates:
                proxy_candidates.append(p)
        # Tambahkan direct connection (None) sebagai fallback terakhir yang dijamin tembus
        proxy_candidates.append(None)

        last_error = ""
        for p_idx, proxy in enumerate(proxy_candidates, start=1):
            client_kwargs: Dict[str, Any] = {
                "base_url": self.BASE_URL,
                "http2": False if proxy else True,
                "timeout": httpx.Timeout(5.0 if proxy else 15.0),
            }
            if proxy:
                client_kwargs["proxy"] = proxy

            try:
                with httpx.Client(**client_kwargs) as client:
                    response = client.post(self.SIGNUP_ENDPOINT, headers=headers, json=payload)

                    if response.status_code == 429:
                        last_error = response.text
                        logger.warning("[429] Rate limit IP terdeteksi, beralih ke kandidat IP/proxy berikutnya...")
                        continue

                    # Tangani 400 No IPs in selected country atau 407 Proxy Authentication Required dari proxy
                    if response.status_code == 407 or (response.status_code == 400 and ("no ips" in response.text.lower() or "proxy" in response.text.lower())):
                        if proxy:
                            self.proxy_manager.remove_bad_proxy(proxy)
                        logger.warning("[Proxy %s] Status %d terdeteksi, eliminasi proxy & rotasi...", proxy, response.status_code)
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

                    logger.info(
                        "Registrasi BERHASIL! User ID: %s | Email: %s | Negara: %s (Koneksi: %s)",
                        user_id,
                        profile.email,
                        profile.country,
                        proxy or "Direct",
                    )
                    return {
                        "status": "success",
                        "account": account_record,
                        "raw_response": data,
                    }

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    last_error = exc.response.text
                    continue
                if is_proxy_error(exc) and proxy:
                    self.proxy_manager.remove_bad_proxy(proxy)
                    logger.warning("[Proxy Error %s] Status %s: eliminasi & coba koneksi lain...", proxy, exc.response.status_code)
                    continue

                logger.error("Registrasi GAGAL [HTTP %d]: %s", exc.response.status_code, exc.response.text)
                return {
                    "status": "error",
                    "code": exc.response.status_code,
                    "error": exc.response.text,
                    "profile": asdict(profile),
                }
            except Exception as exc:
                if proxy:
                    self.proxy_manager.remove_bad_proxy(proxy)
                if is_proxy_error(exc) and p_idx < len(proxy_candidates):
                    logger.warning("[Proxy Fail %s] %s -> Eliminasi & coba kandidat ke-%d...", proxy, exc, p_idx + 1)
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
        with open(self.accounts_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


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

    print_safe(f"\n[Konfigurasi Target: Negara = {selected_country} | Jumlah Akun = {args.count} | UA = {args.ua_mode}]")

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
    print_safe(f"\n--- Menjalankan Pendaftaran {args.count} Akun ke API Quarterfull ---")
    runner = RegistrationRunner(accounts_file="akun.txt", profile_generator=profile_gen, ua_generator=ua_gen)

    success_count = 0
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
