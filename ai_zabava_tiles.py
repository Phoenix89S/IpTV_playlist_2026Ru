#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIG
# ============================================================

PLAYLIST_URL = (
    "http://dmitry-tv.ddns.net/iptv/freesat/gtmedia/ZABAVA/custom_url.m3u"
)

NGENIX_BASE_URL = (
    "https://zabava-htlive.cdn.ngenix.net"
)

OUTPUT_TXT = "zabava_tails.txt"
OUTPUT_JSON = "zabava_tails.json"
OUTPUT_RESULTS_TXT = "zabava_ngenix_results.txt"
OUTPUT_RESULTS_JSON = "zabava_ngenix_results.json"
OUTPUT_M3U = "zabava_working.m3u"          # <-- итоговый рабочий плейлист
OUTPUT_LOG = "zabava_scan.log"

TIMEOUT = 20
MAX_WORKERS = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

# ============================================================
# LOGGER
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
# DOWNLOAD PLAYLIST
# ============================================================

def download_playlist(logger):

    logger.info("========================================")
    logger.info("       START ZABAVA NGNIX SCAN")
    logger.info("========================================")

    logger.info(
        f"SOURCE : {PLAYLIST_URL}"
    )

    response = requests.get(
        PLAYLIST_URL,
        timeout=TIMEOUT,
        headers=HEADERS
    )

    response.raise_for_status()

    logger.info(
        f"DOWNLOAD OK : HTTP {response.status_code}"
    )

    logger.info(
        f"PLAYLIST SIZE : {len(response.content)} bytes"
    )

    return response.text

# ============================================================
# EXTRACT URLS
# ============================================================

def extract_urls(playlist):

    urls = []

    for line in playlist.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if line.startswith(
            ("http://", "https://")
        ):

            urls.append(line)

    return urls

# ============================================================
# EXTRACT TAIL
# ============================================================

def extract_tail(url):

    match = re.search(
        r"(/hls/.*)",
        url
    )

    if not match:
        return None

    tail = match.group(1)

    tail = tail.split(
        "?",
        1
    )[0]

    tail = tail.split(
        "#",
        1
    )[0]

    return tail

# ============================================================
# EXTRACT CH_* FROM PLAYLIST
# ============================================================

def extract_channel_ids(
    playlist,
    logger
):

    """
    Получаем CH_* из исходного M3U.

    ВАЖНО:

    НИКАКОЙ ДЕДУПЛИКАЦИИ.

    НИКАКОГО set().

    НИКАКОГО seen.

    НИКАКОГО удаления повторов.

    Сохраняется исходный порядок.
    """

    urls = extract_urls(
        playlist
    )

    channel_ids = []

    for url in urls:

        tail = extract_tail(
            url
        )

        if not tail:
            continue

        match = re.search(
            r"/hls/([^/?#]+)",
            tail
        )

        if not match:
            continue

        channel_id = match.group(1)

        if not channel_id.startswith(
            "CH_"
        ):
            continue

        channel_ids.append(
            channel_id
        )

        logger.found(
            f"[CH {len(channel_ids):05d}] "
            f"{channel_id}"
        )

    return urls, channel_ids

# ============================================================
# BUILD NGENIX URL
# ============================================================

def build_ngenix_url(channel_id):

    return (
        f"{NGENIX_BASE_URL}"
        f"/hls/{channel_id}/variant.m3u8"
    )

# ============================================================
# VALIDATE M3U8
# ============================================================

def validate_m3u8(content):

    if not content:
        return False

    text = content.lstrip()

    return text.startswith(
        "#EXTM3U"
    )

# ============================================================
# CHECK ONE URL
# ============================================================

def check_ngenix(
    index,
    channel_id
):

    url = build_ngenix_url(
        channel_id
    )

    result = {
        "index": index,
        "channel_id": channel_id,
        "url": url,
        "http_status": None,
        "content_type": None,
        "content_length": None,
        "m3u8": False,
        "status": "ERROR",
        "error": None,
    }

    try:

        response = requests.get(
            url,
            timeout=TIMEOUT,
            headers=HEADERS
        )

        result["http_status"] = (
            response.status_code
        )

        result["content_type"] = (
            response.headers.get(
                "Content-Type"
            )
        )

        result["content_length"] = (
            len(response.content)
        )

        if response.status_code == 200:

            result["m3u8"] = (
                validate_m3u8(
                    response.text
                )
            )

            if result["m3u8"]:

                result["status"] = "FOUND"

            else:

                result["status"] = (
                    "HTTP_200_NOT_M3U8"
                )

        else:

            result["status"] = (
                "HTTP_ERROR"
            )

    except requests.Timeout:

        result["status"] = "TIMEOUT"

        result["error"] = (
            "Request timeout"
        )

    except requests.RequestException as e:

        result["status"] = "REQUEST_ERROR"

        result["error"] = str(e)

    except Exception as e:

        result["status"] = "ERROR"

        result["error"] = str(e)

    return result

