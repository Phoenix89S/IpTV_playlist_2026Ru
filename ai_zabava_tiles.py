#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import requests

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin


# ============================================================
#                         CONFIGURATION
# ============================================================

# ------------------------------------------------------------
# DmitryTV result
# ------------------------------------------------------------

JSON_INPUT = "_scanner_zaba_1788021849270.txt"


# ------------------------------------------------------------
# NGENIX CDN
# ------------------------------------------------------------

BASE_CDN = "https://zabava-htlive.cdn.ngenix.net"

# Эталон:
#
# https://zabava-htlive.cdn.ngenix.net/hls/CH_TVC/variant.m3u8
#
MASTER_VARIANT = "/hls/CH_TVC/variant.m3u8"


# ------------------------------------------------------------
# OUTPUT
# ------------------------------------------------------------

OUT_JSON = "zabava_ngenix_scan.json"
OUT_M3U = "zabava_ngenix.m3u"
OUT_SKALA = "zabava_ngenix_skala.txt"
OUT_LOG = "zabava_ngenix_scan.log"


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HTTP_TIMEOUT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 ZABAVA-NGENIX-SCANNER/3.0",
    "Accept": "*/*",
}


# ============================================================
#                         SKALA LOGGER
# ============================================================

class ScalaLogger:

    def __init__(self, filename):

        self.filename = filename

        with open(
            filename,
            "w",
            encoding="utf-8"
        ):
            pass

    def log(self, level, message):

        now = datetime.now(
            timezone.utc
        ).astimezone()

        timestamp = now.strftime(
            "%Y-%m-%dT%H:%M:%S.%f%z"
        )

        line = (
            f"{timestamp} "
            f"[{level:<7}] "
            f"{message}"
        )

        print(line)

        with open(
            self.filename,
            "a",
            encoding="utf-8"
        ) as f:

            f.write(line + "\n")

    def info(self, message):
        self.log("INFO", message)

    def found(self, message):
        self.log("FOUND", message)

    def warn(self, message):
        self.log("WARN", message)

    def error(self, message):
        self.log("ERROR", message)


# ============================================================
#                 LOAD DMITRYTV SOURCE
# ============================================================

def load_dmitrytv_source(logger):

    logger.info(
        f"LOAD DMITRYTV SOURCE: {JSON_INPUT}"
    )

    path = Path(JSON_INPUT)

    if not path.exists():

        raise FileNotFoundError(
            f"Файл не найден: {JSON_INPUT}"
        )

    text = path.read_text(
        encoding="utf-8-sig"
    )

    text = text.strip()

    if not text:

        raise ValueError(
            "Файл DmitryTV пустой"
        )

    # --------------------------------------------------------
    # Вариант 1: файл является JSON
    # --------------------------------------------------------

    try:

        data = json.loads(text)

        if isinstance(data, dict):

            tails = data.get(
                "tails",
                []
            )

            if isinstance(
                tails,
                list
            ):

                logger.info(
                    f"JSON TAILS: {len(tails)}"
                )

                return data, tails

        elif isinstance(data, list):

            logger.info(
                f"JSON ARRAY TAILS: {len(data)}"
            )

            return {
                "tails": data
            }, data

    except json.JSONDecodeError:

        pass

    # --------------------------------------------------------
    # Вариант 2: обычный TXT со строками tails
    # --------------------------------------------------------

    tails = []

    for line in text.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        tails.append(line)

    logger.info(
        f"TEXT TAILS: {len(tails)}"
    )

    return {
        "tails": tails
    }, tails


# ============================================================
#              EXTRACT EXACT CH ALIAS FROM TAIL
# ============================================================

