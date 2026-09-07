#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IpTV_fallback.py
================

Модуль автоматического восстановления IPTV-потоков.

Основная задача:
    1. Загрузить исходный M3U.
    2. Проверить КАЖДЫЙ поток КАЖДОГО канала.
    3. Для мёртвых потоков собрать кандидатов из публичных источников.
    4. Отдельно проверить известные NGENIX / Rostelecom / Zabava / Wink
       URL-шаблоны и уже обнаруженные узлы/алиасы.
    5. Генерировать варианты:
           http/https
           /hls/CHANNEL/variant.m3u8
           известные альтернативные окончания
    6. Строго сопоставлять кандидата с исходным каналом.
    7. Проверять HLS.
    8. Сохранять ВСЕ найденные кандидаты в SQLite, не затирая старые.
    9. Выбирать лучший рабочий fallback.
   10. Создавать:
           merge_auto.m3u
           merge_auto_1.m3u
           merge_auto_2.m3u
           ...
       не затирая предыдущие результаты.
   11. Сохранять подробный JSON-отчёт.
   12. При следующем запуске использовать накопленную БД.

Пример:

    python3 IpTV_fallback.py \
        --playlist input.m3u \
        --output-dir output

URL вместо локального файла:

    python3 IpTV_fallback.py \
        --playlist https://example.org/public.m3u

Дополнительно:

    python3 IpTV_fallback.py \
        --playlist input.m3u \
        --output-dir output \
        --workers 32 \
        --timeout 5

Мониторинг:

    python3 IpTV_fallback.py \
        --playlist input.m3u \
        --output-dir output \
        --watch \
        --interval 300

Примечание:
    Проверяются только публичные URL и источники, перечисленные в конфигурации.
    Код не использует логины/пароли Xtream и не пытается получать доступ
    к закрытым потокам.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


# ============================================================================
# VERSION
# ============================================================================

VERSION = "IpTV-fallback-2.0"
DB_SCHEMA_VERSION = 3


# ============================================================================
# NETWORK CONFIGURATION
# ============================================================================

DEFAULT_TIMEOUT = 6
DISCOVERY_TIMEOUT = 20
DEFAULT_WORKERS = 32

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "close",
}

# User requested Wink/Zabava style UA.
# Kept configurable rather than forcing a proprietary identity everywhere.
WINK_USER_AGENT = "Wink/1.0"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# ============================================================================
# PUBLIC DISCOVERY SOURCES
# ============================================================================

PUBLIC_PLAYLIST_SOURCES = [
    # iptv-org
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://iptv-org.github.io/iptv/languages/rus.m3u",

    # naggdd
    "https://naggdd.github.io/iptv/ru.m3u",

    # Free-TV/IPTV
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",

    # DropTV / IPTVRU2026
    "https://raw.githubusercontent.com/IPTVRU2026/IPTVMIR/main/IPTV_MEGA_PLAYLIST.m3u",

    # smolnp
    "https://smolnp.github.io/IPTVru/IPTVru.m3u",
    "https://smolnp.github.io/IPTVru/IPTVstable.m3u8",
    "https://smolnp.github.io/IPTVru/IPTVmir.m3u8",
    "https://smolnp.github.io/IPTVru/IPTVavto.m3u",

    # MaximKiselev
    "https://github.com/MaximKiselev/iptv/raw/main/playlist.m3u",

    # KITTV
    "https://kittv.ru/rus.m3u",

    # TVPass
    "https://tvpass.org/playlist/m3u",
    "https://raw.githubusercontent.com/phosani/tvpass/refs/heads/main/tvpasshd.m3u",

    # apsattv public collections
    "https://www.apsattv.com/distro.m3u",
    "https://www.apsattv.com/vizio.m3u",
    "https://www.apsattv.com/orka.m3u",
    "https://www.apsattv.com/cineverse.m3u",
    "https://www.apsattv.com/firetv.m3u",

    # Free codecs generated list
    "https://www.free-codecs.com/app/iptv-finder/playlist/?c=ru",
]


# ============================================================================
# OPTIONAL PUBLIC SOURCE INDEXES
# ============================================================================

PUBLIC_INDEX_PAGES = [
    "https://github.com/iptv-org/iptv",
    "https://github.com/Free-TV/IPTV",
    "https://github.com/IPTVRU2026/IPTVMIR",
    "https://github.com/naggdd/iptv",
    "https://github.com/smolnp/IPTVru",
    "https://github.com/MaximKiselev/iptv",
    "https://github.com/CrocoUser/zabava-project",
]


# ============================================================================
# PROVIDER / CDN HOSTS
# ============================================================================

# IMPORTANT:
# This is an explicit allowlist of known public hostnames.
# It does not perform arbitrary network scanning.

KNOWN_NGENIX_HOSTS = [
    "zabava-htlive.cdn.ngenix.net",
    "rt-nw-klgr-htlive.cdn.ngenix.net",

    # Explicitly supplied / previously discovered s-nodes can be placed here.
    "s70790.cdn.ngenix.net",
]

KNOWN_ROSTELECOM_HOSTS = [
    "hlsstr01.svc.iptv.rt.ru",
]

KNOWN_ZABAVA_HOSTS = [
    "zabava-htlive.cdn.ngenix.net",
]

KNOWN_WINK_HOSTS = [
    "wink.ru",
]


# ============================================================================
# NGENIX NODE PATTERNS
# ============================================================================

NGENIX_NODE_RE = re.compile(
    r"\b(s\d{4,6})\b",
    re.IGNORECASE,
)

NGENIX_HOST_RE = re.compile(
    r"(s\d{4,6})(?:\.cdn)?\.ngenix\.net",
    re.IGNORECASE,
)


# ============================================================================
# CHANNEL ALIASES
# ============================================================================

