#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Alias Verification Engine
6.1.7602.2901626_AI_build_6.1.760229.0162631
ngSKALA ML-Hybrid Evolution Edition

Основные возможности:

1. Мульти-нодовый CDN Ngenix scanner.
2. EPG 2016 Knowledge Layer.
3. Универсальная загрузка исторических JSON.
4. Накопительная SQLite Knowledge Base.
5. Самообучение на ВСЕЙ накопленной истории.
6. Усиленный ML-ансамбль:
   - RandomForest
   - ExtraTrees
   - HistGradientBoosting
   - второй RandomForest с независимыми параметрами
7. Расширенный набор детерминированных признаков.
8. Стабильные признаки без Python hash().
9. Версионирование каждого запуска:
   playlist_N.m3u
   Ai_Alias_N.txt
   Ai_Alias_ngnorm_N.txt
   Ai_Alias_export_N.json
   ngSKALA_learned_report_N.txt
   data/model_N.joblib
10. model_latest.joblib всегда указывает на последнюю обученную модель.
11. Предыдущие версии НЕ удаляются.
12. NGG_RUN_NUMBER передаётся из GitHub Actions.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
from typing import Dict, List, Optional, Set, Tuple
import unicodedata

from urllib.error import HTTPError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import joblib
import numpy as np

try:
    from sklearn.ensemble import (
        ExtraTreesClassifier,
        HistGradientBoostingClassifier,
        RandomForestClassifier,
    )
    from sklearn.metrics import (
        accuracy_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )
    from sklearn.model_selection import train_test_split

    HAS_ML = True

except ImportError:
    HAS_ML = False


# ============================================================
# CONFIGURATION
# ============================================================

EPG_URL = "http://epg.one/epg2.xml.gz"

EPG_2016_KNOWLEDGE_URL = (
    "https://raw.githubusercontent.com/"
    "Phoenix89S/IpTV_playlist_2026Ru/main/"
    "xml_2016_knowledge.json"
)

LOCAL_EPG_2016_CACHE = "xml_2016_knowledge.json"

DB_FILE_PATH = "knowledge.db"

DATA_DIR = Path("data")

MODEL_FILE = DATA_DIR / "model_latest.joblib"

MACHINE_SOURCE = (
    "ALIAS_MODULE_"
    "6.1.7602.2901626_AI_build_6.1.760229.0162631"
)

ENGINE_VERSION = (
    "6.1.7602.2901626_AI_build_6.1.760229.0162631"
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_REQUEST_TIMEOUT = 3

MAX_WORKER_THREADS = 20

TOP_RESULTS = 25

MIN_TRAINING_SAMPLES = 30

RANDOM_STATE = 42


# ============================================================
# RUN VERSION
# ============================================================

def get_run_number() -> int:
    """
    Получает номер запуска из GitHub Actions.

    YAML должен установить:

        NGG_RUN_NUMBER=<N>

    Если запуск производится вручную локально,
    используется следующий номер на основе существующих файлов.
    """

    env_value = os.environ.get("NGG_RUN_NUMBER", "").strip()

    if env_value.isdigit() and int(env_value) > 0:
        return int(env_value)

    numbers = []

    patterns = [
        "playlist_*.m3u",
        "Ai_Alias_*.txt",
        "Ai_Alias_export_*.json",
        "Ai_Alias_ngnorm_*.txt",
    ]

    for pattern in patterns:
        for path in Path(".").glob(pattern):
            match = re.search(r"_(\d+)\.[^.]+$", path.name)

            if match:
                numbers.append(int(match.group(1)))

    return max(numbers, default=0) + 1


RUN_NUMBER = get_run_number()


# ============================================================
# VERSIONED FILE NAMES
# ============================================================

def versioned_name(base: str, run_number: int = RUN_NUMBER) -> str:
    """
    Формирует:

        Ai_Alias_1.txt
        Ai_Alias_2.txt
        ...

    Первый запуск также получает номер.
    """

    path = Path(base)

    return str(
        path.with_name(
            f"{path.stem}_{run_number}{path.suffix}"
        )
    )


HUMAN_REPORT_FILE = versioned_name("Ai_Alias.txt")

MACHINE_REPORT_FILE = versioned_name("Ai_Alias_ngnorm.txt")

JSON_EXPORT_FILE = versioned_name("Ai_Alias_export.json")

PLAYLIST_FILE = versioned_name("playlist.m3u")

LEARNED_REPORT_FILE = versioned_name(
    "ngSKALA_learned_report.txt"
)

VERSIONED_MODEL_FILE = (
    DATA_DIR / f"model_{RUN_NUMBER}.joblib"
)


# ============================================================
# SSL
# ============================================================

SSL_CONTEXT = ssl.create_default_context()

SSL_CONTEXT.check_hostname = False

SSL_CONTEXT.verify_mode = ssl.CERT_NONE


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] "
           "%(name)s: %(message)s",
)

LOGGER = logging.getLogger(
    "AliasEngine_6.1.7602.2901626"
)


# ============================================================
# NGENIX
# ============================================================

NGENIX_NODES = [
    f"s703{i}"
    for i in range(78, 91)
]


DEFAULT_PATTERNS = [
    "{v}/index.m3u8",
    "{v}/mono.m3u8",
    "{v}/live.m3u8",
    "hls/{v}/variant.m3u8",
    "{v}/tracks-v1a1/mono.m3u8",
    "{v}/1/index.m3u8",
    "hls/CH_{v}/variant.m3u8",
]


# ============================================================
# TRANSLITERATION
# ============================================================

RUSSIAN_TRANSLITERATION_TABLE = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "yo",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "c",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def normalize_unicode(value: str) -> str:
    return (
        unicodedata.normalize(
            "NFKC",
            value
        ).strip()
        if value
        else ""
    )


def transliterate_russian(value: str) -> str:
    return (
        normalize_unicode(value)
        .casefold()
        .translate(
            RUSSIAN_TRANSLITERATION_TABLE
        )
    )


