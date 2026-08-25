#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alias Verification Engine v5.5 (ngSKALA ML-Hybrid Edition + EPG 2016 Knowledge Layer)
Самообучающийся сканер CDN Ngenix с ML-ранжированием кандидатов (Scikit-Learn Ensemble),
поддержкой EPG 2016, мульти-нод, глубокой валидацией HLS, универсальным загрузчиком JSON и генерацией отчетов.
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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import joblib
import numpy as np

try:
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
    from sklearn.model_selection import train_test_split
    HAS_ML = True
except ImportError:
    HAS_ML = False

# ============================================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ И КОНФИГУРАЦИЯ
# ============================================================

EPG_URL = "http://epg.one/epg2.xml.gz"
EPG_2016_KNOWLEDGE_URL = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/xml_2016_knowledge.json"
LOCAL_EPG_2016_CACHE = "xml_2016_knowledge.json"

DB_FILE_PATH = "knowledge.db"
MODEL_FILE = "data/model.joblib"

BASE_HUMAN_REPORT_NAME = "Ai_Alias.txt"
BASE_MACHINE_REPORT_NAME = "Ai_Alias_ngnorm.txt"
BASE_JSON_EXPORT_NAME = "Ai_Alias_export.json"
OUTPUT_PLAYLIST = "playlist.m3u"

MACHINE_SOURCE = "ALIAS_MODULE_V5_5_ML_HYBRID"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"

DEFAULT_REQUEST_TIMEOUT = 3
MAX_WORKER_THREADS = 20
MIN_TRAINING_SAMPLES = 30
TOP_RESULTS = 25

# ============================================================
# UNIVERSAL JSON SOURCES
# ============================================================

REMOTE_JSON_SOURCES = [
    "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/Ai_Alias_export.json",
    "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/Ai_Alias_export_1.json",
    "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/Ai_Alias_export_2.json",
]


def load_json_source(source):
    """
    Универсальная загрузка JSON:
    - URL
    - локальный файл
    """
    try:
        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------
        if str(source).startswith(("http://", "https://")):
            req = Request(source, headers={"User-Agent": DEFAULT_USER_AGENT})
            with urlopen(req, timeout=DEFAULT_REQUEST_TIMEOUT, context=SSL_CONTEXT) as response:
                status_code = getattr(response, "status", 200)
                if status_code != 200:
                    print(
                        f"[JSON] HTTP "
                        f"{status_code}: "
                        f"{source}"
                    )
                    return None
                return json.loads(response.read().decode("utf-8"))

        # ----------------------------------------------------
        # Локальный файл
        # ----------------------------------------------------
        path = Path(source)

        if not path.exists():
            print(
                f"[JSON] Файл не найден: "
                f"{path}"
            )
            return None

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception as e:
        print(
            f"[JSON] Ошибка загрузки "
            f"{source}: {e}"
        )
        return None


def iter_json_records(data):
    """
    Универсально извлекает записи из JSON.
    """
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
        return

    if isinstance(data, dict):
        # Возможные контейнеры
        for key in (
            "records",
            "data",
            "items",
            "results",
            "aliases",
            "channels",
        ):
            value = data.get(key)

            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return

        # JSON может сам быть одной записью
        yield data


def find_all_json_sources():
    """
    Сохраняет универсальный поиск локальных JSON
    и добавляет фиксированные удалённые источники.
    """
    sources = []

    # ========================================================
    # 1. ТВОЙ СУЩЕСТВУЮЩИЙ УНИВЕРСАЛЬНЫЙ ПОИСК
    # ========================================================
    patterns = [
        "*.json",
        "Ai_Alias*.json",
        "*Alias*.json",
    ]

    found = set()

    for pattern in patterns:
        for path in Path(".").rglob(pattern):
            if path.is_file():
                found.add(
                    path.resolve()
                )

    for path in sorted(found):
        sources.append(
            str(path)
        )

    # ========================================================
    # 2. ФИКСИРОВАННЫЕ ИСТОРИЧЕСКИЕ JSON
    # ========================================================
    for url in REMOTE_JSON_SOURCES:
        if url not in sources:
            sources.append(url)

    return sources


