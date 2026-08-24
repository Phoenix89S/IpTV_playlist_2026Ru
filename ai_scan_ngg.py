#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alias Verification Engine v4.0 (ngSKALA Hybrid Edition + EPG 2016 Knowledge Layer)
Самообучающийся сканер CDN Ngenix с поддержкой EPG, мульти-нод, валидацией HLS
и интеграцией исторической базы знания xml_2016_knowledge.json.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import asdict, dataclass, field
from datetime import datetime
import gzip
import io
import json
import logging
import os
import re
import sqlite3
import ssl
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

# ============================================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ И КОНФИГУРАЦИЯ
# ============================================================

EPG_URL = "http://epg.one/epg2.xml.gz"
EPG_2016_KNOWLEDGE_URL = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/xml_2016_knowledge.json"
LOCAL_EPG_2016_CACHE = "xml_2016_knowledge.json"

DB_FILE_PATH = "knowledge.db"

BASE_HUMAN_REPORT_NAME = "Ai_Alias.txt"
BASE_MACHINE_REPORT_NAME = "Ai_Alias_ngnorm.txt"
BASE_JSON_EXPORT_NAME = "Ai_Alias_export.json"
OUTPUT_PLAYLIST = "playlist.m3u"

MACHINE_SOURCE = "ALIAS_MODULE_V4_HYBRID"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"

DEFAULT_REQUEST_TIMEOUT = 3
MAX_WORKER_THREADS = 20

# Узлы CDN Ngenix для динамического опроса
NGENIX_NODES = [f"s703{i}" for i in range(78, 91)]

DEFAULT_PATTERNS = [
    "{v}/index.m3u8",
    "{v}/mono.m3u8",
    "{v}/live.m3u8",
    "hls/{v}/variant.m3u8",
    "{v}/tracks-v1a1/mono.m3u8",
    "{v}/1/index.m3u8",
    "hls/CH_{v}/variant.m3u8"
]

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger("AliasEngineV4")

# ============================================================
# СЛУЖЕБНЫЕ ТАБЛИЦЫ, ПСЕВДОНИМЫ И СТОП-СЛОВА
# ============================================================

RUSSIAN_TRANSLITERATION_TABLE = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)

QUALITY_TOKENS = {"hd", "sd", "fhd", "uhd", "4k", "8k", "hevc", "50fps"}
TECHNICAL_SUFFIXES = {"channel", "tv", "television", "online", "live", "stream", "hd"}
STUB_TOKENS = {"заглушка", "stub", "test", "temp", "placeholder", "тест", "проверка", "резерв"}

CHANNEL_NAME_ALIASES: Dict[str, str] = {
    "голливуд hd": "Hollywood HD",
    "голливуд": "Hollywood HD",
    "рен тв hd": "РЕН ТВ",
    "первый": "Первый канал",
    "россия 1 hd": "Россия 1",
    "матч тв hd": "Матч ТВ",
}

KNOWN_ALIAS_DICTIONARY: Dict[str, Set[str]] = {
    "Hollywood HD": {"amc"},
    "AMC": {"amc"},
    "Карусель": {"karousel"},
    "РЕН ТВ": {"ren_tv"},
    "ТВ-3": {"tv_3"},
    "Мир": {"mir"},
    "НТВ Сериал": {"ntv_serial"},
    "Мир сериала": {"mir_seriala"},
    "Дом Кино": {"dom_kino"},
    "FilmBox": {"filmbox"},
    "Sony Turbo": {"sony_turbo"},
    "NickToons": {"nicktoons"},
    "Nickelodeon": {"nickelodeon"},
    "Gulli": {"gulli"},
    "TiJi": {"tiji"},
    "Ocean TV": {"ocean_tv"},
    "RTVi": {"rtvi"},
    "Ностальгия": {"nostalgia"},
    "Mezzo": {"mezzo"},
    "ТНТ Music": {"tnt_music"},
    "Galaxy": {"galaxy"},
}