CHANNEL_ALIASES: Dict[str, List[str]] = {

    "1tv": [
        "1 канал",
        "первый канал",
        "1tv",
        "one",
        "channel one",
    ],

    "russia1": [
        "россия 1",
        "россия1",
        "russia 1",
        "russia1",
    ],

    "karusel": [
        "карусель",
        "karusel",
    ],

    "rentv": [
        "рен тв",
        "рен-тв",
        "ren tv",
        "rentv",
    ],

    "tv3": [
        "тв-3",
        "тв 3",
        "tv3",
        "tv-3",
    ],

    "mir": [
        "мир",
        "mir",
    ],

    "ocean_tv": [
        "ocean tv",
        "океан",
        "океан тв",
    ],

    "viasat_nature": [
        "viju nature",
        "viasat nature",
        "viju_nature",
    ],

    "viasat_explore": [
        "viju explore",
        "viasat explore",
        "viju_explore",
    ],

    "viasat_history": [
        "viju history",
        "viasat history",
        "viju_history",
    ],

    "tiji": [
        "tiji",
        "тижи",
    ],

    "gulli": [
        "gulli",
        "гулли",
    ],

    "nickelodeon": [
        "nickelodeon",
        "никелодеон",
    ],

    "nicktoons": [
        "nicktoons",
        "ник тунс",
    ],

    "baby_tv": [
        "baby tv",
        "babytv",
        "baby television",
    ],

    "match_planeta": [
        "матч планета",
        "матч! планета",
        "match planeta",
    ],

    "fightbox": [
        "fightbox",
        "файтбокс",
    ],

    "trace_sport_stars": [
        "trace sport",
        "trace sport stars",
    ],

    "amedia_1": [
        "amedia 1",
        "amedIa1",
        "a1",
    ],

    "amedia_2": [
        "amedia 2",
        "amedIa2",
        "a2",
    ],

    "amedia_premium_hd": [
        "amedia premium",
        "amedia premium hd",
    ],

    "amedia_hit": [
        "amedia hit",
        "amedia hit hd",
    ],

    "filmbox": [
        "filmbox",
        "филмбокс",
    ],

    "filmbox_arthouse": [
        "filmbox arthouse",
        "filmbox art house",
    ],

    "amc": [
        "amc",
    ],

    "dom_kino": [
        "дом кино",
        "dom kino",
    ],

    "dom_kino_premium_hd": [
        "дом кино премиум",
        "дом кино премиум hd",
        "dom kino premium",
    ],

    "evrokino": [
        "еврокино",
        "eurokino",
        "euro kino",
    ],

    "illusion_plus": [
        "иллюзион+",
        "иллюзион",
        "illusion+",
        "illusion plus",
    ],

    "mir_seriala": [
        "мир сериала",
        "mir seriala",
        "mirseriala",
    ],

    "tv_xxi": [
        "тв xxi",
        "tv xxi",
        "тв 21",
        "tv21",
    ],

    "365_dney_tv": [
        "365 дней",
        "365 дней тв",
        "365 dney",
        "365dney",
    ],

    "galaxy": [
        "galaxy",
        "галактика",
    ],

    "sony_channel": [
        "sony channel",
        "sony",
        "сони канал",
    ],

    "sony_turbo": [
        "sony turbo",
        "сони турбо",
    ],

    "history_2": [
        "history 2",
        "history2",
    ],

    "docubox": [
        "docubox",
        "docu box",
    ],

    "nostalgia": [
        "ностальгия",
        "nostalgia",
    ],

    "da_vinci": [
        "da vinci",
        "да винчи",
        "davinci",
    ],

    "kitchen_tv": [
        "kitchen tv",
        "kitchen",
        "кухня тв",
    ],

    "mezzo": [
        "mezzo",
    ],

    "tnt_music": [
        "тнт music",
        "тнт мьюзик",
        "tnt music",
    ],

    "rtvi": [
        "rtvi",
        "ртви",
    ],

    "tv5_monde": [
        "tv5 monde",
        "tv5monde",
    ],

    "fashion_tv": [
        "fashion tv",
        "fashiontv",
        "fashion",
    ],

    "viasat_sport": [
        "viju plus sport",
        "viju sport",
        "viasat sport",
    ],

    "vip_premiere": [
        "viju plus premiere",
        "viju premiere",
        "vip premiere",
    ],

    "vip_megahit": [
        "viju plus megahit",
        "viju megahit",
        "vip megahit",
    ],

    "vip_comedy": [
        "viju plus comedy",
        "viju comedy",
        "vip comedy",
    ],

    "vip_serial": [
        "viju plus serial",
        "viju serial",
        "vip serial",
    ],

    "ctc": [
        "стс",
        "ctc",
        "sts",
    ],

    "ctc_kids": [
        "стс kids",
        "ctc kids",
        "стс kids hd",
    ],

    "zoopark": [
        "зоопарк",
        "zoopark",
    ],

    "ducktv": [
        "duck tv",
        "ducktv",
    ],

    "euronews": [
        "euronews",
        "евроньюс",
    ],

    "kvn_tv": [
        "kvn tv",
        "квн тв",
    ],
}


# ============================================================================
# PROVIDER CHANNEL MAP
# ============================================================================

# The map intentionally contains only channel/path relationships supplied
# or explicitly known by the module. New relationships can be learned from
# public playlists and stored in the DB.

PROVIDER_PATHS: Dict[str, List[str]] = {

    "karusel": [
        "CH_R01_KARUSEL",
        "CH_KARUSEL",
    ],

    "rentv": [
        "CH_R01_RENTV",
        "CH_RENTV",
    ],

    "tv3": [
        "CH_R01_TV3",
        "CH_TV3",
    ],

    "mir": [
        "CH_R01_MIR",
        "CH_MIR",
    ],

    "ocean_tv": [
        "CH_R01_OCEANTV",
        "CH_OCEANTV",
    ],

    "viasat_nature": [
        "CH_R01_VIASATNATUREHD",
        "CH_VIASATNATUREHD",
    ],

    "viasat_explore": [
        "CH_R01_VIASATEXPLOREHD",
        "CH_VIASATEXPLOREHD",
    ],

    "viasat_history": [
        "CH_R01_VIASATHISTORYHD",
        "CH_VIASATHISTORYHD",
    ],

    "tiji": [
        "CH_R01_TIJI",
        "CH_TIJI",
    ],

    "gulli": [
        "CH_R01_GULLI",
        "CH_GULLI",
    ],

    "nickelodeon": [
        "CH_R01_NICKEL",
        "CH_NICKEL",
    ],

    "nicktoons": [
        "CH_R01_NICKTOONS",
        "CH_NICKTOONS",
    ],

    "baby_tv": [
        "CH_R01_BABYTV",
        "CH_BABYTV",
    ],

    "match_planeta": [
        "CH_R01_MATCHPLANETA",
        "CH_MATCHPLANETA",
    ],

    "fightbox": [
        "CH_R01_FIGHTBOX",
        "CH_FIGHTBOX",
    ],

    "trace_sport_stars": [
        "CH_R01_TRACESPORT",
        "CH_TRACESPORT",
    ],

    "amedia_1": [
        "CH_R01_AMEDIA1",
        "CH_AMEDIA1",
    ],

    "amedia_2": [
        "CH_R01_AMEDIA2",
        "CH_AMEDIA2",
    ],

    "amedia_premium_hd": [
        "CH_R01_AMEDIAPREMIUMHD",
        "CH_AMEDIAPREMIUMHD",
    ],

    "amedia_hit": [
        "CH_R01_AMEDIAHITHD",
        "CH_AMEDIAHITHD",
    ],

    "filmbox": [
        "CH_R01_FILMBOX",
        "CH_FILMBOX",
    ],

    "filmbox_arthouse": [
        "CH_R01_FILMBOXARTHOUSE",
        "CH_FILMBOXARTHOUSE",
    ],

    "amc": [
        "CH_R01_AMC",
        "CH_AMC",
    ],

    "dom_kino": [
        "CH_R01_DOMKINO",
        "CH_DOMKINO",
    ],

    "dom_kino_premium_hd": [
        "CH_R01_DOMKINOPREMIUMHD",
        "CH_DOMKINOPREMIUMHD",
    ],

    "evrokino": [
        "CH_R01_EUROKINO",
        "CH_EUROKINO",
    ],

    "illusion_plus": [
        "CH_R01_ILLUSIONPLUS",
        "CH_ILLUSIONPLUS",
    ],

    "mir_seriala": [
        "CH_R01_MIRSERIALA",
        "CH_MIRSERIALA",
    ],

    "tv_xxi": [
        "CH_R01_TVXXI",
        "CH_TVXXI",
    ],

    "365_dney_tv": [
        "CH_R01_365DNEYTV",
        "CH_365DNEYTV",
    ],

    "galaxy": [
        "CH_R01_GALAXY",
        "CH_GALAXY",
    ],

    "sony_channel": [
        "CH_R01_SONYCHANNEL",
        "CH_SONYCHANNEL",
    ],

    "sony_turbo": [
        "CH_R01_SONYTURBO",
        "CH_SONYTURBO",
    ],

    "history_2": [
        "CH_R01_HISTORY2",
        "CH_HISTORY2",
    ],

    "docubox": [
        "CH_R01_DOCUBOX",
        "CH_DOCUBOX",
    ],

    "nostalgia": [
        "CH_R01_NOSTALGIA",
        "CH_NOSTALGIA",
    ],

    "da_vinci": [
        "CH_R01_DAVINCILEARNING",
        "CH_DAVINCILEARNING",
    ],

    "kitchen_tv": [
        "CH_R01_KITCHENTV",
        "CH_KITCHENTV",
    ],

    "mezzo": [
        "CH_R01_MEZZO",
        "CH_MEZZO",
    ],

    "tnt_music": [
        "CH_R01_TNTMUSICHD",
        "CH_TNTMUSICHD",
    ],

    "rtvi": [
        "CH_R01_RTVI",
        "CH_RTVI",
    ],

    "tv5_monde": [
        "CH_R01_TV5MONDE",
        "CH_TV5MONDE",
    ],

    "fashion_tv": [
        "CH_R01_FASHIONTVHD",
        "CH_FASHIONTVHD",
    ],

    "viasat_sport": [
        "CH_R01_VIASATSPORT",
        "CH_VIASATSPORT",
    ],

    "vip_premiere": [
        "CH_R01_VIJUPREMIERE",
        "CH_VIJUPREMIERE",
    ],

    "vip_megahit": [
        "CH_R01_VIJUMEGAHIT",
        "CH_VIJUMEGAHIT",
    ],

    "vip_comedy": [
        "CH_R01_VIJUCOMEDY",
        "CH_VIJUCOMEDY",
    ],

    "vip_serial": [
        "CH_R01_VIJUSERIAL",
        "CH_VIJUSERIAL",
    ],

    "ctc": [
        "CH_R01_CTC",
        "CH_CTC",
    ],

    "ctc_kids": [
        "CH_R01_CTC_KIDS",
        "CH_CTC_KIDS",
    ],

    "zoopark": [
        "CH_R01_ZOOPARK",
        "CH_ZOOPARK",
    ],

    "ducktv": [
        "CH_R01_DUCKTV",
        "CH_DUCKTV",
    ],

    "euronews": [
        "CH_R01_EURONEWS",
        "CH_EURONEWS",
    ],

    "kvn_tv": [
        "CH_R01_KVN",
        "CH_KVN",
    ],
}


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class Stream:
    url: str
    alive: bool = False
    status: int = 0
    content_type: str = ""
    source: str = "original"
    reason: str = ""
    score: float = 0.0


