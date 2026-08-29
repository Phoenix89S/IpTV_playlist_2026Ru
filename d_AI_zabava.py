#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
===============================================================================
d_AI_zabava.py
===============================================================================

СКАЛА ДРЕГ / D-AI ZABAVA
Modernized discovery + telemetry + HLS validation + ML dataset + M3U.

OUTPUT:
    d_AI_zabava.json
    d_AI_zabava.txt
    d_AI_zabava.m3u

Основные изменения:
    • расширенная генерация URL-кандидатов;
    • улучшенная HLS/M3U8 validation;
    • Content-Type учитывается как дополнительный признак;
    • BOM/whitespace корректно обрабатываются;
    • discovery score;
    • channel-level aggregation;
    • несколько рабочих URL одного канала;
    • M3U содержит найденные каналы;
    • дубликаты URL удаляются;
    • лучший URL канала идёт первым;
    • сохраняются все успешные варианты;
    • JSON содержит discovery summary;
    • TXT содержит список найденных каналов;
    • tvg-id / tvg-name / tvg-chno / group-title сохраняются.

HTTP 200 сам по себе НЕ является достаточным условием.
Рабочим считается URL, который распознан как HLS/M3U8.

===============================================================================
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import socket
import sys
import time
import traceback
import uuid

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:
    print("ERROR: требуется библиотека requests")
    print("Установите: pip install requests")
    raise

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


# =============================================================================
# FILES
# =============================================================================

SCRIPT_NAME = "d_AI_zabava.py"

OUTPUT_JSON = "d_AI_zabava.json"
OUTPUT_TXT = "d_AI_zabava.txt"
OUTPUT_M3U = "d_AI_zabava.m3u"


# =============================================================================
# VERSION
# =============================================================================

SKALA_NAME = "СКАЛА ДРЕГ"
VERSION = "4.2.0"
SCHEMA_VERSION = "4.2"
ENGINE_NAME = "D-AI ZABAVA"


# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_TIMEOUT = 4.0
DEFAULT_WORKERS = 16

MAX_INSPECT_BYTES = 65536

# Сколько рабочих URL максимум сохранять для одного канала.
# 0 = все найденные.
MAX_URLS_PER_CHANNEL = 0

USER_AGENT = (
    "SKALA-DREG/4.2 "
    "(D-AI-ZABAVA; telemetry; HLS validator)"
)

NGENIX_NODES = [
    f"s{i}.cdn.ngenix.net"
    for i in range(70378, 70391)
]


# =============================================================================
# CHANNEL CANON
# =============================================================================

CHANNELS: List[Dict[str, Any]] = [
    {
        "chno": 1,
        "name": "Первый канал",
        "key": "perviy",
        "tvg_id": "perviy",
        "aliases": [
            "1tv",
            "ch_1tv",
            "perviy",
            "pervy",
            "perviy_kanal",
        ],
    },
    {
        "chno": 2,
        "name": "Россия 1",
        "key": "rossiya_1",
        "tvg_id": "rossiya_1",
        "aliases": [
            "rossiya_1",
            "russia_1",
            "rossiya1",
            "russia1",
            "ch_russia1",
        ],
    },
    {
        "chno": 3,
        "name": "Матч ТВ",
        "key": "match_tv",
        "tvg_id": "match_tv",
        "aliases": [
            "match_tv",
            "matchtv",
            "match",
            "ch_matchtv",
        ],
    },
    {
        "chno": 4,
        "name": "НТВ",
        "key": "ntv",
        "tvg_id": "ntv",
        "aliases": [
            "ntv",
            "ntv_hd",
            "ch_ntv",
        ],
    },
    {
        "chno": 5,
        "name": "Пятый канал",
        "key": "pyatyi",
        "tvg_id": "pyatyi",
        "aliases": [
            "pyatyi",
            "5tv",
            "5kanal",
            "ch_5tv",
        ],
    },
    {
        "chno": 6,
        "name": "Россия К",
        "key": "rossiya_k",
        "tvg_id": "rossiya_k",
        "aliases": [
            "rossiya_k",
            "kultura",
            "rossiya_kultura",
            "ch_russiak",
        ],
    },
    {
        "chno": 7,
        "name": "Россия 24",
        "key": "rossiya_24",
        "tvg_id": "rossiya_24",
        "aliases": [
            "rossiya_24",
            "rossiya24",
            "russia24",
            "ch_russia24",
        ],
    },
    {
        "chno": 8,
        "name": "Карусель",
        "key": "karusel",
        "tvg_id": "karusel",
        "aliases": [
            "karusel",
            "karusel_tv",
            "ch_karusel",
        ],
    },
    {
        "chno": 9,
        "name": "ОТР",
        "key": "otr",
        "tvg_id": "otr",
        "aliases": [
            "otr",
            "otr_tv",
            "ch_otr",
        ],
    },
    {
        "chno": 10,
        "name": "ТВ Центр",
        "key": "tvc",
        "tvg_id": "tvc",
        "aliases": [
            "tvc",
            "tv_center",
            "tvcentr",
            "ch_tvc",
        ],
    },
    {
        "chno": 11,
        "name": "РЕН ТВ",
        "key": "rentv",
        "tvg_id": "rentv",
        "aliases": [
            "rentv",
            "ren_tv",
        ],
    },
]


# =============================================================================
# TIME
# =============================================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def moscow_datetime(dt: datetime) -> Optional[datetime]:
    if ZoneInfo is None:
        return None

    try:
        return dt.astimezone(
            ZoneInfo("Europe/Moscow")
        )
    except Exception:
        return None