# ============================================================
# SAVE TAILS
# ============================================================

def save_tails(
    urls,
    logger
):

    tails = []

    with open(
        OUTPUT_TXT,
        "w",
        encoding="utf-8"
    ) as f:

        for url in urls:

            tail = extract_tail(
                url
            )

            if tail is None:
                continue

            # НИКАКОГО DEDUPLICATE
            tails.append(tail)

            f.write(
                tail + "\n"
            )

    logger.info(
        f"TAILS SAVED : {len(tails)}"
    )

    logger.info(
        f"TXT SAVED : {OUTPUT_TXT}"
    )

    return tails

# ============================================================
# SAVE PARSING JSON
# ============================================================

def save_parsing_json(
    urls,
    tails,
    channel_ids,
    logger
):

    data = {
        "scanner": "zabava_tails.py",
        "version": "2.1",

        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),

        "source": PLAYLIST_URL,

        "statistics": {
            "urls_found": len(urls),
            "tails_found": len(tails),
            "channel_ids_found": len(
                channel_ids
            ),
        },

        "channel_ids": channel_ids,

        "tails": tails,
    }

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    logger.info(
        f"JSON SAVED : {OUTPUT_JSON}"
    )

# ============================================================
# NGENIX SCAN
# ============================================================

def scan_ngenix(
    channel_ids,
    logger
):

    total = len(
        channel_ids
    )

    logger.info("========================================")
    logger.info("        START NGENIX INTERROGATION")
    logger.info("========================================")

    logger.info(
        f"CHANNEL IDS : {total}"
    )

    logger.info(
        f"EXPECTED CHECKS : {total}"
    )

    logger.info(
        f"WORKERS : {MAX_WORKERS}"
    )

    # Результаты заранее создаются
    # размером ровно с исходный список.
    #
    # Поэтому порядок сохраняется.
    results = [
        None
    ] * total

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for index, channel_id in enumerate(
            channel_ids,
            start=1
        ):

            future = executor.submit(
                check_ngenix,
                index,
                channel_id
            )

            futures[future] = index

        completed = 0

        for future in as_completed(
            futures
        ):

            index = futures[
                future
            ]

            try:

                result = future.result()

                results[
                    index - 1
                ] = result

                completed += 1

                if result["status"] == "FOUND":

                    logger.found(
                        f"[{index:05d}/{total:05d}] "
                        f"{result['channel_id']} "
                        f"-> FOUND "
                        f"HTTP={result['http_status']} "
                        f"-> {result['url']}"
                    )

                else:

                    logger.info(
                        f"[{index:05d}/{total:05d}] "
                        f"{result['channel_id']} "
                        f"-> {result['status']} "
                        f"HTTP={result['http_status']}"
                    )

            except Exception as e:

                channel_id = (
                    channel_ids[
                        index - 1
                    ]
                )

                results[
                    index - 1
                ] = {
                    "index": index,
                    "channel_id": channel_id,
                    "url": build_ngenix_url(
                        channel_id
                    ),
                    "http_status": None,
                    "content_type": None,
                    "content_length": None,
                    "m3u8": False,
                    "status": "WORKER_ERROR",
                    "error": str(e),
                }

                completed += 1

                logger.error(
                    f"[{index:05d}/{total:05d}] "
                    f"WORKER ERROR : {e}"
                )

    return results

# ============================================================
# SAVE RESULTS TXT
# ============================================================

def save_results_txt(
    results,
    logger
):

    with open(
        OUTPUT_RESULTS_TXT,
        "w",
        encoding="utf-8"
    ) as f:

        for result in results:

            line = (
                f"{result['index']:05d} | "
                f"{result['channel_id']} | "
                f"{result['status']} | "
                f"HTTP={result['http_status']} | "
                f"{result['url']}"
            )

            f.write(
                line + "\n"
            )

    logger.info(
        f"RESULT TXT SAVED : "
        f"{OUTPUT_RESULTS_TXT}"
    )

# ============================================================
# SAVE RESULTS JSON
# ============================================================

