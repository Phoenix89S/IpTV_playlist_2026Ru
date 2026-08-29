#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import requests
from datetime import datetime, timezone
from pathlib import Path

# === Исходный плейлист Дмитрий-TV ===
PLAYLIST_URL = "http://dmitry-tv.ddns.net/iptv/freesat/gtmedia/ZABAVA/custom_url.m3u"

# === CDN Zabava ===
BASE_CDN = "https://zabava-htlive.cdn.ngenix.net"

# === Выходные файлы ===
OUTPUT_TXT = "zabava_tails.txt"
OUTPUT_JSON = "zabava_tails.json"
OUTPUT_LOG = "zabava_scan.log"

OUT_JSON_NEW = "_scanner_zaba_2.json"
OUT_PLAYLIST_JSON = "zabava_tiles2.json"
OUT_SKALA_TXT = "zabava_skala2.txt"
OUT_M3U = "zabava_tiles2.m3u"

TIMEOUT = 20


# ============================================================
#                     ЛОГГЕР SKALA
# ============================================================

class ScalaLogger:
    def __init__(self, filename):
        self.filename = filename
        with open(filename, "w", encoding="utf-8"):
            pass

    def log(self, level, message):
        now = datetime.now(timezone.utc).astimezone()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        line = f"{timestamp} [{level:<7}] {message}"
        print(line)
        with open(self.filename, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def info(self, message): self.log("INFO", message)
    def found(self, message): self.log("FOUND", message)
    def warn(self, message): self.log("WARN", message)
    def error(self, message): self.log("ERROR", message)


# ============================================================
#                     СКАЧИВАЕМ ПЛЕЙЛИСТ
# ============================================================

def download_playlist(logger):
    logger.info("========================================")
    logger.info("       START ZABAVA TAIL SCAN")
    logger.info("========================================")
    logger.info(f"SOURCE : {PLAYLIST_URL}")

    response = requests.get(
        PLAYLIST_URL,
        timeout=TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()

    logger.info(f"DOWNLOAD OK : HTTP {response.status_code}")
    logger.info(f"PLAYLIST SIZE : {len(response.content)} bytes")

    return response.text


# ============================================================
#                     ИЗВЛЕЧЕНИЕ URL
# ============================================================

def extract_urls(playlist):
    urls = []
    for line in playlist.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("http://", "https://")):
            urls.append(line)
    return urls


# ============================================================
#                     ИЗВЛЕЧЕНИЕ ХВОСТОВ
# ============================================================

def extract_tail(url):
    match = re.search(r"(/hls/.*)", url)
    if not match:
        return None

    tail = match.group(1)
    tail = tail.split("?", 1)[0]
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
            logger.info(f"DUPLICATE [{index}] : {tail}")
            continue

        seen.add(tail)
        tails.append(tail)
        logger.found(f"[{len(tails):05d}] {tail}")

    return urls, tails


# ============================================================
#                     HTTP ПРОВЕРКА ВСЕХ ХВОСТОВ
# ============================================================

def check_http_all(tails, logger):
    results = []
    for tail in tails:
        full_url = BASE_CDN + tail
        try:
            r = requests.head(full_url, timeout=5)
            status = r.status_code
        except Exception:
            status = None

        ok = (status == 200)
        results.append({
            "tail": tail,
            "url": full_url,
            "http": status,
            "ok": ok,
        })

        logger.info(f"CHECK {tail} -> {status} {'OK' if ok else 'FAIL'}")

    return results


# ============================================================
#                     СОЗДАНИЕ НОВОГО JSON v2
# ============================================================

def build_new_scanner_json(results):
    tails_ok = [r["tail"] for r in results if r["ok"]]
    return {
        "scanner": "zabava_tails.py",
        "version": "2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": BASE_CDN,
        "tails": tails_ok,
    }


# ============================================================
#                     ПЛЕЙЛИСТЫ
# ============================================================

def build_playlist_json(results):
    channels = []
    for r in results:
        if r["ok"]:
            channels.append({
                "name": r["tail"].split("/")[-1],
                "url": r["url"],
            })
    return {"channels": channels}


def build_m3u(results):
    lines = ["#EXTM3U"]
    for r in results:
        if r["ok"]:
            name = r["tail"].split("/")[-1]
            lines.append(f"#EXTINF:-1,{name}")
            lines.append(r["url"])
    return "\n".join(lines)


def build_skala_txt(results):
    lines = ["SKALA REPORT: ZABAVA CDN\n"]
    ok_count = sum(1 for r in results if r["ok"])
    total = len(results)
    lines.append(f"OK: {ok_count}/{total}\n")

    for r in results:
        lines.append(f"{r['tail']} -> {r['http']} {'OK' if r['ok'] else 'FAIL'}")

    return "\n".join(lines)


# ============================================================
#                     MAIN
# ============================================================

def main():
    logger = ScalaLogger(OUTPUT_LOG)
    started = datetime.now(timezone.utc)

    try:
        playlist = download_playlist(logger)
        urls, tails = scan(playlist, logger)

        # Сохраняем исходные хвосты
        Path(OUTPUT_TXT).write_text("\n".join(tails), encoding="utf-8")
        Path(OUTPUT_JSON).write_text(json.dumps({
            "tails": tails,
            "urls": urls
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        # Проверяем хвосты на реальном CDN
        results = check_http_all(tails, logger)

        # Новый JSON v2
        new_json = build_new_scanner_json(results)
        Path(OUT_JSON_NEW).write_text(json.dumps(new_json, ensure_ascii=False, indent=2), encoding="utf-8")

        # JSON-плейлист
        playlist_json = build_playlist_json(results)
        Path(OUT_PLAYLIST_JSON).write_text(json.dumps(playlist_json, ensure_ascii=False, indent=2), encoding="utf-8")

        # M3U
        Path(OUT_M3U).write_text(build_m3u(results), encoding="utf-8")

        # SKALA
        Path(OUT_SKALA_TXT).write_text(build_skala_txt(results), encoding="utf-8")

        duration = (datetime.now(timezone.utc) - started).total_seconds()

        logger.info("========================================")
        logger.info("             SCAN RESULT")
        logger.info("========================================")
        logger.info(f"URLS        : {len(urls)}")
        logger.info(f"UNIQUE TAILS: {len(tails)}")
        logger.info(f"DURATION    : {duration:.3f}s")
        logger.info("========================================")
        logger.info("       ZABAVA TAIL SCAN COMPLETE")
        logger.info("========================================")

    except Exception as e:
        logger.error(f"FATAL ERROR : {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()