@dataclass
class Channel:
    tvg_id: str
    name: str
    group: str
    original_index: int
    streams: List[Stream] = field(default_factory=list)


@dataclass
class CheckResult:
    url: str
    alive: bool
    status: int
    content_type: str
    bytes_read: int
    elapsed_ms: int
    reason: str


@dataclass
class Candidate:
    tvg_id: str
    channel_name: str
    url: str
    source: str
    source_url: str
    match_score: float
    reason: str = ""
    status: int = 0
    alive: bool = False
    first_seen: int = 0
    last_checked: int = 0


# ============================================================================
# NORMALIZATION
# ============================================================================

def normalize_text(value: str) -> str:
    value = value or ""
    value = value.lower().strip()

    replacements = {
        "ё": "е",
        "_": " ",
        "-": " ",
        "—": " ",
        "–": " ",
        ".": " ",
        ",": " ",
        "!": " ",
        "+": " plus ",
        "/": " ",
        "\\": " ",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = re.sub(r"\[[^\]]*\]", " ", value)
    value = re.sub(r"\([^\)]*\)", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_id(value: str) -> str:
    value = normalize_text(value)
    return re.sub(r"[^a-z0-9а-яё]+", "", value)


def channel_aliases(tvg_id: str, name: str = "") -> List[str]:
    aliases = []

    key = normalize_id(tvg_id)

    for k, vals in CHANNEL_ALIASES.items():
        if normalize_id(k) == key:
            aliases.extend(vals)

    if name:
        aliases.append(name)

    aliases.append(tvg_id.replace("_", " "))

    result = []
    seen = set()

    for item in aliases:
        n = normalize_text(item)
        if n and n not in seen:
            result.append(n)
            seen.add(n)

    return result


def strong_channel_match(
    tvg_id: str,
    channel_name: str,
    candidate_id: str,
    candidate_name: str,
) -> Tuple[float, str]:

    target_id = normalize_id(tvg_id)
    candidate_id_n = normalize_id(candidate_id)

    target_name = normalize_text(channel_name)
    candidate_name_n = normalize_text(candidate_name)

    # Exact tvg-id match is strongest.
    if target_id and candidate_id_n and target_id == candidate_id_n:
        return 100.0, "exact_tvg_id"

    aliases = channel_aliases(tvg_id, channel_name)

    for alias in aliases:
        if alias and alias == candidate_name_n:
            return 95.0, "exact_alias"

    # Token based matching.
    target_tokens = set(target_name.split())
    candidate_tokens = set(candidate_name_n.split())

    if target_tokens and candidate_tokens:
        common = target_tokens & candidate_tokens

        if common:
            ratio = len(common) / max(
                1,
                min(len(target_tokens), len(candidate_tokens))
            )

            if ratio >= 0.75:
                return 82.0, "strong_name"

    # Substring only if the names are sufficiently long.
    for alias in aliases:
        if len(alias) >= 5 and alias in candidate_name_n:
            return 70.0, "alias_substring"

    return 0.0, "no_match"


# ============================================================================
# HTTP
# ============================================================================

def request_headers(url: str) -> Dict[str, str]:
    headers = dict(HEADERS)

    host = urllib.parse.urlparse(url).hostname or ""

    if (
        "ngenix.net" in host.lower()
        or "svc.iptv.rt.ru" in host.lower()
        or "zabava" in host.lower()
        or "wink" in host.lower()
    ):
        headers["User-Agent"] = WINK_USER_AGENT

    return headers


def fetch_url(
    url: str,
    timeout: int = DISCOVERY_TIMEOUT,
) -> Optional[str]:

    try:
        req = urllib.request.Request(
            url,
            headers=request_headers(url),
            method="GET",
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout,
            context=SSL_CTX,
        ) as response:

            data = response.read()

        return data.decode("utf-8", errors="ignore")

    except Exception:
        return None


def check_stream(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> CheckResult:

    started = time.monotonic()

    try:
        req = urllib.request.Request(
            url,
            headers=request_headers(url),
            method="GET",
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout,
            context=SSL_CTX,
        ) as response:

            status = getattr(response, "status", 200)
            content_type = response.headers.get(
                "Content-Type",
                "",
            )

            data = response.read(8192)

        elapsed = int(
            (time.monotonic() - started) * 1000
        )

        low = data.lower()

        looks_hls = (
            b"#extm3u" in low
            or b"#ext-x-" in low
            or b"#extinf" in low
        )

        if status == 200 and (
            looks_hls
            or len(data) >= 32
        ):
            return CheckResult(
                url=url,
                alive=True,
                status=status,
                content_type=content_type,
                bytes_read=len(data),
                elapsed_ms=elapsed,
                reason="ok",
            )

        return CheckResult(
            url=url,
            alive=False,
            status=status,
            content_type=content_type,
            bytes_read=len(data),
            elapsed_ms=elapsed,
            reason="invalid_hls_or_short_response",
        )

    except urllib.error.HTTPError as exc:

        elapsed = int(
            (time.monotonic() - started) * 1000
        )

        return CheckResult(
            url=url,
            alive=False,
            status=exc.code,
            content_type="",
            bytes_read=0,
            elapsed_ms=elapsed,
            reason=f"http_{exc.code}",
        )

    except urllib.error.URLError as exc:

        elapsed = int(
            (time.monotonic() - started) * 1000
        )

        return CheckResult(
            url=url,
            alive=False,
            status=0,
            content_type="",
            bytes_read=0,
            elapsed_ms=elapsed,
            reason=f"url_error:{exc.reason}",
        )

    except Exception as exc:

        elapsed = int(
            (time.monotonic() - started) * 1000
        )

        return CheckResult(
            url=url,
            alive=False,
            status=0,
            content_type="",
            bytes_read=0,
            elapsed_ms=elapsed,
            reason=f"{type(exc).__name__}:{exc}",
        )


# ============================================================================
# M3U PARSER
# ============================================================================

def parse_attributes(extinf: str) -> Dict[str, str]:
    result = {}

    for key, value in re.findall(
        r'([A-Za-z0-9_-]+)="([^"]*)"',
        extinf,
    ):
        result[key.lower()] = value

    return result


def parse_m3u(text: str) -> List[Channel]:

    channels: List[Channel] = []

    current: Optional[Channel] = None
    index = 0

    for raw_line in text.splitlines():

        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            attrs = parse_attributes(line)

            if "," in line:
                display_name = line.split(",", 1)[1].strip()
            else:
                display_name = attrs.get("tvg-name", "")

            display_name = re.sub(
                r"^\d+\.\s*",
                "",
                display_name,
            )

            tvg_id = attrs.get(
                "tvg-id",
                normalize_id(display_name),
            )

            group = attrs.get(
                "group-title",
                "",
            )

            index += 1

            current = Channel(
                tvg_id=tvg_id,
                name=display_name,
                group=group,
                original_index=index,
            )

            channels.append(current)

            continue

        if line.startswith("#"):
            continue

        if current is not None:

            current.streams.append(
                Stream(
                    url=line,
                    source="original",
                )
            )

    return channels


# ============================================================================
# SOURCE PARSER
# ============================================================================

def parse_source_records(
    text: str,
) -> List[Tuple[str, str, str]]:

    """
    Возвращает:
        [(candidate_id, candidate_name, url), ...]
    """

    result = []

    current_id = ""
    current_name = ""

    for raw_line in text.splitlines():

        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            attrs = parse_attributes(line)

            current_id = attrs.get(
                "tvg-id",
                "",
            )

            if "," in line:
                current_name = line.split(
                    ",",
                    1,
                )[1].strip()
            else:
                current_name = attrs.get(
                    "tvg-name",
                    "",
                )

            continue

        if line.startswith("#"):
            continue

        if current_name or current_id:

            result.append(
                (
                    current_id,
                    current_name,
                    line,
                )
            )

        current_id = ""
        current_name = ""

    return result


# ============================================================================
# SQLITE
# ============================================================================

class Database:

    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(
            str(path),
            timeout=30,
        )

        self.conn.execute(
            "PRAGMA journal_mode=WAL"
        )

        self.conn.execute(
            "PRAGMA synchronous=NORMAL"
        )

        self.init_schema()

    def init_schema(self):

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tvg_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                match_score REAL DEFAULT 0,
                reason TEXT,
                status INTEGER DEFAULT 0,
                alive INTEGER DEFAULT 0,
                first_seen INTEGER NOT NULL,
                last_checked INTEGER NOT NULL,
                checks INTEGER DEFAULT 0,
                successes INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0,
                UNIQUE(tvg_id, url)
            );

            CREATE TABLE IF NOT EXISTS stream_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tvg_id TEXT,
                url TEXT,
                source TEXT,
                status INTEGER,
                alive INTEGER,
                reason TEXT,
                elapsed_ms INTEGER,
                checked_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tvg_id TEXT,
                alias TEXT,
                source TEXT,
                first_seen INTEGER,
                last_seen INTEGER,
                UNIQUE(tvg_id, alias)
            );

            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT,
                host TEXT,
                source TEXT,
                first_seen INTEGER,
                last_seen INTEGER,
                UNIQUE(provider, host)
            );

            CREATE TABLE IF NOT EXISTS paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tvg_id TEXT,
                path TEXT,
                source TEXT,
                first_seen INTEGER,
                last_seen INTEGER,
                UNIQUE(tvg_id, path)
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at INTEGER,
                finished_at INTEGER,
                input_playlist TEXT,
                total_channels INTEGER,
                total_streams INTEGER,
                alive_channels INTEGER,
                dead_channels INTEGER,
                fallback_channels INTEGER,
                output_file TEXT,
                report_file TEXT
            );
            """
        )

        self.conn.execute(
            """
            INSERT OR REPLACE INTO meta(key, value)
            VALUES('schema_version', ?)
            """,
            (
                str(DB_SCHEMA_VERSION),
            ),
        )

        self.conn.commit()

    def close(self):
        self.conn.close()

    def save_alias(
        self,
        tvg_id: str,
        alias: str,
        source: str,
    ):

        now = int(time.time())

        self.conn.execute(
            """
            INSERT INTO aliases(
                tvg_id,
                alias,
                source,
                first_seen,
                last_seen
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tvg_id, alias)
            DO UPDATE SET
                last_seen=excluded.last_seen
            """,
            (
                tvg_id,
                alias,
                source,
                now,
                now,
            ),
        )

    def save_node(
        self,
        provider: str,
        host: str,
        source: str,
    ):

        now = int(time.time())

        self.conn.execute(
            """
            INSERT INTO nodes(
                provider,
                host,
                source,
                first_seen,
                last_seen
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(provider, host)
            DO UPDATE SET
                last_seen=excluded.last_seen
            """,
            (
                provider,
                host,
                source,
                now,
                now,
            ),
        )

    def save_path(
        self,
        tvg_id: str,
        path: str,
        source: str,
    ):

        now = int(time.time())

        self.conn.execute(
            """
            INSERT INTO paths(
                tvg_id,
                path,
                source,
                first_seen,
                last_seen
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tvg_id, path)
            DO UPDATE SET
                last_seen=excluded.last_seen
            """,
            (
                tvg_id,
                path,
                source,
                now,
                now,
            ),
        )

    def save_candidate(
        self,
        candidate: Candidate,
    ):

        now = int(time.time())

        self.conn.execute(
            """
            INSERT INTO candidates(
                tvg_id,
                channel_name,
                url,
                source,
                source_url,
                match_score,
                reason,
                status,
                alive,
                first_seen,
                last_checked,
                checks,
                successes,
                failures
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)

            ON CONFLICT(tvg_id, url)
            DO UPDATE SET
                channel_name=excluded.channel_name,
                source=excluded.source,
                source_url=excluded.source_url,
                match_score=MAX(
                    candidates.match_score,
                    excluded.match_score
                ),
                reason=excluded.reason,
                status=excluded.status,
                alive=excluded.alive,
                last_checked=excluded.last_checked,
                checks=candidates.checks + 1,
                successes=candidates.successes +
                    CASE
                        WHEN excluded.alive=1 THEN 1
                        ELSE 0
                    END,
                failures=candidates.failures +
                    CASE
                        WHEN excluded.alive=0 THEN 1
                        ELSE 0
                    END
            """,
            (
                candidate.tvg_id,
                candidate.channel_name,
                candidate.url,
                candidate.source,
                candidate.source_url,
                candidate.match_score,
                candidate.reason,
                candidate.status,
                1 if candidate.alive else 0,
                candidate.first_seen or now,
                candidate.last_checked or now,
                1 if candidate.alive else 0,
                1 if not candidate.alive else 0,
            ),
        )

    def save_check(
        self,
        tvg_id: str,
        result: CheckResult,
        source: str,
    ):

        self.conn.execute(
            """
            INSERT INTO stream_checks(
                tvg_id,
                url,
                source,
                status,
                alive,
                reason,
                elapsed_ms,
                checked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tvg_id,
                result.url,
                source,
                result.status,
                1 if result.alive else 0,
                result.reason,
                result.elapsed_ms,
                int(time.time()),
            ),
        )

    def commit(self):
        self.conn.commit()

    def candidate_count(self) -> int:

        row = self.conn.execute(
            "SELECT COUNT(*) FROM candidates"
        ).fetchone()

        return int(row[0])

    def candidates_for(
        self,
        tvg_id: str,
    ) -> List[Candidate]:

        rows = self.conn.execute(
            """
            SELECT
                tvg_id,
                channel_name,
                url,
                source,
                source_url,
                match_score,
                reason,
                status,
                alive,
                first_seen,
                last_checked
            FROM candidates
            WHERE tvg_id=?
            ORDER BY
                alive DESC,
                match_score DESC,
                successes DESC,
                last_checked DESC
            """,
            (tvg_id,),
        ).fetchall()

        result = []

        for row in rows:

            result.append(
                Candidate(
                    tvg_id=row[0],
                    channel_name=row[1],
                    url=row[2],
                    source=row[3],
                    source_url=row[4] or "",
                    match_score=float(row[5] or 0),
                    reason=row[6] or "",
                    status=int(row[7] or 0),
                    alive=bool(row[8]),
                    first_seen=int(row[9] or 0),
                    last_checked=int(row[10] or 0),
                )
            )

        return result

    def learned_nodes(self) -> List[Tuple[str, str]]:

        rows = self.conn.execute(
            """
            SELECT provider, host
            FROM nodes
            ORDER BY last_seen DESC
            """
        ).fetchall()

        return [
            (
                str(provider),
                str(host),
            )
            for provider, host in rows
        ]

    def learned_paths(
        self,
        tvg_id: str,
    ) -> List[str]:

        rows = self.conn.execute(
            """
            SELECT path
            FROM paths
            WHERE tvg_id=?
            ORDER BY last_seen DESC
            """,
            (tvg_id,),
        ).fetchall()

        return [
            str(row[0])
            for row in rows
        ]


