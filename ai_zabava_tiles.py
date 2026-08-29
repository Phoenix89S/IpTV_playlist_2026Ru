#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

import aiohttp


# ============================================================
#                         НАСТРОЙКИ
# ============================================================

INPUT_FILE = "dmitrytv_list.txt"

# NGENIX MASTER
BASE_URL_TEMPLATE = (
    "https://zabava-htlive.cdn.ngenix.net/hls/{alias}/variant.m3u8"
)

# Выходные файлы
OUTPUT_M3U = "playlist_ngenix.m3u"
OUTPUT_JSON = "playlist_data.json"
OUTPUT_SKALA = "skala_report.txt"
LOG_FILE = "scan_process.log"

# HTTP
USER_AGENT = "HlsWinkPlayer"
HTTP_TIMEOUT = 7
CONCURRENCY_LIMIT = 25


# ============================================================
#                       ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_FILE,
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ],
)


def now_local() -> str:
    return datetime.now().astimezone().isoformat()


def skala(
    message: str,
    level: str = "INFO",
) -> None:

    line = (
        f"{now_local()} "
        f"[{level:<7}] "
        f"{message}"
    )

    print(line)

    # ВАЖНО:
    # append, а не "w".
    # Накопленный SKALA не уничтожается.
    with open(
        OUTPUT_SKALA,
        "a",
        encoding="utf-8",
    ) as f:
        f.write(line + "\n")


# ============================================================
#             ИЗВЛЕЧЕНИЕ CH ИЗ DMITRYTV
# ============================================================

def extract_alias_from_line(
    line: str,
) -> Optional[str]:

    line = line.strip()

    if not line:
        return None

    if line.startswith("#"):
        return None

    # Берём CH_ и всё допустимое после него
    match = re.search(
        r"(CH_[A-Za-z0-9_-]+)",
        line,
    )

    if not match:
        return None

    return match.group(1)


def load_source_entries(
    filepath: str,
) -> List[Dict]:

    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"Не найден входной файл: {filepath}"
        )

    entries: List[Dict] = []

    with open(
        filepath,
        "r",
        encoding="utf-8",
    ) as f:

        for line_number, raw_line in enumerate(
            f,
            start=1,
        ):

            source_line = raw_line.strip()

            alias = extract_alias_from_line(
                source_line
            )

            if not alias:
                continue

            # ==================================================
            # ВАЖНО:
            #
            # НИКАКОГО seen
            # НИКАКОГО set
            # НИКАКОГО удаления дублей
            #
            # Каждая строка = отдельная запись.
            # ==================================================

            entries.append(
                {
                    "source_index": len(entries) + 1,
                    "source_line": line_number,
                    "source_path": source_line,
                    "alias": alias,
                }
            )

    return entries


# ============================================================
#                  ПОСТРОЕНИЕ NGENIX URL
# ============================================================

def build_ngenix_url(
    alias: str,
) -> str:

    return BASE_URL_TEMPLATE.format(
        alias=alias
    )


# ============================================================
#                  ПАРСИНГ EXTINF
# ============================================================

def parse_extinf_line(
    line: str,
    alias: str,
) -> Dict:

    info = {
        "tvg_id": "",
        "tvg_name": "",
        "tvg_logo": "",
        "group_title": "",
        "title": alias.replace(
            "CH_",
            "",
            1,
        ),
        "raw_extinf": line,
    }

    if not line.startswith("#EXTINF:"):
        return info

    attributes = {
        "tvg-id": "tvg_id",
        "tvg-name": "tvg_name",
        "tvg-logo": "tvg_logo",
        "group-title": "group_title",
    }

    for source_key, target_key in attributes.items():

        match = re.search(
            rf'{re.escape(source_key)}="([^"]*)"',
            line,
            re.IGNORECASE,
        )

        if match:
            info[target_key] = match.group(1)

    # Название после последней запятой
    if "," in line:

        title = line.split(
            ",",
            1
        )[1].strip()

        if title:
            info["title"] = title

    return info