def extract_alias_from_tail(tail):

    """
    ВАЖНО:

    Берём ровно имя CH_... перед .m3u8.

    Например:

        /hls/DVB-T2/CH_1TV-1.m3u8
        -> CH_1TV-1

        /hls/DVB-T2/CH_1TV.m3u8
        -> CH_1TV

        /hls/DVB-T2/CH_1TV_2.m3u8
        -> CH_1TV_2

        /hls/zabava/region/ABAKAN/CH_TV7.m3u8
        -> CH_TV7

    Никаких объединений или удаления суффиксов.
    """

    if not isinstance(
        tail,
        str
    ):
        return None

    tail = tail.strip()

    if not tail:
        return None

    # --------------------------------------------------------
    # Берём ПОСЛЕДНИЙ сегмент пути:
    #
    # CH_XXXXX.m3u8
    # --------------------------------------------------------

    match = re.search(
        r"/(CH_[^/]+)\.m3u8(?:[?#].*)?$",
        tail,
        flags=re.IGNORECASE
    )

    if match:

        return match.group(1)

    # --------------------------------------------------------
    # На случай если в данных уже:
    #
    # CH_TVC.m3u8
    # --------------------------------------------------------

    match = re.fullmatch(
        r"(CH_[^/]+)\.m3u8",
        tail,
        flags=re.IGNORECASE
    )

    if match:

        return match.group(1)

    return None


# ============================================================
#                  EXTRACT ALL ALIASES
# ============================================================

def extract_aliases(
    tails,
    logger
):

    aliases = []

    # alias -> исходные tails
    source_map = {}

    seen = set()

    for tail in tails:

        alias = extract_alias_from_tail(
            tail
        )

        if not alias:

            logger.warn(
                f"ALIAS NOT FOUND: {tail}"
            )

            continue

        # ----------------------------------------------------
        # Сохраняем происхождение
        # ----------------------------------------------------

        source_map.setdefault(
            alias,
            []
        ).append(
            tail
        )

        # ----------------------------------------------------
        # Одинаковый alias проверяем один раз.
        #
        # Но разные:
        #
        # CH_1TV
        # CH_1TV_2
        # CH_1TV_4
        #
        # считаются РАЗНЫМИ alias.
        # ----------------------------------------------------

        if alias in seen:

            logger.info(
                f"DUPLICATE ALIAS SKIP: {alias}"
            )

            continue

        seen.add(alias)

        aliases.append(
            alias
        )

        logger.found(
            f"ALIAS EXTRACTED: {alias}"
        )

    logger.info(
        f"UNIQUE ALIASES: {len(aliases)}"
    )

    return aliases, source_map


# ============================================================
#                   BUILD NGENIX URL
# ============================================================

def build_ngenix_tail(alias):

    return (
        "/hls/"
        + alias
        + "/variant.m3u8"
    )


def build_ngenix_url(alias):

    return (
        BASE_CDN
        + build_ngenix_tail(alias)
    )


# ============================================================
#                GENERATE COMPLETE LIST
# ============================================================

def generate_ngenix_candidates(
    aliases,
    source_map,
    logger
):

    candidates = []

    for index, alias in enumerate(
        aliases,
        start=1
    ):

        ngenix_tail = build_ngenix_tail(
            alias
        )

        ngenix_url = build_ngenix_url(
            alias
        )

        item = {

            "index":
                index,

            "alias":
                alias,

            "source_tails":
                source_map.get(
                    alias,
                    []
                ),

            "ngenix_tail":
                ngenix_tail,

            "ngenix_url":
                ngenix_url,

        }

        candidates.append(
            item
        )

        logger.info(
            f"GENERATED "
            f"[{index}/{len(aliases)}] "
            f"{alias} -> {ngenix_url}"
        )

    logger.info(
        f"TOTAL NGENIX CANDIDATES: "
        f"{len(candidates)}"
    )

    return candidates


# ============================================================
#                 EXTRACT EXTINF INFORMATION
# ============================================================

def parse_extinf(playlist_text):

    """
    Берём реальный #EXTINF из полученного
    NGENIX variant.m3u8.

    Возвращаем:

        extinf
        tvg attributes
        display name

    Ничего не придумываем, если #EXTINF есть.
    """

    if not playlist_text:

        return {
            "extinf": None,
            "tvg": {},
            "name": None,
        }

    lines = playlist_text.splitlines()

    for line in lines:

        line = line.strip()

        if not line.startswith(
            "#EXTINF:"
        ):
            continue

        extinf = line

        # ----------------------------------------------------
        # Извлекаем duration
        # ----------------------------------------------------

        duration = None

        duration_match = re.match(
            r"#EXTINF:([^,]+),",
            line
        )

        if duration_match:

            duration = (
                duration_match.group(1)
            )

        # ----------------------------------------------------
        # Извлекаем tvg-* атрибуты
        # ----------------------------------------------------

        tvg = {}

        attributes = re.findall(
            r'([\w-]+)="([^"]*)"',
            line
        )

        for key, value in attributes:

            if key.startswith(
                "tvg-"
            ):

                tvg[key] = value

        # ----------------------------------------------------
        # Имя после последней запятой
        # ----------------------------------------------------

        name = None

        if "," in line:

            name = (
                line.split(
                    ",",
                    1
                )[1]
                .strip()
            )

        return {
            "extinf": extinf,
            "duration": duration,
            "tvg": tvg,
            "name": name,
        }

    return {
        "extinf": None,
        "duration": None,
        "tvg": {},
        "name": None,
    }