EXTRA_CHANNELS = [
    {"id": "scream", "name": "Scream", "logo": ""},
    {"id": "shokiruyuschee", "name": "Шокирующее", "logo": ""},
    {"id": "viju_planet", "name": "viju+ planet", "logo": ""},
    {"id": "viju_tv1000_romantica", "name": "viju TV1000 romantica", "logo": ""},
    {"id": "viju_tv1000_novella", "name": "viju TV1000 новелла", "logo": ""},
    {"id": "viju_tv1000_action", "name": "viju TV1000 action", "logo": ""},
    {"id": "viju_tv1000_russkoe", "name": "viju TV1000 русское", "logo": ""},
    {"id": "hit", "name": "ХИТ", "logo": ""},
    {"id": "kinokomediya", "name": "Кинокомедия", "logo": ""},
    {"id": "cinema", "name": "CINEMA", "logo": ""},
    {"id": "mosfilm_gold", "name": "Мосфильм. Золотая коллекция", "logo": ""},
    {"id": "fantastic_channel", "name": "Fantastic Channel", "logo": ""},
    {"id": "boevik", "name": "Боевик", "logo": ""},
    {"id": "kinomix", "name": "Киномикс", "logo": ""},
    {"id": "detektiv", "name": "Детектив", "logo": ""},
    {"id": "rodnoe_kino", "name": "Родное кино", "logo": ""},
    {"id": "patriot", "name": "Патриот", "logo": ""},
    {"id": "rtg_hd", "name": "RTG HD", "logo": ""},
    {"id": "rtg_int", "name": "RTG Int", "logo": ""},
    {"id": "nat_geo_ru", "name": "National Geographic RU", "logo": ""},
    {"id": "nat_geo_wild", "name": "NAT GEO WILD", "logo": ""},
    {"id": "kinoujas", "name": "КИНОУЖАС", "logo": ""},
    {"id": "kinosemya", "name": "КИНОСЕМЬЯ", "logo": ""},
    {"id": "russkiy_roman", "name": "Русский роман", "logo": ""},
    {"id": "russkiy_detektiv", "name": "Русский детектив", "logo": ""},
    {"id": "komediya", "name": "Комедия", "logo": ""},
    {"id": "klyuch", "name": "Ключ", "logo": ""},
    {"id": "ntv_plus", "name": "НТВ-ПЛЮС", "logo": ""},
    {"id": "rutube", "name": "RUTUBE", "logo": ""},
    {"id": "premier", "name": "PREMIER", "logo": ""},
    {"id": "ntv", "name": "НТВ", "logo": ""},
    {"id": "tnt", "name": "ТНТ", "logo": ""},
    {"id": "pyatnica", "name": "Пятница!", "logo": ""},
    {"id": "tv3", "name": "ТВ-3", "logo": ""},
    {"id": "tnt4", "name": "ТНТ4", "logo": ""},
    {"id": "match_tv", "name": "Матч ТВ", "logo": ""},
    {"id": "trash", "name": "Trash", "logo": ""},
    {"id": "match_strana", "name": "Матч! Страна", "logo": ""},
    {"id": "2x2", "name": "2x2", "logo": ""},
    {"id": "subbota", "name": "Суббота!", "logo": ""},
    {"id": "ntv_style", "name": "НТВ Стиль", "logo": ""},
    {"id": "ntv_pravo", "name": "НТВ Право", "logo": ""},
    {"id": "ntv_serial", "name": "НТВ Сериал", "logo": ""},
    {"id": "ntv_hit", "name": "НТВ Хит", "logo": ""},
    {"id": "unknown_russia", "name": "Неизвестная Россия", "logo": ""},
    {"id": "boec", "name": "Боец", "logo": ""},
]

# ============================================================
# МОДУЛЬ ЗАГРУЗКИ BAZY ZNANIY EPG 2016
# ============================================================

