#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CINERAMA SCALA GRAPH PROCESSOR / VERIFICATOR

Назначение:
    1. Получает исходный M3U-плейлист из gh-pages.
    2. Исходный файл локально называется:
           Extra_channels2026.m3u
    3. Находит URL потоков.
    4. Заменяет:
           https://stream8.cinerama.uz
       на:
           https://stream1.cinerama.uz
    5. Реально проверяет сетевой HLS-поток.
    6. Проверяет HTTP-ответ.
    7. Проверяет #EXTM3U.
    8. Проверяет HLS playlist.
    9. Проверяет HLS-сегменты.
   10. Только реально прошедшие потоки получают статус VERIFIED.
   11. Формирует:
           Extra_channels2026_verified.m3u
   12. Подробный лог:
           playlist_log.txt

Архитектура GitHub:

    gh-pages
        └── Extra_channels2026.m3u
                    │
                    ▼
        GitHub Actions скачивает файл
                    │
                    ▼
        Extra_channels2026.m3u
                    │
                    ▼
        CINERAMA_SCALA_GRAPH_PROCESSOR_verificator.py
                    │
                    ├── stream8 -> stream1
                    ├── HTTP
                    ├── HLS
                    └── сегменты
                    │
                    ▼
        Extra_channels2026_verified.m3u
                    │
                    ▼
        gh-pages-2

    playlist_log.txt
            │
            ▼
          main
