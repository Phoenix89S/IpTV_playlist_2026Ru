#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IPTV FALLBACK / AUTO RECOVERY
=============================

Назначение:
    Автоматическое обнаружение мёртвых IPTV-потоков и восстановление
    вещания путём поиска/генерации и проверки альтернативных потоков.

Основная схема:

    PLAYLIST
       |
       +--> проверка существующих потоков
       |
       +--> dead channel
                |
                +--> known aliases
                +--> NGENIX hosts / s7xxxx
                +--> RT / Ростелеком HLS
                +--> generated CH_* paths
                +--> external public M3U sources
                +--> public discovery pages
                |
                +--> STRICT CHANNEL MATCH
                |
                +--> HLS CHECK
                |
                +--> FALLBACK
                         |
                         +--> megred_auto.m3u
                         +--> fallback_report.txt
                         +--> fallback_state.json

Примеры:

    python3 IpTV_fallback.py \
        --playlist input.m3u \
        --output megred_auto.m3u

    python3 IpTV_fallback.py \
        --playlist input.m3u \
        --output megred_auto.m3u \
        --watch

    python3 IpTV_fallback.py \
        --playlist input.m3u \
        --output megred_auto.m3u \
        --workers 32

ВАЖНО:
    Этот модуль НЕ считает ссылку рабочей только потому, что она найдена.
    Каждый кандидат проходит проверку.

    Также URL не считается заменой только из-за похожего названия.
    Для fallback требуется совпадение tvg-id, точного alias или достаточно
    строгого имени канала.

    Например:
        "H1 Исторический"
    не может автоматически получить:
        "Первый канал"
