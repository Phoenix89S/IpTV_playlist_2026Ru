#!/usr/bin/env python3
"""
Ultra IPTV Checker & Alternative Stream Finder  v2.1
====================================================
Главное отличие от Ksenia: АКТИВНЫЙ ПОИСК ПО ИНТЕРНЕТУ.

При флаге --discover скрипт сам скачивает публичные плейлисты
(iptv-org, Free-TV, smolnp и др.) и ищет рабочие альтернативные
потоки для каналов по всему интернету, а не только из того,
что ты ему дал.

Combines the best ideas from:
  - dearbulut/iptv, bjzhou, freearhey, vitter, m3u-proxy
  - smolnp/Ksenia M3U Editor (нормализация, fuzzy, кодировки)
  - smolnp/IPTVru (stable-пайплайн)

Usage:
  # Только твои источники (как Ksenia)
  python ultra_iptv_checker.py -s mylist.m3u --deep

  # АКТИВНЫЙ ПОИСК ПО ИНТЕРНЕТУ + твои источники
  python ultra_iptv_checker.py -s mylist.m3u --discover --deep --top 3

  # Только поиск по интернету (без своих файлов)
  python ultra_iptv_checker.py --discover --workers 40

  # 18+ / эротика фильтруется автоматически (встроенный список)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)
from rich.table import Table

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 10
DEFAULT_WORKERS = 80
DEFAULT_TOP_N = 12
DEFAULT_FUZZY = 0.78          # порог похожести названий (Ksenia-style)
FFPROBE_TIMEOUT = 12

# Публичные источники в интернете (активный поиск альтернатив)
# Максимально расширенный список (Россия + СНГ + мир)
# При --discover скрипт сам скачивает ВСЕ эти источники
PUBLIC_INTERNET_SOURCES = [
    # === Глобальные (максимум охвата → к 10k) ===
    "https://iptv-org.github.io/iptv/index.m3u",
    "https://iptv-org.github.io/iptv/index.category.m3u",
    "https://iptv-org.github.io/iptv/index.country.m3u",
    "https://iptv-org.github.io/iptv/index.language.m3u",
    "https://iptv-org.github.io/iptv/languages/rus.m3u",
    "https://iptv-org.github.io/iptv/regions/cis.m3u",
    "https://iptv-org.github.io/iptv/regions/cas.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
    # dearbulut — ~10k рабочих потоков
    "https://dearbulut.github.io/iptv/playlists/best.m3u",
    "https://dearbulut.github.io/iptv/playlists/online.m3u",
    "https://dearbulut.github.io/iptv/playlists/index.m3u",
    "https://dearbulut.github.io/iptv/playlists/country/ru.m3u",
    "https://dearbulut.github.io/iptv/playlists/country/kz.m3u",
    "https://dearbulut.github.io/iptv/playlists/country/uz.m3u",
    "https://dearbulut.github.io/iptv/playlists/country/kg.m3u",
    "https://dearbulut.github.io/iptv/playlists/country/tj.m3u",
    "https://dearbulut.github.io/iptv/playlists/country/mn.m3u",
    "https://dearbulut.github.io/iptv/playlists/country/by.m3u",
    "https://dearbulut.github.io/iptv/playlists/country/ua.m3u",

    # === Россия ===
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVru.m3u",
    "https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVstable.m3u8",
    "https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVmir.m3u8",
    "https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVdonor.m3u",
    "https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPRadio.m3u",
    "https://naggdd.github.io/iptv/ru.m3u",
    "https://naggdd.github.io/iptv/music.m3u",
    "https://naggdd.github.io/iptv/cartoons.m3u",
    "https://raw.githubusercontent.com/IPTVRU2026/IPTVMIR/main/IPTV_MEGA_PLAYLIST.m3u",

    # === Казахстан (провайдеры) ===
    "https://iptv-org.github.io/iptv/countries/kz.m3u",
    "https://aidoseg.github.io/qazaqiptv/playlist.m3u8",
    "https://raw.githubusercontent.com/Monoloshka/iptv/main/BeeTV.m3u",
    "https://raw.githubusercontent.com/Monoloshka/iptv/main/full-iptv.m3u",
    "https://raw.githubusercontent.com/Monoloshka/iptv/main/tv.m3u",

    # === Узбекистан ===
    "https://iptv-org.github.io/iptv/countries/uz.m3u",

    # === Кыргызстан ===
    "https://iptv-org.github.io/iptv/countries/kg.m3u",

    # === Таджикистан ===
    "https://iptv-org.github.io/iptv/countries/tj.m3u",

    # === Монголия ===
    "https://iptv-org.github.io/iptv/countries/mn.m3u",

    # === Украина + остальные СНГ ===
    "https://iptv-org.github.io/iptv/countries/ua.m3u",
    "https://myplaylists.github.io/iptv/ua.m3u",
    "https://myplaylists.github.io/iptv/ru.m3u",
    "https://iptv-org.github.io/iptv/countries/by.m3u",
    "https://iptv-org.github.io/iptv/countries/am.m3u",
    "https://iptv-org.github.io/iptv/countries/ge.m3u",
    "https://iptv-org.github.io/iptv/countries/az.m3u",
    "https://iptv-org.github.io/iptv/countries/md.m3u",
    "https://iptv-org.github.io/iptv/countries/tm.m3u",

    # === sat-portal / iptv.org.ua (много плейлистов, в т.ч. СНГ) ===
    "https://iptv.org.ua/iptv/avto.m3u",
    "https://iptv.org.ua/iptv/avto-full.m3u",
    "https://iptv.org.ua/iptv/tva1.m3u",
    "https://iptv.org.ua/iptv/provayder.m3u",
    "https://iptv.org.ua/iptv/avtomini.m3u",

    # === Категории ===
    "https://iptv-org.github.io/iptv/categories/news.m3u",
    "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "https://iptv-org.github.io/iptv/categories/movies.m3u",
    "https://iptv-org.github.io/iptv/categories/entertainment.m3u",
    "https://iptv-org.github.io/iptv/categories/kids.m3u",
    "https://iptv-org.github.io/iptv/categories/music.m3u",
    "https://iptv-org.github.io/iptv/categories/documentary.m3u",
    "https://iptv-org.github.io/iptv/categories/general.m3u",
]

console = Console()
log = logging.getLogger("ultra_iptv")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class StreamInfo:
    url: str
    source: str = ""          # откуда пришёл поток (для приоритета)
    latency_ms: float = 99999.0
    http_ok: bool = False
    status_code: int = 0
    resolution: str = ""
    width: int = 0
    height: int = 0
    codec: str = ""
    bitrate_kbps: float = 0.0
    score: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Channel:
    name: str
    group: str = "Undefined"
    logo: str = ""
    tvg_id: str = ""
    streams: list[StreamInfo] = field(default_factory=list)

    @property
    def best_stream(self) -> Optional[StreamInfo]:
        alive = [s for s in self.streams if s.http_ok]
        if not alive:
            return None
        return max(alive, key=lambda s: s.score)

    @property
    def alive_streams(self) -> list[StreamInfo]:
        return sorted(
            [s for s in self.streams if s.http_ok],
            key=lambda s: s.score,
            reverse=True,
        )


# ---------------------------------------------------------------------------
# Ksenia-style smart normalization
# ---------------------------------------------------------------------------
QUALITY_RE = re.compile(
    r"[\s\-_]*(4[Kk]|UHD|FHD|HD|SD|HEVC|H\.?265|H\.?264|AVC|"
    r"50[Ff]ps|60[Ff]ps|\d{3,4}[pP]|HQ|LQ|Full\s*HD)[\s\-_]*",
    re.IGNORECASE,
)
EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)
GEO_RE = re.compile(
    r"[\s\-_]*(Geo[\s\-]?blocked|Only\s*(RU|BY|KZ|UA|EU|US)|"
    r"\[(RU|BY|KZ|UA)\])[\s\-_]*",
    re.IGNORECASE,
)

# Встроенный чёрный список ТОЛЬКО для 18+ / эротики
ADULT_KEYWORDS = [
    "xxx", "porn", "porno", "erotica", "эротика", "эротический", "adult",
    "18+", "18 +", "+18", "sex", "sexy", "hentai", "brazzers", "playboy",
    "blue hentai", "redlight", "red light", "private", "reality kings",
    "bonga", "cam4", "chaturbate", "onlyfans", "xhamster", "xvideos",
    "pornhub", "youporn", "redtube", "tube8", "spankbang", "xnxx",
    "nude", "naked", "strip", "striptease", "fetish", "bdsm", "hardcore",
    "softcore", "amateur", "milf", "teen sex", "lesbian", "gay porn",
    "busty", "big tits", "anal", "oral", "cum", "squirting",
    "русская эротика", "русское порно", "adult channel", "adult tv",
    "night club", "nightclub", "sexy night", "hot night", "private gold",
    "dorcel", "private platinum", "vivid", "hustler", "penthouse",
]

BRACKETS_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")


def normalize_name(name: str) -> str:
    """
    Умная нормализация в стиле Ksenia M3U Editor:
    - убирает качество (720p, 1080p, 4K, HD...)
    - убирает эмодзи
    - убирает geo-теги
    - убирает содержимое скобок
    - приводит к lower + схлопывает пробелы
    """
    if not name:
        return ""
    n = name.strip()
    n = EMOJI_RE.sub(" ", n)
    n = QUALITY_RE.sub(" ", n)
    n = GEO_RE.sub(" ", n)
    n = BRACKETS_RE.sub(" ", n)
    n = re.sub(r"[^\w\sа-яА-ЯёЁ\-]", " ", n, flags=re.UNICODE)
    n = re.sub(r"\s+", " ", n).strip().lower()
    return n


def fuzzy_ratio(a: str, b: str) -> float:
    """SequenceMatcher ratio (как в Ksenia)."""
    return SequenceMatcher(None, a, b).ratio()


def find_best_key(name: str, existing_keys: dict[str, str], threshold: float) -> Optional[str]:
    """Находит наиболее похожий ключ среди уже существующих (fuzzy)."""
    norm = normalize_name(name)
    if not norm:
        return None
    if norm in existing_keys:
        return norm

    best_key = None
    best_score = 0.0
    for key in existing_keys:
        score = fuzzy_ratio(norm, key)
        if score > best_score and score >= threshold:
            best_score = score
            best_key = key
    return best_key


# ---------------------------------------------------------------------------
# Parsing (with encoding support)
# ---------------------------------------------------------------------------
def detect_and_decode(raw: bytes) -> str:
    """Пробуем UTF-8 → Windows-1251 → CP1251 → latin-1."""
    for enc in ("utf-8", "windows-1251", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def parse_m3u(content: str) -> list[dict]:
    channels = []
    lines = content.splitlines()
    i = 0
    current = {}

    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#EXTM3U"):
            i += 1
            continue

        if line.startswith("#EXTINF:"):
            info = {"name": "", "group": "Undefined", "logo": "", "tvg_id": ""}
            m = re.search(r'tvg-id="([^"]*)"', line, re.I)
            if m:
                info["tvg_id"] = m.group(1)
            m = re.search(r'tvg-logo="([^"]*)"', line, re.I)
            if m:
                info["logo"] = m.group(1)
            m = re.search(r'group-title="([^"]*)"', line, re.I)
            if m:
                info["group"] = m.group(1) or "Undefined"
            if "," in line:
                info["name"] = line.split(",", 1)[1].strip()
            current = info
            i += 1
            continue

        if line.startswith("#"):
            i += 1
            continue

        if current and line.startswith(("http://", "https://", "rtmp://", "rtsp://", "udp://")):
            current["url"] = line
            channels.append(current)
            current = {}
        i += 1
    return channels


def parse_txt(content: str) -> list[dict]:
    channels = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "," in line:
            parts = line.split(",", 1)
        elif "|" in line:
            parts = line.split("|", 1)
        else:
            continue
        if len(parts) == 2:
            channels.append({
                "name": parts[0].strip(),
                "url": parts[1].strip(),
                "group": "Undefined",
                "logo": "",
                "tvg_id": "",
            })
    return channels


def load_sources(
    sources: list[str],
    timeout: int = 15,
    source_priority: Optional[dict[str, int]] = None,
) -> list[dict]:
    """Загрузка с поддержкой разных кодировок + приоритет источника."""
    all_entries = []
    console.print(f"[bold cyan]Loading {len(sources)} source(s)...[/]")

    for idx, src in enumerate(sources):
        content = ""
        prio = 10
        if source_priority and src in source_priority:
            prio = source_priority[src]
        else:
            prio = 10 - idx  # первый источник чуть приоритетнее

        if src.startswith(("http://", "https://")):
            try:
                import requests
                r = requests.get(
                    src,
                    timeout=timeout,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                )
                r.raise_for_status()
                content = detect_and_decode(r.content)
            except Exception as e:
                log.error(f"Cannot load {src}: {e}")
                continue
        else:
            path = Path(src)
            if not path.exists():
                log.error(f"File not found: {src}")
                continue
            raw = path.read_bytes()
            content = detect_and_decode(raw)

        if not content:
            continue

        if "#EXTM3U" in content[:300] or "#EXTINF" in content:
            entries = parse_m3u(content)
        else:
            entries = parse_txt(content)

        for e in entries:
            e["_source"] = src
            e["_priority"] = prio

        console.print(f"  → {src}: [green]{len(entries)}[/] channels (prio={prio})")
        all_entries.extend(entries)

    return all_entries



def run_ffprobe(url: str, timeout: int = FFPROBE_TIMEOUT) -> dict:
    if not shutil.which("ffprobe"):
        return {}
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        "-probesize", "500000",
        "-analyzeduration", "2000000",
        "-timeout", str(timeout * 1_000_000),
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        if result.returncode != 0:
            return {"error": (result.stderr or "ffprobe failed")[:100]}
        data = json.loads(result.stdout)
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        fmt = data.get("format", {})
        info = {}
        if video:
            info["width"] = int(video.get("width") or 0)
            info["height"] = int(video.get("height") or 0)
            info["codec"] = video.get("codec_name", "")
            info["resolution"] = f"{info['width']}x{info['height']}" if info["width"] else ""
        if fmt.get("bit_rate"):
            try:
                info["bitrate_kbps"] = round(int(fmt["bit_rate"]) / 1000, 1)
            except Exception:
                pass
        return info
    except subprocess.TimeoutExpired:
        return {"error": "ffprobe timeout"}
    except Exception as e:
        return {"error": str(e)[:80]}


def calculate_score(stream: StreamInfo) -> float:
    if not stream.http_ok:
        return 0.0

    lat = stream.latency_ms
    if lat < 200:
        lat_score = 40
    elif lat < 500:
        lat_score = 35
    elif lat < 1000:
        lat_score = 25
    elif lat < 2000:
        lat_score = 15
    else:
        lat_score = 5

    h = stream.height
    if h >= 2160:
        res_score = 40
    elif h >= 1080:
        res_score = 35
    elif h >= 720:
        res_score = 25
    elif h >= 480:
        res_score = 15
    elif h > 0:
        res_score = 8
    else:
        res_score = 10

    br = stream.bitrate_kbps
    if br > 5000:
        br_score = 15
    elif br > 2500:
        br_score = 12
    elif br > 1000:
        br_score = 8
    elif br > 0:
        br_score = 4
    else:
        br_score = 5

    codec_bonus = 5 if stream.codec in ("h264", "avc", "hevc", "h265") else 0
    return lat_score + res_score + br_score + codec_bonus



async def check_http(
    session: aiohttp.ClientSession,
    url: str,
    timeout: float,
    headers: dict,
    ssl_verify: bool,
) -> tuple[bool, int, float, str]:
    """Быстрая HTTP-проверка потока (latency + status)."""
    start = time.perf_counter()
    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=ssl_verify,
            allow_redirects=True,
        ) as resp:
            # читаем чуть-чуть тела, чтобы убедиться что поток реально отдаёт данные
            await resp.content.read(1024)
            latency = (time.perf_counter() - start) * 1000
            ok = 200 <= resp.status < 400
            return ok, resp.status, latency, "" if ok else f"HTTP {resp.status}"
    except asyncio.TimeoutError:
        return False, 0, (time.perf_counter() - start) * 1000, "timeout"
    except aiohttp.ClientError as e:
        return False, 0, (time.perf_counter() - start) * 1000, str(e)[:80]
    except Exception as e:
        return False, 0, (time.perf_counter() - start) * 1000, str(e)[:80]


async def check_stream(
    session: aiohttp.ClientSession,
    stream: StreamInfo,
    timeout: float,
    headers: dict,
    deep: bool,
    ssl_verify: bool,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        ok, status, latency, err = await check_http(
            session, stream.url, timeout, headers, ssl_verify
        )
        stream.http_ok = ok
        stream.status_code = status
        stream.latency_ms = latency
        stream.error = err

        if ok and deep:
            loop = asyncio.get_running_loop()
            probe = await loop.run_in_executor(None, run_ffprobe, stream.url)
            if "error" not in probe:
                stream.width = probe.get("width", 0)
                stream.height = probe.get("height", 0)
                stream.resolution = probe.get("resolution", "")
                stream.codec = probe.get("codec", "")
                stream.bitrate_kbps = probe.get("bitrate_kbps", 0.0)
            else:
                stream.error = probe.get("error", "")

        stream.score = calculate_score(stream)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
async def process_channels(
    channels: dict[str, Channel],
    workers: int,
    timeout: float,
    deep: bool,
    user_agent: str,
    ssl_verify: bool,
) -> None:
    headers = {"User-Agent": user_agent}
    semaphore = asyncio.Semaphore(workers)
    connector = aiohttp.TCPConnector(limit=workers, ttl_dns_cache=300, ssl=ssl_verify)
    timeout_cfg = aiohttp.ClientTimeout(total=timeout + 5)

    total = sum(len(ch.streams) for ch in channels.values())

    async with aiohttp.ClientSession(connector=connector, timeout=timeout_cfg) as session:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Checking streams...", total=total)

            async def wrapped(stream: StreamInfo):
                await check_stream(
                    session, stream, timeout, headers, deep, ssl_verify, semaphore
                )
                progress.advance(task_id)

            coros = [wrapped(s) for ch in channels.values() for s in ch.streams]
            await asyncio.gather(*coros)


def build_channels(
    entries: list[dict],
    fuzzy_threshold: float = 0.72,
    max_per_name: int = 30,
) -> dict[str, Channel]:
    """
    Группировка с fuzzy-matching (Ksenia-style).
    Ключ словаря — нормализованное имя.
    """
    groups: dict[str, Channel] = {}
    key_to_display: dict[str, str] = {}  # norm → original name
    seen_urls: set[str] = set()

    for e in entries:
        name = e.get("name") or "Unknown"
        url = (e.get("url") or "").strip()
        if not url or url in seen_urls:
            continue

        # Только фильтр 18+ / эротика
        name_l = name.lower()
        norm_check = normalize_name(name)
        if any(kw in name_l or kw in norm_check for kw in ADULT_KEYWORDS):
            continue
        group_l = (e.get("group") or "").lower()
        if any(kw in group_l for kw in ("adult", "xxx", "erotica", "эротика", "18+", "porn")):
            continue

        seen_urls.add(url)

        # fuzzy find existing group
        matched_key = find_best_key(name, key_to_display, fuzzy_threshold)
        if matched_key is None:
            matched_key = normalize_name(name) or name.lower()
            key_to_display[matched_key] = name
            groups[matched_key] = Channel(
                name=name,
                group=e.get("group") or "Undefined",
                logo=e.get("logo") or "",
                tvg_id=e.get("tvg_id") or "",
            )

        ch = groups[matched_key]
        if len(ch.streams) < max_per_name:
            ch.streams.append(StreamInfo(
                url=url,
                source=e.get("_source", ""),
            ))

    return groups


def write_m3u(
    channels: dict[str, Channel],
    path: Path,
    mode: str = "best",
    top_n: int = 3,
    min_score: float = 0.0,
) -> int:
    lines = ["#EXTM3U"]
    count = 0
    sorted_chs = sorted(channels.values(), key=lambda c: c.name.lower())

    for ch in sorted_chs:
        alive = [s for s in ch.alive_streams if s.score >= min_score]
        if not alive:
            continue

        if mode == "best":
            selected = alive[:1]
        elif mode == "stable":
            # только те, у кого latency < 1500 и score высокий
            selected = [s for s in alive if s.latency_ms < 1500][:1]
            if not selected:
                selected = alive[:1]
        elif mode == "online":
            selected = alive
        else:  # all
            selected = alive[:top_n]

        for idx, s in enumerate(selected):
            attrs = []
            if ch.tvg_id:
                attrs.append(f'tvg-id="{ch.tvg_id}"')
            if ch.logo:
                attrs.append(f'tvg-logo="{ch.logo}"')
            attrs.append(f'group-title="{ch.group}"')

            extra = ""
            if len(selected) > 1:
                extra = f" [{idx+1}/{len(selected)}]"
            if s.resolution:
                extra += f" {s.resolution}"
            if s.latency_ms < 9000:
                extra += f" {int(s.latency_ms)}ms"

            name = f"{ch.name}{extra}"
            lines.append(f'#EXTINF:-1 {" ".join(attrs)},{name}')
            lines.append(s.url)
            count += 1

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def print_summary(channels: dict[str, Channel]) -> None:
    total_ch = len(channels)
    alive_ch = sum(1 for c in channels.values() if c.alive_streams)
    total_streams = sum(len(c.streams) for c in channels.values())
    alive_streams = sum(len(c.alive_streams) for c in channels.values())

    table = Table(title="Ultra IPTV Checker v2 — Summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Channels total", str(total_ch))
    table.add_row("Channels with ≥1 working stream", str(alive_ch))
    table.add_row("Streams checked", str(total_streams))
    table.add_row("Working streams", str(alive_streams))
    if alive_streams:
        avg_lat = sum(s.latency_ms for c in channels.values() for s in c.alive_streams) / alive_streams
        table.add_row("Avg latency (working)", f"{avg_lat:.0f} ms")
    console.print(table)

    top = []
    for c in channels.values():
        best = c.best_stream
        if best:
            top.append((c.name, best.score, best.latency_ms, best.resolution or "?", best.url[:55]))
    top.sort(key=lambda x: x[1], reverse=True)

    if top:
        console.print("\n[bold]Top 10 highest-scored streams:[/]")
        t2 = Table()
        t2.add_column("Channel")
        t2.add_column("Score", justify="right")
        t2.add_column("Latency", justify="right")
        t2.add_column("Res")
        t2.add_column("URL (truncated)")
        for name, score, lat, res, url in top[:10]:
            t2.add_row(name[:35], f"{score:.0f}", f"{lat:.0f}ms", res, url)
        console.print(t2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ultra IPTV Checker v2 — поиск альтернатив по всему интернету",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-s", "--source", action="append", default=[],
                        help="M3U/TXT file or URL (можно несколько раз). Необязательно, если есть --discover")
    parser.add_argument("-o", "--output-dir", default="./output")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--deep", action="store_true", help="ffprobe deep check")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                        help="Мин. запасных потоков на канал в all_with_alts (default 12)")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--max-alts", type=int, default=30,
                        help="Макс. альтернатив на канал до проверки (для больших списков)")
    parser.add_argument("--fuzzy", type=float, default=DEFAULT_FUZZY,
                        help=f"Fuzzy name match threshold 0.0-1.0 (default {DEFAULT_FUZZY})")
    parser.add_argument("--no-ssl-verify", action="store_true",
                        help="Ignore SSL certificate errors (Ksenia-style)")
    parser.add_argument("--discover", action="store_true",
                        help="АКТИВНЫЙ ПОИСК ПО ИНТЕРНЕТУ: автоматически скачивает "
                             "публичные плейлисты (iptv-org, Free-TV, smolnp и др.) "
                             "и ищет альтернативные потоки")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    # Собираем все источники
    sources = list(args.source or [])
    if args.discover:
        console.print("[bold magenta]Режим --discover: ищем альтернативы по всему интернету...[/]")
        sources.extend(PUBLIC_INTERNET_SOURCES)
        # убираем дубли
        sources = list(dict.fromkeys(sources))
    if not sources:
        console.print("[red]Нужен хотя бы один источник (-s) или флаг --discover[/]")
        sys.exit(1)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    
    # 1. Load (включая публичные источники при --discover)
    entries = load_sources(sources, timeout=int(args.timeout) + 5)
    if not entries:
        console.print("[red]No channels loaded. Exiting.[/]")
        sys.exit(1)
    console.print(f"\n[bold]Total raw entries (после поиска по интернету):[/] {len(entries)}")

    # 2. Group with fuzzy + adult filter
    channels = build_channels(
        entries,
        fuzzy_threshold=args.fuzzy,
        max_per_name=args.max_alts,
    )
    console.print(f"[bold]Unique channels after fuzzy grouping:[/] {len(channels)}")

    # 3. Check
    console.print(f"\n[bold cyan]Starting check[/] (workers={args.workers}, deep={args.deep}, fuzzy={args.fuzzy})...")
    start = time.time()
    asyncio.run(process_channels(
        channels,
        workers=args.workers,
        timeout=args.timeout,
        deep=args.deep,
        user_agent=args.user_agent,
        ssl_verify=not args.no_ssl_verify,
    ))
    elapsed = time.time() - start
    console.print(f"[green]Check finished in {elapsed:.1f}s[/]\n")

    # 4. Write
    n_best = write_m3u(channels, out_dir / "best.m3u", mode="best")
    n_stable = write_m3u(channels, out_dir / "stable.m3u", mode="stable")
    n_online = write_m3u(channels, out_dir / "online.m3u", mode="online")
    n_alts = write_m3u(channels, out_dir / "all_with_alts.m3u", mode="all", top_n=args.top)

    console.print(f"[bold green]Playlists written:[/]")
    console.print(f"  • {out_dir / 'best.m3u'}          → {n_best}  (1 best per channel)")
    console.print(f"  • {out_dir / 'stable.m3u'}        → {n_stable}  (low latency only)")
    console.print(f"  • {out_dir / 'online.m3u'}        → {n_online}  (all working)")
    console.print(f"  • {out_dir / 'all_with_alts.m3u'} → {n_alts}  (up to {args.top} alternatives)")

    print_summary(channels)

    # JSON dump
    json_path = out_dir / "results.json"
    data = {
        key: {
            "name": ch.name,
            "group": ch.group,
            "streams": [s.to_dict() for s in ch.streams],
        }
        for key, ch in channels.items()
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\nDetailed results → [cyan]{json_path}[/]")


if __name__ == "__main__":
    main()
