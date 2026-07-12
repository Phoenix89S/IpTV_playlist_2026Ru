#!/usr/bin/env python3

Denis IPTV Builder / iptv_parser_v12_ultra.py

import re
import requests
import os
import shutil
import base64
import sqlite3
import time
import threading
from dataclasses import dataclass, field
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

#============================================================
# TURBO CORE (УСИЛЕННЫЙ ПУЛ v12 — объединение 4 ИИ + твой v10)
#============================================================

TURBO = True

HTTP_WORKERS = 256
STREAM_WORKERS = 256

_executor = ThreadPoolExecutor(
    max_workers=max(256, (os.cpu_count() or 4) * 32)
)

_thread_local = threading.local()

def get_session():
    if not hasattr(_thread_local, "session"):
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.2,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=None
        )
        adapter = HTTPAdapter(
            pool_connections=HTTP_WORKERS,
            pool_maxsize=HTTP_WORKERS,
            max_retries=retry
        )
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({
            "User-Agent": "Denis-IPTV-Builder/12.0-Ultra-Turbo"
        })
        _thread_local.session = session
    return _thread_local.session

#============================================================
# SMART HTTP ENGINE (СЕМАФОР + УСКОРЕННЫЕ ТАЙМАУТЫ)
#============================================================

_network_semaphore = threading.Semaphore(256)

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 4
DEFAULT_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

NETWORK_STATS = {"requests": 0, "errors": 0, "bytes": 0}

def safe_get(url, **kwargs):
    with _network_semaphore:
        try:
            r = get_session().get(url, **kwargs)
            NETWORK_STATS["requests"] += 1
            if r.ok:
                NETWORK_STATS["bytes"] += len(r.content or b"")
            return r
        except Exception:
            NETWORK_STATS["errors"] += 1
            raise

def request_json(url, timeout=DEFAULT_TIMEOUT):
    r = safe_get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def request_text(url, timeout=DEFAULT_TIMEOUT):
    r = safe_get(url, timeout=timeout)
    r.raise_for_status()
    return r.text

#============================================================
# TURBO CACHE (ОБЪЕДИНЁННЫЙ КЭШ 4 ИИ)
#============================================================

_stream_alive_cache = {}
_stream_quality_cache = {}
_commit_cache = {}

#============================================================
# ULTRA DATABASE (SQLite TURBO WAL + MEMORY)
#============================================================

DB_NAME = "Denis_iptv_cache.db"
_db = sqlite3.connect(DB_NAME, check_same_thread=False)

_db.execute("PRAGMA journal_mode=WAL;")
_db.execute("PRAGMA synchronous=NORMAL;")
_db.execute("PRAGMA temp_store=MEMORY;")

_db.execute("""
CREATE TABLE IF NOT EXISTS stream_cache
(
    url TEXT PRIMARY KEY,
    alive INTEGER,
    quality REAL,
    checked INTEGER
)
""")
_db.commit()

CACHE_TTL = 3600

def db_get_stream(url):
    row = _db.execute(
        "SELECT alive, quality, checked FROM stream_cache WHERE url=?",
        (url,)
    ).fetchone()
    if row is None:
        return None
    alive, quality, checked = row
    if time.time() - checked > CACHE_TTL:
        return None
    return bool(alive), quality

def db_put_stream(url, alive, quality):
    _db.execute(
        "INSERT OR REPLACE INTO stream_cache VALUES (?, ?, ?, ?)",
        (url, int(alive), quality, int(time.time()))
    )
    _db.commit()

#============================================================
# SCADA НУМЕРАЦИЯ (ТВОЯ ОРИГИНАЛЬНАЯ ЛОГИКА — СОХРАНЕНО БЕЗ ИЗМЕНЕНИЙ)
#============================================================

_scada_cache = {}

def build_scada_code(global_number: int, sub_number: int = 1) -> str:
    return f"{global_number}.{sub_number}.E.F(00).{global_number}.{sub_number}"