# ============================================================
# CHANNEL KNOWLEDGE
# ============================================================

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
    {
        "id": "viju_tv1000_romantica",
        "name": "viju TV1000 romantica",
        "logo": "",
    },
    {
        "id": "viju_tv1000_novella",
        "name": "viju TV1000 новелла",
        "logo": "",
    },
    {
        "id": "viju_tv1000_action",
        "name": "viju TV1000 action",
        "logo": "",
    },
    {
        "id": "viju_tv1000_russkoe",
        "name": "viju TV1000 русское",
        "logo": "",
    },
    {"id": "hit", "name": "ХИТ", "logo": ""},
    {"id": "kinokomediya", "name": "Кинокомедия", "logo": ""},
    {"id": "cinema", "name": "CINEMA", "logo": ""},
    {
        "id": "mosfilm_gold",
        "name": "Мосфильм. Золотая коллекция",
        "logo": "",
    },
    {
        "id": "fantastic_channel",
        "name": "Fantastic Channel",
        "logo": "",
    },
    {"id": "boevik", "name": "Боевик", "logo": ""},
    {"id": "kinomix", "name": "Киномикс", "logo": ""},
    {"id": "detektiv", "name": "Детектив", "logo": ""},
    {"id": "rodnoe_kino", "name": "Родное кино", "logo": ""},
    {"id": "patriot", "name": "Патриот", "logo": ""},
    {"id": "rtg_hd", "name": "RTG HD", "logo": ""},
    {"id": "rtg_int", "name": "RTG Int", "logo": ""},
    {
        "id": "nat_geo_ru",
        "name": "National Geographic RU",
        "logo": "",
    },
    {
        "id": "nat_geo_wild",
        "name": "NAT GEO WILD",
        "logo": "",
    },
    {"id": "kinoujas", "name": "КИНОУЖАС", "logo": ""},
    {"id": "kinosemya", "name": "КИНОСЕМЬЯ", "logo": ""},
    {
        "id": "russkiy_roman",
        "name": "Русский роман",
        "logo": "",
    },
    {
        "id": "russkiy_detektiv",
        "name": "Русский детектив",
        "logo": "",
    },
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
    {
        "id": "match_strana",
        "name": "Матч! Страна",
        "logo": "",
    },
    {"id": "2x2", "name": "2x2", "logo": ""},
    {"id": "subbota", "name": "Суббота!", "logo": ""},
    {
        "id": "ntv_style",
        "name": "НТВ Стиль",
        "logo": "",
    },
    {
        "id": "ntv_pravo",
        "name": "НТВ Право",
        "logo": "",
    },
    {
        "id": "ntv_serial",
        "name": "НТВ Сериал",
        "logo": "",
    },
    {"id": "ntv_hit", "name": "НТВ Хит", "logo": ""},
    {
        "id": "unknown_russia",
        "name": "Неизвестная Россия",
        "logo": "",
    },
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
    candidates: List[AliasCandidate] = field(
        default_factory=list
    )
    streams: List[CDNStream] = field(
        default_factory=list
    )


# ============================================================
# JSON LOADING
# ============================================================

REMOTE_JSON_SOURCES = [
    "https://raw.githubusercontent.com/"
    "Phoenix89S/IpTV_playlist_2026Ru/main/"
    "Ai_Alias_export.json",

    "https://raw.githubusercontent.com/"
    "Phoenix89S/IpTV_playlist_2026Ru/main/"
    "Ai_Alias_export_1.json",

    "https://raw.githubusercontent.com/"
    "Phoenix89S/IpTV_playlist_2026Ru/main/"
    "Ai_Alias_export_2.json",
]


def load_json_source(source):

    try:

        if str(source).startswith(
            ("http://", "https://")
        ):

            req = Request(
                source,
                headers={
                    "User-Agent":
                    DEFAULT_USER_AGENT
                },
            )

            with urlopen(
                req,
                timeout=DEFAULT_REQUEST_TIMEOUT,
                context=SSL_CONTEXT,
            ) as response:

                status_code = getattr(
                    response,
                    "status",
                    200,
                )

                if status_code != 200:
                    print(
                        f"[JSON] HTTP "
                        f"{status_code}: "
                        f"{source}"
                    )
                    return None

                return json.loads(
                    response.read().decode(
                        "utf-8"
                    )
                )

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
            encoding="utf-8",
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[JSON] Ошибка загрузки "
            f"{source}: {e}"
        )

        return None


def iter_json_records(data):

    if isinstance(data, list):

        for item in data:

            if isinstance(item, dict):
                yield item

        return

    if isinstance(data, dict):

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

        yield data


def find_all_json_sources():

    sources = []

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

    for url in REMOTE_JSON_SOURCES:

        if url not in sources:
            sources.append(url)

    return sources


def load_all_json_records():

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


# ============================================================
# STABLE ML FEATURES
# ============================================================

def stable_rule_value(rule: str) -> float:
    """
    Стабильное числовое представление правила.

    ВАЖНО:
    Python hash() намеренно рандомизируется между
    процессами. Поэтому старый вариант:

        hash(rule)

    был плохим ML-признаком.
    """

    total = 0

    for index, char in enumerate(
        rule.casefold()
    ):
        total += (
            (index + 1)
            * ord(char)
        )

    return float(
        total % 10000
    )


def make_features(
    candidate: str,
    rule: str,
) -> List[float]:

    val = normalize_unicode(
        str(candidate)
    )

    lower = val.casefold()

    translit = transliterate_russian(
        lower
    )

    alpha = sum(
        c.isalpha()
        for c in val
    )

    digits = sum(
        c.isdigit()
        for c in val
    )

    unique = len(
        set(lower)
    )

    vowels = len(
        re.findall(
            r"[aeiouyаеиоуыэюя]",
            lower,
        )
    )

    consonants = len(
        re.findall(
            r"[bcdfghjklmnpqrstvwxyz"
            r"бвгджзйклмнпрстфхцчшщ]",
            lower,
        )
    )

    separators = (
        val.count("_")
        + val.count("-")
        + val.count(".")
        + val.count(" ")
    )

    return [
        float(len(val)),
        float(alpha),
        float(digits),
        float(unique),

        float(val.count("_")),
        float(val.count("-")),
        float(val.count(".")),
        float(val.count(" ")),
        float(separators),

        float("hd" in lower),
        float("uhd" in lower),
        float("fhd" in lower),
        float("tv" in lower),
        float("plus" in lower),
        float("premium" in lower),
        float("live" in lower),
        float("channel" in lower),

        float(bool(re.search(r"\d", val))),
        float(bool(re.search(r"^\d", val))),
        float(bool(re.search(r"\d$", val))),

        float(
            len(
                re.findall(
                    r"[aeiouy]",
                    lower,
                )
            )
        ),

        float(
            len(
                re.findall(
                    r"[aeiouyаеиоуыэюя]",
                    lower,
                )
            )
        ),

        float(consonants),

        float(
            translit == lower
        ),

        float(
            len(translit)
        ),

        float("viju" in lower),
        float("ntv" in lower),
        float("tnt" in lower),
        float("ren" in lower),
        float("mir" in lower),
        float("match" in lower),

        float(
            sum(
                c.isupper()
                for c in val
            )
        ),

        float(
            digits / max(len(val), 1)
        ),

        float(
            unique / max(len(val), 1)
        ),

        stable_rule_value(rule),
    ]


