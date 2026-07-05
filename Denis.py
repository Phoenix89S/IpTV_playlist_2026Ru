#!/usr/bin/env python3
# Denis IPTV Builder / iptv_parser_v10_ultra.py

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

# ==========================
# TURBO CORE
# ==========================
TURBO = True

HTTP_WORKERS = 32
STREAM_WORKERS = 64

_executor = ThreadPoolExecutor(
    max_workers=min(128, (os.cpu_count() or 4) * 8)
)

_thread_local = threading.local()

def get_session():
    if not hasattr(_thread_local, "session"):
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.5,
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
            "User-Agent": "Denis-IPTV-Builder/10.0"
        })
        _thread_local.session = session
    return _thread_local.session

# ==========================
# SMART HTTP ENGINE
# ==========================
_network_semaphore = threading.Semaphore(16)

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 5
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

# ==========================
# TURBO CACHE
# ==========================
_stream_alive_cache = {}
_stream_quality_cache = {}
_commit_cache = {}

# ==========================
# ULTRA DATABASE (SQLite Cache)
# ==========================
DB_NAME = "Denis_iptv_cache.db"
_db = sqlite3.connect(DB_NAME, check_same_thread=False)
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

# ==========================
# ЛОГГЕР
# ==========================
def log(msg: str):
    print(msg)

ERROR_LOG = "Denis_iptv_errors.log"
def log_error(text):
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(text + "\n")

# ==========================
# SCADA НУМЕРАЦИЯ
# ==========================
_scada_cache = {}
def build_scada_code(global_number: int, sub_number: int = 1) -> str:
    return f"{global_number}.{sub_number}.E.F(00).{global_number}.{sub_number}"

def build_scada_code_cached(global_number, sub_number=1):
    key = (global_number, sub_number)
    if key not in _scada_cache:
        _scada_cache[key] = build_scada_code(global_number, sub_number)
    return _scada_cache[key]

# ==========================
# SAFE INT
# ==========================
def safe_int(value):
    try:
        return int(value)
    except:
        digits = ''.join(ch for ch in str(value) if ch.isdigit())
        if digits:
            return int(digits)
        return 1

# ==========================
# НОРМАЛИЗАЦИЯ
# ==========================
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

# ==========================
# ИСТОЧНИКИ
# ==========================
@dataclass
class Source:
    id: str
    playlist_url: str
    git_repo: str
    git_file: str
    commits_limit: int = 20

SOURCE_A = Source(
    id="A",
    playlist_url="https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVdonor.m3u",
    git_repo="https://api.github.com/repos/smolnp/IPTVru",
    git_file="IPTVdonor.m3u",
    commits_limit=6
)

SOURCE_B = Source(
    id="B",
    playlist_url="https://raw.githubusercontent.com/smolnp/IPTVru/iptv-pro/IPTVххх.m3u",
    git_repo="https://api.github.com/repos/smolnp/IPTVru",
    git_file="IPTVхх.m3u",
    commits_limit=6
)

SOURCE_A_BACKUPS = [SOURCE_A.playlist_url]
SOURCE_B_BACKUPS = [SOURCE_B.playlist_url]

def download_first_available(urls):
    last_error = None
    for url in urls:
        try:
            return request_text(url)
        except Exception as e:
            last_error = e
            log(f"[WARN] Failed: {url}")
            log_error(str(e))
    raise RuntimeError(f"All sources failed: {last_error}")

# ==========================
# МОДЕЛИ
# ==========================
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
    best_stream: Optional[StreamInfo] = None
    reserve_stream: Optional[StreamInfo] = None
    quality_history: List[StreamInfo] = field(default_factory=list)

# ==========================
# ФИЛЬТРЫ
# ==========================
ADULT_CHANNELS = ["brazzers","dorcel","hustler","penthouse","xxx","eroxxxhd","extasytv","redlight","playboy","venus","hot","sextv","adult","erotic"]
GAMBLING_CHANNELS = ["casino","poker","bet","stake","1xbet","melbet","parimatch","fonbet"]

