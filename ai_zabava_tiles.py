#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DmitryTV → Ngenix scanner (combined v5.1)

1. Скачивает живой M3U с DmitryTV.
2. Извлекает CH_* (порядок сохраняется, без дедупликации).
3. Строит URL: https://zabava-htlive.cdn.ngenix.net/hls/{alias}/variant.m3u8
4. Параллельно проверяет каждый URL (aiohttp).
5. Пишет (имена как в «еже»):
   - playlist_ngenix_working.m3u   — только рабочие
   - playlist_ngenix_data.json     — полный отчёт
   - skala_ngenix_report.txt       — человекочитаемый лог/отчёт
   - scan_ngenix_process.log       — технический лог

Дополнительно (из «ужа»):
   - zabava_tails.txt
   - zabava_tails.json
"""

import os
import re
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

import aiohttp
import requests


# ============================================================
#                         НАСТРОЙКИ
# ============================================================

PLAYLIST_URL = (
    "http://dmitry-tv.ddns.net/iptv/freesat/gtmedia/ZABAVA/custom_url.m3u"
)

# Итоговые имена — как в «еже», не меняем
OUTPUT_M3U   = "playlist_ngenix_working.m3u"
OUTPUT_JSON  = "playlist_ngenix_data.json"
OUTPUT_SKALA = "skala_ngenix_report.txt"
LOG_FILE     = "scan_ngenix_process.log"

# Хвосты (из «ужа»)
OUTPUT_TAILS_TXT  = "zabava_tails.txt"
OUTPUT_TAILS_JSON = "zabava_tails.json"

BASE_URL_TEMPLATE = (
    "https://zabava-htlive.cdn.ngenix.net/hls/{alias}/variant.m3u8"
)

USER_AGENT = "HlsWinkPlayer"
HTTP_TIMEOUT = 8
DOWNLOAD_TIMEOUT = 20
CONCURRENCY_LIMIT = 30


# ============================================================
#                       ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


def now_local() -> str:
    return datetime.now().astimezone().isoformat()


def skala(message: str, level: str = "INFO") -> None:
    line = f"{now_local()} [{level:<7}] {message}"
    print(line)
    with open(OUTPUT_SKALA, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ============================================================
#             СКАЧИВАНИЕ ПЛЕЙЛИСТА (из «ужа»)
# ============================================================

def download_playlist() -> str:
    skala("========================================")
    skala("       START DMITRYTV → NGENIX")
    skala("========================================")
    skala(f"SOURCE : {PLAYLIST_URL}")

    response = requests.get(
        PLAYLIST_URL,
        timeout=DOWNLOAD_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    skala(f"DOWNLOAD OK : HTTP {response.status_code}")
    skala(f"PLAYLIST SIZE : {len(response.content)} bytes")

    return response.text


# ============================================================
#             ИЗВЛЕЧЕНИЕ URL / TAIL / CH_* (из «ужа»)
# ============================================================

def extract_urls(playlist: str) -> List[str]:
    urls = []
    for line in playlist.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("http://", "https://")):
            urls.append(line)
    return urls


def extract_tail(url: str) -> Optional[str]:
    """
    Из любой ссылки извлекается только хвост, начиная с /hls/.
    Пример:
      https://zabava-htlive.cdn.ngenix.net/hls/CH_TVC/variant.m3u8
    → /hls/CH_TVC/variant.m3u8
    """
    match = re.search(r"(/hls/.*)", url)
    if not match:
        return None
    tail = match.group(1)
    tail = tail.split("?", 1)[0]
    tail = tail.split("#", 1)[0]
    return tail


def extract_channel_ids(
    playlist: str
) -> Tuple[List[str], List[str], List[Dict]]:

    """
    Извлекает:
      - все URL из M3U;
      - tails начиная с /hls/;
      - CH_* непосредственно из URL.

    ВАЖНО:
      - порядок сохраняется;
      - дубликаты НЕ удаляются;
      - set() НЕ используется;
      - seen НЕ используется;
      - один исходный CH_* = одна запись для Ngenix.
    """

    urls = extract_urls(playlist)

    tails: List[str] = []
    entries: List[Dict] = []

    for line_number, url in enumerate(urls, start=1):

        # ----------------------------------------------------
        # TAIL
        # ----------------------------------------------------

        tail = extract_tail(url)

        if tail:
            tails.append(tail)

        # ----------------------------------------------------
        # CH_* ИЩЕМ ВО ВСЕЙ URL
        # ----------------------------------------------------

        matches = re.findall(
            r"CH_[A-Za-z0-9_-]+",
            url
        )

        if not matches:
            continue

        # Обычно здесь будет одно CH_*.
        # Если в URL несколько CH_*, каждое сохраняем.
        for alias in matches:

            entries.append({
                "source_index": len(entries) + 1,
                "source_line": line_number,
                "source_path": url,
                "alias": alias,
            })

            skala(
                f"[CH {len(entries):05d}] {alias}",
                "FOUND",
            )

    return urls, tails, entries


# ============================================================
#                  ПОСТРОЕНИЕ NGENIX URL
# ============================================================

def build_ngenix_url(alias: str) -> str:
    return BASE_URL_TEMPLATE.format(alias=alias)


# ============================================================
#                  ПАРСИНГ M3U8 (лёгкий)
# ============================================================

def parse_m3u8(text: str, alias: str) -> Dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    stream_inf = []
    media = []
    urls = []

    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF:"):
            stream_inf.append(line)
        elif line.startswith("#EXT-X-MEDIA:"):
            media.append(line)
        elif not line.startswith("#") and (
            line.startswith("http://")
            or line.startswith("https://")
            or line.endswith(".m3u8")
            or line.endswith(".ts")
        ):
            urls.append(line)

    title = alias[3:] if alias.startswith("CH_") else alias

    return {
        "stream_inf": stream_inf,
        "stream_inf_count": len(stream_inf),
        "media": media,
        "media_count": len(media),
        "urls": urls,
        "url_count": len(urls),
        "title": title,
        "raw_m3u8": text,
    }


# ============================================================
#                ПРОВЕРКА ОДНОГО NGENIX URL
# ============================================================

async def check_ngenix(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    entry: Dict,
) -> Dict:

    alias = entry["alias"]
    url = build_ngenix_url(alias)

    result = {
        "source_index": entry["source_index"],
        "source_line": entry["source_line"],
        "source_path": entry["source_path"],
        "alias": alias,
        "ngenix_url": url,
        "http_status": None,
        "working": False,
        "content_type": "",
        "content_length": 0,
        "metadata": {
            "tvg_id": alias,
            "tvg_name": alias,
            "title": alias[3:] if alias.startswith("CH_") else alias,
        },
        "stream_inf_count": 0,
        "media_count": 0,
        "url_count": 0,
        "raw_m3u8": "",
        "error": None,
    }

    async with semaphore:
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "*/*",
                },
            ) as response:

                result["http_status"] = response.status
                result["content_type"] = response.headers.get("Content-Type", "")

                if response.status != 200:
                    result["error"] = f"HTTP {response.status}"
                    skala(
                        f"{entry['source_index']:04d} {alias} → HTTP {response.status} → FAIL",
                        "WARN",
                    )
                    return result

                text = await response.text(errors="replace")
                result["content_length"] = len(text)

                if not text.lstrip().startswith("#EXTM3U"):
                    result["error"] = "HTTP 200, но нет #EXTM3U"
                    skala(
                        f"{entry['source_index']:04d} {alias} → HTTP 200 → NOT M3U8",
                        "WARN",
                    )
                    return result

                parsed = parse_m3u8(text, alias)

                result["working"] = True
                result["metadata"]["title"] = parsed["title"]
                result["stream_inf_count"] = parsed["stream_inf_count"]
                result["media_count"] = parsed["media_count"]
                result["url_count"] = parsed["url_count"]
                result["raw_m3u8"] = text

                skala(
                    f"{entry['source_index']:04d} {alias} → HTTP 200 → OK → {parsed['title']}",
                    "FOUND",
                )
                return result

        except asyncio.TimeoutError:
            result["error"] = "TIMEOUT"
            skala(f"{entry['source_index']:04d} {alias} → TIMEOUT", "WARN")
            return result

        except aiohttp.ClientError as e:
            result["error"] = f"{type(e).__name__}: {e}"
            skala(f"{entry['source_index']:04d} {alias} → HTTP ERROR → {e}", "ERROR")
            return result

        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            skala(f"{entry['source_index']:04d} {alias} → ERROR → {e}", "ERROR")
            return result


# ============================================================
#                   СКАНИРОВАНИЕ ВСЕХ
# ============================================================

async def scan_all(entries: List[Dict]) -> List[Dict]:
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT)
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [
            check_ngenix(session, semaphore, entry)
            for entry in entries
        ]
        results = await asyncio.gather(*tasks)

    return results


# ============================================================
#                    СОХРАНЕНИЕ ХВОСТОВ (из «ужа»)
# ============================================================

def save_tails(urls: List[str], tails: List[str]) -> None:
    with open(OUTPUT_TAILS_TXT, "w", encoding="utf-8") as f:
        for tail in tails:
            f.write(tail + "\n")

    data = {
        "scanner": "dmitrytv_to_ngenix",
        "version": "5.1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": PLAYLIST_URL,
        "statistics": {
            "urls_found": len(urls),
            "tails_found": len(tails),
        },
        "tails": tails,
    }

    with open(OUTPUT_TAILS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    skala(f"TAILS TXT  : {OUTPUT_TAILS_TXT} ({len(tails)})")
    skala(f"TAILS JSON : {OUTPUT_TAILS_JSON}")


# ============================================================
#                         JSON (результаты Ngenix)
# ============================================================

def build_json(entries: List[Dict], results: List[Dict]) -> Dict:
    working = [r for r in results if r["working"]]
    failed  = [r for r in results if not r["working"]]

    return {
        "scanner": "dmitrytv_to_ngenix",
        "version": "5.1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": {
            "name": "DmitryTV",
            "url": PLAYLIST_URL,
            "entries_total": len(entries),
        },
        "ngenix": {
            "base": "https://zabava-htlive.cdn.ngenix.net",
            "template": BASE_URL_TEMPLATE,
        },
        "statistics": {
            "source_ch_entries": len(entries),
            "ngenix_urls_built": len(entries),
            "checked": len(results),
            "working": len(working),
            "failed": len(failed),
        },
        "channels": results,
    }


def write_json(entries: List[Dict], results: List[Dict]) -> None:
    data = build_json(entries, results)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
#                         M3U
# ============================================================

def write_m3u(results: List[Dict]) -> int:
    working = [r for r in results if r["working"]]

    with open(OUTPUT_M3U, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")

        for ch in working:
            alias = ch["alias"]
            meta  = ch["metadata"]
            title = meta.get("title") or (alias[3:] if alias.startswith("CH_") else alias)

            f.write(
                f'#EXTINF:-1 tvg-id="{alias}" tvg-name="{title}",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(ch["ngenix_url"] + "\n")

    return len(working)


# ============================================================
#                    SKALA ИТОГОВЫЙ ОТЧЁТ
# ============================================================

def append_final_skala_report(
    entries: List[Dict],
    results: List[Dict],
    m3u_count: int,
) -> None:

    working = [r for r in results if r["working"]]
    failed  = [r for r in results if not r["working"]]

    with open(OUTPUT_SKALA, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n")
        f.write("============================================================\n")
        f.write("                 FINAL SKALA REPORT\n")
        f.write("============================================================\n")
        f.write(f"TIME: {now_local()}\n")
        f.write(f"SOURCE: {PLAYLIST_URL}\n")
        f.write("NGENIX: https://zabava-htlive.cdn.ngenix.net\n")
        f.write("\n")
        f.write("-------------------- СТАТИСТИКА --------------------\n")
        f.write(f"CH ENTRIES IN SOURCE   : {len(entries)}\n")
        f.write(f"NGENIX URLS BUILT      : {len(entries)}\n")
        f.write(f"CHECKED                : {len(results)}\n")
        f.write(f"HTTP 200 / WORKING     : {len(working)}\n")
        f.write(f"FAILED                 : {len(failed)}\n")
        f.write(f"M3U ENTRIES            : {m3u_count}\n")

        if len(entries) == len(results):
            f.write("ONE-TO-ONE CHECK       : OK\n")
        else:
            f.write("ONE-TO-ONE CHECK       : ERROR\n")

        f.write("\n")
        f.write("-------------------- РЕЗУЛЬТАТЫ --------------------\n")

        for r in results:
            status = "OK" if r["working"] else "FAIL"
            title  = r["metadata"].get("title", "")
            f.write(
                f"[{r['source_index']:04d}] {r['alias']} → {status} → "
                f"HTTP {r['http_status']} → {title}\n"
            )
            f.write(f"  NGENIX: {r['ngenix_url']}\n")
            if r["error"]:
                f.write(f"  ERROR : {r['error']}\n")

        f.write("\n")
        f.write("-------------------- ФАЙЛЫ --------------------\n")
        f.write(f"M3U       : {OUTPUT_M3U}\n")
        f.write(f"JSON      : {OUTPUT_JSON}\n")
        f.write(f"SKALA     : {OUTPUT_SKALA}\n")
        f.write(f"LOG       : {LOG_FILE}\n")
        f.write(f"TAILS TXT : {OUTPUT_TAILS_TXT}\n")
        f.write(f"TAILS JSON: {OUTPUT_TAILS_JSON}\n")
        f.write("============================================================\n")


# ============================================================
#                           MAIN
# ============================================================

def main():
    # Новый запуск — чистый SKALA
    with open(OUTPUT_SKALA, "w", encoding="utf-8") as f:
        f.write("SKALA NGENIX SCAN\n")
        f.write(f"START: {now_local()}\n\n")

    started = datetime.now(timezone.utc)

    try:
        # ----------------------------------------------------
        # 1. Скачиваем живой M3U
        # ----------------------------------------------------
        playlist = download_playlist()

        # ----------------------------------------------------
        # 2. Парсим URLs / tails / CH_*
        # ----------------------------------------------------
        urls, tails, entries = extract_channel_ids(playlist)

        skala(f"URLS FOUND     : {len(urls)}")
        skala(f"TAILS FOUND    : {len(tails)}")
        skala(f"CH ENTRIES     : {len(entries)}")

        if not entries:
            skala("Нет CH_* для проверки — выход", "ERROR")
            return

        # ----------------------------------------------------
        # 3. Сохраняем хвосты (из «ужа»)
        # ----------------------------------------------------
        save_tails(urls, tails)

        # ----------------------------------------------------
        # 4. Строим Ngenix URL (one-to-one)
        # ----------------------------------------------------
        skala("BUILD NGENIX URLS: ONE SOURCE CH = ONE NGENIX URL")
        for entry in entries:
            url = build_ngenix_url(entry["alias"])
            skala(f"[{entry['source_index']:04d}] {entry['alias']} → {url}")

        skala(f"NGENIX URLS BUILT: {len(entries)}")

        # ----------------------------------------------------
        # 5. Скан Ngenix
        # ----------------------------------------------------
        skala("START NGENIX SCAN")
        results = asyncio.run(scan_all(entries))

        if len(results) != len(entries):
            skala("CRITICAL: SOURCE/RESULT COUNT MISMATCH", "ERROR")
            raise RuntimeError(
                "Количество результатов Ngenix ≠ количеству исходных CH."
            )

        skala(
            f"ONE-TO-ONE CHECK: {len(entries)} → {len(results)} → OK",
            "FOUND",
        )

        working = [r for r in results if r["working"]]
        failed  = [r for r in results if not r["working"]]

        skala("============================================================")
        skala(f"SCAN COMPLETE: {len(working)}/{len(entries)} WORKING", "FOUND")
        skala(f"FAILED: {len(failed)}")

        # ----------------------------------------------------
        # 6. JSON
        # ----------------------------------------------------
        write_json(entries, results)
        skala(f"JSON READY: {OUTPUT_JSON}", "FOUND")

        # ----------------------------------------------------
        # 7. M3U
        # ----------------------------------------------------
        m3u_count = write_m3u(results)
        skala(f"M3U READY: {OUTPUT_M3U} ({m3u_count} working entries)", "FOUND")

        # ----------------------------------------------------
        # 8. Финальный отчёт
        # ----------------------------------------------------
        append_final_skala_report(entries, results, m3u_count)
        skala(f"SKALA REPORT READY: {OUTPUT_SKALA}", "FOUND")

        # ----------------------------------------------------
        # 9. Финал
        # ----------------------------------------------------
        duration = (datetime.now(timezone.utc) - started).total_seconds()

        skala("============================================================")
        skala("FINAL RESULT")
        skala(f"SOURCE CH         : {len(entries)}")
        skala(f"NGENIX URLS       : {len(entries)}")
        skala(f"CHECKED           : {len(results)}")
        skala(f"WORKING HTTP 200  : {len(working)}", "FOUND")
        skala(f"FAILED            : {len(failed)}")
        skala(f"M3U ENTRIES       : {m3u_count}")
        skala(f"DURATION          : {duration:.3f}s")
        skala("COMPLETE")

    except Exception as e:
        skala(f"FATAL ERROR : {type(e).__name__}: {e}", "ERROR")
        raise


if __name__ == "__main__":
    main()