def build_matrix(
    rows: List[Dict],
) -> np.ndarray:

    return np.asarray(
        [
            make_features(
                row["candidate"],
                row["rule"],
            )
            for row in rows
        ],
        dtype=np.float64,
    )


# ============================================================
# POWERFUL ML ENSEMBLE
# ============================================================

class EnsembleModel:

    VERSION = ENGINE_VERSION

    def __init__(self):

        self.trained = False

        self.metrics = {}

        self.training_samples = 0

        self.created_at = None

        if not HAS_ML:
            return

        # ----------------------------------------------------
        # MODEL 1
        # ----------------------------------------------------

        self.random_forest = RandomForestClassifier(
            n_estimators=700,
            max_depth=20,
            min_samples_leaf=1,
            max_features="sqrt",
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        )

        # ----------------------------------------------------
        # MODEL 2
        # ----------------------------------------------------

        self.extra_trees = ExtraTreesClassifier(
            n_estimators=700,
            max_depth=24,
            min_samples_leaf=1,
            max_features="sqrt",
            class_weight="balanced",
            random_state=123,
            n_jobs=-1,
        )

        # ----------------------------------------------------
        # MODEL 3
        # ----------------------------------------------------

        self.gradient_boosting = (
            HistGradientBoostingClassifier(
                max_iter=500,
                learning_rate=0.035,
                max_leaf_nodes=31,
                min_samples_leaf=10,
                l2_regularization=1.0,
                random_state=777,
            )
        )

        # ----------------------------------------------------
        # MODEL 4
        # ----------------------------------------------------

        self.random_forest_deep = (
            RandomForestClassifier(
                n_estimators=500,
                max_depth=32,
                min_samples_leaf=2,
                max_features=None,
                class_weight="balanced",
                random_state=999,
                n_jobs=-1,
            )
        )

    def fit(
        self,
        rows: List[Dict],
    ) -> Dict[str, float]:

        if not HAS_ML:
            raise ValueError(
                "Scikit-learn не установлен."
            )

        if len(rows) < MIN_TRAINING_SAMPLES:
            raise ValueError(
                f"Недостаточно данных "
                f"({len(rows)}/"
                f"{MIN_TRAINING_SAMPLES})."
            )

        X = build_matrix(rows)

        y = np.asarray(
            [
                int(row["success"])
                for row in rows
            ],
            dtype=np.int8,
        )

        classes = set(
            y.tolist()
        )

        if len(classes) < 2:

            raise ValueError(
                "Требуются данные обоих "
                "классов (SUCCESS и FAIL)."
            )

        X_train, X_test, y_train, y_test = (
            train_test_split(
                X,
                y,
                test_size=0.25,
                random_state=RANDOM_STATE,
                stratify=y,
            )
        )

        LOGGER.info(
            "ML train: %d samples",
            len(X_train),
        )

        LOGGER.info(
            "ML test: %d samples",
            len(X_test),
        )

        # ----------------------------------------------------
        # TRAIN FOUR MODELS
        # ----------------------------------------------------

        self.random_forest.fit(
            X_train,
            y_train,
        )

        self.extra_trees.fit(
            X_train,
            y_train,
        )

        self.gradient_boosting.fit(
            X_train,
            y_train,
        )

        self.random_forest_deep.fit(
            X_train,
            y_train,
        )

        # ----------------------------------------------------
        # PROBABILITIES
        # ----------------------------------------------------

        rf_prob = (
            self.random_forest
            .predict_proba(X_test)[:, 1]
        )

        et_prob = (
            self.extra_trees
            .predict_proba(X_test)[:, 1]
        )

        gb_prob = (
            self.gradient_boosting
            .predict_proba(X_test)[:, 1]
        )

        deep_prob = (
            self.random_forest_deep
            .predict_proba(X_test)[:, 1]
        )

        # ----------------------------------------------------
        # ENSEMBLE
        # ----------------------------------------------------

        probability = (
            rf_prob * 0.30
            + et_prob * 0.30
            + gb_prob * 0.25
            + deep_prob * 0.15
        )

        prediction = (
            probability >= 0.5
        ).astype(int)

        metrics = {
            "accuracy": float(
                accuracy_score(
                    y_test,
                    prediction,
                )
            ),
            "precision": float(
                precision_score(
                    y_test,
                    prediction,
                    zero_division=0,
                )
            ),
            "recall": float(
                recall_score(
                    y_test,
                    prediction,
                    zero_division=0,
                )
            ),
        }

        try:

            metrics["roc_auc"] = float(
                roc_auc_score(
                    y_test,
                    probability,
                )
            )

        except ValueError:

            metrics["roc_auc"] = 0.0

        self.trained = True

        self.metrics = metrics

        self.training_samples = len(
            rows
        )

        self.created_at = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        return metrics

    def predict_probability(
        self,
        candidates: List[Dict],
    ) -> List[float]:

        if (
            not HAS_ML
            or not self.trained
            or not candidates
        ):
            return [
                0.5
                for _ in candidates
            ]

        X = build_matrix(
            candidates
        )

        rf_prob = (
            self.random_forest
            .predict_proba(X)[:, 1]
        )

        et_prob = (
            self.extra_trees
            .predict_proba(X)[:, 1]
        )

        gb_prob = (
            self.gradient_boosting
            .predict_proba(X)[:, 1]
        )

        deep_prob = (
            self.random_forest_deep
            .predict_proba(X)[:, 1]
        )

        probability = (
            rf_prob * 0.30
            + et_prob * 0.30
            + gb_prob * 0.25
            + deep_prob * 0.15
        )

        return probability.tolist()

    def save(
        self,
        path: Path,
    ) -> None:

        path = Path(path)

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        joblib.dump(
            self,
            path,
            compress=3,
        )

    @staticmethod
    def load(
        path: Path = MODEL_FILE,
    ) -> Optional["EnsembleModel"]:

        path = Path(path)

        if not path.exists():
            return None

        try:

            model = joblib.load(
                path
            )

            if isinstance(
                model,
                EnsembleModel,
            ):
                return model

        except Exception as e:

            LOGGER.warning(
                "Не удалось загрузить ML "
                "модель %s: %s",
                path,
                e,
            )

        return None