class EPGKnowledgeBase:
    """Загрузчик и индексатор исторической базы xml_2016_knowledge.json."""
    def __init__(self, url: str = EPG_2016_KNOWLEDGE_URL, cache_path: str = LOCAL_EPG_2016_CACHE):
        self.url = url
        self.cache_path = Path(cache_path)
        self.name_to_candidates: Dict[str, Set[str]] = {}

    def load(self) -> None:
        data = None
        # 1. Загрузка по сети
        req = Request(self.url, headers={"User-Agent": DEFAULT_USER_AGENT})
        try:
            with urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
                raw_bytes = resp.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                self.cache_path.write_bytes(raw_bytes)
                LOGGER.info("База xml_2016_knowledge.json обновлена из GitHub Raw.")
        except Exception as e:
            LOGGER.warning("Не удалось скачать xml_2016_knowledge.json из сети (%s). Пробуем локальный кэш...", e)

        # 2. Локальный кэш
        if not data and self.cache_path.exists():
            try:
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                LOGGER.info("База xml_2016_knowledge.json успешно загружена из локального файла.")
            except Exception as e:
                LOGGER.error("Ошибка чтения локального файла кэша EPG 2016: %s", e)

        if data and "items" in data:
            self._index(data["items"])

    def _index(self, items: List[dict]) -> None:
        for item in items:
            ru_names = item.get("ru_names", [])
            en_names = item.get("en_names", [])
            channel_id = item.get("channel_id", "")
            candidates = set(item.get("cdn_candidates", []))

            if not candidates:
                continue

            all_names = set(ru_names + en_names)
            if channel_id:
                all_names.add(channel_id)

            for name in all_names:
                norm_key = self._normalize(name)
                if norm_key:
                    if norm_key not in self.name_to_candidates:
                        self.name_to_candidates[norm_key] = set()
                    self.name_to_candidates[norm_key].update(candidates)

        LOGGER.info("Индексация EPG 2016 завершена. Покрыто уникальных названий/ID: %d", len(self.name_to_candidates))

    @staticmethod
    def _normalize(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r'\(.*?\)', '', s)
        s = re.sub(r'[^a-zа-я0-9]', '', s)
        return s

    def get_candidates(self, channel_name: str, tvg_id: str = "") -> List[str]:
        results = set()
        for key in (channel_name, tvg_id):
            if key:
                norm = self._normalize(key)
                if norm in self.name_to_candidates:
                    results.update(self.name_to_candidates[norm])
        return list(results)

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class ChannelInput:
    display_name: str
    tvg_id: str = ""
    tvg_name: str = ""
    group_title: str = ""
    logo: str = ""

@dataclass
class CDNStream:
    alias: str
    url: str
    node: str
    pattern: str
    rule_name: str
    http_status: Optional[int] = None
    reachable: Optional[bool] = None
    response_time_ms: float = 0.0

@dataclass
class AliasCandidate:
    value: str
    reason: str
    score: float = 0.0
    confirmed: bool = False

@dataclass
class AliasMatch:
    channel_name: str
    normalized_name: str
    cdn_alias: Optional[str]
    match_type: str
    confidence: float
    reason: str
    candidates: List[AliasCandidate] = field(default_factory=list)
    streams: List[CDNStream] = field(default_factory=list)

# ============================================================
# ДИНАМИЧЕСКАЯ БАЗА ДАННЫХ И ДВИЖОК ОБУЧЕНИЯ
# ============================================================