# ============================================================
#             EXTRACT ADDITIONAL PLAYLIST DATA
# ============================================================

def extract_playlist_info(
    playlist_text,
    variant_url
):

    """
    Сохраняем полезную информацию
    из реально полученного variant.m3u8.

    Это не проверка сегментов.
    Это просто разбор уже полученного
    master/media playlist.
    """

    info = parse_extinf(
        playlist_text
    )

    segments = []

    if playlist_text:

        for line in playlist_text.splitlines():

            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            segments.append(
                urljoin(
                    variant_url,
                    line
                )
            )

    info["segment_count"] = len(
        segments
    )

    return info


# ============================================================
#                    CHECK NGENIX CDN
# ============================================================

def check_ngenix(
    candidates,
    logger
):

    """
    Для каждого alias:

        1. GET NGENIX variant.m3u8
        2. проверяем HTTP 200
        3. если 200 — сохраняем содержимое
        4. извлекаем реальный EXTINF
        5. канал считается рабочим

    Никакого опроса DmitryTV здесь нет.
    Проверяется именно NGENIX.
    """

    results = []

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    total = len(candidates)

    for number, candidate in enumerate(
        candidates,
        start=1
    ):

        alias = candidate[
            "alias"
        ]

        url = candidate[
            "ngenix_url"
        ]

        status = None
        error = None
        response_text = None

        try:

            response = session.get(
                url,
                timeout=HTTP_TIMEOUT,
                allow_redirects=True
            )

            status = response.status_code

            if status == 200:

                response_text = (
                    response.text
                )

            response.close()

        except requests.RequestException as exc:

            error = str(exc)

        ok = (
            status == 200
        )

        result = dict(
            candidate
        )

        result["http"] = status
        result["ok"] = ok

        # ----------------------------------------------------
        # Реальный EXTINF из NGENIX
        # ----------------------------------------------------

        if ok:

            playlist_info = (
                extract_playlist_info(
                    response_text,
                    url
                )
            )

            result[
                "playlist"
            ] = playlist_info

            # Сохраняем реальный EXTINF
            result[
                "extinf"
            ] = playlist_info.get(
                "extinf"
            )

            result[
                "channel_name"
            ] = playlist_info.get(
                "name"
            )

        else:

            result[
                "playlist"
            ] = {
                "extinf": None,
                "duration": None,
                "tvg": {},
                "name": None,
                "segment_count": 0,
            }

            result[
                "extinf"
            ] = None

            result[
                "channel_name"
            ] = None

        if error:

            result[
                "error"
            ] = error

        results.append(
            result
        )

        # ====================================================
        # КРАТКИЙ SKALA
        # ====================================================

        if ok:

            ext_name = (
                result.get(
                    "channel_name"
                )
                or alias
            )

            logger.found(
                f"[{number}/{total}] "
                f"{alias} -> 200 OK -> "
                f"{ext_name}"
            )

        elif error:

            logger.warn(
                f"[{number}/{total}] "
                f"{alias} -> ERROR"
            )

        else:

            logger.warn(
                f"[{number}/{total}] "
                f"{alias} -> HTTP {status}"
            )

    return results


# ============================================================
#                  BUILD FINAL JSON
# ============================================================