# ============================================================
# DATABASE
# ============================================================

class Database:

    def __init__(
        self,
        db_path: str = DB_FILE_PATH,
    ):

        self.db_path = db_path

        self._init_db()

    def _get_connection(
        self,
    ) -> sqlite3.Connection:

        conn = sqlite3.connect(
            self.db_path
        )

        conn.row_factory = (
            sqlite3.Row
        )

        return conn

    def _init_db(self):

        with self._get_connection() as conn:

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    pattern TEXT,
                    node TEXT,
                    success INTEGER NOT NULL,
                    status_code INTEGER,
                    response_time REAL,
                    run_number INTEGER DEFAULT 0,
                    created_at DATETIME
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learned_aliases (
                    channel_name TEXT PRIMARY KEY,
                    cdn_alias TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    hit_count INTEGER DEFAULT 1,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_att_cand
                ON attempts(candidate)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_att_rule
                ON attempts(rule)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_att_run
                ON attempts(run_number)
                """
            )

            # ------------------------------------------------
            # Миграция старой БД
            # ------------------------------------------------

            columns = [
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(attempts)"
                ).fetchall()
            ]

            if "run_number" not in columns:

                conn.execute(
                    """
                    ALTER TABLE attempts
                    ADD COLUMN run_number INTEGER DEFAULT 0
                    """
                )

            conn.commit()

    def save_attempt(
        self,
        candidate: str,
        rule: str,
        pattern: str,
        node: str,
        success: bool,
        status_code: Optional[int],
        response_time: float,
        run_number: int = RUN_NUMBER,
    ) -> None:

        with self._get_connection() as conn:

            conn.execute(
                """
                INSERT INTO attempts (
                    candidate,
                    rule,
                    pattern,
                    node,
                    success,
                    status_code,
                    response_time,
                    run_number,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate,
                    rule,
                    pattern,
                    node,
                    1 if success else 0,
                    status_code,
                    response_time,
                    run_number,
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
                ),
            )

            conn.commit()

    def get_training_data(
        self,
    ) -> List[Dict]:

        with self._get_connection() as conn:

            rows = conn.execute(
                """
                SELECT
                    candidate,
                    rule,
                    success,
                    status_code,
                    response_time,
                    run_number
                FROM attempts
                ORDER BY id
                """
            ).fetchall()

            return [
                dict(row)
                for row in rows
            ]

    def record_learned_alias(
        self,
        channel_name: str,
        cdn_alias: str,
    ) -> None:

        with self._get_connection() as conn:

            conn.execute(
                """
                INSERT INTO learned_aliases (
                    channel_name,
                    cdn_alias,
                    confidence,
                    hit_count
                )
                VALUES (?, ?, 1.0, 1)

                ON CONFLICT(channel_name)
                DO UPDATE SET
                    cdn_alias = excluded.cdn_alias,
                    hit_count =
                        hit_count + 1,
                    last_updated =
                        CURRENT_TIMESTAMP
                """,
                (
                    channel_name,
                    cdn_alias,
                ),
            )

            conn.commit()

    def get_statistics(
        self,
    ) -> Dict[str, int]:

        with self._get_connection() as conn:

            total = conn.execute(
                "SELECT COUNT(*) FROM attempts"
            ).fetchone()[0]

            successful = conn.execute(
                """
                SELECT COUNT(*)
                FROM attempts
                WHERE success = 1
                """
            ).fetchone()[0]

            runs = conn.execute(
                """
                SELECT COUNT(
                    DISTINCT run_number
                )
                FROM attempts
                """
            ).fetchone()[0]

            return {
                "total": total,
                "successful": successful,
                "failed": total - successful,
                "runs": runs,
            }


# ============================================================
# EPG KNOWLEDGE BASE
# ============================================================

class EPGKnowledgeBase:

    def __init__(
        self,
        url: str = EPG_2016_KNOWLEDGE_URL,
        cache_path: str = LOCAL_EPG_2016_CACHE,
    ):

        self.url = url

        self.cache_path = Path(
            cache_path
        )

        self.name_to_candidates: Dict[
            str,
            Set[str],
        ] = {}

    def load(self):

        data = None

        req = Request(
            self.url,
            headers={
                "User-Agent":
                DEFAULT_USER_AGENT
            },
        )

        try:

            with urlopen(
                req,
                timeout=10,
                context=SSL_CONTEXT,
            ) as resp:

                raw_bytes = (
                    resp.read()
                )

                data = json.loads(
                    raw_bytes.decode(
                        "utf-8"
                    )
                )

                self.cache_path.write_bytes(
                    raw_bytes
                )

                LOGGER.info(
                    "База xml_2016_knowledge.json "
                    "обновлена из GitHub Raw."
                )

        except Exception as e:

            LOGGER.warning(
                "Не удалось скачать "
                "xml_2016_knowledge.json "
                "(%s). Пробуем кэш...",
                e,
            )

        if (
            not data
            and self.cache_path.exists()
        ):

            try:

                data = json.loads(
                    self.cache_path.read_text(
                        encoding="utf-8"
                    )
                )

                LOGGER.info(
                    "База xml_2016_knowledge.json "
                    "загружена из локального файла."
                )

            except Exception as e:

                LOGGER.error(
                    "Ошибка чтения локального "
                    "EPG 2016: %s",
                    e,
                )

        if data and "items" in data:

            self._index(
                data["items"]
            )

    def _index(
        self,
        items: List[dict],
    ):

        for item in items:

            ru_names = item.get(
                "ru_names",
                [],
            )

            en_names = item.get(
                "en_names",
                [],
            )

            channel_id = item.get(
                "channel_id",
                "",
            )

            candidates = set(
                item.get(
                    "cdn_candidates",
                    [],
                )
            )

            if not candidates:
                continue

            all_names = set(
                ru_names + en_names
            )

            if channel_id:
                all_names.add(
                    channel_id
                )

            for name in all_names:

                norm_key = (
                    self._normalize(name)
                )

                if norm_key:

                    if (
                        norm_key
                        not in
                        self.name_to_candidates
                    ):

                        self.name_to_candidates[
                            norm_key
                        ] = set()

                    self.name_to_candidates[
                        norm_key
                    ].update(
                        candidates
                    )

        LOGGER.info(
            "Индексация EPG 2016 "
            "завершена. "
            "Индексировано названий: %d",
            len(
                self.name_to_candidates
            ),
        )

    @staticmethod
    def _normalize(
        s: str,
    ) -> str:

        s = s.lower().strip()

        s = re.sub(
            r"\(.*?\)",
            "",
            s,
        )

        s = re.sub(
            r"[^a-zа-я0-9]",
            "",
            s,
        )

        return s

    def get_candidates(
        self,
        channel_name: str,
        tvg_id: str = "",
    ) -> List[str]:

        results = set()

        for key in (
            channel_name,
            tvg_id,
        ):

            if key:

                norm = self._normalize(
                    key
                )

                if (
                    norm
                    in self.name_to_candidates
                ):

                    results.update(
                        self.name_to_candidates[
                            norm
                        ]
                    )

        return list(results)


