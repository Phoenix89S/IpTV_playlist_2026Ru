#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import aiohttp
import pathlib
import subprocess
import json
from typing import Optional, List, Dict

EOL = "\r\n"

# ==========================
# НАСТРОЙКИ
# ==========================

UMA_DOMAINS = [
    "https://bl.uma.media",
    "https://bl2.uma.media",
    "https://bl3.uma.media",
]

# Диапазон ID UMA CDN
ID_START = 300000
ID_END = 700000

# Ветки качества UMA
HLS_VARIANTS = [
    "4614144_3",
    "4614144_2",
    "4614144_1",
    "4614144_0",
]

# Папка для результата
STREAMS_DIR = pathlib.Path("streams")
STREAMS_DIR.mkdir(parents=True, exist_ok=True)

CONCURRENCY = 150
TIMEOUT = 8


# ==========================
# МОДЕЛЬ КАНАЛА
# ==========================

class UmaChannel:
    def __init__(self, id_: int, url: str, extinf: str, quality: str):
        self.id = id_
        self.url = url
        self.extinf = extinf
        self.quality = quality

    def to_m3u_block(self) -> str:
        return f"{self.extinf}{EOL}{self.url}"


# ==========================
# HTTP КЛИЕНТ
# ==========================

class HttpClient:
    def __init__(self, timeout: int = TIMEOUT):
        self.timeout = timeout

    async def get_text(self, url: str) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=self.timeout) as resp:
                    if resp.status >= 200 and resp.status < 400:
                        return await resp.text()
                    return None
        except Exception:
            return None

    async def get_bytes(self, url: str) -> Optional[bytes]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=self.timeout) as resp:
                    if resp.status >= 200 and resp.status < 400:
                        return await resp.read()
                    return None
        except Exception:
            return None


# ==========================
# ПАРСЕР playlist.m3u8
# ==========================

def extract_extinf_and_url(m3u_text: str) -> Optional[Dict[str, str]]:
    lines = [l.strip() for l in m3u_text.splitlines() if l.strip()]
    extinf = None
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            extinf = line
            for j in range(i + 1, len(lines)):
                if not lines[j].startswith("#"):
                    return {"extinf": extinf, "url": lines[j]}
    return None


# ==========================
# ПРОВЕРКА ПОТОКА (ffprobe)
# ==========================

def ffprobe_check(data: bytes) -> bool:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if proc.returncode != 0:
            return False

        info = json.loads(proc.stdout or b"{}")
        streams = info.get("streams", [])
        return len(streams) > 0
    except Exception:
        return False


# ==========================
# СКАНЕР UMA CDN
# ==========================

class UmaScanner:
    def __init__(self, domains: List[str], variants: List[str], client: HttpClient):
        self.domains = domains
        self.variants = variants
        self.client = client
        self.found_channels: List[UmaChannel] = []

    async def scan_id(self, id_: int):
        for domain in self.domains:
            for variant in self.variants:

                url = f"{domain}/live/{id_}/HLS/{variant}/2/1/playlist.m3u8"
                m3u = await self.client.get_text(url)
                if not m3u:
                    continue

                info = extract_extinf_and_url(m3u)
                if not info:
                    continue

                # Проверяем поток через ffprobe
                data = await self.client.get_bytes(url)
                if not data:
                    continue

                if not ffprobe_check(data):
                    continue

                extinf = info["extinf"]
                quality = variant.split("_")[0]

                channel = UmaChannel(id_=id_, url=url, extinf=extinf, quality=quality)
                self.found_channels.append(channel)

                print(f"[FOUND] ID={id_} → {extinf}")
                return

    async def scan_range(self, start: int, end: int):
        sem = asyncio.Semaphore(CONCURRENCY)

        async def worker(id_):
            async with sem:
                await self.scan_id(id_)

        await asyncio.gather(*(worker(i) for i in range(start, end + 1)))

    def to_m3u(self) -> str:
        output = "#EXTM3U" + EOL
        for ch in self.found_channels:
            output += ch.to_m3u_block() + EOL
        return output


# ==========================
# MAIN
# ==========================

async def main():
    client = HttpClient(timeout=TIMEOUT)
    scanner = UmaScanner(UMA_DOMAINS, HLS_VARIANTS, client)

    print(f"🚀 Сканируем UMA CDN: ID {ID_START}..{ID_END}")
    await scanner.scan_range(ID_START, ID_END)

    print(f"✅ Найдено каналов: {len(scanner.found_channels)}")

    m3u_content = scanner.to_m3u()
    out_file = STREAMS_DIR / "uma_full_scan.m3u"
    out_file.write_text(m3u_content, encoding="utf-8")

    print(f"💾 Плейлист сохранён: {out_file}")


if __name__ == "__main__":
    asyncio.run(main())