#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IPTV FALLBACK / AUTO RECOVERY

Накопительный failover-модуль для Зои.
- проверяет каждый исходный поток;
- для мёртвого канала ищет строго совпадающие кандидаты;
- генерирует CH_* / CH_R01_* варианты на известных NGENIX и RT host;
- проверяет HLS, HTTP-код и latency;
- сохраняет кандидатов и историю проверок в SQLite без удаления старых записей;
- создаёт новый merged_auto_N.m3u и fallback_report_N.json при каждом запуске;
- старые результаты никогда не перезаписываются.

Источники ограничены публичными M3U/URL, переданными пользователем или указанными
в конфигурации.
"""

import argparse
import concurrent.futures
import json
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_WORKERS = 48
DEFAULT_TIMEOUT = 5
DEFAULT_FETCH_TIMEOUT = 20
DEFAULT_INTERVAL = 60

DB_PATH = "fallback_candidates.db"
DEFAULT_OUTPUT = "merged_auto.m3u"
DEFAULT_REPORT = "fallback_report.json"


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
    "Wink/1.0 (Linux; Android 10; TV)"
)

UA_HLS = (
    "Mozilla/5.0 "
    "(compatible; IPTV-Fallback/2.0)"
)


# ============================================================
# EXTERNAL SOURCES
# ============================================================

EXTERNAL_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://naggdd.github.io/iptv/ru.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_russia.m3u8",
]


# ============================================================
# NGENIX / RT HOSTS
# ============================================================

NGENIX_HOSTS = [
    "rt-nw-klgr-htlive.cdn.ngenix.net",
    "s70790.cdn.ngenix.net",
]

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
        "тв 3",
        "тв-3",
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
        "viasat_nature",
    ],

    "viasat_explore": [
        "viju explore",
        "viasat explore",
        "viju_explore",
        "viasat_explore",
    ],

    "viasat_history": [
        "viju history",
        "viasat history",
        "viju_history",
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
    ],

    "fightbox": [
        "fightbox",
        "fight box",
    ],

    "trace_sport_stars": [
        "trace sport",
        "trace sport stars",
        "trace_sport_stars",
    ],

    "amedia_1": [
        "amedia 1",
        "amedia1",
        "a1",
    ],

    "amedia_2": [
        "amedia 2",
        "amedia2",
        "a2",
    ],

    "amedia_premium_hd": [
        "amedia premium",
        "amedia premium hd",
    ],

    "amedia_hit": [
        "amedia hit",
        "amedia hit hd",
        "amediahithd",
    ],

    "filmbox": [
        "filmbox",
        "film box",
    ],

    "filmbox_arthouse": [
        "filmbox arthouse",
        "film box arthouse",
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
    ],

    "365_dney_tv": [
        "365 дней",
        "365 дней тв",
        "365 dney",
        "365 dney tv",
    ],

    "galaxy": [
        "galaxy",
        "галактика",
    ],

    "sony_channel": [
        "sony channel",
        "sony",
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
    ],

    "viasat_sport": [
        "viju plus sport",
        "viju+ sport",
        "viasat sport",
        "viasat_sport",
    ],

    "vip_premiere": [
        "viju plus premiere",
        "viju+ premiere",
        "vip premiere",
        "viju premiere",
    ],

    "vip_megahit": [
        "viju plus megahit",
        "viju+ megahit",
        "vip megahit",
        "viju megahit",
    ],

    "vip_comedy": [
        "viju plus comedy",
        "viju+ comedy",
        "vip comedy",
        "viju comedy",
    ],

    "vip_serial": [
        "viju plus serial",
        "viju+ serial",
        "vip serial",
        "viju serial",
    ],
}


# ============================================================
# CHANNEL PATH ALIASES
# ============================================================

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
# NORMALIZATION
# ============================================================

def norm(value):
    value = (value or "").lower()

    value = value.replace("ё", "е")
    value = value.replace("_", " ")
    value = value.replace("-", " ")

    value = re.sub(
        r"\[[^]]*\]|\([^)]*\)",
        " ",
        value,
    )

    value = re.sub(
        r"[^\w\sа-яА-ЯёЁ]+",
        " ",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def cid(value):
    return norm(value).replace(" ", "_")


def clean_name(value):
    value = re.sub(
        r"^\s*\d+\.\s*",
        "",
        value or "",
    )

    value = re.sub(
        r"\s*\[(?:калининград|rt федеральный|федеральный)\]\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    return value.strip()


# ============================================================
# ALIAS RESOLUTION
# ============================================================

def aliases(channel):
    tid = cid(channel["tvg_id"])

    result = {
        norm(channel["tvg_id"]),
        norm(channel["name"]),
    }

    result.update(
        norm(x)
        for x in CHANNEL_ALIASES.get(tid, [])
    )

    return {
        x for x in result
        if x
    }


def path_aliases(channel):
    tid = cid(channel["tvg_id"])

    if tid in CHANNEL_PATH_ALIASES:
        return list(
            dict.fromkeys(
                CHANNEL_PATH_ALIASES[tid]
            )
        )

    raw = re.sub(
        r"[^A-Z0-9]+",
        "",
        tid.upper(),
    )

    if not raw:
        return []

    return [
        f"CH_{raw}",
        f"CH_R01_{raw}",
    ]


def channel_score(
    channel,
    name="",
    tvg_id="",
):
    target = cid(channel["tvg_id"])
    candidate_tid = cid(tvg_id)

    candidate_name = norm(name)
    known = aliases(channel)

    if candidate_tid and candidate_tid == target:
        return 1000

    if candidate_name and candidate_name in known:
        return 900

    if (
        candidate_name
        and candidate_name == norm(channel["name"])
    ):
        return 800

    return 0


# ============================================================
# HTTP
# ============================================================

def headers(url):
    host = (
        urlparse(url).hostname
        or ""
    ).lower()

    if (
        "ngenix.net" in host
        or "zabava" in host
    ):
        ua = UA_WINK_ZABAVA
    else:
        ua = UA_HLS

    return {
        "User-Agent": ua,
        "Accept": "*/*",
        "Connection": "close",
    }


def fetch(
    url,
    timeout=DEFAULT_FETCH_TIMEOUT,
):
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA_GENERIC,
                "Accept": "*/*",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=SSL_CTX,
        ) as response:

            return response.read().decode(
                "utf-8",
                errors="ignore",
            )

    except Exception:
        return None


# ============================================================
# HLS CHECK
# ============================================================

def check(
    url,
    timeout=DEFAULT_TIMEOUT,
):
    started = time.monotonic()

    try:
        request = urllib.request.Request(
            url,
            headers=headers(url),
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=SSL_CTX,
        ) as response:

            status = getattr(
                response,
                "status",
                200,
            )

            content_type = response.headers.get(
                "Content-Type",
                "",
            )

            data = response.read(8192)

        latency = round(
            time.monotonic() - started,
            3,
        )

        if not (
            200 <= status < 300
        ):
            return {
                "alive": False,
                "status": status,
                "reason": f"http_{status}",
                "latency": latency,
                "content_type": content_type,
            }

        lower = data.lower()

        hls = (
            b"#extm3u" in lower
            or b"#extinf" in lower
            or b"#ext-x-" in lower
        )

        media = (
            len(data) >= 100
            and (
                "mpegurl"
                in content_type.lower()
                or "video"
                in content_type.lower()
                or "audio"
                in content_type.lower()
            )
        )

        if hls:
            return {
                "alive": True,
                "status": status,
                "reason": "hls_ok",
                "latency": latency,
                "content_type": content_type,
            }

        if media:
            return {
                "alive": True,
                "status": status,
                "reason": "media_ok",
                "latency": latency,
                "content_type": content_type,
            }

        return {
            "alive": False,
            "status": status,
            "reason": "not_hls",
            "latency": latency,
            "content_type": content_type,
        }

    except urllib.error.HTTPError as exc:
        return {
            "alive": False,
            "status": exc.code,
            "reason": f"http_{exc.code}",
            "latency": round(
                time.monotonic() - started,
                3,
            ),
            "content_type": "",
        }

    except urllib.error.URLError as exc:
        return {
            "alive": False,
            "status": 0,
            "reason": f"url_error:{exc.reason}",
            "latency": round(
                time.monotonic() - started,
                3,
            ),
            "content_type": "",
        }

    except Exception as exc:
        return {
            "alive": False,
            "status": 0,
            "reason": type(exc).__name__,
            "latency": round(
                time.monotonic() - started,
                3,
            ),
            "content_type": "",
        }


# ============================================================
# M3U PARSER
# ============================================================

def parse_m3u(text):
    channels = []
    current = None

    for line in (text or "").splitlines():

        line = line.strip()

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
                name = clean_name(
                    line.split(
                        ",",
                        1,
                    )[1]
                )

            tvg_id = (
                tvg_match.group(1).strip()
                if tvg_match
                else cid(name)
            )

            current = {
                "tvg_id": tvg_id,
                "canonical_id": cid(tvg_id),
                "name": name,
                "group": (
                    group_match.group(1).strip()
                    if group_match
                    else ""
                ),
                "streams": [],
                "fallbacks": [],
            }

            channels.append(current)

        elif (
            current
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
                }
            )

    return channels


# ============================================================
# CANDIDATE
# ============================================================

def candidate(
    url,
    channel,
    source,
    name="",
    tvg_id="",
    derivation="",
):
    path_score = 0

    current_path = (
        urlparse(url)
        .path
        .upper()
        .rstrip("/")
    )

    for alias in path_aliases(channel):

        expected = (
            f"/HLS/"
            f"{alias.upper()}/"
            f"VARIANT.M3U8"
        )

        if current_path == expected:

            path_score = (
                750
                if alias.upper().startswith(
                    "CH_R01_"
                )
                else 700
            )

            break

    score = max(
        channel_score(
            channel,
            name,
            tvg_id,
        ),
        path_score,
    )

    return {
        "channel": channel["tvg_id"],
        "name": channel["name"],
        "url": url,
        "source": source,
        "candidate_name": name,
        "candidate_tvg_id": tvg_id,
        "derivation": derivation,
        "score": score,
        "alive": False,
        "verified": False,
        "reason": "not_checked",
    }


# ============================================================
# URL GENERATION
# ============================================================

def gen_urls(
    channel,
    hosts,
):
    result = []

    for host in hosts:

        host = host.strip()

        if not host:
            continue

        if "://" in host:
            hostname = urlparse(
                host
            ).netloc
        else:
            hostname = host.split(
                "/",
                1,
            )[0]

        for path_alias in path_aliases(
            channel
        ):

            path = (
                f"/hls/"
                f"{path_alias}/"
                f"variant.m3u8"
            )

            for scheme in (
                "https",
                "http",
            ):

                url = (
                    f"{scheme}://"
                    f"{hostname}"
                    f"{path}"
                )

                derivation = (
                    f"{hostname}:"
                    f"{path_alias}:"
                    f"{scheme}"
                )

                result.append(
                    (
                        url,
                        derivation,
                    )
                )

    return list(
        dict.fromkeys(result)
    )


# ============================================================
# EXTERNAL M3U
# ============================================================

def parse_external(
    text,
    channel,
    source,
):
    result = []

    current_name = ""
    current_tvg_id = ""

    for line in (text or "").splitlines():

        line = line.strip()

        if line.startswith("#EXTINF"):

            tvg_match = re.search(
                r'tvg-id="([^"]*)"',
                line,
                flags=re.IGNORECASE,
            )

            current_tvg_id = (
                tvg_match.group(1)
                if tvg_match
                else ""
            )

            current_name = clean_name(
                line.split(
                    ",",
                    1,
                )[1]
                if "," in line
                else ""
            )

        elif line.startswith(
            (
                "http://",
                "https://",
            )
        ):

            score = channel_score(
                channel,
                current_name,
                current_tvg_id,
            )

            if score:

                result.append(
                    candidate(
                        line,
                        channel,
                        source,
                        current_name,
                        current_tvg_id,
                        "external_m3u",
                    )
                )

            current_name = ""
            current_tvg_id = ""

    return result


# ============================================================
# DATABASE
# ============================================================

def utc():
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(),
    )


def init_db(path):
    db = sqlite3.connect(
        path,
        timeout=30,
    )

    db.execute(
        "PRAGMA journal_mode=WAL"
    )

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_utc TEXT NOT NULL,
            finished_utc TEXT,
            playlist TEXT,
            output TEXT,
            report TEXT
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            channel_id TEXT NOT NULL,
            channel_name TEXT,
            tvg_id TEXT,

            url TEXT NOT NULL UNIQUE,

            host TEXT,
            provider TEXT,

            source TEXT,
            discovered_from TEXT,
            derivation TEXT,

            score INTEGER DEFAULT 0,

            first_seen TEXT NOT NULL,
            last_checked TEXT,

            last_status INTEGER,
            last_alive INTEGER DEFAULT 0,
            last_reason TEXT,

            latency REAL,
            hls_valid INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS candidate_sources (
            candidate_id INTEGER,
            run_id INTEGER,
            source TEXT,
            discovered_from TEXT,
            seen_utc TEXT,

            PRIMARY KEY (
                candidate_id,
                run_id,
                source,
                discovered_from
            )
        );

        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            candidate_id INTEGER,
            run_id INTEGER,

            checked_utc TEXT,

            status INTEGER,
            alive INTEGER,
            hls_valid INTEGER,

            reason TEXT,
            latency REAL
        );

        CREATE TABLE IF NOT EXISTS aliases (
            channel_id TEXT,
            alias TEXT,

            first_seen TEXT,
            last_seen TEXT,

            PRIMARY KEY (
                channel_id,
                alias
            )
        );

        CREATE TABLE IF NOT EXISTS hosts (
            host TEXT PRIMARY KEY,

            provider TEXT,

            first_seen TEXT,
            last_seen TEXT
        );
        """
    )

    db.commit()

    return db


