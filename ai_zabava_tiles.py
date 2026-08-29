#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import requests
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
#                     КОНФИГУРАЦИЯ
# ============================================================

# Исходный JSON со списком хвостов
JSON_INPUT = "_scanner_zaba_1788021849270.txt"

# CDN Zabava
BASE_CDN = "https://zabava-htlive.cdn.ngenix.net"

# Мастер-вариант.
# Именно по этой структуре строятся ссылки для всех каналов.
MASTER_VARIANT = "/hls/CH_TVC/variant.m3u8"

# Выходные файлы
OUT_JSON_NEW = "_scanner_zaba_2.json"
OUT_PLAYLIST_JSON = "zabava_tiles2.json"
OUT_SKALA_TXT = "zabava_skala2.txt"
OUT_M3U = "zabava_tiles2.m3u"
OUT_LOG = "zabava_scan2.log"

# HTTP timeout
HTTP_TIMEOUT = 10

# Заголовки запроса
HEADERS = {
    "User-Agent": "Mozilla/5.0 ZABAVA-CDN-SCANNER/2.0",
    "Accept": "*/*",
}


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

    def info(self, message):
        self.log("INFO", message)

    def found(self, message):
        self.log("FOUND", message)

    def warn(self, message):
        self.log("WARN", message)

    def error(self, message):
        self.log("ERROR", message)


# ============================================================
#                     ЗАГРУЗКА JSON
# ============================================================

