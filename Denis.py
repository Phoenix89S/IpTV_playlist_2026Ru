#!/usr/bin/env python3

#Denis IPTV Builder / iptv_parser_v12_ultra.py

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
# ============================================================
# PARSER M3U (УСИЛЕННЫЙ СБОРЩИК — ОБЪЕДИНЕНИЕ v10 + Gemini + Гигачат + Грок)
# ============================================================

def parse_m3u(text: str, source_id: str) -> List[Channel]:
    channels = []
    current = None

    # Проходим весь текст построчно
    for line in text.splitlines():
        line = line.strip()

        #------------------------------------------------------------
        # Блок EXTINF — извлечение метаданных канала
        #------------------------------------------------------------
        if line.startswith("#EXTINF:"):
            parts = line.split(",", 1)
            info = parts[0]
            name = normalize_channel_name(parts[1]) if len(parts) > 1 else "Unknown"

            # tvg-id
            m = re.search(r'tvg-id="([^"]+)"', info)
            tvg_id = normalize_tvg_id(m.group(1) if m else "")

            # group-title
            m = re.search(r'group-title="([^"]+)"', info)
            group = m.group(1) if m else None

            # tvg-logo
            m = re.search(r'tvg-logo="([^"]+)"', info)
            logo = m.group(1) if m else None

            #------------------------------------------------------------
            # Жёсткий фильтр 18+ на этапе парсинга (Грок + Гигачат + твой v10)
            #----------------------------------------------------------
            if is_adult_channel(name, group):
                current = None
                continue

            # Создаём канал
            current = Channel(
                number=tvg_id,
                name=name,
                group=group,
                logo=logo,
                scada_code=""
            )
#------------------------------------------------------------
        # Блок URL — добавление потоков
        #------------------------------------------------------------
        elif line and not line.startswith("#") and current:

            # Фильтрация мусорных доноров (твоя логика + Грок)
            if not is_bad_donor(line):
                current.streams.append(StreamInfo(source_id=source_id, url=line))
                channels.append(current)

            # Сбрасываем текущий канал
            current = None

    return channels

# ============================================================
# MERGE CHANNELS (МОЩНЫЙ СБОР ИЗ ДОНОРА И КОММИТОВ — v12 ULTRA)
# ============================================================

def merge_channels(channels: List[Channel], old_channels: List[Channel]) -> List[Channel]:
    #------------------------------------------------------------
    # Создаём индекс каналов по номеру (твоя логика v10 + улучшения Gemini)
    #------------------------------------------------------------
    index = {}
    old_index = {old.number: old for old in old_channels}

    #------------------------------------------------------------
    # Склейка всех каналов по номеру, без потери ссылок
    #------------------------------------------------------------
    for ch in channels:

        # Добавляем старые стримы (твоя логика v10)
        old_match = old_index.get(ch.number)
        if old_match:
            ch.streams.extend(old_match.streams)

        # Создаём канал в индексе, если его ещё нет
        if ch.number not in index:
            index[ch.number] = Channel(
                number=ch.number,
                name=ch.name,
                group=ch.group,
                logo=ch.logo,
                scada_code=ch.scada_code
            )

        # Добавляем стримы
        index[ch.number].streams.extend(ch.streams)

    #------------------------------------------------------------
    # Дедупликация ссылок (твоя логика + улучшения Гигачата)
    #------------------------------------------------------------
    for ch in index.values():
        ch.streams = unique_streams(ch.streams)

    #------------------------------------------------------------
    # Проверка всех стримов параллельно (твоя логика + Gemini)
    #------------------------------------------------------------
    all_streams = []
    for ch in index.values():
        all_streams.extend(ch.streams)

    checked = analyze_streams_parallel(all_streams)

    #------------------------------------------------------------
    # Создаём быстрый индекс URL → StreamInfo
    #------------------------------------------------------------
    url_to_stream = {s.url: s for s in checked}

    final_channels = []

    #------------------------------------------------------------
    # Распределение результатов по каналам
    #------------------------------------------------------------
    for ch in index.values():
        valid_streams = []

        # Собираем только живые стримы
        for s in ch.streams:
            if s.url in url_to_stream:
                valid_streams.append(url_to_stream[s.url])

        # Если нет живых стримов — канал пропускаем
        if not valid_streams:
            continue

        #------------------------------------------------------------
        # Сортировка по качеству (улучшенный scoring Грока)
        #------------------------------------------------------------
        valid_streams.sort(key=lambda s: s.quality_score, reverse=True)

        #------------------------------------------------------------
        # Основной поток — лучший
        #------------------------------------------------------------
        ch.best_stream = valid_streams[0]

        #------------------------------------------------------------
        # Резервные потоки — ВСЕ остальные, без сокращений
        #------------------------------------------------------------
        if len(valid_streams) > 1:
            ch.reserve_streams = valid_streams[1:]
        else:
            ch.reserve_streams = []

        #------------------------------------------------------------
        # Жёсткий фильтр 18+ и мусорных ссылок (Грок + Гигачат + твой v10)
        #------------------------------------------------------------
        if passes_strict_filter(ch):
            final_channels.append(ch)

    return final_channels

