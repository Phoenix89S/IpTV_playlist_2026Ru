#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import requests
from datetime import datetime, timezone
from pathlib import Path

# === Исходный JSON со списком хвостов ===
JSON_INPUT = "_scanner_zaba_1788021849270.txt"

# === CDN Zabava ===
BASE_CDN = "https://zabava-htlive.cdn.ngenix.net"

# === Мастер‑пример для структуры ===
MASTER_VARIANT = "/hls/CH_TVC/variant.m3u8"

# === Выходные файлы ===
OUT_JSON_NEW = "_scanner_zaba_2.json"
OUT_PLAYLIST_JSON = "zabava_tiles2.json"
OUT_SKALA_TXT = "zabava_skala2.txt"
OUT_M3U = "zabava_tiles2.m3u"
OUT_LOG = "zabava_scan2.log"


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
#                     ЗАГРУЗКА ХВОСТОВ
# ============================================================

def load_tails(logger):
    logger.info(f"LOAD JSON: {JSON_INPUT}")
    with open(JSON_INPUT, "r", encoding="utf-8") as f:
        data = json.load(f)
    tails = data["tails"]
    logger.info(f"TAILS LOADED: {len(tails)}")
    return tails


# ============================================================
#                     ГЕНЕРАЦИЯ ССЫЛОК ПО ШАБЛОНУ
# ============================================================

def generate_links_from_tails(tails, logger):
    """
    Пример:
    tail = "/hls/zabava/CH_TVC/mono.m3u8"

    Мы должны получить:
    https://zabava-htlive.cdn.ngenix.net/hls/zabava/CH_TVC/mono.m3u8
    """

    links = []

    for tail in tails:
        full_url = BASE_CDN + tail
        links.append(full_url)
        logger.found(f"GENERATED: {full_url}")

    return links


# ============================================================
#                     HTTP ПРОВЕРКА
# ============================================================

def check_http_all(links, logger):
    results = []

    for url in links:
        try:
            r = requests.head(url, timeout=10)
            status = r.status_code
        except Exception:
            status = None

        ok = (status == 200)

        results.append({
            "url": url,
            "tail": url.replace(BASE_CDN, ""),
            "http": status,
            "ok": ok,
        })

        logger.info(f"CHECK {url} -> {status} {'OK' if ok else 'FAIL'}")

    return results


# ============================================================
#                     СОЗДАНИЕ JSON v2
# ============================================================

def build_new_json(results):
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
            name = r["tail"].split("/")[-1]
            channels.append({
                "name": name,
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
    logger = ScalaLogger(OUT_LOG)
    logger.info("=== START ZABAVA FULL CDN CHECK ===")

    tails = load_tails(logger)
    links = generate_links_from_tails(tails, logger)
    results = check_http_all(links, logger)

    # Новый JSON v2
    new_json = build_new_json(results)
    Path(OUT_JSON_NEW).write_text(json.dumps(new_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # JSON-плейлист
    playlist_json = build_playlist_json(results)
    Path(OUT_PLAYLIST_JSON).write_text(json.dumps(playlist_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # M3U
    Path(OUT_M3U).write_text(build_m3u(results), encoding="utf-8")

    # SKALA
    Path(OUT_SKALA_TXT).write_text(build_skala_txt(results), encoding="utf-8")

    logger.info("=== COMPLETE ===")


if __name__ == "__main__":
    main()