"""

import argparse
import concurrent.futures
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


# ============================================================
# CONFIG
# ============================================================

DEFAULT_WORKERS = 32
DEFAULT_TIMEOUT = 5
DEFAULT_FETCH_TIMEOUT = 15
DEFAULT_WATCH_INTERVAL = 60

OUTPUT_REPORT = "fallback_report.txt"
OUTPUT_STATE = "fallback_state.json"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# ============================================================
# USER AGENTS
# ============================================================

UA_GENERIC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)

UA_WINK_ZABAVA = (
    "Wink/1.0 "
    "(Linux; Android 10; TV)"
)

UA_HLS = (
    "Mozilla/5.0 "
    "(compatible; IPTV-Fallback/1.0; +https://github.com/)"
)


# ============================================================
# KNOWN EXTERNAL SOURCES
# ============================================================

EXTERNAL_SOURCES = [
    # Public IPTV repositories / playlists.
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://naggdd.github.io/iptv/ru.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_russia.m3u8",
]


# ============================================================
# NGENIX HOSTS
# ============================================================

# Известные/используемые NGENIX entry points.
# Список можно расширять без изменения основной логики.
NGENIX_HOSTS = [
    "rt-nw-klgr-htlive.cdn.ngenix.net",
]

# s7xxxx hosts.
# Допускается автоматическое расширение диапазона через CLI:
# --ngenix-host s70790.cdn.ngenix.net
NGENIX_S_HOSTS = [
    "s70790.cdn.ngenix.net",
]


# ============================================================
# ROSTELECOM / RT HLS HOSTS
# ============================================================

RT_HOSTS = [
    "hlsstr01.svc.iptv.rt.ru",
]


# ============================================================
# CHANNEL ALIASES
# ============================================================

CHANNEL_ALIASES = {
    "karusel": [
        "карусель",
        "karusel",
    ],

    "rentv": [
        "рен тв",
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
        "viju_nature",
        "viasat nature",
        "viasat_nature",
    ],

    "viasat_explore": [
        "viju explore",
        "viju_explore",
        "viasat explore",
        "viasat_explore",
    ],

    "viasat_history": [
        "viju history",
        "viju_history",
        "viasat history",
        "viasat_history",
    ],

    "tiji": [
        "tiji",
        "тижи",
        "tiji tv",
    ],

    "gulli": [
        "gulli",
        "гулли",
    ],

    "nickelodeon": [
        "nickelodeon",
    ],

    "nicktoons": [
        "nicktoons",
        "nick toons",
    ],

    "baby_tv": [
        "baby tv",
        "babytv",
        "baby_tv",
    ],

    "match_planeta": [
        "матч планета",
        "матч! планета",
        "match planeta",
        "match! planeta",
        "match_planeta",
    ],

    "fightbox": [
        "fightbox",
        "fight box",
    ],

    "trace_sport_stars": [
        "trace sport",
        "trace sport stars",
        "trace_sport",
        "trace_sport_stars",
    ],

    "amedia_1": [
        "amedia 1",
        "a1",
        "amedia1",
    ],

    "amedia_2": [
        "amedia 2",
        "a2",
        "amedia2",
    ],

    "amedia_premium_hd": [
        "amedia premium",
        "amedia premium hd",
        "amedia_premium_hd",
    ],

    "amedia_hit": [
        "amedia hit",
        "amedia hit hd",
        "amedia_hit",
        "amediahithd",
    ],

    "filmbox": [
        "filmbox",
        "film box",
    ],

    "filmbox_arthouse": [
        "filmbox arthouse",
        "film box arthouse",
        "filmbox_arthouse",
    ],

    "amc": [
        "amc",
    ],

    "dom_kino": [
        "дом кино",
        "dom kino",
        "dom_kino",
    ],

    "dom_kino_premium_hd": [
        "дом кино премиум",
        "дом кино премиум hd",
        "dom kino premium",
        "dom_kino_premium_hd",
    ],

    "evrokino": [
        "еврокино",
        "evrokino",
        "eurokino",
    ],

    "illusion_plus": [
        "иллюзион",
        "иллюзион+",
        "illusion",
        "illusion plus",
        "illusion_plus",
    ],

    "mir_seriala": [
        "мир сериала",
        "mir seriala",
        "mir_seriala",
    ],

    "tv_xxi": [
        "тв xxi",
        "tv xxi",
        "тв 21",
        "tv 21",
        "tvxxi",
        "tv_xxi",
    ],

    "365_dney_tv": [
        "365 дней",
        "365 дней тв",
        "365",
        "365 dney",
        "365 dney tv",
        "365_dney_tv",
    ],

    "galaxy": [
        "galaxy",
        "галактика",
    ],

    "sony_channel": [
        "sony channel",
        "sony",
        "sony_channel",
    ],

    "sony_turbo": [
        "sony turbo",
        "sony_turbo",
    ],

    "history_2": [
        "history 2",
        "history2",
        "history_2",
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
        "da_vinci",
        "davinci",
        "davincilearning",
    ],

    "kitchen_tv": [
        "kitchen tv",
        "kitchen",
        "kitchen_tv",
    ],

    "mezzo": [
        "mezzo",
    ],

    "tnt_music": [
        "тнт music",
        "тнт мьюзик",
        "tnt music",
        "tnt_music",
        "tntmusic",
    ],

    "rtvi": [
        "rtvi",
        "ртви",
    ],

    "tv5_monde": [
        "tv5 monde",
        "tv5monde",
        "tv5_monde",
    ],

    "fashion_tv": [
        "fashion tv",
        "fashion",
        "fashiontv",
        "fashion_tv",
    ],

    "viasat_sport": [
        "viju plus sport",
        "viju+ sport",
        "viasat sport",
        "viasat_sport",
        "viasatplus sport",
    ],

    "vip_premiere": [
        "viju plus premiere",
        "viju+ premiere",
        "vip premiere",
        "viju premiere",
        "vip_premiere",
    ],

    "vip_megahit": [
        "viju plus megahit",
        "viju+ megahit",
        "vip megahit",
        "viju megahit",
        "vip_megahit",
    ],

    "vip_comedy": [
        "viju plus comedy",
        "viju+ comedy",
        "vip comedy",
        "viju comedy",
        "vip_comedy",
    ],

    "vip_serial": [
        "viju plus serial",
        "viju+ serial",
        "vip serial",
        "viju serial",
        "vip_serial",
    ],
}


# ============================================================
# KNOWN PATH ALIASES
# ============================================================

# tvg-id -> canonical CH names.
#
# Это отдельный слой от названий.
# Он нужен для преобразования:
#
# tvg-id
#    ->
# CH_...
#    ->
# /hls/CH_.../variant.m3u8
#
# и:
#
# tvg-id
#    ->
# CH_R01_...
#
CHANNEL_PATH_ALIASES = {
    "karusel": [
        "CH_KARUSEL",
        "CH_R01_KARUSEL",
    ],

    "rentv": [
        "CH_RENTV",
        "CH_R01_RENTV",
    ],

    "tv3": [
        "CH_TV3",
        "CH_R01_TV3",
    ],

    "mir": [
        "CH_MIR",
        "CH_R01_MIR",
    ],

    "ocean_tv": [
        "CH_OCEANTV",
        "CH_R01_OCEANTV",
    ],

    "viasat_nature": [
        "CH_VIASATNATUREHD",
        "CH_R01_VIASATNATUREHD",
    ],

    "viasat_explore": [
        "CH_VIASATEXPLOREHD",
        "CH_R01_VIASATEXPLOREHD",
    ],

    "viasat_history": [
        "CH_VIASATHISTORYHD",
        "CH_R01_VIASATHISTORYHD",
    ],

    "tiji": [
        "CH_TIJI",
        "CH_R01_TIJI",
    ],

    "gulli": [
        "CH_GULLI",
        "CH_R01_GULLI",
    ],

    "nickelodeon": [
        "CH_NICKEL",
        "CH_R01_NICKEL",
    ],

    "nicktoons": [
        "CH_NICKTOONS",
        "CH_R01_NICKTOONS",
    ],

    "baby_tv": [
        "CH_BABYTV",
        "CH_R01_BABYTV",
    ],

    "match_planeta": [
        "CH_MATCHPLANETA",
        "CH_R01_MATCHPLANETA",
    ],

    "fightbox": [
        "CH_FIGHTBOX",
        "CH_R01_FIGHTBOX",
    ],

    "trace_sport_stars": [
        "CH_TRACESPORT",
        "CH_R01_TRACESPORT",
    ],

    "amedia_1": [
        "CH_AMEDIA1",
        "CH_R01_AMEDIA1",
    ],

    "amedia_2": [
        "CH_AMEDIA2",
        "CH_R01_AMEDIA2",
    ],

    "amedia_premium_hd": [
        "CH_AMEDIAPREMIUMHD",
        "CH_R01_AMEDIAPREMIUMHD",
    ],

    "amedia_hit": [
        "CH_AMEDIAHITHD",
        "CH_R01_AMEDIAHITHD",
    ],

    "filmbox": [
        "CH_FILMBOX",
        "CH_R01_FILMBOX",
    ],

    "filmbox_arthouse": [
        "CH_FILMBOXARTHOUSE",
        "CH_R01_FILMBOXARTHOUSE",
    ],

    "amc": [
        "CH_AMC",
        "CH_R01_AMC",
    ],

    "dom_kino": [
        "CH_DOMKINO",
        "CH_R01_DOMKINO",
    ],

    "dom_kino_premium_hd": [
        "CH_DOMKINOPREMIUMHD",
        "CH_R01_DOMKINOPREMIUMHD",
    ],

    "evrokino": [
        "CH_EUROKINO",
        "CH_R01_EUROKINO",
    ],

    "illusion_plus": [
        "CH_ILLUSIONPLUS",
        "CH_R01_ILLUSIONPLUS",
    ],

    "mir_seriala": [
        "CH_MIRSERIALA",
        "CH_R01_MIRSERIALA",
    ],

    "tv_xxi": [
        "CH_TVXXI",
        "CH_R01_TVXXI",
    ],

    "365_dney_tv": [
        "CH_365DNEYTV",
        "CH_R01_365DNEYTV",
    ],

    "galaxy": [
        "CH_GALAXY",
        "CH_R01_GALAXY",
    ],

    "sony_channel": [
        "CH_SONYCHANNEL",
        "CH_R01_SONYCHANNEL",
    ],

    "sony_turbo": [
        "CH_SONYTURBO",
        "CH_R01_SONYTURBO",
    ],

    "history_2": [
        "CH_HISTORY2",
        "CH_R01_HISTORY2",
    ],

    "docubox": [
        "CH_DOCUBOX",
        "CH_R01_DOCUBOX",
    ],

    "nostalgia": [
        "CH_NOSTALGIA",
        "CH_R01_NOSTALGIA",
    ],

    "da_vinci": [
        "CH_DAVINCILEARNING",
        "CH_R01_DAVINCILEARNING",
    ],

    "kitchen_tv": [
        "CH_KITCHENTV",
        "CH_R01_KITCHENTV",
    ],

    "mezzo": [
        "CH_MEZZO",
        "CH_R01_MEZZO",
    ],

    "tnt_music": [
        "CH_TNTMUSICHD",
        "CH_R01_TNTMUSICHD",
    ],

    "rtvi": [
        "CH_RTVI",
        "CH_R01_RTVI",
    ],

    "tv5_monde": [
        "CH_TV5MONDE",
        "CH_R01_TV5MONDE",
    ],

    "fashion_tv": [
        "CH_FASHIONTVHD",
        "CH_R01_FASHIONTVHD",
    ],

    "viasat_sport": [
        "CH_VIASATSPORT",
        "CH_R01_VIASATSPORT",
    ],

    "vip_premiere": [
        "CH_VIJUPREMIERE",
        "CH_R01_VIJUPREMIERE",
    ],

    "vip_megahit": [
        "CH_VIJUMEGAHIT",
        "CH_R01_VIJUMEGAHIT",
    ],

    "vip_comedy": [
        "CH_VIJUCOMEDY",
        "CH_R01_VIJUCOMEDY",
    ],

    "vip_serial": [
        "CH_VIJUSERIAL",
        "CH_R01_VIJUSERIAL",
    ],
}


# ============================================================
# HTTP
# ============================================================

def headers_for_url(url):
    """
    Выбор User-Agent.

    Для NGENIX / Zabava используется Wink UA.
    Для остальных HLS используется обычный IPTV UA.
    """
    host = urlparse(url).hostname or ""
    host = host.lower()

    if (
        "ngenix.net" in host
        or "cdn.ngenix.net" in host
        or "zabava" in host
    ):
        return {
            "User-Agent": UA_WINK_ZABAVA,
            "Accept": "*/*",
            "Connection": "close",
        }

    return {
        "User-Agent": UA_HLS,
        "Accept": "*/*",
        "Connection": "close",
    }


def fetch_url(url, timeout=DEFAULT_FETCH_TIMEOUT):
    """
    Загрузка внешнего M3U / страницы.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA_GENERIC,
                "Accept": "*/*",
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout,
            context=SSL_CTX,
        ) as resp:
            data = resp.read()

        return data.decode("utf-8", errors="ignore")

    except Exception:
        return None