# ============================================================================
# URL / NODE EXTRACTION
# ============================================================================

def extract_nodes_from_text(
    text: str,
) -> List[Tuple[str, str]]:

    found = []

    # Full NGENIX hosts.
    for match in NGENIX_HOST_RE.finditer(text):

        node = match.group(1).lower()

        host = match.group(0).lower()

        found.append(
            (
                "ngenix",
                host,
            )
        )

    # Standalone sXXXXX nodes.
    for match in NGENIX_NODE_RE.finditer(text):

        node = match.group(1).lower()

        found.append(
            (
                "ngenix-node",
                node,
            )
        )

    # Known provider hostnames.
    for host in KNOWN_ROSTELECOM_HOSTS:
        if host.lower() in text.lower():
            found.append(
                (
                    "rostelecom",
                    host,
                )
            )

    for host in KNOWN_ZABAVA_HOSTS:
        if host.lower() in text.lower():
            found.append(
                (
                    "zabava",
                    host,
                )
            )

    return list(
        dict.fromkeys(found)
    )


def url_path(url: str) -> str:

    parsed = urllib.parse.urlparse(url)

    return parsed.path or "/"


def is_hls_url(url: str) -> bool:

    lower = url.lower()

    return (
        ".m3u8" in lower
        or "/hls/" in lower
        or "manifest" in lower
        or "playlist" in lower
    )