def load_tails(logger):
    logger.info(f"LOAD JSON: {JSON_INPUT}")

    with open(JSON_INPUT, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("JSON должен содержать объект верхнего уровня")

    if "tails" not in data:
        raise KeyError("В JSON отсутствует поле 'tails'")

    tails = data["tails"]

    if not isinstance(tails, list):
        raise ValueError("Поле 'tails' должно быть массивом")

    logger.info(f"TAILS LOADED: {len(tails)}")

    return tails


# ============================================================
#                 ИЗВЛЕЧЕНИЕ КАНАЛА
# ============================================================

def extract_channel(tail):
    """
    Извлекает только идентификатор канала.

    Примеры входа:

        /hls/CH_TVC/mono.m3u8
        /hls/CH_TVC/variant.m3u8
        https://example.com/hls/CH_TVC/variant.m3u8
        CH_TVC

    Результат:

        CH_TVC

    Нам нужен только CH_XXX.
    """

    if not isinstance(tail, str):
        return None

    tail = tail.strip()

    if not tail:
        return None

    # Ищем сегмент вида CH_XXXXXX
    match = re.search(
        r"(?:^|/)("
        r"CH_[A-Za-z0-9_-]+"
        r")(?:/|$)",
        tail
    )

    if match:
        return match.group(1)

    # Дополнительный вариант:
    # если JSON уже содержит просто CH_TVC
    if re.fullmatch(r"CH_[A-Za-z0-9_-]+", tail):
        return tail

    return None


# ============================================================
#              ФОРМИРОВАНИЕ MASTER-ССЫЛКИ
# ============================================================

def build_variant_url(channel):
    """
    Формирует ссылку СТРОГО по мастер-примеру:

        /hls/CH_TVC/variant.m3u8

    Для CH_NTV:

        /hls/CH_NTV/variant.m3u8
    """

    master_match = re.fullmatch(
        r"/hls/CH_[A-Za-z0-9_-]+/variant\.m3u8",
        MASTER_VARIANT
    )

    if not master_match:
        raise ValueError(
            f"MASTER_VARIANT имеет неправильный формат: "
            f"{MASTER_VARIANT}"
        )

    path = f"/hls/{channel}/variant.m3u8"

    return BASE_CDN + path


# ============================================================
#              ГЕНЕРАЦИЯ КАНАЛОВ И ССЫЛОК
# ============================================================

def generate_links_from_tails(tails, logger):
    """
    Из tails извлекается только имя канала.

    Затем для каждого канала строится:

        https://zabava-htlive.cdn.ngenix.net/hls/CHANNEL/variant.m3u8

    Дубликаты каналов удаляются.
    """

    channels = []
    seen = set()

    for tail in tails:

        channel = extract_channel(tail)

        if not channel:
            logger.warn(
                f"CHANNEL NOT FOUND: {tail}"
            )
            continue

        if channel in seen:
            logger.info(
                f"DUPLICATE CHANNEL SKIP: {channel}"
            )
            continue

        seen.add(channel)

        url = build_variant_url(channel)

        item = {
            "channel": channel,
            "url": url,
            "tail": f"/hls/{channel}/variant.m3u8",
        }

        channels.append(item)

        logger.found(
            f"GENERATED: {channel} -> {url}"
        )

    logger.info(
        f"CHANNELS GENERATED: {len(channels)}"
    )

    return channels


# ============================================================
#                     HTTP ПРОВЕРКА CDN
# ============================================================

def check_http_all(channels, logger):
    """
    Проверка CDN.

    Единственный критерий успеха:

        HTTP 200

    Никаких дополнительных проверок содержимого,
    сегментов, EPG и т. д. нет.
    """

    results = []

    session = requests.Session()
    session.headers.update(HEADERS)

    total = len(channels)

    for index, item in enumerate(channels, start=1):

        channel = item["channel"]
        url = item["url"]

        status = None
        error = None

        try:
            response = session.get(
                url,
                timeout=HTTP_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )

            status = response.status_code

            # Нам нужен только HTTP-код.
            response.close()

        except requests.RequestException as exc:
            error = str(exc)

        ok = status == 200

        result = {
            "channel": channel,
            "url": url,
            "tail": item["tail"],
            "http": status,
            "ok": ok,
        }

        if error:
            result["error"] = error

        results.append(result)

        if ok:
            logger.found(
                f"[{index}/{total}] CDN 200 OK: "
                f"{channel} -> {url}"
            )
        else:
            if error:
                logger.warn(
                    f"[{index}/{total}] CDN FAIL: "
                    f"{channel} -> {url} -> {error}"
                )
            else:
                logger.warn(
                    f"[{index}/{total}] CDN HTTP {status}: "
                    f"{channel} -> {url}"
                )

    return results


# ============================================================
#                     НОВЫЙ JSON
# ============================================================

def build_new_json(results):
    """
    В новый JSON попадают только каналы,
    которые получили HTTP 200.
    """

    tails_ok = [
        result["tail"]
        for result in results
        if result["ok"]
    ]

    channels_ok = [
        result["channel"]
        for result in results
        if result["ok"]
    ]

    return {
        "scanner": "zabava_tails.py",
        "version": "2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": BASE_CDN,
        "master_variant": MASTER_VARIANT,
        "total_checked": len(results),
        "total_ok": len(tails_ok),
        "channels": channels_ok,
        "tails": tails_ok,
    }


# ============================================================
#                     JSON-ПЛЕЙЛИСТ
# ============================================================

def build_playlist_json(results):

    channels = []

    for result in results:

        if not result["ok"]:
            continue

        channels.append({
            "name": result["channel"],
            "url": result["url"],
        })

    return {
        "channels": channels
    }


# ============================================================
#                         M3U
# ============================================================

def build_m3u(results):

    lines = [
        "#EXTM3U"
    ]

    for result in results:

        if not result["ok"]:
            continue

        channel = result["channel"]
        url = result["url"]

        lines.append(
            f"#EXTINF:-1,{channel}"
        )

        lines.append(url)

    return "\n".join(lines) + "\n"


# ============================================================
#                     SKALA REPORT
# ============================================================

def build_skala_txt(results):

    lines = [
        "SKALA REPORT: ZABAVA CDN",
        ""
    ]

    ok_count = sum(
        1 for result in results
        if result["ok"]
    )

    total = len(results)

    lines.append(
        f"OK: {ok_count}/{total}"
    )

    lines.append("")

    for result in results:

        channel = result["channel"]
        url = result["url"]
        status = result["http"]

        if result["ok"]:
            state = "OK"
        else:
            state = "FAIL"

        lines.append(
            f"{channel} -> {url} -> "
            f"{status} {state}"
        )

    return "\n".join(lines) + "\n"


# ============================================================
#                     СОХРАНЕНИЕ JSON
# ============================================================

def save_json(filename, data):

    Path(filename).write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
#                         MAIN
# ============================================================

def main():

    logger = ScalaLogger(OUT_LOG)

    logger.info(
        "========================================"
    )
    logger.info(
        "       START ZABAVA CDN SCAN v2"
    )
    logger.info(
        "========================================"
    )

    logger.info(
        f"MASTER VARIANT: {MASTER_VARIANT}"
    )

    logger.info(
        f"BASE CDN: {BASE_CDN}"
    )

    # --------------------------------------------------------
    # 1. Загружаем tails
    # --------------------------------------------------------

    tails = load_tails(logger)

    # --------------------------------------------------------
    # 2. Извлекаем CH_XXX и строим variant.m3u8
    # --------------------------------------------------------

    channels = generate_links_from_tails(
        tails,
        logger
    )

    # --------------------------------------------------------
    # 3. Проверяем CDN
    # --------------------------------------------------------

    results = check_http_all(
        channels,
        logger
    )

    # --------------------------------------------------------
    # 4. Новый JSON
    # --------------------------------------------------------

    new_json = build_new_json(results)

    save_json(
        OUT_JSON_NEW,
        new_json
    )

    # --------------------------------------------------------
    # 5. JSON-плейлист
    # --------------------------------------------------------

    playlist_json = build_playlist_json(
        results
    )

    save_json(
        OUT_PLAYLIST_JSON,
        playlist_json
    )

    # --------------------------------------------------------
    # 6. M3U
    # --------------------------------------------------------

    Path(OUT_M3U).write_text(
        build_m3u(results),
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # 7. SKALA
    # --------------------------------------------------------

    Path(OUT_SKALA_TXT).write_text(
        build_skala_txt(results),
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # 8. Итог
    # --------------------------------------------------------

    total = len(results)

    ok_count = sum(
        1 for result in results
        if result["ok"]
    )

    fail_count = total - ok_count

    logger.info(
        "========================================"
    )

    logger.info(
        f"TOTAL CHECKED: {total}"
    )

    logger.info(
        f"HTTP 200 OK:   {ok_count}"
    )

    logger.info(
        f"FAILED:        {fail_count}"
    )

    logger.info(
        f"PLAYLIST:      {OUT_M3U}"
    )

    logger.info(
        f"JSON:          {OUT_PLAYLIST_JSON}"
    )

    logger.info(
        "========================================"
    )

    logger.info(
        "=== COMPLETE ==="
    )


# ============================================================
#                         ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()