# ============================================================
# STREAM CHECK
# ============================================================

def check_stream(url, timeout=DEFAULT_TIMEOUT):
    """
    Проверка HLS/M3U8.

    Возвращает:
        {
            alive,
            status,
            reason,
            content_type
        }

    Мы НЕ скачиваем весь поток.
    Читаем только небольшой кусок.

    Это существенно быстрее для большого количества кандидатов.
    """

    started = time.monotonic()

    try:
        req = urllib.request.Request(
            url,
            headers=headers_for_url(url),
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout,
            context=SSL_CTX,
        ) as resp:

            status = getattr(resp, "status", 200)
            content_type = resp.headers.get("Content-Type", "")

            if status < 200 or status >= 400:
                return {
                    "alive": False,
                    "status": status,
                    "reason": f"http_{status}",
                    "content_type": content_type,
                    "latency": round(
                        time.monotonic() - started,
                        3,
                    ),
                }

            data = resp.read(4096)

            if not data:
                return {
                    "alive": False,
                    "status": status,
                    "reason": "empty_response",
                    "content_type": content_type,
                    "latency": round(
                        time.monotonic() - started,
                        3,
                    ),
                }

            lower = data.lower()

            # Нормальный HLS playlist.
            if (
                b"#extm3u" in lower
                or b"#extinf" in lower
                or b"#ext-x-" in lower
            ):
                return {
                    "alive": True,
                    "status": status,
                    "reason": "hls_ok",
                    "content_type": content_type,
                    "latency": round(
                        time.monotonic() - started,
                        3,
                    ),
                }

            # Некоторые CDN возвращают binary/media data.
            if len(data) >= 100:
                return {
                    "alive": True,
                    "status": status,
                    "reason": "data_ok",
                    "content_type": content_type,
                    "latency": round(
                        time.monotonic() - started,
                        3,
                    ),
                }

            return {
                "alive": False,
                "status": status,
                "reason": "short_response",
                "content_type": content_type,
                "latency": round(
                    time.monotonic() - started,
                    3,
                ),
            }

    except urllib.error.HTTPError as exc:
        return {
            "alive": False,
            "status": exc.code,
            "reason": f"http_{exc.code}",
            "content_type": "",
            "latency": round(
                time.monotonic() - started,
                3,
            ),
        }

    except urllib.error.URLError as exc:
        return {
            "alive": False,
            "status": 0,
            "reason": f"url_error:{exc.reason}",
            "content_type": "",
            "latency": round(
                time.monotonic() - started,
                3,
            ),
        }

    except Exception as exc:
        return {
            "alive": False,
            "status": 0,
            "reason": type(exc).__name__,
            "content_type": "",
            "latency": round(
                time.monotonic() - started,
                3,
            ),
        }


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_text(value):
    """
    Нормализация имени для безопасного сравнения.

    Не используется как единственный критерий.
    """
    if not value:
        return ""

    value = value.lower().strip()

    value = value.replace("ё", "е")
    value = value.replace("_", " ")
    value = value.replace("-", " ")

    value = re.sub(r"\[[^\]]*\]", " ", value)
    value = re.sub(r"\([^)]*\)", " ", value)

    value = re.sub(r"[^\w\sа-яА-ЯёЁ]+", " ", value)

    value = re.sub(r"\s+", " ", value)

    return value.strip()