def load_all_json_records():
    """
    Загружает ВСЕ источники:
    локальные + удалённые.

    Возвращает единый список записей.
    """
    sources = find_all_json_sources()

    all_records = []

    print(
        f"[JSON] Источников найдено: "
        f"{len(sources)}"
    )

    for source in sources:
        print(
            f"[JSON] Загрузка: {source}"
        )

        data = load_json_source(
            source
        )

        if data is None:
            continue

        count = 0

        for record in iter_json_records(
            data
        ):
            record = dict(record)

            # сохраняем происхождение записи
            record[
                "_json_source"
            ] = source

            all_records.append(
                record
            )

            count += 1

        print(
            f"[JSON] Получено записей: "
            f"{count}"
        )

    print(
        f"[JSON] Всего записей: "
        f"{len(all_records)}"
    )

    return all_records

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
LOGGER = logging.getLogger("AliasEngineV5.5")

# ============================================================
# СЛУЖЕБНЫЕ ТАБЛИЦЫ И СЛОВАРНЫЕ ДАННЫЕ
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
# ML FEATURE EXTRACTOR
# ============================================================

def make_features(candidate: str, rule: str) -> List[float]:
    val = str(candidate)
    lower = val.lower()
    return [
        float(len(val)),
        float(sum(c.isdigit() for c in val)),
        float(sum(c.isalpha() for c in val)),
        float(val.count("_")),
        float(val.count("-")),
        float(val.count(".")),
        float(val.count(" ")),
        float("hd" in lower),
        float("tv" in lower),
        float("plus" in lower),
        float("premium" in lower),
        float(val == lower),
        float("_" in val),
        float("-" in val),
        float(len(set(val))),
        float(len(re.findall(r"[aeiou]", lower))),
        float(len(re.findall(r"[0-9]", val))),
        float(hash(rule) % 1000),
    ]

def build_matrix(rows: List[Dict]) -> np.ndarray:
    return np.asarray([make_features(r["candidate"], r["rule"]) for r in rows], dtype=float)

# ============================================================
# ML ENSEMBLE CLASSIFIER MODEL
# ============================================================

class EnsembleModel:
    def __init__(self):
        if not HAS_ML:
            self.trained = False
            return

        self.random_forest = RandomForestClassifier(
            n_estimators=250, max_depth=12, min_samples_leaf=2,
            class_weight="balanced", random_state=42, n_jobs=-1
        )
        self.gradient_boosting = HistGradientBoostingClassifier(
            max_iter=250, learning_rate=0.05, max_leaf_nodes=20,
            l2_regularization=0.5, random_state=42
        )
        self.trained = False

    def fit(self, rows: List[Dict]) -> Dict[str, float]:
        if not HAS_ML:
            raise ValueError("Библиотека scikit-learn не установлена.")
        if len(rows) < MIN_TRAINING_SAMPLES:
            raise ValueError(f"Недостаточно данных ({len(rows)}/{MIN_TRAINING_SAMPLES}).")

        X = build_matrix(rows)
        y = np.asarray([row["success"] for row in rows])

        if len(set(y)) < 2:
            raise ValueError("Требуются данные обоих классов (SUCCESS и FAIL).")

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42, stratify=y
        )

        self.random_forest.fit(X_train, y_train)
        self.gradient_boosting.fit(X_train, y_train)

        rf_prob = self.random_forest.predict_proba(X_test)[:, 1]
        gb_prob = self.gradient_boosting.predict_proba(X_test)[:, 1]
        prob = (rf_prob + gb_prob) * 0.5
        pred = (prob >= 0.5).astype(int)

        metrics = {
            "accuracy": accuracy_score(y_test, pred),
            "precision": precision_score(y_test, pred, zero_division=0),
            "recall": recall_score(y_test, pred, zero_division=0),
            "roc_auc": roc_auc_score(y_test, prob),
        }
        self.trained = True
        return metrics

    def predict_probability(self, candidates: List[Dict]) -> List[float]:
        if not HAS_ML or not self.trained:
            return [0.5 for _ in candidates]

        X = build_matrix(candidates)
        rf_prob = self.random_forest.predict_proba(X)[:, 1]
        gb_prob = self.gradient_boosting.predict_proba(X)[:, 1]
        return ((rf_prob + gb_prob) * 0.5).tolist()

    def save(self, path: str = MODEL_FILE) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, p)

    @staticmethod
    def load(path: str = MODEL_FILE) -> Optional[EnsembleModel]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            return joblib.load(p)
        except Exception:
            return None