def iso_msk(dt: datetime) -> str:
    msk = moscow_datetime(dt)

    if msk is None:
        return dt.astimezone(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    return msk.strftime(
        "%Y-%m-%d %H:%M:%S MSK"
    )


def timestamp_bundle(dt: datetime) -> Dict[str, Any]:

    msk = moscow_datetime(dt)

    return {
        "utc": iso_utc(dt),
        "msk": iso_msk(dt),
        "timezone": "Europe/Moscow",
        "utc_offset": "+03:00",
        "unix": dt.timestamp(),
        "epoch_ms": int(
            dt.timestamp() * 1000
        ),
        "msk_datetime": (
            msk.isoformat()
            if msk is not None
            else None
        ),
    }


# =============================================================================
# HASH
# =============================================================================

def sha256_text(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


# =============================================================================
# PATH GENERATION
# =============================================================================

def generate_paths(
    channel: Dict[str, Any],
) -> List[str]:

    aliases = list(
        channel.get(
            "aliases",
            [],
        )
    )

    key = channel["key"]

    # Canonical key ставится первым.
    tokens: List[str] = []

    for token in [key] + aliases:
        token = str(token).strip()

        if token and token not in tokens:
            tokens.append(token)

    result: List[str] = []

    patterns = [
        "/{token}/1/index.m3u8",
        "/{token}/2/index.m3u8",
        "/{token}/index.m3u8",
        "/hls/{token}/index.m3u8",
        "/hls/{token}/variant.m3u8",
        "/{token}/variant.m3u8",
        "/{token}/master.m3u8",
        "/hls/{token}/master.m3u8",
        "/{token}/playlist.m3u8",
        "/hls/{token}/playlist.m3u8",
    ]

    for token in tokens:
        for pattern in patterns:
            result.append(
                pattern.format(
                    token=token
                )
            )

    unique: List[str] = []
    seen = set()

    for path in result:

        if path in seen:
            continue

        seen.add(path)
        unique.append(path)

    return unique


# =============================================================================
# CANDIDATE FEATURES
# =============================================================================

def candidate_features(
    node: str,
    path: str,
    channel: Dict[str, Any],
) -> Dict[str, Any]:

    parsed = urlparse(
        f"https://{node}{path}"
    )

    path_lower = path.lower()

    parts = [
        x
        for x in path.split("/")
        if x
    ]

    filename = (
        parts[-1]
        if parts
        else ""
    )

    extension = (
        filename.rsplit(
            ".",
            1,
        )[-1].lower()
        if "." in filename
        else ""
    )

    node_match = re.search(
        r"s(\d+)",
        node,
    )

    node_numeric_id = (
        int(
            node_match.group(1)
        )
        if node_match
        else -1
    )

    return {
        "node_length": len(node),

        "node_numeric_id": (
            node_numeric_id
        ),

        "path_length": len(path),

        "path_depth": len(parts),

        "filename_length": len(
            filename
        ),

        "extension": extension,

        "contains_hls": int(
            "/hls/" in path_lower
        ),

        "contains_variant": int(
            "variant.m3u8"
            in path_lower
        ),

        "contains_master": int(
            "master.m3u8"
            in path_lower
        ),

        "contains_playlist": int(
            "playlist.m3u8"
            in path_lower
        ),

        "contains_index": int(
            "index.m3u8"
            in path_lower
        ),

        "contains_numeric_variant": int(
            bool(
                re.search(
                    r"/[0-9]+/index\.m3u8$",
                    path_lower,
                )
            )
        ),

        "contains_ch_prefix": int(
            bool(
                re.search(
                    r"/ch_[^/]+/",
                    path_lower,
                )
            )
        ),

        "contains_hd": int(
            "hd" in path_lower
        ),

        "contains_m3u8": int(
            ".m3u8" in path_lower
        ),

        "contains_index_name": int(
            filename == "index.m3u8"
        ),

        "contains_variant_name": int(
            filename == "variant.m3u8"
        ),

        "contains_master_name": int(
            filename == "master.m3u8"
        ),

        "url_length": len(
            f"https://{node}{path}"
        ),

        "hostname": (
            parsed.hostname or ""
        ),

        "channel_number": channel[
            "chno"
        ],

        "alias_count": len(
            channel.get(
                "aliases",
                [],
            )
        ),

        "channel_key_length": len(
            channel["key"]
        ),
    }


# =============================================================================
# HEADERS
# =============================================================================

def build_headers() -> Dict[str, str]:

    return {
        "User-Agent": USER_AGENT,

        "Accept": (
            "application/vnd.apple.mpegurl,"
            "application/x-mpegURL,"
            "audio/mpegurl,"
            "audio/x-mpegurl,"
            "text/plain,*/*"
        ),

        "Connection": "close",

        "Cache-Control": "no-cache",

        "Accept-Encoding": "identity",
    }


# =============================================================================
# HLS VALIDATION
# =============================================================================

def inspect_hls_body(
    body: bytes,
    content_type: str = "",
    final_url: str = "",
) -> Dict[str, Any]:

    inspected = body[
        :MAX_INSPECT_BYTES
    ]

    try:
        text = inspected.decode(
            "utf-8-sig",
            errors="replace",
        )
    except Exception:
        text = ""

    # Иногда playlist начинается с BOM или пробелов.
    text = text.lstrip(
        "\ufeff \t\r\n"
    )

    lower_text = text.lower()

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    first_line = (
        lines[0]
        if lines
        else ""
    )

    has_extm3u = (
        first_line.upper()
        == "#EXTM3U"
    )

    has_ext_x = (
        "#EXT-X-" in text.upper()
    )

    has_target_duration = (
        "#EXT-X-TARGETDURATION"
        in text.upper()
    )

    has_media_sequence = (
        "#EXT-X-MEDIA-SEQUENCE"
        in text.upper()
    )

    has_stream_inf = (
        "#EXT-X-STREAM-INF"
        in text.upper()
    )

    has_extinf = (
        "#EXTINF:" in text.upper()
    )

    has_endlist = (
        "#EXT-X-ENDLIST"
        in text.upper()
    )

    has_media_segment = False

    media_uri_count = 0
    m3u8_uri_count = 0

    for line in lines:

        if line.startswith("#"):
            continue

        uri_lower = line.lower()

        if (
            ".m3u8" in uri_lower
            or ".ts" in uri_lower
            or ".aac" in uri_lower
            or ".mp4" in uri_lower
            or ".m4s" in uri_lower
            or ".mp3" in uri_lower
        ):
            media_uri_count += 1

        if ".m3u8" in uri_lower:
            m3u8_uri_count += 1

        if (
            ".ts" in uri_lower
            or ".aac" in uri_lower
            or ".m4s" in uri_lower
        ):
            has_media_segment = True

    content_type_lower = (
        content_type.lower()
        if content_type
        else ""
    )

    content_type_hls = any(
        marker in content_type_lower
        for marker in (
            "mpegurl",
            "m3u8",
            "vnd.apple.mpegurl",
            "x-mpegurl",
        )
    )

    url_hls = (
        ".m3u8"
        in final_url.lower()
    )

    if has_stream_inf:
        playlist_type = "MASTER"

    elif (
        has_extinf
        or has_media_sequence
        or has_media_segment
    ):
        playlist_type = "MEDIA"

    else:
        playlist_type = "UNKNOWN"

    structural_score = 0

    if has_extm3u:
        structural_score += 35

    if has_ext_x:
        structural_score += 15

    if has_target_duration:
        structural_score += 10

    if has_media_sequence:
        structural_score += 10

    if has_stream_inf:
        structural_score += 15

    if has_extinf:
        structural_score += 15

    if media_uri_count > 0:
        structural_score += 10

    if content_type_hls:
        structural_score += 10

    if url_hls:
        structural_score += 5

    structural_score = min(
        structural_score,
        100,
    )

    # Более устойчивое распознавание:
    #
    # 1. Нормальный #EXTM3U + HLS tags;
    # 2. либо #EXTM3U + URI;
    # 3. либо явный HLS Content-Type + HLS structure.
    #
    # Не считаем простой HTTP 200 рабочим.

    valid_hls = bool(
        has_extm3u
        and (
            has_ext_x
            or has_extinf
            or media_uri_count > 0
        )
    )

    if not valid_hls:
        if (
            content_type_hls
            and has_ext_x
            and (
                media_uri_count > 0
                or has_extinf
                or has_stream_inf
            )
        ):
            valid_hls = True

    return {
        "content_bytes": len(body),

        "inspect_bytes": len(
            inspected
        ),

        "looks_like_m3u8": bool(
            has_extm3u
            or has_ext_x
            or content_type_hls
        ),

        "has_extm3u": has_extm3u,

        "has_ext_x": has_ext_x,

        "has_target_duration": (
            has_target_duration
        ),

        "has_media_sequence": (
            has_media_sequence
        ),

        "has_stream_inf": (
            has_stream_inf
        ),

        "has_extinf": has_extinf,

        "has_endlist": has_endlist,

        "has_media_segment": (
            has_media_segment
        ),

        "media_uri_count": (
            media_uri_count
        ),

        "m3u8_uri_count": (
            m3u8_uri_count
        ),

        "content_type_hls": (
            content_type_hls
        ),

        "url_hls": url_hls,

        "first_line": first_line[
            :200
        ],

        "playlist_type": playlist_type,

        "structural_score": (
            structural_score
        ),

        "valid_hls": valid_hls,

        "body_sha256": sha256_bytes(
            body
        ),
    }


# =============================================================================
# DISCOVERY SCORE
# =============================================================================

def calculate_discovery_score(
    event: Dict[str, Any],
) -> float:

    response = event.get(
        "response",
        {}
    )

    hls = event.get(
        "hls",
        {}
    )

    result = event.get(
        "result",
        {}
    )

    if not result.get(
        "success",
        False,
    ):
        return 0.0

    score = 0.0

    status = response.get(
        "status_code"
    )

    if status == 200:
        score += 30

    structural = float(
        hls.get(
            "structural_score",
            0,
        )
    )

    score += (
        structural * 0.5
    )

    if hls.get(
        "content_type_hls",
        False,
    ):
        score += 10

    if hls.get(
        "has_extm3u",
        False,
    ):
        score += 10

    latency = response.get(
        "latency_ms"
    )

    if latency is not None:

        if latency <= 100:
            score += 10

        elif latency <= 250:
            score += 7

        elif latency <= 500:
            score += 4

        elif latency <= 1000:
            score += 2

    score = min(
        score,
        100.0,
    )

    return round(
        score,
        3,
    )


# =============================================================================
# ERROR CLASSIFICATION
# =============================================================================

def classify_error(
    status_code: Optional[int],
    error_text: str,
    exception_type: Optional[str] = None,
) -> Dict[str, Any]:

    if (
        status_code == 200
        and not error_text
    ):
        return {
            "error_class": "NONE",
            "error_family": "SUCCESS",
            "retryable": False,
            "severity": 0,
        }

    if status_code == 404:
        return {
            "error_class": "HTTP_404",
            "error_family": "NOT_FOUND",
            "retryable": False,
            "severity": 2,
        }

    if status_code in (
        401,
        403,
    ):
        return {
            "error_class": f"HTTP_{status_code}",
            "error_family": "AUTH_OR_ACCESS",
            "retryable": False,
            "severity": 3,
        }

    if status_code in (
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    ):
        return {
            "error_class": f"HTTP_{status_code}",
            "error_family": "SERVER_OR_RATE_LIMIT",
            "retryable": True,
            "severity": 2,
        }

    if exception_type:

        name = (
            exception_type.lower()
        )

        if (
            "timeout" in name
            or "timedout" in name
        ):
            return {
                "error_class": "TIMEOUT",
                "error_family": "NETWORK",
                "retryable": True,
                "severity": 2,
            }

        if (
            "connection" in name
            or "connect" in name
        ):
            return {
                "error_class": "CONNECTION_ERROR",
                "error_family": "NETWORK",
                "retryable": True,
                "severity": 2,
            }

        if "dns" in name:
            return {
                "error_class": "DNS_ERROR",
                "error_family": "DNS",
                "retryable": True,
                "severity": 3,
            }

    text = (
        error_text.lower()
        if error_text
        else ""
    )

    if "timeout" in text:
        return {
            "error_class": "TIMEOUT",
            "error_family": "NETWORK",
            "retryable": True,
            "severity": 2,
        }

    if (
        "dns" in text
        or "name or service" in text
        or "temporary failure in name" in text
        or "nodename nor servname" in text
    ):
        return {
            "error_class": "DNS_ERROR",
            "error_family": "DNS",
            "retryable": True,
            "severity": 3,
        }

    if (
        "connection" in text
        or "connect" in text
    ):
        return {
            "error_class": "CONNECTION_ERROR",
            "error_family": "NETWORK",
            "retryable": True,
            "severity": 2,
        }

    return {
        "error_class": (
            f"HTTP_{status_code}"
            if status_code is not None
            else "REQUEST_ERROR"
        ),
        "error_family": "OTHER",
        "retryable": True,
        "severity": 2,
    }


# =============================================================================
# HTTP PROBE
# =============================================================================

def probe_candidate(
    candidate: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:

    url = candidate["url"]

    started_dt = utc_now()
    started_perf = time.perf_counter()

    status_code: Optional[int] = None

    error = ""
    exception_type: Optional[str] = None

    response_headers: Dict[str, Any] = {}

    hls_info: Dict[str, Any] = {}

    dns_ms: Optional[float] = None
    connect_ms: Optional[float] = None
    read_ms: Optional[float] = None

    content_type = ""

    redirect_count = 0

    final_url = url

    response_content_length = 0

    try:

        # ---------------------------------------------------------------------
        # DNS
        # ---------------------------------------------------------------------

        dns_start = time.perf_counter()

        try:
            socket.gethostbyname(
                candidate["node"]
            )
        except Exception:
            pass

        dns_ms = round(
            (
                time.perf_counter()
                - dns_start
            ) * 1000,
            3,
        )

        # ---------------------------------------------------------------------
        # HTTP
        # ---------------------------------------------------------------------

        request_start = (
            time.perf_counter()
        )

        response = requests.get(
            url,
            headers=build_headers(),
            timeout=timeout,
            allow_redirects=True,
            stream=False,
        )

        request_elapsed = (
            time.perf_counter()
            - request_start
        )

        status_code = (
            response.status_code
        )

        final_url = str(
            response.url
        )

        redirect_count = len(
            response.history
        )

        content_type = (
            response.headers.get(
                "Content-Type",
                "",
            )
        )

        response_content_length = (
            len(response.content)
        )

        response_headers = {
            "content_type": content_type,

            "content_length": (
                response.headers.get(
                    "Content-Length",
                    "",
                )
            ),

            "cache_control": (
                response.headers.get(
                    "Cache-Control",
                    "",
                )
            ),

            "server": (
                response.headers.get(
                    "Server",
                    "",
                )
            ),

            "etag": (
                response.headers.get(
                    "ETag",
                    "",
                )
            ),

            "last_modified": (
                response.headers.get(
                    "Last-Modified",
                    "",
                )
            ),

            "location": (
                response.headers.get(
                    "Location",
                    "",
                )
            ),

            "content_encoding": (
                response.headers.get(
                    "Content-Encoding",
                    "",
                )
            ),

            "accept_ranges": (
                response.headers.get(
                    "Accept-Ranges",
                    "",
                )
            ),
        }

        read_ms = round(
            request_elapsed * 1000,
            3,
        )

        # ---------------------------------------------------------------------
        # HLS
        # ---------------------------------------------------------------------

        if status_code == 200:

            hls_info = inspect_hls_body(
                response.content,
                content_type=content_type,
                final_url=final_url,
            )

            if not hls_info.get(
                "valid_hls",
                False,
            ):

                error = (
                    "HTTP 200 but response "
                    "is not recognized as "
                    "valid HLS/M3U8"
                )

        else:

            error = (
                f"HTTP {status_code}"
            )

    except requests.exceptions.Timeout as exc:

        exception_type = "Timeout"

        error = (
            str(exc)
            or "Request timeout"
        )

    except requests.exceptions.ConnectionError as exc:

        exception_type = (
            "ConnectionError"
        )

        error = (
            str(exc)
            or "Connection error"
        )

    except requests.exceptions.RequestException as exc:

        exception_type = (
            type(exc).__name__
        )

        error = (
            str(exc)
            or type(exc).__name__
        )

    except Exception as exc:

        exception_type = (
            type(exc).__name__
        )

        error = (
            str(exc)
            or repr(exc)
        )

    finished_dt = utc_now()

    latency_ms = round(
        (
            time.perf_counter()
            - started_perf
        ) * 1000,
        3,
    )

    error_meta = classify_error(
        status_code=status_code,
        error_text=error,
        exception_type=exception_type,
    )

    if (
        status_code == 200
        and not hls_info.get(
            "valid_hls",
            False,
        )
    ):

        error_meta = {
            "error_class": (
                "INVALID_HLS_BODY"
            ),
            "error_family": (
                "CONTENT_VALIDATION"
            ),
            "retryable": False,
            "severity": 2,
        }

    success = bool(
        status_code == 200
        and hls_info.get(
            "valid_hls",
            False,
        )
    )

    started_ts = timestamp_bundle(
        started_dt
    )

    finished_ts = timestamp_bundle(
        finished_dt
    )

    ml_features = {
        **candidate["features"],

        "status_code": (
            status_code
            if status_code is not None
            else 0
        ),

        "latency_ms": latency_ms,

        "dns_ms": (
            dns_ms
            if dns_ms is not None
            else -1
        ),

        "connect_ms": (
            connect_ms
            if connect_ms is not None
            else -1
        ),

        "read_ms": (
            read_ms
            if read_ms is not None
            else -1
        ),

        "content_length": (
            response_content_length
        ),

        "redirect_count": (
            redirect_count
        ),

        "success": int(
            success
        ),

        "retryable": int(
            error_meta[
                "retryable"
            ]
        ),

        "severity": (
            error_meta["severity"]
        ),

        "valid_hls": int(
            hls_info.get(
                "valid_hls",
                False,
            )
        ),

        "hls_structural_score": int(
            hls_info.get(
                "structural_score",
                0,
            )
        ),

        "media_uri_count": int(
            hls_info.get(
                "media_uri_count",
                0,
            )
        ),

        "m3u8_uri_count": int(
            hls_info.get(
                "m3u8_uri_count",
                0,
            )
        ),

        "content_type_hls": int(
            hls_info.get(
                "content_type_hls",
                False,
            )
        ),

        "discovery_score": 0.0,
    }

    event = {
        "event_type": (
            "url_probe_completed"
        ),

        "event_id": str(
            uuid.uuid4()
        ),

        "run_id": candidate[
            "run_id"
        ],

        "timestamp": {
            "started": started_ts,
            "finished": finished_ts,
        },

        "channel": {
            "number": candidate[
                "channel"
            ]["chno"],

            "name": candidate[
                "channel"
            ]["name"],

            "key": candidate[
                "channel"
            ]["key"],

            "tvg_id": candidate[
                "channel"
            ]["tvg_id"],

            "aliases": candidate[
                "channel"
            ].get(
                "aliases",
                [],
            ),
        },

        "node": {
            "hostname": candidate[
                "node"
            ],

            "url_host": candidate[
                "node"
            ],

            "node_family": "NGENIX",
        },

        "candidate": {
            "path": candidate[
                "path"
            ],

            "url": url,

            "final_url": final_url,

            "url_sha256": sha256_text(
                url
            ),

            "path_sha256": sha256_text(
                candidate[
                    "path"
                ]
            ),

            "features": candidate[
                "features"
            ],
        },

        "request": {
            "method": "GET",

            "timeout_seconds": timeout,

            "allow_redirects": True,

            "user_agent": USER_AGENT,

            "headers": build_headers(),
        },

        "response": {
            "status_code": status_code,

            "latency_ms": latency_ms,

            "dns_ms": dns_ms,

            "connect_ms": connect_ms,

            "read_ms": read_ms,

            "redirect_count": redirect_count,

            "final_url": final_url,

            "content_type": content_type,

            "content_length": (
                response_content_length
            ),

            "headers": response_headers,
        },

        "error": {
            **error_meta,

            "message": error,

            "exception_type": exception_type,
        },

        "hls": hls_info,

        "result": {
            "success": success,

            "label": (
                "positive"
                if success
                else "negative"
            ),

            "playlist_eligible": success,
        },

        "ml": {
            "target": int(success),

            "class": (
                "positive"
                if success
                else "negative"
            ),

            "label": (
                "VALID_HLS"
                if success
                else error_meta[
                    "error_class"
                ]
            ),

            "feature_vector": ml_features,
        },
    }

    discovery_score = (
        calculate_discovery_score(
            event
        )
    )

    event["discovery"] = {
        "score": discovery_score,

        "found": success,

        "channel_key": candidate[
            "channel"
        ]["key"],

        "channel_number": candidate[
            "channel"
        ]["chno"],
    }

    event["ml"][
        "feature_vector"
    ][
        "discovery_score"
    ] = discovery_score

    return event


# =============================================================================
# CANDIDATES
# =============================================================================

def build_candidates(
    run_id: str,
) -> List[Dict[str, Any]]:

    candidates: List[
        Dict[str, Any]
    ] = []

    for channel in CHANNELS:

        paths = generate_paths(
            channel
        )

        for node in NGENIX_NODES:

            for path in paths:

                url = (
                    f"https://{node}{path}"
                )

                candidates.append(
                    {
                        "run_id": run_id,

                        "channel": channel,

                        "node": node,

                        "path": path,

                        "url": url,

                        "features": (
                            candidate_features(
                                node=node,
                                path=path,
                                channel=channel,
                            )
                        ),
                    }
                )

    return candidates


# =============================================================================
# SUCCESSFUL CHANNEL INDEX
# =============================================================================

def build_success_index(
    events: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:

    by_channel: Dict[
        str,
        List[Dict[str, Any]],
    ] = defaultdict(list)

    seen = set()

    for event in events:

        if not event.get(
            "result",
            {},
        ).get(
            "playlist_eligible",
            False,
        ):
            continue

        url = event.get(
            "candidate",
            {},
        ).get(
            "url"
        )

        if not url:
            continue

        if url in seen:
            continue

        seen.add(url)

        channel_key = event.get(
            "channel",
            {},
        ).get(
            "key"
        )

        if not channel_key:
            continue

        by_channel[
            channel_key
        ].append(
            event
        )

    for channel_key in by_channel:

        by_channel[
            channel_key
        ].sort(
            key=lambda event: (
                -float(
                    event.get(
                        "discovery",
                        {},
                    ).get(
                        "score",
                        0,
                    )
                ),

                float(
                    event.get(
                        "response",
                        {},
                    ).get(
                        "latency_ms"
                    )
                    or 999999
                ),

                event.get(
                    "node",
                    {},
                ).get(
                    "hostname",
                    ""
                ),

                event.get(
                    "candidate",
                    {},
                ).get(
                    "url",
                    ""
                ),
            )
        )

    return dict(
        by_channel
    )


# =============================================================================
# SQL ROW
# =============================================================================

def event_to_sql_row(
    event: Dict[str, Any],
) -> Dict[str, Any]:

    channel = event.get(
        "channel",
        {},
    )

    node = event.get(
        "node",
        {},
    )

    candidate = event.get(
        "candidate",
        {},
    )

    response = event.get(
        "response",
        {},
    )

    error = event.get(
        "error",
        {},
    )

    result = event.get(
        "result",
        {},
    )

    ml = event.get(
        "ml",
        {},
    )

    hls = event.get(
        "hls",
        {},
    )

    timestamp = event.get(
        "timestamp",
        {},
    )

    started = timestamp.get(
        "started",
        {},
    )

    discovery = event.get(
        "discovery",
        {},
    )

    return {
        "event_id": event.get(
            "event_id"
        ),

        "run_id": event.get(
            "run_id"
        ),

        "started_utc": started.get(
            "utc"
        ),

        "started_msk": started.get(
            "msk"
        ),

        "channel_number": channel.get(
            "number"
        ),

        "channel_name": channel.get(
            "name"
        ),

        "channel_key": channel.get(
            "key"
        ),

        "tvg_id": channel.get(
            "tvg_id"
        ),

        "node": node.get(
            "hostname"
        ),

        "node_family": node.get(
            "node_family"
        ),

        "url": candidate.get(
            "url"
        ),

        "final_url": candidate.get(
            "final_url"
        ),

        "path": candidate.get(
            "path"
        ),

        "status_code": response.get(
            "status_code"
        ),

        "latency_ms": response.get(
            "latency_ms"
        ),

        "dns_ms": response.get(
            "dns_ms"
        ),

        "connect_ms": response.get(
            "connect_ms"
        ),

        "read_ms": response.get(
            "read_ms"
        ),

        "redirect_count": response.get(
            "redirect_count"
        ),

        "content_type": response.get(
            "content_type"
        ),

        "content_length": response.get(
            "content_length"
        ),

        "error_class": error.get(
            "error_class"
        ),

        "error_family": error.get(
            "error_family"
        ),

        "error_message": error.get(
            "message"
        ),

        "exception_type": error.get(
            "exception_type"
        ),

        "retryable": int(
            bool(
                error.get(
                    "retryable",
                    False,
                )
            )
        ),

        "severity": error.get(
            "severity"
        ),

        "valid_hls": int(
            bool(
                hls.get(
                    "valid_hls",
                    False,
                )
            )
        ),

        "hls_type": hls.get(
            "playlist_type"
        ),

        "hls_structural_score": hls.get(
            "structural_score"
        ),

        "media_uri_count": hls.get(
            "media_uri_count"
        ),

        "m3u8_uri_count": hls.get(
            "m3u8_uri_count"
        ),

        "content_type_hls": int(
            bool(
                hls.get(
                    "content_type_hls",
                    False,
                )
            )
        ),

        "playlist_eligible": int(
            bool(
                result.get(
                    "playlist_eligible",
                    False,
                )
            )
        ),

        "discovery_score": discovery.get(
            "score",
            0,
        ),

        "ml_target": ml.get(
            "target"
        ),

        "ml_class": ml.get(
            "class"
        ),

        "ml_label": ml.get(
            "label"
        ),

        "url_sha256": candidate.get(
            "url_sha256"
        ),

        "path_sha256": candidate.get(
            "path_sha256"
        ),
    }


# =============================================================================
# LATENCY
# =============================================================================

def latency_statistics(
    values: List[float],
) -> Dict[str, Any]:

    if not values:

        return {
            "avg_ms": None,
            "min_ms": None,
            "max_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
        }

    ordered = sorted(
        values
    )

    count = len(
        ordered
    )

    def percentile(
        fraction: float,
    ) -> float:

        index = min(
            max(
                int(
                    count
                    * fraction
                ),
                0,
            ),
            count - 1,
        )

        return ordered[
            index
        ]

    return {
        "avg_ms": round(
            sum(ordered)
            / count,
            3,
        ),

        "min_ms": ordered[0],

        "max_ms": ordered[-1],

        "p50_ms": percentile(
            0.50
        ),

        "p95_ms": percentile(
            0.95
        ),

        "p99_ms": percentile(
            0.99
        ),
    }


# =============================================================================
# AGGREGATIONS
# =============================================================================

def build_aggregations(
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:

    node_stats = defaultdict(
        lambda: {
            "events": 0,
            "positive": 0,
            "negative": 0,
            "latencies_ms": [],
            "statuses": Counter(),
            "errors": Counter(),
        }
    )

    channel_stats = defaultdict(
        lambda: {
            "events": 0,
            "positive": 0,
            "negative": 0,
            "latencies_ms": [],
            "statuses": Counter(),
            "errors": Counter(),
        }
    )

    for event in events:

        node = event.get(
            "node",
            {},
        ).get(
            "hostname",
            "UNKNOWN",
        )

        channel = event.get(
            "channel",
            {},
        ).get(
            "name",
            "UNKNOWN",
        )

        response = event.get(
            "response",
            {},
        )

        status = response.get(
            "status_code"
        )

        latency = response.get(
            "latency_ms"
        )

        error_class = event.get(
            "error",
            {},
        ).get(
            "error_class",
            "UNKNOWN",
        )

        success = bool(
            event.get(
                "result",
                {},
            ).get(
                "success",
                False,
            )
        )

        for bucket in (
            node_stats[node],
            channel_stats[channel],
        ):

            bucket[
                "events"
            ] += 1

            if success:
                bucket[
                    "positive"
                ] += 1
            else:
                bucket[
                    "negative"
                ] += 1

            if latency is not None:
                bucket[
                    "latencies_ms"
                ].append(
                    float(latency)
                )

            bucket[
                "statuses"
            ][
                str(status)
                if status is not None
                else "NONE"
            ] += 1

            bucket[
                "errors"
            ][
                error_class
            ] += 1

    def finalize(
        data: Dict[str, Any],
    ) -> Dict[str, Any]:

        result = {}

        for key, value in data.items():

            events_count = value[
                "events"
            ]

            positive = value[
                "positive"
            ]

            result[key] = {
                "events": events_count,

                "positive": positive,

                "negative": value[
                    "negative"
                ],

                "success_rate_percent": (
                    round(
                        positive
                        / events_count
                        * 100,
                        4,
                    )
                    if events_count
                    else 0.0
                ),

                "latency": latency_statistics(
                    value[
                        "latencies_ms"
                    ]
                ),

                "statuses": dict(
                    value[
                        "statuses"
                    ]
                ),

                "errors": dict(
                    value[
                        "errors"
                    ]
                ),
            }

        return result

    return {
        "by_node": finalize(
            node_stats
        ),

        "by_channel": finalize(
            channel_stats
        ),
    }


# =============================================================================
# JSON DOCUMENT
# =============================================================================

def make_json_document(
    run_id: str,
    started: datetime,
    finished: datetime,
    candidates: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:

    status_counter = Counter()
    error_counter = Counter()
    node_counter = Counter()
    channel_counter = Counter()

    positive = 0
    negative = 0

    valid_hls = 0
    invalid_hls = 0

    latencies: List[
        float
    ] = []

    for event in events:

        response = event.get(
            "response",
            {},
        )

        status = response.get(
            "status_code"
        )

        status_counter[
            str(status)
            if status is not None
            else "NONE"
        ] += 1

        error_counter[
            event.get(
                "error",
                {},
            ).get(
                "error_class",
                "UNKNOWN",
            )
        ] += 1

        node_counter[
            event.get(
                "node",
                {},
            ).get(
                "hostname",
                "UNKNOWN",
            )
        ] += 1

        channel_counter[
            event.get(
                "channel",
                {},
            ).get(
                "name",
                "UNKNOWN",
            )
        ] += 1

        if event.get(
            "result",
            {},
        ).get(
            "success",
            False,
        ):
            positive += 1
        else:
            negative += 1

        if event.get(
            "hls",
            {},
        ).get(
            "valid_hls",
            False,
        ):
            valid_hls += 1
        else:
            invalid_hls += 1

        latency = response.get(
            "latency_ms"
        )

        if latency is not None:
            latencies.append(
                float(latency)
            )

    successful_urls = [
        event[
            "candidate"
        ][
            "url"
        ]

        for event in events

        if event.get(
            "result",
            {},
        ).get(
            "success",
            False,
        )
    ]

    unique_successful_urls = list(
        dict.fromkeys(
            successful_urls
        )
    )

    success_index = (
        build_success_index(
            events
        )
    )

    channels_found = []

    for channel in CHANNELS:

        key = channel["key"]

        found_events = (
            success_index.get(
                key,
                []
            )
        )

        channels_found.append(
            {
                "number": channel[
                    "chno"
                ],

                "name": channel[
                    "name"
                ],

                "key": key,

                "tvg_id": channel[
                    "tvg_id"
                ],

                "found": bool(
                    found_events
                ),

                "url_count": len(
                    found_events
                ),

                "urls": [
                    item[
                        "candidate"
                    ][
                        "url"
                    ]
                    for item in found_events
                ],
            }
        )

    document = {
        "schema": {
            "name": (
                "SKALA_DREG_TELEMETRY"
            ),

            "version": SCHEMA_VERSION,

            "engine": ENGINE_NAME,

            "description": (
                "Full URL probing "
                "telemetry and "
                "validated channel "
                "discovery dataset."
            ),

            "format": "JSON",

            "encoding": "UTF-8",

            "time_standard": (
                "UTC + Europe/Moscow"
            ),
        },

        "engine": {
            "name": ENGINE_NAME,

            "skala_name": SKALA_NAME,

            "version": VERSION,

            "mode": "FULL_SCAN",

            "purpose": (
                "URL/HLS candidate "
                "discovery, "
                "validation and "
                "playlist generation"
            ),
        },

        "run": {
            "run_id": run_id,

            "engine": ENGINE_NAME,

            "version": VERSION,

            "started": timestamp_bundle(
                started
            ),

            "finished": timestamp_bundle(
                finished
            ),

            "duration_seconds": round(
                (
                    finished
                    - started
                ).total_seconds(),
                3,
            ),

            "host": platform.node(),

            "platform": platform.platform(),

            "python": sys.version,

            "pid": os.getpid(),
        },

        "configuration": {
            "timeout_seconds": (
                DEFAULT_TIMEOUT
            ),

            "workers": (
                DEFAULT_WORKERS
            ),

            "max_urls_per_channel": (
                MAX_URLS_PER_CHANNEL
            ),

            "user_agent": USER_AGENT,

            "nodes": NGENIX_NODES,

            "channel_count": len(
                CHANNELS
            ),

            "candidate_count": len(
                candidates
            ),

            "path_generation": {
                "method": (
                    "canonical key + "
                    "aliases + "
                    "HLS path patterns"
                ),

                "deduplicate": True,
            },

            "hls_validation": {
                "enabled": True,

                "max_inspect_bytes": (
                    MAX_INSPECT_BYTES
                ),

                "http_200_requires_valid_hls": (
                    True
                ),

                "content_type_detection": True,

                "bom_support": True,

                "master_playlist_support": True,

                "media_playlist_support": True,
            },
        },

        "channel_canon": CHANNELS,

        "discovery": {
            "channels_total": len(
                CHANNELS
            ),

            "channels_found": sum(
                1
                for item in channels_found
                if item["found"]
            ),

            "channels_not_found": sum(
                1
                for item in channels_found
                if not item["found"]
            ),

            "total_validated_urls": len(
                unique_successful_urls
            ),

            "channels": channels_found,
        },

        "summary": {
            "candidates_generated": len(
                candidates
            ),

            "candidates_checked": len(
                events
            ),

            "positive_events": positive,

            "negative_events": negative,

            "valid_hls_events": valid_hls,

            "invalid_hls_events": invalid_hls,

            "successful_channels": len(
                {
                    event[
                        "channel"
                    ][
                        "key"
                    ]

                    for event in events

                    if event.get(
                        "result",
                        {},
                    ).get(
                        "success",
                        False,
                    )
                }
            ),

            "successful_nodes": len(
                {
                    event[
                        "node"
                    ][
                        "hostname"
                    ]

                    for event in events

                    if event.get(
                        "result",
                        {},
                    ).get(
                        "success",
                        False,
                    )
                }
            ),

            "success_rate_percent": (
                round(
                    positive
                    / len(events)
                    * 100,
                    4,
                )
                if events
                else 0.0
            ),

            "hls_valid_rate_percent": (
                round(
                    valid_hls
                    / len(events)
                    * 100,
                    4,
                )
                if events
                else 0.0
            ),

            "latency": latency_statistics(
                latencies
            ),

            "status_counts": dict(
                status_counter
            ),

            "error_counts": dict(
                error_counter
            ),

            "node_event_counts": dict(
                node_counter
            ),

            "channel_event_counts": dict(
                channel_counter
            ),

            "successful_urls": (
                unique_successful_urls
            ),
        },

        "aggregations": build_aggregations(
            events
        ),

        "ml_dataset": {
            "task": (
                "binary_url_hls_classification"
            ),

            "target": "ml.target",

            "positive_class": 1,

            "negative_class": 0,

            "positive_definition": (
                "HTTP 200 + valid HLS/M3U8"
            ),

            "negative_definition": (
                "HTTP error, network error, "
                "or invalid HLS body"
            ),

            "feature_policy": (
                "Every probe is retained."
            ),

            "features": [
                "channel_number",
                "channel_key_length",
                "path_length",
                "path_depth",
                "filename_length",
                "node_length",
                "node_numeric_id",
                "contains_hls",
                "contains_variant",
                "contains_master",
                "contains_playlist",
                "contains_index",
                "contains_numeric_variant",
                "contains_ch_prefix",
                "contains_hd",
                "contains_m3u8",
                "contains_index_name",
                "contains_variant_name",
                "contains_master_name",
                "url_length",
                "alias_count",
                "status_code",
                "latency_ms",
                "dns_ms",
                "connect_ms",
                "read_ms",
                "content_length",
                "redirect_count",
                "success",
                "retryable",
                "severity",
                "valid_hls",
                "hls_structural_score",
                "media_uri_count",
                "m3u8_uri_count",
                "content_type_hls",
                "discovery_score",
            ],

            "sql_ml_ready": True,
        },

        "sql_projection": {
            "table": (
                "skala_url_probe_events"
            ),

            "primary_key": "event_id",

            "recommended_columns": [
                "event_id",
                "run_id",
                "started_utc",
                "started_msk",
                "channel_number",
                "channel_name",
                "channel_key",
                "tvg_id",
                "node",
                "node_family",
                "url",
                "final_url",
                "path",
                "status_code",
                "latency_ms",
                "dns_ms",
                "connect_ms",
                "read_ms",
                "redirect_count",
                "content_type",
                "content_length",
                "error_class",
                "error_family",
                "error_message",
                "exception_type",
                "retryable",
                "severity",
                "valid_hls",
                "hls_type",
                "hls_structural_score",
                "media_uri_count",
                "m3u8_uri_count",
                "content_type_hls",
                "playlist_eligible",
                "discovery_score",
                "ml_target",
                "ml_class",
                "ml_label",
                "url_sha256",
                "path_sha256",
            ],

            "rows": [
                event_to_sql_row(
                    event
                )
                for event in events
            ],
        },

        "training_statistics": {
            "row_count": len(
                events
            ),

            "positive_rows": positive,

            "negative_rows": negative,

            "class_balance": {
                "positive_percent": (
                    round(
                        positive
                        / len(events)
                        * 100,
                        4,
                    )
                    if events
                    else 0.0
                ),

                "negative_percent": (
                    round(
                        negative
                        / len(events)
                        * 100,
                        4,
                    )
                    if events
                    else 0.0
                ),
            },

            "recommended_ml_target": (
                "ml_target"
            ),

            "recommended_grouping": [
                "channel_key",
                "node",
            ],
        },

        "events": events,
    }

    return document


# =============================================================================
# M3U
# =============================================================================

def build_m3u(
    events: List[Dict[str, Any]],
) -> str:

    lines = [
        "#EXTM3U",
        (
            f"# Generated by "
            f"{SKALA_NAME} v{VERSION}"
        ),
        "# X-SKALA-ENGINE=D-AI-ZABAVA",
        "# X-SKALA-SCHEMA=SKALA_DREG_TELEMETRY",
        "# X-SKALA-TIMEZONE=Europe/Moscow",
        "# X-SKALA-UTC-OFFSET=+03:00",
        "# X-SKALA-VALIDATION=HTTP200+VALID_HLS",
        "# X-SKALA-DISCOVERY=CHANNEL_GROUPED",
        "",
    ]

    success_index = (
        build_success_index(
            events
        )
    )

    # -------------------------------------------------------------------------
    # Каналы идут по каноническому номеру.
    # -------------------------------------------------------------------------

    found_channel_count = 0
    found_url_count = 0

    for channel in CHANNELS:

        channel_key = channel[
            "key"
        ]

        channel_events = (
            success_index.get(
                channel_key,
                [],
            )
        )

        if not channel_events:
            continue

        found_channel_count += 1

        if (
            MAX_URLS_PER_CHANNEL > 0
        ):
            channel_events = (
                channel_events[
                    :MAX_URLS_PER_CHANNEL
                ]
            )

        for variant_index, event in enumerate(
            channel_events,
            start=1,
        ):

            url = event[
                "candidate"
            ][
                "url"
            ]

            final_url = event[
                "candidate"
            ].get(
                "final_url",
                url,
            )

            if not final_url:
                final_url = url

            latency = event[
                "response"
            ].get(
                "latency_ms"
            )

            discovery_score = (
                event.get(
                    "discovery",
                    {},
                ).get(
                    "score",
                    0,
                )
            )

            node = event[
                "node"
            ][
                "hostname"
            ]

            hls_type = event.get(
                "hls",
                {},
            ).get(
                "playlist_type",
                "UNKNOWN",
            )

            if latency is None:
                latency_text = "NA"
            else:
                latency_text = (
                    f"{latency}ms"
                )

            display_name = (
                channel["name"]
                if variant_index == 1
                else (
                    f"{channel['name']} "
                    f"[вариант {variant_index}]"
                )
            )

            lines.append(
                (
                    f'#EXTINF:-1 '
                    f'tvg-id="{channel["tvg_id"]}" '
                    f'tvg-name="{channel["name"]}" '
                    f'tvg-chno="{channel["chno"]}" '
                    f'group-title="ZABAVA",'
                    f'{display_name}'
                )
            )

            lines.append(
                (
                    f"#EXTVLCOPT:http-user-agent="
                    f"{USER_AGENT}"
                )
            )

            lines.append(
                (
                    f"# X-SKALA-CHANNEL="
                    f"{channel['chno']}"
                )
            )

            lines.append(
                (
                    f"# X-SKALA-CHANNEL-KEY="
                    f"{channel['key']}"
                )
            )

            lines.append(
                (
                    f"# X-SKALA-NODE="
                    f"{node}"
                )
            )

            lines.append(
                (
                    f"# X-SKALA-LATENCY="
                    f"{latency_text}"
                )
            )

            lines.append(
                (
                    f"# X-SKALA-DISCOVERY-SCORE="
                    f"{discovery_score}"
                )
            )

            lines.append(
                (
                    f"# X-SKALA-HLS-TYPE="
                    f"{hls_type}"
                )
            )

            lines.append(
                (
                    f"# X-SKALA-VARIANT="
                    f"{variant_index}"
                )
            )

            lines.append(
                url
            )

            lines.append("")

            found_url_count += 1

    # -------------------------------------------------------------------------
    # Если ничего не найдено — явно фиксируем это.
    # Но нормальный найденный результат выше всегда содержит EXTINF.
    # -------------------------------------------------------------------------

    if found_url_count == 0:

        lines.extend(
            [
                "# X-SKALA-DISCOVERY-RESULT=NO_VALIDATED_CHANNELS",
                "# X-SKALA-DISCOVERY-CHANNELS=0",
                "# X-SKALA-DISCOVERY-URLS=0",
                "",
            ]
        )

    else:

        lines.extend(
            [
                (
                    f"# X-SKALA-DISCOVERY-CHANNELS="
                    f"{found_channel_count}"
                ),

                (
                    f"# X-SKALA-DISCOVERY-URLS="
                    f"{found_url_count}"
                ),

                "",
            ]
        )

    return "\n".join(
        lines
    )


# =============================================================================
# TXT REPORT
# =============================================================================

def make_txt_report(
    document: Dict[str, Any],
    m3u: str,
) -> str:

    summary = document[
        "summary"
    ]

    run = document[
        "run"
    ]

    discovery = document[
        "discovery"
    ]

    lines: List[str] = []

    lines.append(
        "=" * 110
    )

    lines.append(
        f"{SKALA_NAME} | "
        f"{ENGINE_NAME} | "
        f"VERSION {VERSION}"
    )

    lines.append(
        "ПОЛНЫЙ СКАЛА ДРЕГ / D-AI ZABAVA REPORT"
    )

    lines.append(
        "=" * 110
    )

    lines.append(
        f"Run ID: {run['run_id']}"
    )

    lines.append(
        f"Начало UTC: "
        f"{run['started']['utc']}"
    )

    lines.append(
        f"Начало Москва: "
        f"{run['started']['msk']}"
    )

    lines.append(
        f"Окончание UTC: "
        f"{run['finished']['utc']}"
    )

    lines.append(
        f"Окончание Москва: "
        f"{run['finished']['msk']}"
    )

    lines.append(
        f"Длительность: "
        f"{run['duration_seconds']} сек."
    )

    lines.append(
        ""
    )

    # -------------------------------------------------------------------------
    # SUMMARY
    # -------------------------------------------------------------------------

    lines.append(
        "=" * 110
    )

    lines.append(
        "ОБЩАЯ СТАТИСТИКА"
    )

    lines.append(
        "=" * 110
    )

    lines.append(
        f"Кандидатов сгенерировано: "
        f"{summary['candidates_generated']}"
    )

    lines.append(
        f"URL проверено: "
        f"{summary['candidates_checked']}"
    )

    lines.append(
        f"Positive: "
        f"{summary['positive_events']}"
    )

    lines.append(
        f"Negative: "
        f"{summary['negative_events']}"
    )

    lines.append(
        f"Valid HLS: "
        f"{summary['valid_hls_events']}"
    )

    lines.append(
        f"Invalid HLS: "
        f"{summary['invalid_hls_events']}"
    )

    lines.append(
        f"Успешных каналов: "
        f"{summary['successful_channels']}"
    )

    lines.append(
        f"Успешных nodes: "
        f"{summary['successful_nodes']}"
    )

    lines.append(
        f"Успешных URL: "
        f"{len(summary['successful_urls'])}"
    )

    lines.append(
        f"Success rate: "
        f"{summary['success_rate_percent']}%"
    )

    lines.append(
        f"HLS valid rate: "
        f"{summary['hls_valid_rate_percent']}%"
    )

    lines.append(
        ""
    )

    # -------------------------------------------------------------------------
    # CHANNEL DISCOVERY
    # -------------------------------------------------------------------------

    lines.append(
        "=" * 110
    )

    lines.append(
        "НАЙДЕННЫЕ КАНАЛЫ"
    )

    lines.append(
        "=" * 110
    )

    lines.append(
        f"Всего каналов в CANON: "
        f"{discovery['channels_total']}"
    )

    lines.append(
        f"Найдено каналов: "
        f"{discovery['channels_found']}"
    )

    lines.append(
        f"Не найдено: "
        f"{discovery['channels_not_found']}"
    )

    lines.append(
        f"Всего валидированных URL: "
        f"{discovery['total_validated_urls']}"
    )

    lines.append(
        ""
    )

    for item in discovery[
        "channels"
    ]:

        if item["found"]:

            lines.append(
                (
                    f"[FOUND] "
                    f"{item['number']:02d} | "
                    f"{item['name']} | "
                    f"URLs={item['url_count']}"
                )
            )

            for index, url in enumerate(
                item["urls"],
                start=1,
            ):

                lines.append(
                    f"    {index}. {url}"
                )

        else:

            lines.append(
                (
                    f"[NOT FOUND] "
                    f"{item['number']:02d} | "
                    f"{item['name']}"
                )
            )

        lines.append("")

    # -------------------------------------------------------------------------
    # HTTP
    # -------------------------------------------------------------------------

    lines.append(
        "=" * 110
    )

    lines.append(
        "HTTP STATUS COUNTS"
    )

    lines.append(
        "=" * 110
    )

    for status, count in sorted(
        summary[
            "status_counts"
        ].items()
    ):

        lines.append(
            f"HTTP {status}: {count}"
        )

    # -------------------------------------------------------------------------
    # ERRORS
    # -------------------------------------------------------------------------

    lines.append(
        ""
    )

    lines.append(
        "=" * 110
    )

    lines.append(
        "ERROR CLASSIFICATION"
    )

    lines.append(
        "=" * 110
    )

    for (
        error_name,
        count,
    ) in sorted(
        summary[
            "error_counts"
        ].items(),
        key=lambda item: (
            -item[1],
            item[0],
        ),
    ):

        lines.append(
            f"{error_name}: {count}"
        )

    # -------------------------------------------------------------------------
    # NODE ANALYSIS
    # -------------------------------------------------------------------------

    lines.append(
        ""
    )

    lines.append(
        "=" * 110
    )

    lines.append(
        "NGENIX NODE ANALYSIS"
    )

    lines.append(
        "=" * 110
    )

    for (
        node,
        stats,
    ) in sorted(
        document[
            "aggregations"
        ][
            "by_node"
        ].items()
    ):

        lines.append(
            f"NODE: {node}"
        )

        lines.append(
            f"    Events: "
            f"{stats['events']}"
        )

        lines.append(
            f"    Positive: "
            f"{stats['positive']}"
        )

        lines.append(
            f"    Negative: "
            f"{stats['negative']}"
        )

        lines.append(
            f"    Success rate: "
            f"{stats['success_rate_percent']}%"
        )

        lines.append(
            f"    Avg latency: "
            f"{stats['latency']['avg_ms']} ms"
        )

        lines.append(
            f"    P95 latency: "
            f"{stats['latency']['p95_ms']} ms"
        )

        lines.append("")

    # -------------------------------------------------------------------------
    # CHANNEL ANALYSIS
    # -------------------------------------------------------------------------

    lines.append(
        "=" * 110
    )

    lines.append(
        "CHANNEL ANALYSIS"
    )

    lines.append(
        "=" * 110
    )

    for (
        channel,
        stats,
    ) in sorted(
        document[
            "aggregations"
        ][
            "by_channel"
        ].items()
    ):

        lines.append(
            f"CHANNEL: {channel}"
        )

        lines.append(
            f"    Events: "
            f"{stats['events']}"
        )

        lines.append(
            f"    Positive: "
            f"{stats['positive']}"
        )

        lines.append(
            f"    Negative: "
            f"{stats['negative']}"
        )

        lines.append(
            f"    Success rate: "
            f"{stats['success_rate_percent']}%"
        )

        lines.append(
            f"    Avg latency: "
            f"{stats['latency']['avg_ms']} ms"
        )

        lines.append("")

    # -------------------------------------------------------------------------
    # FULL TELEMETRY
    # -------------------------------------------------------------------------

    lines.append(
        "=" * 110
    )

    lines.append(
        "ПОЛНАЯ СКАЛА ДРЕГ ТЕЛЕМЕТРИЯ"
    )

    lines.append(
        "=" * 110
    )

    for event in document[
        "events"
    ]:

        channel = event[
            "channel"
        ]

        node = event[
            "node"
        ]

        candidate = event[
            "candidate"
        ]

        response = event[
            "response"
        ]

        error = event[
            "error"
        ]

        result = event[
            "result"
        ]

        timestamp = event[
            "timestamp"
        ]

        hls = event[
            "hls"
        ]

        discovery_event = event.get(
            "discovery",
            {}
        )

        state = (
            "OK"
            if result.get(
                "success",
                False,
            )
            else "FAIL"
        )

        lines.append(
            f"[СКАЛА] [{state}] "
            f"EventID={event['event_id']}"
        )

        lines.append(
            f"    Channel: "
            f"{channel['number']} | "
            f"{channel['name']}"
        )

        lines.append(
            f"    Key: "
            f"{channel['key']}"
        )

        lines.append(
            f"    tvg-id: "
            f"{channel['tvg_id']}"
        )

        lines.append(
            f"    Node: "
            f"{node['hostname']}"
        )

        lines.append(
            f"    URL: "
            f"{candidate['url']}"
        )

        lines.append(
            f"    Final URL: "
            f"{candidate.get('final_url', '')}"
        )

        lines.append(
            f"    Path: "
            f"{candidate['path']}"
        )

        lines.append(
            f"    Time UTC: "
            f"{timestamp['started']['utc']}"
        )

        lines.append(
            f"    Time MSK: "
            f"{timestamp['started']['msk']}"
        )

        lines.append(
            f"    HTTP: "
            f"{response.get('status_code')}"
        )

        lines.append(
            f"    Latency: "
            f"{response.get('latency_ms')} ms"
        )

        lines.append(
            f"    DNS: "
            f"{response.get('dns_ms')} ms"
        )

        lines.append(
            f"    Content-Type: "
            f"{response.get('content_type', '')}"
        )

        lines.append(
            f"    Content-Length: "
            f"{response.get('content_length', 0)}"
        )

        lines.append(
            f"    Redirects: "
            f"{response.get('redirect_count', 0)}"
        )

        lines.append(
            f"    ErrorClass: "
            f"{error.get('error_class')}"
        )

        lines.append(
            f"    ErrorFamily: "
            f"{error.get('error_family')}"
        )

        lines.append(
            f"    Error: "
            f"{error.get('message')}"
        )

        lines.append(
            f"    Retryable: "
            f"{error.get('retryable')}"
        )

        lines.append(
            f"    Severity: "
            f"{error.get('severity')}"
        )

        lines.append(
            f"    HLS valid: "
            f"{hls.get('valid_hls', False)}"
        )

        lines.append(
            f"    HLS type: "
            f"{hls.get('playlist_type', 'UNKNOWN')}"
        )

        lines.append(
            f"    HLS structural score: "
            f"{hls.get('structural_score', 0)}"
        )

        lines.append(
            f"    HLS media URI count: "
            f"{hls.get('media_uri_count', 0)}"
        )

        lines.append(
            f"    HLS m3u8 URI count: "
            f"{hls.get('m3u8_uri_count', 0)}"
        )

        lines.append(
            f"    HLS Content-Type: "
            f"{hls.get('content_type_hls', False)}"
        )

        lines.append(
            f"    Discovery score: "
            f"{discovery_event.get('score', 0)}"
        )

        lines.append(
            f"    ML class: "
            f"{event['ml'].get('class')}"
        )

        lines.append(
            f"    ML target: "
            f"{event['ml'].get('target')}"
        )

        lines.append(
            f"    ML label: "
            f"{event['ml'].get('label')}"
        )

        lines.append("")

    # -------------------------------------------------------------------------
    # M3U EXCERPT
    # -------------------------------------------------------------------------

    lines.append(
        "=" * 110
    )

    lines.append(
        "M3U PLAYLIST"
    )

    lines.append(
        "=" * 110
    )

    lines.extend(
        m3u.splitlines()
    )

    # -------------------------------------------------------------------------
    # END
    # -------------------------------------------------------------------------

    lines.append(
        ""
    )

    lines.append(
        "=" * 110
    )

    lines.append(
        "END OF SKALA DREG REPORT"
    )

    lines.append(
        "=" * 110
    )

    return "\n".join(
        lines
    )


# =============================================================================
# SCANNER
# =============================================================================

def run_scan(
    timeout: float,
    workers: int,
) -> Tuple[
    Dict[str, Any],
    str,
    str,
]:

    run_id = (
        f"SKALA-"
        f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-"
        f"{uuid.uuid4().hex[:8]}"
    )

    started = utc_now()

    print("=" * 100)

    print(
        f"{SKALA_NAME} | "
        f"{ENGINE_NAME} | "
        f"VERSION {VERSION}"
    )

    print("=" * 100)

    print(
        f"Run ID: {run_id}"
    )

    print(
        f"Start UTC: "
        f"{iso_utc(started)}"
    )

    print(
        f"Start MSK: "
        f"{iso_msk(started)}"
    )

    print(
        f"Nodes: "
        f"{len(NGENIX_NODES)}"
    )

    print(
        f"Channels: "
        f"{len(CHANNELS)}"
    )

    candidates = build_candidates(
        run_id
    )

    print(
        f"URL candidates: "
        f"{len(candidates)}"
    )

    print(
        f"Workers: "
        f"{workers}"
    )

    print(
        f"Timeout: "
        f"{timeout}s"
    )

    print("=" * 100)

    events: List[
        Dict[str, Any]
    ] = []

    total = len(
        candidates
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers
    ) as executor:

        futures = [
            executor.submit(
                probe_candidate,
                candidate,
                timeout,
            )
            for candidate in candidates
        ]

        for (
            index,
            future,
        ) in enumerate(
            concurrent.futures.as_completed(
                futures
            ),
            start=1,
        ):

            try:

                event = future.result()

            except Exception as exc:

                now = utc_now()

                event = {
                    "event_type": (
                        "scanner_internal_error"
                    ),

                    "event_id": str(
                        uuid.uuid4()
                    ),

                    "run_id": run_id,

                    "timestamp": {
                        "started": (
                            timestamp_bundle(
                                now
                            )
                        ),

                        "finished": (
                            timestamp_bundle(
                                now
                            )
                        ),
                    },

                    "channel": {},

                    "node": {},

                    "candidate": {},

                    "response": {},

                    "hls": {},

                    "result": {
                        "success": False,

                        "label": "negative",

                        "playlist_eligible": False,
                    },

                    "error": {
                        "error_class": (
                            "SCANNER_INTERNAL_ERROR"
                        ),

                        "error_family": "ENGINE",

                        "retryable": True,

                        "severity": 5,

                        "message": str(exc),

                        "exception_type": (
                            type(exc).__name__
                        ),
                    },

                    "traceback": (
                        traceback.format_exc()
                    ),

                    "ml": {
                        "target": 0,

                        "class": "negative",

                        "label": (
                            "SCANNER_INTERNAL_ERROR"
                        ),

                        "feature_vector": {},
                    },

                    "discovery": {
                        "score": 0,
                        "found": False,
                    },
                }

            events.append(
                event
            )

            if (
                index % 25 == 0
                or index == total
            ):

                success_count = sum(
                    1
                    for item in events
                    if item.get(
                        "result",
                        {},
                    ).get(
                        "success",
                        False,
                    )
                )

                print(
                    f"[СКАЛА] "
                    f"{index}/{total} | "
                    f"FOUND={success_count} | "
                    f"FAIL="
                    f"{index - success_count}"
                )

    finished = utc_now()

    # Стабильный порядок telemetry.
    events.sort(
        key=lambda event: (
            event.get(
                "channel",
                {},
            ).get(
                "number",
                999999,
            ),

            event.get(
                "node",
                {},
            ).get(
                "hostname",
                "",
            ),

            event.get(
                "candidate",
                {},
            ).get(
                "url",
                "",
            ),
        )
    )

    document = make_json_document(
        run_id=run_id,

        started=started,

        finished=finished,

        candidates=candidates,

        events=events,
    )

    m3u = build_m3u(
        events
    )

    txt = make_txt_report(
        document=document,

        m3u=m3u,
    )

    return (
        document,
        txt,
        m3u,
    )


# =============================================================================
# FILE WRITER
# =============================================================================

def write_outputs(
    document: Dict[str, Any],
    txt: str,
    m3u: str,
) -> None:

    Path(
        OUTPUT_JSON
    ).write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    Path(
        OUTPUT_TXT
    ).write_text(
        txt,
        encoding="utf-8",
    )

    Path(
        OUTPUT_M3U
    ).write_text(
        m3u,
        encoding="utf-8",
    )


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "СКАЛА ДРЕГ / D-AI ZABAVA "
            "modernized discovery + "
            "telemetry + HLS + M3U"
        )
    )

    parser.add_argument(
        "--scan",
        action="store_true",
        help="Запустить полный скан",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=(
            "HTTP timeout в секундах "
            f"(default: {DEFAULT_TIMEOUT})"
        ),
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            "Количество параллельных "
            "worker threads "
            f"(default: {DEFAULT_WORKERS})"
        ),
    )

    return parser.parse_args()


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> int:

    args = parse_args()

    if not args.scan:

        print("=" * 100)

        print(
            f"{SKALA_NAME} | "
            f"{ENGINE_NAME} | "
            f"VERSION {VERSION}"
        )

        print("=" * 100)

        print()

        print(
            "Для запуска полного сканирования:"
        )

        print()

        print(
            "    python d_AI_zabava.py --scan"
        )

        print()

        print(
            "Дополнительно:"
        )

        print(
            "    --timeout 4"
        )

        print(
            "    --workers 16"
        )

        print()

        print(
            "Будут созданы:"
        )

        print(
            f"    {OUTPUT_JSON}"
        )

        print(
            f"    {OUTPUT_TXT}"
        )

        print(
            f"    {OUTPUT_M3U}"
        )

        print()

        return 0

    if args.timeout <= 0:

        print(
            "ERROR: timeout должен быть > 0"
        )

        return 2

    if args.workers <= 0:

        print(
            "ERROR: workers должен быть > 0"
        )

        return 2

    try:

        document, txt, m3u = run_scan(
            timeout=args.timeout,
            workers=args.workers,
        )

        write_outputs(
            document=document,
            txt=txt,
            m3u=m3u,
        )

        summary = document[
            "summary"
        ]

        discovery = document[
            "discovery"
        ]

        print()

        print("=" * 100)

        print(
            "СКАЛА ДРЕГ — FINISHED"
        )

        print("=" * 100)

        print(
            f"Проверено URL: "
            f"{summary['candidates_checked']}"
        )

        print(
            f"Valid HLS: "
            f"{summary['valid_hls_events']}"
        )

        print(
            f"Найдено каналов: "
            f"{discovery['channels_found']}/"
            f"{discovery['channels_total']}"
        )

        print(
            f"Найдено рабочих URL: "
            f"{discovery['total_validated_urls']}"
        )

        print(
            f"Успешных nodes: "
            f"{summary['successful_nodes']}"
        )

        print(
            f"Success rate: "
            f"{summary['success_rate_percent']}%"
        )

        print()

        print(
            f"[FILE] {OUTPUT_JSON}"
        )

        print(
            f"[FILE] {OUTPUT_TXT}"
        )

        print(
            f"[FILE] {OUTPUT_M3U}"
        )

        print()

        # -------------------------------------------------------------
        # ВАЖНО: непосредственно показываем результат M3U в консоли.
        # -------------------------------------------------------------

        print(
            "НАЙДЕННЫЕ КАНАЛЫ:"
        )

        for item in discovery[
            "channels"
        ]:

            if item["found"]:

                print(
                    f"  [{item['number']:02d}] "
                    f"{item['name']} "
                    f"-> "
                    f"{item['url_count']} URL"
                )

        print()

        print("=" * 100)

        return 0

    except KeyboardInterrupt:

        print()

        print(
            "[СКАЛА] Остановлено пользователем."
        )

        return 130

    except Exception as exc:

        print(
            f"[СКАЛА] FATAL ERROR: {exc}"
        )

        traceback.print_exc()

        return 1


if __name__ == "__main__":

    raise SystemExit(
        main()
    )