def canonical_id(value):
    """
    Канонический tvg-id.
    """
    return normalize_text(value).replace(" ", "_")


def clean_channel_name(name):
    """
    Убирает номер и технические хвосты.
    """
    if not name:
        return ""

    name = re.sub(r"^\s*\d+\.\s*", "", name)
    name = re.sub(
        r"\s*\[(?:калининград|rt федеральный|федеральный)\]\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    )

    return name.strip()


# ============================================================
# M3U PARSER
# ============================================================

def parse_m3u(text):
    """
    Парсер M3U.

    Возвращает список каналов, а не dict только по tvg-id.
    Это важно: один tvg-id может иметь несколько оригинальных потоков.
    """

    channels = []

    current = None

    for raw_line in text.splitlines():

        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            tvg_match = re.search(
                r'tvg-id="([^"]*)"',
                line,
                flags=re.IGNORECASE,
            )

            group_match = re.search(
                r'group-title="([^"]*)"',
                line,
                flags=re.IGNORECASE,
            )

            name = ""

            if "," in line:
                name = line.split(",", 1)[1].strip()

            name = clean_channel_name(name)

            tvg_id = (
                tvg_match.group(1).strip()
                if tvg_match
                else canonical_id(name)
            )

            current = {
                "tvg_id": tvg_id,
                "canonical_id": canonical_id(tvg_id),
                "name": name,
                "group": (
                    group_match.group(1).strip()
                    if group_match
                    else ""
                ),
                "streams": [],
            }

            channels.append(current)

        elif (
            current
            and not line.startswith("#")
            and (
                line.startswith("http://")
                or line.startswith("https://")
            )
        ):

            current["streams"].append(
                {
                    "url": line,
                    "alive": False,
                    "source": "original",
                    "reason": "not_checked",
                    "score": 1000,
                }
            )

    return channels


# ============================================================
# ALIAS / PATH RESOLUTION
# ============================================================

def aliases_for_channel(channel):
    """
    Возвращает строгий набор alias.

    Важный момент:
        tvg-id имеет максимальный приоритет.

    Если tvg-id неизвестен, используем только имя.
    """

    tid = canonical_id(channel["tvg_id"])
    name = normalize_text(channel["name"])

    aliases = set()

    if tid in CHANNEL_ALIASES:
        aliases.update(
            normalize_text(x)
            for x in CHANNEL_ALIASES[tid]
        )

    aliases.add(normalize_text(channel["tvg_id"]))

    if name:
        aliases.add(name)

    return {
        x for x in aliases
        if x
    }


def path_aliases_for_channel(channel):
    """
    Возвращает CH_* варианты.

    Сначала используем явно известные варианты.
    """

    tid = canonical_id(channel["tvg_id"])

    result = []

    if tid in CHANNEL_PATH_ALIASES:
        result.extend(CHANNEL_PATH_ALIASES[tid])

    # Для неизвестных tvg-id безопасный автоматический вариант.
    #
    # ВАЖНО:
    # это только кандидат.
    # Он всё равно проходит строгую идентификацию и stream check.
    if tid and tid not in CHANNEL_PATH_ALIASES:
        generated = re.sub(
            r"[^A-Z0-9]+",
            "",
            tid.upper(),
        )

        if generated:
            result.append(f"CH_{generated}")
            result.append(f"CH_R01_{generated}")

    return list(dict.fromkeys(result))


# ============================================================
# CHANNEL MATCHING
# ============================================================

def exact_token_match(a, b):
    """
    Строгое сравнение нормализованных значений.
    """
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return False

    return a == b


def channel_match_score(channel, candidate_name="", candidate_tvg_id=""):
    """
    СТРОГИЙ scoring.

    Чем выше score, тем лучше.

    1000 = exact tvg-id
     900 = exact known alias
     800 = exact channel name
     700 = exact normalized candidate tvg-id

    0 = нельзя использовать.

    ВАЖНО:
    substring matching намеренно НЕ используется.

    Именно это предотвращает ситуацию:

        H1 Исторический
              ->
        Первый канал

    """

    tid = canonical_id(channel["tvg_id"])

    candidate_tid = canonical_id(candidate_tvg_id)

    aliases = aliases_for_channel(channel)

    candidate_name_norm = normalize_text(candidate_name)

    # 1. Exact tvg-id.
    if candidate_tid and candidate_tid == tid:
        return 1000

    # 2. Exact alias.
    if candidate_name_norm in aliases:
        return 900

    # 3. Exact original channel name.
    if exact_token_match(
        candidate_name_norm,
        normalize_text(channel["name"]),
    ):
        return 800

    return 0


def url_path_match_score(channel, url):
    """
    Сравнение CH_* path с известными alias.

    Возвращает score:
        750 / 700
        или 0
    """

    path = urlparse(url).path.upper()

    if not path:
        return 0

    aliases = [
        x.upper()
        for x in path_aliases_for_channel(channel)
    ]

    for alias in aliases:

        expected = f"/HLS/{alias}/VARIANT.M3U8"

        if path.rstrip("/").upper() == expected.rstrip("/").upper():
            if alias.startswith("CH_R01_"):
                return 750

            return 700

    return 0


# ============================================================
# CANDIDATE
# ============================================================

def make_candidate(
    channel,
    url,
    source,
    candidate_name="",
    candidate_tvg_id="",
    priority=0,
):
    """
    Формирует кандидата.

    Кандидат не считается рабочим до check_stream().
    """

    score = channel_match_score(
        channel,
        candidate_name=candidate_name,
        candidate_tvg_id=candidate_tvg_id,
    )

    path_score = url_path_match_score(
        channel,
        url,
    )

    score = max(
        score,
        path_score,
    )

    return {
        "channel": channel["tvg_id"],
        "name": channel["name"],
        "url": url,
        "source": source,
        "candidate_name": candidate_name,
        "candidate_tvg_id": candidate_tvg_id,
        "score": score + priority,
        "verified": False,
        "alive": False,
        "reason": "not_checked",
    }


# ============================================================
# URL GENERATION
# ============================================================

def generate_ngenix_urls(channel, ngenix_hosts):
    """
    Генерирует полный набор NGENIX URL.

    Пример:

        https://rt-nw-klgr-htlive.cdn.ngenix.net/
            hls/CH_R01_TV3/variant.m3u8

    и:

        https://s70790.cdn.ngenix.net/
            hls/CH_R01_TV3/variant.m3u8

    Также создаём HTTP/HTTPS.

    Внешняя проверка определит, какой вариант реально существует.
    """

    urls = []

    paths = path_aliases_for_channel(channel)

    for host in ngenix_hosts:

        host = host.strip()

        if not host:
            continue

        if "://" in host:
            parsed = urlparse(host)
            hostname = parsed.netloc
        else:
            hostname = host

        for path_alias in paths:

            path = (
                f"/hls/"
                f"{path_alias}/"
                f"variant.m3u8"
            )

            urls.append(
                f"https://{hostname}{path}"
            )

            urls.append(
                f"http://{hostname}{path}"
            )

    return list(dict.fromkeys(urls))


def generate_rt_urls(channel, rt_hosts):
    """
    Генерирует Ростелекомовские HLS URL.

    Например:

        http://hlsstr01.svc.iptv.rt.ru/
        hls/CH_SONYTURBO/variant.m3u8
    """

    urls = []

    paths = path_aliases_for_channel(channel)

    for host in rt_hosts:

        host = host.strip()

        if not host:
            continue

        if "://" in host:
            parsed = urlparse(host)
            hostname = parsed.netloc
        else:
            hostname = host

        for path_alias in paths:

            path = (
                f"/hls/"
                f"{path_alias}/"
                f"variant.m3u8"
            )

            urls.append(
                f"http://{hostname}{path}"
            )

            urls.append(
                f"https://{hostname}{path}"
            )

    return list(dict.fromkeys(urls))


# ============================================================
# EXTERNAL M3U SEARCH
# ============================================================

def parse_external_candidates(
    text,
    channel,
    source_url,
):
    """
    Поиск кандидатов во внешнем M3U.

    В отличие от старой версии:
        НЕ используем "if alias in name".

    Только точные совпадения.

    Это критически важно для failover.
    """

    candidates = []

    current_name = ""
    current_tvg_id = ""

    for raw_line in text.splitlines():

        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            current_name = ""
            current_tvg_id = ""

            tvg_match = re.search(
                r'tvg-id="([^"]*)"',
                line,
                flags=re.IGNORECASE,
            )

            if tvg_match:
                current_tvg_id = tvg_match.group(1)

            if "," in line:
                current_name = clean_channel_name(
                    line.split(",", 1)[1].strip()
                )

            continue

        if (
            line.startswith("http://")
            or line.startswith("https://")
        ):

            score = channel_match_score(
                channel,
                candidate_name=current_name,
                candidate_tvg_id=current_tvg_id,
            )

            if score > 0:

                candidates.append(
                    make_candidate(
                        channel,
                        line,
                        source=source_url,
                        candidate_name=current_name,
                        candidate_tvg_id=current_tvg_id,
                        priority=score,
                    )
                )

            current_name = ""
            current_tvg_id = ""

    return candidates


# ============================================================
# EXTERNAL DISCOVERY
# ============================================================

def load_external_sources(source_urls):
    """
    Быстрая параллельная загрузка M3U.
    """

    loaded = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(8, max(1, len(source_urls)))
    ) as pool:

        futures = {
            pool.submit(fetch_url, url): url
            for url in source_urls
        }

        for future in concurrent.futures.as_completed(
            futures
        ):

            source = futures[future]

            try:
                text = future.result()
            except Exception:
                text = None

            if text:
                loaded.append(
                    {
                        "url": source,
                        "text": text,
                    }
                )

    return loaded