def db_run(
    db,
    playlist,
    output,
    report,
):
    cursor = db.execute(
        """
        INSERT INTO runs (
            started_utc,
            playlist,
            output,
            report
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            utc(),
            playlist,
            output,
            report,
        ),
    )

    db.commit()

    return cursor.lastrowid


def db_save_aliases_hosts(
    db,
    channel,
    candidates,
    run_id,
):
    now = utc()

    # --------------------------------------------------------
    # ALIASES
    # --------------------------------------------------------

    for alias in aliases(channel):

        db.execute(
            """
            INSERT INTO aliases (
                channel_id,
                alias,
                first_seen,
                last_seen
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT (
                channel_id,
                alias
            )
            DO UPDATE SET
                last_seen = excluded.last_seen
            """,
            (
                cid(channel["tvg_id"]),
                alias,
                now,
                now,
            ),
        )

    # --------------------------------------------------------
    # HOSTS / CANDIDATES
    # --------------------------------------------------------

    for item in candidates:

        host = (
            urlparse(
                item["url"]
            ).hostname
            or ""
        )

        if (
            "ngenix.net"
            in host
        ):
            provider = "NGENIX"

        elif (
            "rt.ru"
            in host
        ):
            provider = "Rostelecom"

        else:
            provider = "external"

        if host:

            db.execute(
                """
                INSERT INTO hosts (
                    host,
                    provider,
                    first_seen,
                    last_seen
                )
                VALUES (?, ?, ?, ?)

                ON CONFLICT(host)
                DO UPDATE SET
                    last_seen = excluded.last_seen
                """,
                (
                    host,
                    provider,
                    now,
                    now,
                ),
            )

        db.execute(
            """
            INSERT INTO candidates (
                channel_id,
                channel_name,
                tvg_id,
                url,
                host,
                provider,
                source,
                discovered_from,
                derivation,
                score,
                first_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(url)
            DO UPDATE SET
                channel_name = excluded.channel_name,
                score = MAX(
                    candidates.score,
                    excluded.score
                )
            """,
            (
                cid(channel["tvg_id"]),
                channel["name"],
                channel["tvg_id"],
                item["url"],
                host,
                provider,
                item.get(
                    "source",
                    "",
                ),
                item.get(
                    "source",
                    "",
                ),
                item.get(
                    "derivation",
                    "",
                ),
                item.get(
                    "score",
                    0,
                ),
                now,
            ),
        )

        db.commit()

        row = db.execute(
            """
            SELECT id
            FROM candidates
            WHERE url = ?
            """,
            (
                item["url"],
            ),
        ).fetchone()

        if row:

            candidate_id = row[0]

            db.execute(
                """
                INSERT OR IGNORE INTO
                candidate_sources (
                    candidate_id,
                    run_id,
                    source,
                    discovered_from,
                    seen_utc
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    run_id,
                    item.get(
                        "source",
                        "",
                    ),
                    item.get(
                        "source",
                        "",
                    ),
                    now,
                ),
            )

    db.commit()


def db_record_checks(
    db,
    run_id,
    checked,
):
    now = utc()

    for item in checked:

        row = db.execute(
            """
            SELECT id
            FROM candidates
            WHERE url = ?
            """,
            (
                item["url"],
            ),
        ).fetchone()

        if not row:
            continue

        candidate_id = row[0]

        hls_valid = int(
            item.get("alive")
            and item.get("reason")
            == "hls_ok"
        )

        db.execute(
            """
            UPDATE candidates
            SET
                last_checked = ?,
                last_status = ?,
                last_alive = ?,
                last_reason = ?,
                latency = ?,
                hls_valid = ?,
                score = MAX(
                    score,
                    ?
                )
            WHERE id = ?
            """,
            (
                now,
                item.get(
                    "status",
                    0,
                ),
                int(
                    item.get(
                        "alive",
                        False,
                    )
                ),
                item.get(
                    "reason",
                    "",
                ),
                item.get(
                    "latency",
                    0,
                ),
                hls_valid,
                item.get(
                    "score",
                    0,
                ),
                candidate_id,
            ),
        )

        db.execute(
            """
            INSERT INTO checks (
                candidate_id,
                run_id,
                checked_utc,
                status,
                alive,
                hls_valid,
                reason,
                latency
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                run_id,
                now,
                item.get(
                    "status",
                    0,
                ),
                int(
                    item.get(
                        "alive",
                        False,
                    )
                ),
                hls_valid,
                item.get(
                    "reason",
                    "",
                ),
                item.get(
                    "latency",
                    0,
                ),
            ),
        )

    db.commit()


def db_known_candidates(
    db,
    channel,
):
    rows = db.execute(
        """
        SELECT
            url,
            source,
            score,
            last_alive,
            last_status,
            last_reason
        FROM candidates
        WHERE channel_id = ?
        ORDER BY
            last_alive DESC,
            score DESC,
            latency ASC
        """,
        (
            cid(
                channel["tvg_id"]
            ),
        ),
    ).fetchall()

    result = []

    for row in rows:

        result.append(
            {
                "channel": channel["tvg_id"],
                "name": channel["name"],
                "url": row[0],
                "source": row[1] or "db",
                "score": row[2] or 0,
                "candidate_name": "",
                "candidate_tvg_id": channel[
                    "tvg_id"
                ],
                "derivation": "db_memory",
                "alive": False,
                "verified": False,
                "reason": "db_unchecked",
            }
        )

    return result


# ============================================================
# EXTERNAL SOURCES
# ============================================================

def load_external_sources(
    urls,
):
    result = []

    if not urls:
        return result

    workers = min(
        8,
        len(urls),
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        futures = {
            pool.submit(
                fetch,
                url,
            ): url
            for url in urls
        }

        for future in concurrent.futures.as_completed(
            futures
        ):

            source_url = futures[
                future
            ]

            try:
                text = future.result()
            except Exception:
                text = None

            if text:

                result.append(
                    {
                        "url": source_url,
                        "text": text,
                    }
                )

    return result


# ============================================================
# CANDIDATE VERIFICATION
# ============================================================

def verify(
    candidates,
    workers,
    timeout,
):
    if not candidates:
        return []

    result = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        futures = {
            pool.submit(
                check,
                item["url"],
                timeout,
            ): item
            for item in candidates
        }

        for future in concurrent.futures.as_completed(
            futures
        ):

            item = dict(
                futures[future]
            )

            try:
                status = future.result()

            except Exception as exc:

                status = {
                    "alive": False,
                    "status": 0,
                    "reason": (
                        f"worker_error:"
                        f"{type(exc).__name__}"
                    ),
                    "latency": 0,
                }

            item.update(status)
            item["verified"] = True

            result.append(item)

    return result


# ============================================================
# DISCOVERY
# ============================================================

def discover(
    channel,
    external_sources,
    db,
    ngenix_hosts,
    rt_hosts,
    workers,
    timeout,
    run_id,
):
    candidates = []

    # --------------------------------------------------------
    # DATABASE MEMORY
    # --------------------------------------------------------

    candidates.extend(
        db_known_candidates(
            db,
            channel,
        )
    )

    # --------------------------------------------------------
    # NGENIX
    # --------------------------------------------------------

    for url, derivation in gen_urls(
        channel,
        ngenix_hosts,
    ):

        candidates.append(
            candidate(
                url,
                channel,
                "generated_ngenix",
                derivation=derivation,
            )
        )

    # --------------------------------------------------------
    # ROSTELECOM
    # --------------------------------------------------------

    for url, derivation in gen_urls(
        channel,
        rt_hosts,
    ):

        candidates.append(
            candidate(
                url,
                channel,
                "generated_rostelecom",
                derivation=derivation,
            )
        )

    # --------------------------------------------------------
    # EXTERNAL M3U
    # --------------------------------------------------------

    for source in external_sources:

        candidates.extend(
            parse_external(
                source["text"],
                channel,
                source["url"],
            )
        )

    # --------------------------------------------------------
    # DEDUP
    # --------------------------------------------------------

    unique = {}

    for item in candidates:

        url = item["url"]

        if url not in unique:
            unique[url] = item
        else:
            unique[url]["score"] = max(
                unique[url].get(
                    "score",
                    0,
                ),
                item.get(
                    "score",
                    0,
                ),
            )

    candidates = list(
        unique.values()
    )

    # --------------------------------------------------------
    # STRICT MATCH
    # --------------------------------------------------------

    candidates = [
        item
        for item in candidates
        if item.get(
            "score",
            0,
        ) > 0
    ]

    candidates.sort(
        key=lambda item: (
            -item.get(
                "score",
                0,
            ),
            item["url"],
        )
    )

    # --------------------------------------------------------
    # STORE DISCOVERED CANDIDATES
    # --------------------------------------------------------

    db_save_aliases_hosts(
        db,
        channel,
        candidates,
        run_id,
    )

    # --------------------------------------------------------
    # VERIFY ALL
    # --------------------------------------------------------

    checked = verify(
        candidates,
        workers,
        timeout,
    )

    db_record_checks(
        db,
        run_id,
        checked,
    )

    alive = [
        item
        for item in checked
        if item.get("alive")
    ]

    alive.sort(
        key=lambda item: (
            -item.get(
                "score",
                0,
            ),
            item.get(
                "latency",
                999,
            ),
        )
    )

    return alive, checked


# ============================================================
# ORIGINAL STREAM CHECK
# ============================================================

def check_originals(
    channels,
    workers,
    timeout,
):
    jobs = [
        (channel, stream)
        for channel in channels
        for stream in channel["streams"]
    ]

    if not jobs:
        return

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        futures = {
            pool.submit(
                check,
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

            channel, stream = futures[
                future
            ]

            try:
                result = future.result()

            except Exception as exc:

                result = {
                    "alive": False,
                    "status": 0,
                    "reason": (
                        f"worker_error:"
                        f"{type(exc).__name__}"
                    ),
                    "latency": 0,
                }

            stream.update(result)


# ============================================================
# OUTPUT FILE NUMBERING
# ============================================================

def next_pair(
    output,
    report,
):
    output_path = Path(output)
    report_path = Path(report)

    number = 0

    while True:

        if number == 0:

            current_output = output_path
            current_report = report_path

        else:

            current_output = (
                output_path.with_name(
                    f"{output_path.stem}_"
                    f"{number}"
                    f"{output_path.suffix or '.m3u'}"
                )
            )

            current_report = (
                report_path.with_name(
                    f"{report_path.stem}_"
                    f"{number}"
                    f"{report_path.suffix or '.json'}"
                )
            )

        if (
            not current_output.exists()
            and not current_report.exists()
        ):
            return (
                current_output,
                current_report,
                number,
            )

        number += 1


# ============================================================
# PLAYLIST
# ============================================================

def make_playlist(
    channels,
    output_path,
):
    lines = [
        "#EXTM3U",
        "#EXT-X-FALLBACK: generated by IpTV_fallback.py",
    ]

    index = 1
    written = set()

    for channel in channels:

        original_alive = [
            stream
            for stream in channel["streams"]
            if stream.get("alive")
        ]

        if original_alive:

            selected = original_alive

        else:

            selected = channel.get(
                "fallbacks",
                [],
            )

        for stream in selected:

            url = stream["url"]

            if url in written:
                continue

            source = stream.get(
                "source",
                "original",
            )

            if source == "original":
                tag = ""
            else:
                tag = (
                    " [fallback:"
                    f"{source}"
                    "]"
                )

            lines.append(
                f'#EXTINF:-1 '
                f'tvg-id="{channel["tvg_id"]}" '
                f'group-title="{channel["group"]}",'
                f'{index}. '
                f'{channel["name"]}'
                f'{tag}'
            )

            lines.append(url)

            written.add(url)
            index += 1

    output_path.write_text(
        "\n".join(lines)
        + "\n",
        encoding="utf-8",
    )

    return index - 1


# ============================================================
# JSON REPORT
# ============================================================

def report_json(
    channels,
    run_id,
    path,
    output,
    started,
    db_path,
):
    report = {
        "run_id": run_id,
        "started_utc": started,
        "finished_utc": utc(),
        "database": db_path,
        "output": str(output),

        "summary": {
            "channels": len(channels),
            "alive": 0,
            "recovered": 0,
            "unresolved": 0,
            "output_entries": 0,
        },

        "channels": [],
    }

    for channel in channels:

        originals = []

        for stream in channel[
            "streams"
        ]:

            originals.append(
                {
                    "url": stream["url"],
                    "alive": stream.get(
                        "alive",
                        False,
                    ),
                    "status": stream.get(
                        "status",
                        0,
                    ),
                    "reason": stream.get(
                        "reason",
                        "",
                    ),
                    "latency": stream.get(
                        "latency",
                        0,
                    ),
                }
            )

        original_alive = any(
            item["alive"]
            for item in originals
        )

        fallbacks = channel.get(
            "fallbacks",
            [],
        )

        if original_alive:

            report["summary"]["alive"] += 1

        elif fallbacks:

            report["summary"][
                "recovered"
            ] += 1

        else:

            report["summary"][
                "unresolved"
            ] += 1

        if original_alive:

            selected_for_output = [
                item
                for item in channel[
                    "streams"
                ]
                if item.get("alive")
            ]

        else:

            selected_for_output = fallbacks

        report["summary"][
            "output_entries"
        ] += len(
            selected_for_output
        )

        fallback_report = []

        for item in fallbacks:

            fallback_report.append(
                {
                    key: value
                    for key, value
                    in item.items()
                    if key != "_raw"
                }
            )

        selected = None

        if fallbacks:
            selected = fallbacks[0]["url"]

        else:

            for stream in channel[
                "streams"
            ]:

                if stream.get("alive"):

                    selected = stream[
                        "url"
                    ]

                    break

        report["channels"].append(
            {
                "tvg_id": channel[
                    "tvg_id"
                ],
                "name": channel[
                    "name"
                ],
                "group": channel[
                    "group"
                ],

                "original": originals,

                "fallbacks": fallback_report,

                "selected": selected,
            }
        )

    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# ONE RECOVERY RUN
# ============================================================

def run_once(
    args,
    db,
    ngenix_hosts,
    rt_hosts,
    source_urls,
):
    started = utc()

    output_path, report_path, number = (
        next_pair(
            args.output,
            args.report,
        )
    )

    run_id = db_run(
        db,
        args.playlist,
        str(output_path),
        str(report_path),
    )

    playlist_path = Path(
        args.playlist
    )

    if playlist_path.exists():

        text = playlist_path.read_text(
            encoding="utf-8-sig",
            errors="ignore",
        )

    else:

        text = fetch(
            args.playlist,
            timeout=DEFAULT_FETCH_TIMEOUT,
        )

    if not text:

        raise RuntimeError(
            "Не удалось загрузить "
            f"playlist: {args.playlist}"
        )

    channels = parse_m3u(
        text
    )

    if not channels:

        raise RuntimeError(
            "В плейлисте не найдено "
            "каналов"
        )

    print(
        f"RUN #{run_id}: "
        f"каналов={len(channels)}"
    )

    # --------------------------------------------------------
    # ORIGINAL CHECK
    # --------------------------------------------------------

    check_originals(
        channels,
        args.workers,
        args.timeout,
    )

    dead_channels = [
        channel
        for channel in channels
        if not any(
            stream.get("alive")
            for stream in channel[
                "streams"
            ]
        )
    ]

    print(
        "Исходные живые="
        f"{len(channels) - len(dead_channels)}"
        " | требуют fallback="
        f"{len(dead_channels)}"
    )

    # --------------------------------------------------------
    # EXTERNAL SOURCES
    # --------------------------------------------------------

    external_sources = (
        load_external_sources(
            source_urls
        )
    )

    print(
        "Внешних M3U загружено="
        f"{len(external_sources)}"
    )

    # --------------------------------------------------------
    # RECOVERY
    # --------------------------------------------------------

    for number_index, channel in enumerate(
        dead_channels,
        start=1,
    ):

        print(
            f"[{number_index}/"
            f"{len(dead_channels)}] "
            f"{channel['name']} "
            f"({channel['tvg_id']})"
        )

        alive, checked = discover(
            channel,
            external_sources,
            db,
            ngenix_hosts,
            rt_hosts,
            args.workers,
            args.timeout,
            run_id,
        )

        channel["fallbacks"] = alive

        if alive:

            best = alive[0]

            print(
                "  OK -> "
                f"{best['url']}"
            )

            print(
                "  score="
                f"{best.get('score', 0)} "
                "source="
                f"{best.get('source', '')} "
                "reason="
                f"{best.get('reason', '')}"
            )

        else:

            print(
                "  FAIL -> "
                "подходящий живой "
                "поток не найден"
            )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    entries = make_playlist(
        channels,
        output_path,
    )

    report_json(
        channels,
        run_id,
        report_path,
        output_path,
        started,
        args.db,
    )

    db.execute(
        """
        UPDATE runs
        SET finished_utc = ?
        WHERE run_id = ?
        """,
        (
            utc(),
            run_id,
        ),
    )

    db.commit()

    print(
        f"Готово: {output_path}"
    )

    print(
        f"entries={entries}"
    )

    print(
        f"report={report_path}"
    )

    print(
        f"DB={args.db}"
    )

    return channels


# ============================================================
# CLI
# ============================================================

def build_parser():

    parser = argparse.ArgumentParser(
        description=(
            "IPTV automatic "
            "failover / recovery"
        )
    )

    parser.add_argument(
        "--playlist",
        "-p",
        required=True,
    )

    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT,
    )

    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT,
    )

    parser.add_argument(
        "--db",
        default=DB_PATH,
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
    )

    parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
    )

    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        default=[],
        help=(
            "Дополнительный "
            "публичный M3U URL"
        ),
    )

    parser.add_argument(
        "--ngenix-host",
        action="append",
        default=[],
        help=(
            "Дополнительный "
            "NGENIX host"
        ),
    )

    parser.add_argument(
        "--rt-host",
        action="append",
        default=[],
        help=(
            "Дополнительный "
            "RT/Rostelecom host"
        ),
    )

    return parser


# ============================================================
# MAIN
# ============================================================

def main():

    args = build_parser().parse_args()

    args.workers = max(
        1,
        min(
            args.workers,
            128,
        ),
    )

    args.timeout = max(
        2,
        min(
            args.timeout,
            30,
        ),
    )

    args.interval = max(
        10,
        args.interval,
    )

    source_urls = list(
        dict.fromkeys(
            EXTERNAL_SOURCES
            + args.sources
        )
    )

    ngenix_hosts = list(
        dict.fromkeys(
            NGENIX_HOSTS
            + args.ngenix_host
        )
    )

    rt_hosts = list(
        dict.fromkeys(
            RT_HOSTS
            + args.rt_host
        )
    )

    db = init_db(
        args.db
    )

    print("=" * 72)
    print(
        "IPTV FALLBACK / AUTO RECOVERY"
    )
    print(
        f"NGENIX hosts="
        f"{len(ngenix_hosts)}"
        " | RT hosts="
        f"{len(rt_hosts)}"
        " | sources="
        f"{len(source_urls)}"
    )
    print("=" * 72)

    try:

        channels = run_once(
            args,
            db,
            ngenix_hosts,
            rt_hosts,
            source_urls,
        )

        if args.watch:

            while True:

                time.sleep(
                    args.interval
                )

                print(
                    "[WATCH] "
                    "новый проход"
                )

                channels = run_once(
                    args,
                    db,
                    ngenix_hosts,
                    rt_hosts,
                    source_urls,
                )

        return 0

    except KeyboardInterrupt:

        print(
            "\nОстановка."
        )

        return 0

    except Exception as exc:

        print(
            f"FATAL: "
            f"{type(exc).__name__}: "
            f"{exc}",
            file=sys.stderr,
        )

        return 1

    finally:

        db.close()


if __name__ == "__main__":
    sys.exit(main())