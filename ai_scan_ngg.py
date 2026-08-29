#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Alias Verification Engine
6.1.7602.2901626_AI_build_6.1.760229.0162631
ngSKALA ML-Hybrid Evolution Edition

Исправленная и усиленная версия.

Основные возможности:

1. Мульти-нодовый CDN Ngenix scanner.
2. EPG 2016 Knowledge Layer.
3. Универсальная загрузка исторических JSON.
4. Импорт исторических JSON в SQLite Knowledge Base.
5. Накопительная SQLite Knowledge Base.
6. Самообучение по ВСЕЙ накопленной истории.
7. ML-ансамбль:
   - RandomForest
   - ExtraTrees
   - HistGradientBoosting
   - Deep RandomForest
8. Стабильные детерминированные ML-признаки.
9. Версионирование каждого запуска.
10. model_latest.joblib = последняя совместимая обученная модель.
11. Предыдущие модели и отчёты не удаляются.
12. NGG_RUN_NUMBER поддерживается через GitHub Actions.
13. Исторические JSON приводятся к единой схеме.
14. Защита от повторного импорта исторических данных.
15. Детерминированный порядок результатов.
16. Исправлено соответствие ChannelInput <-> AliasMatch.
17. Расширенная статистика.
18. Контроль версии feature schema.
19. Безопасное поведение при недостатке данных для ML.
20. ML используется для РАНЖИРОВАНИЯ кандидатов,
    а физическое подтверждение выполняется CDN/HLS scanner.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import ssl
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
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

ENGINE_NAME = "Alias Verification Engine"

ENGINE_VERSION = (
    "6.1.7602.2901626_AI_build_6.1.760229.0162631"
)

FEATURE_SCHEMA_VERSION = "FS-2"

MODEL_SCHEMA_VERSION = "MODEL-2"

MACHINE_SOURCE = (
    "ALIAS_MODULE_"
    "6.1.7602.2901626_AI_build_6.1.760229.0162631"
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_REQUEST_TIMEOUT = 4

EPG_REQUEST_TIMEOUT = 20

MAX_WORKER_THREADS = 20

MAX_NODE_WORKERS = 15

TOP_RESULTS = 25

MIN_TRAINING_SAMPLES = 30

MIN_CLASS_SAMPLES = 2

RANDOM_STATE = 42

# Для проверки HLS достаточно первых нескольких KB.
HLS_PROBE_BYTES = 4096

# Если True, HTTPS-сертификаты проверяются.
# При необходимости можно отключить через переменную:
# NGG_SSL_VERIFY=0
SSL_VERIFY = (
    os.environ.get(
        "NGG_SSL_VERIFY",
        "1",
    ).strip().lower()
    not in {
        "0",
        "false",
        "no",
        "off",
    }
)


# ============================================================
# RUN NUMBER
# ============================================================

def get_run_number() -> int:
    """
    Получает номер запуска.

    Приоритет:

    1. NGG_RUN_NUMBER
    2. Максимальный существующий номер + 1
    """

    env_value = os.environ.get(
        "NGG_RUN_NUMBER",
        "",
    ).strip()

    if (
        env_value.isdigit()
        and int(env_value) > 0
    ):
        return int(env_value)

    numbers: List[int] = []

    patterns = [
        "playlist_*.m3u",
        "Ai_Alias_*.txt",
        "Ai_Alias_export_*.json",
        "Ai_Alias_ngnorm_*.txt",
        "ngSKALA_learned_report_*.txt",
    ]

    for pattern in patterns:

        for path in Path(".").glob(pattern):

            match = re.search(
                r"_(\d+)\.[^.]+$",
                path.name,
            )

            if match:

                try:
                    numbers.append(
                        int(match.group(1))
                    )
                except ValueError:
                    pass

    return max(
        numbers,
        default=0,
    ) + 1


RUN_NUMBER = get_run_number()


# ============================================================
# VERSIONED FILES
# ============================================================

def versioned_name(
    base: str,
    run_number: int = RUN_NUMBER,
) -> str:

    path = Path(base)

    return str(
        path.with_name(
            f"{path.stem}_{run_number}"
            f"{path.suffix}"
        )
    )


HUMAN_REPORT_FILE = versioned_name(
    "Ai_Alias.txt"
)

MACHINE_REPORT_FILE = versioned_name(
    "Ai_Alias_ngnorm.txt"
)

JSON_EXPORT_FILE = versioned_name(
    "Ai_Alias_export.json"
)

PLAYLIST_FILE = versioned_name(
    "playlist.m3u"
)

LEARNED_REPORT_FILE = versioned_name(
    "ngSKALA_learned_report.txt"
)

VERSIONED_MODEL_FILE = (
    DATA_DIR
    / f"model_{RUN_NUMBER}.joblib"
)


# ============================================================
# SSL
# ============================================================

if SSL_VERIFY:

    SSL_CONTEXT = (
        ssl.create_default_context()
    )

else:

    SSL_CONTEXT = (
        ssl.create_default_context()
    )

    SSL_CONTEXT.check_hostname = False

    SSL_CONTEXT.verify_mode = (
        ssl.CERT_NONE
    )


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "[%(levelname)s] "
        "%(name)s: %(message)s"
    ),
)