# ============================================================
# NGENIX CANDIDATES
# ============================================================

def generated_candidates(
    channel,
    ngenix_hosts,
    rt_hosts,
):
    """
    Генерация кандидатов непосредственно из известных
    CDN naming/path схем.
    """

    candidates = []

    for url in generate_ngenix_urls(
        channel,
        ngenix_hosts,
    ):

        candidates.append(
            make_candidate(
                channel,
                url,
                source="generated_ngenix",
                priority=300,
            )
        )

    for url in generate_rt_urls(
        channel,
        rt_hosts,
    ):

        candidates.append(
            make_candidate(
                channel,
                url,
                source="generated_rostelecom",
                priority=250,
            )
        )

    return candidates


# ============================================================
# DEDUP
# ============================================================

def deduplicate_candidates(candidates):
    """
    Убирает дубликаты URL.
    """

    result = []
    seen = set()

    for candidate in candidates:

        url = candidate["url"]

        if url in seen:
            continue

        seen.add(url)
        result.append(candidate)

    return result


# ============================================================
# CHECK CANDIDATES
# ============================================================

def check_candidate(candidate, timeout):
    """
    Реальная проверка кандидата.
    """

    result = check_stream(
        candidate["url"],
        timeout=timeout,
    )

    candidate = dict(candidate)

    candidate["alive"] = result["alive"]
    candidate["verified"] = True
    candidate["reason"] = result["reason"]
    candidate["status"] = result["status"]
    candidate["latency"] = result["latency"]

    return candidate


def verify_candidates(
    candidates,
    workers,
    timeout,
):
    """
    Параллельная проверка.

    Не допускаем бесконечных потоков.
    """

    if not candidates:
        return []

    results = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        futures = {
            pool.submit(
                check_candidate,
                candidate,
                timeout,
            ): candidate
            for candidate in candidates
        }

        for future in concurrent.futures.as_completed(
            futures
        ):

            try:
                results.append(
                    future.result()
                )
            except Exception as exc:

                candidate = dict(
                    futures[future]
                )

                candidate["verified"] = True
                candidate["alive"] = False
                candidate["reason"] = (
                    f"worker_error:{type(exc).__name__}"
                )

                results.append(candidate)

    return results