# ============================================================
# ALIAS GENERATION
# ============================================================

def generate_alias_candidates(
    channel: ChannelInput,
    epg_kb: Optional[
        EPGKnowledgeBase
    ] = None,
    ml_model: Optional[
        EnsembleModel
    ] = None,
) -> List[AliasCandidate]:

    name = channel.display_name

    epg_id = (
        channel.tvg_id
        or name
    )

    candidates: Dict[
        str,
        Tuple[str, str],
    ] = {}

    def add_cand(
        val: str,
        rule: str,
    ):

        val_clean = re.sub(
            r"[^a-z0-9]+",
            "_",
            str(val).casefold(),
        ).strip("_")

        if (
            val_clean
            and val_clean
            not in candidates
        ):

            candidates[
                val_clean
            ] = (
                val_clean,
                rule,
            )

    # --------------------------------------------------------
    # EPG
    # --------------------------------------------------------

    if epg_kb:

        for cand in epg_kb.get_candidates(
            name,
            channel.tvg_id,
        ):

            add_cand(
                cand,
                "epg_xml_2016",
            )

    # --------------------------------------------------------
    # DICTIONARY
    # --------------------------------------------------------

    display_norm = (
        name.strip().casefold()
    )

    mapped_name = (
        CHANNEL_NAME_ALIASES.get(
            display_norm
        )
    )

    dict_matches = set(
        KNOWN_ALIAS_DICTIONARY.get(
            name,
            set(),
        )
    )

    if mapped_name:

        dict_matches.update(
            KNOWN_ALIAS_DICTIONARY.get(
                mapped_name,
                set(),
            )
        )

    for alias in dict_matches:

        add_cand(
            alias,
            "known_dictionary",
        )

    # --------------------------------------------------------
    # GENERATED VARIANTS
    # --------------------------------------------------------

    name_lower = (
        name.lower().strip()
    )

    clean_id = (
        epg_id
        .lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )

    translit_name = (
        transliterate_russian(
            name_lower
        )
    )

    add_cand(
        epg_id,
        "exact_id",
    )

    add_cand(
        clean_id,
        "clean_id",
    )

    add_cand(
        name_lower.replace(
            " ",
            "_",
        ),
        "underscore",
    )

    add_cand(
        name_lower.replace(
            " ",
            "",
        ),
        "no_spaces",
    )

    add_cand(
        translit_name.replace(
            " ",
            "_",
        ),
        "translit_underscore",
    )

    add_cand(
        translit_name.replace(
            " ",
            "",
        ),
        "translit_nospaces",
    )

    if "hd" in name_lower:

        add_cand(
            name_lower
            .replace(
                "hd",
                "",
            )
            .replace(
                " ",
                "",
            ),
            "strip_hd",
        )

    if "viju" in name_lower:

        core = transliterate_russian(
            name_lower
            .replace(
                "viju",
                "",
            )
            .replace(
                "+",
                "",
            )
            .strip()
        )

        add_cand(
            f"vip_{core}",
            "viju_prefix",
        )

    if mapped_name:

        add_cand(
            transliterate_russian(
                mapped_name
            ),
            "mapped_name",
        )

    # --------------------------------------------------------
    # ML RANKING
    # --------------------------------------------------------

    cand_dicts = [
        {
            "candidate": value,
            "rule": rule,
        }
        for value, rule
        in candidates.values()
    ]

    if (
        ml_model
        and ml_model.trained
    ):

        scores = (
            ml_model.predict_probability(
                cand_dicts
            )
        )

    else:

        scores = [
            0.5
            for _ in cand_dicts
        ]

    result = []

    for (
        (value, rule),
        score,
    ) in zip(
        candidates.values(),
        scores,
    ):

        result.append(
            AliasCandidate(
                value=value,
                reason=rule,
                score=float(score),
            )
        )

    return sorted(
        result,
        key=lambda x: -x.score,
    )


# ============================================================
# CDN SCANNER
# ============================================================