def build_final_json(
    source_data,
    tails,
    aliases,
    candidates,
    results
):

    working = [
        item
        for item in results
        if item["ok"]
    ]

    failed = [
        item
        for item in results
        if not item["ok"]
    ]

    return {

        # ----------------------------------------------------
        # Scanner information
        # ----------------------------------------------------

        "scanner": {

            "name":
                "zabava_ngenix_scan",

            "version":
                "3.0",

            "timestamp":
                datetime.now(
                    timezone.utc
                ).isoformat(),

        },

        # ----------------------------------------------------
        # DmitryTV source
        # ----------------------------------------------------

        "source": {

            "type":
                "DmitryTV",

            "file":
                JSON_INPUT,

            "tails_received":
                len(tails),

        },

        # ----------------------------------------------------
        # NGENIX master
        # ----------------------------------------------------

        "ngenix": {

            "base":
                BASE_CDN,

            "master_variant":
                MASTER_VARIANT,

            "generated_pattern":
                "/hls/{alias}/variant.m3u8",

        },

        # ----------------------------------------------------
        # Statistics
        # ----------------------------------------------------

        "statistics": {

            "tails_received":
                len(tails),

            "aliases_extracted":
                len(aliases),

            "urls_generated":
                len(candidates),

            "urls_checked":
                len(results),

            "http_200":
                len(working),

            "failed":
                len(failed),

        },

        # ----------------------------------------------------
        # ALL generated and checked channels
        # ----------------------------------------------------

        "channels":
            results,

        # ----------------------------------------------------
        # Working NGENIX channels
        # ----------------------------------------------------

        "working_channels": [
            item
            for item in results
            if item["ok"]
        ],

        # ----------------------------------------------------
        # Failed channels
        # ----------------------------------------------------

        "failed_channels": [
            item
            for item in results
            if not item["ok"]
        ],

        # ----------------------------------------------------
        # Original DmitryTV source
        # ----------------------------------------------------

        "source_snapshot":
            source_data,

    }


# ============================================================
#                       BUILD M3U
# ============================================================

def build_m3u(results):

    lines = [
        "#EXTM3U"
    ]

    for item in results:

        if not item["ok"]:
            continue

        url = item[
            "ngenix_url"
        ]

        # ----------------------------------------------------
        # Если NGENIX действительно вернул EXTINF,
        # используем его БЕЗ изменения.
        # ----------------------------------------------------

        extinf = item.get(
            "extinf"
        )

        if extinf:

            lines.append(
                extinf
            )

        else:

            # ------------------------------------------------
            # Резервный EXTINF только если
            # NGENIX не прислал его.
            # ------------------------------------------------

            name = (
                item.get(
                    "channel_name"
                )
                or item[
                    "alias"
                ]
            )

            lines.append(
                f"#EXTINF:-1,{name}"
            )

        # ----------------------------------------------------
        # URL именно NGENIX
        # ----------------------------------------------------

        lines.append(
            url
        )

    return (
        "\n".join(lines)
        + "\n"
    )


# ============================================================
#                    BUILD SKALA TXT
# ============================================================

def build_skala(
    tails,
    aliases,
    candidates,
    results
):

    working = sum(
        1
        for item in results
        if item["ok"]
    )

    failed = (
        len(results)
        - working
    )

    lines = []

    lines.append(
        "=================================================="
    )

    lines.append(
        "             ZABAVA NGENIX SKALA"
    )

    lines.append(
        "=================================================="
    )

    lines.append("")

    lines.append(
        f"SOURCE        : DmitryTV"
    )

    lines.append(
        f"CDN           : {BASE_CDN}"
    )

    lines.append(
        f"MASTER        : {MASTER_VARIANT}"
    )

    lines.append("")

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    lines.append(
        "---------------- SUMMARY ----------------"
    )

    lines.append(
        f"TAILS         : {len(tails)}"
    )

    lines.append(
        f"ALIASES       : {len(aliases)}"
    )

    lines.append(
        f"GENERATED     : {len(candidates)}"
    )

    lines.append(
        f"CHECKED       : {len(results)}"
    )

    lines.append(
        f"HTTP 200      : {working}"
    )

    lines.append(
        f"FAILED        : {failed}"
    )

    lines.append("")

    # --------------------------------------------------------
    # Channels
    # --------------------------------------------------------

    lines.append(
        "---------------- CHANNELS ----------------"
    )

    for item in results:

        alias = item[
            "alias"
        ]

        status = item[
            "http"
        ]

        if item["ok"]:

            name = (
                item.get(
                    "channel_name"
                )
                or "-"
            )

            lines.append(
                f"{alias:<32} "
                f"{str(status):<5} "
                f"OK   "
                f"{name}"
            )

        else:

            lines.append(
                f"{alias:<32} "
                f"{str(status):<5} "
                f"FAIL"
            )

    lines.append("")

    # --------------------------------------------------------
    # Working
    # --------------------------------------------------------

    lines.append(
        "--------------- WORKING ----------------"
    )

    for item in results:

        if not item["ok"]:
            continue

        alias = item[
            "alias"
        ]

        name = (
            item.get(
                "channel_name"
            )
            or "-"
        )

        lines.append(
            f"{alias} -> {name}"
        )

    lines.append("")

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    lines.append(
        "---------------- OUTPUT -----------------"
    )

    lines.append(
        f"JSON          : {OUT_JSON}"
    )

    lines.append(
        f"PLAYLIST      : {OUT_M3U}"
    )

    lines.append(
        f"SKALA TXT     : {OUT_SKALA}"
    )

    lines.append(
        f"LOG           : {OUT_LOG}"
    )

    lines.append("")

    lines.append(
        "=================================================="
    )

    lines.append(
        "              SKALA REPORT END"
    )

    lines.append(
        "=================================================="
    )

    return (
        "\n".join(lines)
        + "\n"
    )