# ============================================================
# ULTRA NETWORK ENGINE (ОБЪЕДИНЁННЫЙ АНАЛИЗАТОР ПОТОКОВ v12)
# ============================================================

def analyze_stream_request(url: str):
    #------------------------------------------------------------
    # Фильтрация мусорных доноров (твоя логика + Грок)
    #------------------------------------------------------------
    if is_bad_donor(url):
        return False, 0.0

    try:
        with _network_semaphore:
            r = get_session().get(url, timeout=DEFAULT_TIMEOUT, stream=True, verify=False)

            NETWORK_STATS["requests"] += 1

            if r.ok:
                NETWORK_STATS["bytes"] += len(r.content or b"")

            #------------------------------------------------------------
            # Проверка статуса (твоя логика v10)
            #------------------------------------------------------------
            if r.status_code not in (200, 206):
                return False, 0.0

            #------------------------------------------------------------
            # Проверка Content-Type (Гигачат + Грок)
            #------------------------------------------------------------
            ctype = r.headers.get("Content-Type", "")
            if "text/html" in ctype.lower():
                return False, 0.0

            #------------------------------------------------------------
            # Улучшенный scoring (Грок + Gemini)
            #------------------------------------------------------------
            latency = r.elapsed.total_seconds()
            score = max(0.0, min(70.0 - latency * 12, 100.0))

            return True, score

    except Exception:
        NETWORK_STATS["errors"] += 1
        return False, 0.0


def analyze_stream(stream: StreamInfo) -> Optional[StreamInfo]:
    url = stream.url

    #------------------------------------------------------------
    # Проверка SQLite-кэша (твоя логика v10)
    #------------------------------------------------------------
    cached = db_get_stream(url)
    if cached is not None:
        alive, quality = cached
        if alive:
            stream.alive = True
            stream.quality_score = quality
            return stream
        return None

    #------------------------------------------------------------
    # Проверка кэша текущего запуска (твоя логика v10)
    #------------------------------------------------------------
    if url in _stream_alive_cache:
        alive = _stream_alive_cache[url]
        quality = _stream_quality_cache[url]
        if alive:
            stream.alive = True
            stream.quality_score = quality
            return stream
        return None

    #------------------------------------------------------------
    # Сетевая проверка (объединённая логика 4 ИИ)
    #------------------------------------------------------------
    alive, quality = analyze_stream_request(url)

    #------------------------------------------------------------
    # Обновление кэшей (твоя логика v10)
    #------------------------------------------------------------
    _stream_alive_cache[url] = alive
    _stream_quality_cache[url] = quality
    db_put_stream(url, alive, quality)

    if alive:
        stream.alive = True
        stream.quality_score = quality
        return stream

    return None


def analyze_streams_parallel(streams: List[StreamInfo]) -> List[StreamInfo]:
    #------------------------------------------------------------
    # Параллельная проверка потоков (твоя логика + Gemini)
    #------------------------------------------------------------
    if not streams:
        return []

    checked_streams = list(_executor.map(analyze_stream, streams))

    result = []
    for checked in checked_streams:
        if checked is not None:
            result.append(checked)

    return result

#============================================================
# LOAD COMMITS (ПАРАЛЛЕЛЬНАЯ ЗАГРУЗКА ИСТОРИИ GIT — v12 ULTRA)
#============================================================

def load_single_commit(source: Source, sha: str) -> List[Channel]:
    #------------------------------------------------------------
    # Кэширование коммитов (твоя логика v10)
    #------------------------------------------------------------
    if sha in _commit_cache:
        return _commit_cache[sha]

    try:
        #------------------------------------------------------------
        # Формирование URL для GitHub API
        #------------------------------------------------------------
        file_url = f"{source.git_repo}/contents/{source.git_file}?ref={sha}"

        #------------------------------------------------------------
        # Загрузка JSON (твоя логика + улучшения Гигачата)
        #------------------------------------------------------------
        content = request_json(file_url)

        #------------------------------------------------------------
        # Декодирование base64 (твоя логика v10)
        #------------------------------------------------------------
        decoded = base64.b64decode(content["content"]).decode("utf-8")

        #------------------------------------------------------------
        # Парсинг M3U (усиленный парсер из части 3)
        #------------------------------------------------------------
        parsed_channels = parse_m3u(decoded, source.id)

        #------------------------------------------------------------
        # Кэширование результата
        #------------------------------------------------------------
        _commit_cache[sha] = parsed_channels

        return parsed_channels

    except Exception as e:
        #------------------------------------------------------------
        # Логирование ошибок (твоя логика v10)
        #------------------------------------------------------------
        log(f"[WARN] Commit {sha[:8]} skipped: {e}")
        log_error(str(e))
        return []