class MultiNodeScanner:

    def __init__(
        self,
        db: Database,
        epg_kb: Optional[
            EPGKnowledgeBase
        ] = None,
        ml_model: Optional[
            EnsembleModel
        ] = None,
    ):

        self.db = db

        self.epg_kb = epg_kb

        self.ml_model = ml_model

        self.active_nodes: List[
            str
        ] = []

    def ping_nodes(self):

        LOGGER.info(
            "Опрос доступности узлов "
            "Ngenix CDN..."
        )

        valid_nodes = []

        def check_node(
            node: str,
        ) -> Optional[str]:

            url = (
                f"https://{node}.cdn.ngenix.net/"
            )

            req = Request(
                url,
                method="HEAD",
                headers={
                    "User-Agent":
                    DEFAULT_USER_AGENT
                },
            )

            try:

                with urlopen(
                    req,
                    timeout=DEFAULT_REQUEST_TIMEOUT,
                    context=SSL_CONTEXT,
                ) as response:

                    if (
                        getattr(
                            response,
                            "status",
                            200,
                        )
                        < 500
                    ):

                        return node

            except HTTPError as e:

                if e.code < 500:
                    return node

            except Exception:
                pass

            return None

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=15
        ) as executor:

            futures = [
                executor.submit(
                    check_node,
                    node,
                )
                for node in NGENIX_NODES
            ]

            for future in (
                concurrent.futures.as_completed(
                    futures
                )
            ):

                res = future.result()

                if res:
                    valid_nodes.append(
                        res
                    )

        self.active_nodes = (
            sorted(valid_nodes)
            if valid_nodes
            else ["s70378"]
        )

        LOGGER.info(
            "Откликнулись узлы Ngenix "
            "(%d/%d): %s",
            len(self.active_nodes),
            len(NGENIX_NODES),
            ", ".join(
                self.active_nodes
            ),
        )

        return self.active_nodes

    def verify_hls_stream(
        self,
        url: str,
    ) -> Tuple[
        bool,
        int,
        float,
    ]:

        start_time = time.time()

        req = Request(
            url,
            headers={
                "User-Agent":
                DEFAULT_USER_AGENT
            },
        )

        try:

            with urlopen(
                req,
                timeout=DEFAULT_REQUEST_TIMEOUT,
                context=SSL_CONTEXT,
            ) as response:

                status = getattr(
                    response,
                    "status",
                    200,
                )

                elapsed_ms = (
                    time.time()
                    - start_time
                ) * 1000.0

                if status == 200:

                    chunk = (
                        response.read(
                            256
                        )
                        .decode(
                            "utf-8",
                            errors="ignore",
                        )
                    )

                    if "#EXTM3U" in chunk:

                        return (
                            True,
                            200,
                            elapsed_ms,
                        )

                return (
                    False,
                    status,
                    elapsed_ms,
                )

        except HTTPError as e:

            return (
                False,
                e.code,
                (
                    time.time()
                    - start_time
                )
                * 1000.0,
            )

        except Exception:

            return (
                False,
                0,
                (
                    time.time()
                    - start_time
                )
                * 1000.0,
            )

    def probe_channel(
        self,
        channel: ChannelInput,
    ) -> AliasMatch:

        candidates = (
            generate_alias_candidates(
                channel,
                self.epg_kb,
                self.ml_model,
            )
        )

        selected_candidates = (
            candidates[:TOP_RESULTS]
        )

        nodes = (
            self.active_nodes
            if self.active_nodes
            else ["s70378"]
        )

        for cand in selected_candidates:

            for node in nodes:

                for pattern in DEFAULT_PATTERNS:

                    relative_path = (
                        pattern.format(
                            v=cand.value
                        )
                    )

                    stream_url = (
                        f"https://"
                        f"{node}.cdn.ngenix.net/"
                        f"{relative_path}"
                    )

                    (
                        is_valid,
                        http_status,
                        ping_ms,
                    ) = self.verify_hls_stream(
                        stream_url
                    )

                    self.db.save_attempt(
                        candidate=cand.value,
                        rule=cand.reason,
                        pattern=pattern,
                        node=node,
                        success=is_valid,
                        status_code=http_status,
                        response_time=ping_ms,
                        run_number=RUN_NUMBER,
                    )

                    if is_valid:

                        cand.confirmed = True

                        self.db.record_learned_alias(
                            channel.display_name,
                            cand.value,
                        )

                        stream = CDNStream(
                            alias=cand.value,
                            url=stream_url,
                            node=f"{node}.cdn.ngenix.net",
                            pattern=pattern,
                            rule_name=cand.reason,
                            http_status=http_status,
                            reachable=True,
                            response_time_ms=ping_ms,
                        )

                        return AliasMatch(
                            channel_name=channel.display_name,
                            normalized_name=cand.value,
                            cdn_alias=cand.value,
                            match_type=(
                                "CONFIRMED_ML_ENSEMBLE"
                            ),
                            confidence=cand.score,
                            reason=(
                                f"HLS поток "
                                f"валидирован на "
                                f"{node} "
                                f"(Правило: "
                                f"{cand.reason})"
                            ),
                            candidates=candidates,
                            streams=[stream],
                        )

        return AliasMatch(
            channel_name=channel.display_name,
            normalized_name=(
                channel.display_name.lower()
            ),
            cdn_alias=None,
            match_type="UNKNOWN",
            confidence=0.0,
            reason=(
                "Ни один кандидат/"
                "узел не прошел "
                "проверку HLS"
            ),
            candidates=candidates,
            streams=[],
        )


# ============================================================
# EPG
# ============================================================

def fetch_epg_channels() -> List[
    ChannelInput
]:

    LOGGER.info(
        "Загрузка и парсинг EPG "
        "с %s ...",
        EPG_URL,
    )

    req = Request(
        EPG_URL,
        headers={
            "User-Agent":
            DEFAULT_USER_AGENT
        },
    )

    channels = []

    try:

        with urlopen(
            req,
            timeout=15,
            context=SSL_CONTEXT,
        ) as resp:

            gz = gzip.GzipFile(
                fileobj=io.BytesIO(
                    resp.read()
                )
            )

            root = ET.fromstring(
                gz.read()
            )

            for ch in root.findall(
                "channel"
            ):

                cid = ch.get(
                    "id",
                    "",
                ).strip()

                disp = ch.find(
                    "display-name"
                )

                icon = ch.find(
                    "icon"
                )

                logo = (
                    icon.get(
                        "src",
                        "",
                    ).strip()
                    if icon is not None
                    else ""
                )

                name = (
                    disp.text.strip()
                    if (
                        disp is not None
                        and disp.text
                    )
                    else ""
                )

                if cid and name:

                    channels.append(
                        ChannelInput(
                            display_name=name,
                            tvg_id=cid,
                            logo=logo,
                        )
                    )

    except Exception as e:

        LOGGER.error(
            "Ошибка загрузки EPG: %s",
            e,
        )

    for extra in EXTRA_CHANNELS:

        channels.append(
            ChannelInput(
                display_name=extra["name"],
                tvg_id=extra["id"],
                logo=extra["logo"],
            )
        )

    LOGGER.info(
        "Всего сформировано каналов "
        "для сканирования: %d",
        len(channels),
    )

    return channels


# ============================================================
# REPORT EXPORT
# ============================================================

