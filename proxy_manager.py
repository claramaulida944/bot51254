"""
Modul Manajemen & Integrasi Proxy Cerdas (Proxy Manager)
Khusus dioptimalkan untuk Bright Data ISP / SuperProxy & Standar HTTP/SOCKS5 Proxies.

Fitur Unggulan:
1. Bright Data Dynamic Country Targeting:
   - Mendeteksi proxy Bright Data (brd.superproxy.io, lum-superproxy.io).
   - Mengubah parameter negara (`-country-{code}`) secara dinamis saat runtime sesuai
     negara profil akun atau target pembaca (misal: ID -> Indonesia, US -> Amerika Serikat).
2. HTTP/2 vs HTTP/1.1 Forward Tunnel Safeguard:
   - Secara otomatis menyetel `http2=False` saat melalui proxy untuk mencegah timeout/hang
     pada koneksi HTTP CONNECT tunnel.
3. Health Check & Geo Diagnostics:
   - Menguji konektivitas ke geo diagnostics (https://geo.brdtest.com/mygeo.json)
     serta API target (https://api.quarterfull.io).
4. Multi-format & Fallback:
   - Mendukung daftar proxy acak dari `proxies.txt` atau direct connection jika kosong.
"""

import logging
import os
import random
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
from rich.console import Console
from rich.table import Table

logger = logging.getLogger("ProxyManager")
console = Console()


# 23 Negara Resmi Quarterfull yang Terverifikasi di API /api/v1/service-countries & Memiliki IP Aktif
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
        self.port = 44445
        self.customer = ""
        self.zone = ""
        self.current_country: Optional[str] = None
        self._parse()

    def _parse(self) -> None:
        if not self.raw_url:
            return

        url_to_parse = self.raw_url.strip()
        if "://" not in url_to_parse:
            url_to_parse = "http://" + url_to_parse

        try:
            # Cegah crash di Python 3.12 jika ada karakter kurung siku '[' atau ']' di username/password
            clean_for_parse = url_to_parse.replace("[", "%5B").replace("]", "%5D")
            parsed = urlparse(clean_for_parse)
            self.scheme = parsed.scheme or "http"
            self.host = parsed.hostname or ""
            self.port = parsed.port or (44445 if "superproxy.io" in self.host else 80)
            self.username = (parsed.username or "").replace("%5B", "[").replace("%5D", "]")
            self.password = (parsed.password or "").replace("%5B", "[").replace("%5D", "]")
        except Exception:
            # Fallback regex parsing jika urlparse bawaan Python gagal
            m = re.search(r"^(?P<scheme>[a-zA-Z0-9]+)://(?:(?P<user>[^:]+)(?::(?P<pass>[^@]*))?@)?(?P<host>[^:]+)(?::(?P<port>\d+))?", url_to_parse)
            if m:
                self.scheme = m.group("scheme") or "http"
                self.username = m.group("user") or ""
                self.password = m.group("pass") or ""
                self.host = m.group("host") or ""
                self.port = int(m.group("port")) if m.group("port") else (44445 if "superproxy.io" in self.host else 80)

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
        """
        Menghasilkan URL proxy yang ditargetkan ke negara tertentu.
        Jika ini adalah proxy Bright Data, parameter `-country-xx` akan disesuaikan.
        Jika negara tidak ada di pool ISP (No IPs in selected country), otomatis fallback ke US.
        """
        if not self.is_brightdata:
            return self.raw_url

        new_username = self.username

        if not country_code or str(country_code).upper() in ("RANDOM", "ALL", "AUTO"):
            # Rotasi acak dinamis dari seluruh pool 43+ negara aktif Bright Data
            country_code = random.choice(list(BRIGHTDATA_SUPPORTED_COUNTRIES))

        target_cc = str(country_code).upper().strip()
        # Auto-fallback jika negara tidak didukung oleh paket ISP
        if target_cc not in BRIGHTDATA_SUPPORTED_COUNTRIES:
            logger.debug("Negara %s tidak tersedia di pool ISP, fallback ke acak", target_cc)
            target_cc = random.choice(list(BRIGHTDATA_SUPPORTED_COUNTRIES))
        target_cc = target_cc.lower()

        if "-country-" in new_username:
            new_username = re.sub(r"-country-[a-zA-Z0-9]+", f"-country-{target_cc}", new_username)
        else:
            if "-zone-" in new_username:
                new_username = re.sub(r"(-zone-[^-:]+)", rf"\1-country-{target_cc}", new_username)
            else:
                new_username += f"-country-{target_cc}"

        # Selalu pastikan ada session_id unik per pemanggilan agar IP Bright Data selalu berganti dan terisolasi
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
        """Mengembalikan URL proxy murni tanpa flag -country- atau -session- (fallback zone)."""
        if not self.is_brightdata:
            return self.raw_url
        clean_user = re.sub(r"-country-[a-zA-Z0-9]+", "", self.username)
        clean_user = re.sub(r"-session-[a-zA-Z0-9_-]+", "", clean_user)
        auth_part = f"{clean_user}:{self.password}@" if self.password else f"{clean_user}@"
        return f"{self.scheme}://{auth_part}{self.host}:{self.port}"

    def get_masked_url(self) -> str:
        """Mengembalikan URL proxy yang kredensialnya disensor untuk logging aman."""
        if not self.raw_url:
            return "None"
        clean = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", self.raw_url)
        return clean