def extract_channel_code(
    url: str,
) -> Optional[str]:

    match = re.search(
        r"/hls/([^/?#]+)/",
        url,
        re.IGNORECASE,
    )

    if match:
        return match.group(1)

    return None


# ============================================================================
# GENERATED NGENIX / ROSTELECOM PATHS
# ============================================================================

def generate_channel_paths(
    tvg_id: str,
    db: Database,
) -> List[str]:

    values = []

    # Built-in paths.
    values.extend(
        PROVIDER_PATHS.get(
            tvg_id,
            [],
        )
    )

    # Learned paths.
    values.extend(
        db.learned_paths(tvg_id)
    )

    # Deduplicate.
    values = list(
        dict.fromkeys(
            p.strip("/")
            for p in values
            if p.strip("/")
        )
    )

    return values


def generate_hls_variants(
    host: str,
    channel_paths: Sequence[str],
) -> List[str]:

    host = host.strip()

    if not host:
        return []

    if not host.startswith(
        ("http://", "https://")
    ):
        host = "https://" + host

    host = host.rstrip("/")

    urls = []

    for channel_path in channel_paths:

        channel_path = channel_path.strip("/")

        if not channel_path:
            continue

        paths = [
            f"/hls/{channel_path}/variant.m3u8",
            f"/hls/{channel_path}/index.m3u8",
            f"/hls/{channel_path}/playlist.m3u8",
            f"/hls/{channel_path}/master.m3u8",
        ]

        for path in paths:

            urls.append(
                host + path
            )

    # HTTP/HTTPS transformation.
    transformed = []

    for url in urls:

        transformed.append(url)

        parsed = urllib.parse.urlparse(url)

        if parsed.scheme == "https":
            transformed.append(
                urllib.parse.urlunparse(
                    (
                        "http",
                        parsed.netloc,
                        parsed.path,
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )
            )

        elif parsed.scheme == "http":
            transformed.append(
                urllib.parse.urlunparse(
                    (
                        "https",
                        parsed.netloc,
                        parsed.path,
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )
            )

    return list(
        dict.fromkeys(transformed)
    )


# ============================================================================
# PROVIDER CANDIDATES
# ============================================================================

def provider_candidates(
    channel: Channel,
    db: Database,
) -> List[Candidate]:

    paths = generate_channel_paths(
        channel.tvg_id,
        db,
    )

    candidates = []

    # Known hosts only.
    hosts = []

    for host in KNOWN_NGENIX_HOSTS:
        hosts.append(
            (
                "ngenix",
                host,
            )
        )

    for provider, host in db.learned_nodes():

        if provider.startswith("ngenix"):
            hosts.append(
                (
                    provider,
                    host,
                )
            )

    for host in KNOWN_ROSTELECOM_HOSTS:
        hosts.append(
            (
                "rostelecom",
                host,
            )
        )

    for host in KNOWN_ZABAVA_HOSTS:
        hosts.append(
            (
                "zabava",
                host,
            )
        )

    hosts = list(
        dict.fromkeys(hosts)
    )

    for provider, host in hosts:

        for url in generate_hls_variants(
            host,
            paths,
        ):

            candidates.append(
                Candidate(
                    tvg_id=channel.tvg_id,
                    channel_name=channel.name,
                    url=url,
                    source=provider,
                    source_url=host,
                    match_score=100.0,
                    reason="generated_provider_path",
                )
            )

    return candidates


# ============================================================================
# PUBLIC SOURCE SEARCH
# ============================================================================

def search_public_source(
    channel: Channel,
    text: str,
    source_url: str,
) -> List[Candidate]:

    result = []

    records = parse_source_records(text)

    for candidate_id, candidate_name, url in records:

        score, reason = strong_channel_match(
            channel.tvg_id,
            channel.name,
            candidate_id,
            candidate_name,
        )

        # Hard threshold prevents the "mir_seriala -> ctc_kids"
        # type of accidental replacement.
        if score < 70:
            continue

        if not is_hls_url(url):
            continue

        result.append(
            Candidate(
                tvg_id=channel.tvg_id,
                channel_name=channel.name,
                url=url,
                source="public_playlist",
                source_url=source_url,
                match_score=score,
                reason=reason,
            )
        )

    return result


# ============================================================================
# SOURCE CACHE
# ============================================================================

def load_public_sources(
    source_urls: Sequence[str],
) -> Dict[str, str]:

    result = {}

    print(
        f"[DISCOVERY] источников: {len(source_urls)}"
    )

    def worker(
        url: str,
    ) -> Tuple[str, Optional[str]]:

        print(
            f"[DISCOVERY] GET {url}"
        )

        return (
            url,
            fetch_url(
                url,
                DISCOVERY_TIMEOUT,
            ),
        )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(
            12,
            max(1, len(source_urls)),
        )
    ) as pool:

        futures = [
            pool.submit(
                worker,
                url,
            )
            for url in source_urls
        ]

        for future in concurrent.futures.as_completed(
            futures
        ):

            url, text = future.result()

            if text:
                result[url] = text

                print(
                    f"[DISCOVERY] OK {url} "
                    f"({len(text):,} bytes)"
                )
            else:
                print(
                    f"[DISCOVERY] FAIL {url}"
                )

    return result


# ============================================================================
# DISCOVERY FROM PUBLIC SOURCES
# ============================================================================

def discover_candidates(
    channels: Sequence[Channel],
    source_cache: Dict[str, str],
    db: Database,
) -> Dict[str, List[Candidate]]:

    result: Dict[str, List[Candidate]] = {
        channel.tvg_id: []
        for channel in channels
    }

    # Provider-generated candidates first.
    for channel in channels:

        generated = provider_candidates(
            channel,
            db,
        )

        result[
            channel.tvg_id
        ].extend(generated)

    # Public playlists.
    for source_url, text in source_cache.items():

        nodes = extract_nodes_from_text(
            text
        )

        for provider, host in nodes:

            db.save_node(
                provider,
                host,
                source_url,
            )

        records = parse_source_records(
            text
        )

        for channel in channels:

            candidates = search_public_source(
                channel,
                text,
                source_url,
            )

            result[
                channel.tvg_id
            ].extend(candidates)

            # Save aliases learned from public source.
            for candidate_id, candidate_name, _ in records:

                score, _ = strong_channel_match(
                    channel.tvg_id,
                    channel.name,
                    candidate_id,
                    candidate_name,
                )

                if score >= 70:

                    db.save_alias(
                        channel.tvg_id,
                        candidate_name,
                        source_url,
                    )

    db.commit()

    # Deduplicate each channel.
    for tvg_id, candidates in result.items():

        unique = {}

        for candidate in candidates:

            key = candidate.url

            old = unique.get(key)

            if old is None:
                unique[key] = candidate
                continue

            if candidate.match_score > old.match_score:
                unique[key] = candidate

        result[tvg_id] = list(
            unique.values()
        )

    return result


# ============================================================================
# CHECK CANDIDATES
# ============================================================================

def check_candidates(
    channel: Channel,
    candidates: List[Candidate],
    db: Database,
    workers: int,
    timeout: int,
) -> List[Candidate]:

    if not candidates:
        return []

    unique = {}

    for candidate in candidates:

        if candidate.url not in unique:
            unique[candidate.url] = candidate

    candidates = list(
        unique.values()
    )

    def worker(
        candidate: Candidate,
    ) -> Tuple[Candidate, CheckResult]:

        return (
            candidate,
            check_stream(
                candidate.url,
                timeout,
            ),
        )

    checked = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(
            workers,
            max(1, len(candidates)),
        )
    ) as pool:

        futures = [
            pool.submit(
                worker,
                candidate,
            )
            for candidate in candidates
        ]

        for future in concurrent.futures.as_completed(
            futures
        ):

            candidate, check = future.result()

            candidate.alive = check.alive
            candidate.status = check.status
            candidate.last_checked = int(
                time.time()
            )

            if check.alive:

                candidate.reason = (
                    candidate.reason
                    + ";verified"
                )

            else:

                candidate.reason = (
                    candidate.reason
                    + ";"
                    + check.reason
                )

            db.save_candidate(
                candidate
            )

            db.save_check(
                channel.tvg_id,
                check,
                candidate.source,
            )

            checked.append(
                candidate
            )

    db.commit()

    return checked