def build_scada_code_cached(global_number, sub_number=1):
    key = (global_number, sub_number)
    if key not in _scada_cache:
        _scada_cache[key] = build_scada_code(global_number, sub_number)
    return _scada_cache[key]

#============================================================
# SAFE INT (ТВОЯ ЛОГИКА — СОХРАНЕНО)
#============================================================

def safe_int(value):
    try:
        return int(value)
    except:
        digits = ''.join(ch for ch in str(value) if ch.isdigit())
        if digits:
            return int(digits)
        return 1

#============================================================
# НОРМАЛИЗАЦИЯ (ТВОЯ ЛОГИКА + ДОРАБОТКИ ГРОКА)
#============================================================

def normalize_channel_name(name: str) -> str:
    name = name.lstrip(" ,")
    name = name.replace("–", "-").replace("—", "-")
    name = re.sub(r"\s+", " ", name)
    name = name.replace("(", "").replace(")", "")
    return name.strip()

def normalize_tvg_id(tvg: str) -> str:
    if not tvg:
        return "1"
    return tvg.strip().lstrip(" ,")

#============================================================
# МОДЕЛИ (ТВОЯ БАЗА + ДОРАБОТКИ ГЕМИНИ + ГИГАЧАТ + ГРОК)
#============================================================

@dataclass
class StreamInfo:
    source_id: str
    url: str
    quality_score: float = 0.0
    alive: bool = False

@dataclass
class Channel:
    number: str
    name: str
    group: Optional[str] = None
    logo: Optional[str] = None
    scada_code: str = ""
    streams: List[StreamInfo] = field(default_factory=list)

    # В v10 было best_stream + reserve_stream
    # В v11/v12 — резервов может быть много, не сокращаем
    best_stream: Optional[StreamInfo] = None
    reserve_streams: List[StreamInfo] = field(default_factory=list)

    # История качества — из твоего v10, сохраняем
    quality_history: List[StreamInfo] = field(default_factory=list)

#============================================================
# ЖЁСТКИЙ ФИЛЬТР 18+ (ОБЪЕДИНЁННЫЙ: ТВОЙ + ГИГАЧАТ + ГРОК + ГЕМИНИ)
#============================================================

ADULT_CHANNELS = [
    "brazzers","dorcel","hustler","penthouse","xxx","eroxxxhd","extasytv","redlight",
    "playboy","venus","hot","sextv","adult","erotic","эротика","порно","porn","porno",
    "onlyfans","fansly","hentai","sex","fuck","18+","для взрослых","blue hustler",
    "babes","tits","cum","anal","milf","sensual","softcore","hardcore","xx-cel",
    "private","creampie","squirt","blowjob","lesbian","gay","trans"
]

GAMBLING_CHANNELS = [
    "casino","poker","bet","stake","1xbet","melbet","parimatch","fonbet",
    "казино","покер","ставки","vulkan","вулкан","leon","леон"
]

STRICT_URL_BLOCKLIST = [
    "ott.watch/stream/", "ru2.tvtm.one", "adult", "xxx", "porn", "sex",
    "bad.donor.url", "tracking.link/", ".exe?", ".php?token=", "&ip=", "&user=", "&pass="
]

def is_adult_channel(name: str, group: Optional[str]) -> bool:
    if group and group.lower().strip() == "для взрослых":
        return True
    text = (name + " " + (group or "")).lower().strip()
    for bad in ADULT_CHANNELS + GAMBLING_CHANNELS:
        if bad in text:
            return True
    return False

def is_bad_donor(url: str) -> bool:
    url_l = url.lower()
    if "ott.watch/stream/" in url_l:
        return True
    if "ru2.tvtm.one" in url_l and url_l.endswith("m3u8?"):
        return True
    for bad in STRICT_URL_BLOCKLIST:
        if bad in url_l:
            return True
    return False

def passes_strict_filter(channel: Channel) -> bool:
    if is_adult_channel(channel.name, channel.group):
        return False
    for s in channel.streams:
        if is_bad_donor(s.url):
            return False
    return True