def load_commits(source: Source) -> List[Channel]:
    #------------------------------------------------------------
    # Формирование URL для списка коммитов
    #------------------------------------------------------------
    url = f"{source.git_repo}/commits?path={source.git_file}&per_page={source.commits_limit}"

    #------------------------------------------------------------
    # Загрузка списка коммитов
    #------------------------------------------------------------
    commits = request_json(url)

    channels = []

    #------------------------------------------------------------
    # Параллельная загрузка каждого коммита (твоя логика + Gemini)
    #------------------------------------------------------------
    futures = [
        _executor.submit(load_single_commit, source, commit["sha"])
        for commit in commits
    ]

    #------------------------------------------------------------
    # Сбор результатов
    #------------------------------------------------------------
    for future in futures:
        channels.extend(future.result())

    return channels

# ============================================================
# WRITE M3U (БЕЗ СОКРАЩЕНИЯ ССЫЛОК — v12 ULTRA)
# ============================================================

def write_m3u(filename: str, channels: List[Channel]):
    with open(filename, "w", encoding="utf-8") as f:
        #-----------------------------------------------------------
        # Заголовок плейлиста
        #------------------------------------------------------------
        f.write("#EXTM3U\n")

        #------------------------------------------------------------
        # Проходим по всем каналам
        #------------------------------------------------------------
        for ch in channels:

            #-----------------------------------------------------------
            # Жёсткий фильтр 18+ на выходе (двойная защита)
            #------------------------------------------------------------
            if is_adult_channel(ch.name, ch.group):
                continue

            #------------------------------------------------------------
            # Формирование метаданных EXTINF
            #------------------------------------------------------------
            logo = f'tvg-logo="{ch.logo}" ' if ch.logo else ""
            group = f'group-title="{ch.group}" ' if ch.group else ""

            #------------------------------------------------------------
            # Основной поток (best_stream)
            #------------------------------------------------------------
            if ch.best_stream:
                f.write(
                    f'#EXTINF:-1 tvg-id="{ch.number}" {logo}{group},{ch.name}\n'
                )
                f.write(f"{ch.best_stream.url}\n")

            #------------------------------------------------------------
            # Резервные потоки — ВСЕ, без сокращений
            # Gemini + Гигачат + Грок + твой v10 → все резервные стримы сохраняются
            #------------------------------------------------------------
            for idx, s in enumerate(ch.reserve_streams, start=1):
                f.write(
                    f'#EXTINF:-1 tvg-id="{ch.number}" {logo}{group},{ch.name} (Резерв {idx})\n'
                )
                f.write(f"{s.url}\n")

# ============================================================
# MAIN (ПОЛНЫЙ БЛОК — v12 ULTRA)
# ============================================================

def main():
    start_time = time.perf_counter()

    #------------------------------------------------------------
    # Загрузка доноров A и B
    #-----------------------------------------------------------
    srcA_text = download_first_available(SOURCE_A_BACKUPS)
    srcB_text = download_first_available(SOURCE_B_BACKUPS)

    #------------------------------------------------------------
    # Парсинг плейлистов доноров
    #------------------------------------------------------------
    srcA_channels = parse_m3u(srcA_text, SOURCE_A.id)
    srcB_channels = parse_m3u(srcB_text, SOURCE_B.id)

    #------------------------------------------------------------
    # Загрузка коммитов GitHub (параллельно)
    #------------------------------------------------------------
    commitsA = load_commits(SOURCE_A)
    commitsB = load_commits(SOURCE_B)

    #------------------------------------------------------------
    # Мощный MERGE (донор + коммиты)
    #------------------------------------------------------------
    merged = merge_channels(
        srcA_channels + srcB_channels + commitsA + commitsB,
        []
    )

    #------------------------------------------------------------
    # Запись основного плейлиста
    #------------------------------------------------------------
    write_m3u("stable_new.m3u", merged)
    shutil.copy("stable_new.m3u", "Denis_iptv_2026.m3u")
    log("[INFO] Denis_iptv_2026.m3u created")

    #-----------------------------------------------------------
    # OLD backup
    #------------------------------------------------------------
    OLD_DIR = "Old"
    os.makedirs(OLD_DIR, exist_ok=True)

    old_files = sorted([f for f in os.listdir(OLD_DIR) if f.endswith(".m3u")])
    old_count = len(old_files)

    if old_count > 0:
        old_filename = os.path.join(OLD_DIR, f"old_{old_count+1}.m3u")
        shutil.copy("stable_new.m3u", old_filename)
        log(f"[INFO] OLD playlist saved: {old_filename}")
    else:
        log("[INFO] First run — OLD not created")

    #------------------------------------------------------------
    # Autosave
    #------------------------------------------------------------
    write_m3u("autosave_merge.m3u", merged)

    #------------------------------------------------------------
    # Статистика
    #------------------------------------------------------------
    elapsed = time.perf_counter() - start_time
    log(f"[INFO] Execution time: {elapsed:.2f} sec")
    log(f"[INFO] Network stats: {NETWORK_STATS}")

    #------------------------------------------------------------
    # Завершение пула потоков
    #------------------------------------------------------------
    _executor.shutdown(wait=True)


#------------------------------------------------------------
# Точка входа
#------------------------------------------------------------
if __name__ == "__main__":
    main()