# ============================================================
# ORIGINAL STREAM CHECK
# ============================================================

def check_original_streams(
    channels,
    workers,
    timeout,
):
    """
    Проверяем все исходные потоки.
    """

    jobs = []

    for channel in channels:

        for stream in channel["streams"]:

            jobs.append(
                (
                    channel,
                    stream,
                )
            )

    if not jobs:
        return

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        futures = {
            pool.submit(
                check_stream,
                stream["url"],
                timeout,
            ): (
                channel,
                stream,
            )
            for channel, stream in jobs
        }

        for future in concurrent.futures.as_completed(
            futures
        ):

            channel, stream = futures[future]

            try:
                result = future.result()
            except Exception as exc:

                stream["alive"] = False
                stream["reason"] = (
                    f"worker_error:{type(exc).__name__}"
                )
                continue

            stream["alive"] = result["alive"]
            stream["reason"] = result["reason"]
            stream["status"] = result["status"]
            stream["latency"] = result["latency"]


# ============================================================
# FALLBACK DISCOVERY
# ============================================================

def find_fallbacks_for_channel(
    channel,
    external_sources,
    ngenix_hosts,
    rt_hosts,
    workers,
    timeout,
):
    """
    Полный discovery одного канала.

    Порядок:

        1. generated NGENIX
        2. generated RT
        3. external M3U

    После этого всё проверяется.

    Возвращаем только реальные рабочие кандидаты.
    """

    candidates = []

    # --------------------------------------------------------
    # GENERATED CDN / RT
    # --------------------------------------------------------

    candidates.extend(
        generated_candidates(
            channel,
            ngenix_hosts,
            rt_hosts,
        )
    )

    # --------------------------------------------------------
    # EXTERNAL M3U
    # --------------------------------------------------------

    for source in external_sources:

        try:
            candidates.extend(
                parse_external_candidates(
                    source["text"],
                    channel,
                    source["url"],
                )
            )
        except Exception:
            continue

    candidates = deduplicate_candidates(
        candidates
    )

    # --------------------------------------------------------
    # STRICT FILTER
    # --------------------------------------------------------

    candidates = [
        c for c in candidates
        if c["score"] > 0
    ]

    # --------------------------------------------------------
    # PRIORITY
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    # --------------------------------------------------------
    # CHECK
    # --------------------------------------------------------

    checked = verify_candidates(
        candidates,
        workers=workers,
        timeout=timeout,
    )

    alive = [
        x for x in checked
        if x.get("alive")
    ]

    alive.sort(
        key=lambda x: (
            -x.get("score", 0),
            x.get("latency", 999),
        )
    )

    return alive, checked


# ============================================================
# PLAYLIST GENERATION
# ============================================================

def make_extinf(channel, index, tag=""):
    """
    Создание EXTINF.
    """

    tvg_id = channel["tvg_id"]
    group = channel["group"]
    name = channel["name"]

    return (
        f'#EXTINF:-1 '
        f'tvg-id="{tvg_id}" '
        f'group-title="{group}",'
        f'{index}. {name}{tag}'
    )


def generate_playlist(
    channels,
    output_path,
):
    """
    Создаёт megred_auto.m3u.

    Для каждого канала:
        - сначала рабочие оригинальные;
        - затем fallback.

    Таким образом, fallback не уничтожает исходную рабочую ссылку.
    """

    lines = [
        "#EXTM3U",
        "#EXT-X-FALLBACK: generated by IpTV_fallback.py",
    ]

    index = 1
    written = set()

    for channel in channels:

        # ----------------------------------------------------
        # ORIGINAL
        # ----------------------------------------------------

        for stream in channel["streams"]:

            if not stream.get("alive"):
                continue

            url = stream["url"]

            if url in written:
                continue

            lines.append(
                make_extinf(
                    channel,
                    index,
                    tag="",
                )
            )

            lines.append(url)

            written.add(url)
            index += 1

        # ----------------------------------------------------
        # FALLBACK
        # ----------------------------------------------------

        for fallback in channel.get(
            "fallbacks",
            [],
        ):

            if not fallback.get("alive"):
                continue

            url = fallback["url"]

            if url in written:
                continue

            source = fallback.get(
                "source",
                "fallback",
            )

            tag = (
                f" [fallback:{source}]"
            )

            lines.append(
                make_extinf(
                    channel,
                    index,
                    tag=tag,
                )
            )

            lines.append(url)

            written.add(url)
            index += 1

    Path(output_path).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    return index - 1


# ============================================================
# REPORT
# ============================================================

def write_report(
    channels,
    report_path,
    generated_count,
):
    """
    Подробный текстовый отчёт.
    """

    lines = []

    lines.append(
        "IPTV FALLBACK REPORT"
    )

    lines.append(
        "=" * 72
    )

    lines.append(
        f"UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}"
    )

    lines.append("")

    total_channels = len(channels)

    alive_channels = sum(
        1
        for channel in channels
        if any(
            s.get("alive")
            for s in channel["streams"]
        )
        or channel.get("fallbacks")
    )

    recovered = sum(
        1
        for channel in channels
        if (
            not any(
                s.get("alive")
                for s in channel["streams"]
            )
            and channel.get("fallbacks")
        )
    )

    dead = total_channels - alive_channels

    lines.append(
        f"Channels: {total_channels}"
    )

    lines.append(
        f"Recovered: {recovered}"
    )

    lines.append(
        f"Unresolved: {dead}"
    )

    lines.append(
        f"Generated playlist entries: {generated_count}"
    )

    lines.append("")

    for channel in channels:

        lines.append(
            "-" * 72
        )

        lines.append(
            f"CHANNEL: {channel['name']}"
        )

        lines.append(
            f"TVG-ID: {channel['tvg_id']}"
        )

        original_alive = [
            s for s in channel["streams"]
            if s.get("alive")
        ]

        lines.append(
            f"Original alive: {len(original_alive)}"
        )

        for stream in channel["streams"]:

            lines.append(
                f"  ORIGINAL "
                f"{'OK' if stream.get('alive') else 'FAIL'} "
                f"{stream.get('reason', '')} "
                f"{stream['url']}"
            )

        fallbacks = channel.get(
            "fallbacks",
            [],
        )

        lines.append(
            f"Fallbacks: {len(fallbacks)}"
        )

        for fallback in fallbacks:

            lines.append(
                f"  FALLBACK "
                f"score={fallback.get('score', 0)} "
                f"{fallback.get('reason', '')} "
                f"{fallback['url']}"
            )

    Path(report_path).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ============================================================