# ============================================================
# DATABASE & KNOWLEDGE BASE
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
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    pattern TEXT,
                    node TEXT,
                    success INTEGER NOT NULL,
                    status_code INTEGER,
                    response_time REAL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_att_cand ON attempts(candidate)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_att_rule ON attempts(rule)")
            conn.commit()

    def save_attempt(self, candidate: str, rule: str, pattern: str, node: str, success: bool, status_code: Optional[int], response_time: float) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO attempts (candidate, rule, pattern, node, success, status_code, response_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (candidate, rule, pattern, node, 1 if success else 0, status_code, response_time))
            conn.commit()

    def get_training_data(self) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT candidate, rule, success, status_code, response_time FROM attempts ORDER BY id").fetchall()
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

    def get_statistics(self) -> Dict[str, int]:
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            successful = conn.execute("SELECT COUNT(*) FROM attempts WHERE success = 1").fetchone()[0]
            return {"total": total, "successful": successful, "failed": total - successful}

# ============================================================
# EPG KNOWLEDGE BASE 2016
# ============================================================

class EPGKnowledgeBase:
    def __init__(self, url: str = EPG_2016_KNOWLEDGE_URL, cache_path: str = LOCAL_EPG_2016_CACHE):
        self.url = url
        self.cache_path = Path(cache_path)
        self.name_to_candidates: Dict[str, Set[str]] = {}

    def load(self) -> None:
        data = None
        req = Request(self.url, headers={"User-Agent": DEFAULT_USER_AGENT})
        try:
            with urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
                raw_bytes = resp.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                self.cache_path.write_bytes(raw_bytes)
                LOGGER.info("База xml_2016_knowledge.json обновлена из GitHub Raw.")
        except Exception as e:
            LOGGER.warning("Не удалось скачать xml_2016_knowledge.json (%s). Пробуем кэш...", e)

        if not data and self.cache_path.exists():
            try:
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                LOGGER.info("База xml_2016_knowledge.json загружена из локального файла.")
            except Exception as e:
                LOGGER.error("Ошибка чтения локального файла EPG 2016: %s", e)

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

        LOGGER.info("Индексация EPG 2016 завершена. Индексировано названий: %d", len(self.name_to_candidates))

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
# РАБОТА С АЛИАСАМИ И ТРАНСФОРМАЦИЯМИ
# ============================================================

def normalize_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip() if value else ""

def transliterate_russian(value: str) -> str:
    return normalize_unicode(value).casefold().translate(RUSSIAN_TRANSLITERATION_TABLE)

def generate_alias_candidates(channel: ChannelInput, epg_kb: Optional[EPGKnowledgeBase] = None) -> List[AliasCandidate]:
    name = channel.display_name
    epg_id = channel.tvg_id or name
    candidates: Dict[str, Tuple[str, str]] = {}

    def add_cand(val: str, rule: str) -> None:
        val_clean = re.sub(r"[^a-z0-9]+", "_", val.casefold()).strip("_")
        if val_clean and val_clean not in candidates:
            candidates[val_clean] = (val_clean, rule)

    if epg_kb:
        for cand in epg_kb.get_candidates(name, channel.tvg_id):
            add_cand(cand, "epg_xml_2016")

    display_norm = name.strip().casefold()
    mapped_name = CHANNEL_NAME_ALIASES.get(display_norm)
    dict_matches = set(KNOWN_ALIAS_DICTIONARY.get(name, set()))
    if mapped_name:
        dict_matches.update(KNOWN_ALIAS_DICTIONARY.get(mapped_name, set()))

    for alias in dict_matches:
        add_cand(alias, "known_dictionary")

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

    ml_model = EnsembleModel.load()
    cand_dicts = [{"candidate": c[0], "rule": c[1]} for c in candidates.values()]

    if ml_model and ml_model.trained:
        scores = ml_model.predict_probability(cand_dicts)
    else:
        scores = [0.5 for _ in cand_dicts]

    result = []
    for (val, rule), score in zip(candidates.values(), scores):
        result.append(AliasCandidate(value=val, reason=rule, score=score))

    return sorted(result, key=lambda x: -x.score)

# ============================================================
# M3U8 И МУЛЬТИ-НОДОВЫЙ СКАНИРОВЩИК
# ============================================================