def save_all_reports(
    channels: List[ChannelInput],
    results: List[AliasMatch],
) -> None:

    # --------------------------------------------------------
    # HUMAN REPORT
    # --------------------------------------------------------

    with open(
        HUMAN_REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            "=== ALIAS ENGINE "
            "6.1.7602.2901626_AI_build_6.1.760229.0162631 "
            "ML-EVOLUTION REPORT ===\n"
        )

        f.write(
            f"RUN={RUN_NUMBER}\n"
        )

        f.write(
            f"GENERATED="
            f"{datetime.now(timezone.utc)"
            ".isoformat()}\n\n"
        )

        for res in results:

            if (
                res.cdn_alias
                and res.streams
            ):

                s = res.streams[0]

                f.write(
                    f"[КАНАЛ] "
                    f"{res.channel_name}\n"
                )

                f.write(
                    f"  [ALIAS] "
                    f"{res.cdn_alias}\n"
                )

                f.write(
                    f"  [CONFIDENCE] "
                    f"{res.confidence:.6f}\n"
                )

                f.write(
                    f"  [NODE] "
                    f"{s.node}\n"
                )

                f.write(
                    f"  [RULE] "
                    f"{s.rule_name}\n"
                )

                f.write(
                    f"  [URL] "
                    f"{s.url}\n"
                )

                f.write(
                    "-" * 60
                    + "\n"
                )

    # --------------------------------------------------------
    # MACHINE REPORT
    # --------------------------------------------------------

    found_time = datetime.now(
        timezone.utc
    ).isoformat()

    with open(
        MACHINE_REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            f"RUN={RUN_NUMBER}\n\n"
        )

        for res in results:

            s = (
                res.streams[0]
                if res.streams
                else None
            )

            f.write(
                f"NAME={res.channel_name}\n"
            )

            f.write(
                f"ALIAS="
                f"{res.cdn_alias or ''}\n"
            )

            f.write(
                f"CONFIDENCE="
                f"{res.confidence:.6f}\n"
            )

            f.write(
                f"URL="
                f"{s.url if s else ''}\n"
            )

            f.write(
                f"STATUS="
                f"{s.http_status if s else 'UNKNOWN'}\n"
            )

            f.write(
                f"SOURCE={MACHINE_SOURCE}\n"
            )

            f.write(
                f"RUN={RUN_NUMBER}\n"
            )

            f.write(
                f"FOUND={found_time}\n\n"
            )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    with open(
        JSON_EXPORT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            {
                "engine": (
                    "AliasEngine"
                    "6.1.7602.2901626_AI_build_"
                    "6.1.760229.0162631"
                ),
                "run_number": RUN_NUMBER,
                "generated_at": found_time,
                "results": [
                    asdict(r)
                    for r in results
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    # --------------------------------------------------------
    # M3U
    # --------------------------------------------------------

    with open(
        PLAYLIST_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            '#EXTM3U '
            'url-tvg="http://epg.one/epg2.xml.gz" '
            f' x-ngg-run="{RUN_NUMBER}"\n'
        )

        for idx, res in enumerate(
            results
        ):

            if (
                res.cdn_alias
                and res.streams
            ):

                ch = (
                    channels[idx]
                    if idx < len(channels)
                    else ChannelInput(
                        display_name=(
                            res.channel_name
                        )
                    )
                )

                logo_attr = (
                    f' tvg-logo="{ch.logo}"'
                    if ch.logo
                    else ""
                )

                f.write(
                    f'#EXTINF:-1 '
                    f'tvg-id="{ch.tvg_id}" '
                    f'tvg-name="{res.channel_name}"'
                    f'{logo_attr},'
                    f'{res.channel_name}\n'
                )

                f.write(
                    f"{res.streams[0].url}\n"
                )

    # --------------------------------------------------------
    # LEARNED REPORT
    # --------------------------------------------------------

    with open(
        LEARNED_REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            "=== ngSKALA LEARNING REPORT ===\n\n"
        )

        f.write(
            f"RUN={RUN_NUMBER}\n"
        )

        f.write(
            "ENGINE="
            "AliasEngine"
            "6.1.7602.2901626_AI_build_"
            "6.1.760229.0162631\n"
        )

        f.write(
            f"MODEL={VERSIONED_MODEL_FILE}\n\n"
        )

        successful = sum(
            1
            for r in results
            if r.cdn_alias
        )

        f.write(
            f"CHANNELS={len(results)}\n"
        )

        f.write(
            f"CONFIRMED={successful}\n"
        )

        f.write(
            f"UNKNOWN="
            f"{len(results) - successful}\n\n"
        )

        for res in results:

            if res.cdn_alias:

                f.write(
                    f"{res.channel_name}"
                    f" => "
                    f"{res.cdn_alias}"
                    f" | "
                    f"{res.confidence:.6f}\n"
                )

    LOGGER.info(
        "Сохранен текстовый отчет: %s",
        HUMAN_REPORT_FILE,
    )

    LOGGER.info(
        "Сохранен машинный отчет: %s",
        MACHINE_REPORT_FILE,
    )

    LOGGER.info(
        "Сохранен JSON экспорт: %s",
        JSON_EXPORT_FILE,
    )

    LOGGER.info(
        "Сохранен M3U плейлист: %s",
        PLAYLIST_FILE,
    )

    LOGGER.info(
        "Сохранен ML report: %s",
        LEARNED_REPORT_FILE,
    )


# ============================================================
# TRAINING
# ============================================================

def retrain_ml_model() -> Optional[
    EnsembleModel
]:

    if not HAS_ML:

        LOGGER.warning(
            "Scikit-learn не найден. "
            "Обучение пропущено."
        )

        return None

    db = Database()

    rows = db.get_training_data()

    LOGGER.info(
        "Собрано %d наблюдений "
        "из БД для ML-обучения.",
        len(rows),
    )

    if len(rows) < MIN_TRAINING_SAMPLES:

        LOGGER.info(
            "Недостаточно попыток "
            "для ML (%d/%d).",
            len(rows),
            MIN_TRAINING_SAMPLES,
        )

        return None

    successes = sum(
        int(row["success"])
        for row in rows
    )

    failures = (
        len(rows)
        - successes
    )

    LOGGER.info(
        "TRAINING DATA: "
        "SUCCESS=%d FAIL=%d",
        successes,
        failures,
    )

    if successes == 0 or failures == 0:

        LOGGER.warning(
            "Обучение пока невозможно: "
            "в накопленной истории "
            "должны присутствовать "
            "оба класса SUCCESS и FAIL."
        )

        return None

    model = EnsembleModel()

    try:

        metrics = model.fit(
            rows
        )

        # ----------------------------------------------------
        # Версия текущего запуска
        # ----------------------------------------------------

        model.save(
            VERSIONED_MODEL_FILE
        )

        # ----------------------------------------------------
        # Последняя рабочая модель
        # ----------------------------------------------------

        model.save(
            MODEL_FILE
        )

        LOGGER.info(
            "ML-модель сохранена: %s",
            VERSIONED_MODEL_FILE,
        )

        LOGGER.info(
            "ML latest сохранён: %s",
            MODEL_FILE,
        )

        LOGGER.info(
            "=== ML ENSEMBLE "
            "6.1.7602.2901626_AI_build_"
            "6.1.760229.0162631 ==="
        )

        for key, value in (
            metrics.items()
        ):

            LOGGER.info(
                "%-12s: %.6f",
                key,
                value,
            )

        return model

    except ValueError as e:

        LOGGER.warning(
            "Обучение пока невозможно: %s",
            e,
        )

        return None


# ============================================================
# PIPELINE
# ============================================================

def run_pipeline():

    LOGGER.info(
        "========================================"
    )

    LOGGER.info(
        "Alias Verification Engine "
        "6.1.7602.2901626_AI_build_"
        "6.1.760229.0162631"
    )

    LOGGER.info(
        "NGG AI RUN #%d",
        RUN_NUMBER,
    )

    LOGGER.info(
        "========================================"
    )

    db = Database()

    epg_kb = (
        EPGKnowledgeBase()
    )

    epg_kb.load()

    # --------------------------------------------------------
    # Historical JSON
    # --------------------------------------------------------

    all_json_records = (
        load_all_json_records()
    )

    if all_json_records:

        LOGGER.info(
            "[JSON] Успешно обработано "
            "записей из единого пула: %d",
            len(all_json_records),
        )

    # --------------------------------------------------------
    # Existing model
    #
    # Используется latest модель ДО нового обучения.
    # --------------------------------------------------------

    ml_model = (
        EnsembleModel.load()
    )

    if ml_model and ml_model.trained:

        LOGGER.info(
            "Загружена предыдущая "
            "ML-модель для ранжирования."
        )

        LOGGER.info(
            "Модель обучена на: %d "
            "наблюдениях",
            ml_model.training_samples,
        )

    else:

        LOGGER.info(
            "Рабочая ML-модель отсутствует. "
            "Используется базовое ранжирование."
        )

        ml_model = None

    # --------------------------------------------------------
    # CDN
    # --------------------------------------------------------

    scanner = MultiNodeScanner(
        db,
        epg_kb,
        ml_model,
    )

    scanner.ping_nodes()

    # --------------------------------------------------------
    # EPG
    # --------------------------------------------------------

    channels = (
        fetch_epg_channels()
    )

    results: List[
        AliasMatch
    ] = []

    LOGGER.info(
        "Старт параллельного "
        "ML-сканирования "
        "6.1.7602.2901626_AI_build_"
        "6.1.760229.0162631..."
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_WORKER_THREADS
    ) as executor:

        future_to_ch = {
            executor.submit(
                scanner.probe_channel,
                ch,
            ): ch
            for ch in channels
        }

        for future in (
            concurrent.futures.as_completed(
                future_to_ch
            )
        ):

            res = future.result()

            results.append(
                res
            )

            if res.cdn_alias:

                LOGGER.info(
                    "[+] Найден: "
                    "%s -> %s "
                    "(%.4f)",
                    res.channel_name,
                    res.streams[0].url,
                    res.confidence,
                )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Только ПОСЛЕ свежего сканирования
    # добавленные SUCCESS/FAIL попадают
    # в обучение.
    # --------------------------------------------------------

    LOGGER.info(
        "Запуск автообучения ML-модели "
        "по всей накопленной истории..."
    )

    trained_model = (
        retrain_ml_model()
    )

    if trained_model:

        LOGGER.info(
            "Новая ML-модель построена "
            "на всей накопленной истории."
        )

    else:

        LOGGER.info(
            "Новая модель не создана. "
            "Существующая история сохранена."
        )

    # --------------------------------------------------------
    # Reports
    # --------------------------------------------------------

    LOGGER.info(
        "Экспорт всех видов отчетов..."
    )

    save_all_reports(
        channels,
        results,
    )

    stats = db.get_statistics()

    LOGGER.info(
        "========================================"
    )

    LOGGER.info(
        "RUN #%d FINISHED",
        RUN_NUMBER,
    )

    LOGGER.info(
        "ENGINE VERSION: %s",
        ENGINE_VERSION,
    )

    LOGGER.info(
        "DB attempts: %d",
        stats["total"],
    )

    LOGGER.info(
        "DB SUCCESS: %d",
        stats["successful"],
    )

    LOGGER.info(
        "DB FAIL: %d",
        stats["failed"],
    )

    LOGGER.info(
        "DB runs: %d",
        stats["runs"],
    )

    LOGGER.info(
        "========================================"
    )


# ============================================================
# STATS
# ============================================================

def show_stats():

    db = Database()

    stats = (
        db.get_statistics()
    )

    print(
        "\n=== СТАТИСТИКА "
        "БАЗЫ ДАННЫХ ==="
    )

    print(
        f"Версия движка: "
        f"{ENGINE_VERSION}"
    )

    print(
        f"Всего проверок: "
        f"{stats['total']}"
    )

    print(
        f"Успешных:       "
        f"{stats['successful']}"
    )

    print(
        f"Неуспешных:     "
        f"{stats['failed']}"
    )

    print(
        f"Запусков:       "
        f"{stats['runs']}"
    )

    model = (
        EnsembleModel.load()
    )

    if model:

        print(
            "\n=== ПОСЛЕДНЯЯ ML-МОДЕЛЬ ==="
        )

        print(
            f"Версия:          "
            f"{model.VERSION}"
        )

        print(
            f"Обучающих данных: "
            f"{model.training_samples}"
        )

        print(
            f"Создана:          "
            f"{model.created_at}"
        )

        for key, value in (
            model.metrics.items()
        ):

            print(
                f"{key}: "
                f"{value:.6f}"
            )


# ============================================================
# CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Alias Verification Engine "
            "6.1.7602.2901626_AI_build_"
            "6.1.760229.0162631 "
            "ML Evolution"
        )
    )

    parser.add_argument(
        "--scan",
        action="store_true",
        help=(
            "Полное сканирование "
            "и обучение"
        ),
    )

    parser.add_argument(
        "--train",
        action="store_true",
        help=(
            "Переобучить ML-модель "
            "по всей накопленной истории"
        ),
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help=(
            "Показать статистику "
            "и состояние модели"
        ),
    )

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