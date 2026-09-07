#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
IpTV_fallback.py
================

Автоматический failover-модуль IPTV.

Логика:

1. Загружает исходный M3U.
2. Проверяет ВСЕ существующие потоки каждого канала.
3. Нормализует tvg-id / название канала.
4. Для мёртвого канала строит кандидатов:
   - существующие альтернативы;
   - NGENIX hosts;
   - RT/Rostelecom hosts;
   - alias -> path transformations;
   - HTTP/HTTPS варианты;
   - публичные M3U/M3U8 источники.
5. Проверяет кандидатов параллельно.
6. Берёт только подтверждённый живой поток.
7. Автоматически добавляет его в megred_auto.m3u.
8. Ведёт JSON-кэш discovery.
9. В режиме --watch повторяет failover при падении рабочего потока.

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
    --workers 32 \
    --timeout 5

Важно:
Модуль работает с публично доступными HTTP/HTTPS потоками.
Авторизация, DRM и обход ограничений доступа не выполняются.
"""

import argparse
import concurrent.futures
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from pathlib import Path


# ============================================================================
# CONFIG
# ============================================================================

DEFAULT_OUTPUT = "megred_auto.m3u"
DEFAULT_CACHE = "fallback_discovery.json"
DEFAULT_LOG = "fallback.log"

DEFAULT_WORKERS = 32
DEFAULT_TIMEOUT = 5
DEFAULT_FETCH_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Wink/1.0 "
        "Mozilla/5.0 "
        "AppleWebKit/537.36 "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "close",
}

# Дополнительные UA.
# Первый используется как основной.
USER_AGENTS = [
    HEADERS["User-Agent"],
    "Mozilla/5.0",
    "VLC/3.0.20 LibVLC/3.0.20",
    "Lavf/60.3.100",
]


# ============================================================================
# PUBLIC SOURCES
# ============================================================================

EXTERNAL_SOURCES = [
    # IPTV-org
    "https://iptv-org.github.io/iptv/index.m3u",
    "https://iptv-org.github.io/iptv/countries/ru.m3u",

    # Старые/дополнительные источники пользователя
    "https://naggdd.github.io/iptv/ru.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_russia.m3u8",
    "https://raw.githubusercontent.com/IPTVRU2026/IPTVMIR/main/IPTV_MEGA_PLAYLIST.m3u",
]


# ============================================================================
# NGENIX / RT HOSTS
# ============================================================================

# Здесь можно дописывать конкретные известные hosts.
# s70xxx можно указывать как hostname без схемы.
NGENIX_HOSTS = [
    "s70790.cdn.ngenix.net",
    "s70378.cdn.ngenix.net",
    "s70379.cdn.ngenix.net",
    "s70380.cdn.ngenix.net",
    "s70381.cdn.ngenix.net",
    "s70382.cdn.ngenix.net",
    "s70383.cdn.ngenix.net",
    "s70384.cdn.ngenix.net",
    "s70385.cdn.ngenix.net",
    "s70386.cdn.ngenix.net",
    "s70387.cdn.ngenix.net",
    "s70388.cdn.ngenix.net",
    "s70389.cdn.ngenix.net",
    "s70390.cdn.ngenix.net",

    # Региональный RT/NGENIX endpoint из исходного плейлиста
    "rt-nw-klgr-htlive.cdn.ngenix.net",
]


RT_HOSTS = [
    "hlsstr01.svc.iptv.rt.ru",
]


# ============================================================================
# CHANNEL ALIASES
# ============================================================================

CHANNEL_ALIASES = {
    "karusel": [
        "карусель",
        "karusel",
        "karusel tv",
        "ch_karusel",
        "ch_r01_karusel",
    ],

    "rentv": [
        "рен тв",
        "рен тв hd",
        "ren tv",
        "rentv",
        "ch_rentv",
        "ch_r01_rentv",
    ],

    "tv3": [
        "тв-3",
        "тв 3",
        "tv3",
        "tv-3",
        "ch_tv3",
        "ch_r01_tv3",
    ],

    "mir": [
        "мир",
        "mir",
        "ch_mir",
        "ch_r01_mir",
    ],

    "ocean_tv": [
        "ocean tv",
        "океан",
        "ocean",
        "ch_oceantv",
        "ch_r01_oceantv",
    ],

    "viasat_nature": [
        "viju nature",
        "viju_nature",
        "viasat nature",
        "viasatnature",
        "ch_viasatnaturehd",
        "ch_r01_viasatnaturehd",
    ],

    "viasat_explore": [
        "viju explore",
        "viju_explore",
        "viasat explore",
        "viasatexplore",
        "ch_viasatexplorehd",
        "ch_r01_viasatexplorehd",
    ],

    "viasat_history": [
        "viju history",
        "viju_history",
        "viasat history",
        "viasathistory",
        "ch_viasathistoryhd",
        "ch_r01_viasathistoryhd",
    ],

    "tiji": [
        "tiji",
        "тижи",
        "ch_tiji",
        "ch_r01_tiji",
    ],

    "gulli": [
        "gulli",
        "гулли",
        "ch_gulli",
        "ch_r01_gulli",
    ],

    "nickelodeon": [
        "nickelodeon",
        "nick",
        "ch_nickel",
        "ch_r01_nickel",
    ],

    "nicktoons": [
        "nicktoons",
        "nicktoons hd",
        "ch_nicktoons",
        "ch_r01_nicktoons",
    ],

    "baby_tv": [
        "baby tv",
        "babytv",
        "baby",
        "ch_babytv",
        "ch_r01_babytv",
    ],

    "match_planeta": [
        "матч планета",
        "match planeta",
        "match! planeta",
        "ch_matchplaneta",
        "ch_r01_matchplaneta",
    ],

    "fightbox": [
        "fightbox",
        "fight box",
        "ch_fightbox",
        "ch_r01_fightbox",
    ],

    "trace_sport_stars": [
        "trace sport",
        "trace sport stars",
        "tracesport",
        "ch_tracesport",
        "ch_r01_tracesport",
    ],

    "amedia_1": [
        "amedia 1",
        "a1",
        "amedia1",
        "ch_amedia1",
        "ch_r01_amedia1",
    ],

    "amedia_2": [
        "amedia 2",
        "a2",
        "amedia2",
        "ch_amedia2",
        "ch_r01_amedia2",
    ],

    "amedia_premium_hd": [
        "amedia premium",
        "amedia premium hd",
        "amediapremiumhd",
        "ch_amediapremiumhd",
        "ch_r01_amediapremiumhd",
    ],

    "amedia_hit": [
        "amedia hit",
        "amedia hit hd",
        "amediahithd",
        "ch_amediahithd",
        "ch_r01_amediahithd",
    ],

    "filmbox": [
        "filmbox",
        "film box",
        "ch_filmbox",
        "ch_r01_filmbox",
    ],

    "filmbox_arthouse": [
        "filmbox arthouse",
        "film box arthouse",
        "filmboxarthouse",
        "ch_filmboxarthouse",
        "ch_r01_filmboxarthouse",
    ],

    "amc": [
        "amc",
        "ch_amc",
        "ch_r01_amc",
    ],

    "dom_kino": [
        "дом кино",
        "dom kino",
        "domkino",
        "ch_domkino",
        "ch_r01_domkino",
    ],

    "dom_kino_premium_hd": [
        "дом кино премиум",
        "дом кино премиум hd",
        "dom kino premium",
        "domkinopremiumhd",
        "ch_domkinopremiumhd",
        "ch_r01_domkinopremiumhd",
    ],

    "evrokino": [
        "еврокино",
        "evrokino",
        "ch_evrokino",
        "ch_r01_evrokino",
    ],

    "illusion_plus": [
        "иллюзион",
        "иллюзион+",
        "illusion",
        "illusion plus",
        "illusionplus",
        "ch_illusionplus",
        "ch_r01_illusionplus",
    ],

    "mir_seriala": [
        "мир сериала",
        "mir seriala",
        "mirseriala",
        "ch_mirseriala",
        "ch_r01_mirseriala",
    ],

    "tv_xxi": [
        "тв xxi",
        "tv xxi",
        "тв 21",
        "tv21",
        "tvxxi",
        "ch_tvxxi",
        "ch_r01_tvxxi",
    ],

    "365_dney_tv": [
        "365 дней",
        "365 дней тв",
        "365 dney",
        "365",
        "365dneytv",
        "ch_365dneytv",
        "ch_r01_365dneytv",
    ],

    "galaxy": [
        "galaxy",
        "галактика",
        "ch_galaxy",
        "ch_r01_galaxy",
    ],

    "sony_channel": [
        "sony channel",
        "sony channel hd",
        "sony",
        "sonychannel",
        "ch_sonychannel",
        "ch_r01_sonychannel",
    ],

    "sony_turbo": [
        "sony turbo",
        "sony_turbo",
        "sonyturbo",
        "ch_sonyturbo",
        "ch_r01_sonyturbo",
    ],

    "history_2": [
        "history 2",
        "history2",
        "history ii",
        "ch_history2",
        "ch_r01_history2",
    ],

    "docubox": [
        "docubox",
        "docu box",
        "ch_docubox",
        "ch_r01_docubox",
    ],

    "nostalgia": [
        "ностальгия",
        "nostalgia",
        "ch_nostalgia",
        "ch_r01_nostalgia",
    ],

    "da_vinci": [
        "da vinci",
        "да винчи",
        "davinci",
        "davincilearning",
        "ch_davincilearning",
        "ch_r01_davincilearning",
    ],

    "kitchen_tv": [
        "kitchen tv",
        "kitchen",
        "kitchentv",
        "ch_kitchentv",
        "ch_r01_kitchentv",
    ],

    "mezzo": [
        "mezzo",
        "меццо",
        "ch_mezzo",
        "ch_r01_mezzo",
    ],

    "tnt_music": [
        "тнт music",
        "тнт мьюзик",
        "tnt music",
        "tntmusic",
        "tntmusichd",
        "ch_tntmusichd",
        "ch_r01_tntmusichd",
    ],

    "rtvi": [
        "rtvi",
        "ртви",
        "ch_rtvi",
        "ch_r01_rtvi",
    ],

    "tv5_monde": [
        "tv5 monde",
        "tv5",
        "tv5monde",
        "ch_tv5monde",
        "ch_r01_tv5monde",
    ],

    "fashion_tv": [
        "fashion tv",
        "fashion",
        "fashiontv",
        "fashiontvhd",
        "ch_fashiontvhd",
        "ch_r01_fashiontvhd",
    ],

    "viasat_sport": [
        "viju plus sport",
        "viju sport",
        "viasat sport",
        "viasatsport",
        "viasat sport hd",
        "ch_viasatsport",
        "ch_r01_viasatsport",
    ],

    "vip_premiere": [
        "viju plus premiere",
        "viju premiere",
        "vip premiere",
        "vip_premiere",
        "vijupremiere",
        "ch_vijupremiere",
        "ch_r01_vijupremiere",
    ],

    "vip_megahit": [
        "viju plus megahit",
        "viju megahit",
        "vip megahit",
        "vip_megahit",
        "vijumegahit",
        "ch_vijumegahit",
        "ch_r01_vijumegahit",
    ],

    "vip_comedy": [
        "viju plus comedy",
        "viju comedy",
        "vip comedy",
        "vip_comedy",
        "vijucomedy",
        "ch_vijucomedy",
        "ch_r01_vijucomedy",
    ],

    "vip_serial": [
        "viju plus serial",
        "viju serial",
        "vip serial",
        "vip_serial",
        "vijuserial",
        "ch_vijuserial",
        "ch_r01_vijuserial",
    ],
}


# ============================================================================
# SSL
# ============================================================================

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# ============================================================================
# LOGGING
# ============================================================================

LOG_FILE = DEFAULT_LOG


def log(message):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"

    print(line, flush=True)

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ============================================================================
# NORMALIZATION
# ============================================================================

def normalize(value):
    if not value:
        return ""

    value = value.lower().strip()

    value = value.replace("ё", "е")
    value = value.replace("_", " ")
    value = value.replace("-", " ")
    value = value.replace("+", " plus ")

    value = re.sub(r"\[.*?\]", " ", value)
    value = re.sub(r"\(.*?\)", " ", value)
    value = re.sub(r"\bhd\b", " ", value)
    value = re.sub(r"\buhd\b", " ", value)
    value = re.sub(r"\bfhd\b", " ", value)

    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def compact(value):
    return normalize(value).replace(" ", "")


# ============================================================================
# CHANNEL MATCHING
# ============================================================================

def aliases_for(tvg_id, name=""):
    result = []

    if tvg_id in CHANNEL_ALIASES:
        result.extend(CHANNEL_ALIASES[tvg_id])

    result.append(tvg_id)
    result.append(name)

    normalized = normalize(name)

    if normalized:
        result.append(normalized)
        result.append(compact(normalized))

    out = []

    for item in result:
        if not item:
            continue

        item = str(item).strip().lower()

        if item and item not in out:
            out.append(item)

    return out


def channel_matches(tvg_id, channel_name, candidate_name, candidate_url=""):
    aliases = aliases_for(tvg_id, channel_name)

    haystack = normalize(
        f"{candidate_name} {candidate_url}"
    )

    haystack_compact = compact(haystack)

    for alias in aliases:
        n = normalize(alias)

        if not n:
            continue

        if n in haystack:
            return True

        if compact(n) and compact(n) in haystack_compact:
            return True

    return False


# ============================================================================
# HTTP
# ============================================================================

def request_url(url, timeout=DEFAULT_TIMEOUT, user_agent=None):
    headers = dict(HEADERS)

    if user_agent:
        headers["User-Agent"] = user_agent

    req = urllib.request.Request(
        url,
        headers=headers,
        method="GET",
    )

    return urllib.request.urlopen(
        req,
        timeout=timeout,
        context=SSL_CTX,
    )


def fetch_url(url, timeout=DEFAULT_FETCH_TIMEOUT):
    try:
        with request_url(url, timeout=timeout) as resp:
            data = resp.read()

            charset = resp.headers.get_content_charset() or "utf-8"

            return data.decode(
                charset,
                errors="ignore",
            )

    except Exception:
        return None


# ============================================================================
# STREAM PROBE
# ============================================================================

def probe_stream(url, timeout=DEFAULT_TIMEOUT):
    """
    Проверка кандидата.

    Возвращает dict, а не bool:
    {
        alive,
        status,
        content_type,
        bytes,
        error
    }
    """

    for ua in USER_AGENTS:
        try:
            headers = dict(HEADERS)
            headers["User-Agent"] = ua
            headers["Range"] = "bytes=0-2047"

            req = urllib.request.Request(
                url,
                headers=headers,
                method="GET",
            )

            with urllib.request.urlopen(
                req,
                timeout=timeout,
                context=SSL_CTX,
            ) as resp:

                status = getattr(resp, "status", 200)

                data = resp.read(2048)

                content_type = (
                    resp.headers.get("Content-Type", "")
                    or ""
                ).lower()

                if status < 200 or status >= 400:
                    return {
                        "alive": False,
                        "status": status,
                        "content_type": content_type,
                        "bytes": len(data),
                        "error": f"http_{status}",
                    }

                body = data.lstrip().lower()

                looks_like_hls = (
                    b"#extm3u" in body
                    or b"#extinf" in body
                    or b"#ext-x-" in body
                )

                media_type = (
                    "mpegurl" in content_type
                    or "m3u8" in content_type
                    or "application/vnd.apple.mpegurl"
                    in content_type
                )

                alive = (
                    looks_like_hls
                    or media_type
                    or len(data) > 100
                )

                return {
                    "alive": alive,
                    "status": status,
                    "content_type": content_type,
                    "bytes": len(data),
                    "error": None,
                }

        except urllib.error.HTTPError as exc:
            status = getattr(exc, "code", 0)

            # Для 403 нет смысла бесконечно повторять запросы.
            if status == 403:
                return {
                    "alive": False,
                    "status": 403,
                    "content_type": "",
                    "bytes": 0,
                    "error": "http_403",
                }

            if status == 404:
                return {
                    "alive": False,
                    "status": 404,
                    "content_type": "",
                    "bytes": 0,
                    "error": "http_404",
                }

        except Exception:
            pass

    return {
        "alive": False,
        "status": 0,
        "content_type": "",
        "bytes": 0,
        "error": "connection_failed",
    }


# ============================================================================
# M3U PARSER
# ============================================================================

def parse_m3u(text, source="unknown"):
    """
    Возвращает список записей:

    {
        tvg_id,
        group,
        name,
        url,
        source
    }
    """

    entries = []

    if not text:
        return entries

    current = None

    for raw in text.splitlines():

        line = raw.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            tvg = re.search(
                r'tvg-id="([^"]*)"',
                line,
                re.IGNORECASE,
            )

            group = re.search(
                r'group-title="([^"]*)"',
                line,
                re.IGNORECASE,
            )

            name = (
                line.split(",", 1)[1]
                if "," in line
                else "Unknown"
            )

            name = re.sub(
                r"^\s*\d+\.\s*",
                "",
                name.strip(),
            )

            current = {
                "tvg_id": (
                    tvg.group(1).strip()
                    if tvg and tvg.group(1).strip()
                    else compact(name)
                ),
                "group": (
                    group.group(1).strip()
                    if group
                    else ""
                ),
                "name": name,
                "url": "",
                "source": source,
            }

        elif not line.startswith("#") and current:

            current["url"] = line

            entries.append(current)

            current = None

    return entries


# ============================================================================
# LOCAL PLAYLIST
# ============================================================================

def load_playlist(source):
    path = Path(source)

    if path.exists():
        return path.read_text(
            encoding="utf-8-sig",
            errors="ignore",
        )

    return fetch_url(source)


# ============================================================================
# DISCOVERY CACHE
# ============================================================================

def load_cache(path):
    try:
        p = Path(path)

        if not p.exists():
            return {}

        return json.loads(
            p.read_text(
                encoding="utf-8",
                errors="ignore",
            )
        )

    except Exception:
        return {}


def save_cache(path, data):
    try:
        Path(path).write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    except Exception as exc:
        log(f"[CACHE] write failed: {exc}")


# ============================================================================
# URL PATH EXTRACTION
# ============================================================================

def extract_path(url):
    try:
        parsed = urllib.parse.urlsplit(url)

        return (
            parsed.path
            + (
                "?" + parsed.query
                if parsed.query
                else ""
            )
        )

    except Exception:
        return ""


def extract_host(url):
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except Exception:
        return ""


def extract_aliases_from_url(url):
    """
    Извлекает возможные channel aliases из URL.

    Например:

    /hls/CH_R01_SONYTURBO/variant.m3u8

    даст:

    CH_R01_SONYTURBO
    SONYTURBO
    """

    result = []

    path = extract_path(url)

    parts = [
        p
        for p in path.split("/")
        if p
    ]

    for part in parts:

        base = re.sub(
            r"\.(m3u8|m3u|ts|mp4)$",
            "",
            part,
            flags=re.IGNORECASE,
        )

        if not base:
            continue

        result.append(base)

        upper = base.upper()

        if upper.startswith("CH_R01_"):
            result.append(
                upper.replace(
                    "CH_R01_",
                    "CH_",
                    1,
                )
            )

            result.append(
                upper.replace(
                    "CH_R01_",
                    "",
                    1,
                )
            )

        elif upper.startswith("CH_"):
            result.append(
                "CH_R01_"
                + upper[3:]
            )

            result.append(
                upper[3:]
            )

    return list(
        dict.fromkeys(
            result
        )
    )


# ============================================================================
# PATH GENERATOR
# ============================================================================

def build_channel_aliases(tvg_id, name, known_urls):
    aliases = set()

    for item in aliases_for(
        tvg_id,
        name,
    ):
        aliases.add(item)

    # Канонические variants
    for item in list(aliases):

        n = compact(item)

        if not n:
            continue

        upper = n.upper()

        aliases.add(upper)
        aliases.add("CH_" + upper)
        aliases.add("CH_R01_" + upper)

    # URL-derived aliases
    for url in known_urls:

        for item in extract_aliases_from_url(url):

            aliases.add(item)
            aliases.add(item.upper())

            normalized = compact(item)

            if normalized:
                aliases.add(
                    "CH_" + normalized.upper()
                )

                aliases.add(
                    "CH_R01_"
                    + normalized.upper()
                )

    # Системные канонические aliases
    if tvg_id:

        base = compact(tvg_id).upper()

        aliases.add(base)
        aliases.add("CH_" + base)
        aliases.add("CH_R01_" + base)

    return [
        x
        for x in aliases
        if x
    ]


def generate_paths(
    tvg_id,
    name,
    known_urls,
):
    """
    Генерирует допустимые HLS paths.

    Сначала реальные paths из существующих URL,
    затем стандартные HLS варианты.
    """

    paths = set()

    # Сохраняем реальные найденные paths.
    for url in known_urls:

        path = extract_path(url)

        if path:
            paths.add(path)

    aliases = build_channel_aliases(
        tvg_id,
        name,
        known_urls,
    )

    for alias in aliases:

        clean = alias.strip("/")

        if not clean:
            continue

        paths.add(
            f"/hls/{clean}/variant.m3u8"
        )

        paths.add(
            f"/hls/{clean}/index.m3u8"
        )

        paths.add(
            f"/hls/{clean}/master.m3u8"
        )

    return list(paths)


# ============================================================================
# HOST GENERATOR
# ============================================================================

def normalize_host(host):
    host = host.strip()

    host = re.sub(
        r"^https?://",
        "",
        host,
        flags=re.IGNORECASE,
    )

    host = host.rstrip("/")

    return host


def build_hosts(
    known_urls,
    extra_hosts=None,
):
    hosts = set()

    for host in NGENIX_HOSTS:
        hosts.add(
            normalize_host(host)
        )

    for host in RT_HOSTS:
        hosts.add(
            normalize_host(host)
        )

    for url in known_urls:

        host = extract_host(url)

        if host:
            hosts.add(
                normalize_host(host)
            )

    if extra_hosts:

        for host in extra_hosts:
            hosts.add(
                normalize_host(host)
            )

    return [
        h
        for h in hosts
        if h
    ]


# ============================================================================
# GENERATED CANDIDATES
# ============================================================================

def generate_candidates(
    tvg_id,
    name,
    known_urls,
    discovered_urls,
):
    candidates = set()

    # 1. Уже известные URL
    for url in known_urls:
        if url:
            candidates.add(url)

    # 2. Уже обнаруженные публичные URL
    for url in discovered_urls:
        if url:
            candidates.add(url)

    hosts = build_hosts(
        known_urls
    )

    paths = generate_paths(
        tvg_id,
        name,
        known_urls,
    )

    # 3. Host × path × scheme
    for host in hosts:

        host_lower = host.lower()

        for path in paths:

            # Не пытаемся менять произвольные схемы
            # или выполнять обход авторизации.
            for scheme in (
                "https",
                "http",
            ):

                url = (
                    f"{scheme}://"
                    f"{host_lower}"
                    f"{path}"
                )

                candidates.add(url)

    # 4. Нормализованные alias paths.
    aliases = build_channel_aliases(
        tvg_id,
        name,
        known_urls,
    )

    for host in hosts:

        for alias in aliases:

            candidates.add(
                "https://"
                + host
                + "/hls/"
                + alias
                + "/variant.m3u8"
            )

            candidates.add(
                "http://"
                + host
                + "/hls/"
                + alias
                + "/variant.m3u8"
            )

    # Убираем очевидный мусор.
    clean = []

    for url in candidates:

        if not url.startswith(
            ("http://", "https://")
        ):
            continue

        if ".m3u" not in url.lower():
            continue

        clean.append(url)

    return list(
        dict.fromkeys(clean)
    )


# ============================================================================
# EXTERNAL DISCOVERY
# ============================================================================

def parse_external_source(
    text,
    source_name,
):
    entries = parse_m3u(
        text,
        source=source_name,
    )

    return entries


def discover_from_sources(
    channel,
    cached_sources,
):
    result = []

    tvg_id = channel["tvg_id"]
    name = channel["name"]

    for source_name, text in cached_sources:

        if not text:
            continue

        entries = parse_external_source(
            text,
            source_name,
        )

        for entry in entries:

            if channel_matches(
                tvg_id,
                name,
                entry["name"],
                entry["url"],
            ):
                result.append(
                    entry["url"]
                )

    return list(
        dict.fromkeys(result)
    )


# ============================================================================
# PARALLEL PROBE
# ============================================================================

def probe_candidates(
    candidates,
    workers,
    timeout,
    stop_after_first=True,
):
    """
    Параллельная проверка.

    stop_after_first=True:
    возвращается первый подтверждённый живой URL.

    Для полного inventory:
    stop_after_first=False.
    """

    if not candidates:
        return []

    alive = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        future_map = {
            pool.submit(
                probe_stream,
                url,
                timeout,
            ): url

            for url in candidates
        }

        for future in concurrent.futures.as_completed(
            future_map
        ):

            url = future_map[future]

            try:
                result = future.result()

            except Exception as exc:

                result = {
                    "alive": False,
                    "status": 0,
                    "error": str(exc),
                }

            if result.get("alive"):

                alive.append(
                    (
                        url,
                        result,
                    )
                )

                if stop_after_first:
                    # Отменяем ещё не начавшиеся jobs.
                    for other in future_map:
                        if other is not future:
                            other.cancel()

                    break

    return alive


# ============================================================================
# CHANNEL GROUPING
# ============================================================================

def group_channels(entries):
    channels = {}

    for entry in entries:

        tvg_id = (
            entry["tvg_id"]
            or compact(entry["name"])
        )

        if tvg_id not in channels:

            channels[tvg_id] = {
                "tvg_id": tvg_id,
                "name": entry["name"],
                "group": entry["group"],
                "streams": [],
            }

        channels[tvg_id]["streams"].append(
            {
                "url": entry["url"],
                "alive": False,
                "source": entry["source"],
                "status": None,
            }
        )

    return channels


# ============================================================================
# INITIAL CHECK
# ============================================================================

def check_all_streams(
    channels,
    workers,
    timeout,
):
    jobs = []

    for channel in channels.values():

        for stream in channel["streams"]:

            jobs.append(
                (
                    channel,
                    stream,
                )
            )

    if not jobs:
        return

    log(
        f"[PROBE] проверяю {len(jobs)} потоков "
        f"с concurrency={workers}"
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        future_map = {
            pool.submit(
                probe_stream,
                stream["url"],
                timeout,
            ): (
                channel,
                stream,
            )

            for channel, stream in jobs
        }

        for future in concurrent.futures.as_completed(
            future_map
        ):

            channel, stream = future_map[future]

            try:
                result = future.result()

            except Exception:

                result = {
                    "alive": False,
                    "status": 0,
                    "error": "exception",
                }

            stream["alive"] = bool(
                result.get("alive")
            )

            stream["status"] = result.get(
                "status"
            )

            if not stream["alive"]:

                log(
                    f"[DEAD] "
                    f"{channel['tvg_id']} "
                    f"{stream['url']} "
                    f"status={result.get('status')} "
                    f"error={result.get('error')}"
                )


# ============================================================================
# FAILOVER
# ============================================================================

def failover_channel(
    channel,
    cached_sources,
    workers,
    timeout,
    cache,
):
    tvg_id = channel["tvg_id"]
    name = channel["name"]

    known_urls = [
        stream["url"]
        for stream in channel["streams"]
        if stream.get("url")
    ]

    # Сначала используем уже обнаруженные cache URLs.
    discovered_urls = cache.get(
        tvg_id,
        [],
    )

    if not isinstance(
        discovered_urls,
        list,
    ):
        discovered_urls = []

    log(
        f"[FAILOVER] {tvg_id} / {name}"
    )

    # ----------------------------------------------------------------------
    # External discovery
    # ----------------------------------------------------------------------

    external = discover_from_sources(
        channel,
        cached_sources,
    )

    if external:

        log(
            f"[DISCOVERY] {tvg_id}: "
            f"{len(external)} внешних кандидатов"
        )

        discovered_urls.extend(
            external
        )

    discovered_urls = list(
        dict.fromkeys(
            discovered_urls
        )
    )

    # ----------------------------------------------------------------------
    # Candidate generation
    # ----------------------------------------------------------------------

    candidates = generate_candidates(
        tvg_id,
        name,
        known_urls,
        discovered_urls,
    )

    # Убираем уже проверенные мёртвые originals,
    # чтобы не тратить время второй раз.
    alive_existing = {
        s["url"]
        for s in channel["streams"]
        if s.get("alive")
    }

    candidates = [
        url
        for url in candidates
        if url not in alive_existing
    ]

    log(
        f"[GENERATE] {tvg_id}: "
        f"{len(candidates)} кандидатов"
    )

    # ----------------------------------------------------------------------
    # Probe
    # ----------------------------------------------------------------------

    alive = probe_candidates(
        candidates,
        workers=workers,
        timeout=timeout,
        stop_after_first=True,
    )

    if not alive:

        log(
            f"[FAILOVER] {tvg_id}: "
            f"живой replacement не найден"
        )

        cache[tvg_id] = discovered_urls

        return False

    replacement_url, result = alive[0]

    # ----------------------------------------------------------------------
    # Проверяем ещё раз победителя.
    # ----------------------------------------------------------------------

    final = probe_stream(
        replacement_url,
        timeout,
    )

    if not final.get("alive"):

        log(
            f"[RACE] кандидат умер до подстановки: "
            f"{replacement_url}"
        )

        return False

    # ----------------------------------------------------------------------
    # Insert replacement
    # ----------------------------------------------------------------------

    exists = any(
        stream["url"] == replacement_url
        for stream in channel["streams"]
    )

    if not exists:

        channel["streams"].append(
            {
                "url": replacement_url,
                "alive": True,
                "source": "failover",
                "status": final.get("status"),
            }
        )

    else:

        for stream in channel["streams"]:

            if stream["url"] == replacement_url:

                stream["alive"] = True
                stream["status"] = final.get(
                    "status"
                )

    cache[tvg_id] = list(
        dict.fromkeys(
            discovered_urls
            + [replacement_url]
        )
    )

    log(
        f"[ALIVE] {tvg_id}: "
        f"{replacement_url} "
        f"status={final.get('status')}"
    )

    log(
        f"[REPLACE] {name} -> "
        f"{replacement_url}"
    )

    return True


# ============================================================================
# OUTPUT
# ============================================================================

def write_playlist(
    channels,
    output,
):
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
    ]

    idx = 1
    written = set()

    for channel in channels.values():

        # Сначала оригинальные живые,
        # затем failover.
        streams = sorted(
            channel["streams"],
            key=lambda s: (
                0
                if s.get("source") == "original"
                else 1
            ),
        )

        for stream in streams:

            if not stream.get("alive"):
                continue

            url = stream["url"]

            if url in written:
                continue

            written.add(url)

            source = stream.get(
                "source",
                "unknown",
            )

            suffix = ""

            if source == "failover":
                suffix = " [FAILOVER]"

            elif source == "external":
                suffix = " [EXTERNAL]"

            elif source != "original":
                suffix = f" [{source.upper()}]"

            name = channel["name"]

            group = channel["group"]

            tvg_id = channel["tvg_id"]

            lines.append(
                '#EXTINF:-1 '
                f'tvg-id="{tvg_id}" '
                f'group-title="{group}",'
                f'{idx}. {name}{suffix}'
            )

            lines.append(url)

            idx += 1

    Path(output).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    log(
        f"[WRITE] {output}: "
        f"{idx - 1} потоков"
    )

    return idx - 1


# ============================================================================
# STATISTICS
# ============================================================================

def statistics(channels):
    total = 0
    alive = 0
    dead_channels = 0

    for channel in channels.values():

        streams = channel["streams"]

        total += len(streams)

        if any(
            s.get("alive")
            for s in streams
        ):
            alive += 1

        else:
            dead_channels += 1

    return {
        "channels": len(channels),
        "streams": total,
        "alive_channels": alive,
        "dead_channels": dead_channels,
    }


# ============================================================================
# FAILOVER PASS
# ============================================================================

def run_failover_pass(
    channels,
    cached_sources,
    workers,
    timeout,
    cache,
):
    dead = [
        channel
        for channel in channels.values()
        if not any(
            s.get("alive")
            for s in channel["streams"]
        )
    ]

    if not dead:
        return 0

    log(
        f"[FAILOVER] мёртвых каналов: "
        f"{len(dead)}"
    )

    recovered = 0

    # Каналы обрабатываются параллельно,
    # но внутренний probe каждого канала
    # также ограничен workers.
    #
    # Поэтому здесь намеренно небольшой
    # верхний уровень concurrency.
    outer_workers = min(
        8,
        max(1, len(dead)),
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=outer_workers
    ) as pool:

        future_map = {
            pool.submit(
                failover_channel,
                channel,
                cached_sources,
                workers,
                timeout,
                cache,
            ): channel
            for channel in dead
        }

        for future in concurrent.futures.as_completed(
            future_map
        ):

            try:
                if future.result():
                    recovered += 1

            except Exception as exc:

                channel = future_map[future]

                log(
                    f"[ERROR] failover "
                    f"{channel['tvg_id']}: "
                    f"{exc}"
                )

    return recovered


# ============================================================================
# EXTERNAL SOURCE LOADING
# ============================================================================

def load_external_sources():
    result = []

    log(
        f"[SOURCES] загружаю "
        f"{len(EXTERNAL_SOURCES)} источников"
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(
            8,
            len(EXTERNAL_SOURCES),
        )
    ) as pool:

        future_map = {
            pool.submit(
                fetch_url,
                source,
                DEFAULT_FETCH_TIMEOUT,
            ): source
            for source in EXTERNAL_SOURCES
        }

        for future in concurrent.futures.as_completed(
            future_map
        ):

            source = future_map[future]

            try:
                text = future.result()

            except Exception:
                text = None

            if text:

                log(
                    f"[SOURCE OK] {source} "
                    f"bytes={len(text)}"
                )

                result.append(
                    (
                        source,
                        text,
                    )
                )

            else:

                log(
                    f"[SOURCE FAIL] {source}"
                )

    return result


# ============================================================================
# WATCH
# ============================================================================

def watch_loop(
    channels,
    cached_sources,
    workers,
    timeout,
    interval,
    output,
    cache,
    cache_path,
):
    log(
        f"[WATCH] interval={interval}s"
    )

    while True:

        try:

            changed = False

            # Проверяем все текущие рабочие потоки.
            jobs = []

            for channel in channels.values():

                for stream in channel["streams"]:

                    if stream.get("alive"):

                        jobs.append(
                            (
                                channel,
                                stream,
                            )
                        )

            log(
                f"[WATCH] проверяю "
                f"{len(jobs)} рабочих потоков"
            )

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers
            ) as pool:

                future_map = {
                    pool.submit(
                        probe_stream,
                        stream["url"],
                        max(3, timeout),
                    ): (
                        channel,
                        stream,
                    )

                    for channel, stream in jobs
                }

                for future in concurrent.futures.as_completed(
                    future_map
                ):

                    channel, stream = future_map[
                        future
                    ]

                    try:
                        result = future.result()

                    except Exception:
                        result = {
                            "alive": False,
                            "status": 0,
                        }

                    if not result.get(
                        "alive"
                    ):

                        stream["alive"] = False

                        log(
                            f"[WATCH DEAD] "
                            f"{channel['tvg_id']} "
                            f"{stream['url']} "
                            f"status="
                            f"{result.get('status')}"
                        )

                        changed = True

            # После проверки запускаем failover
            # только для каналов, где больше
            # нет живого потока.
            recovered = run_failover_pass(
                channels,
                cached_sources,
                workers,
                timeout,
                cache,
            )

            if recovered:
                changed = True

            if changed:

                write_playlist(
                    channels,
                    output,
                )

                save_cache(
                    cache_path,
                    cache,
                )

            time.sleep(interval)

        except KeyboardInterrupt:

            log("[WATCH] остановка")

            break

        except Exception as exc:

            log(
                f"[WATCH ERROR] {exc}"
            )

            time.sleep(interval)


# ============================================================================
# MAIN
# ============================================================================

def main():
    global LOG_FILE

    parser = argparse.ArgumentParser(
        description=(
            "IPTV automatic failover / "
            "NGENIX / RT / external discovery"
        )
    )

    parser.add_argument(
        "--playlist",
        "-p",
        required=True,
        help="Исходный M3U или URL",
    )

    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT,
        help="Выходной M3U",
    )

    parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Постоянный мониторинг",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Интервал watch в секундах",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Количество параллельных probe",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Timeout probe",
    )

    parser.add_argument(
        "--cache",
        default=DEFAULT_CACHE,
        help="JSON discovery cache",
    )

    parser.add_argument(
        "--log",
        default=DEFAULT_LOG,
        help="Файл лога",
    )

    args = parser.parse_args()

    LOG_FILE = args.log

    workers = max(
        4,
        min(args.workers, 64),
    )

    timeout = max(
        2,
        min(args.timeout, 30),
    )

    log(
        "=================================================="
    )

    log(
        "IPTV FALLBACK START"
    )

    log(
        f"playlist={args.playlist}"
    )

    log(
        f"output={args.output}"
    )

    log(
        f"workers={workers}"
    )

    log(
        f"timeout={timeout}"
    )

    # ----------------------------------------------------------------------
    # Load main playlist
    # ----------------------------------------------------------------------

    text = load_playlist(
        args.playlist
    )

    if not text:

        log(
            f"[FATAL] невозможно загрузить: "
            f"{args.playlist}"
        )

        return 2

    entries = parse_m3u(
        text,
        source="original",
    )

    if not entries:

        log(
            "[FATAL] M3U не содержит потоков"
        )

        return 3

    channels = group_channels(
        entries
    )

    log(
        f"[LOAD] channels={len(channels)} "
        f"streams={len(entries)}"
    )

    # ----------------------------------------------------------------------
    # Initial probe
    # ----------------------------------------------------------------------

    check_all_streams(
        channels,
        workers,
        timeout,
    )

    stats = statistics(
        channels
    )

    log(
        "[STATS] "
        f"channels={stats['channels']} "
        f"streams={stats['streams']} "
        f"alive={stats['alive_channels']} "
        f"dead={stats['dead_channels']}"
    )

    # ----------------------------------------------------------------------
    # External sources
    # ----------------------------------------------------------------------

    cached_sources = (
        load_external_sources()
    )

    # ----------------------------------------------------------------------
    # Discovery cache
    # ----------------------------------------------------------------------

    cache = load_cache(
        args.cache
    )

    # ----------------------------------------------------------------------
    # Failover
    # ----------------------------------------------------------------------

    recovered = run_failover_pass(
        channels,
        cached_sources,
        workers,
        timeout,
        cache,
    )

    log(
        f"[RESULT] recovered={recovered}"
    )

    # ----------------------------------------------------------------------
    # Output
    # ----------------------------------------------------------------------

    written = write_playlist(
        channels,
        args.output,
    )

    save_cache(
        args.cache,
        cache,
    )

    stats = statistics(
        channels
    )

    log(
        "[FINAL] "
        f"channels={stats['channels']} "
        f"alive={stats['alive_channels']} "
        f"dead={stats['dead_channels']} "
        f"written={written}"
    )

    # ----------------------------------------------------------------------
    # Watch
    # ----------------------------------------------------------------------

    if args.watch:

        watch_loop(
            channels,
            cached_sources,
            workers,
            timeout,
            max(10, args.interval),
            args.output,
            cache,
            args.cache,
        )

    log(
        "IPTV FALLBACK FINISHED"
    )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )