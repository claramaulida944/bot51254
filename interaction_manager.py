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

from proxy_manager import (
    ProxyManager,
    ProxyInfo,
    default_proxy_manager,
    SUPPORTED_QUARTERFULL_COUNTRIES,
    sanitize_proxy_url,
    is_dead_or_proxy_error,
)

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
    """Membaca daftar proxy dari berkas dan membersihkan format kotor."""
    proxies = []
    p = Path(filepath)
    if not p.exists():
        return proxies

    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = sanitize_proxy_url(line)
            if cleaned:
                proxies.append(cleaned)
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
    def _resolve_proxy(cls, proxy: Optional[str] = None) -> Optional[str]:
        """Menyediakan proxy aktif secara konsisten untuk melindungi IP lokal."""
        if proxy:
            return proxy
        if default_proxy_manager and default_proxy_manager.has_proxies:
            return default_proxy_manager.get_proxy()
        return None

    @classmethod
    def get_novel_details(cls, novel_id: str, proxy: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Mengambil metadata novel untuk verifikasi target via proxy."""
        active_proxy = cls._resolve_proxy(proxy)
        url = f"{cls.BASE_URL}/api/v1/novels/{novel_id}"
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": "okhttp/4.12.0",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
        }
        try:
            with httpx.Client(http2=False if active_proxy else True, proxy=active_proxy, timeout=12.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal mengambil detail novel: {exc}")
        return None

    @classmethod
    def get_author_details(cls, author_id: str, proxy: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Mengambil metadata author profil dan relasi sosial via proxy."""
        active_proxy = cls._resolve_proxy(proxy)
        url = f"{cls.BASE_URL}/api/v1/author-profiles/public/{author_id}/profile"
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": "okhttp/4.12.0",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
        }
        try:
            with httpx.Client(http2=False if active_proxy else True, proxy=active_proxy, timeout=12.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal mengambil detail author: {exc}")
        return None

    @classmethod
    def get_social_relationship(cls, author_id: str, token: str, proxy: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Mengambil status relasi sosial & jumlah followers akun via proxy."""
        active_proxy = cls._resolve_proxy(proxy)
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
            with httpx.Client(http2=False if active_proxy else True, proxy=active_proxy, timeout=12.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal mengambil status relasi: {exc}")
        return None

    @classmethod
    def get_novel_chapters(cls, novel_id: str, proxy: Optional[str] = None) -> List[Dict[str, Any]]:
        """Mengambil daftar bab novel lengkap dari API backend via proxy."""
        active_proxy = cls._resolve_proxy(proxy)
        url = f"{cls.BASE_URL}/api/v1/novels/{novel_id}/chapters?order=asc&limit=100"
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": "okhttp/4.12.0",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
        }
        try:
            with httpx.Client(http2=False if active_proxy else True, proxy=active_proxy, timeout=15.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        except Exception as exc:
            logger.debug(f"Gagal mengambil daftar bab: {exc}")
        return []

    @classmethod
    def check_remix_eligibility(cls, novel_id: str, proxy: Optional[str] = None) -> bool:
        """Memeriksa apakah novel memenuhi kualifikasi untuk fitur Remix Cerita via proxy."""
        active_proxy = cls._resolve_proxy(proxy)
        url = f"{cls.BASE_URL}/api/v1/studio-cursor/reader-remixes/eligibility/{novel_id}"
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": "okhttp/4.12.0",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
        }
        try:
            with httpx.Client(http2=False if active_proxy else True, proxy=active_proxy, timeout=12.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("eligible", False)
        except Exception as exc:
            logger.debug(f"Gagal memeriksa eligibilitas remix: {exc}")
        return False

    @classmethod
    def get_remix_roots(cls, novel_id: str, token: str, proxy: Optional[str] = None) -> List[Dict[str, Any]]:
        """Mengambil daftar akar seri (series roots) remix yang tersedia untuk novel via proxy."""
        active_proxy = cls._resolve_proxy(proxy)
        url = f"{cls.BASE_URL}/api/v1/studio-cursor/reader-remixes/series/novel/{novel_id}/roots?limit=8"
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": "okhttp/4.12.0",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "authorization": f"Bearer {token}",
        }
        try:
            with httpx.Client(http2=False if active_proxy else True, proxy=active_proxy, timeout=12.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("items", []) if isinstance(data, dict) else []
        except Exception as exc:
            logger.debug(f"Gagal mengambil roots remix: {exc}")
        return []


class RemixModeGenerator:
    """
    Generator opsi, atribut, dan prompt remix dengan variasi tinggi untuk 7 mode resmi:
    1. status_window_next_chapter (Jendela Status RPG / Level Up)
    2. attractive_existing_character (Penampilan Baru Tokoh yang Ada)
    3. self_insert_next_chapter (Intervensi Tokoh Baru / Pembaca)
    4. attractive_self_insert (Tokoh Baru Memikat / Daya Tarik Spesial)
    5. mutual_fate_next_chapter (Kartu Takdir Bersama Dua Karakter)
    6. constellation_next_chapter (Rasi Bintang / Sponsor Transenden)
    7. character_regression (Regresi Waktu / Membawa Memori Masa Depan)
    """

    AVAILABLE_MODES = [
        "status_window_next_chapter",
        "attractive_existing_character",
        "self_insert_next_chapter",
        "attractive_self_insert",
        "mutual_fate_next_chapter",
        "constellation_next_chapter",
        "character_regression",
    ]

    CHARACTERS_POOL = [
        "Julian", "April", "Clara", "Ramy", "Harris", "Elena", "Denis",
        "Letisa", "Arga", "Reyhan", "Karin", "Maya", "Dion", "Edrick",
        "Mira", "Thomas", "Sugi", "Zahra", "Aditya", "Nadine", "Lian",
        "Alana", "Damian", "Devan", "Bima", "Tara", "Revan", "Viona"
    ]

    EXP_RULES = [
        "Saat berhasil mengelabui lawan dalam konfrontasi",
        "Ketika selamat dari situasi bahaya kritis",
        "Saat mengungkap rahasia tersembunyi kubu musuh",
        "Setiap kali berhasil melindungi orang lain dari maut",
        "Saat membuat keputusan strategis yang mengubah alur cerita",
        "Ketika berlatih keras dalam kesendirian hingga fajar",
        "Saat berhasil menegosiasikan kesepakatan berisiko tinggi",
        "Ketika menahan rasa sakit fisik demi tujuan mulia",
        "Saat menemukan petunjuk konspirasi keluarga tersembunyi",
        "Setiap kali menolak tunduk pada tekanan kekuasaan tiran",
        "Ketika membongkar kebohongan orang terdekat",
        "Saat berhasil mengumpulkan bukti dokumen rahasia",
    ]

    GROWTH_STATS_POOL = [
        "Wawasan", "Pesona", "Keberuntungan", "Kekuatan", "Ketangkasan",
        "Kecerdasan", "Daya Tahan", "Otoritas", "Mana", "Insting",
        "Karisma", "Pengaruh", "Konsentrasi", "Refleks", "Ketabahan",
        "Aura Pembunuh", "Intuisi Taktis", "Ketajaman Mental"
    ]

    STATUS_TITLES = [
        "Status Pertumbuhan", "Jendela Status Kebangkitan", "Sistem Transenden Protagonis",
        "Panel Kemampuan Tersembunyi", "Status Warisan Leluhur", "Jendela Takdir Baru",
        "Sistem Penakluk Bayangan", "Panel Otoritas Mutlak"
    ]

    APPEARANCES_POOL = [
        "Mengenakan mantel wol hitam panjang dengan tatapan dingin penuh karisma",
        "Gaya rambut baru yang lebih rapi dengan setelan jas formal berkelas tinggi",
        "Berpakaian serba hitam dengan aura misterius dan bekas luka tipis di alis",
        "Mengenakan seragam militer taktis dengan postur tegap dan senyum tipis memikat",
        "Gaun sutra malam elegan dengan liontin perak kuno yang memancarkan cahaya redup",
        "Tampil sederhana dengan kemeja putih kasual namun memiliki tatapan tajam tak terbantahkan",
        "Mengenakan jubah bangsawan berkerah bulu dengan pedang berukir di pinggang",
        "Rambut terurai bebas dengan mata perak yang tampak berkilat di bawah temaram lampu",
        "Setelan jas abu-abu arang dengan jam saku perak antik warisan keluarga",
        "Pakaian kasual bertudung hitam yang menyamarkan ekspresi wajah di tengah keramaian"
    ]

    ROLES_POOL = [
        "Detektif swasta independen yang disewa oleh informan rahasia",
        "Dokter ahli bedah lapangan yang terjebak di tengah perselisihan keluarga konglomerat",
        "Pengawal pribadi baru yang diam-diam memiliki agenda penyelidikan pribadi",
        "Pialang saham cerdik yang mengetahui skandal finansial tersembunyi",
        "Arsitek muda pemegang cetak biru rahasia bangunan persembunyian",
        "Mantan agen intelijen yang sedang mencari tempat suaka aman",
        "Kolektor barang antik penyimpan artefak perjanjian masa lalu",
        "Diplomat netral yang datang membawa peringatan bahaya dari luar negeri",
        "Penyusup bayaran yang berniat membatalkan kontrak demi membantu tokoh utama",
        "Asisten pribadi berdarah dingin yang sebenarnya adalah pewaris yang disembunyikan"
    ]

    BACKGROUNDS_POOL = [
        "Memiliki koneksi rahasia dengan sindikat bawah tanah dan tetap tenang di bawah todongan senjata.",
        "Hanya bertindak atas dasar bukti objektif, namun memiliki kode moral ketat untuk tidak menyakiti orang tak bersalah.",
        "Mengetahui skenario besar musuh dan berusaha mengubah arah takdir sebelum titik kehancuran.",
        "Tampak ramah dan bersahaja dari luar, namun memiliki insting tempur dan deduksi luar biasa tajam.",
        "Memiliki masa lalu yang kelam dengan keluarga bangsawan utama dan ingin menuntut keadilan sejati."
    ]

    CHARMS_POOL = [
        "Memiliki daya tarik magnetis yang sulit diabaikan serta wibawa tenang yang mendominasi seisi ruangan.",
        "Tutur kata yang santun namun penuh perhitungan strategis, memikat sekaligus membuat lawan bicara waspada.",
        "Tatapan mata tajam yang sanggup membaca ketakutan tersembunyi, diimbangi senyum percaya diri yang menawan.",
        "Aura misterius yang membangkitkan rasa ingin tahu sang protagonis untuk terus mendekat."
    ]

    FATE_RELATIONS = [
        "Keduanya terikat sumpah darah leluhur di mana rasa sakit satu sama lain saling beresonansi secara fisik.",
        "Keduanya memiliki sepasang liontin kembar antik yang mulai berpendar saat salah satu menghadapi bahaya maut.",
        "Takdir masa lalu yang bertentangan kini memaksa mereka berbagi satu-satunya kunci keselamatan.",
        "Sebuah ramalan kuno menyatakan bahwa keberhasilan yang satu bergantung mutlak pada kesetiaan yang lain.",
        "Garis keturunan mereka saling terkait dalam rahasia kematian misterius generasi terdahulu."
    ]

    CONSTELLATIONS = [
        "Hakim Malam Abadi", "Pengamat Ujung Horison", "Naga Perak dari Utara",
        "Ratu Kegelapan Tanpa Mahkota", "Penjaga Rahasia Samudra", "Arsitek Bintang Pertama",
        "Pemberontak Langit Kelabu", "Penakluk Waktu yang Hilang"
    ]

    CONSTELLATION_MESSAGES = [
        "'Tunjukkan padaku apakah tekadmu sanggup melampaui harga yang harus dibayar.'",
        "'Aku telah mengawasimu sejak hari pertama. Terimalah berkah ini jika nyalimu belum surut.'",
        "'Jalan di depanmu dipenuhi jebakan duri pengkhianatan, namun sorot mataku takkan berpaling.'",
        "'Pilihanmu di detik ini akan menentukan apakah namamu tercatat abadi di antara bintang.'",
        "'Keberanian sejati bukan ketiadaan rasa takut, melainkan langkah maju di tengah kegelapan.'"
    ]

    REGRESSION_TURNING_POINTS = [
        "Kembali ke momen 24 jam sebelum dokumen perjanjian kepemilikan ditandatangani secara paksa.",
        "Membawa seluruh ingatan kegagalan di masa depan untuk mencegah kematian tragis orang terkasih.",
        "Mengetahui siapa dalang pengkhianat di lingkaran terdekat sebelum racun sempat dituangkan.",
        "Menyadari bahwa musuh terbesar selama ini bukanlah sosok yang tampak di garis depan.",
        "Mengulangi malam lelang sita aset dengan persiapan finansial dan kartu as tersembunyi."
    ]

    @classmethod
    def generate(
        cls,
        mode: Optional[str] = None,
        target_character: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Menghasilkan konfigurasi remix, parameter PUT, dan prompt streaming acak dengan variasi tinggi."""
        chosen_mode = mode if mode in cls.AVAILABLE_MODES else random.choice(cls.AVAILABLE_MODES)
        char = target_character if target_character else random.choice(cls.CHARACTERS_POOL)

        if chosen_mode == "status_window_next_chapter":
            rule = random.choice(cls.EXP_RULES)
            stats = random.sample(cls.GROWTH_STATS_POOL, 3)
            title = random.choice(cls.STATUS_TITLES)
            msg = (
                f"[INTERVENSI JENDELA STATUS]\n"
                f"Tokoh target: {char}\n"
                f"Syarat EXP: {rule}\n"
                f"Statistik pertumbuhan: {', '.join(stats)}\n"
                f"Tulis satu bab berikutnya yang utuh, tempat aturan ini benar-benar mengubah kejadian dan pilihan."
            )
            return {
                "mode": chosen_mode,
                "title": "Jendela Status RPG (Level Up)",
                "summary": f"Tokoh: {char} | Syarat: {rule[:35]}... | Stats: {', '.join(stats)}",
                "stream_message": msg,
                "put_endpoint": "status-window",
                "put_payload": {
                    "target_character": char,
                    "experience_rule": rule,
                    "growth_stats": stats,
                    "status_title": title,
                    "profile_visible": True,
                },
            }

        elif chosen_mode == "attractive_existing_character":
            app = random.choice(cls.APPEARANCES_POOL)
            msg = (
                f"Tokoh yang akan diubah: {char}\n"
                f"Penampilan yang diinginkan: {app}\n"
                f"Buat bab berikutnya sekarang dengan penampilan baru tokoh ini."
            )
            return {
                "mode": chosen_mode,
                "title": "Ubah Penampilan Tokoh",
                "summary": f"Tokoh: {char} | Penampilan: {app[:40]}...",
                "stream_message": msg,
                "put_endpoint": "existing-character-attractive",
                "put_payload": {
                    "target_character": char,
                    "appearance": app,
                    "profile_visible": False,
                },
            }

        elif chosen_mode == "self_insert_next_chapter":
            role = random.choice(cls.ROLES_POOL)
            bg = random.choice(cls.BACKGROUNDS_POOL)
            msg = (
                f"[INTERVENSI TOKOH BARU]\n"
                f"Peran: {role}\n"
                f"Latar Belakang: {bg}\n"
                f"Masukkan tokoh ini ke dalam jalan cerita bab berikutnya sebagai figur kunci yang mempengaruhi keputusan tokoh utama."
            )
            return {
                "mode": chosen_mode,
                "title": "Intervensi Tokoh Baru",
                "summary": f"Peran: {role[:40]}... | Sifat: {bg[:35]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }

        elif chosen_mode == "attractive_self_insert":
            role = random.choice(cls.ROLES_POOL)
            charm = random.choice(cls.CHARMS_POOL)
            msg = (
                f"[INTERVENSI TOKOH MEMIKAT]\n"
                f"Peran: {role}\n"
                f"Daya Tarik: {charm}\n"
                f"Bawa tokoh ini hadir di bab berikutnya dengan dinamika ketegangan dan ketertarikan yang intens bersama tokoh utama."
            )
            return {
                "mode": chosen_mode,
                "title": "Tokoh Baru Memikat",
                "summary": f"Peran: {role[:40]}... | Karisma: {charm[:35]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }

        elif chosen_mode == "mutual_fate_next_chapter":
            fate = random.choice(cls.FATE_RELATIONS)
            msg = (
                f"[INTERVENSI TAKDIR BERSAMA]\n"
                f"Ikatan Takdir: {fate}\n"
                f"Tulis bab berikutnya di mana benang merah takdir ini mulai terungkap dan mengubah hubungan kedua karakter."
            )
            return {
                "mode": chosen_mode,
                "title": "Kartu Takdir Bersama",
                "summary": f"Takdir: {fate[:50]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }

        elif chosen_mode == "constellation_next_chapter":
            const = random.choice(cls.CONSTELLATIONS)
            c_msg = random.choice(cls.CONSTELLATION_MESSAGES)
            msg = (
                f"[INTERVENSI RASI BINTANG]\n"
                f"Rasi Bintang Transenden: {const}\n"
                f"Pesan Pertama: {c_msg}\n"
                f"Hadirkan intervensi entitas rasi bintang ini dalam bab berikutnya untuk memberikan berkah atau ujian misterius."
            )
            return {
                "mode": chosen_mode,
                "title": "Rasi Bintang / Sponsor",
                "summary": f"Entitas: {const} | Pesan: {c_msg[:40]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }

        else:  # character_regression
            tp = random.choice(cls.REGRESSION_TURNING_POINTS)
            msg = (
                f"[INTERVENSI REGRESI WAKTU]\n"
                f"Tokoh yang Regresi: {char}\n"
                f"Titik Balik: {tp}\n"
                f"Tulis bab berikutnya di mana tokoh ini bertindak dengan pengetahuan masa depan, membalikkan keadaan secara dramatis."
            )
            return {
                "mode": "character_regression",
                "title": "Regresi Waktu (Time Travel)",
                "summary": f"Tokoh: {char} | Titik Balik: {tp[:45]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }


class SocialInteractionBot:
    """Eksekutor interaksi bot untuk Like, Bookmark, Follow, dan Remix Cerita."""

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

    def _get_proxy(self, country_code: str = "ID", account: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        Mengambil proxy yang tepat dan aman untuk akun:
        1. Jika akun memiliki konfigurasi 'proxy' khusus di akun.txt, prioritaskan proxy tersebut.
        2. Jika tidak, gunakan proxy manager sesuai targeting negara akun dengan session isolation.
        """
        if account and account.get("proxy"):
            return account.get("proxy")

        if not self.proxy_manager.has_proxies:
            return None

        clean_cc = str(country_code or "ID").upper().strip()
        if clean_cc not in SUPPORTED_QUARTERFULL_COUNTRIES:
            clean_cc = "ID"

        sess_id = f"soc_{secrets.token_hex(4)}"
        if account and account.get("email"):
            safe_email = re.sub(r"[^a-zA-Z0-9]", "", account["email"].split("@")[0])[:12]
            sess_id = f"soc_{safe_email}_{secrets.token_hex(3)}"

        return self.proxy_manager.pop_proxy(country_code=clean_cc, session_id=sess_id)

    def _build_headers(self, account: Dict[str, Any]) -> Dict[str, str]:
        """Menyusun header mobile fingerprint yang konsisten."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_country = str(account.get("country", "ID")).upper().strip()
        country = raw_country if raw_country in SUPPORTED_QUARTERFULL_COUNTRIES else "ID"
        cfg = SUPPORTED_QUARTERFULL_COUNTRIES[country]

        return {
            "host": "api.quarterfull.io",
            "user-agent": account.get("user_agent", "okhttp/4.12.0"),
            "accept-encoding": "gzip",
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "x-timezone": cfg["timezone"],
            "x-local-date": today,
            "x-user-country": country,
            "x-user-raw-country": country,
            "accept-language": cfg["lang"],
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
        account: Optional[Dict[str, Any]] = None,
        timeout: float = 12.0,
    ) -> httpx.Response:
        """
        Mengirim HTTP request melalui proxy yang sesuai dengan proteksi ketat anti-leak.
        Dilarang keras melakukan koneksi langsung tanpa proxy (Zero Direct Fallback).
        """
        proxy = self._get_proxy(country_code=account_country, account=account)
        if not proxy:
            email_info = account.get("email", "-") if account else "-"
            raise RuntimeError(
                f"[AKSI DITOLAK] Proxy WAJIB digunakan untuk melindungi akun {email_info} (Negara: {account_country})! "
                f"Tidak ada proxy aktif yang tersedia di proxies.txt. Koneksi langsung (Direct IP) diblokir total demi keselamatan akun."
            )

        last_exc: Optional[Exception] = None

        try:
            with httpx.Client(http2=False, proxy=proxy, timeout=timeout) as client:
                if method.upper() == "POST":
                    resp = client.post(url, headers=headers, json=json_body)
                elif method.upper() == "PUT":
                    resp = client.put(url, headers=headers, json=json_body)
                elif method.upper() == "PATCH":
                    resp = client.patch(url, headers=headers, json=json_body)
                else:
                    resp = client.get(url, headers=headers)

                # Jika proxy mengembalikan respons HTTP 400 No IPs in selected country
                if resp.status_code == 400 and ("no ips" in resp.text.lower() or "selected country" in resp.text.lower()):
                    logger.warning("[Proxy 400] IP negara %s tidak tersedia di proxy. Mencoba proxy negara lain...", account_country)
                    for alt_cc in ["US", "ID", "GB", "DE", "JP", "FR"]:
                        if alt_cc.upper() == account_country.upper():
                            continue
                        alt_proxy = self._get_proxy(alt_cc, account=None)
                        if not alt_proxy:
                            continue
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
                if resp.status_code == 200 and proxy:
                    self.proxy_manager.mark_used(proxy)
                return resp
        except Exception as exc:
            if proxy and is_dead_or_proxy_error(exc):
                self.proxy_manager.mark_failed(proxy, exc)
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
                    alt_proxy = self._get_proxy(alt_cc, account=None)
                    if not alt_proxy:
                        continue
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
                            if alt_resp.status_code == 200 and alt_proxy:
                                self.proxy_manager.mark_used(alt_proxy)
                            return alt_resp
                    except Exception as alt_err:
                        if alt_proxy and is_dead_or_proxy_error(alt_err):
                            self.proxy_manager.mark_failed(alt_proxy, alt_err)
                        last_exc = alt_err
                        continue

            if last_exc:
                raise last_exc
            raise exc

    def get_account_novel_status(self, novel_id: str, account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Mengambil metadata status interaksi novel (is_liked, is_saved) spesifik untuk akun ini via proxy."""
        url = f"{self.BASE_URL}/api/v1/novels/{novel_id}"
        headers = self._build_headers(account)
        country = account.get("country", "ID")
        try:
            resp = self._send_request("GET", url, headers=headers, account_country=country, account=account)
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal mengambil status novel akun: {exc}")
        return None

    def get_account_author_relationship(self, author_id: str, account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Mengambil status relasi akun terhadap profil author (is_following) via proxy."""
        url = f"{self.BASE_URL}/api/v1/social/profiles/{author_id}/relationship"
        headers = self._build_headers(account)
        country = account.get("country", "ID")
        try:
            resp = self._send_request("GET", url, headers=headers, account_country=country, account=account)
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug(f"Gagal memeriksa status follow akun: {exc}")
        return None

    def _save_refreshed_account(self, account: Dict[str, Any], filepath: str = "akun.txt") -> None:
        """Menyimpan pembaruan access_token & refresh_token ke berkas akun.txt."""
        try:
            p = Path(filepath)
            if not p.exists():
                return
            lines = []
            updated = False
            email = account.get("email")
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        data = json.loads(line_str)
                        if data.get("email") == email:
                            data["access_token"] = account.get("access_token", "")
                            if account.get("refresh_token"):
                                data["refresh_token"] = account.get("refresh_token")
                            lines.append(json.dumps(data, ensure_ascii=False))
                            updated = True
                            continue
                    except Exception:
                        pass
                    lines.append(line_str)
            if updated:
                with open(p, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
        except Exception as exc:
            logger.debug(f"Gagal menyimpan token ke {filepath}: {exc}")

    def refresh_access_token(self, account: Dict[str, Any]) -> bool:
        """Memperbarui access_token akun menggunakan refresh_token via proxy negara akun."""
        refresh_token = account.get("refresh_token")
        if not refresh_token:
            return False
        country = account.get("country", "ID")
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": account.get("user_agent", "okhttp/4.12.0"),
            "x-device-id": account.get("device_id", "d24063e7-a5ff-4831-92b2-4e28c0123498"),
            "content-type": "application/json",
            "accept": "application/json",
        }
        try:
            resp = self._send_request(
                "POST",
                f"{self.BASE_URL}/api/auth/token/refresh",
                headers=headers,
                json_body={"refresh_token": refresh_token},
                account_country=country,
                account=account,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_access = data.get("access_token")
                new_refresh = data.get("refresh_token")
                if new_access:
                    account["access_token"] = new_access
                    if new_refresh:
                        account["refresh_token"] = new_refresh
                    self._save_refreshed_account(account)
                    return True
        except Exception as exc:
            logger.debug(f"Gagal refresh token {account.get('email')}: {exc}")
        return False

    def login_with_password(self, account: Dict[str, Any]) -> bool:
        """Melakukan login ulang akun menggunakan login_id (email) dan password via proxy."""
        email = account.get("email")
        password = account.get("password")
        if not email or not password:
            return False
        country = account.get("country", "ID")
        headers = {
            "host": "api.quarterfull.io",
            "user-agent": account.get("user_agent", "okhttp/4.12.0"),
            "x-device-id": account.get("device_id", "d24063e7-a5ff-4831-92b2-4e28c0123498"),
            "x-platform": "android",
            "x-app-variant": "prod",
            "x-app-version": "3.0.52",
            "content-type": "application/json",
            "accept": "application/json",
        }
        try:
            resp = self._send_request(
                "POST",
                f"{self.BASE_URL}/api/auth/login",
                headers=headers,
                json_body={"login_id": email, "password": password},
                account_country=country,
                account=account,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_access = data.get("access_token")
                new_refresh = data.get("refresh_token")
                if new_access:
                    account["access_token"] = new_access
                    if new_refresh:
                        account["refresh_token"] = new_refresh
                    self._save_refreshed_account(account)
                    logger.info("Akun %s berhasil login ulang!", email)
                    return True
        except Exception as exc:
            logger.debug(f"Gagal login ulang {email}: {exc}")
        return False

    def ensure_valid_session(self, account: Dict[str, Any]) -> bool:
        """Memverifikasi validitas token akun via GET /api/auth/profile, auto refresh/relogin jika kedaluwarsa."""
        token = account.get("access_token")
        if token:
            headers = self._build_headers(account)
            country = account.get("country", "ID")
            try:
                resp = self._send_request(
                    "GET",
                    f"{self.BASE_URL}/api/auth/profile",
                    headers=headers,
                    account_country=country,
                    account=account,
                )
                if resp.status_code == 200:
                    return True
            except Exception:
                pass

        if self.refresh_access_token(account):
            return True
        return self.login_with_password(account)

    def like_novel_single(self, novel_id: str, account: Dict[str, Any], check_first: bool = True) -> Dict[str, Any]:
        """
        Menyukai novel via satu akun menggunakan proxy yang terisolasi.
        Jika check_first=True, memeriksa terlebih dahulu:
        - Jika akun sudah Like (is_liked=True), langsung SKIP tanpa melakukan toggle ulang.
        """
        if not self.ensure_valid_session(account):
            return {"status": "error", "message": "Sesi akun kedaluwarsa & gagal login", "code": 401}

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
            resp = self._send_request("POST", url, headers=headers, account_country=country, account=account)
            if resp.status_code == 401:
                if self.login_with_password(account):
                    headers = self._build_headers(account)
                    resp = self._send_request("POST", url, headers=headers, account_country=country, account=account)

            if resp.status_code == 200:
                data = resp.json()
                is_liked = data.get("is_liked", True)
                if not is_liked:
                    # Menghindari un-like (toggle kembali agar menjadi True)
                    time.sleep(0.4)
                    resp2 = self._send_request("POST", url, headers=headers, account_country=country, account=account)
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
        Menambahkan novel ke rak / bookmark via satu akun menggunakan proxy yang terisolasi.
        Jika check_first=True, memeriksa terlebih dahulu:
        - Jika akun sudah menyimpan (is_saved=True), langsung SKIP tanpa melakukan toggle ulang.
        """
        if not self.ensure_valid_session(account):
            return {"status": "error", "message": "Sesi akun kedaluwarsa & gagal login", "code": 401}

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
            resp = self._send_request("POST", url, headers=headers, account_country=country, account=account)
            if resp.status_code == 401:
                if self.login_with_password(account):
                    headers = self._build_headers(account)
                    resp = self._send_request("POST", url, headers=headers, account_country=country, account=account)

            if resp.status_code == 200:
                data = resp.json()
                is_saved = data.get("is_saved", True)
                if not is_saved:
                    # Menghindari un-bookmark
                    time.sleep(0.4)
                    resp2 = self._send_request("POST", url, headers=headers, account_country=country, account=account)
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
        Mengikuti (Follow) profil kreator/penulis via satu akun menggunakan proxy yang terisolasi.
        Jika check_first=True, memeriksa terlebih dahulu:
        - Jika akun sudah follow (is_following=True), langsung SKIP tanpa melakukan toggle ulang.
        """
        if not self.ensure_valid_session(account):
            return {"status": "error", "message": "Sesi akun kedaluwarsa & gagal login", "code": 401}

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
            resp = self._send_request("PUT", url, headers=headers, account_country=country, account=account)
            if resp.status_code == 401:
                if self.login_with_password(account):
                    headers = self._build_headers(account)
                    resp = self._send_request("PUT", url, headers=headers, account_country=country, account=account)

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
        Mengubah nama pengguna (nickname) akun di API server via proxy.
        Endpoint: PATCH /api/auth/profile
        """
        url = f"{self.BASE_URL}/api/auth/profile"
        headers = self._build_headers(account)
        headers["content-type"] = "application/json"
        country = account.get("country", "ID")

        try:
            resp = self._send_request(
                "PATCH",
                url,
                headers=headers,
                json_body={"nickname": new_nickname},
                account_country=country,
                account=account,
            )
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

    def _send_stream_hit_and_run(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        account_country: str = "ID",
        account: Optional[Dict[str, Any]] = None,
        timeout: float = 8.0,
    ) -> bool:
        """
        Mengirim inisiasi streaming remix prompt secara Hit & Run melalui proxy:
        Membuka koneksi HTTP POST streaming SSE via proxy akun, membaca chunk pembuka,
        lalu segera memutus koneksi tanpa memblokir proses menunggu seluruh generasi teks AI.
        """
        proxy = self._get_proxy(country_code=account_country, account=account)
        if not proxy:
            email_info = account.get("email", "-") if account else "-"
            raise RuntimeError(
                f"[AKSI DITOLAK] Proxy WAJIB aktif! Akun {email_info} (Negara: {account_country}) tidak mendapatkan alokasi proxy. "
                f"Koneksi stream H&R langsung (Direct IP) dilarang keras."
            )

        try:
            with httpx.Client(http2=False, proxy=proxy, timeout=timeout) as client:
                with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code in (200, 201):
                        for chunk in resp.iter_raw():
                            if chunk:
                                break
                        if proxy:
                            self.proxy_manager.mark_used(proxy)
                        return True
                    return False
        except Exception as exc:
            if proxy and is_dead_or_proxy_error(exc):
                self.proxy_manager.mark_failed(proxy, exc)
            # Pada mode Hit & Run, pemutusan koneksi awal setelah stream diterima adalah perilaku normal
            logger.debug(f"Hit & run stream disengaged: {exc}")
            return True

    def report_novel_single(
        self,
        novel_id: str,
        account: Dict[str, Any],
        reason: str = "ai_harm",
        details: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Mengirimkan laporan kepatuhan moderasi konten tunggal untuk sebuah novel via proxy.
        Endpoint: POST /api/v1/novels/{novel_id}/report
        """
        if not self.ensure_valid_session(account):
            return {"status": "error", "message": "Sesi akun kedaluwarsa & gagal login", "code": 401}

        headers = self._build_headers(account)
        headers["content-type"] = "application/json"
        country = account.get("country", "ID")
        url = f"{self.BASE_URL}/api/v1/novels/{novel_id}/report"
        payload = {"reason": reason, "details": details}

        try:
            resp = self._send_request("POST", url, headers=headers, json_body=payload, account_country=country, account=account)
            if resp.status_code == 200:
                data = resp.json()
                return {"status": "success", "report_id": data.get("report_id"), "ok": data.get("ok", True), "code": 200}
            else:
                return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:100]}", "code": resp.status_code}
        except Exception as exc:
            return {"status": "error", "message": str(exc), "code": 500}

    def remix_novel_chapter(
        self,
        novel_id: str,
        chapter_id: str,
        account: Dict[str, Any],
        mode: Optional[str] = None,
        target_character: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Mengeksekusi siklus Remix Cerita bab novel secara Hit & Run (H&R) melalui proxy aman.
        1. Memverifikasi kelayakan remix (eligibility).
        2. Mengambil atau membuat cabang seri (series branch).
        3. Mengirim inisiasi stream prompt AI dengan variasi data acak.
        4. Menyimpan pembaruan konfigurasi atribut / karakter.
        5. Mendaftarkan event pemilihan & keterpaparan (choice-event).
        """
        if not self.ensure_valid_session(account):
            return {"status": "error", "message": "Sesi akun kedaluwarsa & gagal login", "code": 401}

        headers = self._build_headers(account)
        headers["content-type"] = "application/json"
        country = account.get("country", "ID")

        # 1. Periksa eligibilitas novel
        elig_url = f"{self.BASE_URL}/api/v1/studio-cursor/reader-remixes/eligibility/{novel_id}"
        try:
            elig_resp = self._send_request("GET", elig_url, headers=headers, account_country=country, account=account)
            if elig_resp.status_code == 200:
                elig_data = elig_resp.json()
                if not elig_data.get("eligible", False):
                    return {"status": "error", "message": "Novel ini belum memenuhi syarat Remix Cerita di platform", "code": 400}
        except Exception as exc:
            logger.debug(f"Gagal periksa eligibilitas remix: {exc}")

        # 2. Ambil roots series
        roots_url = f"{self.BASE_URL}/api/v1/studio-cursor/reader-remixes/series/novel/{novel_id}/roots?limit=8"
        series_id = None
        source_session_id = None

        try:
            roots_resp = self._send_request("GET", roots_url, headers=headers, account_country=country, account=account)
            if roots_resp.status_code == 200:
                roots_data = roots_resp.json()
                items = roots_data.get("items", [])
                if items:
                    series_item = random.choice(items)
                    series_id = series_item.get("series_id")
                    ep_url = f"{self.BASE_URL}/api/v1/studio-cursor/reader-remixes/series/{series_id}/episodes/1"
                    ep_resp = self._send_request("GET", ep_url, headers=headers, account_country=country, account=account)
                    if ep_resp.status_code == 200:
                        source_session_id = ep_resp.json().get("episode", {}).get("session_id")
        except Exception as exc:
            logger.debug(f"Gagal mengambil roots remix: {exc}")

        if not series_id:
            return {"status": "error", "message": "Tidak ditemukan seri remix aktif untuk novel ini (Belum ada root series)", "code": 404}

        # 3. Branch intervention
        branch_url = f"{self.BASE_URL}/api/v1/studio-cursor/reader-remixes/series/{series_id}/branch-intervention"
        branch_payload = {
            "source_session_id": source_session_id,
            "chapter_hash_id": chapter_id,
        }
        remix_session_id = None
        try:
            branch_resp = self._send_request(
                "POST",
                branch_url,
                headers=headers,
                json_body=branch_payload,
                account_country=country,
                account=account,
            )
            if branch_resp.status_code == 200:
                remix_session_id = branch_resp.json().get("session", {}).get("id")
            else:
                return {"status": "error", "message": f"Branch intervention gagal: HTTP {branch_resp.status_code}", "code": branch_resp.status_code}
        except Exception as exc:
            return {"status": "error", "message": f"Gagal membuat branch: {exc}", "code": 500}

        if not remix_session_id:
            return {"status": "error", "message": "Gagal mendapatkan remix_session_id dari server", "code": 500}

        # 4. Generate random payload dan prompt
        remix_data = RemixModeGenerator.generate(mode=mode, target_character=target_character)
        selected_mode = remix_data["mode"]
        stream_message = remix_data["stream_message"]

        # 5. Hit & Run ke turns/stream
        stream_url = f"{self.BASE_URL}/api/v1/studio-cursor/reader-remixes/{remix_session_id}/turns/stream"
        stream_payload = {
            "message": stream_message,
            "locale": "id",
            "message_visibility": "visible",
        }
        self._send_stream_hit_and_run(stream_url, headers, stream_payload, account_country=country, account=account)

        # 6. Kirim pembaruan konfigurasi PUT spesifik jika ada
        if remix_data.get("put_endpoint") and remix_data.get("put_payload"):
            put_url = f"{self.BASE_URL}/api/v1/studio-cursor/reader-remixes/{remix_session_id}/{remix_data['put_endpoint']}"
            try:
                self._send_request(
                    "PUT",
                    put_url,
                    headers=headers,
                    json_body=remix_data["put_payload"],
                    account_country=country,
                    account=account,
                )
            except Exception as put_exc:
                logger.debug(f"PUT config update error (non-fatal): {put_exc}")

        # 7. Kirim choice event (selection & exposure)
        choice_url = f"{self.BASE_URL}/api/v1/studio-cursor/reader-remixes/{remix_session_id}/choice-event"
        try:
            self._send_request(
                "POST",
                choice_url,
                headers=headers,
                json_body={"event_type": "selection", "selected_mode": selected_mode},
                account_country=country,
                account=account,
            )
            self._send_request(
                "POST",
                choice_url,
                headers=headers,
                json_body={"event_type": "exposure"},
                account_country=country,
                account=account,
            )
        except Exception as ch_exc:
            logger.debug(f"Choice event error (non-fatal): {ch_exc}")

        return {
            "status": "success",
            "session_id": remix_session_id,
            "mode": selected_mode,
            "mode_title": remix_data["title"],
            "summary": remix_data["summary"],
            "code": 200,
        }

    def run_mass_interaction(
        self,
        action_type: str,
        target_id: str,
        target_title: str,
        count: int,
        pacing_delay: float = 1.5,
        chapter_id: Optional[str] = None,
        remix_mode: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Menjalankan aksi interaksi massal dengan antarmuka terminal yang rapi.
        action_type: 'like' | 'bookmark' | 'follow' | 'remix' | 'report'
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
            "remix": ("Auto Remix Cerita Bab", "[bold magenta]⚡ REMIX[/]"),
            "report": ("Report Moderasi Konten", "[bold red]⚑ REPORT[/]"),
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
            elif action_type == "remix":
                ch_target = chapter_id or target_id
                res = self.remix_novel_chapter(target_id, ch_target, acc, mode=remix_mode)
            elif action_type == "report":
                res = self.report_novel_single(target_id, acc)
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
                        elif action_type == "remix":
                            ch_target = chapter_id or target_id
                            retry_res = self.remix_novel_chapter(target_id, ch_target, alt_acc, mode=remix_mode)
                        elif action_type == "report":
                            retry_res = self.report_novel_single(target_id, alt_acc)
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
                elif action_type == "remix":
                    m_title = res.get("mode_title", "Remix")
                    status_text = f"[bold green][OK] {m_title} (H&R Terdaftar)[/]"
                elif action_type == "report":
                    status_text = f"[bold green][OK] Laporan Dikirim (ID: {res.get('report_id')})[/]"
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
    if not proxies and not default_proxy_manager.has_proxies:
        console.print(
            "\n[bold red][PERINGATAN KESELAMATAN] Proxy WAJIB aktif![/]\n"
            "[red]Setiap akun di akun.txt memiliki negara dan proxy tersendiri.\n"
            "Berkas 'proxies.txt' kosong atau tidak ada proxy aktif yang terkonfigurasi.\n"
            "Operasi dibatalkan demi keselamatan akun untuk mencegah kebocoran IP lokal.[/]\n"
        )
        return

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

    initial_proxy = bot._get_proxy()
    with console.status("[bold cyan]Memeriksa data novel target di API Quarterfull...[/]"):
        novel_data = TargetResolver.get_novel_details(novel_id, proxy=initial_proxy)

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
    if not proxies and not default_proxy_manager.has_proxies:
        console.print(
            "\n[bold red][PERINGATAN KESELAMATAN] Proxy WAJIB aktif![/]\n"
            "[red]Setiap akun di akun.txt memiliki negara dan proxy tersendiri.\n"
            "Berkas 'proxies.txt' kosong atau tidak ada proxy aktif yang terkonfigurasi.\n"
            "Operasi dibatalkan demi keselamatan akun untuk mencegah kebocoran IP lokal.[/]\n"
        )
        return

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

    initial_proxy = bot._get_proxy()
    with console.status("[bold cyan]Memeriksa data novel target di API Quarterfull...[/]"):
        novel_data = TargetResolver.get_novel_details(novel_id, proxy=initial_proxy)

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
    if not proxies and not default_proxy_manager.has_proxies:
        console.print(
            "\n[bold red][PERINGATAN KESELAMATAN] Proxy WAJIB aktif![/]\n"
            "[red]Setiap akun di akun.txt memiliki negara dan proxy tersendiri.\n"
            "Berkas 'proxies.txt' kosong atau tidak ada proxy aktif yang terkonfigurasi.\n"
            "Operasi dibatalkan demi keselamatan akun untuk mencegah kebocoran IP lokal.[/]\n"
        )
        return

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

    initial_proxy = bot._get_proxy()
    with console.status("[bold cyan]Memeriksa target profil penulis di API Quarterfull...[/]"):
        novel_check = TargetResolver.get_novel_details(extracted_id, proxy=initial_proxy)
        if novel_check and novel_check.get("author"):
            author_info = novel_check["author"]
            author_id = author_info.get("hash_id", extracted_id)
            author_name = author_info.get("pen_name", "Author")
            console.print(f"[dim]Mendeteksi Penulis dari Novel '{novel_check.get('title')}': [bold yellow]{author_name}[/] ({author_id})[/]")
        else:
            author_data = TargetResolver.get_author_details(author_id, proxy=initial_proxy)
            if author_data and author_data.get("author"):
                author_name = author_data["author"].get("pen_name", "Penulis")

        bot.ensure_valid_session(accounts[0])
        rel_info = TargetResolver.get_social_relationship(author_id, accounts[0].get("access_token", ""), proxy=initial_proxy)
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
    if not proxies and not default_proxy_manager.has_proxies:
        console.print(
            "\n[bold red][PERINGATAN KESELAMATAN] Proxy WAJIB aktif![/]\n"
            "[red]Setiap akun di akun.txt memiliki negara dan proxy tersendiri.\n"
            "Berkas 'proxies.txt' kosong atau tidak ada proxy aktif yang terkonfigurasi.\n"
            "Operasi dibatalkan demi keselamatan akun untuk mencegah kebocoran IP lokal.[/]\n"
        )
        return

    bot = SocialInteractionBot(accounts, proxies)

    console.print("\n[bold cyan]>>> Modul Pembaruan Nama Pengguna (Nickname Synchronizer)[/]\n")
    console.print("[dim]Fitur ini mengubah nama default bot di server menjadi nama asli yang sesuai akun.txt.[/]\n")

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

            if idx < count:
                time.sleep(0.4 if proxies else 1.2)

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


def run_auto_remix_cli(preset_novel_id: Optional[str] = None) -> None:
    """Alur interaktif untuk fitur Auto Remix Cerita Bab Novel (Hit & Run)."""
    accounts = load_accounts_from_file("akun.txt")
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun.txt'. Harap buat akun terlebih dahulu lewat menu Auto Signup.[/]")
        return

    proxies = load_proxies_from_file("proxies.txt")
    if not proxies and not default_proxy_manager.has_proxies:
        console.print(
            "\n[bold red][PERINGATAN KESELAMATAN] Proxy WAJIB aktif![/]\n"
            "[red]Setiap akun di akun.txt memiliki negara dan proxy tersendiri.\n"
            "Berkas 'proxies.txt' kosong atau tidak ada proxy aktif yang terkonfigurasi.\n"
            "Operasi dibatalkan demi keselamatan akun untuk mencegah kebocoran IP lokal.[/]\n"
        )
        return

    bot = SocialInteractionBot(accounts, proxies)

    console.print("\n[bold cyan]>>> Modul Auto Remix Cerita Novel (Hit & Run + Variasi Multi-Mode)[/]\n")

    if not preset_novel_id:
        target_raw = Prompt.ask(
            "[bold green]?[/] Masukkan URL Novel atau Novel ID target (contoh: kpQJ0dNk7V8eLOvE)",
            default="kpQJ0dNk7V8eLOvE",
        ).strip()
    else:
        target_raw = preset_novel_id

    _, novel_id = TargetResolver.clean_target(target_raw)

    initial_proxy = bot._get_proxy()
    with console.status("[bold cyan]Memeriksa metadata & eligibilitas remix novel target di API Quarterfull...[/]"):
        novel_data = TargetResolver.get_novel_details(novel_id, proxy=initial_proxy)
        is_eligible = TargetResolver.check_remix_eligibility(novel_id, proxy=initial_proxy)
        chapters = TargetResolver.get_novel_chapters(novel_id, proxy=initial_proxy)

    novel_title = novel_data.get("title", f"Novel-{novel_id}") if novel_data else f"Novel ID: {novel_id}"
    author_info = novel_data.get("author", {}) if novel_data else {}
    author_name = author_info.get("pen_name", "Unknown")

    console.print(f"[bold green]Novel Target:[/] [bold yellow]{novel_title}[/] oleh [cyan]{author_name}[/] (ID: {novel_id})")
    console.print(f"Status Eligibilitas Remix: {'[bold green]MEMENUHI SYARAT (Eligible)[/]' if is_eligible else '[bold yellow]Standard / Universal[/]'}")
    console.print(f"Total Bab Ditemukan: [bold cyan]{len(chapters)}[/] Bab\n")

    if not chapters:
        console.print("[bold red]Gagal memuat daftar bab untuk novel ini.[/]")
        return

    # Pemilihan mode remix
    console.print("[bold yellow]Pilih Mode Intervensi Remix:[/]")
    console.print("[0] [bold green]Acak Otomatis (Rekomendasi - Variasi tinggi berbeda tiap bab & akun)[/]")
    console.print("[1] Jendela Status RPG (Level Up & Stat Points)")
    console.print("[2] Ubah Penampilan Tokoh yang Ada (Attractive Existing Character)")
    console.print("[3] Intervensi Tokoh Baru Pembaca (Self-Insert Role)")
    console.print("[4] Tokoh Baru Memikat (Attractive Self-Insert)")
    console.print("[5] Kartu Takdir Bersama (Mutual Fate)")
    console.print("[6] Rasi Bintang / Sponsor Transenden (Constellation)")
    console.print("[7] Regresi Waktu (Time Travel / Future Memories)")

    mode_choice = Prompt.ask("Pilih mode", choices=["0", "1", "2", "3", "4", "5", "6", "7"], default="0")
    mode_map = {
        "0": None,
        "1": "status_window_next_chapter",
        "2": "attractive_existing_character",
        "3": "self_insert_next_chapter",
        "4": "attractive_self_insert",
        "5": "mutual_fate_next_chapter",
        "6": "constellation_next_chapter",
        "7": "character_regression",
    }
    selected_mode = mode_map[mode_choice]

    # Pemilihan bab
    console.print("\n[bold yellow]Pilih Jangkauan Bab yang Ingin Di-Remix:[/]")
    console.print("[1] Remix Bab Pertama Saja (Bab 1)")
    console.print(f"[2] Remix Seluruh Bab Novel ({len(chapters)} Bab secara berurutan)")
    console.print("[3] Masukkan Bab Tertentu (Nomor Bab)")

    scope_choice = Prompt.ask("Pilih jangkauan", choices=["1", "2", "3"], default="1")
    target_chapters: List[Dict[str, Any]] = []

    if scope_choice == "1":
        target_chapters = [chapters[0]]
    elif scope_choice == "2":
        target_chapters = chapters
    else:
        num = IntPrompt.ask(f"Nomor bab yang ingin di-remix [1 - {len(chapters)}]", default=1)
        idx = max(0, min(num - 1, len(chapters) - 1))
        target_chapters = [chapters[idx]]

    count = prompt_user_quantity(len(accounts), "Akun untuk Menjalankan Remix")

    console.print(f"\n[bold cyan]=== Memulai Auto Remix Cerita ({len(target_chapters)} Bab x {count} Akun) ===[/]")
    for ch in target_chapters:
        ch_id = ch.get("hash_id")
        ch_num = ch.get("chapter_num", 1)
        ch_title = ch.get("title", f"Bab {ch_num}")
        console.print(f"\n[bold yellow]>>> Memproses Bab {ch_num}: {ch_title} (ID: {ch_id})[/]")
        bot.run_mass_interaction(
            action_type="remix",
            target_id=novel_id,
            target_title=f"{novel_title} - Bab {ch_num}",
            count=count,
            chapter_id=ch_id,
            remix_mode=selected_mode,
        )


def run_auto_report_cli(preset_novel_id: Optional[str] = None) -> None:
    """Alur interaktif untuk fitur Report Novel Massal (Moderasi Konten)."""
    accounts = load_accounts_from_file("akun.txt")
    if not accounts:
        console.print("[bold red]Belum ada akun di 'akun.txt'. Harap buat akun terlebih dahulu lewat menu Auto Signup.[/]")
        return

    proxies = load_proxies_from_file("proxies.txt")
    if not proxies and not default_proxy_manager.has_proxies:
        console.print(
            "\n[bold red][PERINGATAN KESELAMATAN] Proxy WAJIB aktif![/]\n"
            "[red]Setiap akun di akun.txt memiliki negara dan proxy tersendiri.\n"
            "Berkas 'proxies.txt' kosong atau tidak ada proxy aktif yang terkonfigurasi.\n"
            "Operasi dibatalkan demi keselamatan akun untuk mencegah kebocoran IP lokal.[/]\n"
        )
        return

    bot = SocialInteractionBot(accounts, proxies)

    console.print("\n[bold cyan]>>> Modul Report Konten Novel Massal (Content Moderation Bot)[/]\n")

    if not preset_novel_id:
        target_raw = Prompt.ask(
            "[bold green]?[/] Masukkan URL Novel atau Novel ID target (contoh: Py7LDdwpEQ8e1YKX)",
            default="Py7LDdwpEQ8e1YKX",
        ).strip()
    else:
        target_raw = preset_novel_id

    _, novel_id = TargetResolver.clean_target(target_raw)

    initial_proxy = bot._get_proxy()
    with console.status("[bold cyan]Memeriksa data novel target di API Quarterfull...[/]"):
        novel_data = TargetResolver.get_novel_details(novel_id, proxy=initial_proxy)

    novel_title = novel_data.get("title", f"Novel-{novel_id}") if novel_data else f"Novel ID: {novel_id}"
    author_info = novel_data.get("author", {}) if novel_data else {}
    author_name = author_info.get("pen_name", "Unknown")

    console.print(f"[bold green]Novel Ditemukan:[/] [bold yellow]{novel_title}[/] oleh [cyan]{author_name}[/] (ID: {novel_id})\n")

    console.print("[bold yellow]Pilih Kategori Alasan Laporan (Report Reason):[/]")
    console.print("[1] ai_harm (Konten Berbahaya / AI Malicious)")
    console.print("[2] copyright (Pelanggaran Hak Cipta / Plagiarisme)")
    console.print("[3] inappropriate (Konten Tidak Pantas / NSFW)")
    console.print("[4] spam (Spam / Konten Sampah)")
    console.print("[5] hate_speech (Ujaran Kebencian / Diskriminasi)")

    reason_choice = Prompt.ask("Pilih alasan", choices=["1", "2", "3", "4", "5"], default="1")
    reason_map = {
        "1": "ai_harm",
        "2": "copyright",
        "3": "inappropriate",
        "4": "spam",
        "5": "hate_speech",
    }
    selected_reason = reason_map[reason_choice]

    details = Prompt.ask(
        "[bold green]?[/] Keterangan detail laporan (opsional, tekan Enter untuk default)",
        default="This content violates platform terms of service and community guidelines.",
    ).strip()

    count = prompt_user_quantity(len(accounts), "Akun untuk Melaporkan Novel")

    confirm = Confirm.ask(
        f"[bold red]?[/] Yakin ingin mengirim {count} laporan serentak untuk novel '{novel_title}'?",
        default=True,
    )
    if not confirm:
        console.print("[yellow]Aksi pelaporan dibatalkan.[/]")
        return

    bot.run_mass_interaction(
        action_type="report",
        target_id=novel_id,
        target_title=novel_title,
        count=count,
    )


if __name__ == "__main__":
    console.print("[bold cyan]=== Tester Interaksi Sosial & Remix Cerita Toodat ===[/]")
    console.print("[1] Auto Like Novel")
    console.print("[2] Auto Bookmark Novel")
    console.print("[3] Auto Followers Akun")
    console.print("[4] Ubah Nama Pengguna (Nickname) Sesuai akun.txt")
    console.print("[5] Auto Remix Cerita Bab Novel (Hit & Run)")
    console.print("[6] Report Konten Novel Massal")
    pilih = Prompt.ask("Pilih fitur", choices=["1", "2", "3", "4", "5", "6"], default="1")
    if pilih == "1":
        run_auto_like_cli()
    elif pilih == "2":
        run_auto_bookmark_cli()
    elif pilih == "3":
        run_auto_followers_cli()
    elif pilih == "4":
        run_sync_nicknames_cli()
    elif pilih == "5":
        run_auto_remix_cli()
    elif pilih == "6":
        run_auto_report_cli()
