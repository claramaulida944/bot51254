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
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

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
    """Manajer Proxy Tunggal untuk seluruh Bot dengan Auto-Replenish & Auto-Prune."""

    def __init__(
        self,
        proxy_file: str = "proxies.txt",
        auto_replenish: bool = True,
        min_replenish_threshold: int = 3,
    ):
        self.proxy_file = Path(proxy_file)
        self.raw_proxies: List[str] = []
        self.parsed_proxies: List[ProxyInfo] = []
        self._current_index: int = 0
        self._lock = threading.RLock()
        self._auto_replenish: bool = auto_replenish
        self._min_replenish_threshold: int = min_replenish_threshold
        self.load_proxies()

    def _write_to_file(self, proxy_list: List[str]) -> None:
        """Menulis daftar proxy ke proxies.txt secara aman."""
        try:
            with open(self.proxy_file, "w", encoding="utf-8") as f:
                f.write("# =============================================================================\n")
                f.write("# DAFTAR PROXY BOT TOODAT / QUARTERFULL\n")
                f.write("# Diperbarui secara otomatis melalui FreeProxyScraper (ProxyScrape & Top Sources)\n")
                f.write("# =============================================================================\n\n")
                for px in proxy_list:
                    clean = px.strip()
                    if clean:
                        f.write(f"{clean}\n")
        except Exception as exc:
            logger.error(f"Gagal menulis file {self.proxy_file}: {exc}")

    def load_proxies(self) -> int:
        """Membaca proxies.txt dan mem-parsing seluruh entri aktif secara thread-safe."""
        with self._lock:
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
        with self._lock:
            return len(self.parsed_proxies) > 0

    @property
    def is_brightdata(self) -> bool:
        with self._lock:
            return any(p.is_brightdata for p in self.parsed_proxies)

    def ensure_proxies(self, min_count: int = 3, target_count: int = 30) -> int:
        """
        Memastikan ketersediaan proxy aktif minimal `min_count`.
        Jika proxy di memory/file kurang dari `min_count` dan bukan Bright Data,
        secara otomatis memicu FreeProxyScraper untuk mengambil dan memvalidasi proxy baru.
        """
        with self._lock:
            # Jika sudah ada proxy Bright Data, gateway selalu aktif dan tidak butuh auto-scrape
            if self.is_brightdata:
                return len(self.parsed_proxies)

            if len(self.parsed_proxies) >= min_count:
                return len(self.parsed_proxies)

            console.print(
                f"\n[bold magenta][AUTO-REPLENISH] Sisa proxy ({len(self.parsed_proxies)}) menipis / habis (min: {min_count}).[/] "
                f"[yellow]Otomatis scraping {target_count} proxy aktif baru...[/]"
            )
            try:
                FreeProxyScraper.scrape_and_update(
                    target_count=target_count,
                    output_file=str(self.proxy_file),
                    show_table=False,
                )
                self.load_proxies()
                console.print(f"[bold green][AUTO-REPLENISH] Selesai! {len(self.parsed_proxies)} proxy aktif siap digunakan.[/]\n")
                return len(self.parsed_proxies)
            except Exception as exc:
                logger.error(f"[Auto-Replenish] Gagal scraping proxy otomatis: {exc}")
                return len(self.parsed_proxies)

    def remove_proxy(self, proxy_url: Optional[str], reason: str = "failed") -> bool:
        """
        Menghapus proxy dari memory dan file proxies.txt secara thread-safe.
        Jika proxy merupakan Bright Data SuperProxy, tidak dihapus jika reason=='used',
        namun jika reason=='failed' (misal akun suspended / 407), tetap dapat diproses jika diinginkan.
        """
        if not proxy_url:
            return False

        with self._lock:
            target_raw = proxy_url.strip()
            p_to_remove: Optional[ProxyInfo] = None

            try:
                target_parsed = urlparse(target_raw if "://" in target_raw else f"http://{target_raw}")
                target_host = target_parsed.hostname
                target_port = target_parsed.port
            except Exception:
                target_host = None
                target_port = None

            for p in self.parsed_proxies:
                if p.raw_url == target_raw:
                    p_to_remove = p
                    break
                if target_host and p.host == target_host and (target_port is None or p.port == target_port):
                    p_to_remove = p
                    break

            if not p_to_remove:
                return False

            # Bright Data gateway tidak dihapus saat "used" karena dirancang reusable dengan dynamic session
            if p_to_remove.is_brightdata and reason == "used":
                return False

            if p_to_remove in self.parsed_proxies:
                self.parsed_proxies.remove(p_to_remove)
            if p_to_remove.raw_url in self.raw_proxies:
                self.raw_proxies.remove(p_to_remove.raw_url)

            # Simpan pembaruan ke proxies.txt
            self._write_to_file([p.raw_url for p in self.parsed_proxies])

            masked = p_to_remove.get_masked_url()
            sisa = len(self.parsed_proxies)
            if reason == "failed":
                logger.warning(f"[-] [ProxyManager] Proxy mati/gagal konek otomatis DIHAPUS: {masked} (Sisa: {sisa})")
                console.print(f"[dim red][DEL] Proxy mati dihapus:[/] [dim]{masked}[/] [dim](Sisa {sisa} proxy)[/]")
            elif reason == "used":
                logger.info(f"[+] [ProxyManager] Proxy selesai dipakai & DIHAPUS dari antrean: {masked} (Sisa: {sisa})")
                console.print(f"[dim yellow][USED] Proxy selesai digunakan & dilepas:[/] [dim]{masked}[/] [dim](Sisa {sisa} proxy)[/]")

            # Auto-replenish jika kuota habis/menipis
            if self._auto_replenish and not self.is_brightdata and sisa < self._min_replenish_threshold:
                # Jalankan replenish di thread terpisah agar pemanggil tidak terblokir lama jika sedang dalam eksekusi
                threading.Thread(target=self.ensure_proxies, args=(self._min_replenish_threshold, 30), daemon=True).start()

            return True

    def mark_failed(self, proxy_url: Optional[str], error: Optional[Any] = None) -> bool:
        """Menandai dan menghapus proxy yang gagal konek / mati / error dari antrean dan file."""
        return self.remove_proxy(proxy_url, reason="failed")

    def mark_used(self, proxy_url: Optional[str]) -> bool:
        """Menandai dan menghapus proxy yang telah selesai digunakan (free proxy consumed)."""
        return self.remove_proxy(proxy_url, reason="used")

    def pop_proxy(
        self,
        country_code: Optional[str] = None,
        session_id: Optional[str] = None,
        auto_replenish: bool = True,
    ) -> Optional[str]:
        """
        Mengambil proxy dan langsung mengeluarkannya dari antrean / file proxies.txt (khusus free proxy).
        Jika Bright Data, tidak dihapus dari file karena merupakan gateway rotasi dinamis.
        """
        with self._lock:
            if not self.parsed_proxies and auto_replenish and not self.is_brightdata:
                self.ensure_proxies(min_count=1, target_count=30)

            if not self.parsed_proxies:
                return None

            proxy_info = self.parsed_proxies.pop(0)
            if proxy_info.raw_url in self.raw_proxies:
                self.raw_proxies.remove(proxy_info.raw_url)

            # Jika bukan Bright Data, simpan ke file
            if not proxy_info.is_brightdata:
                self._write_to_file([p.raw_url for p in self.parsed_proxies])
                console.print(
                    f"[dim yellow][ALLOC] Proxy dialokasikan & dikeluarkan dari antrean:[/] [dim]{proxy_info.get_masked_url()}[/] "
                    f"[dim](Sisa {len(self.parsed_proxies)} proxy)[/]"
                )
            else:
                # Bright Data gateway dikembalikan ke pool
                self.parsed_proxies.append(proxy_info)

            # Auto-replenish jika sisa proxy menipis
            if auto_replenish and not self.is_brightdata and len(self.parsed_proxies) < self._min_replenish_threshold:
                threading.Thread(target=self.ensure_proxies, args=(self._min_replenish_threshold, 30), daemon=True).start()

            if proxy_info.is_brightdata:
                return proxy_info.format_for_country(country_code=country_code, session_id=session_id)

            return proxy_info.raw_url

    def get_proxy(
        self,
        country_code: Optional[str] = None,
        session_id: Optional[str] = None,
        auto_replenish: bool = True,
    ) -> Optional[str]:
        """
        Mengambil proxy berikutnya.
        Jika proxy kosong dan auto_replenish=True, otomatis mengambil proxy gratis baru.
        Jika proxy merupakan Bright Data, otomatis ditargetkan ke `country_code`.
        """
        with self._lock:
            if not self.parsed_proxies and auto_replenish and not self.is_brightdata:
                self.ensure_proxies(min_count=1, target_count=30)

            if not self.parsed_proxies:
                return None

            proxy_info = self.parsed_proxies[self._current_index % len(self.parsed_proxies)]
            self._current_index += 1

            if proxy_info.is_brightdata:
                return proxy_info.format_for_country(country_code=country_code, session_id=session_id)

            return proxy_info.raw_url

    def get_base_fallback_proxy(self) -> Optional[str]:
        """Mengambil base proxy tanpa targeting negara spesifik."""
        with self._lock:
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
            with httpx.Client(proxy=target_proxy, http2=False, timeout=8.0) as client:
                # 1. Cek Geo Diagnostic JSON (opsional jika proxy standar)
                try:
                    geo_resp = client.get("https://geo.brdtest.com/mygeo.json", timeout=3.0)
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
                api_resp = client.get("https://api.quarterfull.io/api/auth/app-version", timeout=4.0)
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

    def save_proxies(self, proxy_list: List[str]) -> None:
        """Menyimpan daftar proxy ke proxies.txt dan memuat ulang instance secara thread-safe."""
        with self._lock:
            self._write_to_file(proxy_list)
            self.load_proxies()

    def clear_proxies(self) -> None:
        """Mengosongkan daftar proxy agar bot menggunakan Direct Connection secara thread-safe."""
        with self._lock:
            self._write_to_file([])
            self.load_proxies()


