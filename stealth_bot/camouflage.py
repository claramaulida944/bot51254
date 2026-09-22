"""
Modul Penyamaran & Penjelajah Katalog Organik (Camouflage Engine).
Mematahkan deteksi konsentrasi 1 novel tunggal (Target Concentration)
dan membangun rekam jejak discovery path resmi:
1. Menjelajahi katalog resmi (Rising, New Release, For You).
2. Membaca novel-novel populer lain secara acak sebagai lalu lintas kamuflase.
3. Menjaga rasio pembacaan novel target tetap proporsional (organik).
"""

import logging
import random
from typing import Any, Dict, List, Optional
import httpx

from .config import BASE_URL, CAMOUFLAGE_NOVEL_RATIO

logger = logging.getLogger("StealthCamouflage")


class CamouflageEngine:
    """Mengelola discovery path dan pemilihan novel kamuflase dari katalog resmi."""

    DISCOVERY_ENDPOINTS = [
        "/api/v1/bookstore/sections/rising",
        "/api/v1/bookstore/sections/new-release-best",
        "/api/v1/bookstore/for-you-shelf?limit=18",
        "/api/v1/novels?limit=24",
    ]

    def __init__(self, target_novel_id: str):
        self.target_novel_id = target_novel_id
        self.cached_novels: List[Dict[str, Any]] = []

    async def fetch_catalog_novels(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Mengambil daftar novel populer yang sedang tayang di platform secara langsung."""
        if self.cached_novels:
            return self.cached_novels

        collected = []
        endpoint = random.choice(self.DISCOVERY_ENDPOINTS)
        try:
            resp = await client.get(endpoint)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", []) or data.get("novels", [])
                if isinstance(data, list):
                    items = data

                for item in items:
                    novel_id = item.get("hash_id") or item.get("id")
                    title = item.get("title", "")
                    if novel_id and novel_id != self.target_novel_id:
                        collected.append({
                            "hash_id": novel_id,
                            "title": title,
                        })
        except Exception as exc:
            logger.debug("Gagal mengambil katalog kamuflase (%s): %s", endpoint, exc)

        # Fallback jika gagal mengambil dari endpoint (novel-novel nyata terpopuler)
        if not collected:
            collected = [
                {"hash_id": "VqQK9b6Z99bEvYnG", "title": "Billionaire's Secret"},
                {"hash_id": "kpQJ0dNk7V8eLOvE", "title": "Moonlight Shadow"},
                {"hash_id": "DXMVyb8jKjevAZEJ", "title": "Silent Promises"},
                {"hash_id": "kQWjnegmEDbwZ1p0", "title": "Destined to Reign"},
            ]

        self.cached_novels = collected
        return self.cached_novels

    def pick_next_action(self, consecutive_target_count: int = 0) -> str:
        """
        Menentukan apakah bab berikutnya harus membaca novel target atau novel kamuflase:
        - Memastikan rasio target tidak melebihi batas anomali.
        - Membatasi pembacaan target beruntun.
        """
        if consecutive_target_count >= 3:
            return "camouflage"

        # Kemungkinan membaca kamuflase berdasarkan rasio
        if random.random() < CAMOUFLAGE_NOVEL_RATIO:
            return "camouflage"
        return "target"

    async def get_random_camouflage_novel(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Mengambil satu novel kamuflase acak dari katalog."""
        novels = await self.fetch_catalog_novels(client)
        return random.choice(novels)
