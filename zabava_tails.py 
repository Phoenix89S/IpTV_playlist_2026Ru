#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import requests
from datetime import datetime, timezone


PLAYLIST_URL = (
    "http://dmitry-tv.ddns.net/iptv/freesat/gtmedia/ZABAVA/custom_url.m3u"
)

OUTPUT_TXT = "zabava_tails.txt"
OUTPUT_JSON = "zabava_tails.json"
OUTPUT_LOG = "zabava_scan.log"

TIMEOUT = 20


class ScalaLogger:

    def __init__(self, filename):
        self.filename = filename

        with open(filename, "w", encoding="utf-8") as f:
            pass

    def log(self, level, message):
        now = datetime.now(timezone.utc).astimezone()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f%z")

        line = f"{timestamp} [{level:<7}] {message}"

        print(line)

        with open(self.filename, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def info(self, message):
        self.log("INFO", message)

    def found(self, message):
        self.log("FOUND", message)

    def warn(self, message):
        self.log("WARN", message)

    def error(self, message):
        self.log("ERROR", message)


def download_playlist(logger):

    logger.info("========================================")
    logger.info("       START ZABAVA TAIL SCAN")
    logger.info("========================================")

    logger.info(f"SOURCE : {PLAYLIST_URL}")

    response = requests.get(
        PLAYLIST_URL,
        timeout=TIMEOUT,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    logger.info(
        f"DOWNLOAD OK : HTTP {response.status_code}"
    )

    logger.info(
        f"PLAYLIST SIZE : {len(response.content)} bytes"
    )

    return response.text


def extract_urls(playlist):

    urls = []

    for line in playlist.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if line.startswith(("http://", "https://")):
            urls.append(line)

    return urls


def extract_tail(url):

    """
    Из любой ссылки извлекается только хвост,
    начиная с /hls/.

    Пример:

    https://zabava-htlive.cdn.ngenix.net/hls/CH_TVC/variant.m3u8

    превращается в:

    /hls/CH_TVC/variant.m3u8
    """

    match = re.search(r"(/hls/.*)", url)

    if not match:
        return None

    tail = match.group(1)

    # Удаляем query-параметры
    tail = tail.split("?", 1)[0]

    # Удаляем fragment
    tail = tail.split("#", 1)[0]

    return tail


def scan(playlist, logger):

    urls = extract_urls(playlist)

    logger.info(f"URLS FOUND : {len(urls)}")

    tails = []
    seen = set()

    for index, url in enumerate(urls, start=1):

        tail = extract_tail(url)

        if not tail:
            continue

        if tail in seen:

            logger.info(
                f"DUPLICATE [{index}] : {tail}"
            )

            continue

        seen.add(tail)
        tails.append(tail)

        logger.found(
            f"[{len(tails):05d}] {tail}"
        )

    return urls, tails


def save_txt(tails, logger):

    with open(
        OUTPUT_TXT,
        "w",
        encoding="utf-8"
    ) as f:

        for tail in tails:
            f.write(tail + "\n")

    logger.info(
        f"TXT SAVED : {OUTPUT_TXT}"
    )


def save_json(tails, urls, logger):

    data = {
        "scanner": "zabava_tails.py",
        "version": "1.0",
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),

        "source": PLAYLIST_URL,

        "statistics": {
            "urls_found": len(urls),
            "tails_found": len(tails),
        },

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


def main():

    logger = ScalaLogger(OUTPUT_LOG)

    started = datetime.now(timezone.utc)

    try:

        playlist = download_playlist(logger)

        urls, tails = scan(
            playlist,
            logger
        )

        save_txt(
            tails,
            logger
        )

        save_json(
            tails,
            urls,
            logger
        )

        duration = (
            datetime.now(timezone.utc)
            - started
        ).total_seconds()

        logger.info("========================================")
        logger.info("             SCAN RESULT")
        logger.info("========================================")

        logger.info(
            f"URLS        : {len(urls)}"
        )

        logger.info(
            f"UNIQUE TAILS: {len(tails)}"
        )

        logger.info(
            f"DURATION    : {duration:.3f}s"
        )

        logger.info("========================================")
        logger.info("       ZABAVA TAIL SCAN COMPLETE")
        logger.info("========================================")

    except Exception as e:

        logger.error(
            f"FATAL ERROR : {type(e).__name__}: {e}"
        )

        raise


if __name__ == "__main__":
    main()