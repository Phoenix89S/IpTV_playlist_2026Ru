#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import requests
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
#                         CONFIGURATION
# ============================================================

# Результат сканирования DmitryTV
JSON_INPUT = "_scanner_zaba_1788021849270.txt"

# NGENIX CDN
BASE_CDN = "https://zabava-htlive.cdn.ngenix.net"

# Мастер-вариант NGENIX
#
# Именно эта структура является эталоном:
#
# /hls/CH_TVC/variant.m3u8
#
MASTER_VARIANT = "/hls/CH_TVC/variant.m3u8"

# ============================================================
#                         OUTPUT
# ============================================================

OUT_JSON = "zabava_ngenix_scan.json"
OUT_M3U = "zabava_ngenix.m3u"
OUT_SKALA = "zabava_ngenix_skala.txt"
OUT_LOG = "zabava_ngenix_scan.log"

# HTTP timeout
HTTP_TIMEOUT = 10

# HTTP headers
HEADERS = {
    "User-Agent": "ZABAVA-NGENIX-SCANNER/3.0",
    "Accept": "*/*",
}


# ============================================================
#                         SKALA LOGGER
# ============================================================

class ScalaLogger:

    def __init__(self, filename):

        self.filename = filename

        with open(
            self.filename,
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
#                  LOAD DMITRYTV RESULT
# ============================================================

def load_source_json(logger):

    logger.info(
        f"LOAD DMITRYTV JSON: {JSON_INPUT}"
    )

    with open(
        JSON_INPUT,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if not isinstance(data, dict):

        raise ValueError(
            "Исходный JSON должен быть объектом"
        )

    if "tails" not in data:

        raise KeyError(
            "В исходном JSON отсутствует поле 'tails'"
        )

    tails = data["tails"]

    if not isinstance(tails, list):

        raise ValueError(
            "Поле 'tails' должно быть массивом"
        )

    logger.info(
        f"DMITRYTV TAILS LOADED: {len(tails)}"
    )

    return data, tails


# ============================================================
#                     EXTRACT CH ALIAS
# ============================================================

def extract_alias(tail):

    """
    Из tail извлекается только CH-алиас.

    Например:

        /hls/DmitryTV/CH_TVC/mono.m3u8

    превращается в:

        CH_TVC

    Никакая часть DmitryTV URL дальше
    не используется для формирования NGENIX URL.
    """

    if not isinstance(tail, str):

        return None

    tail = tail.strip()

    if not tail:

        return None

    # Основной вариант.
    #
    # Ищем отдельный сегмент:
    #
    # /CH_TVC/
    # /CH_NTV/
    # /CH_REN/
    #

    match = re.search(
        r"(?:^|/)"
        r"(CH_[A-Za-z0-9_-]+)"
        r"(?:/|$)",
        tail
    )

    if match:

        return match.group(1)

    # Если tail уже является самим алиасом.

    if re.fullmatch(
        r"CH_[A-Za-z0-9_-]+",
        tail
    ):

        return tail

    return None


# ============================================================
#                  EXTRACT UNIQUE ALIASES
# ============================================================

def extract_aliases(
    tails,
    logger
):

    aliases = []

    # Для каждого alias сохраняем
    # все исходные DmitryTV tails.
    alias_sources = {}

    seen = set()

    for tail in tails:

        alias = extract_alias(
            tail
        )

        if not alias:

            logger.warn(
                f"ALIAS NOT FOUND: {tail}"
            )

            continue

        if alias not in alias_sources:

            alias_sources[alias] = []

        alias_sources[alias].append(
            tail
        )

        # Один alias проверяем только один раз.

        if alias in seen:

            logger.info(
                f"DUPLICATE ALIAS SKIP: {alias}"
            )

            continue

        seen.add(alias)

        aliases.append(alias)

        logger.found(
            f"ALIAS EXTRACTED: {alias}"
        )

    logger.info(
        f"UNIQUE ALIASES: {len(aliases)}"
    )

    return aliases, alias_sources


# ============================================================
#                    BUILD NGENIX URL
# ============================================================

def build_ngenix_tail(alias):

    """
    Формирует tail NGENIX строго по master:

        /hls/CH_TVC/variant.m3u8

    где CH_TVC заменяется на найденный alias.
    """

    return (
        "/hls/"
        + alias
        + "/variant.m3u8"
    )


def build_ngenix_url(alias):

    """
    Формирует полный NGENIX URL:

        https://zabava-htlive.cdn.ngenix.net
        /hls/CH_TVC/variant.m3u8
    """

    return (
        BASE_CDN
        + build_ngenix_tail(alias)
    )


# ============================================================
#                GENERATE COMPLETE NGENIX LIST
# ============================================================

def generate_ngenix_list(
    aliases,
    alias_sources,
    logger
):

    channels = []

    for alias in aliases:

        nginx_tail = build_ngenix_tail(
            alias
        )

        url = build_ngenix_url(
            alias
        )

        item = {

            # Алиас, полученный из DmitryTV
            "alias": alias,

            # Исходные tails DmitryTV,
            # из которых получен alias
            "source_tails": alias_sources.get(
                alias,
                []
            ),

            # Новый tail NGENIX
            "ngenix_tail": nginx_tail,

            # Полная ссылка NGENIX
            "ngenix_url": url,

        }

        channels.append(
            item
        )

        logger.info(
            f"NGENIX GENERATED: "
            f"{alias} -> {url}"
        )

    logger.info(
        f"NGENIX URLS GENERATED: "
        f"{len(channels)}"
    )

    return channels


# ============================================================
#                     NGENIX CDN CHECK
# ============================================================

def check_ngenix(
    channels,
    logger
):

    """
    Проверяется КАЖДЫЙ сформированный NGENIX URL.

    Единственный критерий рабочего канала:

        HTTP 200

    Если HTTP != 200:
        канал считается нерабочим.

    Никаких дополнительных проверок
    сегментов, EPG и содержимого здесь нет.
    """

    results = []

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    total = len(channels)

    for number, channel in enumerate(
        channels,
        start=1
    ):

        alias = channel["alias"]
        url = channel["ngenix_url"]

        status = None
        error = None

        try:

            response = session.get(
                url,
                timeout=HTTP_TIMEOUT,
                allow_redirects=True,
                stream=True
            )

            status = response.status_code

            response.close()

        except requests.RequestException as exc:

            error = str(exc)

        ok = (
            status == 200
        )

        result = dict(
            channel
        )

        result["http"] = status
        result["ok"] = ok

        if error:

            result["error"] = error

        results.append(
            result
        )

        # ----------------------------------------------------
        # КРАТКИЙ РЕЗУЛЬТАТ В SKALA
        # ----------------------------------------------------

        if ok:

            logger.found(
                f"[{number}/{total}] "
                f"NGENIX {alias} -> 200 OK"
            )

        elif error:

            logger.warn(
                f"[{number}/{total}] "
                f"NGENIX {alias} -> ERROR"
            )

        else:

            logger.warn(
                f"[{number}/{total}] "
                f"NGENIX {alias} -> HTTP {status}"
            )

    return results


# ============================================================
#                    BUILD FINAL JSON
# ============================================================

def build_final_json(
    source_data,
    tails,
    aliases,
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
        # Scanner
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
        # Source DmitryTV
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
        # NGENIX
        # ----------------------------------------------------

        "ngenix": {

            "base":
                BASE_CDN,

            "master_variant":
                MASTER_VARIANT,

            "generated_variant":
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

            "aliases_checked":
                len(results),

            "working_http_200":
                len(working),

            "failed":
                len(failed),

        },

        # ----------------------------------------------------
        # Complete channel information
        # ----------------------------------------------------

        "channels":
            results,

        # ----------------------------------------------------
        # Convenient working list
        # ----------------------------------------------------

        "working_channels": [

            item["alias"]

            for item in working

        ],

        # ----------------------------------------------------
        # Failed list
        # ----------------------------------------------------

        "failed_channels": [

            item["alias"]

            for item in failed

        ],

        # ----------------------------------------------------
        # Original DmitryTV JSON
        # ----------------------------------------------------

        "source_snapshot":
            source_data,

    }


# ============================================================
#                        BUILD M3U
# ============================================================

def build_m3u(results):

    lines = [
        "#EXTM3U"
    ]

    for item in results:

        # Только HTTP 200

        if not item["ok"]:

            continue

        alias = item["alias"]
        url = item["ngenix_url"]

        lines.append(
            f"#EXTINF:-1,{alias}"
        )

        lines.append(
            url
        )

    return (
        "\n".join(lines)
        + "\n"
    )


# ============================================================
#                    BUILD SKALA REPORT
# ============================================================

def build_skala(
    tails,
    aliases,
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
        "========================================"
    )

    lines.append(
        "          ZABAVA NGENIX SKALA"
    )

    lines.append(
        "========================================"
    )

    lines.append("")

    lines.append(
        f"SOURCE       : DmitryTV"
    )

    lines.append(
        f"NGENIX CDN   : {BASE_CDN}"
    )

    lines.append(
        f"MASTER       : {MASTER_VARIANT}"
    )

    lines.append("")

    lines.append(
        "--------------- SUMMARY --------------"
    )

    lines.append(
        f"TAILS        : {len(tails)}"
    )

    lines.append(
        f"ALIASES      : {len(aliases)}"
    )

    lines.append(
        f"CHECKED      : {len(results)}"
    )

    lines.append(
        f"HTTP 200     : {working}"
    )

    lines.append(
        f"FAILED       : {failed}"
    )

    lines.append("")

    lines.append(
        "------------- CHANNELS ---------------"
    )

    for item in results:

        alias = item["alias"]
        status = item["http"]

        if item["ok"]:

            state = "OK"

        else:

            state = "FAIL"

        lines.append(
            f"{alias:<30} "
            f"{str(status):<5} "
            f"{state}"
        )

    lines.append("")

    lines.append(
        "--------------- OUTPUT ---------------"
    )

    lines.append(
        f"JSON         : {OUT_JSON}"
    )

    lines.append(
        f"PLAYLIST     : {OUT_M3U}"
    )

    lines.append(
        f"SKALA        : {OUT_SKALA}"
    )

    lines.append(
        f"LOG          : {OUT_LOG}"
    )

    lines.append("")

    lines.append(
        "========================================"
    )

    lines.append(
        "             SKALA COMPLETE"
    )

    lines.append(
        "========================================"
    )

    return (
        "\n".join(lines)
        + "\n"
    )


# ============================================================
#                       SAVE JSON
# ============================================================

def save_json(
    filename,
    data
):

    Path(filename).write_text(

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
        "========================================"
    )

    logger.info(
        "       START ZABAVA NGENIX SCAN"
    )

    logger.info(
        "========================================"
    )

    logger.info(
        f"NGENIX CDN: {BASE_CDN}"
    )

    logger.info(
        f"MASTER VARIANT: {MASTER_VARIANT}"
    )

    # ========================================================
    # 1. Получаем tails DmitryTV
    # ========================================================

    source_data, tails = load_source_json(
        logger
    )

    # ========================================================
    # 2. Извлекаем CH_* aliases
    # ========================================================

    aliases, alias_sources = extract_aliases(
        tails,
        logger
    )

    # ========================================================
    # 3. Для каждого alias создаём
    #    одну NGENIX variant.m3u8 ссылку
    # ========================================================

    channels = generate_ngenix_list(
        aliases,
        alias_sources,
        logger
    )

    # ========================================================
    # 4. Проверяем каждую ссылку NGENIX
    # ========================================================

    results = check_ngenix(
        channels,
        logger
    )

    # ========================================================
    # 5. Формируем полный итоговый JSON
    # ========================================================

    final_json = build_final_json(
        source_data,
        tails,
        aliases,
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
    # 7. SKALA REPORT
    # ========================================================

    skala = build_skala(
        tails,
        aliases,
        results
    )

    Path(
        OUT_SKALA
    ).write_text(
        skala,
        encoding="utf-8"
    )

    # ========================================================
    # 8. FINAL LOGGER RESULT
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
        "========================================"
    )

    logger.info(
        "              FINAL RESULT"
    )

    logger.info(
        "========================================"
    )

    logger.info(
        f"DMITRYTV TAILS : {len(tails)}"
    )

    logger.info(
        f"ALIASES        : {len(aliases)}"
    )

    logger.info(
        f"NGENIX CHECKED  : {len(results)}"
    )

    logger.info(
        f"NGENIX HTTP 200 : {working}"
    )

    logger.info(
        f"FAILED          : {failed}"
    )

    logger.info(
        f"JSON            : {OUT_JSON}"
    )

    logger.info(
        f"M3U             : {OUT_M3U}"
    )

    logger.info(
        f"SKALA           : {OUT_SKALA}"
    )

    logger.info(
        f"LOG             : {OUT_LOG}"
    )

    logger.info(
        "========================================"
    )

    logger.info(
        "=== NGENIX SCAN COMPLETE ==="
    )


# ============================================================
#                       ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()