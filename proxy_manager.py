"""
Modul Manajemen & Integrasi Proxy Cerdas (Proxy Manager)
Mendukung:
1. Scraper Otomatis Puluhan Ribu Proxy Gratis dari 20+ Sumber Terverifikasi.
2. Auto-Retry & Pruning Proxy Mati/Invalid (otomatis menghapus proxy gagal dari daftar).
3. Auto-Pull Fresh Proxies sebelum menjalankan fitur bot.
4. Dukungan backward-compatible untuk Bright Data ISP / SuperProxy & Standar HTTP/SOCKS5.
"""

import asyncio
import ipaddress
import logging
import os
import random
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

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
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

logger = logging.getLogger("ProxyManager")
console = Console(highlight=False)

# =============================================================================
# 20+ SUMBER PROXY GRATIS TERVERIFIKASI (500.000+ TOTAL PROXY TERSEDIA)
# =============================================================================
FREE_PROXY_SOURCES: List[Dict[str, str]] = [
    # --- 8 Sumber Utama dari Pengguna ---
    {"name": "monosans (All)", "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/all.txt", "protocol": "mixed"},
    {"name": "monosans (HTTP)", "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt", "protocol": "http"},
    {"name": "monosans (SOCKS5)", "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt", "protocol": "socks5"},
    {"name": "roosterkid (HTTPS)", "url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt", "protocol": "http"},
    {"name": "roosterkid (SOCKS5)", "url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt", "protocol": "socks5"},
    {"name": "proxifly (All)", "url": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt", "protocol": "mixed"},
    {"name": "zloi-user (HTTP)", "url": "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt", "protocol": "http"},
    {"name": "zloi-user (HTTPS)", "url": "https://raw.githubusercontent.com/zloi-user/hideip.me/main/https.txt", "protocol": "http"},
    {"name": "zloi-user (SOCKS5)", "url": "https://raw.githubusercontent.com/zloi-user/hideip.me/main/socks5.txt", "protocol": "socks5"},
    {"name": "VPSLabCloud (All)", "url": "https://raw.githubusercontent.com/VPSLabCloud/VPSLab-Free-Proxy-List/main/all_proxies.txt", "protocol": "mixed"},
    {"name": "dpangestuw (HTTP)", "url": "https://raw.githubusercontent.com/dpangestuw/Free-Proxy/main/http_proxies.txt", "protocol": "http"},
    {"name": "dpangestuw (SOCKS5)", "url": "https://raw.githubusercontent.com/dpangestuw/Free-Proxy/main/socks5_proxies.txt", "protocol": "socks5"},
    {"name": "xyzs996 (Health List)", "url": "https://raw.githubusercontent.com/xyzs996/free-proxy-health-list/main/all.txt", "protocol": "mixed"},
    {"name": "rix4uni (Fresh List)", "url": "https://raw.githubusercontent.com/rix4uni/fresh-proxy-list/main/proxylist.txt", "protocol": "mixed"},

    # --- 12 Sumber Tambahan Terverifikasi & Aktif ---
    {"name": "zevtyardt (100k+ All)", "url": "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/all.txt", "protocol": "mixed"},
    {"name": "TheSpeedX (HTTP)", "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt", "protocol": "http"},
    {"name": "TheSpeedX (SOCKS5)", "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt", "protocol": "socks5"},
    {"name": "MuRongPIG (HTTP 100k+)", "url": "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt", "protocol": "http"},
    {"name": "MuRongPIG (SOCKS5 100k+)", "url": "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks5.txt", "protocol": "socks5"},
    {"name": "ErcinDedeoglu (HTTP 60k+)", "url": "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/http.txt", "protocol": "http"},
    {"name": "ErcinDedeoglu (SOCKS5)", "url": "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/socks5.txt", "protocol": "socks5"},
    {"name": "jetkai (Online Proxies)", "url": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies.txt", "protocol": "mixed"},
    {"name": "Anonym0usWork (HTTP)", "url": "https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/proxy_files/http_proxies.txt", "protocol": "http"},
    {"name": "Anonym0usWork (SOCKS5)", "url": "https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/proxy_files/socks5_proxies.txt", "protocol": "socks5"},
    {"name": "clarketm (Raw List)", "url": "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt", "protocol": "http"},
    {"name": "KangProxy (HTTP)", "url": "https://raw.githubusercontent.com/officialputuid/KangProxy/master/http/http.txt", "protocol": "http"},
    {"name": "KangProxy (SOCKS5)", "url": "https://raw.githubusercontent.com/officialputuid/KangProxy/master/socks5/socks5.txt", "protocol": "socks5"},
    {"name": "vakhov (HTTP)", "url": "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt", "protocol": "http"},
    {"name": "vakhov (SOCKS5)", "url": "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/socks5.txt", "protocol": "socks5"},
    {"name": "hookzof (SOCKS5)", "url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt", "protocol": "socks5"},
    {"name": "yakumo (Checked HTTP)", "url": "https://raw.githubusercontent.com/elliottophellia/yakumo/master/results/http/global/http_checked.txt", "protocol": "http"},
    {"name": "ProxyScrape API (HTTP)", "url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all", "protocol": "http"},
]

# 23 Negara Resmi Quarterfull yang Terverifikasi di API /api/v1/service-countries
SUPPORTED_QUARTERFULL_COUNTRIES: Dict[str, Dict[str, str]] = {
    "US": {"name": "United States", "timezone": "America/New_York", "lang": "en-US,en;q=0.9"},
    "ID": {"name": "Indonesia", "timezone": "Asia/Jakarta", "lang": "id"},
    "GB": {"name": "United Kingdom", "timezone": "Europe/London", "lang": "en-GB,en;q=0.9"},
    "AU": {"name": "Australia", "timezone": "Australia/Sydney", "lang": "en-AU,en;q=0.9"},
    "CA": {"name": "Canada", "timezone": "America/Toronto", "lang": "en-CA,en;q=0.9"},
    "SG": {"name": "Singapore", "timezone": "Asia/Singapore", "lang": "en-SG,en;q=0.9"},
    "MY": {"name": "Malaysia", "timezone": "Asia/Kuala_Lumpur", "lang": "ms-MY,ms;q=0.9,en;q=0.8"},
    "JP": {"name": "Japan", "timezone": "Asia/Tokyo", "lang": "ja-JP,ja;q=0.9"},
    "IN": {"name": "India", "timezone": "Asia/Kolkata", "lang": "en-IN,en;q=0.9,hi;q=0.8"},
    "DE": {"name": "Germany", "timezone": "Europe/Berlin", "lang": "de-DE,de;q=0.9"},
    "FR": {"name": "France", "timezone": "Europe/Paris", "lang": "fr-FR,fr;q=0.9"},
    "ES": {"name": "Spain", "timezone": "Europe/Madrid", "lang": "es-ES,es;q=0.9"},
    "IT": {"name": "Italy", "timezone": "Europe/Rome", "lang": "it-IT,it;q=0.9"},
    "NL": {"name": "Netherlands", "timezone": "Europe/Amsterdam", "lang": "nl-NL,nl;q=0.9"},
    "SE": {"name": "Sweden", "timezone": "Europe/Stockholm", "lang": "sv-SE,sv;q=0.9"},
    "NO": {"name": "Norway", "timezone": "Europe/Oslo", "lang": "no-NO,no;q=0.9"},
    "DK": {"name": "Denmark", "timezone": "Europe/Copenhagen", "lang": "da-DK,da;q=0.9"},
    "PL": {"name": "Poland", "timezone": "Europe/Warsaw", "lang": "pl-PL,pl;q=0.9"},
    "CZ": {"name": "Czech Republic", "timezone": "Europe/Prague", "lang": "cs-CZ,cs;q=0.9"},
    "TR": {"name": "Turkey", "timezone": "Europe/Istanbul", "lang": "tr-TR,tr;q=0.9"},
    "BR": {"name": "Brazil", "timezone": "America/Sao_Paulo", "lang": "pt-BR,pt;q=0.9"},
    "AR": {"name": "Argentina", "timezone": "America/Argentina/Buenos_Aires", "lang": "es-AR,es;q=0.9"},
    "CO": {"name": "Colombia", "timezone": "America/Bogota", "lang": "es-CO,es;q=0.9"},
}

BRIGHTDATA_SUPPORTED_COUNTRIES = set(SUPPORTED_QUARTERFULL_COUNTRIES.keys())


class ProxyInfo:
    """Menganalisis dan memformat URL proxy."""

    def __init__(self, raw_url: str):
        self.raw_url = raw_url.strip()
        self.is_brightdata = False
        self.scheme = "http"
        self.username = ""
        self.password = ""
        self.host = ""
        self.port = 80
        self.customer = ""
        self.zone = ""
        self.current_country: Optional[str] = None
        self._parse()

    def _parse(self) -> None:
        if not self.raw_url:
            return

        url_to_parse = self.raw_url
        if "://" not in url_to_parse:
            url_to_parse = "http://" + url_to_parse

        parsed = urlparse(url_to_parse)
        self.scheme = parsed.scheme or "http"
        self.host = parsed.hostname or ""
        self.port = parsed.port or (44445 if "superproxy.io" in self.host else 80)
        self.username = parsed.username or ""
        self.password = parsed.password or ""

        # Deteksi Bright Data
        if "superproxy.io" in self.host or "brd-customer" in self.username or "lum-customer" in self.username:
            self.is_brightdata = True
            m_cust = re.search(r"(?:brd|lum)-customer-([^-_]+)", self.username)
            if m_cust:
                self.customer = m_cust.group(1)

            m_zone = re.search(r"-zone-([^-:]+)", self.username)
            if m_zone:
                self.zone = m_zone.group(1)

            m_country = re.search(r"-country-([a-zA-Z]{2})", self.username)
            if m_country:
                self.current_country = m_country.group(1).upper()

    def format_for_country(
        self,
        country_code: Optional[str] = None,
        session_id: Optional[str] = None,
        use_default_if_none: bool = True,
    ) -> str:
        """Menghasilkan URL proxy Bright Data dengan targeting negara dinamis."""
        if not self.is_brightdata:
            return self.raw_url

        new_username = self.username
        if not country_code or str(country_code).upper() in ("RANDOM", "ALL", "AUTO"):
            country_code = random.choice(list(BRIGHTDATA_SUPPORTED_COUNTRIES))

        target_cc = str(country_code).upper().strip()
        if target_cc not in BRIGHTDATA_SUPPORTED_COUNTRIES:
            target_cc = random.choice(list(BRIGHTDATA_SUPPORTED_COUNTRIES))
        target_cc = target_cc.lower()

        if "-country-" in new_username:
            new_username = re.sub(r"-country-[a-zA-Z0-9]+", f"-country-{target_cc}", new_username)
        else:
            if "-zone-" in new_username:
                new_username = re.sub(r"(-zone-[^-:]+)", rf"\1-country-{target_cc}", new_username)
            else:
                new_username += f"-country-{target_cc}"

        if not session_id:
            session_id = f"sess_{secrets.token_hex(4)}"

        clean_session = re.sub(r"[^a-zA-Z0-9_-]", "", str(session_id))
        if "-session-" in new_username:
            new_username = re.sub(r"-session-[a-zA-Z0-9_-]+", f"-session-{clean_session}", new_username)
        else:
            new_username += f"-session-{clean_session}"

        auth_part = f"{new_username}:{self.password}@" if self.password else f"{new_username}@"
        return f"{self.scheme}://{auth_part}{self.host}:{self.port}"

    def get_base_url(self) -> str:
        """Mengembalikan URL proxy murni tanpa flag -country- atau -session-."""
        if not self.is_brightdata:
            return self.raw_url
        clean_user = re.sub(r"-country-[a-zA-Z0-9]+", "", self.username)
        clean_user = re.sub(r"-session-[a-zA-Z0-9_-]+", "", clean_user)
        auth_part = f"{clean_user}:{self.password}@" if self.password else f"{clean_user}@"
        return f"{self.scheme}://{auth_part}{self.host}:{self.port}"

    def get_masked_url(self) -> str:
        """Mengembalikan URL proxy dengan kredensial disensor untuk logging aman."""
        if not self.raw_url:
            return "None"
        clean = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", self.raw_url)
        return clean


# =============================================================================
# FREE PROXY SCRAPER (ASYNCHRONOUS MULTI-SOURCE ENGINE)
# =============================================================================
class FreeProxyScraper:
    """Mesin pengikis proxy gratis berkinerja tinggi dari 20+ sumber terverifikasi."""

    IP_PORT_PATTERN = re.compile(
        r"(?:(?P<proto>https?|socks5)://)?(?P<ip>(?:\d{1,3}\.){3}\d{1,3}):(?P<port>\d{2,5})",
        re.IGNORECASE,
    )

    @classmethod
    def is_public_ip(cls, ip_str: str) -> bool:
        """Memastikan IP adalah IP publik valid (bukan loopback, link-local, atau privat)."""
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            return not (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast)
        except ValueError:
            return False

    @classmethod
    def extract_proxies_from_text(cls, text: str, default_proto: str = "http") -> Set[str]:
        """Mengekstrak dan menormalisasi proxy dari teks mentah ke format URL standar (HTTP & SOCKS5)."""
        extracted: Set[str] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Abaikan baris socks4 karena httpx tidak mendukung protokol socks4
            if line.lower().startswith("socks4://"):
                continue

            match = cls.IP_PORT_PATTERN.search(line)
            if not match:
                continue

            ip = match.group("ip")
            port = int(match.group("port"))

            if not (1 <= port <= 65535):
                continue
            if not cls.is_public_ip(ip):
                continue

            proto_match = match.group("proto")
            if proto_match:
                proto = proto_match.lower()
                if proto in ("http", "https"):
                    proto = "http"
            else:
                proto = default_proto.lower()
                if proto in ("mixed", "https", "socks4"):
                    proto = "http"

            proxy_url = f"{proto}://{ip}:{port}"
            extracted.add(proxy_url)

        return extracted

    @classmethod
    async def fetch_source(
        cls,
        client: httpx.AsyncClient,
        source: Dict[str, str],
        semaphore: asyncio.Semaphore,
    ) -> Set[str]:
        """Mengunduh satu sumber proxy secara asinkron dengan pembatas semafor."""
        async with semaphore:
            try:
                resp = await client.get(source["url"])
                if resp.status_code == 200:
                    proxies = cls.extract_proxies_from_text(resp.text, default_proto=source.get("protocol", "http"))
                    logger.debug("Sumber '%s' menghasilkan %d proxy", source["name"], len(proxies))
                    return proxies
                else:
                    logger.debug("Sumber '%s' gagal HTTP %d", source["name"], resp.status_code)
            except Exception as exc:
                logger.debug("Gagal mengunduh sumber '%s': %s", source["name"], exc)
            return set()

    @classmethod
    async def scrape_all(
        cls,
        sources: Optional[List[Dict[str, str]]] = None,
        max_concurrency: int = 15,
        timeout: float = 12.0,
        show_progress: bool = True,
    ) -> List[str]:
        """
        Mengikis seluruh sumber proxy secara paralel.
        Mengembalikan daftar proxy unik yang telah diacak.
        """
        target_sources = sources or FREE_PROXY_SOURCES
        semaphore = asyncio.Semaphore(max_concurrency)
        total_unique: Set[str] = set()

        headers = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "accept": "text/plain,*/*",
        }

        start_time = time.time()
        if show_progress:
            console.print(f"[bold cyan]>>> Menghubungi {len(target_sources)} Sumber Proxy Publik Gratis...[/]")

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), headers=headers, follow_redirects=True) as client:
            tasks = [cls.fetch_source(client, src, semaphore) for src in target_sources]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for res in results:
                if isinstance(res, set):
                    total_unique.update(res)

        elapsed = time.time() - start_time
        proxy_list = list(total_unique)
        random.shuffle(proxy_list)

        if show_progress:
            console.print(
                f"[bold green][OK][/] Berhasil mengikis [bold yellow]{len(proxy_list):,}[/] proxy unik "
                f"dari {len(target_sources)} sumber dalam {elapsed:.1f} detik!\n"
            )

        return proxy_list