LOGGER = logging.getLogger(
    "AliasEngine"
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


def normalize_unicode(
    value: str,
) -> str:

    if not value:
        return ""

    return unicodedata.normalize(
        "NFKC",
        str(value),
    ).strip()


def transliterate_russian(
    value: str,
) -> str:

    return (
        normalize_unicode(value)
        .casefold()
        .translate(
            RUSSIAN_TRANSLITERATION_TABLE
        )
    )


def canonical_text(
    value: str,
) -> str:

    value = normalize_unicode(
        value
    )

    value = value.casefold()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


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


KNOWN_ALIAS_DICTIONARY: Dict[
    str,
    Set[str],
] = {
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
    {
        "id": "scream",
        "name": "Scream",
        "logo": "",
    },
    {
        "id": "shokiruyuschee",
        "name": "Шокирующее",
        "logo": "",
    },
    {
        "id": "viju_planet",
        "name": "viju+ planet",
        "logo": "",
    },
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
    {
        "id": "hit",
        "name": "ХИТ",
        "logo": "",
    },
    {
        "id": "kinokomediya",
        "name": "Кинокомедия",
        "logo": "",
    },
    {
        "id": "cinema",
        "name": "CINEMA",
        "logo": "",
    },
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
    {
        "id": "boevik",
        "name": "Боевик",
        "logo": "",
    },
    {
        "id": "kinomix",
        "name": "Киномикс",
        "logo": "",
    },
    {
        "id": "detektiv",
        "name": "Детектив",
        "logo": "",
    },
    {
        "id": "rodnoe_kino",
        "name": "Родное кино",
        "logo": "",
    },
    {
        "id": "patriot",
        "name": "Патриот",
        "logo": "",
    },
    {
        "id": "rtg_hd",
        "name": "RTG HD",
        "logo": "",
    },
    {
        "id": "rtg_int",
        "name": "RTG Int",
        "logo": "",
    },
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
    {
        "id": "kinoujas",
        "name": "КИНОУЖАС",
        "logo": "",
    },
    {
        "id": "kinosemya",
        "name": "КИНОСЕМЬЯ",
        "logo": "",
    },
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
    {
        "id": "komediya",
        "name": "Комедия",
        "logo": "",
    },
    {
        "id": "klyuch",
        "name": "Ключ",
        "logo": "",
    },
    {
        "id": "ntv_plus",
        "name": "НТВ-ПЛЮС",
        "logo": "",
    },
    {
        "id": "rutube",
        "name": "RUTUBE",
        "logo": "",
    },
    {
        "id": "premier",
        "name": "PREMIER",
        "logo": "",
    },
    {
        "id": "ntv",
        "name": "НТВ",
        "logo": "",
    },
    {
        "id": "tnt",
        "name": "ТНТ",
        "logo": "",
    },
    {
        "id": "pyatnica",
        "name": "Пятница!",
        "logo": "",
    },
    {
        "id": "tv3",
        "name": "ТВ-3",
        "logo": "",
    },
    {
        "id": "tnt4",
        "name": "ТНТ4",
        "logo": "",
    },
    {
        "id": "match_tv",
        "name": "Матч ТВ",
        "logo": "",
    },
    {
        "id": "trash",
        "name": "Trash",
        "logo": "",
    },
    {
        "id": "match_strana",
        "name": "Матч! Страна",
        "logo": "",
    },
    {
        "id": "2x2",
        "name": "2x2",
        "logo": "",
    },
    {
        "id": "subbota",
        "name": "Суббота!",
        "logo": "",
    },
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
    {
        "id": "ntv_hit",
        "name": "НТВ Хит",
        "logo": "",
    },
    {
        "id": "unknown_russia",
        "name": "Неизвестная Россия",
        "logo": "",
    },
    {
        "id": "boec",
        "name": "Боец",
        "logo": "",
    },
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
    candidates: List[
        AliasCandidate
    ] = field(default_factory=list)
    streams: List[
        CDNStream
    ] = field(default_factory=list)


# ============================================================
# UTILS
# ============================================================

def utc_now() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


def stable_sha256(
    value: str,
) -> str:

    return hashlib.sha256(
        value.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()


def safe_int(
    value: Any,
    default: int = 0,
) -> int:

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return default


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return default


def parse_bool(
    value: Any,
) -> Optional[bool]:

    if isinstance(
        value,
        bool,
    ):
        return value

    if value is None:
        return None

    text = (
        str(value)
        .strip()
        .casefold()
    )

    if text in {
        "1",
        "true",
        "yes",
        "success",
        "ok",
        "valid",
        "confirmed",
    }:
        return True

    if text in {
        "0",
        "false",
        "no",
        "fail",
        "failed",
        "invalid",
        "unknown",
    }:
        return False

    return None


# ============================================================
# JSON LOADING
# ============================================================

REMOTE_JSON_SOURCES = [
    (
        "https://raw.githubusercontent.com/"
        "Phoenix89S/IpTV_playlist_2026Ru/main/"
        "Ai_Alias_export.json"
    ),
    (
        "https://raw.githubusercontent.com/"
        "Phoenix89S/IpTV_playlist_2026Ru/main/"
        "Ai_Alias_export_1.json"
    ),
    (
        "https://raw.githubusercontent.com/"
        "Phoenix89S/IpTV_playlist_2026Ru/main/"
        "Ai_Alias_export_2.json"
    ),
]


def load_json_source(
    source: str,
) -> Optional[Any]:

    try:

        if str(source).startswith(
            (
                "http://",
                "https://",
            )
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

                    LOGGER.warning(
                        "[JSON] HTTP %s: %s",
                        status_code,
                        source,
                    )

                    return None

                raw = response.read()

                if not raw:
                    return None

                return json.loads(
                    raw.decode(
                        "utf-8-sig"
                    )
                )

        path = Path(source)

        if not path.exists():
            return None

        if not path.is_file():
            return None

        return json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )

    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
    ) as exc:

        LOGGER.warning(
            "[JSON] Ошибка загрузки %s: %s",
            source,
            exc,
        )

    except Exception as exc:

        LOGGER.warning(
            "[JSON] Непредвиденная ошибка "
            "%s: %s",
            source,
            exc,
        )

    return None


def iter_json_records(
    data: Any,
) -> Iterable[Dict[str, Any]]:

    if isinstance(
        data,
        list,
    ):

        for item in data:

            if isinstance(
                item,
                dict,
            ):
                yield item

        return

    if not isinstance(
        data,
        dict,
    ):
        return

    containers = (
        "records",
        "data",
        "items",
        "results",
        "aliases",
        "channels",
        "history",
        "attempts",
    )

    for key in containers:

        value = data.get(key)

        if isinstance(
            value,
            list,
        ):

            for item in value:

                if isinstance(
                    item,
                    dict,
                ):
                    yield item

            return

    yield data


def find_all_json_sources() -> List[str]:

    sources: List[str] = []

    patterns = [
        "*.json",
        "Ai_Alias*.json",
        "*Alias*.json",
    ]

    found: Set[str] = set()

    for pattern in patterns:

        for path in Path(
            "."
        ).rglob(pattern):

            if path.is_file():

                try:
                    resolved = str(
                        path.resolve()
                    )

                    # Не импортируем внутренние
                    # служебные каталоги Git.
                    if (
                        "/.git/"
                        in resolved.replace(
                            "\\",
                            "/",
                        )
                    ):
                        continue

                    found.add(
                        resolved
                    )

                except OSError:
                    continue

    for path in sorted(found):
        sources.append(path)

    for url in REMOTE_JSON_SOURCES:

        if url not in sources:
            sources.append(url)

    return sources


def load_all_json_records() -> List[Dict[str, Any]]:

    sources = find_all_json_sources()

    all_records: List[
        Dict[str, Any]
    ] = []

    LOGGER.info(
        "[JSON] Источников найдено: %d",
        len(sources),
    )

    for source in sources:

        LOGGER.info(
            "[JSON] Загрузка: %s",
            source,
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

        LOGGER.info(
            "[JSON] Получено записей: %d",
            count,
        )

    LOGGER.info(
        "[JSON] Всего исторических "
        "записей: %d",
        len(all_records),
    )

    return all_records


# ============================================================
# HISTORICAL JSON NORMALIZATION
# ============================================================

def first_value(
    record: Dict[str, Any],
    keys: Tuple[str, ...],
) -> Any:

    for key in keys:

        if key in record:

            value = record[key]

            if value is not None:
                return value

    return None


def normalize_historical_record(
    record: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    candidate = first_value(
        record,
        (
            "candidate",
            "alias",
            "cdn_alias",
            "cdnAlias",
            "value",
            "normalized_name",
            "normalized",
        ),
    )

    rule = first_value(
        record,
        (
            "rule",
            "rule_name",
            "reason",
            "source_rule",
        ),
    )

    success_value = first_value(
        record,
        (
            "success",
            "confirmed",
            "valid",
            "reachable",
            "status",
        ),
    )

    if candidate is None:
        return None

    candidate = str(
        candidate
    ).strip()

    if not candidate:
        return None

    success = parse_bool(
        success_value
    )

    if success is None:

        status_code = safe_int(
            first_value(
                record,
                (
                    "status_code",
                    "http_status",
                    "statusCode",
                ),
            ),
            0,
        )

        success = (
            status_code == 200
        )

    if rule is None:
        rule = "historical_json"

    pattern = first_value(
        record,
        (
            "pattern",
            "url_pattern",
        ),
    )

    node = first_value(
        record,
        (
            "node",
            "cdn_node",
        ),
    )

    status_code = safe_int(
        first_value(
            record,
            (
                "status_code",
                "http_status",
                "statusCode",
            ),
        ),
        200 if success else 0,
    )

    response_time = safe_float(
        first_value(
            record,
            (
                "response_time",
                "response_time_ms",
                "ping_ms",
            ),
        ),
        0.0,
    )

    return {
        "candidate": candidate,
        "rule": str(rule),
        "pattern": (
            str(pattern)
            if pattern is not None
            else ""
        ),
        "node": (
            str(node)
            if node is not None
            else ""
        ),
        "success": 1 if success else 0,
        "status_code": status_code,
        "response_time": response_time,
        "source": str(
            record.get(
                "_json_source",
                "historical_json",
            )
        ),
    }


# ============================================================
# STABLE ML FEATURES
# ============================================================

def stable_rule_value(
    rule: str,
) -> float:
    """
    Стабильное представление правила.

    НИКОГДА не используется Python hash().
    """

    digest = hashlib.sha256(
        rule.casefold().encode(
            "utf-8"
        )
    ).digest()

    value = int.from_bytes(
        digest[:4],
        byteorder="big",
        signed=False,
    )

    return float(
        value % 10000
    )


def stable_string_value(
    value: str,
) -> float:

    digest = hashlib.sha256(
        value.casefold().encode(
            "utf-8"
        )
    ).digest()

    number = int.from_bytes(
        digest[:4],
        byteorder="big",
        signed=False,
    )

    return float(
        number % 10000
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

    uppercase = sum(
        c.isupper()
        for c in val
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

        float(bool(
            re.search(
                r"\d",
                val,
            )
        )),

        float(bool(
            re.search(
                r"^\d",
                val,
            )
        )),

        float(bool(
            re.search(
                r"\d$",
                val,
            )
        )),

        float(
            len(
                re.findall(
                    r"[aeiouy]",
                    lower,
                )
            )
        ),

        float(vowels),

        float(consonants),

        float(
            translit == lower
        ),

        float(len(translit)),

        float("viju" in lower),
        float("ntv" in lower),
        float("tnt" in lower),
        float("ren" in lower),
        float("mir" in lower),
        float("match" in lower),

        float(uppercase),

        float(
            digits / max(
                len(val),
                1,
            )
        ),

        float(
            unique / max(
                len(val),
                1,
            )
        ),

        stable_rule_value(rule),

        stable_string_value(
            candidate
        ),

        stable_string_value(
            rule
        ),
    ]


def build_matrix(
    rows: List[Dict[str, Any]],
) -> np.ndarray:

    if not rows:

        return np.empty(
            (
                0,
                len(
                    make_features(
                        "",
                        "",
                    )
                ),
            ),
            dtype=np.float64,
        )

    return np.asarray(
        [
            make_features(
                row.get(
                    "candidate",
                    "",
                ),
                row.get(
                    "rule",
                    "",
                ),
            )
            for row in rows
        ],
        dtype=np.float64,
    )


# ============================================================
# ML MODEL
# ============================================================

class EnsembleModel:

    VERSION = ENGINE_VERSION

    FEATURE_SCHEMA = (
        FEATURE_SCHEMA_VERSION
    )

    MODEL_SCHEMA = (
        MODEL_SCHEMA_VERSION
    )

    def __init__(self):

        self.trained = False

        self.metrics: Dict[
            str,
            float,
        ] = {}

        self.training_samples = 0

        self.created_at: Optional[
            str
        ] = None

        self.feature_schema = (
            FEATURE_SCHEMA_VERSION
        )

        self.model_schema = (
            MODEL_SCHEMA_VERSION
        )

        self.class_distribution: Dict[
            str,
            int,
        ] = {}

        if not HAS_ML:
            return

        self.random_forest = (
            RandomForestClassifier(
                n_estimators=700,
                max_depth=20,
                min_samples_leaf=1,
                max_features="sqrt",
                class_weight=(
                    "balanced_subsample"
                ),
                random_state=42,
                n_jobs=-1,
            )
        )

        self.extra_trees = (
            ExtraTreesClassifier(
                n_estimators=700,
                max_depth=24,
                min_samples_leaf=1,
                max_features="sqrt",
                class_weight="balanced",
                random_state=123,
                n_jobs=-1,
            )
        )

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
        rows: List[Dict[str, Any]],
    ) -> Dict[str, float]:

        if not HAS_ML:

            raise ValueError(
                "Scikit-learn не установлен."
            )

        if len(rows) < (
            MIN_TRAINING_SAMPLES
        ):

            raise ValueError(
                "Недостаточно данных: "
                f"{len(rows)}/"
                f"{MIN_TRAINING_SAMPLES}."
            )

        X = build_matrix(
            rows
        )

        y = np.asarray(
            [
                int(
                    row.get(
                        "success",
                        0,
                    )
                )
                for row in rows
            ],
            dtype=np.int8,
        )

        unique, counts = np.unique(
            y,
            return_counts=True,
        )

        if len(unique) < 2:

            raise ValueError(
                "Требуются SUCCESS и FAIL."
            )

        class_distribution = {
            str(
                int(cls)
            ): int(count)
            for cls, count
            in zip(
                unique,
                counts,
            )
        }

        if any(
            count < MIN_CLASS_SAMPLES
            for count in counts
        ):

            raise ValueError(
                "Для каждого класса "
                "нужно минимум "
                f"{MIN_CLASS_SAMPLES} "
                "наблюдения."
            )

        test_size = max(
            0.25,
            1.0 / max(
                len(rows),
                1,
            ),
        )

        # Не позволяем test-набору
        # оказаться меньше количества классов.
        test_count = max(
            int(
                round(
                    len(rows)
                    * test_size
                )
            ),
            len(unique),
        )

        if (
            len(rows)
            - test_count
            < len(unique)
        ):

            test_count = len(unique)

        if (
            test_count >= len(rows)
        ):

            test_count = max(
                len(unique),
                int(
                    len(rows)
                    * 0.2
                ),
            )

        try:

            X_train, X_test, y_train, y_test = (
                train_test_split(
                    X,
                    y,
                    test_size=test_count,
                    random_state=RANDOM_STATE,
                    stratify=y,
                )
            )

        except ValueError as exc:

            raise ValueError(
                "Не удалось разделить "
                f"training/test данные: {exc}"
            )

        LOGGER.info(
            "ML train: %d samples",
            len(X_train),
        )

        LOGGER.info(
            "ML test: %d samples",
            len(X_test),
        )

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

        self.training_samples = (
            len(rows)
        )

        self.created_at = utc_now()

        self.class_distribution = (
            class_distribution
        )

        return metrics

    def predict_probability(
        self,
        candidates: List[
            Dict[str, Any]
        ],
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

        if (
            getattr(
                self,
                "feature_schema",
                None,
            )
            != FEATURE_SCHEMA_VERSION
        ):

            LOGGER.warning(
                "Feature schema модели "
                "несовместима с текущей."
            )

            return [
                0.5
                for _ in candidates
            ]

        X = build_matrix(
            candidates
        )

        try:

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

            return [
                float(
                    min(
                        max(
                            value,
                            0.0,
                        ),
                        1.0,
                    )
                )
                for value in probability
            ]

        except Exception as exc:

            LOGGER.warning(
                "Ошибка ML prediction: %s",
                exc,
            )

            return [
                0.5
                for _ in candidates
            ]

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
    ) -> Optional[
        "EnsembleModel"
    ]:

        path = Path(path)

        if not path.exists():
            return None

        try:

            model = joblib.load(
                path
            )

            if not isinstance(
                model,
                EnsembleModel,
            ):

                LOGGER.warning(
                    "Файл %s содержит "
                    "неизвестный объект.",
                    path,
                )

                return None

            if (
                getattr(
                    model,
                    "feature_schema",
                    None,
                )
                != FEATURE_SCHEMA_VERSION
            ):

                LOGGER.warning(
                    "Модель %s имеет "
                    "старую схему признаков.",
                    path,
                )

                return None

            if (
                getattr(
                    model,
                    "model_schema",
                    None,
                )
                != MODEL_SCHEMA_VERSION
            ):

                LOGGER.warning(
                    "Модель %s имеет "
                    "несовместимую схему.",
                    path,
                )

                return None

            return model

        except Exception as exc:

            LOGGER.warning(
                "Не удалось загрузить "
                "ML модель %s: %s",
                path,
                exc,
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
            self.db_path,
            timeout=30,
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
                    source TEXT DEFAULT 'live_scan',
                    record_hash TEXT,
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
                    last_updated DATETIME
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

            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                idx_att_record_hash
                ON attempts(record_hash)
                """
            )

            self._migrate_columns(
                conn
            )

            conn.commit()

    @staticmethod
    def _migrate_columns(
        conn: sqlite3.Connection,
    ) -> None:

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(attempts)"
            ).fetchall()
        }

        migrations = {
            "run_number":
                "ALTER TABLE attempts "
                "ADD COLUMN run_number "
                "INTEGER DEFAULT 0",

            "source":
                "ALTER TABLE attempts "
                "ADD COLUMN source "
                "TEXT DEFAULT 'live_scan'",

            "record_hash":
                "ALTER TABLE attempts "
                "ADD COLUMN record_hash "
                "TEXT",

        }

        for column, sql in migrations.items():

            if column not in columns:

                try:

                    conn.execute(sql)

                except sqlite3.OperationalError:

                    LOGGER.debug(
                        "Колонка %s уже существует.",
                        column,
                    )

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
        source: str = "live_scan",
        record_hash: Optional[str] = None,
    ) -> bool:

        if record_hash is None:

            raw = "|".join(
                [
                    candidate,
                    rule,
                    pattern,
                    node,
                    str(
                        1
                        if success
                        else 0
                    ),
                    str(
                        status_code
                        or 0
                    ),
                    f"{response_time:.4f}",
                    str(run_number),
                    source,
                ]
            )

            record_hash = stable_sha256(
                raw
            )

        try:

            with self._get_connection() as conn:

                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO attempts (
                        candidate,
                        rule,
                        pattern,
                        node,
                        success,
                        status_code,
                        response_time,
                        run_number,
                        source,
                        record_hash,
                        created_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?
                    )
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
                        source,
                        record_hash,
                        utc_now(),
                    ),
                )

                conn.commit()

                return (
                    cursor.rowcount > 0
                )

        except sqlite3.Error as exc:

            LOGGER.warning(
                "SQLite save_attempt error: %s",
                exc,
            )

            return False

    def import_historical_records(
        self,
        records: List[
            Dict[str, Any]
        ],
    ) -> int:

        imported = 0

        for record in records:

            normalized = (
                normalize_historical_record(
                    record
                )
            )

            if normalized is None:
                continue

            source = normalized[
                "source"
            ]

            raw = "|".join(
                [
                    normalized[
                        "candidate"
                    ],
                    normalized[
                        "rule"
                    ],
                    normalized[
                        "pattern"
                    ],
                    normalized[
                        "node"
                    ],
                    str(
                        normalized[
                            "success"
                        ]
                    ),
                    str(
                        normalized[
                            "status_code"
                        ]
                    ),
                    str(
                        normalized[
                            "response_time"
                        ]
                    ),
                    "historical",
                    source,
                ]
            )

            record_hash = (
                stable_sha256(raw)
            )

            if self.save_attempt(
                candidate=normalized[
                    "candidate"
                ],
                rule=normalized[
                    "rule"
                ],
                pattern=normalized[
                    "pattern"
                ],
                node=normalized[
                    "node"
                ],
                success=bool(
                    normalized[
                        "success"
                    ]
                ),
                status_code=normalized[
                    "status_code"
                ],
                response_time=normalized[
                    "response_time"
                ],
                run_number=0,
                source=(
                    "historical_json:"
                    + source
                ),
                record_hash=record_hash,
            ):

                imported += 1

        LOGGER.info(
            "[JSON] Импортировано новых "
            "исторических ML-наблюдений: %d",
            imported,
        )

        return imported

    def get_training_data(
        self,
    ) -> List[Dict[str, Any]]:

        with self._get_connection() as conn:

            rows = conn.execute(
                """
                SELECT
                    candidate,
                    rule,
                    success,
                    status_code,
                    response_time,
                    run_number,
                    source
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
        confidence: float = 1.0,
    ) -> None:

        with self._get_connection() as conn:

            conn.execute(
                """
                INSERT INTO learned_aliases (
                    channel_name,
                    cdn_alias,
                    confidence,
                    hit_count,
                    last_updated
                )
                VALUES (
                    ?, ?, ?, 1, ?
                )

                ON CONFLICT(channel_name)
                DO UPDATE SET
                    cdn_alias =
                        CASE
                            WHEN excluded.confidence
                                 >= learned_aliases.confidence
                            THEN excluded.cdn_alias
                            ELSE learned_aliases.cdn_alias
                        END,

                    confidence =
                        MAX(
                            learned_aliases.confidence,
                            excluded.confidence
                        ),

                    hit_count =
                        learned_aliases.hit_count + 1,

                    last_updated =
                        excluded.last_updated
                """,
                (
                    channel_name,
                    cdn_alias,
                    confidence,
                    utc_now(),
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

            historical = conn.execute(
                """
                SELECT COUNT(*)
                FROM attempts
                WHERE source LIKE 'historical_json:%'
                """
            ).fetchone()[0]

            live = conn.execute(
                """
                SELECT COUNT(*)
                FROM attempts
                WHERE source = 'live_scan'
                """
            ).fetchone()[0]

            runs = conn.execute(
                """
                SELECT COUNT(DISTINCT run_number)
                FROM attempts
                WHERE run_number > 0
                """
            ).fetchone()[0]

            aliases = conn.execute(
                """
                SELECT COUNT(*)
                FROM learned_aliases
                """
            ).fetchone()[0]

            return {
                "total": int(total),
                "successful": int(
                    successful
                ),
                "failed": int(
                    total - successful
                ),
                "runs": int(runs),
                "historical": int(
                    historical
                ),
                "live": int(live),
                "learned_aliases": int(
                    aliases
                ),
            }

    def get_top_aliases(
        self,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:

        with self._get_connection() as conn:

            rows = conn.execute(
                """
                SELECT
                    channel_name,
                    cdn_alias,
                    confidence,
                    hit_count,
                    last_updated
                FROM learned_aliases
                ORDER BY
                    confidence DESC,
                    hit_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            return [
                dict(row)
                for row in rows
            ]


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

    def load(self) -> None:

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
                timeout=EPG_REQUEST_TIMEOUT,
                context=SSL_CONTEXT,
            ) as resp:

                raw_bytes = resp.read()

                if raw_bytes:

                    data = json.loads(
                        raw_bytes.decode(
                            "utf-8-sig"
                        )
                    )

                    self.cache_path.write_bytes(
                        raw_bytes
                    )

                    LOGGER.info(
                        "EPG 2016 knowledge "
                        "обновлена из GitHub."
                    )

        except Exception as exc:

            LOGGER.warning(
                "Не удалось скачать "
                "EPG 2016 knowledge: %s",
                exc,
            )

        if (
            data is None
            and self.cache_path.exists()
        ):

            try:

                data = json.loads(
                    self.cache_path.read_text(
                        encoding="utf-8-sig"
                    )
                )

                LOGGER.info(
                    "EPG 2016 knowledge "
                    "загружена из кэша."
                )

            except Exception as exc:

                LOGGER.error(
                    "Ошибка локального "
                    "EPG knowledge: %s",
                    exc,
                )

        if not isinstance(
            data,
            dict,
        ):
            return

        items = data.get(
            "items",
            [],
        )

        if isinstance(
            items,
            list,
        ):

            self._index(
                items
            )

    def _index(
        self,
        items: List[dict],
    ) -> None:

        for item in items:

            if not isinstance(
                item,
                dict,
            ):
                continue

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
                ru_names
                if isinstance(
                    ru_names,
                    list,
                )
                else []
            )

            all_names.update(
                en_names
                if isinstance(
                    en_names,
                    list,
                )
                else []
            )

            if channel_id:
                all_names.add(
                    channel_id
                )

            for name in all_names:

                norm_key = (
                    self._normalize(
                        str(name)
                    )
                )

                if not norm_key:
                    continue

                self.name_to_candidates.setdefault(
                    norm_key,
                    set(),
                ).update(
                    str(candidate)
                    for candidate
                    in candidates
                    if str(candidate).strip()
                )

        LOGGER.info(
            "EPG 2016 индексировано "
            "названий: %d",
            len(
                self.name_to_candidates
            ),
        )

    @staticmethod
    def _normalize(
        s: str,
    ) -> str:

        s = canonical_text(
            s
        )

        s = re.sub(
            r"\(.*?\)",
            "",
            s,
        )

        s = re.sub(
            r"[^a-zа-яё0-9]",
            "",
            s,
        )

        return s

    def get_candidates(
        self,
        channel_name: str,
        tvg_id: str = "",
    ) -> List[str]:

        results: Set[str] = set()

        for key in (
            channel_name,
            tvg_id,
        ):

            if not key:
                continue

            norm = self._normalize(
                key
            )

            if norm in (
                self.name_to_candidates
            ):

                results.update(
                    self.name_to_candidates[
                        norm
                    ]
                )

        return sorted(
            results
        )


# ============================================================
# ALIAS GENERATION
# ============================================================

def clean_alias(
    value: str,
) -> str:

    value = normalize_unicode(
        str(value)
    ).casefold()

    value = re.sub(
        r"[^a-z0-9]+",
        "_",
        value,
    )

    return value.strip("_")


def generate_alias_candidates(
    channel: ChannelInput,
    epg_kb: Optional[
        EPGKnowledgeBase
    ] = None,
    ml_model: Optional[
        EnsembleModel
    ] = None,
) -> List[AliasCandidate]:

    name = (
        channel.display_name
    )

    epg_id = (
        channel.tvg_id
        or name
    )

    candidates: Dict[
        str,
        Tuple[str, str],
    ] = {}

    def add_cand(
        value: str,
        rule: str,
    ) -> None:

        val_clean = clean_alias(
            value
        )

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

        for candidate in (
            epg_kb.get_candidates(
                name,
                channel.tvg_id,
            )
        ):

            add_cand(
                candidate,
                "epg_xml_2016",
            )

    # --------------------------------------------------------
    # DICTIONARY
    # --------------------------------------------------------

    display_norm = canonical_text(
        name
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

    clean_id = re.sub(
        r"[^a-zA-Z0-9]+",
        "",
        epg_id,
    ).casefold()

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

    result: List[
        AliasCandidate
    ] = []

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

    # Вторичный приоритет:
    # одинаковый score -> EPG/dictionary выше.
    rule_priority = {
        "epg_xml_2016": 5,
        "known_dictionary": 4,
        "mapped_name": 3,
        "exact_id": 3,
        "clean_id": 2,
        "translit_underscore": 2,
        "translit_nospaces": 2,
        "underscore": 1,
        "no_spaces": 1,
        "strip_hd": 1,
        "viju_prefix": 1,
    }

    result.sort(
        key=lambda item: (
            -item.score,
            -rule_priority.get(
                item.reason,
                0,
            ),
            item.value,
        )
    )

    return result


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

    def ping_nodes(self) -> List[str]:

        LOGGER.info(
            "Опрос доступности "
            "Ngenix CDN..."
        )

        valid_nodes: List[
            str
        ] = []

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

                    status = getattr(
                        response,
                        "status",
                        200,
                    )

                    if status < 500:
                        return node

            except HTTPError as exc:

                if exc.code < 500:
                    return node

            except Exception:
                pass

            return None

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_NODE_WORKERS
        ) as executor:

            futures = [
                executor.submit(
                    check_node,
                    node,
                )
                for node in NGENIX_NODES
            ]

            for future in concurrent.futures.as_completed(
                futures
            ):

                try:

                    result = (
                        future.result()
                    )

                except Exception:

                    result = None

                if result:

                    valid_nodes.append(
                        result
                    )

        self.active_nodes = sorted(
            set(valid_nodes)
        )

        if not self.active_nodes:

            LOGGER.warning(
                "Не найдено доступных "
                "Ngenix узлов."
            )

        LOGGER.info(
            "Доступных узлов: %d/%d: %s",
            len(self.active_nodes),
            len(NGENIX_NODES),
            ", ".join(
                self.active_nodes
            )
            if self.active_nodes
            else "NONE",
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

        start_time = time.monotonic()

        req = Request(
            url,
            headers={
                "User-Agent":
                DEFAULT_USER_AGENT,
                "Accept":
                "application/vnd.apple.mpegurl,"
                "application/x-mpegURL,"
                "text/plain,*/*",
            },
        )

        try:

            with urlopen(
                req,
                timeout=DEFAULT_REQUEST_TIMEOUT,
                context=SSL_CONTEXT,
            ) as response:

                status = safe_int(
                    getattr(
                        response,
                        "status",
                        200,
                    ),
                    200,
                )

                elapsed_ms = (
                    time.monotonic()
                    - start_time
                ) * 1000.0

                if status != 200:

                    return (
                        False,
                        status,
                        elapsed_ms,
                    )

                chunk = (
                    response.read(
                        HLS_PROBE_BYTES
                    )
                )

                text = chunk.decode(
                    "utf-8",
                    errors="ignore",
                )

                # HLS playlist должен иметь
                # EXTM3U в начале/первых KB.
                valid = (
                    "#EXTM3U"
                    in text.upper()
                )

                return (
                    valid,
                    status,
                    elapsed_ms,
                )

        except HTTPError as exc:

            return (
                False,
                exc.code,
                (
                    time.monotonic()
                    - start_time
                )
                * 1000.0,
            )

        except (
            URLError,
            TimeoutError,
            OSError,
        ):

            return (
                False,
                0,
                (
                    time.monotonic()
                    - start_time
                )
                * 1000.0,
            )

        except Exception:

            return (
                False,
                0,
                (
                    time.monotonic()
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

        if not selected_candidates:

            return AliasMatch(
                channel_name=(
                    channel.display_name
                ),
                normalized_name="",
                cdn_alias=None,
                match_type="UNKNOWN",
                confidence=0.0,
                reason=(
                    "Не удалось "
                    "сформировать кандидатов."
                ),
                candidates=[],
                streams=[],
            )

        nodes = list(
            self.active_nodes
        )

        if not nodes:

            return AliasMatch(
                channel_name=(
                    channel.display_name
                ),
                normalized_name=(
                    channel.display_name
                ),
                cdn_alias=None,
                match_type="NO_CDN_NODES",
                confidence=0.0,
                reason=(
                    "Нет доступных "
                    "Ngenix узлов."
                ),
                candidates=candidates,
                streams=[],
            )

        for candidate in (
            selected_candidates
        ):

            for node in nodes:

                for pattern in (
                    DEFAULT_PATTERNS
                ):

                    relative_path = (
                        pattern.format(
                            v=candidate.value
                        )
                    )

                    stream_url = (
                        "https://"
                        f"{node}.cdn.ngenix.net/"
                        f"{relative_path}"
                    )

                    (
                        is_valid,
                        http_status,
                        ping_ms,
                    ) = (
                        self.verify_hls_stream(
                            stream_url
                        )
                    )

                    self.db.save_attempt(
                        candidate=(
                            candidate.value
                        ),
                        rule=(
                            candidate.reason
                        ),
                        pattern=pattern,
                        node=node,
                        success=is_valid,
                        status_code=(
                            http_status
                        ),
                        response_time=(
                            ping_ms
                        ),
                        run_number=RUN_NUMBER,
                        source="live_scan",
                    )

                    if is_valid:

                        candidate.confirmed = True

                        # Для подтверждённого потока
                        # физическая проверка важнее
                        # вероятности старой модели.
                        confidence = max(
                            float(
                                candidate.score
                            ),
                            0.90,
                        )

                        self.db.record_learned_alias(
                            channel.display_name,
                            candidate.value,
                            confidence,
                        )

                        stream = CDNStream(
                            alias=(
                                candidate.value
                            ),
                            url=stream_url,
                            node=(
                                f"{node}.cdn.ngenix.net"
                            ),
                            pattern=pattern,
                            rule_name=(
                                candidate.reason
                            ),
                            http_status=(
                                http_status
                            ),
                            reachable=True,
                            response_time_ms=(
                                ping_ms
                            ),
                        )

                        return AliasMatch(
                            channel_name=(
                                channel.display_name
                            ),
                            normalized_name=(
                                candidate.value
                            ),
                            cdn_alias=(
                                candidate.value
                            ),
                            match_type=(
                                "CONFIRMED_ML_ENSEMBLE"
                            ),
                            confidence=confidence,
                            reason=(
                                "HLS поток "
                                "валидирован на "
                                f"{node} "
                                "(правило: "
                                f"{candidate.reason})"
                            ),
                            candidates=candidates,
                            streams=[stream],
                        )

        return AliasMatch(
            channel_name=(
                channel.display_name
            ),
            normalized_name=(
                channel.display_name
            ),
            cdn_alias=None,
            match_type="UNKNOWN",
            confidence=0.0,
            reason=(
                "Ни один кандидат/"
                "узел/паттерн не прошёл "
                "проверку HLS."
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
        "Загрузка EPG: %s",
        EPG_URL,
    )

    req = Request(
        EPG_URL,
        headers={
            "User-Agent":
            DEFAULT_USER_AGENT
        },
    )

    channels: List[
        ChannelInput
    ] = []

    try:

        with urlopen(
            req,
            timeout=EPG_REQUEST_TIMEOUT,
            context=SSL_CONTEXT,
        ) as resp:

            raw = resp.read()

            if not raw:
                raise ValueError(
                    "EPG пустой."
                )

            try:

                with gzip.GzipFile(
                    fileobj=io.BytesIO(raw)
                ) as gz:

                    xml_data = gz.read()

            except OSError:

                # Некоторые серверы могут
                # вернуть уже распакованный XML.
                xml_data = raw

            root = ET.fromstring(
                xml_data
            )

            for ch in root.findall(
                "channel"
            ):

                cid = (
                    ch.get(
                        "id",
                        "",
                    ).strip()
                )

                display_nodes = ch.findall(
                    "display-name"
                )

                name = ""

                for node in display_nodes:

                    if (
                        node.text
                        and node.text.strip()
                    ):

                        name = (
                            node.text.strip()
                        )

                        break

                icon = ch.find(
                    "icon"
                )

                logo = ""

                if icon is not None:

                    logo = (
                        icon.get(
                            "src",
                            "",
                        ).strip()
                    )

                if cid and name:

                    channels.append(
                        ChannelInput(
                            display_name=name,
                            tvg_id=cid,
                            tvg_name=name,
                            logo=logo,
                        )
                    )

    except Exception as exc:

        LOGGER.error(
            "Ошибка загрузки EPG: %s",
            exc,
        )

    # --------------------------------------------------------
    # EXTRA CHANNELS
    # --------------------------------------------------------

    existing_ids = {
        canonical_text(
            channel.tvg_id
        )
        for channel
        in channels
        if channel.tvg_id
    }

    existing_names = {
        canonical_text(
            channel.display_name
        )
        for channel
        in channels
    }

    for extra in EXTRA_CHANNELS:

        extra_id = canonical_text(
            extra["id"]
        )

        extra_name = canonical_text(
            extra["name"]
        )

        if (
            extra_id in existing_ids
            or extra_name in existing_names
        ):
            continue

        channels.append(
            ChannelInput(
                display_name=extra[
                    "name"
                ],
                tvg_id=extra[
                    "id"
                ],
                tvg_name=extra[
                    "name"
                ],
                logo=extra[
                    "logo"
                ],
            )
        )

    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ:
    # детерминированный порядок.
    channels.sort(
        key=lambda channel: (
            canonical_text(
                channel.display_name
            ),
            canonical_text(
                channel.tvg_id
            ),
        )
    )

    LOGGER.info(
        "Всего каналов для "
        "сканирования: %d",
        len(channels),
    )

    return channels


# ============================================================
# REPORT HELPERS
# ============================================================

def escape_m3u_attribute(
    value: str,
) -> str:

    return (
        str(value)
        .replace(
            '"',
            "'",
        )
        .replace(
            "\r",
            " ",
        )
        .replace(
            "\n",
            " ",
        )
    )


def result_map(
    channels: List[ChannelInput],
    results: List[AliasMatch],
) -> Dict[str, AliasMatch]:

    mapping: Dict[
        str,
        AliasMatch,
    ] = {}

    for result in results:

        key = (
            canonical_text(
                result.channel_name
            )
        )

        mapping[key] = result

    return mapping


# ============================================================
# REPORT EXPORT
# ============================================================

def save_all_reports(
    channels: List[ChannelInput],
    results: List[AliasMatch],
    model: Optional[
        EnsembleModel
    ] = None,
) -> None:

    generated_at = utc_now()

    mapping = result_map(
        channels,
        results,
    )

    # --------------------------------------------------------
    # HUMAN REPORT
    # --------------------------------------------------------

    with open(
        HUMAN_REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            "=== ALIAS VERIFICATION ENGINE ===\n"
        )

        f.write(
            f"ENGINE={ENGINE_VERSION}\n"
        )

        f.write(
            f"RUN={RUN_NUMBER}\n"
        )

        f.write(
            f"FEATURE_SCHEMA="
            f"{FEATURE_SCHEMA_VERSION}\n"
        )

        f.write(
            f"GENERATED={generated_at}\n\n"
        )

        for channel in channels:

            result = mapping.get(
                canonical_text(
                    channel.display_name
                )
            )

            if result is None:
                continue

            f.write(
                f"[КАНАЛ] "
                f"{result.channel_name}\n"
            )

            f.write(
                f"  [ALIAS] "
                f"{result.cdn_alias or ''}\n"
            )

            f.write(
                f"  [MATCH] "
                f"{result.match_type}\n"
            )

            f.write(
                f"  [CONFIDENCE] "
                f"{result.confidence:.6f}\n"
            )

            f.write(
                f"  [REASON] "
                f"{result.reason}\n"
            )

            if result.streams:

                stream = result.streams[0]

                f.write(
                    f"  [NODE] "
                    f"{stream.node}\n"
                )

                f.write(
                    f"  [RULE] "
                    f"{stream.rule_name}\n"
                )

                f.write(
                    f"  [STATUS] "
                    f"{stream.http_status}\n"
                )

                f.write(
                    f"  [TIME_MS] "
                    f"{stream.response_time_ms:.2f}\n"
                )

                f.write(
                    f"  [URL] "
                    f"{stream.url}\n"
                )

            f.write(
                "-" * 70
                + "\n"
            )

    # --------------------------------------------------------
    # MACHINE REPORT
    # --------------------------------------------------------

    with open(
        MACHINE_REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            f"RUN={RUN_NUMBER}\n"
        )

        f.write(
            f"ENGINE={ENGINE_VERSION}\n"
        )

        f.write(
            f"FEATURE_SCHEMA="
            f"{FEATURE_SCHEMA_VERSION}\n\n"
        )

        for channel in channels:

            result = mapping.get(
                canonical_text(
                    channel.display_name
                )
            )

            if result is None:
                continue

            stream = (
                result.streams[0]
                if result.streams
                else None
            )

            f.write(
                f"NAME="
                f"{result.channel_name}\n"
            )

            f.write(
                f"ALIAS="
                f"{result.cdn_alias or ''}\n"
            )

            f.write(
                f"CONFIDENCE="
                f"{result.confidence:.6f}\n"
            )

            f.write(
                f"TYPE="
                f"{result.match_type}\n"
            )

            f.write(
                f"URL="
                f"{stream.url if stream else ''}\n"
            )

            f.write(
                f"STATUS="
                f"{stream.http_status if stream else 'UNKNOWN'}\n"
            )

            f.write(
                f"SOURCE={MACHINE_SOURCE}\n"
            )

            f.write(
                f"RUN={RUN_NUMBER}\n"
            )

            f.write(
                f"FOUND={generated_at}\n\n"
            )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    export_results = []

    for channel in channels:

        result = mapping.get(
            canonical_text(
                channel.display_name
            )
        )

        if result is None:
            continue

        item = asdict(
            result
        )

        item[
            "channel"
        ] = asdict(
            channel
        )

        export_results.append(
            item
        )

    json_payload = {
        "engine": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,
        "feature_schema": FEATURE_SCHEMA_VERSION,
        "model_schema": MODEL_SCHEMA_VERSION,
        "run_number": RUN_NUMBER,
        "generated_at": generated_at,
        "model": {
            "trained": bool(
                model
                and model.trained
            ),
            "training_samples": (
                model.training_samples
                if model
                else 0
            ),
            "metrics": (
                model.metrics
                if model
                else {}
            ),
        },
        "results": export_results,
    }

    with open(
        JSON_EXPORT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            json_payload,
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
            'url-tvg="'
            f'{EPG_URL}'
            '" '
            f'x-ngg-run="{RUN_NUMBER}" '
            f'x-ngg-engine="{ENGINE_VERSION}"\n'
        )

        for channel in channels:

            result = mapping.get(
                canonical_text(
                    channel.display_name
                )
            )

            if (
                result is None
                or not result.cdn_alias
                or not result.streams
            ):
                continue

            stream = result.streams[0]

            tvg_id = escape_m3u_attribute(
                channel.tvg_id
            )

            tvg_name = escape_m3u_attribute(
                channel.tvg_name
                or channel.display_name
            )

            logo = escape_m3u_attribute(
                channel.logo
            )

            display_name = (
                escape_m3u_attribute(
                    channel.display_name
                )
            )

            logo_attr = (
                f' tvg-logo="{logo}"'
                if logo
                else ""
            )

            f.write(
                '#EXTINF:-1 '
                f'tvg-id="{tvg_id}" '
                f'tvg-name="{tvg_name}"'
                f'{logo_attr},'
                f'{display_name}\n'
            )

            f.write(
                f"{stream.url}\n"
            )

    # --------------------------------------------------------
    # LEARNED REPORT
    # --------------------------------------------------------

    successful = sum(
        1
        for result in results
        if result.cdn_alias
    )

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
            f"ENGINE={ENGINE_VERSION}\n"
        )

        f.write(
            f"FEATURE_SCHEMA="
            f"{FEATURE_SCHEMA_VERSION}\n"
        )

        f.write(
            f"MODEL_SCHEMA="
            f"{MODEL_SCHEMA_VERSION}\n"
        )

        f.write(
            f"MODEL_FILE="
            f"{VERSIONED_MODEL_FILE}\n\n"
        )

        f.write(
            f"CHANNELS={len(channels)}\n"
        )

        f.write(
            f"CONFIRMED={successful}\n"
        )

        f.write(
            f"UNKNOWN="
            f"{len(channels) - successful}\n\n"
        )

        if model:

            f.write(
                "=== MODEL ===\n"
            )

            f.write(
                f"TRAINED="
                f"{model.trained}\n"
            )

            f.write(
                f"SAMPLES="
                f"{model.training_samples}\n"
            )

            f.write(
                f"CREATED="
                f"{model.created_at}\n"
            )

            f.write(
                f"CLASS_DISTRIBUTION="
                f"{json.dumps("
                f"model.class_distribution,"
                f"ensure_ascii=False"
                f")}\n"
            )

            for key, value in (
                model.metrics.items()
            ):

                f.write(
                    f"{key.upper()}="
                    f"{value:.6f}\n"
                )

            f.write("\n")

        f.write(
            "=== LEARNED ALIASES ===\n"
        )

        for channel in channels:

            result = mapping.get(
                canonical_text(
                    channel.display_name
                )
            )

            if (
                result is not None
                and result.cdn_alias
            ):

                f.write(
                    f"{result.channel_name}"
                    f" => "
                    f"{result.cdn_alias}"
                    f" | "
                    f"{result.confidence:.6f}"
                    f"\n"
                )

    LOGGER.info(
        "Сохранён HUMAN report: %s",
        HUMAN_REPORT_FILE,
    )

    LOGGER.info(
        "Сохранён MACHINE report: %s",
        MACHINE_REPORT_FILE,
    )

    LOGGER.info(
        "Сохранён JSON export: %s",
        JSON_EXPORT_FILE,
    )

    LOGGER.info(
        "Сохранён M3U: %s",
        PLAYLIST_FILE,
    )

    LOGGER.info(
        "Сохранён learning report: %s",
        LEARNED_REPORT_FILE,
    )


# ============================================================
# TRAINING
# ============================================================

def retrain_ml_model(
    db: Optional[
        Database
    ] = None,
) -> Optional[
    EnsembleModel
]:

    if not HAS_ML:

        LOGGER.warning(
            "Scikit-learn не установлен. "
            "ML обучение пропущено."
        )

        return None

    db = db or Database()

    rows = db.get_training_data()

    LOGGER.info(
        "ML: накоплено наблюдений: %d",
        len(rows),
    )

    if len(rows) < (
        MIN_TRAINING_SAMPLES
    ):

        LOGGER.info(
            "ML: недостаточно данных "
            "(%d/%d).",
            len(rows),
            MIN_TRAINING_SAMPLES,
        )

        return None

    successes = sum(
        int(
            row.get(
                "success",
                0,
            )
        )
        for row in rows
    )

    failures = (
        len(rows)
        - successes
    )

    LOGGER.info(
        "ML DATA: SUCCESS=%d FAIL=%d",
        successes,
        failures,
    )

    if (
        successes < MIN_CLASS_SAMPLES
        or failures < MIN_CLASS_SAMPLES
    ):

        LOGGER.warning(
            "Недостаточно примеров "
            "одного из классов."
        )

        return None

    model = EnsembleModel()

    try:

        metrics = model.fit(
            rows
        )

        # Сначала версия запуска.
        model.save(
            VERSIONED_MODEL_FILE
        )

        # Затем latest.
        model.save(
            MODEL_FILE
        )

        LOGGER.info(
            "ML versioned model: %s",
            VERSIONED_MODEL_FILE,
        )

        LOGGER.info(
            "ML latest model: %s",
            MODEL_FILE,
        )

        LOGGER.info(
            "=== ML ENSEMBLE ==="
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

    except ValueError as exc:

        LOGGER.warning(
            "ML обучение невозможно: %s",
            exc,
        )

        return None

    except Exception as exc:

        LOGGER.exception(
            "Критическая ошибка ML: %s",
            exc,
        )

        return None


# ============================================================
# PIPELINE
# ============================================================

def run_pipeline() -> None:

    LOGGER.info(
        "========================================"
    )

    LOGGER.info(
        "%s",
        ENGINE_NAME,
    )

    LOGGER.info(
        "ENGINE VERSION: %s",
        ENGINE_VERSION,
    )

    LOGGER.info(
        "FEATURE SCHEMA: %s",
        FEATURE_SCHEMA_VERSION,
    )

    LOGGER.info(
        "NGG AI RUN #%d",
        RUN_NUMBER,
    )

    LOGGER.info(
        "========================================"
    )

    db = Database()

    # --------------------------------------------------------
    # EPG KNOWLEDGE
    # --------------------------------------------------------

    epg_kb = (
        EPGKnowledgeBase()
    )

    epg_kb.load()

    # --------------------------------------------------------
    # HISTORICAL JSON
    # --------------------------------------------------------

    all_json_records = (
        load_all_json_records()
    )

    if all_json_records:

        imported = (
            db.import_historical_records(
                all_json_records
            )
        )

        LOGGER.info(
            "[JSON] Новых записей "
            "в Knowledge Base: %d",
            imported,
        )

    # --------------------------------------------------------
    # EXISTING MODEL
    # --------------------------------------------------------

    ml_model = (
        EnsembleModel.load()
    )

    if ml_model:

        LOGGER.info(
            "Загружена совместимая "
            "предыдущая ML-модель."
        )

        LOGGER.info(
            "Модель обучена на %d "
            "наблюдениях.",
            ml_model.training_samples,
        )

    else:

        LOGGER.info(
            "Совместимая ML-модель "
            "не найдена."
        )

    # --------------------------------------------------------
    # CDN
    # --------------------------------------------------------

    scanner = MultiNodeScanner(
        db=db,
        epg_kb=epg_kb,
        ml_model=ml_model,
    )

    scanner.ping_nodes()

    # --------------------------------------------------------
    # EPG
    # --------------------------------------------------------

    channels = (
        fetch_epg_channels()
    )

    results_by_name: Dict[
        str,
        AliasMatch,
    ] = {}

    LOGGER.info(
        "Старт параллельного "
        "CDN/ML сканирования..."
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_WORKER_THREADS
    ) as executor:

        future_to_channel = {
            executor.submit(
                scanner.probe_channel,
                channel,
            ): channel
            for channel in channels
        }

        for future in concurrent.futures.as_completed(
            future_to_channel
        ):

            channel = future_to_channel[
                future
            ]

            try:

                result = (
                    future.result()
                )

            except Exception as exc:

                LOGGER.exception(
                    "Ошибка сканирования "
                    "канала %s: %s",
                    channel.display_name,
                    exc,
                )

                result = AliasMatch(
                    channel_name=(
                        channel.display_name
                    ),
                    normalized_name=(
                        channel.display_name
                    ),
                    cdn_alias=None,
                    match_type="ERROR",
                    confidence=0.0,
                    reason=str(exc),
                    candidates=[],
                    streams=[],
                )

            results_by_name[
                canonical_text(
                    channel.display_name
                )
            ] = result

            if result.cdn_alias:

                LOGGER.info(
                    "[+] %s -> %s "
                    "(confidence=%.4f)",
                    result.channel_name,
                    result.cdn_alias,
                    result.confidence,
                )

    # --------------------------------------------------------
    # CRITICAL:
    # RESTORE ORIGINAL CHANNEL ORDER
    # --------------------------------------------------------

    results: List[
        AliasMatch
    ] = []

    for channel in channels:

        key = canonical_text(
            channel.display_name
        )

        result = results_by_name.get(
            key
        )

        if result is None:

            result = AliasMatch(
                channel_name=(
                    channel.display_name
                ),
                normalized_name=(
                    channel.display_name
                ),
                cdn_alias=None,
                match_type="MISSING",
                confidence=0.0,
                reason=(
                    "Результат сканирования "
                    "отсутствует."
                ),
                candidates=[],
                streams=[],
            )

        results.append(
            result
        )

    # --------------------------------------------------------
    # RETRAIN
    # --------------------------------------------------------

    LOGGER.info(
        "Запуск ML обучения "
        "по ВСЕЙ накопленной истории..."
    )

    trained_model = (
        retrain_ml_model(
            db
        )
    )

    if trained_model:

        LOGGER.info(
            "Новая ML модель "
            "успешно построена."
        )

    else:

        LOGGER.info(
            "Новая ML модель "
            "не создана."
        )

    # --------------------------------------------------------
    # REPORTS
    # --------------------------------------------------------

    save_all_reports(
        channels=channels,
        results=results,
        model=trained_model
        or ml_model,
    )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    stats = db.get_statistics()

    LOGGER.info(
        "========================================"
    )

    LOGGER.info(
        "RUN #%d FINISHED",
        RUN_NUMBER,
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
        "DB live: %d",
        stats["live"],
    )

    LOGGER.info(
        "DB historical: %d",
        stats["historical"],
    )

    LOGGER.info(
        "DB runs: %d",
        stats["runs"],
    )

    LOGGER.info(
        "Learned aliases: %d",
        stats["learned_aliases"],
    )

    LOGGER.info(
        "========================================"
    )


# ============================================================
# STATS
# ============================================================

def show_stats() -> None:

    db = Database()

    stats = (
        db.get_statistics()
    )

    print()
    print(
        "=== СТАТИСТИКА KNOWLEDGE BASE ==="
    )

    print(
        f"Engine:            "
        f"{ENGINE_NAME}"
    )

    print(
        f"Engine version:    "
        f"{ENGINE_VERSION}"
    )

    print(
        f"Feature schema:    "
        f"{FEATURE_SCHEMA_VERSION}"
    )

    print(
        f"Всего проверок:    "
        f"{stats['total']}"
    )

    print(
        f"Успешных:          "
        f"{stats['successful']}"
    )

    print(
        f"Неуспешных:        "
        f"{stats['failed']}"
    )

    print(
        f"Live scan:         "
        f"{stats['live']}"
    )

    print(
        f"Historical JSON:   "
        f"{stats['historical']}"
    )

    print(
        f"Запусков:          "
        f"{stats['runs']}"
    )

    print(
        f"Learned aliases:   "
        f"{stats['learned_aliases']}"
    )

    model = (
        EnsembleModel.load()
    )

    if model:

        print()
        print(
            "=== ПОСЛЕДНЯЯ ML-МОДЕЛЬ ==="
        )

        print(
            f"Version:           "
            f"{model.VERSION}"
        )

        print(
            f"Feature schema:    "
            f"{model.feature_schema}"
        )

        print(
            f"Model schema:      "
            f"{model.model_schema}"
        )

        print(
            f"Training samples:  "
            f"{model.training_samples}"
        )

        print(
            f"Created:           "
            f"{model.created_at}"
        )

        print(
            f"Classes:           "
            f"{model.class_distribution}"
        )

        for key, value in (
            model.metrics.items()
        ):

            print(
                f"{key:12s}: "
                f"{value:.6f}"
            )

    else:

        print()
        print(
            "ML latest model: отсутствует "
            "или несовместима."
        )

    aliases = db.get_top_aliases(
        limit=20
    )

    if aliases:

        print()
        print(
            "=== TOP LEARNED ALIASES ==="
        )

        for item in aliases:

            print(
                f"{item['channel_name']} "
                f"=> "
                f"{item['cdn_alias']} "
                f"| confidence="
                f"{item['confidence']:.4f} "
                f"| hits="
                f"{item['hit_count']}"
            )


# ============================================================
# CLI
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            f"{ENGINE_NAME} "
            f"{ENGINE_VERSION}"
        )
    )

    parser.add_argument(
        "--scan",
        action="store_true",
        help=(
            "Полное сканирование, "
            "импорт истории и обучение"
        ),
    )

    parser.add_argument(
        "--train",
        action="store_true",
        help=(
            "Переобучить ML по всей "
            "накопленной истории"
        ),
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help=(
            "Показать состояние "
            "Knowledge Base и ML"
        ),
    )

    parser.add_argument(
        "--import-json",
        action="store_true",
        help=(
            "Импортировать исторические "
            "JSON без CDN сканирования"
        ),
    )

    args = parser.parse_args()

    if args.scan:

        run_pipeline()

    elif args.train:

        db = Database()

        retrain_ml_model(
            db
        )

    elif args.stats:

        show_stats()

    elif args.import_json:

        db = Database()

        records = (
            load_all_json_records()
        )

        imported = (
            db.import_historical_records(
                records
            )
        )

        print(
            f"Импортировано новых записей: "
            f"{imported}"
        )

    else:

        run_pipeline()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()