# ============================================================
#                         SAVE JSON
# ============================================================

def save_json(
    filename,
    data
):

    Path(
        filename
    ).write_text(

        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),

        encoding="utf-8"
    )


# ============================================================
#                          MAIN
# ============================================================

def main():

    logger = ScalaLogger(
        OUT_LOG
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "          START ZABAVA NGENIX SCAN"
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        f"DMITRYTV SOURCE: {JSON_INPUT}"
    )

    logger.info(
        f"NGENIX CDN: {BASE_CDN}"
    )

    logger.info(
        f"NGENIX MASTER: {MASTER_VARIANT}"
    )

    # ========================================================
    # 1. Читаем результат DmitryTV
    # ========================================================

    source_data, tails = (
        load_dmitrytv_source(
            logger
        )
    )

    # ========================================================
    # 2. Из каждого tail берём РОВНО CH_...m3u8
    # ========================================================

    aliases, source_map = (
        extract_aliases(
            tails,
            logger
        )
    )

    # ========================================================
    # 3. Для КАЖДОГО alias строим ОДНУ
    #    ссылку NGENIX
    # ========================================================

    candidates = (
        generate_ngenix_candidates(
            aliases,
            source_map,
            logger
        )
    )

    # ========================================================
    # 4. Полностью сформированный список
    #    проверяем на NGENIX
    # ========================================================

    results = check_ngenix(
        candidates,
        logger
    )

    # ========================================================
    # 5. Полный итоговый JSON
    # ========================================================

    final_json = build_final_json(
        source_data,
        tails,
        aliases,
        candidates,
        results
    )

    save_json(
        OUT_JSON,
        final_json
    )

    # ========================================================
    # 6. Готовый M3U
    # ========================================================

    m3u = build_m3u(
        results
    )

    Path(
        OUT_M3U
    ).write_text(
        m3u,
        encoding="utf-8"
    )

    # ========================================================
    # 7. SKALA TXT
    # ========================================================

    skala = build_skala(
        tails,
        aliases,
        candidates,
        results
    )

    Path(
        OUT_SKALA
    ).write_text(
        skala,
        encoding="utf-8"
    )

    # ========================================================
    # 8. Финальный лог
    # ========================================================

    working = sum(
        1
        for item in results
        if item["ok"]
    )

    failed = (
        len(results)
        - working
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "                  FINAL RESULT"
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        f"DMITRYTV TAILS : {len(tails)}"
    )

    logger.info(
        f"ALIASES        : {len(aliases)}"
    )

    logger.info(
        f"NGENIX URLS    : {len(candidates)}"
    )

    logger.info(
        f"NGENIX CHECKED : {len(results)}"
    )

    logger.info(
        f"HTTP 200       : {working}"
    )

    logger.info(
        f"FAILED         : {failed}"
    )

    logger.info(
        f"JSON           : {OUT_JSON}"
    )

    logger.info(
        f"M3U            : {OUT_M3U}"
    )

    logger.info(
        f"SKALA TXT      : {OUT_SKALA}"
    )

    logger.info(
        f"LOG            : {OUT_LOG}"
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "             NGENIX SCAN COMPLETE"
    )

    logger.info(
        "=================================================="
    )


# ============================================================
#                       ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()