"""


# ============================================================================
# IMPORTS
# ============================================================================

import sys
import os
import ssl
import time
import socket
import logging
import urllib.request
import urllib.error
import urllib.parse

from datetime import datetime

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

from collections import Counter


# ============================================================================
# НАСТРОЙКИ
# ============================================================================

# ============================================================================
# ИСХОДНЫЙ M3U
# ============================================================================

# ВАЖНО:
#
# Этот файл берётся GitHub Actions из ветки:
#
#     gh-pages
#
# и скачивается в рабочую директорию runner.
#
# Фактический источник:
#
#     gh-pages/Extra_channels2026.m3u
#
INPUT_PLAYLIST = "Extra_channels2026.m3u"


# ============================================================================
# ВЫХОДНОЙ VERIFIED M3U
# ============================================================================

# Этот файл после обработки workflow отправляет в:
#
#     gh-pages-2
#
OUTPUT_FILE = "Extra_channels2026_verified.m3u"


# ============================================================================
# ЛОГ
# ============================================================================

# Лог остаётся в main:
#
#     main/playlist_log.txt
#
LOG_FILE = "playlist_log.txt"


# ============================================================================
# ПАРАЛЛЕЛЬНОСТЬ
# ============================================================================

MAX_WORKERS = 12


# ============================================================================
# HTTP TIMEOUT
# ============================================================================

HTTP_TIMEOUT = 8


# ============================================================================
# КОЛИЧЕСТВО HLS-СЕГМЕНТОВ
# ============================================================================

SEGMENT_CHECK_COUNT = 3


# ============================================================================
# МИНИМАЛЬНЫЙ РАЗМЕР PLAYLIST
# ============================================================================

MIN_PLAYLIST_SIZE = 20


# ============================================================================
# USER AGENT
# ============================================================================

USER_AGENT = (
    "Mozilla/5.0 "
    "(Linux; Android 12) "
    "AppleWebKit/537.36 "
    "Chrome/120.0 Safari/537.36 "
    "CineramaScalaVerifier/1.0"
)


# ============================================================================
# МАКСИМАЛЬНЫЙ РАЗМЕР HLS PLAYLIST
# ============================================================================

MAX_PLAYLIST_BYTES = 2 * 1024 * 1024


# ============================================================================
# SSL
# ============================================================================

SSL_CONTEXT = ssl.create_default_context()


# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_FILE,
            mode="w",
            encoding="utf-8"
        )
    ]
)

logger = logging.getLogger(
    "CineramaScalaGraph"
)


# ============================================================================
# ПРАВИЛА ЗАМЕНЫ HOST
# ============================================================================

CINERAMA_HOST_REPLACEMENTS = {
    "https://stream8.cinerama.uz":
        "https://stream1.cinerama.uz",
}


# ============================================================================
# СТАТИСТИКА
# ============================================================================

stats = {

    # Всего URL потоков.
    "total_streams": 0,

    # Реально отправлено на проверку.
    "checked": 0,

    # Получен HTTP-ответ.
    "responded": 0,

    # Общие ошибки.
    "errors": 0,

    # Заменено stream8 -> stream1.
    "stream8_replaced": 0,

    # Уже stream1.
    "stream1_unchanged": 0,

    # Ошибки проверки.
    "verification_errors": 0,

    # VERIFIED.
    "verified": 0,

    # FAILED.
    "failed": 0,

    # HTTP errors.
    "http_errors": 0,

    # Timeout.
    "timeouts": 0,

    # Connection / DNS.
    "connection_errors": 0,

    # HLS errors.
    "hls_errors": 0,

    # Unknown.
    "unknown_errors": 0,

    # Проверено сегментов.
    "segments_checked": 0,

    # Ошибки сегментов.
    "segment_errors": 0,

    # Сработало правил замены.
    "matched_rules": 0,
}


replacement_counter = Counter()


# ============================================================================
# СБРОС СТАТИСТИКИ
# ============================================================================

def reset_statistics():

    for key in stats:
        stats[key] = 0

    replacement_counter.clear()


# ============================================================================
# ОПРЕДЕЛЕНИЕ INPUT-ФАЙЛА
# ============================================================================

def get_input_file():

    """
    Приоритет:

    1. Аргумент командной строки.
    2. Переменная INPUT_PLAYLIST.
    3. INPUT_PLAYLIST из настроек.

    В GitHub Actions используется:

        python3 \
          CINERAMA_SCALA_GRAPH_PROCESSOR_verificator.py \
          Extra_channels2026.m3u
    """

    # ------------------------------------------------------------------------
    # ARGUMENT
    # ------------------------------------------------------------------------

    if len(sys.argv) > 1:

        input_file = sys.argv[1]

        logger.info(
            "INPUT SOURCE / ИСТОЧНИК M3U:"
        )

        logger.info(
            f"  Передан аргументом: {input_file}"
        )

        return input_file


    # ------------------------------------------------------------------------
    # ENVIRONMENT
    # ------------------------------------------------------------------------

    env_input = os.getenv(
        "INPUT_PLAYLIST"
    )

    if env_input:

        logger.info(
            "INPUT SOURCE / ИСТОЧНИК M3U:"
        )

        logger.info(
            f"  Из переменной окружения: "
            f"{env_input}"
        )

        return env_input


    # ------------------------------------------------------------------------
    # DEFAULT
    # ------------------------------------------------------------------------

    logger.info(
        "INPUT SOURCE / ИСТОЧНИК M3U:"
    )

    logger.info(
        f"  По умолчанию: {INPUT_PLAYLIST}"
    )

    return INPUT_PLAYLIST


# ============================================================================
# ЧТЕНИЕ M3U
# ============================================================================

def load_playlist(path):

    logger.info("")
    logger.info("=" * 100)

    logger.info(
        "ЗАГРУЗКА ПЛЕЙЛИСТА / PLAYLIST LOADING"
    )

    logger.info("=" * 100)

    logger.info(
        "Источник / Source:"
    )

    logger.info(
        f"  {path}"
    )

    if not os.path.exists(path):

        raise FileNotFoundError(
            "Исходный M3U не найден / "
            "Source M3U not found: "
            f"{path}"
        )

    with open(
        path,
        "r",
        encoding="utf-8",
        errors="replace"
    ) as f:

        content = f.read()

    size = len(
        content.encode("utf-8")
    )

    logger.info(
        f"Размер / Size: {size} bytes"
    )

    if size < MIN_PLAYLIST_SIZE:

        logger.warning(
            "Размер M3U очень маленький / "
            "M3U size is very small"
        )

    logger.info(
        "Плейлист успешно загружен / "
        "Playlist loaded successfully"
    )

    return content


# ============================================================================
# ЗАМЕНА HOST
# ============================================================================

def process_node_scala_style(url):

    original_url = url.strip()

    target_url = original_url

    replacement_applied = False

    replacement_rule = None


    logger.info("")

    logger.info(
        "[GRAPH NODE / УЗЕЛ ГРАФА]"
    )

    logger.info(
        f"ORIGINAL / ИСХОДНЫЙ:\n"
        f"  {original_url}"
    )


    # ------------------------------------------------------------------------
    # ПЕРЕБОР ПРАВИЛ
    # ------------------------------------------------------------------------

    for old_host, new_host in (
        CINERAMA_HOST_REPLACEMENTS.items()
    ):

        if original_url.startswith(
            old_host
        ):

            replacement_rule = (
                f"{old_host} -> {new_host}"
            )

            target_url = (
                original_url.replace(
                    old_host,
                    new_host,
                    1
                )
            )

            replacement_applied = True

            stats[
                "stream8_replaced"
            ] += 1

            stats[
                "matched_rules"
            ] += 1

            replacement_counter[
                replacement_rule
            ] += 1


            logger.info(
                "[GRAPH TRANSFORMATION / "
                "ТРАНСФОРМАЦИЯ ГРАФА]"
            )

            logger.info(
                f"  Было / Original:\n"
                f"    {original_url}"
            )

            logger.info(
                f"  Правило / Rule:\n"
                f"    {replacement_rule}"
            )

            logger.info(
                f"  Стало / New:\n"
                f"    {target_url}"
            )

            logger.info(
                "  Замена выполнена / "
                "Replacement applied: YES"
            )

            break


    # ------------------------------------------------------------------------
    # УЖЕ STREAM1
    # ------------------------------------------------------------------------

    if original_url.startswith(
        "https://stream1.cinerama.uz"
    ):

        stats[
            "stream1_unchanged"
        ] += 1

        logger.info(
            "[STREAM1 UNCHANGED / "
            "STREAM1 БЕЗ ЗАМЕНЫ]"
        )

        logger.info(
            "  URL уже использует stream1"
        )

        logger.info(
            "  URL already uses stream1"
        )


    # ------------------------------------------------------------------------
    # ДРУГОЙ HOST
    # ------------------------------------------------------------------------

    elif not replacement_applied:

        logger.info(
            "[NO REPLACEMENT / БЕЗ ЗАМЕНЫ]"
        )

        logger.info(
            "  Правило замены не применялось"
        )

        logger.info(
            "  No replacement rule applied"
        )


    return (
        target_url,
        replacement_applied,
        replacement_rule
    )


# ============================================================================
# HTTP GET
# ============================================================================

def http_get(
    url,
    timeout=HTTP_TIMEOUT,
    max_bytes=MAX_PLAYLIST_BYTES
):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,

            "Accept": (
                "application/vnd.apple.mpegurl,"
                "application/x-mpegURL,"
                "application/octet-stream,"
                "*/*"
            ),

            "Accept-Encoding": "identity",

            "Connection": "close",
        }
    )


    started = time.perf_counter()


    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=SSL_CONTEXT
        ) as response:

            elapsed = (
                time.perf_counter()
                - started
            )

            status_code = (
                response.getcode()
            )

            content_type = (
                response.headers.get(
                    "Content-Type",
                    ""
                )
            )

            final_url = (
                response.geturl()
            )

            content_length_header = (
                response.headers.get(
                    "Content-Length"
                )
            )


            try:

                declared_length = (
                    int(
                        content_length_header
                    )
                    if content_length_header
                    else None
                )

            except ValueError:

                declared_length = None


            data = response.read(
                max_bytes + 1
            )


            truncated = (
                len(data) > max_bytes
            )


            if truncated:

                data = data[
                    :max_bytes
                ]


            return {

                "success": True,

                "status": status_code,

                "content_type": content_type,

                "final_url": final_url,

                "elapsed": elapsed,

                "data": data,

                "declared_length":
                    declared_length,

                "truncated":
                    truncated,

                "error": None,
            }


    except urllib.error.HTTPError as exc:

        elapsed = (
            time.perf_counter()
            - started
        )

        return {

            "success": False,

            "status": exc.code,

            "content_type": "",

            "final_url": url,

            "elapsed": elapsed,

            "data": b"",

            "declared_length": None,

            "truncated": False,

            "error": (
                f"HTTP {exc.code} "
                f"{exc.reason}"
            ),
        }


    except urllib.error.URLError as exc:

        elapsed = (
            time.perf_counter()
            - started
        )

        return {

            "success": False,

            "status": None,

            "content_type": "",

            "final_url": url,

            "elapsed": elapsed,

            "data": b"",

            "declared_length": None,

            "truncated": False,

            "error": str(
                exc.reason
            ),
        }


    except socket.timeout:

        elapsed = (
            time.perf_counter()
            - started
        )

        return {

            "success": False,

            "status": None,

            "content_type": "",

            "final_url": url,

            "elapsed": elapsed,

            "data": b"",

            "declared_length": None,

            "truncated": False,

            "error": "TIMEOUT",
        }


    except TimeoutError:

        elapsed = (
            time.perf_counter()
            - started
        )

        return {

            "success": False,

            "status": None,

            "content_type": "",

            "final_url": url,

            "elapsed": elapsed,

            "data": b"",

            "declared_length": None,

            "truncated": False,

            "error": "TIMEOUT",
        }


    except Exception as exc:

        elapsed = (
            time.perf_counter()
            - started
        )

        return {

            "success": False,

            "status": None,

            "content_type": "",

            "final_url": url,

            "elapsed": elapsed,

            "data": b"",

            "declared_length": None,

            "truncated": False,

            "error": str(exc),
        }


# ============================================================================
# URL В ABSOLUTE
# ============================================================================

def make_absolute_url(
    base_url,
    value
):

    value = value.strip()

    if not value:

        return None

    return urllib.parse.urljoin(
        base_url,
        value
    )


# ============================================================================
# HLS MEDIA PLAYLIST
# ============================================================================

def extract_media_segments(
    playlist_text,
    playlist_url
):

    lines = (
        playlist_text.splitlines()
    )

    segments = []


    for line in lines:

        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue


        absolute = (
            urllib.parse.urljoin(
                playlist_url,
                line
            )
        )


        segments.append(
            absolute
        )


    return segments


# ============================================================================
# HLS MASTER PLAYLIST
# ============================================================================

def extract_variant_playlists(
    playlist_text,
    playlist_url
):

    lines = (
        playlist_text.splitlines()
    )

    variants = []


    for index, line in enumerate(
        lines
    ):

        line = line.strip()

        if not line:
            continue


        if line.startswith(
            "#EXT-X-STREAM-INF:"
        ):

            for next_index in range(
                index + 1,
                len(lines)
            ):

                candidate = (
                    lines[
                        next_index
                    ].strip()
                )

                if not candidate:
                    continue

                if candidate.startswith(
                    "#"
                ):
                    continue


                absolute = (
                    urllib.parse.urljoin(
                        playlist_url,
                        candidate
                    )
                )


                variants.append(
                    absolute
                )

                break


    return variants


# ============================================================================
# HLS PLAYLIST VERIFICATION
# ============================================================================

def verify_hls_playlist(
    playlist_url,
    response_data
):

    result = {

        "hls_ok": False,

        "playlist_type":
            "UNKNOWN",

        "segments": [],

        "variants": [],

        "error": None,
    }


    if not response_data:

        result["error"] = (
            "Empty response / "
            "Пустой ответ"
        )

        return result


    try:

        text = (
            response_data.decode(
                "utf-8",
                errors="replace"
            )
        )

    except Exception as exc:

        result["error"] = (
            "Decode error / "
            "Ошибка декодирования: "
            f"{exc}"
        )

        return result


    text = text.lstrip(
        "\ufeff"
    )


    # =========================================================================
    # HLS HEADER
    # =========================================================================

    if not text.startswith(
        "#EXTM3U"
    ):

        result["error"] = (
            "Missing #EXTM3U / "
            "Отсутствует #EXTM3U"
        )

        return result


    # =========================================================================
    # MASTER PLAYLIST
    # =========================================================================

    variants = (
        extract_variant_playlists(
            text,
            playlist_url
        )
    )


    if variants:

        result[
            "playlist_type"
        ] = "MASTER"

        result[
            "variants"
        ] = variants


        logger.info(
            "[HLS MASTER / MASTER PLAYLIST]"
        )

        logger.info(
            f"  Variant playlists: "
            f"{len(variants)}"
        )


        variant_url = (
            variants[0]
        )


        logger.info(
            f"  Проверяем variant:\n"
            f"    {variant_url}"
        )


        variant_response = (
            http_get(
                variant_url
            )
        )


        if not variant_response[
            "success"
        ]:

            result["error"] = (
                "Variant playlist request "
                "failed: "
                f"{variant_response['error']}"
            )

            return result


        if variant_response[
            "status"
        ] != 200:

            result["error"] = (
                "Variant HTTP status: "
                f"{variant_response['status']}"
            )

            return result


        variant_text = (
            variant_response[
                "data"
            ]
            .decode(
                "utf-8",
                errors="replace"
            )
            .lstrip(
                "\ufeff"
            )
        )


        if not variant_text.startswith(
            "#EXTM3U"
        ):

            result["error"] = (
                "Variant is not valid HLS"
            )

            return result


        variant_segments = (
            extract_media_segments(
                variant_text,
                variant_url
            )
        )


        if not variant_segments:

            result["error"] = (
                "No media segments "
                "in variant"
            )

            return result


        result[
            "segments"
        ] = variant_segments


        return result


    # =========================================================================
    # MEDIA PLAYLIST
    # =========================================================================

    result[
        "playlist_type"
    ] = "MEDIA"


    segments = (
        extract_media_segments(
            text,
            playlist_url
        )
    )


    if not segments:

        result["error"] = (
            "No media segments found / "
            "HLS-сегменты не найдены"
        )

        return result


    result[
        "segments"
    ] = segments


    return result


# ============================================================================
# ПРОВЕРКА HLS-СЕГМЕНТОВ
# ============================================================================

def verify_segments(
    segments,
    stream_number
):

    checked = 0

    errors = 0


    max_segments = min(
        SEGMENT_CHECK_COUNT,
        len(segments)
    )


    logger.info(
        f"[SEGMENTS / СЕГМЕНТЫ] "
        f"Stream #{stream_number}"
    )


    logger.info(
        f"  Найдено / Found: "
        f"{len(segments)}"
    )


    logger.info(
        f"  Проверяем / Checking: "
        f"{max_segments}"
    )


    for index in range(
        max_segments
    ):

        segment_url = (
            segments[index]
        )


        checked += 1


        response = http_get(
            segment_url,
            timeout=HTTP_TIMEOUT,
            max_bytes=1024 * 1024
        )


        stats[
            "segments_checked"
        ] += 1


        if response[
            "success"
        ]:

            status = (
                response["status"]
            )


            if status == 200:

                logger.info(
                    f"  SEGMENT "
                    f"{index + 1}: "
                    f"HTTP {status} OK | "
                    f"{response['elapsed']:.3f}s | "
                    f"{len(response['data'])} bytes"
                )

            else:

                errors += 1

                stats[
                    "segment_errors"
                ] += 1

                logger.warning(
                    f"  SEGMENT "
                    f"{index + 1}: "
                    f"HTTP {status} FAILED"
                )


        else:

            errors += 1

            stats[
                "segment_errors"
            ] += 1

            logger.warning(
                f"  SEGMENT "
                f"{index + 1}: "
                f"FAILED | "
                f"{response['error']}"
            )


    return (
        checked,
        errors
    )


# ============================================================================
# ПРОВЕРКА ОДНОГО ПОТОКА
# ============================================================================

def verify_stream(
    stream_number,
    original_url
):

    started = time.perf_counter()


    logger.info("")
    logger.info("=" * 100)


    logger.info(
        f"[STREAM #{stream_number}] "
        "ПРОВЕРКА ПОТОКА / "
        "STREAM VERIFICATION"
    )


    logger.info(
        f"Original / Исходный:\n"
        f"  {original_url}"
    )


    # =========================================================================
    # GRAPH REPLACEMENT
    # =========================================================================

    (
        target_url,
        replaced,
        rule
    ) = process_node_scala_style(
        original_url
    )


    logger.info(
        f"Target / Проверяемый URL:\n"
        f"  {target_url}"
    )


    # =========================================================================
    # NETWORK CHECK
    # =========================================================================

    stats[
        "checked"
    ] += 1


    response = http_get(
        target_url
    )


    elapsed = (
        time.perf_counter()
        - started
    )


    logger.info(
        "[NETWORK CHECK / "
        "СЕТЕВАЯ ПРОВЕРКА]"
    )


    logger.info(
        f"  HTTP status / HTTP статус: "
        f"{response['status']}"
    )


    logger.info(
        f"  Response time / Время ответа: "
        f"{response['elapsed']:.3f} sec"
    )


    logger.info(
        f"  Content-Type / Тип: "
        f"{response['content_type'] or 'N/A'}"
    )


    logger.info(
        f"  Response size / Размер: "
        f"{len(response['data'])} bytes"
    )


    # =========================================================================
    # HTTP ERROR
    # =========================================================================

    if not response[
        "success"
    ]:

        stats[
            "errors"
        ] += 1

        stats[
            "failed"
        ] += 1

        stats[
            "verification_errors"
        ] += 1


        error_text = (
            response["error"]
        )


        if (
            error_text
            and
            "TIMEOUT"
            in
            error_text.upper()
        ):

            stats[
                "timeouts"
            ] += 1


        elif response[
            "status"
        ] is not None:

            stats[
                "http_errors"
            ] += 1


        elif error_text:

            stats[
                "connection_errors"
            ] += 1


        else:

            stats[
                "unknown_errors"
            ] += 1


        logger.error(
            "[FAILED / ОШИБКА]"
        )


        logger.error(
            f"  Error / Ошибка: "
            f"{error_text}"
        )


        logger.error(
            f"  Total time / Общее время: "
            f"{elapsed:.3f} sec"
        )


        logger.info(
            "=" * 100
        )


        return {

            "stream_number":
                stream_number,

            "original_url":
                original_url,

            "final_url":
                target_url,

            "verified":
                False,

            "replaced":
                replaced,

            "rule":
                rule,

            "error":
                error_text,
        }


    # =========================================================================
    # HTTP STATUS
    # =========================================================================

    if response[
        "status"
    ] != 200:

        stats[
            "errors"
        ] += 1

        stats[
            "failed"
        ] += 1

        stats[
            "verification_errors"
        ] += 1

        stats[
            "http_errors"
        ] += 1


        logger.error(
            "[FAILED / ОШИБКА]"
        )


        logger.error(
            f"HTTP status "
            f"{response['status']} "
            f"is not 200"
        )


        logger.info(
            "=" * 100
        )


        return {

            "stream_number":
                stream_number,

            "original_url":
                original_url,

            "final_url":
                target_url,

            "verified":
                False,

            "replaced":
                replaced,

            "rule":
                rule,

            "error":
                f"HTTP {response['status']}",
        }


    # =========================================================================
    # HTTP RESPONSE OK
    # =========================================================================

    stats[
        "responded"
    ] += 1


    # =========================================================================
    # HLS CHECK
    # =========================================================================

    hls = verify_hls_playlist(
        target_url,
        response["data"]
    )


    logger.info(
        "[HLS CHECK / ПРОВЕРКА HLS]"
    )


    logger.info(
        f"  Playlist type / Тип: "
        f"{hls['playlist_type']}"
    )


    if hls[
        "error"
    ]:

        stats[
            "errors"
        ] += 1

        stats[
            "failed"
        ] += 1

        stats[
            "verification_errors"
        ] += 1

        stats[
            "hls_errors"
        ] += 1


        logger.error(
            "[HLS FAILED / HLS ОШИБКА]"
        )


        logger.error(
            f"  {hls['error']}"
        )


        logger.info(
            "=" * 100
        )


        return {

            "stream_number":
                stream_number,

            "original_url":
                original_url,

            "final_url":
                target_url,

            "verified":
                False,

            "replaced":
                replaced,

            "rule":
                rule,

            "error":
                hls["error"],
        }


    logger.info(
        "  #EXTM3U: OK"
    )


    logger.info(
        f"  Segments / Сегменты: "
        f"{len(hls['segments'])}"
    )


    # =========================================================================
    # SEGMENT CHECK
    # =========================================================================

    (
        checked_segments,
        segment_errors
    ) = verify_segments(
        hls["segments"],
        stream_number
    )


    logger.info(
        f"  Checked segments / "
        f"Проверено сегментов: "
        f"{checked_segments}"
    )


    logger.info(
        f"  Segment errors / "
        f"Ошибок сегментов: "
        f"{segment_errors}"
    )


    # =========================================================================
    # SEGMENT FAILURE
    # =========================================================================

    if segment_errors > 0:

        stats[
            "errors"
        ] += 1

        stats[
            "failed"
        ] += 1

        stats[
            "verification_errors"
        ] += 1


        logger.error(
            "[FAILED / ОШИБКА]"
        )


        logger.error(
            "HLS playlist exists, "
            "but segment verification failed"
        )


        logger.info(
            "=" * 100
        )


        return {

            "stream_number":
                stream_number,

            "original_url":
                original_url,

            "final_url":
                target_url,

            "verified":
                False,

            "replaced":
                replaced,

            "rule":
                rule,

            "error":
                "Segment verification failed",
        }


    # =========================================================================
    # VERIFIED
    # =========================================================================

    stats[
        "verified"
    ] += 1


    logger.info(
        "[VERIFIED / ПРОВЕРЕНО]"
    )


    logger.info(
        "  HTTP: OK"
    )


    logger.info(
        "  HLS: OK"
    )


    logger.info(
        "  Segments: OK"
    )


    logger.info(
        "  STATUS: VERIFIED"
    )


    logger.info(
        f"  Total time / Общее время: "
        f"{elapsed:.3f} sec"
    )


    logger.info(
        "=" * 100
    )


    return {

        "stream_number":
            stream_number,

        "original_url":
            original_url,

        "final_url":
            target_url,

        "verified":
            True,

        "replaced":
            replaced,

        "rule":
            rule,

        "error":
            None,
    }


# ============================================================================
# ПАРСИНГ M3U
# ============================================================================

def parse_m3u_entries(
    content
):

    lines = (
        content.splitlines()
    )

    entries = []

    current_metadata = []


    for line in lines:

        stripped = line.strip()


        if not stripped:
            continue


        # ---------------------------------------------------------------------
        # URL
        # ---------------------------------------------------------------------

        if (
            not stripped.startswith("#")
            and
            (
                stripped.startswith(
                    "http://"
                )
                or
                stripped.startswith(
                    "https://"
                )
            )
        ):

            entries.append({

                "metadata":
                    list(
                        current_metadata
                    ),

                "url":
                    stripped,
            })


            current_metadata = []


        # ---------------------------------------------------------------------
        # METADATA
        # ---------------------------------------------------------------------

        else:

            if stripped.startswith(
                "#EXTINF"
            ):

                current_metadata.append(
                    line
                )


            elif stripped.startswith(
                "#EXTVLCOPT"
            ):

                current_metadata.append(
                    line
                )


            elif stripped.startswith(
                "#KODIPROP"
            ):

                current_metadata.append(
                    line
                )


            elif stripped.startswith(
                "#EXTGRP"
            ):

                current_metadata.append(
                    line
                )


    return entries


# ============================================================================
# ФОРМИРОВАНИЕ VERIFIED M3U
# ============================================================================

def build_verified_playlist(
    original_content,
    entries,
    results
):

    verified_results = [

        result

        for result in results

        if result[
            "verified"
        ]
    ]


    output_lines = [

        "#EXTM3U",

        "#PLAYLIST:Extra Channels 2026 - Verified",

        "# Generated by "
        "CINERAMA SCALA GRAPH PROCESSOR",

        "# Status: VERIFIED",

        (
            "# Generated: "
            +
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ),
    ]


    result_map = {

        result[
            "stream_number"
        ]:
        result

        for result in results
    }


    # =========================================================================
    # ДОБАВЛЯЕМ ТОЛЬКО VERIFIED
    # =========================================================================

    for index, entry in enumerate(
        entries,
        start=1
    ):

        result = (
            result_map.get(
                index
            )
        )


        if not result:
            continue


        if not result[
            "verified"
        ]:
            continue


        # Metadata.
        for metadata_line in (
            entry["metadata"]
        ):

            output_lines.append(
                metadata_line
            )


        # Проверенный URL.
        output_lines.append(
            result[
                "final_url"
            ]
        )


    return (
        "\n".join(
            output_lines
        )
        +
        "\n"
    )


# ============================================================================
# СОХРАНЕНИЕ VERIFIED PLAYLIST
# ============================================================================

def save_verified_playlist(
    content
):

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            content
        )


    logger.info("")

    logger.info("=" * 100)

    logger.info(
        "VERIFIED PLAYLIST CREATED"
    )

    logger.info("=" * 100)


    logger.info(
        "Файл / File:"
    )

    logger.info(
        f"  {OUTPUT_FILE}"
    )


    logger.info(
        "Статус / Status: VERIFIED"
    )


    logger.info(
        f"Размер / Size: "
        f"{len(content.encode('utf-8'))} bytes"
    )


    logger.info("=" * 100)


# ============================================================================
# ФИНАЛЬНАЯ СТАТИСТИКА
# ============================================================================

def print_final_statistics():

    logger.info("")
    logger.info("")

    logger.info(
        "=" * 100
    )

    logger.info(
        "ИТОГОВАЯ СТАТИСТИКА / "
        "FINAL STATISTICS"
    )

    logger.info(
        "=" * 100
    )


    # =========================================================================
    # РУССКИЙ
    # =========================================================================

    logger.info("")

    logger.info(
        "РУССКИЙ / RUSSIAN"
    )

    logger.info("")


    logger.info(
        f"Всего потоков:        "
        f"{stats['total_streams']}"
    )


    logger.info(
        f"Проверено:             "
        f"{stats['checked']}"
    )


    logger.info(
        f"Ответили:              "
        f"{stats['responded']}"
    )


    logger.info(
        f"Ошибок:                "
        f"{stats['errors']}"
    )


    logger.info(
        f"Заменено stream8:       "
        f"{stats['stream8_replaced']}"
    )


    logger.info(
        f"stream1 без замены:    "
        f"{stats['stream1_unchanged']}"
    )


    logger.info(
        f"Ошибок проверки:       "
        f"{stats['verification_errors']}"
    )


    # =========================================================================
    # ENGLISH
    # =========================================================================

    logger.info("")

    logger.info(
        "АНГЛИЙСКИЙ / ENGLISH"
    )

    logger.info("")


    logger.info(
        f"Total streams:         "
        f"{stats['total_streams']}"
    )


    logger.info(
        f"Checked:                "
        f"{stats['checked']}"
    )


    logger.info(
        f"Responded:              "
        f"{stats['responded']}"
    )


    logger.info(
        f"Errors:                 "
        f"{stats['errors']}"
    )


    logger.info(
        f"stream8 replaced:       "
        f"{stats['stream8_replaced']}"
    )


    logger.info(
        f"stream1 unchanged:      "
        f"{stats['stream1_unchanged']}"
    )


    logger.info(
        f"Verification errors:    "
        f"{stats['verification_errors']}"
    )


    # =========================================================================
    # ДОПОЛНИТЕЛЬНАЯ СТАТИСТИКА
    # =========================================================================

    logger.info("")

    logger.info(
        "ДОПОЛНИТЕЛЬНО / ADDITIONAL"
    )

    logger.info("")


    logger.info(
        f"VERIFIED:               "
        f"{stats['verified']}"
    )


    logger.info(
        f"FAILED:                 "
        f"{stats['failed']}"
    )


    logger.info(
        f"HTTP errors:            "
        f"{stats['http_errors']}"
    )


    logger.info(
        f"Timeouts:               "
        f"{stats['timeouts']}"
    )


    logger.info(
        f"Connection errors:      "
        f"{stats['connection_errors']}"
    )


    logger.info(
        f"HLS errors:             "
        f"{stats['hls_errors']}"
    )


    logger.info(
        f"Segments checked:       "
        f"{stats['segments_checked']}"
    )


    logger.info(
        f"Segment errors:         "
        f"{stats['segment_errors']}"
    )


    # =========================================================================
    # VERIFIED PLAYLIST
    # =========================================================================

    logger.info("")

    logger.info(
        "=" * 100
    )

    logger.info(
        "VERIFIED PLAYLIST"
    )

    logger.info(
        "=" * 100
    )


    logger.info(
        "Output / Результат:"
    )

    logger.info(
        f"  {OUTPUT_FILE}"
    )


    logger.info(
        "Status / Статус: VERIFIED"
    )


    logger.info(
        "=" * 100
    )


# ============================================================================
# MAIN
# ============================================================================

def main():

    reset_statistics()


    # =========================================================================
    # INPUT
    # =========================================================================

    input_file = (
        get_input_file()
    )


    logger.info("")

    logger.info(
        "#" * 100
    )

    logger.info(
        "CINERAMA SCALA GRAPH PROCESSOR"
    )

    logger.info(
        "REAL NETWORK / HLS VERIFICATOR"
    )

    logger.info(
        "РАСШИРЕННАЯ ПРОВЕРКА / "
        "EXTENDED VERIFICATION"
    )

    logger.info("")

    logger.info(
        "SOURCE BRANCH / ВЕТКА ИСТОЧНИКА:"
    )

    logger.info(
        "  gh-pages"
    )

    logger.info("")

    logger.info(
        "SOURCE FILE / ИСХОДНЫЙ ФАЙЛ:"
    )

    logger.info(
        f"  {input_file}"
    )

    logger.info("")

    logger.info(
        "OUTPUT FILE / ВЫХОДНОЙ ФАЙЛ:"
    )

    logger.info(
        f"  {OUTPUT_FILE}"
    )

    logger.info("")

    logger.info(
        "LOG FILE / ФАЙЛ ЛОГА:"
    )

    logger.info(
        f"  {LOG_FILE}"
    )

    logger.info("")

    logger.info(
        f"Workers: {MAX_WORKERS}"
    )

    logger.info(
        f"HTTP timeout: {HTTP_TIMEOUT}s"
    )

    logger.info(
        f"Segment checks: "
        f"{SEGMENT_CHECK_COUNT}"
    )

    logger.info(
        "#" * 100
    )


    # =========================================================================
    # ЗАГРУЗКА
    # =========================================================================

    content = load_playlist(
        input_file
    )


    # =========================================================================
    # ПАРСИНГ
    # =========================================================================

    entries = (
        parse_m3u_entries(
            content
        )
    )


    stats[
        "total_streams"
    ] = len(entries)


    logger.info("")

    logger.info(
        "=" * 100
    )

    logger.info(
        "РАЗБОР M3U / M3U PARSING"
    )

    logger.info(
        "=" * 100
    )


    logger.info(
        f"Найдено потоков / "
        f"Streams found: "
        f"{stats['total_streams']}"
    )


    if not entries:

        logger.warning(
            "Потоки не найдены / "
            "No streams found"
        )


        verified_playlist = (
            "#EXTM3U\n"
            "#PLAYLIST:Extra Channels 2026 - Verified\n"
            "# Status: VERIFIED\n"
        )


        save_verified_playlist(
            verified_playlist
        )


        print_final_statistics()


        return 0


    # =========================================================================
    # NETWORK VERIFICATION
    # =========================================================================

    logger.info("")

    logger.info(
        "=" * 100
    )

    logger.info(
        "НАЧАЛО СЕТЕВОЙ ПРОВЕРКИ / "
        "START NETWORK VERIFICATION"
    )

    logger.info(
        "=" * 100
    )


    results = []


    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:


        future_map = {}


        # ---------------------------------------------------------------------
        # ЗАПУСК ВСЕХ ПОТОКОВ
        # ---------------------------------------------------------------------

        for stream_number, entry in enumerate(
            entries,
            start=1
        ):

            future = executor.submit(
                verify_stream,
                stream_number,
                entry["url"]
            )


            future_map[
                future
            ] = stream_number


        # ---------------------------------------------------------------------
        # ПОЛУЧЕНИЕ РЕЗУЛЬТАТОВ
        # ---------------------------------------------------------------------

        for future in as_completed(
            future_map
        ):

            stream_number = (
                future_map[
                    future
                ]
            )


            try:

                result = (
                    future.result()
                )


                results.append(
                    result
                )


            except Exception as exc:

                stats[
                    "errors"
                ] += 1

                stats[
                    "failed"
                ] += 1

                stats[
                    "verification_errors"
                ] += 1

                stats[
                    "unknown_errors"
                ] += 1


                logger.exception(
                    f"[STREAM #{stream_number}] "
                    "КРИТИЧЕСКАЯ ОШИБКА / "
                    "CRITICAL ERROR:\n"
                    f"{exc}"
                )


                results.append({

                    "stream_number":
                        stream_number,

                    "original_url":
                        entries[
                            stream_number - 1
                        ]["url"],

                    "final_url":
                        entries[
                            stream_number - 1
                        ]["url"],

                    "verified":
                        False,

                    "replaced":
                        False,

                    "rule":
                        None,

                    "error":
                        str(exc),
                })


    # =========================================================================
    # СОРТИРОВКА
    # =========================================================================

    results.sort(
        key=lambda item:
        item[
            "stream_number"
        ]
    )


    # =========================================================================
    # VERIFIED M3U
    # =========================================================================

    logger.info("")

    logger.info(
        "=" * 100
    )

    logger.info(
        "ФОРМИРОВАНИЕ VERIFIED M3U / "
        "BUILDING VERIFIED M3U"
    )

    logger.info(
        "=" * 100
    )


    verified_playlist = (
        build_verified_playlist(
            content,
            entries,
            results
        )
    )


    save_verified_playlist(
        verified_playlist
    )


    # =========================================================================
    # ФИНАЛЬНАЯ СТАТИСТИКА
    # =========================================================================

    print_final_statistics()


    logger.info("")

    logger.info(
        "Лог / Log:"
    )

    logger.info(
        f"  {LOG_FILE}"
    )


    logger.info(
        "Verified playlist / "
        "Проверенный плейлист:"
    )

    logger.info(
        f"  {OUTPUT_FILE}"
    )


    logger.info("")

    logger.info(
        "ИСТОЧНИК / SOURCE:"
    )

    logger.info(
        "  gh-pages/"
        f"{input_file}"
    )


    logger.info(
        "РЕЗУЛЬТАТ / RESULT:"
    )

    logger.info(
        "  gh-pages-2/"
        f"{OUTPUT_FILE}"
    )


    logger.info("")

    logger.info(
        "ОБРАБОТКА ЗАВЕРШЕНА / "
        "PROCESSING COMPLETED"
    )


    return 0


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":

    try:

        exit_code = main()

        sys.exit(
            exit_code
        )


    except KeyboardInterrupt:

        logger.warning(
            "ОБРАБОТКА ПРЕРВАНА ПОЛЬЗОВАТЕЛЕМ / "
            "PROCESSING INTERRUPTED BY USER"
        )

        sys.exit(130)


    except Exception as exc:

        logger.exception(
            "КРИТИЧЕСКАЯ ОШИБКА / "
            "CRITICAL ERROR:\n"
            f"{exc}"
        )

        sys.exit(1)