# JSON STATE
# ============================================================

def write_state(
    channels,
    state_path,
):
    """
    Машиночитаемое состояние.

    Это пригодится для дальнейшей интеграции в Зою.
    """

    state = {
        "generated_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        ),
        "channels": channels,
    }

    Path(state_path).write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# MAIN RECOVERY PASS
# ============================================================

def recovery_pass(
    playlist,
    output,
    report,
    state,
    workers,
    timeout,
    source_urls,
    ngenix_hosts,
    rt_hosts,
):
    """
    Один полный проход восстановления.
    """

    # --------------------------------------------------------
    # LOAD PLAYLIST
    # --------------------------------------------------------

    playlist_path = Path(playlist)

    if playlist_path.exists():

        text = playlist_path.read_text(
            encoding="utf-8-sig",
            errors="ignore",
        )

    else:

        text = fetch_url(
            playlist,
            timeout=DEFAULT_FETCH_TIMEOUT,
        )

    if not text:
        raise RuntimeError(
            f"Не удалось загрузить playlist: {playlist}"
        )

    channels = parse_m3u(text)

    if not channels:
        raise RuntimeError(
            "В плейлисте не найдено каналов."
        )

    total_streams = sum(
        len(c["streams"])
        for c in channels
    )

    print(
        f"Каналов: {len(channels)} | "
        f"потоков: {total_streams}"
    )

    # --------------------------------------------------------
    # CHECK ORIGINAL
    # --------------------------------------------------------

    print(
        "Проверяю исходные потоки..."
    )

    check_original_streams(
        channels,
        workers=workers,
        timeout=timeout,
    )

    alive_channels = [
        c for c in channels
        if any(
            s.get("alive")
            for s in c["streams"]
        )
    ]

    dead_channels = [
        c for c in channels
        if not any(
            s.get("alive")
            for s in c["streams"]
        )
    ]

    print(
        f"Живых каналов: "
        f"{len(alive_channels)}"
    )

    print(
        f"Требуют восстановления: "
        f"{len(dead_channels)}"
    )

    # --------------------------------------------------------
    # EXTERNAL SOURCES
    # --------------------------------------------------------

    print(
        "Загружаю внешние M3U..."
    )

    external_sources = load_external_sources(
        source_urls
    )

    print(
        f"Загружено внешних источников: "
        f"{len(external_sources)}"
    )

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    recovered = 0

    for number, channel in enumerate(
        dead_channels,
        start=1,
    ):

        print(
            f"\n[{number}/{len(dead_channels)}] "
            f"{channel['name']} "
            f"({channel['tvg_id']})"
        )

        fallbacks, checked = (
            find_fallbacks_for_channel(
                channel,
                external_sources,
                ngenix_hosts,
                rt_hosts,
                workers=workers,
                timeout=timeout,
            )
        )

        # ----------------------------------------------------
        # ONLY VERIFIED ALIVE
        # ----------------------------------------------------

        channel["fallbacks"] = fallbacks

        if fallbacks:

            recovered += 1

            best = fallbacks[0]

            print(
                "  OK -> "
                f"{best['url']}"
            )

            print(
                "  score="
                f"{best.get('score', 0)} "
                f"source="
                f"{best.get('source', '')} "
                f"reason="
                f"{best.get('reason', '')}"
            )

        else:

            channel["fallbacks"] = []

            print(
                "  FAIL -> "
                "подходящий живой поток "
                "не найден"
            )

    # --------------------------------------------------------
    # GENERATE OUTPUT
    # --------------------------------------------------------

    generated_count = generate_playlist(
        channels,
        output,
    )

    write_report(
        channels,
        report,
        generated_count,
    )

    write_state(
        channels,
        state,
    )

    print("")
    print(
        f"Восстановлено: "
        f"{recovered}/{len(dead_channels)}"
    )

    print(
        f"Итоговый плейлист: "
        f"{output}"
    )

    print(
        f"Записей: "
        f"{generated_count}"
    )

    print(
        f"Отчёт: "
        f"{report}"
    )

    print(
        f"State: "
        f"{state}"
    )

    return channels


# ============================================================
# WATCH MODE
# ============================================================