class Database:
    def __init__(self, db_path: str = DB_FILE_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    attempts INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    weight REAL DEFAULT 0.5
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT UNIQUE,
                    attempts INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    weight REAL DEFAULT 0.5
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT,
                    rule_name TEXT,
                    pattern TEXT,
                    node TEXT,
                    success INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_aliases (
                    channel_name TEXT PRIMARY KEY,
                    cdn_alias TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    hit_count INTEGER DEFAULT 1,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def register_rule(self, rule_name: str) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO rules (name) VALUES (?)", (rule_name,))
            conn.commit()

    def register_pattern(self, pattern: str) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO patterns (pattern) VALUES (?)", (pattern,))
            conn.commit()

    def log_attempt(self, channel_id: str, rule_name: str, pattern: str, node: str, success: bool) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO history (channel_id, rule_name, pattern, node, success)
                VALUES (?, ?, ?, ?, ?)
            """, (channel_id, rule_name, pattern, node, 1 if success else 0))
            conn.commit()

    def update_weights(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE rules
                SET attempts = (SELECT COUNT(*) FROM history WHERE history.rule_name = rules.name),
                    success  = (SELECT COUNT(*) FROM history WHERE history.rule_name = rules.name AND success = 1)
            """)
            conn.execute("""
                UPDATE rules
                SET weight = (CAST(success AS REAL) + 1.0) / (CAST(attempts AS REAL) + 2.0)
            """)
            conn.execute("""
                UPDATE patterns
                SET attempts = (SELECT COUNT(*) FROM history WHERE history.pattern = patterns.pattern),
                    success  = (SELECT COUNT(*) FROM history WHERE history.pattern = patterns.pattern AND success = 1)
            """)
            conn.execute("""
                UPDATE patterns
                SET weight = (CAST(success AS REAL) + 1.0) / (CAST(attempts AS REAL) + 2.0)
            """)
            conn.commit()

    def get_ranked_rules(self) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT name, weight FROM rules ORDER BY weight DESC").fetchall()
            return [dict(r) for r in rows]

    def get_ranked_patterns(self) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT pattern, weight FROM patterns ORDER BY weight DESC").fetchall()
            return [dict(r) for r in rows]

    def record_learned_alias(self, channel_name: str, cdn_alias: str) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO learned_aliases (channel_name, cdn_alias, confidence, hit_count)
                VALUES (?, ?, 1.0, 1)
                ON CONFLICT(channel_name) DO UPDATE SET
                    cdn_alias = excluded.cdn_alias,
                    hit_count = hit_count + 1,
                    last_updated = CURRENT_TIMESTAMP
            """, (channel_name, cdn_alias))
            conn.commit()

class HybridLearner:
    def __init__(self, db: Database):
        self.db = db
        self._bootstrap()

    def _bootstrap(self) -> None:
        default_rules = [
            "epg_xml_2016", "exact_id", "clean_id", "underscore", "no_spaces",
            "translit_underscore", "translit_nospaces", "known_dictionary",
            "strip_hd", "viju_prefix", "mapped_name"
        ]
        for r in default_rules:
            self.db.register_rule(r)
        for p in DEFAULT_PATTERNS:
            self.db.register_pattern(p)

    def train(self) -> None:
        self.db.update_weights()

    def get_prioritized_patterns(self) -> List[str]:
        ranked = self.db.get_ranked_patterns()
        return [p["pattern"] for p in ranked] if ranked else DEFAULT_PATTERNS

    def get_rule_weights(self) -> Dict[str, float]:
        ranked = self.db.get_ranked_rules()
        return {r["name"]: r["weight"] for r in ranked}

# ============================================================
# НОРМАЛИЗАЦИЯ И ГЕНЕРАЦИЯ ВАРИАНТОВ
# ============================================================

def normalize_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip() if value else ""

def transliterate_russian(value: str) -> str:
    return normalize_unicode(value).casefold().translate(RUSSIAN_TRANSLITERATION_TABLE)

def generate_alias_candidates(channel: ChannelInput, learner: HybridLearner, epg_kb: Optional[EPGKnowledgeBase] = None) -> List[AliasCandidate]:
    name = channel.display_name
    epg_id = channel.tvg_id or name
    rule_weights = learner.get_rule_weights()

    candidates: Dict[str, Tuple[str, str]] = {} # alias -> (alias, rule_name)

    def add_cand(val: str, rule: str) -> None:
        val_clean = re.sub(r"[^a-z0-9]+", "_", val.casefold()).strip("_")
        if val_clean and val_clean not in candidates:
            candidates[val_clean] = (val_clean, rule)

    # 0. Исторический слой из EPG 2016 (Интеграция!)
    if epg_kb:
        historical_candidates = epg_kb.get_candidates(name, channel.tvg_id)
        for cand in historical_candidates:
            add_cand(cand, "epg_xml_2016")

    # 1. Прямой словарь
    display_norm = name.strip().casefold()
    mapped_name = CHANNEL_NAME_ALIASES.get(display_norm)

    dict_matches = set(KNOWN_ALIAS_DICTIONARY.get(name, set()))
    if mapped_name:
        dict_matches.update(KNOWN_ALIAS_DICTIONARY.get(mapped_name, set()))

    for alias in dict_matches:
        add_cand(alias, "known_dictionary")

    # 2. Правила трансформирования
    name_lower = name.lower().strip()
    clean_id = epg_id.lower().replace(" ", "").replace("-", "").replace("_", "")
    translit_name = transliterate_russian(name_lower)

    add_cand(epg_id, "exact_id")
    add_cand(clean_id, "clean_id")
    add_cand(name_lower.replace(" ", "_"), "underscore")
    add_cand(name_lower.replace(" ", ""), "no_spaces")
    add_cand(translit_name.replace(" ", "_"), "translit_underscore")
    add_cand(translit_name.replace(" ", ""), "translit_nospaces")

    if "hd" in name_lower:
        add_cand(name_lower.replace("hd", "").replace(" ", ""), "strip_hd")

    if "viju" in name_lower:
        core = transliterate_russian(name_lower.replace("viju", "").replace("+", "").strip())
        add_cand(f"vip_{core}", "viju_prefix")

    if mapped_name:
        add_cand(transliterate_russian(mapped_name), "mapped_name")

    result = []
    for alias, rule in candidates.values():
        score = rule_weights.get(rule, 0.5)
        result.append(AliasCandidate(value=alias, reason=rule, score=score))

    return sorted(result, key=lambda x: -x.score)

# ============================================================
# МУЛЬТИ-НОДОВЫЙ СКАНИРОВЩИК И ИСПЫТАТЕЛЬ ПОТОКОВ
# ============================================================

class MultiNodeScanner:
    def __init__(self, db: Database, learner: HybridLearner, epg_kb: Optional[EPGKnowledgeBase] = None):
        self.db = db
        self.learner = learner
        self.epg_kb = epg_kb
        self.active_nodes: List[str] = []

    def ping_nodes(self) -> List[str]:
        LOGGER.info("Опрос и проверка доступности узлов Ngenix CDN...")
        valid_nodes = []

        def check_node(node: str) -> Optional[str]:
            url = f"https://{node}.cdn.ngenix.net/"
            req = Request(url, method="HEAD", headers={"User-Agent": DEFAULT_USER_AGENT})
            try:
                with urlopen(req, timeout=DEFAULT_REQUEST_TIMEOUT, context=SSL_CONTEXT) as response:
                    if getattr(response, "status", 200) < 500:
                        return node
            except HTTPError as e:
                if e.code < 500:
                    return node
            except Exception:
                pass
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(check_node, node) for node in NGENIX_NODES]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    valid_nodes.append(res)

        self.active_nodes = sorted(valid_nodes) if valid_nodes else ["s70378"]
        LOGGER.info("Откликнулись узлы Ngenix (%d/%d): %s", len(self.active_nodes), len(NGENIX_NODES), ", ".join(self.active_nodes))
        return self.active_nodes

    def verify_hls_stream(self, url: str) -> Tuple[bool, int, float]:
        start_time = time.time()
        req = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        try:
            with urlopen(req, timeout=DEFAULT_REQUEST_TIMEOUT, context=SSL_CONTEXT) as response:
                status = getattr(response, "status", 200)
                elapsed_ms = (time.time() - start_time) * 1000.0
                if status == 200:
                    chunk = response.read(256).decode("utf-8", errors="ignore")
                    if "#EXTM3U" in chunk:
                        return True, 200, elapsed_ms
                return False, status, elapsed_ms
        except HTTPError as e:
            return False, e.code, (time.time() - start_time) * 1000.0
        except Exception:
            return False, 0, (time.time() - start_time) * 1000.0

    def probe_channel(self, channel: ChannelInput) -> AliasMatch:
        candidates = generate_alias_candidates(channel, self.learner, self.epg_kb)
        patterns = self.learner.get_prioritized_patterns()
        nodes = self.active_nodes if self.active_nodes else ["s70378"]

        for cand in candidates:
            for node in nodes:
                for pattern in patterns:
                    relative_path = pattern.format(v=cand.value)
                    stream_url = f"https://{node}.cdn.ngenix.net/{relative_path}"

                    is_valid, http_status, ping_ms = self.verify_hls_stream(stream_url)
                    self.db.log_attempt(channel.tvg_id or channel.display_name, cand.reason, pattern, node, is_valid)

                    if is_valid:
                        cand.confirmed = True
                        self.db.record_learned_alias(channel.display_name, cand.value)

                        stream = CDNStream(
                            alias=cand.value,
                            url=stream_url,
                            node=f"{node}.cdn.ngenix.net",
                            pattern=pattern,
                            rule_name=cand.reason,
                            http_status=http_status,
                            reachable=True,
                            response_time_ms=ping_ms
                        )

                        return AliasMatch(
                            channel_name=channel.display_name,
                            normalized_name=cand.value,
                            cdn_alias=cand.value,
                            match_type="CONFIRMED_HYBRID",
                            confidence=cand.score,
                            reason=f"Успешная HLS-валидация на ноде {node} (Правило: {cand.reason})",
                            candidates=candidates,
                            streams=[stream]
                        )

        return AliasMatch(
            channel_name=channel.display_name,
            normalized_name=channel.display_name.lower(),
            cdn_alias=None,
            match_type="UNKNOWN",
            confidence=0.0,
            reason="Ни один кандидат/узел не прошел проверку HLS",
            candidates=candidates,
            streams=[]
        )

# ============================================================
# МОДУЛЬ EPG ВЫГРУЗКИ
# ============================================================

def fetch_epg_channels() -> List[ChannelInput]:
    LOGGER.info("Загрузка и парсинг EPG с %s ...", EPG_URL)
    req = Request(EPG_URL, headers={"User-Agent": DEFAULT_USER_AGENT})
    channels = []
    try:
        with urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
            gz = gzip.GzipFile(fileobj=io.BytesIO(resp.read()))
            root = ET.fromstring(gz.read())

            for ch in root.findall("channel"):
                cid = ch.get("id", "").strip()
                disp = ch.find("display-name")
                icon = ch.find("icon")

                logo = icon.get("src", "").strip() if icon is not None else ""
                name = disp.text.strip() if disp is not None and disp.text else ""

                if cid and name:
                    channels.append(ChannelInput(display_name=name, tvg_id=cid, logo=logo))
    except Exception as e:
        LOGGER.error("Ошибка загрузки EPG: %s", e)

    # Добавляем встроенные дополнительные каналы
    for extra in EXTRA_CHANNELS:
        channels.append(ChannelInput(display_name=extra["name"], tvg_id=extra["id"], logo=extra["logo"]))

    LOGGER.info("Всего сформировано каналов для сканирования: %d", len(channels))
    return channels

# ============================================================
# ГЕНЕРАЦИЯ ОТЧЕТОВ И ФАЙЛОВ
# ============================================================

def generate_numbered_filename(base_filename: str) -> str:
    if not os.path.exists(base_filename):
        return base_filename
    name, ext = os.path.splitext(base_filename)
    counter = 1
    while True:
        new_filename = f"{name}_{counter}{ext}"
        if not os.path.exists(new_filename):
            return new_filename
        counter += 1

def save_all_reports(channels: List[ChannelInput], results: List[AliasMatch]) -> None:
    txt_file = generate_numbered_filename(BASE_HUMAN_REPORT_NAME)
    ngnorm_file = generate_numbered_filename(BASE_MACHINE_REPORT_NAME)
    json_file = generate_numbered_filename(BASE_JSON_EXPORT_NAME)
    m3u_file = generate_numbered_filename(OUTPUT_PLAYLIST)

    # 1. Текстовый отчёт
    with open(txt_file, "w", encoding="utf-8") as f:
        f.write("=== ALIAS ENGINE V4 (HYBRID) REPORT ===\n\n")
        for res in results:
            if res.cdn_alias and res.streams:
                s = res.streams[0]
                f.write(f"[КАНАЛ] {res.channel_name}\n")
                f.write(f"  [ALIAS] {res.cdn_alias}\n")
                f.write(f"  [NODE] {s.node}\n")
                f.write(f"  [RULE] {s.rule_name}\n")
                f.write(f"  [URL] {s.url}\n")
                f.write("-" * 50 + "\n")

    # 2. Машинный отчёт
    found_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ngnorm_file, "w", encoding="utf-8") as f:
        for res in results:
            s = res.streams[0] if res.streams else None
            f.write(f"NAME={res.channel_name}\n")
            f.write(f"ALIAS={res.cdn_alias or ''}\n")
            f.write(f"URL={s.url if s else ''}\n")
            f.write(f"STATUS={s.http_status if s else 'UNKNOWN'}\n")
            f.write(f"SOURCE={MACHINE_SOURCE}\n")
            f.write(f"FOUND={found_time}\n\n")

    # 3. JSON Export
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)

    # 4. IPTV M3U Playlist
    with open(m3u_file, "w", encoding="utf-8") as f:
        f.write('#EXTM3U url-tvg="http://epg.one/epg2.xml.gz"\n')
        for idx, res in enumerate(results):
            if res.cdn_alias and res.streams:
                ch = channels[idx] if idx < len(channels) else ChannelInput(display_name=res.channel_name)
                logo_attr = f' tvg-logo="{ch.logo}"' if ch.logo else ""
                f.write(f'#EXTINF:-1 tvg-id="{ch.tvg_id}" tvg-name="{res.channel_name}"{logo_attr},{res.channel_name}\n')
                f.write(f"{res.streams[0].url}\n")

    LOGGER.info("Сохранен текстовый отчет: %s", txt_file)
    LOGGER.info("Сохранен машинный отчет: %s", ngnorm_file)
    LOGGER.info("Сохранен JSON экспорт: %s", json_file)
    LOGGER.info("Сохранен M3U плейлист: %s", m3u_file)

# ============================================================
# ТОЧКА ВХОДА С CLI
# ============================================================

def run_pipeline() -> None:
    db = Database()
    learner = HybridLearner(db)

    # Подключаем EPG 2016 Knowledge Layer
    epg_kb = EPGKnowledgeBase()
    epg_kb.load()

    scanner = MultiNodeScanner(db, learner, epg_kb)

    scanner.ping_nodes()
    channels = fetch_epg_channels()

    results: List[AliasMatch] = []
    LOGGER.info("Старт параллельного сканирования...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS) as executor:
        future_to_ch = {executor.submit(scanner.probe_channel, ch): ch for ch in channels}
        for future in concurrent.futures.as_completed(future_to_ch):
            res = future.result()
            results.append(res)
            if res.cdn_alias:
                LOGGER.info("[+] Найден: %s -> %s (Правило: %s)", res.channel_name, res.streams[0].url, res.streams[0].rule_name)

    LOGGER.info("Перерасчет рейтингов и самообучение модели...")
    learner.train()

    LOGGER.info("Экспорт отчетов...")
    save_all_reports(channels, results)

def show_stats() -> None:
    db = Database()
    print("\n=== РЕЙТИНГ ПРАВИЛ ===")
    for r in db.get_ranked_rules():
        print(f"Правило: {r['name']:<25} Вес: {r['weight']:.4f}")

    print("\n=== РЕЙТИНГ ШАБЛОНОВ ===")
    for p in db.get_ranked_patterns():
        print(f"Шаблон: {p['pattern']:<35} Вес: {p['weight']:.4f}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Alias Verification Engine v4.0 (ngSKALA Hybrid)")
    parser.add_argument("--scan", action="store_true", help="Запустить полное сканирование и сформировать отчёты")
    parser.add_argument("--train", action="store_true", help="Переобучить модель по истории")
    parser.add_argument("--stats", action="store_true", help="Показать веса правил и шаблонов")

    args = parser.parse_args()

    if args.scan:
        run_pipeline()
    elif args.train:
        db = Database()
        HybridLearner(db).train()
        print("[+] Модель успешно переобучена.")
    elif args.stats:
        show_stats()
    else:
        # Режим по умолчанию без параметров
        run_pipeline()

if __name__ == "__main__":
    main()