def is_adult_channel(name: str, group: Optional[str]) -> bool:
    if group and group.lower().strip() == "для взрослых":
        return True
    name_l = name.lower().strip()
    return any(bad in name_l for bad in ADULT_CHANNELS + GAMBLING_CHANNELS)

def is_bad_donor(url: str) -> bool:
    url_l = url.lower()
    if "ott.watch/stream/" in url_l:
        return True
    if "ru2.tvtm.one" in url_l and url_l.endswith("m3u8?"):
        return True
    return False

# ==========================
# ULTRA NETWORK ENGINE
# ==========================
def analyze_stream_request(url: str):
    if is_bad_donor(url):
        return False, 0.0
    try:
        with _network_semaphore:
            r = get_session().get(url, timeout=DEFAULT_TIMEOUT, stream=True, verify=False)
            NETWORK_STATS["requests"] += 1
            if r.ok:
                NETWORK_STATS["bytes"] += len(r.content or b"")
        if r.status_code not in (200, 206):
            return False, 0.0
        ctype = r.headers.get("Content-Type", "")
        if "text/html" in ctype.lower():
            return False, 0.0
        latency = r.elapsed.total_seconds()
        score = max(0.0, min(50.0 - latency * 10, 100.0))
        return True, score
    except Exception:
        NETWORK_STATS["errors"] += 1
        return False, 0.0

def analyze_stream(stream: StreamInfo) -> Optional[StreamInfo]:
    url = stream.url

    # Проверка SQLite-кэша
    cached = db_get_stream(url)
    if cached is not None:
        alive, quality = cached
        if alive:
            stream.alive = True
            stream.quality_score = quality
            return stream
        return None

    # Проверка кэша текущего запуска
    if url in _stream_alive_cache:
        alive = _stream_alive_cache[url]
        quality = _stream_quality_cache[url]
        if alive:
            stream.alive = True
            stream.quality_score = quality
            return stream
        return None

    # Сетевая проверка (объединённая alive+quality)
    alive, quality = analyze_stream_request(url)

    # Обновление кэшей
    _stream_alive_cache[url] = alive
    _stream_quality_cache[url] = quality
    db_put_stream(url, alive, quality)

    if alive:
        stream.alive = True
        stream.quality_score = quality
        return stream

    return None

def analyze_streams_parallel(streams: List[StreamInfo]) -> List[StreamInfo]:
    if not streams:
        return []
    checked_streams = list(_executor.map(analyze_stream, streams))
    result = []
    for checked in checked_streams:
        if checked is not None:
            result.append(checked)
    return result

def merge_channels(channels: List[Channel], old_channels: List[Channel]) -> List[Channel]:
    index = {}
    old_index = {old.number: old for old in old_channels}

    for ch in channels:
        old_match = old_index.get(ch.number)
        if old_match:
            # объединение с OLD
            ch.streams.extend(old_match.streams)

        # проверка потоков параллельно
        streams_candidates = analyze_streams_parallel(ch.streams)

        if streams_candidates:
            best = max(streams_candidates, key=lambda s: s.quality_score)
            ch.best_stream = best
            if len(streams_candidates) > 1:
                reserve = sorted(streams_candidates, key=lambda s: s.quality_score)[-2]
                ch.reserve_stream = reserve

        index[ch.number] = ch

    # удаление дубликатов URL
    for ch in index.values():
        ch.streams = unique_streams(ch.streams)

    return list(index.values())

def load_single_commit(source: Source, sha: str) -> List[Channel]:
    if sha in _commit_cache:
        return _commit_cache[sha]
    try:
        file_url = f"{source.git_repo}/contents/{source.git_file}?ref={sha}"
        content = request_json(file_url)
        decoded = base64.b64decode(content["content"]).decode("utf-8")
        parsed_channels = parse_m3u(decoded, source.id)
        _commit_cache[sha] = parsed_channels
        return parsed_channels
    except Exception as e:
        log(f"[WARN] Commit {sha[:8]} skipped: {e}")
        log_error(str(e))
        return []