def watch_mode(
    channels,
    output,
    report,
    state,
    workers,
    timeout,
    interval,
    source_urls,
    ngenix_hosts,
    rt_hosts,
):
    """
    Мониторинг.

    Если текущий рабочий поток умер:
        -> канал считается dead
        -> запускается discovery
        -> fallback проверяется
        -> playlist пересобирается.
    """

    print("")
    print(
        f"MONITORING: каждые {interval} сек."
    )

    while True:

        try:

            time.sleep(interval)

            print("")
            print(
                "[WATCH] проверка..."
            )

            changed = False

            # ------------------------------------------------
            # CHECK ALL KNOWN STREAMS
            # ------------------------------------------------

            for channel in channels:

                for stream in channel["streams"]:

                    result = check_stream(
                        stream["url"],
                        timeout=timeout,
                    )

                    previous = stream.get(
                        "alive",
                        False,
                    )

                    stream["alive"] = result[
                        "alive"
                    ]

                    stream["reason"] = result[
                        "reason"
                    ]

                    if previous and not stream[
                        "alive"
                    ]:

                        print(
                            "[WATCH] ОТВАЛ: "
                            f"{channel['name']} -> "
                            f"{stream['url']}"
                        )

                        changed = True

            # ------------------------------------------------
            # FALLBACK HEALTH CHECK
            # ------------------------------------------------

            for channel in channels:

                for fallback in channel.get(
                    "fallbacks",
                    [],
                ):

                    result = check_stream(
                        fallback["url"],
                        timeout=timeout,
                    )

                    fallback["alive"] = (
                        result["alive"]
                    )

                    fallback["reason"] = (
                        result["reason"]
                    )

            # ------------------------------------------------
            # RECOVER DEAD CHANNELS
            # ------------------------------------------------

            dead_channels = [
                c
                for c in channels
                if not any(
                    s.get("alive")
                    for s in c["streams"]
                )
            ]

            if dead_channels:

                print(
                    "[WATCH] Требуют "
                    f"восстановления: "
                    f"{len(dead_channels)}"
                )

                external_sources = (
                    load_external_sources(
                        source_urls
                    )
                )

                for channel in dead_channels:

                    fallbacks, checked = (
                        find_fallbacks_for_channel(
                            channel,
                            external_sources,
                            ngenix_hosts,
                            rt_hosts,
                            workers=workers,
                            timeout=timeout,
                        )
                    )

                    if fallbacks:

                        channel["fallbacks"] = (
                            fallbacks
                        )

                        changed = True

                        print(
                            "[WATCH] RECOVERED: "
                            f"{channel['name']} -> "
                            f"{fallbacks[0]['url']}"
                        )

                    else:

                        channel["fallbacks"] = []

                        print(
                            "[WATCH] NO FALLBACK: "
                            f"{channel['name']}"
                        )

            # ------------------------------------------------
            # REBUILD
            # ------------------------------------------------

            if changed:

                generated_count = (
                    generate_playlist(
                        channels,
                        output,
                    )
                )

                write_report(
                    channels,
                    report,
                    generated_count,
                )

                write_state(
                    channels,
                    state,
                )

                print(
                    "[WATCH] playlist обновлён."
                )

        except KeyboardInterrupt:

            print(
                "\nОстановка мониторинга."
            )

            break

        except Exception as exc:

            print(
                "[WATCH] ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


# ============================================================
# CLI
# ============================================================

def build_parser():

    parser = argparse.ArgumentParser(
        description=(
            "IPTV automatic failover / "
            "dead-stream recovery"
        )
    )

    parser.add_argument(
        "--playlist",
        "-p",
        required=True,
        help=(
            "Локальный M3U или URL "
            "исходного плейлиста"
        ),
    )

    parser.add_argument(
        "--output",
        "-o",
        default="megred_auto.m3u",
        help=(
            "Итоговый плейлист "
            "(default: megred_auto.m3u)"
        ),
    )

    parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Включить автоматический мониторинг",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_WATCH_INTERVAL,
        help=(
            "Интервал мониторинга в секундах"
        ),
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            "Количество параллельных проверок"
        ),
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=(
            "Timeout проверки одного URL"
        ),
    )

    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        default=[],
        help=(
            "Дополнительный внешний M3U URL. "
            "Можно указать несколько раз."
        ),
    )

    parser.add_argument(
        "--ngenix-host",
        action="append",
        default=[],
        help=(
            "Дополнительный NGENIX host. "
            "Например s70790.cdn.ngenix.net"
        ),
    )

    parser.add_argument(
        "--rt-host",
        action="append",
        default=[],
        help=(
            "Дополнительный RT HLS host."
        ),
    )

    parser.add_argument(
        "--report",
        default=OUTPUT_REPORT,
        help="Файл отчёта",
    )

    parser.add_argument(
        "--state",
        default=OUTPUT_STATE,
        help="JSON state",
    )

    return parser


# ============================================================
# MAIN
# ============================================================

def main():

    parser = build_parser()

    args = parser.parse_args()

    workers = max(
        1,
        min(args.workers, 128),
    )

    timeout = max(
        2,
        min(args.timeout, 30),
    )

    interval = max(
        10,
        args.interval,
    )

    # --------------------------------------------------------
    # SOURCES
    # --------------------------------------------------------

    source_urls = list(
        dict.fromkeys(
            EXTERNAL_SOURCES
            + args.sources
        )
    )

    # --------------------------------------------------------
    # NGENIX HOSTS
    # --------------------------------------------------------

    ngenix_hosts = list(
        dict.fromkeys(
            NGENIX_HOSTS
            + NGENIX_S_HOSTS
            + args.ngenix_host
        )
    )

    # --------------------------------------------------------
    # RT HOSTS
    # --------------------------------------------------------

    rt_hosts = list(
        dict.fromkeys(
            RT_HOSTS
            + args.rt_host
        )
    )

    print("=" * 72)

    print(
        "IPTV FALLBACK / AUTO RECOVERY"
    )

    print("=" * 72)

    print(
        f"Workers: {workers}"
    )

    print(
        f"Timeout: {timeout}s"
    )

    print(
        f"NGENIX hosts: {len(ngenix_hosts)}"
    )

    print(
        f"RT hosts: {len(rt_hosts)}"
    )

    print(
        f"External sources: {len(source_urls)}"
    )

    print("=" * 72)

    # --------------------------------------------------------
    # FIRST PASS
    # --------------------------------------------------------

    try:

        channels = recovery_pass(
            playlist=args.playlist,
            output=args.output,
            report=args.report,
            state=args.state,
            workers=workers,
            timeout=timeout,
            source_urls=source_urls,
            ngenix_hosts=ngenix_hosts,
            rt_hosts=rt_hosts,
        )

    except Exception as exc:

        print(
            f"FATAL: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        return 1

    # --------------------------------------------------------
    # WATCH
    # --------------------------------------------------------

    if args.watch:

        watch_mode(
            channels=channels,
            output=args.output,
            report=args.report,
            state=args.state,
            workers=workers,
            timeout=timeout,
            interval=interval,
            source_urls=source_urls,
            ngenix_hosts=ngenix_hosts,
            rt_hosts=rt_hosts,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())