class FreeProxyScraper:
    """
    Scraper & Validator Proxy Gratis Multi-Source Berkecepatan Tinggi.
    Mengambil ribuan proxy dari ProxyScrape (v4 API) dan sumber terverifikasi,
    memvalidasi konektivitasnya langsung ke API target (https://api.quarterfull.io),
    serta menyimpan proxy yang aktif ke proxies.txt.
    """

    SOURCES: List[Tuple[str, str]] = [
        # 1. Sumber Utama: ProxyScrape v4 API (Format protocol:ip:port HTTP & SOCKS5)
        (
            "ProxyScrape v4",
            "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text",
        ),
        # 2. Monosans Proxy List (Update tiap 15 menit, GitHub Terverifikasi)
        (
            "monosans/proxy-list",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/all.txt",
        ),
        # 3. Hookzof SOCKS5 List (Kualitas tinggi, latency rendah)
        (
            "hookzof/socks5",
            "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
        ),
        # 4. TheSpeedX SOCKS-List (HTTP & SOCKS5)
        (
            "SpeedX/HTTP",
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
        ),
        (
            "SpeedX/SOCKS5",
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
        ),
        # 5. Proxifly Free Proxy List (Multi-region)
        (
            "proxifly/all",
            "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
        ),
        # 6. Roosterkid OpenProxyList (HTTPS & SOCKS5)
        (
            "roosterkid/HTTPS",
            "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        ),
        (
            "roosterkid/SOCKS5",
            "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
        ),
        # 7. HideIP.me Live Proxies
        (
            "hideip/HTTP",
            "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt",
        ),
        (
            "hideip/SOCKS5",
            "https://raw.githubusercontent.com/zloi-user/hideip.me/main/socks5.txt",
        ),
    ]

    TARGET_TEST_URL = "https://api.quarterfull.io/api/auth/app-version"

    @classmethod
    def scrape_candidates(cls, max_sources: Optional[int] = None) -> List[str]:
        """
        Mengunduh seluruh daftar kandidat dari ProxyScrape dan curated repo.
        Memfilter protocol yang tidak didukung httpx (socks4://) dan menormalisasi format.
        """
        candidates: List[str] = []
        seen = set()
        sources_to_use = cls.SOURCES[:max_sources] if max_sources else cls.SOURCES

        with httpx.Client(timeout=10.0) as client:
            for name, url in sources_to_use:
                try:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue
                    count = 0
                    for raw_line in resp.text.splitlines():
                        line = raw_line.strip()
                        if not line or line.startswith("#") or line.startswith("socks4"):
                            continue
                        # Normalisasi skema jika belum ada
                        if "://" not in line:
                            if "socks5" in url.lower():
                                line = f"socks5://{line}"
                            else:
                                line = f"http://{line}"

                        if line not in seen:
                            seen.add(line)
                            candidates.append(line)
                            count += 1
                    logger.info("Sumber [%s]: menemukan %d kandidat", name, count)
                except Exception as exc:
                    logger.debug("Gagal fetch dari %s: %s", name, exc)

        return candidates

    @classmethod
    def test_single_proxy(
        cls,
        proxy_url: str,
        timeout: float = 2.5,
    ) -> Optional[Dict[str, Any]]:
        """
        Menguji satu proxy secara independen langsung ke Quarterfull API.
        Mengembalikan Dict informasi jika sukses HTTP 200, atau None jika gagal/timeout.
        """
        try:
            t0 = time.time()
            with httpx.Client(proxy=proxy_url, timeout=timeout, http2=False) as client:
                resp = client.get(cls.TARGET_TEST_URL)
                if resp.status_code == 200:
                    latency = round((time.time() - t0) * 1000, 1)
                    return {
                        "proxy": proxy_url,
                        "latency_ms": latency,
                        "status": resp.status_code,
                    }
        except Exception:
            pass
        return None

    @classmethod
    def validate_batch(
        cls,
        candidates: List[str],
        target_count: int = 50,
        max_workers: int = 80,
        timeout: float = 2.2,
    ) -> List[Dict[str, Any]]:
        """
        Memvalidasi sekumpulan kandidat proxy secara konkuren menggunakan ThreadPoolExecutor.
        Mengalirkan kandidat secara kontinyu dan berhenti seketika saat kuota target_count terpenuhi.
        """
        import concurrent.futures

        verified: List[Dict[str, Any]] = []
        tested_count = 0
        cand_iter = iter(candidates)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        futures: Dict[concurrent.futures.Future, str] = {}

        try:
            # Isi awal pool sejumlah max_workers * 2
            for _ in range(min(len(candidates), max_workers * 2)):
                try:
                    p = next(cand_iter)
                    fut = executor.submit(cls.test_single_proxy, p, timeout)
                    futures[fut] = p
                except StopIteration:
                    break

            while futures and len(verified) < target_count:
                done, _ = concurrent.futures.wait(
                    list(futures.keys()),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for fut in done:
                    p_tested = futures.pop(fut, None)
                    tested_count += 1
                    try:
                        res = fut.result()
                        if res:
                            verified.append(res)
                            console.print(
                                f"  [bold green][ALIVE][/] #{len(verified)}/{target_count}: "
                                f"[cyan]{res['proxy']}[/] [yellow]({res['latency_ms']} ms)[/]"
                            )
                            if len(verified) >= target_count:
                                break
                    except Exception:
                        pass

                    # Tambahkan kandidat berikutnya ke executor jika kuota belum penuh
                    if len(verified) < target_count:
                        try:
                            next_p = next(cand_iter)
                            new_fut = executor.submit(cls.test_single_proxy, next_p, timeout)
                            futures[new_fut] = next_p
                        except StopIteration:
                            pass

        finally:
            for f in list(futures.keys()):
                f.cancel()
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                executor.shutdown(wait=False)

        # Urutkan berdasarkan latency tercepat
        verified.sort(key=lambda x: x.get("latency_ms", 9999))
        return verified

    @classmethod
    def scrape_and_update(
        cls,
        target_count: int = 50,
        output_file: str = "proxies.txt",
        max_workers: int = 80,
        show_table: bool = True,
    ) -> List[str]:
        """
        Alur terpadu: Scrape -> Validasi -> Simpan ke proxies.txt.
        """
        console.print("\n[bold cyan]>>> Mengambil Kandidat Proxy dari ProxyScrape & Top Sources...[/]")
        candidates = cls.scrape_candidates()
        console.print(f"Total kandidat terkumpul: [bold green]{len(candidates):,}[/] alamat proxy unik.")

        if not candidates:
            console.print("[red]Gagal mengunduh daftar proxy dari semua sumber.[/]")
            return []

        console.print(f"\n[yellow]Memvalidasi proxy aktif langsung ke Quarterfull API (Target: {target_count} proxy)...[/]\n")
        verified_data = cls.validate_batch(
            candidates,
            target_count=target_count,
            max_workers=max_workers,
            timeout=2.2,
        )

        if not verified_data:
            console.print("[red]Tidak ada proxy yang merespon dalam batas latency toleransi.[/]")
            return []

        verified_urls = [item["proxy"] for item in verified_data]
        mgr = ProxyManager(output_file, auto_replenish=False)
        mgr.save_proxies(verified_urls)

        console.print(f"\n[bold green][OK] Berhasil menemukan {len(verified_urls)} proxy aktif dan menyimpannya ke '{output_file}'![/]\n")

        if show_table:
            # Tampilkan tabel preview 10 proxy tercepat
            table = Table(title=f"Top 10 Proxy Tercepat (dari {len(verified_urls)} Proxy Aktif Terverifikasi)")
            table.add_column("No", style="dim", width=4)
            table.add_column("URL Proxy", style="bold cyan")
            table.add_column("Latency Target API", style="bold yellow")
            table.add_column("Status", style="green")

            for idx, item in enumerate(verified_data[:10], start=1):
                table.add_row(
                    str(idx),
                    item["proxy"],
                    f"{item['latency_ms']} ms",
                    "200 OK (Aktif)",
                )
            console.print(table)
        return verified_urls


def is_dead_or_proxy_error(exc: Optional[Any]) -> bool:
    """
    Mengecek secara komprehensif apakah suatu exception diakibatkan oleh proxy mati / gagal koneksi.
    Mendukung deteksi httpx ProxyError, ConnectError, ConnectTimeout, ReadTimeout, 407, 502/503/504,
    SOCKS handshake error, connection refused, reset by peer, dll.
    """
    if exc is None:
        return False
    if isinstance(exc, (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError)):
        return True
    err_str = str(exc).lower()
    dead_signals = (
        "proxy",
        "407",
        "socks",
        "connection reset",
        "connection refused",
        "actively refused",
        "tunnel",
        "502",
        "503",
        "504",
        "timed out",
        "timeout",
        "connect call failed",
        "remote host closed",
        "broken pipe",
        "no ips",
        "unreachable",
        "handshake failed",
    )
    return any(sig in err_str for sig in dead_signals)


# Instance singleton global
default_proxy_manager = ProxyManager("proxies.txt")


def get_global_proxy_manager() -> ProxyManager:
    """Mengambil instance global ProxyManager."""
    return default_proxy_manager


def test_proxy_cli() -> None:
    """Antarmuka CLI interaktif untuk manajemen, scraping, dan diagnostik proxy."""
    while True:
        console.print("\n[bold cyan]=== PUSAT MANAJEMEN & INTEGRASI PROXY (BOT TOODAT) ===[/]\n")

        mgr = ProxyManager("proxies.txt")
        if mgr.has_proxies:
            p_info = mgr.parsed_proxies[0]
            tipe_str = "Bright Data SuperProxy (ISP)" if p_info.is_brightdata else "Free / Standard HTTP & SOCKS5"
            console.print(f"Status: [bold green]{len(mgr.parsed_proxies)} Proxy Terdaftar[/] | Tipe: [bold yellow]{tipe_str}[/]")
            console.print(f"Sample: [dim]{mgr.parsed_proxies[0].get_masked_url()}[/]\n")
        else:
            console.print("[yellow]Status: DIRECT CONNECTION (Tanpa Proxy - proxies.txt kosong)[/]\n")

        console.print("Pilih Aksi:")
        console.print("  [1] [bold green]Scrape & Verifikasi Proxy Gratis Baru[/] (ProxyScrape v4 + Top Sources)")
        console.print("  [2] [bold cyan]Uji Diagnostik Seluruh Proxy di proxies.txt[/]")
        console.print("  [3] [bold white]Input / Tambah Proxy Manual ke proxies.txt[/]")
        console.print("  [4] [bold red]Kosongkan proxies.txt[/] (Gunakan Direct Connection)")
        console.print("  [0] Kembali ke Menu Utama\n")

        try:
            from rich.prompt import Prompt, IntPrompt
            choice = Prompt.ask("[bold green]?[/] Pilihan Anda", choices=["0", "1", "2", "3", "4"], default="1")
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "0":
            break

        elif choice == "1":
            try:
                target_n = IntPrompt.ask("[bold green]?[/] Berapa jumlah proxy aktif yang ingin dikumpulkan?", default=50)
            except Exception:
                target_n = 50
            FreeProxyScraper.scrape_and_update(target_count=target_n, output_file="proxies.txt")
            default_proxy_manager.load_proxies()

        elif choice == "2":
            if not mgr.has_proxies:
                console.print("[yellow]File 'proxies.txt' kosong. Tidak ada proxy yang dapat diuji.[/]")
                continue

            console.print(f"\n[cyan]Menguji konektivitas {min(len(mgr.parsed_proxies), 15)} proxy pertama...[/]")
            table = Table(title="Hasil Diagnostik Proxy")
            table.add_column("No", style="dim", width=4)
            table.add_column("Proxy URL", style="bold cyan")
            table.add_column("Target API", style="bold")
            table.add_column("Latency", style="yellow")
            table.add_column("Catatan", style="dim")

            for idx, p_item in enumerate(mgr.parsed_proxies[:15], start=1):
                res = mgr.test_proxy(proxy_url=p_item.raw_url)
                if res["success"]:
                    table.add_row(
                        str(idx),
                        p_item.get_masked_url(),
                        "[bold green]200 OK[/]",
                        f"{res['latency_ms']} ms",
                        res.get("country") or "OK",
                    )
                else:
                    table.add_row(
                        str(idx),
                        p_item.get_masked_url(),
                        "[bold red]FAIL[/]",
                        f"{res['latency_ms']} ms",
                        str(res.get("error", "Error"))[:30],
                    )

            console.print(table)

        elif choice == "3":
            manual_proxy = Prompt.ask("[bold green]?[/] Masukkan URL Proxy (contoh: http://ip:port atau socks5://ip:port)").strip()
            if manual_proxy:
                current_lines = [p.raw_url for p in mgr.parsed_proxies]
                if manual_proxy not in current_lines:
                    current_lines.insert(0, manual_proxy)
                    mgr.save_proxies(current_lines)
                    default_proxy_manager.load_proxies()
                    console.print(f"[bold green][OK] Proxy '{manual_proxy}' berhasil ditambahkan ke 'proxies.txt'![/]")
                else:
                    console.print("[yellow]Proxy tersebut sudah ada di proxies.txt.[/]")

        elif choice == "4":
            mgr.clear_proxies()
            default_proxy_manager.load_proxies()
            console.print("[bold green][OK] 'proxies.txt' berhasil dikosongkan. Bot akan menggunakan Direct Connection.[/]")


if __name__ == "__main__":
    import sys
    if "--scrape" in sys.argv or "--free" in sys.argv:
        count = 50
        for arg in sys.argv:
            if arg.isdigit():
                count = int(arg)
                break
        FreeProxyScraper.scrape_and_update(target_count=count)
    elif "--clear" in sys.argv:
        default_proxy_manager.clear_proxies()
        print("[OK] proxies.txt cleared. Using Direct Connection.")
    elif "--check" in sys.argv:
        test_proxy_cli()
    else:
        test_proxy_cli()