def load_commits(source: Source) -> List[Channel]:
    url = f"{source.git_repo}/commits?path={source.git_file}&per_page={source.commits_limit}"
    commits = request_json(url)
    channels = []
    futures = [_executor.submit(load_single_commit, source, commit["sha"]) for commit in commits]
    for future in futures:
        channels.extend(future.result())
    return channels

# ==========================
# UNIQUE STREAMS
# ==========================
def unique_streams(streams: List[StreamInfo]) -> List[StreamInfo]:
    unique = {}
    result = []
    for stream in streams:
        if stream.url in unique:
            continue
        unique[stream.url] = True
        result.append(stream)
    return result

# ==========================
# PARSER M3U
# ==========================
def parse_m3u(text: str, source_id: str) -> List[Channel]:
    channels = []
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            parts = line.split(",", 1)
            info = parts[0]
            name = normalize_channel_name(parts[1]) if len(parts) > 1 else "Unknown"

            # безопасная обработка tvg-id
            m = re.search(r'tvg-id="([^"]+)"', info)
            tvg_id = normalize_tvg_id(m.group(1) if m else "")

            # безопасная обработка group-title
            m = re.search(r'group-title="([^"]+)"', info)
            group = m.group(1) if m else None

            # безопасная обработка tvg-logo
            m = re.search(r'tvg-logo="([^"]+)"', info)
            logo = m.group(1) if m else None

            current = Channel(number=tvg_id, name=name, group=group, logo=logo, scada_code="")

        elif line and not line.startswith("#") and current:
            current.streams.append(StreamInfo(source_id=source_id, url=line))
            channels.append(current)
            current = None

    return channels

# ==========================
# WRITE M3U
# ==========================
def write_m3u(filename: str, channels: List[Channel]):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            if is_adult_channel(ch.name, ch.group):
                continue
            logo = f'tvg-logo="{ch.logo}" ' if ch.logo else ""
            group = f'group-title="{ch.group}" ' if ch.group else ""
            f.write(f'#EXTINF:-1 tvg-id="{ch.number}" {logo}{group},{ch.name}\n')
            if ch.best_stream:
                f.write(f"{ch.best_stream.url}\n")
            if ch.reserve_stream:
                f.write(f"{ch.reserve_stream.url}\n")

# ==========================
# MAIN
# ==========================
def main():
    start_time = time.perf_counter()

    # загрузка источников
    srcA_text = download_first_available(SOURCE_A_BACKUPS)
    srcB_text = download_first_available(SOURCE_B_BACKUPS)

    srcA_channels = parse_m3u(srcA_text, SOURCE_A.id)
    srcB_channels = parse_m3u(srcB_text, SOURCE_B.id)

    commitsA = load_commits(SOURCE_A)
    commitsB = load_commits(SOURCE_B)

    merged = merge_channels(srcA_channels + srcB_channels + commitsA + commitsB, [])

    # запись файлов
    write_m3u("stable_new.m3u", merged)
    shutil.copy("stable_new.m3u", "Denis_iptv_2026.m3u")
    log("[INFO] Denis_iptv_2026.m3u created")

    # ==========================
    # OLD PLAYLIST HANDLING
    # ==========================
    OLD_DIR = "Old"
    os.makedirs(OLD_DIR, exist_ok=True)

    old_files = sorted([f for f in os.listdir(OLD_DIR) if f.endswith(".m3u")])
    old_count = len(old_files)

    # OLD создаётся только начиная со второго запуска
    if old_count > 0:
        old_filename = os.path.join(OLD_DIR, f"old_{old_count+1}.m3u")
        shutil.copy("stable_new.m3u", old_filename)
        log(f"[INFO] OLD playlist saved: {old_filename}")
    else:
        log("[INFO] First run — OLD not created")

    # автосейв
    write_m3u("autosave_merge.m3u", merged)

    elapsed = time.perf_counter() - start_time
    log(f"[INFO] Execution time: {elapsed:.2f} sec")
    log(f"[INFO] Network stats: {NETWORK_STATS}")

    _executor.shutdown(wait=True)

if __name__ == "__main__":
    main()