def parse_m3u8(
    text: str,
    alias: str,
) -> Dict:

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    extinf = []
    stream_inf = []
    media = []
    urls = []

    for line in lines:

        if line.startswith("#EXTINF:"):
            extinf.append(
                parse_extinf_line(
                    line,
                    alias,
                )
            )

        elif line.startswith(
            "#EXT-X-STREAM-INF:"
        ):
            stream_inf.append(line)

        elif line.startswith(
            "#EXT-X-MEDIA:"
        ):
            media.append(line)

        elif (
            not line.startswith("#")
            and (
                line.startswith("http://")
                or line.startswith("https://")
                or line.endswith(".m3u8")
            )
        ):
            urls.append(line)

    first_extinf = (
        extinf[0]
        if extinf
        else {
            "tvg_id": "",
            "tvg_name": "",
            "tvg_logo": "",
            "group_title": "",
            "title": alias.replace(
                "CH_",
                "",
                1,
            ),
            "raw_extinf": "",
        }
    )

    return {
        "extinf": extinf,
        "extinf_count": len(extinf),
        "stream_inf": stream_inf,
        "stream_inf_count": len(stream_inf),
        "media": media,
        "media_count": len(media),
        "urls": urls,
        "url_count": len(urls),
        "primary": first_extinf,
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

    url = build_ngenix_url(
        alias
    )

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
            "tvg_id": "",
            "tvg_name": "",
            "tvg_logo": "",
            "group_title": "",
            "title": alias.replace(
                "CH_",
                "",
                1,
            ),
        },

        "extinf": [],
        "extinf_count": 0,

        "stream_inf": [],
        "stream_inf_count": 0,

        "media": [],
        "media_count": 0,

        "urls": [],
        "url_count": 0,

        "raw_m3u8": "",

        "error": None,
    }

    async with semaphore:

        try:

            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(
                    total=HTTP_TIMEOUT
                ),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "*/*",
                },
            ) as response:

                result["http_status"] = (
                    response.status
                )

                result["content_type"] = (
                    response.headers.get(
                        "Content-Type",
                        "",
                    )
                )

                if response.status != 200:

                    result["error"] = (
                        f"HTTP {response.status}"
                    )

                    skala(
                        f"{entry['source_index']:04d} "
                        f"{alias} -> "
                        f"HTTP {response.status} -> FAIL",
                        "WARN",
                    )

                    return result

                text = await response.text(
                    errors="replace"
                )

                result["content_length"] = len(
                    text
                )

                # Проверяем настоящий M3U8
                if "#EXTM3U" not in text:

                    result["error"] = (
                        "HTTP 200, но ответ "
                        "не содержит #EXTM3U"
                    )

                    skala(
                        f"{entry['source_index']:04d} "
                        f"{alias} -> "
                        f"HTTP 200 -> NOT M3U8",
                        "WARN",
                    )

                    return result

                parsed = parse_m3u8(
                    text,
                    alias,
                )

                result["working"] = True

                result["metadata"] = {
                    "tvg_id": parsed[
                        "primary"
                    ].get(
                        "tvg_id",
                        "",
                    ),

                    "tvg_name": parsed[
                        "primary"
                    ].get(
                        "tvg_name",
                        "",
                    ),

                    "tvg_logo": parsed[
                        "primary"
                    ].get(
                        "tvg_logo",
                        "",
                    ),

                    "group_title": parsed[
                        "primary"
                    ].get(
                        "group_title",
                        "",
                    ),

                    "title": parsed[
                        "primary"
                    ].get(
                        "title",
                        alias.replace(
                            "CH_",
                            "",
                            1,
                        ),
                    ),
                }

                result["extinf"] = parsed[
                    "extinf"
                ]

                result["extinf_count"] = parsed[
                    "extinf_count"
                ]

                result["stream_inf"] = parsed[
                    "stream_inf"
                ]

                result["stream_inf_count"] = (
                    parsed[
                        "stream_inf_count"
                    ]
                )

                result["media"] = parsed[
                    "media"
                ]

                result["media_count"] = parsed[
                    "media_count"
                ]

                result["urls"] = parsed[
                    "urls"
                ]

                result["url_count"] = parsed[
                    "url_count"
                ]

                # Сохраняем полный ответ.
                # Это позволяет не терять информацию,
                # которую Ngenix реально отдал.
                result["raw_m3u8"] = text

                title = (
                    result["metadata"].get(
                        "title"
                    )
                    or alias
                )

                skala(
                    f"{entry['source_index']:04d} "
                    f"{alias} -> "
                    f"HTTP 200 -> OK -> "
                    f"{title}",
                    "FOUND",
                )

                return result

        except asyncio.TimeoutError:

            result["error"] = "TIMEOUT"

            skala(
                f"{entry['source_index']:04d} "
                f"{alias} -> TIMEOUT",
                "WARN",
            )

            return result

        except aiohttp.ClientError as e:

            result["error"] = (
                f"{type(e).__name__}: {e}"
            )

            skala(
                f"{entry['source_index']:04d} "
                f"{alias} -> "
                f"HTTP ERROR -> {e}",
                "ERROR",
            )

            return result

        except Exception as e:

            result["error"] = (
                f"{type(e).__name__}: {e}"
            )

            skala(
                f"{entry['source_index']:04d} "
                f"{alias} -> "
                f"ERROR -> {e}",
                "ERROR",
            )

            return result