# =============================================================================
# SINGLETON PROXY MANAGER DENGAN RETRY & AUTO-PRUNING
# =============================================================================
class ProxyManager:
    """Manajer Proxy Tunggal untuk seluruh Bot dengan fitur eliminasi proxy mati."""

    def __init__(self, proxy_file: str = "proxies.txt"):
        self.proxy_file = Path(proxy_file)
        self.raw_proxies: List[str] = []
        self.parsed_proxies: List[ProxyInfo] = []
        self.bad_proxies_set: Set[str] = set()
        self.last_scrape_time: float = 0.0
        self._current_index: int = 0
        self.load_proxies()

    def load_proxies(self) -> int:
        """Membaca proxies.txt dan mem-parsing seluruh entri aktif (menghindari bad proxies)."""
        self.raw_proxies.clear()
        self.parsed_proxies.clear()

        if not self.proxy_file.exists():
            return 0

        with open(self.proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line in self.bad_proxies_set:
                    continue
                if line.lower().startswith("socks4://"):
                    continue
                self.raw_proxies.append(line)
                self.parsed_proxies.append(ProxyInfo(line))

        # Acak urutan agar beban terdistribusi merata di antara puluhan ribu proxy
        if self.parsed_proxies:
            random.shuffle(self.parsed_proxies)

        return len(self.parsed_proxies)

    @property
    def count(self) -> int:
        """Mengembalikan jumlah proxy aktif yang dimuat."""
        return len(self.parsed_proxies)

    def __len__(self) -> int:
        return len(self.parsed_proxies)

    @property
    def has_proxies(self) -> bool:
        return len(self.parsed_proxies) > 0

    @property
    def is_brightdata(self) -> bool:
        return any(p.is_brightdata for p in self.parsed_proxies)

    def remove_bad_proxy(self, bad_proxy_url: Optional[str]) -> None:
        """
        Menghapus proxy yang mati/invalid dari memory pool dan mendaftarkannya ke blacklist.
        Juga mengeliminasi baris bersangkutan dari daftar aktif (Bright Data tidak dihapus).
        """
        if not bad_proxy_url:
            return

        clean_url = bad_proxy_url.strip()
        if "superproxy.io" in clean_url or "brightdata" in clean_url:
            return

        self.bad_proxies_set.add(clean_url)

        # Hapus dari memori parsed_proxies
        initial_count = len(self.parsed_proxies)
        self.parsed_proxies = [
            p for p in self.parsed_proxies
            if p.raw_url != clean_url and p.get_base_url() != clean_url
        ]
        self.raw_proxies = [p for p in self.raw_proxies if p != clean_url]

        removed = initial_count - len(self.parsed_proxies)
        if removed > 0:
            logger.info(
                "Proxy invalid dieliminasi: %s (Sisa proxy aktif: %d)",
                clean_url[:40],
                len(self.parsed_proxies),
            )

    def sync_to_file(self) -> None:
        """Menyimpan pool proxy yang masih aktif ke file proxies.txt."""
        try:
            active_urls = [p.raw_url for p in self.parsed_proxies if p.raw_url not in self.bad_proxies_set]
            with open(self.proxy_file, "w", encoding="utf-8") as f:
                f.write("# =============================================================================\n")
                f.write(f"# DAFTAR PROXY AKTIF TOODAT BOT ({len(active_urls):,} PROXY)\n")
                f.write(f"# Terakhir Diperbarui: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("# =============================================================================\n\n")
                f.write("\n".join(active_urls) + "\n")
        except Exception as exc:
            logger.debug("Gagal menyinkronkan proxies.txt: %s", exc)

    def get_proxy(
        self,
        country_code: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Mengambil proxy berikutnya dari pool.
        Jika pool kosong, mengembalikan None.
        Jika proxy Bright Data, parameter negara disesuaikan.
        """
        if not self.parsed_proxies:
            return None

        # Ambil acak atau round-robin dari proxy yang masih aktif
        p_info = self.parsed_proxies[self._current_index % len(self.parsed_proxies)]
        self._current_index += 1

        if p_info.is_brightdata:
            return p_info.format_for_country(country_code=country_code, session_id=session_id)

        return p_info.raw_url

    def get_alternate_proxy(self, failed_proxy: Optional[str] = None) -> Optional[str]:
        """Mengeliminasi failed_proxy dan langsung mengembalikan proxy pengganti yang masih aktif."""
        if failed_proxy:
            self.remove_bad_proxy(failed_proxy)
        return self.get_proxy()

    def ensure_fresh_proxies(
        self,
        force: bool = False,
        min_proxies: int = 500,
        max_age_seconds: int = 1800,
    ) -> int:
        """
        Memastikan ketersediaan proxy segar sebelum fitur bot dijalankan:
        - Jika daftar kosong, atau
        - Jika umur proxy > 30 menit (max_age_seconds), atau
        - Jika `force=True`,
        otomatis menjalankan scraper puluhan ribu proxy gratis secara asinkron.
        """
        current_time = time.time()
        file_age = (
            current_time - self.proxy_file.stat().st_mtime
            if self.proxy_file.exists()
            else float("inf")
        )

        if self.is_brightdata:
            logger.debug("Bright Data ISP terdeteksi di proxies.txt, scraper proxy publik dilewati.")
            return len(self.parsed_proxies)

        should_scrape = (
            force
            or len(self.parsed_proxies) < min_proxies
            or (file_age > max_age_seconds and self.last_scrape_time == 0)
        )

        if not should_scrape:
            logger.debug("Proxy pool masih segar (%d proxy aktif)", len(self.parsed_proxies))
            return len(self.parsed_proxies)

        console.print(
            Panel(
                "[bold cyan]Auto-Refresh Proxy Aktif:[/] Menarik puluhan ribu proxy gratis segar "
                "dari 20+ sumber online sebelum fitur dijalankan...",
                border_style="yellow",
            )
        )

        try:
            # Jalankan scraper asinkron
            fresh_proxies = asyncio.run(FreeProxyScraper.scrape_all(show_progress=True))
            if fresh_proxies:
                # Simpan ke file proxies.txt
                with open(self.proxy_file, "w", encoding="utf-8") as f:
                    f.write("# =============================================================================\n")
                    f.write(f"# DAFTAR PROXY GRATIS TOODAT BOT ({len(fresh_proxies):,} PROXY)\n")
                    f.write(f"# Terakhir Diperbarui: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write("# =============================================================================\n\n")
                    f.write("\n".join(fresh_proxies) + "\n")

                self.last_scrape_time = current_time
                count = self.load_proxies()
                console.print(f"[bold green][OK][/] Pool proxy diperbarui: {count:,} proxy aktif siap digunakan!\n")
                return count
        except Exception as exc:
            console.print(f"[yellow]Peringatan: Gagal menarik proxy baru secara otomatis ({exc}). Menggunakan proxy lokal yang ada.[/]")

        return len(self.parsed_proxies)

    @staticmethod
    def get_client_kwargs(
        proxy: Optional[str] = None,
        base_url: Optional[str] = "https://api.quarterfull.io",
        timeout: float = 15.0,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Membuat dictionary argumen standar untuk httpx.Client atau httpx.AsyncClient.
        PENTING: Jika `proxy` aktif, `http2` disetel False agar tunnel HTTP CONNECT stabil tanpa hang.
        Jika direct (tanpa proxy), `http2` disetel True sesuai standar bot.
        """
        kwargs: Dict[str, Any] = {
            "http2": False if proxy else True,
            "timeout": httpx.Timeout(timeout),
        }
        if base_url:
            kwargs["base_url"] = base_url
        if headers:
            kwargs["headers"] = headers
        if proxy:
            kwargs["proxy"] = proxy

        return kwargs

    def test_proxy(
        self,
        proxy_url: Optional[str] = None,
        timeout: float = 8.0,
    ) -> Dict[str, Any]:
        """Menguji koneksi satu proxy langsung ke endpoint target Quarterfull API."""
        target_proxy = proxy_url or self.get_proxy()
        result: Dict[str, Any] = {
            "proxy": target_proxy,
            "success": False,
            "latency_ms": 0.0,
            "error": None,
        }

        if not target_proxy:
            result["error"] = "Tidak ada proxy yang dikonfigurasi"
            return result

        start_time = time.time()
        try:
            with httpx.Client(proxy=target_proxy, http2=False, timeout=timeout) as client:
                api_resp = client.get("https://api.quarterfull.io/api/auth/app-version")
                latency = (time.time() - start_time) * 1000.0
                result["latency_ms"] = round(latency, 1)
                if api_resp.status_code == 200:
                    result["success"] = True
                else:
                    result["error"] = f"HTTP {api_resp.status_code}"
        except Exception as exc:
            result["latency_ms"] = round((time.time() - start_time) * 1000.0, 1)
            result["error"] = str(exc)

        return result


# Singleton global instance
default_proxy_manager = ProxyManager("proxies.txt")


def get_global_proxy_manager() -> ProxyManager:
    """Mengambil instance global ProxyManager."""
    return default_proxy_manager


def test_proxy_cli() -> None:
    """Antarmuka CLI interaktif untuk mengelola dan menguji proxy."""
    mgr = default_proxy_manager
    mgr.load_proxies()

    while True:
        console.print("\n[bold cyan]=== MANAJEMEN & PENGATURAN PROXY GRATIS & ISP ===[/]\n")
        console.print(f"[bold white]Total Proxy Aktif di 'proxies.txt':[/] [bold green]{len(mgr.parsed_proxies):,}[/] Proxy")
        console.print(f"[bold white]Proxy yang Dieliminasi (Bad/Invalid):[/] [bold red]{len(mgr.bad_proxies_set):,}[/] Proxy\n")

        menu_table = Table(box=None, show_header=False)
        menu_table.add_column("No", style="bold cyan")
        menu_table.add_column("Aksi", style="bold white")

        menu_table.add_row("[1]", "Tarik / Scrape Puluhan Ribu Proxy Gratis Baru Sekarang [bold yellow](Force Refresh)[/]")
        menu_table.add_row("[2]", "Uji Sampel Proxy Aktif Terhadap API Target [bold green](Health Check)[/]")
        menu_table.add_row("[3]", "Bersihkan & Simpan Daftar Proxy Aktif ke File [bold magenta](Sinkronisasi proxies.txt)[/]")
        menu_table.add_row("[0]", "Kembali ke Menu Utama")
        console.print(menu_table)
        console.print()

        try:
            choice = Prompt.ask("[bold green]?[/] Pilih tindakan [0-3]", choices=["0", "1", "2", "3"], default="1")
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "1":
            console.print("\n[bold cyan]Memulai proses scraping dari 20+ sumber...[/]")
            mgr.ensure_fresh_proxies(force=True)
        elif choice == "2":
            if not mgr.has_proxies:
                console.print("[yellow]Tidak ada proxy untuk diuji. Silakan scrape terlebih dahulu.[/]")
                continue

            test_count = min(10, len(mgr.parsed_proxies))
            console.print(f"\n[bold cyan]Menguji {test_count} sampel proxy terhadap api.quarterfull.io...[/]\n")

            test_table = Table(title=f"Hasil Uji {test_count} Sampel Proxy", border_style="cyan")
            test_table.add_column("No", style="dim", width=4)
            test_table.add_column("Proxy URL", style="bold white")
            test_table.add_column("Status API", style="bold")
            test_table.add_column("Latency", style="yellow")
            test_table.add_column("Keterangan", style="dim")

            samples = random.sample(mgr.parsed_proxies, test_count)
            success_count = 0
            for idx, p in enumerate(samples, start=1):
                res = mgr.test_proxy(p.raw_url, timeout=6.0)
                if res["success"]:
                    success_count += 1
                    test_table.add_row(
                        str(idx),
                        p.get_masked_url(),
                        "[green]200 OK[/]",
                        f"{res['latency_ms']} ms",
                        "[green]Aktif & Responsif[/]",
                    )
                else:
                    mgr.remove_bad_proxy(p.raw_url)
                    test_table.add_row(
                        str(idx),
                        p.get_masked_url(),
                        "[red]GAGAL[/]",
                        f"{res['latency_ms']} ms",
                        f"[red]Dieliminasi ({str(res['error'])[:25]})[/]",
                    )

            console.print(test_table)
            console.print(f"\n[bold green]Uji selesai: {success_count}/{test_count} sampel berhasil terhubung![/]")
        elif choice == "3":
            mgr.sync_to_file()
            console.print(f"\n[bold green]Berhasil menyinkronkan {len(mgr.parsed_proxies):,} proxy ke 'proxies.txt'![/]")
        elif choice == "0":
            break


if __name__ == "__main__":
    test_proxy_cli()