# ============================================================================
# ORIGINAL STREAM CHECKING
# ============================================================================

def check_original_streams(
    channels: Sequence[Channel],
    db: Database,
    workers: int,
    timeout: int,
):

    jobs = []

    for channel in channels:

        for stream in channel.streams:

            jobs.append(
                (
                    channel,
                    stream,
                )
            )

    print(
        f"[CHECK] исходных потоков: {len(jobs)}"
    )

    def worker(
        job,
    ):

        channel, stream = job

        return (
            channel,
            stream,
            check_stream(
                stream.url,
                timeout,
            ),
        )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        futures = [
            pool.submit(
                worker,
                job,
            )
            for job in jobs
        ]

        for future in concurrent.futures.as_completed(
            futures
        ):

            channel, stream, result = (
                future.result()
            )

            stream.alive = result.alive
            stream.status = result.status
            stream.reason = result.reason

            db.save_check(
                channel.tvg_id,
                result,
                "original",
            )

            symbol = (
                "OK"
                if result.alive
                else "DEAD"
            )

            print(
                f"[{symbol}] "
                f"{channel.name} | "
                f"{result.status} | "
                f"{stream.url}"
            )

    db.commit()


# ============================================================================
# DATABASE FALLBACK CANDIDATES
# ============================================================================

def database_candidates(
    channel: Channel,
    db: Database,
) -> List[Candidate]:

    result = []

    for candidate in db.candidates_for(
        channel.tvg_id
    ):

        # Recalculate identity against current channel.
        score, reason = strong_channel_match(
            channel.tvg_id,
            channel.name,
            candidate.tvg_id,
            candidate.channel_name,
        )

        if score >= 70:

            candidate.match_score = max(
                candidate.match_score,
                score,
            )

            candidate.reason += (
                ";db_match:"
                + reason
            )

            result.append(candidate)

    return result


# ============================================================================
# RANKING
# ============================================================================

def rank_candidate(
    candidate: Candidate,
) -> float:

    score = candidate.match_score

    if candidate.alive:
        score += 100

    # Provider-generated candidates get a small
    # deterministic preference when channel identity
    # is exact.
    source_bonus = {
        "ngenix": 18,
        "ngenix-node": 17,
        "rostelecom": 16,
        "zabava": 15,
        "wink": 14,
        "public_playlist": 8,
        "original": 20,
    }

    score += source_bonus.get(
        candidate.source,
        0,
    )

    if candidate.status == 403:
        score -= 25

    if candidate.status == 404:
        score -= 20

    if candidate.status >= 500:
        score -= 15

    return score


def choose_best(
    candidates: Sequence[Candidate],
) -> Optional[Candidate]:

    alive = [
        c
        for c in candidates
        if c.alive
        and c.match_score >= 70
    ]

    if not alive:
        return None

    return max(
        alive,
        key=rank_candidate,
    )


# ============================================================================
# OUTPUT FILE NUMBERING
# ============================================================================

def next_merge_filename(
    output_dir: Path,
) -> Path:

    base = output_dir / "merge_auto.m3u"

    if not base.exists():
        return base

    index = 1

    while True:

        candidate = (
            output_dir
            / f"merge_auto_{index}.m3u"
        )

        if not candidate.exists():
            return candidate

        index += 1