class MultiNodeScanner:
    def __init__(self, db: Database, epg_kb: Optional[EPGKnowledgeBase] = None):
        self.db = db
        self.epg_kb = epg_kb
        self.active_nodes: List[str] = []

    def ping_nodes(self) -> List[str]:
        LOGGER.info("Опрос доступности узлов Ngenix CDN...")
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
        candidates = generate_alias_candidates(channel, self.epg_kb)
        selected_candidates = candidates[:TOP_RESULTS]
        nodes = self.active_nodes if self.active_nodes else ["s70378"]

        for cand in selected_candidates:
            for node in nodes:
                for pattern in DEFAULT_PATTERNS:
                    relative_path = pattern.format(v=cand.value)
                    stream_url = f"https://{node}.cdn.ngenix.net/{relative_path}"

                    is_valid, http_status, ping_ms = self.verify_hls_stream(stream_url)
                    self.db.save_attempt(cand.value, cand.reason, pattern, node, is_valid, http_status, ping_ms)

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
                            match_type="CONFIRMED_ML_HYBRID",
                            confidence=cand.score,
                            reason=f"HLS поток валидирован на {node} (Правило: {cand.reason})",
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
# PARSING & EXPORT
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

    for extra in EXTRA_CHANNELS:
        channels.append(ChannelInput(display_name=extra["name"], tvg_id=extra["id"], logo=extra["logo"]))

    LOGGER.info("Всего сформировано каналов для сканирования: %d", len(channels))
    return channels

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

    with open(txt_file, "w", encoding="utf-8") as f:
        f.write("=== ALIAS ENGINE V5.5 (ML-HYBRID) REPORT ===\n\n")
        for res in results:
            if res.cdn_alias and res.streams:
                s = res.streams[0]
                f.write(f"[КАНАЛ] {res.channel_name}\n")
                f.write(f"  [ALIAS] {res.cdn_alias}\n")
                f.write(f"  [NODE] {s.node}\n")
                f.write(f"  [RULE] {s.rule_name}\n")
                f.write(f"  [URL] {s.url}\n")
                f.write("-" * 50 + "\n")

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

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)

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
# TRAINER & CLI ENTRYPOINT
# ============================================================

def retrain_ml_model() -> None:
    if not HAS_ML:
        LOGGER.warning("Scikit-learn не найден. Обучение пропущено.")
        return

    db = Database()
    rows = db.get_training_data()
    LOGGER.info("Собрано %d наблюдений из БД для ML-обучения.", len(rows))

    if len(rows) < MIN_TRAINING_SAMPLES:
        LOGGER.info("Накоплено недостаточно попыток для ML (%d/%d).", len(rows), MIN_TRAINING_SAMPLES)
        return

    model = EnsembleModel()
    try:
        metrics = model.fit(rows)
        model.save(MODEL_FILE)
        LOGGER.info("=== МЕТРИКИ ML-АНСАМБЛЯ (v5.5) ===")
        for k, v in metrics.items():
            LOGGER.info(f"{k:10}: {v:.4f}")
    except ValueError as e:
        LOGGER.warning("Обучение пока невозможно: %s", e)

def run_pipeline() -> None:
    db = Database()
    epg_kb = EPGKnowledgeBase()
    epg_kb.load()

    # Загружаем все исторические и локальные JSON-записи через универсальный загрузчик
    all_json_records = load_all_json_records()
    if all_json_records:
        LOGGER.info("[JSON] Успешно обработано записей из единого пула: %d", len(all_json_records))

    scanner = MultiNodeScanner(db, epg_kb)
    scanner.ping_nodes()

    channels = fetch_epg_channels()
    results: List[AliasMatch] = []
    LOGGER.info("Старт параллельного ML-сканирования (v5.5)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS) as executor:
        future_to_ch = {executor.submit(scanner.probe_channel, ch): ch for ch in channels}
        for future in concurrent.futures.as_completed(future_to_ch):
            res = future.result()
            results.append(res)
            if res.cdn_alias:
                LOGGER.info("[+] Найден: %s -> %s (Правило: %s)", res.channel_name, res.streams[0].url, res.streams[0].rule_name)

    LOGGER.info("Запуск автообучения ML-модели по свежим результатам...")
    retrain_ml_model()

    LOGGER.info("Экспорт всех видов отчетов...")
    save_all_reports(channels, results)

def show_stats() -> None:
    db = Database()
    stats = db.get_statistics()
    print("\n=== СТАТИСТИКА БАЗЫ ДАННЫХ (v5.5) ===")
    print(f"Всего проверок: {stats['total']}")
    print(f"Успешных:       {stats['successful']}")
    print(f"Неуспешных:     {stats['failed']}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Alias Verification Engine v5.5 (ngSKALA ML-Hybrid)")
    parser.add_argument("--scan", action="store_true", help="Запустить полное сканирование и сформировать отчёты")
    parser.add_argument("--train", action="store_true", help="Переобучить ML-модель по накопленным данным")
    parser.add_argument("--stats", action="store_true", help="Показать статистику базы данных")

    args = parser.parse_args()

    if args.scan:
        run_pipeline()
    elif args.train:
        retrain_ml_model()
    elif args.stats:
        show_stats()
    else:
        run_pipeline()

if __name__ == "__main__":
    main()