# ============================================================
#                   СКАНИРОВАНИЕ ВСЕХ
# ============================================================

async def scan_all(
    entries: List[Dict],
) -> List[Dict]:

    semaphore = asyncio.Semaphore(
        CONCURRENCY_LIMIT
    )

    connector = aiohttp.TCPConnector(
        limit=CONCURRENCY_LIMIT
    )

    timeout = aiohttp.ClientTimeout(
        total=HTTP_TIMEOUT
    )

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
    ) as session:

        tasks = [
            check_ngenix(
                session,
                semaphore,
                entry,
            )
            for entry in entries
        ]

        results = await asyncio.gather(
            *tasks
        )

    # gather сохраняет порядок задач.
    return results


# ============================================================
#                         JSON
# ============================================================

def build_json(
    entries: List[Dict],
    results: List[Dict],
) -> Dict:

    working = [
        result
        for result in results
        if result["working"]
    ]

    failed = [
        result
        for result in results
        if not result["working"]
    ]

    return {
        "scanner": "dmitrytv_to_ngenix",

        "version": "4.0",

        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),

        "source": {
            "name": "DmitryTV",
            "file": INPUT_FILE,

            # РОВНО количество CH в исходнике
            "entries_total": len(entries),
        },

        "ngenix": {
            "base": (
                "https://zabava-htlive.cdn.ngenix.net"
            ),

            "template": BASE_URL_TEMPLATE,
        },

        "statistics": {
            "source_ch_entries": len(entries),

            "ngenix_urls_built": len(entries),

            "checked": len(results),

            "working": len(working),

            "failed": len(failed),
        },

        # ВАЖНО:
        # Здесь находятся ВСЕ результаты,
        # включая FAIL.
        #
        # Никакой потери элементов.
        "channels": results,
    }


def write_json(
    entries: List[Dict],
    results: List[Dict],
) -> None:

    data = build_json(
        entries,
        results,
    )

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
#                         M3U
# ============================================================