# ============================================================================
# JSON REPORT
# ============================================================================

def write_json_report(
    path: Path,
    report: dict,
):

    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================================
# M3U OUTPUT
# ============================================================================

def build_output_playlist(
    channels: Sequence[Channel],
    selected: Dict[str, Candidate],
    output_file: Path,
):

    lines = [
        "#EXTM3U",
        "#EXT-X-FALLBACK: generated by IpTV_fallback.py",
        f"#EXT-X-FALLBACK-VERSION: {VERSION}",
    ]

    output_index = 1

    for channel in channels:

        candidate = selected.get(
            channel.tvg_id
        )

        if candidate is None:
            continue

        # Only verified candidate.
        if not candidate.alive:
            continue

        if candidate.match_score < 70:
            continue

        lines.append(
            (
                '#EXTINF:-1 '
                f'tvg-id="{channel.tvg_id}" '
                f'group-title="{channel.group}",'
                f'{output_index}. '
                f'{channel.name} '
                f'[fallback:{candidate.source}]'
            )
        )

        lines.append(
            candidate.url
        )

        output_index += 1

    output_file.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    return output_index - 1


# ============================================================================
# RUN ONCE
# ============================================================================

def run_once(
    playlist_arg: str,
    output_dir: Path,
    db_path: Path,
    workers: int,
    timeout: int,
    source_urls: Sequence[str],
):

    started_at = int(time.time())

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    db = Database(
        db_path
    )

    # ------------------------------------------------------------
    # INPUT
    # ------------------------------------------------------------

    print(
        "\n"
        "============================================================\n"
        f" {VERSION}\n"
        " AUTOMATIC IPTV FAILOVER / FALLBACK\n"
        "============================================================\n"
    )

    print(
        f"[INPUT] {playlist_arg}"
    )

    if (
        playlist_arg.startswith(
            ("http://", "https://")
        )
    ):

        text = fetch_url(
            playlist_arg,
            DISCOVERY_TIMEOUT,
        )

    else:

        path = Path(
            playlist_arg
        )

        if not path.exists():

            print(
                f"[ERROR] файл не найден: {path}"
            )

            db.close()
            return None

        text = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

    if not text:

        print(
            "[ERROR] исходный M3U пуст или недоступен"
        )

        db.close()
        return None

    # ------------------------------------------------------------
    # PARSE
    # ------------------------------------------------------------

    channels = parse_m3u(
        text
    )

    total_streams = sum(
        len(channel.streams)
        for channel in channels
    )

    print(
        f"[PARSE] каналов: {len(channels)}"
    )

    print(
        f"[PARSE] потоков: {total_streams}"
    )

    # ------------------------------------------------------------
    # REGISTER NODES / PATHS FROM INPUT
    # ------------------------------------------------------------

    for provider, host in extract_nodes_from_text(
        text
    ):

        db.save_node(
            provider,
            host,
            playlist_arg,
        )

    for channel in channels:

        for stream in channel.streams:

            code = extract_channel_code(
                stream.url
            )

            if code:

                db.save_path(
                    channel.tvg_id,
                    code,
                    playlist_arg,
                )

    db.commit()

    # ------------------------------------------------------------
    # CHECK ALL ORIGINAL STREAMS
    # ------------------------------------------------------------

    print(
        "\n[PHASE 1] Проверка ВСЕХ исходных потоков"
    )

    check_original_streams(
        channels,
        db,
        workers,
        timeout,
    )

    alive_channels = [
        channel
        for channel in channels
        if any(
            stream.alive
            for stream in channel.streams
        )
    ]

    dead_channels = [
        channel
        for channel in channels
        if not any(
            stream.alive
            for stream in channel.streams
        )
    ]

    print(
        "\n"
        f"[STATUS] живых каналов: "
        f"{len(alive_channels)}"
    )

    print(
        f"[STATUS] мёртвых каналов: "
        f"{len(dead_channels)}"
    )

    # ------------------------------------------------------------
    # LOAD PUBLIC SOURCES
    # ------------------------------------------------------------

    print(
        "\n[PHASE 2] Загрузка публичных источников"
    )

    source_cache = load_public_sources(
        source_urls
    )

    # ------------------------------------------------------------
    # DISCOVERY
    # ------------------------------------------------------------

    print(
        "\n[PHASE 3] Поиск кандидатов"
    )

    candidates_map = discover_candidates(
        dead_channels,
        source_cache,
        db,
    )

    # ------------------------------------------------------------
    # ADD DATABASE HISTORY
    # ------------------------------------------------------------

    for channel in dead_channels:

        candidates_map.setdefault(
            channel.tvg_id,
            [],
        )

        candidates_map[
            channel.tvg_id
        ].extend(
            database_candidates(
                channel,
                db,
            )
        )

    # ------------------------------------------------------------
    # DEDUP BEFORE CHECK
    # ------------------------------------------------------------

    for tvg_id, candidates in candidates_map.items():

        unique = {}

        for candidate in candidates:

            old = unique.get(
                candidate.url
            )

            if old is None:
                unique[candidate.url] = candidate
                continue

            if candidate.match_score > old.match_score:
                unique[
                    candidate.url
                ] = candidate

        candidates_map[tvg_id] = list(
            unique.values()
        )

    total_candidates = sum(
        len(values)
        for values in candidates_map.values()
    )

    print(
        f"[DISCOVERY] кандидатов: "
        f"{total_candidates}"
    )

    # ------------------------------------------------------------
    # CHECK CANDIDATES
    # ------------------------------------------------------------

    print(
        "\n[PHASE 4] Проверка fallback-кандидатов"
    )

    checked_candidates: Dict[
        str,
        List[Candidate]
    ] = {}

    for channel in dead_channels:

        candidates = candidates_map.get(
            channel.tvg_id,
            [],
        )

        print(
            f"\n[CANDIDATES] "
            f"{channel.name}: "
            f"{len(candidates)}"
        )

        checked = check_candidates(
            channel,
            candidates,
            db,
            workers,
            timeout,
        )

        checked_candidates[
            channel.tvg_id
        ] = checked

    # ------------------------------------------------------------
    # CHOOSE BEST
    # ------------------------------------------------------------

    print(
        "\n[PHASE 5] Выбор замен"
    )

    selected: Dict[
        str,
        Candidate
    ] = {}

    channel_reports = []

    for channel in channels:

        original_alive = [
            stream
            for stream in channel.streams
            if stream.alive
        ]

        # Existing working stream:
        # preserve it as the primary stream.
        if original_alive:

            best_original = original_alive[0]

            selected[
                channel.tvg_id
            ] = Candidate(
                tvg_id=channel.tvg_id,
                channel_name=channel.name,
                url=best_original.url,
                source="original",
                source_url=playlist_arg,
                match_score=100.0,
                reason="original_alive",
                status=best_original.status,
                alive=True,
            )

            channel_reports.append(
                {
                    "tvg_id": channel.tvg_id,
                    "name": channel.name,
                    "status": "original_alive",
                    "original_streams": [
                        asdict(stream)
                        for stream in channel.streams
                    ],
                    "selected": asdict(
                        selected[channel.tvg_id]
                    ),
                }
            )

            continue

        candidates = checked_candidates.get(
            channel.tvg_id,
            [],
        )

        best = choose_best(
            candidates
        )

        if best:

            selected[
                channel.tvg_id
            ] = best

            print(
                f"[FALLBACK OK] "
                f"{channel.name} -> "
                f"{best.url}"
            )

            channel_reports.append(
                {
                    "tvg_id": channel.tvg_id,
                    "name": channel.name,
                    "status": "fallback_found",
                    "original_streams": [
                        asdict(stream)
                        for stream in channel.streams
                    ],
                    "candidates": [
                        asdict(candidate)
                        for candidate in candidates
                    ],
                    "selected": asdict(best),
                }
            )

        else:

            print(
                f"[FALLBACK MISS] "
                f"{channel.name}"
            )

            channel_reports.append(
                {
                    "tvg_id": channel.tvg_id,
                    "name": channel.name,
                    "status": "no_verified_fallback",
                    "original_streams": [
                        asdict(stream)
                        for stream in channel.streams
                    ],
                    "candidates": [
                        asdict(candidate)
                        for candidate in candidates
                    ],
                    "selected": None,
                }
            )

    # ------------------------------------------------------------
    # OUTPUT
    # ------------------------------------------------------------

    output_file = next_merge_filename(
        output_dir
    )

    output_count = build_output_playlist(
        channels,
        selected,
        output_file,
    )

    # ------------------------------------------------------------
    # JSON REPORT
    # ------------------------------------------------------------

    report_file = (
        output_dir
        / (
            output_file.stem
            + ".json"
        )
    )

    fallback_count = sum(
        1
        for candidate in selected.values()
        if candidate.source != "original"
    )

    report = {
        "version": VERSION,
        "schema_version": DB_SCHEMA_VERSION,
        "run": {
            "started_at": started_at,
            "finished_at": int(time.time()),
            "duration_sec": round(
                time.time() - started_at,
                3,
            ),
        },
        "input": {
            "playlist": playlist_arg,
            "channels": len(channels),
            "streams": total_streams,
        },
        "status": {
            "alive_channels": len(alive_channels),
            "dead_channels": len(dead_channels),
            "fallback_found": fallback_count,
            "output_streams": output_count,
        },
        "discovery": {
            "source_count": len(source_urls),
            "loaded_source_count": len(source_cache),
            "candidate_count": total_candidates,
            "database_candidate_count": db.candidate_count(),
        },
        "providers": {
            "ngenix_hosts": KNOWN_NGENIX_HOSTS,
            "rostelecom_hosts": KNOWN_ROSTELECOM_HOSTS,
            "zabava_hosts": KNOWN_ZABAVA_HOSTS,
            "wink_hosts": KNOWN_WINK_HOSTS,
        },
        "output": {
            "playlist": str(output_file),
            "report": str(report_file),
            "database": str(db_path),
        },
        "channels": channel_reports,
    }

    write_json_report(
        report_file,
        report,
    )

    # ------------------------------------------------------------
    # RUN HISTORY
    # ------------------------------------------------------------

    db.conn.execute(
        """
        INSERT INTO runs(
            started_at,
            finished_at,
            input_playlist,
            total_channels,
            total_streams,
            alive_channels,
            dead_channels,
            fallback_channels,
            output_file,
            report_file
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            started_at,
            int(time.time()),
            playlist_arg,
            len(channels),
            total_streams,
            len(alive_channels),
            len(dead_channels),
            fallback_count,
            str(output_file),
            str(report_file),
        ),
    )

    db.commit()

    print(
        "\n"
        "============================================================"
    )

    print(
        f"\n[DONE] каналов входа: {len(channels)}"
    )

    print(
        f"[DONE] исходных потоков: {total_streams}"
    )

    print(
        f"[DONE] живых каналов: {len(alive_channels)}"
    )

    print(
        f"[DONE] мёртвых каналов: {len(dead_channels)}"
    )

    print(
        f"[DONE] восстановлено: {fallback_count}"
    )

    print(
        f"[DONE] выходных потоков: {output_count}"
    )

    print(
        f"[DONE] M3U: {output_file}"
    )

    print(
        f"[DONE] JSON: {report_file}"
    )

    print(
        f"[DONE] DB: {db_path}"
    )

    print(
        f"[DONE] накопленных кандидатов: "
        f"{db.candidate_count()}"
    )

    print(
        "============================================================\n"
    )

    db.close()

    return {
        "output_file": output_file,
        "report_file": report_file,
        "db_path": db_path,
    }


# ============================================================================
# WATCH MODE
# ============================================================================

def watch(
    playlist_arg: str,
    output_dir: Path,
    db_path: Path,
    workers: int,
    timeout: int,
    source_urls: Sequence[str],
    interval: int,
):

    print(
        f"[WATCH] интервал: {interval} сек."
    )

    while True:

        try:

            run_once(
                playlist_arg=playlist_arg,
                output_dir=output_dir,
                db_path=db_path,
                workers=workers,
                timeout=timeout,
                source_urls=source_urls,
            )

        except KeyboardInterrupt:

            print(
                "\n[WATCH] остановлено."
            )

            return

        except Exception as exc:

            print(
                f"[WATCH ERROR] "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

        print(
            f"[WATCH] следующий запуск "
            f"через {interval} сек."
        )

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return


# ============================================================================
# ARGUMENTS
# ============================================================================

def build_parser():

    parser = argparse.ArgumentParser(
        description=(
            "Automatic IPTV stream failover "
            "and public-source fallback discovery"
        )
    )

    parser.add_argument(
        "--playlist",
        "-p",
        required=True,
        help=(
            "локальный M3U или публичный URL"
        ),
    )

    parser.add_argument(
        "--output-dir",
        "-d",
        default=".",
        help=(
            "каталог для merge_auto*.m3u "
            "и JSON"
        ),
    )

    parser.add_argument(
        "--db",
        default=None,
        help=(
            "путь SQLite DB; "
            "по умолчанию output-dir/iptv_fallback.db"
        ),
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="число параллельных проверок",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="таймаут одного HLS запроса",
    )

    parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="постоянный мониторинг",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="интервал watch в секундах",
    )

    parser.add_argument(
        "--no-public-sources",
        action="store_true",
        help=(
            "не загружать внешние публичные M3U"
        ),
    )

    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help=(
            "добавить собственный публичный M3U "
            "источник; можно указать несколько раз"
        ),
    )

    return parser


# ============================================================================
# MAIN
# ============================================================================

def main():

    parser = build_parser()

    args = parser.parse_args()

    if args.workers < 1:
        args.workers = 1

    if args.timeout < 1:
        args.timeout = 1

    if args.interval < 1:
        args.interval = 1

    output_dir = Path(
        args.output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if args.db:

        db_path = Path(
            args.db
        )

    else:

        db_path = (
            output_dir
            / "iptv_fallback.db"
        )

    # ------------------------------------------------------------
    # SOURCES
    # ------------------------------------------------------------

    if args.no_public_sources:

        source_urls = []

    else:

        source_urls = list(
            PUBLIC_PLAYLIST_SOURCES
        )

    source_urls.extend(
        args.source
    )

    source_urls = list(
        dict.fromkeys(
            url.strip()
            for url in source_urls
            if url.strip()
        )
    )

    # ------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------

    if args.watch:

        watch(
            playlist_arg=args.playlist,
            output_dir=output_dir,
            db_path=db_path,
            workers=args.workers,
            timeout=args.timeout,
            source_urls=source_urls,
            interval=args.interval,
        )

    else:

        run_once(
            playlist_arg=args.playlist,
            output_dir=output_dir,
            db_path=db_path,
            workers=args.workers,
            timeout=args.timeout,
            source_urls=source_urls,
        )


if __name__ == "__main__":
    main()