def save_results_json(
    results,
    logger
):

    found = sum(
        1
        for result in results
        if result["status"] == "FOUND"
    )

    data = {
        "scanner": "zabava_tails.py",
        "version": "2.1",

        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),

        "source": PLAYLIST_URL,

        "ngenix_base": NGENIX_BASE_URL,

        "statistics": {
            "total_checks": len(results),
            "found": found,
            "not_found_or_error": (
                len(results) - found
            ),
        },

        # ВАЖНО:
        # результаты находятся в том же порядке,
        # что и CH_* в исходном M3U.
        #
        # Повторы НЕ удаляются.
        "results": results,
    }

    with open(
        OUTPUT_RESULTS_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    logger.info(
        f"RESULT JSON SAVED : "
        f"{OUTPUT_RESULTS_JSON}"
    )

# ============================================================
# GENERATE WORKING M3U
# ============================================================

def generate_working_m3u(
    results,
    logger
):
    """
    Собираем итоговый рабочий M3U только из каналов
    со статусом FOUND (валидный #EXTM3U на Ngenix).

    Порядок сохраняется как в исходном плейлисте.
    Повторы НЕ удаляются (как и везде в скрипте).
    """

    found_count = 0

    with open(
        OUTPUT_M3U,
        "w",
        encoding="utf-8"
    ) as f:

        f.write("#EXTM3U\n")

        for result in results:

            if result["status"] != "FOUND":
                continue

            channel_id = result["channel_id"]
            url = result["url"]

            # Простая #EXTINF строка.
            # Имя = channel_id (можно потом обогатить tvg-name и т.п.)
            f.write(
                f'#EXTINF:-1 tvg-id="{channel_id}" '
                f'tvg-name="{channel_id}",{channel_id}\n'
            )
            f.write(url + "\n")

            found_count += 1

    logger.info(
        f"WORKING M3U SAVED : {OUTPUT_M3U}"
    )
    logger.info(
        f"WORKING CHANNELS  : {found_count}"
    )

    return found_count

# ============================================================
# MAIN
# ============================================================

def main():

    logger = ScalaLogger(
        OUTPUT_LOG
    )

    started = datetime.now(
        timezone.utc
    )

    try:

        # ----------------------------------------------------
        # 1. DOWNLOAD DMITRY-TV
        # ----------------------------------------------------

        playlist = download_playlist(
            logger
        )

        # ----------------------------------------------------
        # 2. PARSE
        # ----------------------------------------------------

        urls, channel_ids = (
            extract_channel_ids(
                playlist,
                logger
            )
        )

        logger.info(
            f"URLS FOUND : {len(urls)}"
        )

        logger.info(
            f"CHANNEL IDS FOUND : "
            f"{len(channel_ids)}"
        )

        # ----------------------------------------------------
        # 3. SAVE TAILS
        # ----------------------------------------------------

        tails = save_tails(
            urls,
            logger
        )

        # ----------------------------------------------------
        # 4. SAVE PARSING JSON
        # ----------------------------------------------------

        save_parsing_json(
            urls,
            tails,
            channel_ids,
            logger
        )

        # ----------------------------------------------------
        # 5. NGENIX
        # ----------------------------------------------------

        results = scan_ngenix(
            channel_ids,
            logger
        )

        # ----------------------------------------------------
        # 6. SAVE NGENIX RESULTS
        # ----------------------------------------------------

        save_results_txt(
            results,
            logger
        )

        save_results_json(
            results,
            logger
        )

        # ----------------------------------------------------
        # 7. GENERATE WORKING M3U  ← вот оно
        # ----------------------------------------------------

        working_count = generate_working_m3u(
            results,
            logger
        )

        # ----------------------------------------------------
        # 8. FINAL STATISTICS
        # ----------------------------------------------------

        found = sum(
            1
            for result in results
            if result["status"] == "FOUND"
        )

        valid_m3u8 = sum(
            1
            for result in results
            if result["m3u8"] is True
        )

        duration = (
            datetime.now(
                timezone.utc
            )
            - started
        ).total_seconds()

        logger.info("========================================")
        logger.info("             SCAN RESULT")
        logger.info("========================================")

        logger.info(
            f"URLS              : {len(urls)}"
        )

        logger.info(
            f"TAILS             : {len(tails)}"
        )

        logger.info(
            f"CHANNEL IDS       : "
            f"{len(channel_ids)}"
        )

        logger.info(
            f"GENERATED URLS    : "
            f"{len(channel_ids)}"
        )

        logger.info(
            f"TOTAL CHECKS      : "
            f"{len(results)}"
        )

        logger.info(
            f"FOUND             : {found}"
        )

        logger.info(
            f"VALID M3U8        : "
            f"{valid_m3u8}"
        )

        logger.info(
            f"WORKING M3U       : {working_count} channels → {OUTPUT_M3U}"
        )

        logger.info(
            f"NOT FOUND / ERROR : "
            f"{len(results) - found}"
        )

        logger.info(
            f"DURATION          : "
            f"{duration:.3f}s"
        )

        logger.info("========================================")
        logger.info(
            "       ZABAVA NGNIX SCAN COMPLETE"
        )
        logger.info("========================================")

    except Exception as e:

        logger.error(
            f"FATAL ERROR : "
            f"{type(e).__name__}: {e}"
        )

        raise

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()