def write_m3u(
    results: List[Dict],
) -> int:

    working = [
        result
        for result in results
        if result["working"]
    ]

    with open(
        OUTPUT_M3U,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:

        f.write(
            "#EXTM3U\n"
        )

        for channel in working:

            meta = channel[
                "metadata"
            ]

            alias = channel[
                "alias"
            ]

            title = (
                meta.get("title")
                or alias.replace(
                    "CH_",
                    "",
                    1,
                )
            )

            attributes = []

            tvg_id = meta.get(
                "tvg_id",
                "",
            )

            tvg_name = meta.get(
                "tvg_name",
                "",
            )

            tvg_logo = meta.get(
                "tvg_logo",
                "",
            )

            group_title = meta.get(
                "group_title",
                "",
            )

            if tvg_id:
                attributes.append(
                    f'tvg-id="{tvg_id}"'
                )

            if tvg_name:
                attributes.append(
                    f'tvg-name="{tvg_name}"'
                )

            if tvg_logo:
                attributes.append(
                    f'tvg-logo="{tvg_logo}"'
                )

            if group_title:
                attributes.append(
                    f'group-title="{group_title}"'
                )

            if attributes:

                f.write(
                    "#EXTINF:-1 "
                    + " ".join(attributes)
                    + ","
                    + title
                    + "\n"
                )

            else:

                f.write(
                    f"#EXTINF:-1,{title}\n"
                )

            f.write(
                "#EXTVLCOPT:http-user-agent="
                f"{USER_AGENT}\n"
            )

            # ИМЕННО построенный Ngenix URL
            f.write(
                channel["ngenix_url"]
                + "\n"
            )

    return len(working)


# ============================================================
#                    SKALA ИТОГОВЫЙ ОТЧЁТ
# ============================================================

def append_final_skala_report(
    entries: List[Dict],
    results: List[Dict],
    m3u_count: int,
) -> None:

    working = [
        result
        for result in results
        if result["working"]
    ]

    failed = [
        result
        for result in results
        if not result["working"]
    ]

    with open(
        OUTPUT_SKALA,
        "a",
        encoding="utf-8",
        newline="\n",
    ) as f:

        f.write("\n")
        f.write(
            "============================================================\n"
        )
        f.write(
            "                 FINAL SKALA REPORT\n"
        )
        f.write(
            "============================================================\n"
        )

        f.write(
            f"TIME: {now_local()}\n"
        )

        f.write(
            f"SOURCE: {INPUT_FILE}\n"
        )

        f.write(
            "NGENIX: "
            "https://zabava-htlive.cdn.ngenix.net\n"
        )

        f.write("\n")
        f.write(
            "-------------------- СТАТИСТИКА --------------------\n"
        )

        f.write(
            f"CH ENTRIES IN DMITRYTV : {len(entries)}\n"
        )

        f.write(
            f"NGENIX URLS BUILT      : {len(entries)}\n"
        )

        f.write(
            f"CHECKED                : {len(results)}\n"
        )

        f.write(
            f"HTTP 200 / WORKING     : {len(working)}\n"
        )

        f.write(
            f"FAILED                 : {len(failed)}\n"
        )

        f.write(
            f"M3U ENTRIES            : {m3u_count}\n"
        )

        # Контроль главного требования
        if len(entries) == len(results):

            f.write(
                "ONE-TO-ONE CHECK       : OK\n"
            )

        else:

            f.write(
                "ONE-TO-ONE CHECK       : ERROR\n"
            )

        f.write("\n")
        f.write(
            "-------------------- РЕЗУЛЬТАТЫ --------------------\n"
        )

        for result in results:

            status = (
                "OK"
                if result["working"]
                else "FAIL"
            )

            title = result[
                "metadata"
            ].get(
                "title",
                "",
            )

            f.write(
                f"[{result['source_index']:04d}] "
                f"{result['alias']} -> "
                f"{status} -> "
                f"HTTP {result['http_status']} -> "
                f"{title}\n"
            )

            f.write(
                f"  SOURCE: "
                f"{result['source_path']}\n"
            )

            f.write(
                f"  NGENIX: "
                f"{result['ngenix_url']}\n"
            )

            f.write(
                f"  EXTINF: "
                f"{result['extinf_count']}\n"
            )

            if result["error"]:
                f.write(
                    f"  ERROR: "
                    f"{result['error']}\n"
                )

        f.write("\n")
        f.write(
            "-------------------- ФАЙЛЫ --------------------\n"
        )

        f.write(
            f"M3U  : {OUTPUT_M3U}\n"
        )

        f.write(
            f"JSON : {OUTPUT_JSON}\n"
        )

        f.write(
            f"TXT  : {OUTPUT_SKALA}\n"
        )

        f.write(
            f"LOG  : {LOG_FILE}\n"
        )

        f.write(
            "============================================================\n"
        )


# ============================================================
#                           MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # Новый запуск -> новый SKALA.
    # Старые результаты этого же запуска не затираются.
    # --------------------------------------------------------

    with open(
        OUTPUT_SKALA,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            "SKALA NGENIX SCAN\n"
        )

        f.write(
            f"START: {now_local()}\n\n"
        )

    skala(
        "============================================================"
    )

    skala(
        "START DMITRYTV -> NGENIX"
    )

    # --------------------------------------------------------
    # 1. Загружаем ВСЕ CH
    # --------------------------------------------------------

    skala(
        f"LOAD SOURCE: {INPUT_FILE}"
    )

    entries = load_source_entries(
        INPUT_FILE
    )

    skala(
        f"CH ENTRIES EXTRACTED: {len(entries)}"
    )

    # --------------------------------------------------------
    # 2. Строим URL один к одному
    # --------------------------------------------------------

    skala(
        "BUILD NGENIX URLS: ONE SOURCE CH = ONE NGENIX URL"
    )

    for entry in entries:

        url = build_ngenix_url(
            entry["alias"]
        )

        skala(
            f"[{entry['source_index']:04d}] "
            f"{entry['alias']} -> {url}"
        )

    skala(
        f"NGENIX URLS BUILT: {len(entries)}"
    )

    # --------------------------------------------------------
    # 3. Проверяем каждый URL
    # --------------------------------------------------------

    skala(
        "START NGENIX SCAN"
    )

    results = asyncio.run(
        scan_all(
            entries
        )
    )

    # --------------------------------------------------------
    # 4. Контроль количества
    # --------------------------------------------------------

    if len(results) != len(entries):

        skala(
            "CRITICAL: SOURCE/RESULT COUNT MISMATCH",
            "ERROR",
        )

        raise RuntimeError(
            "Количество результатов Ngenix "
            "не равно количеству исходных CH."
        )

    skala(
        f"ONE-TO-ONE CHECK: "
        f"{len(entries)} -> {len(results)} -> OK",
        "FOUND",
    )

    # --------------------------------------------------------
    # 5. Рабочие
    # --------------------------------------------------------

    working = [
        result
        for result in results
        if result["working"]
    ]

    failed = [
        result
        for result in results
        if not result["working"]
    ]

    skala(
        "============================================================"
    )

    skala(
        f"SCAN COMPLETE: "
        f"{len(working)}/{len(entries)} WORKING",
        "FOUND",
    )

    skala(
        f"FAILED: {len(failed)}",
        "INFO",
    )

    # --------------------------------------------------------
    # 6. Полный JSON
    # --------------------------------------------------------

    write_json(
        entries,
        results,
    )

    skala(
        f"JSON READY: {OUTPUT_JSON}",
        "FOUND",
    )

    # --------------------------------------------------------
    # 7. M3U
    # --------------------------------------------------------

    m3u_count = write_m3u(
        results
    )

    skala(
        f"M3U READY: {OUTPUT_M3U} "
        f"({m3u_count} working entries)",
        "FOUND",
    )

    # --------------------------------------------------------
    # 8. Финальный SKALA
    # --------------------------------------------------------

    append_final_skala_report(
        entries,
        results,
        m3u_count,
    )

    skala(
        f"SKALA REPORT READY: {OUTPUT_SKALA}",
        "FOUND",
    )

    # --------------------------------------------------------
    # 9. Финал
    # --------------------------------------------------------

    skala(
        "============================================================"
    )

    skala(
        "FINAL RESULT"
    )

    skala(
        f"DMITRYTV CH       : {len(entries)}"
    )

    skala(
        f"NGENIX URLS       : {len(entries)}"
    )

    skala(
        f"CHECKED           : {len(results)}"
    )

    skala(
        f"WORKING HTTP 200  : {len(working)}",
        "FOUND",
    )

    skala(
        f"FAILED            : {len(failed)}"
    )

    skala(
        f"M3U ENTRIES       : {m3u_count}"
    )

    skala(
        "COMPLETE"
    )


# ============================================================
#                           START
# ============================================================

if __name__ == "__main__":
    main()