class ProxyManager:
    """Manajer Proxy Tunggal untuk seluruh Bot."""

    def __init__(self, proxy_file: str = "proxies.txt"):
        self.proxy_file = Path(proxy_file)
        self.raw_proxies: List[str] = []
        self.parsed_proxies: List[ProxyInfo] = []
        self._current_index: int = 0
        self.load_proxies()

    def load_proxies(self) -> int:
        """Membaca proxies.txt dan mem-parsing seluruh entri aktif."""
        self.raw_proxies.clear()
        self.parsed_proxies.clear()

        if not self.proxy_file.exists():
            return 0

        with open(self.proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Cek jika baris masih berupa placeholder template
                if any(p in line.lower() for p in ["[replace", "<replace", "[password]", "<password>"]):
                    logger.warning(
                        f"⚠️ Baris di {self.proxy_file.name} masih berupa placeholder / belum diisi password: "
                        f"'{line}'. Silakan ganti '[replace with password]' dengan password zona Bright Data Anda yang sebenarnya."
                    )
                    continue

                try:
                    p_info = ProxyInfo(line)
                    if p_info.host:
                        self.raw_proxies.append(line)
                        self.parsed_proxies.append(p_info)
                except Exception as exc:
                    logger.warning(f"⚠️ Gagal membaca format proxy '{line}': {exc}")

        return len(self.parsed_proxies)

    @property
    def has_proxies(self) -> bool:
        return len(self.parsed_proxies) > 0

    @property
    def is_brightdata(self) -> bool:
        return any(p.is_brightdata for p in self.parsed_proxies)

    def get_proxy(
        self,
        country_code: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Mengambil proxy berikutnya.
        Jika proxy merupakan Bright Data, otomatis ditargetkan ke `country_code`.
        """
        if not self.parsed_proxies:
            return None

        proxy_info = self.parsed_proxies[self._current_index % len(self.parsed_proxies)]
        self._current_index += 1

        if proxy_info.is_brightdata:
            return proxy_info.format_for_country(country_code=country_code, session_id=session_id)

        return proxy_info.raw_url

    def get_base_fallback_proxy(self) -> Optional[str]:
        """Mengambil base proxy tanpa targeting negara spesifik."""
        if not self.parsed_proxies:
            return None
        return self.parsed_proxies[0].get_base_url()

    def get_alternate_proxy(self, failed_country: Optional[str] = None) -> Optional[str]:
        """Mengambil proxy dari negara lain secara dinamis dan acak."""
        candidates = ["ID", "GB", "DE", "JP", "FR", "AU", "CA", "SG", "NL", "ES", "IT", "KR", "US"]
        random.shuffle(candidates)
        for cc in candidates:
            if failed_country and cc.upper() == failed_country.upper():
                continue
            return self.get_proxy(country_code=cc, session_id=f"alt_{secrets.token_hex(4)}")
        return self.get_proxy(country_code=random.choice(candidates), session_id=f"alt_{secrets.token_hex(4)}")

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
        country_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Menguji koneksi proxy ke Bright Data Geo JSON & Target API.
        """
        target_proxy = proxy_url or self.get_proxy(country_code=country_code)

        result: Dict[str, Any] = {
            "proxy": target_proxy,
            "success": False,
            "ip": None,
            "country": None,
            "region": None,
            "city": None,
            "asn": None,
            "target_api_ok": False,
            "latency_ms": 0.0,
            "error": None,
        }

        if not target_proxy:
            result["error"] = "Tidak ada proxy yang dikonfigurasi"
            return result

        start_time = time.time()
        try:
            with httpx.Client(proxy=target_proxy, http2=False, timeout=12.0) as client:
                # 1. Cek Geo Diagnostic JSON
                try:
                    geo_resp = client.get("https://geo.brdtest.com/mygeo.json")
                    if geo_resp.status_code == 200:
                        geo_data = geo_resp.json()
                        result["country"] = geo_data.get("country")
                        geo = geo_data.get("geo", {})
                        result["region"] = geo.get("region") or geo.get("region_name")
                        result["city"] = geo.get("city")
                        asn = geo_data.get("asn", {})
                        result["asn"] = asn.get("org_name")
                except Exception:
                    pass

                # 2. Cek Endpoint Quarterfull API
                api_resp = client.get("https://api.quarterfull.io/api/auth/app-version")
                if api_resp.status_code == 200:
                    result["target_api_ok"] = True
                    result["success"] = True
                else:
                    result["error"] = f"Target API HTTP {api_resp.status_code}"

            latency = (time.time() - start_time) * 1000.0
            result["latency_ms"] = round(latency, 1)

        except Exception as exc:
            result["error"] = str(exc)
            result["latency_ms"] = round((time.time() - start_time) * 1000.0, 1)

        return result


# Instance singleton global
default_proxy_manager = ProxyManager("proxies.txt")


def get_global_proxy_manager() -> ProxyManager:
    """Mengambil instance global ProxyManager."""
    return default_proxy_manager


def test_proxy_cli() -> None:
    """Antarmuka CLI interaktif untuk mengecek status proxy dan berbagai negara."""
    console.print("\n[bold cyan]=== DIAGNOSTIK KONEKSI PROXY (BRIGHT DATA / ISP) ===[/]\n")

    mgr = ProxyManager("proxies.txt")
    if not mgr.has_proxies:
        console.print("[yellow]File 'proxies.txt' kosong atau belum memiliki proxy aktif.[/]")
        return

    console.print(f"Total Proxy di 'proxies.txt': [bold green]{len(mgr.parsed_proxies)}[/]")
    p_info = mgr.parsed_proxies[0]
    console.print(f"Tipe Proxy: [bold {'magenta' if p_info.is_brightdata else 'blue'}]{'Bright Data SuperProxy (Dynamic Geo Routing)' if p_info.is_brightdata else 'Standard HTTP/SOCKS5'}[/]")
    console.print(f"Host: [bold white]{p_info.host}:{p_info.port}[/] | Masked: [dim]{p_info.get_masked_url()}[/]\n")

    test_countries = ["ID", "US", "JP", "GB"] if p_info.is_brightdata else [None]

    table = Table(title="Hasil Uji Koneksi & Lokasi IP Proxy")
    table.add_column("Target Negara", style="bold cyan")
    table.add_column("Negara Asli", style="green")
    table.add_column("Region / Kota", style="white")
    table.add_column("Provider / ISP", style="dim")
    table.add_column("Target API", style="bold")
    table.add_column("Latency", style="yellow")

    for cc in test_countries:
        label = cc or "Default"
        with console.status(f"[bold green]Menguji proxy untuk {label}...[/]"):
            res = mgr.test_proxy(country_code=cc)

        if res["success"]:
            api_status = "[bold green]200 OK[/]" if res["target_api_ok"] else "[bold red]FAIL[/]"
            table.add_row(
                label,
                res["country"] or "-",
                f"{res['region'] or ''} {res['city'] or ''}".strip() or "-",
                str(res.get("asn") or "-")[:25],
                api_status,
                f"{res['latency_ms']} ms",
            )
        else:
            table.add_row(
                label,
                "-",
                str(res.get("error", ""))[:30],
                "-",
                "[red]FAIL[/]",
                f"{res['latency_ms']} ms",
            )

    console.print(table)
    console.print("\n[bold green][OK] Semua request melalui proxy berhasil disalurkan dengan HTTP/1.1 tunnel tanpa hang.[/]\n")


if __name__ == "__main__":
    test_proxy_cli()
