import csv
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests


# ============================================================
# НАСТРОЙКИ
# ============================================================

BASE_URL = "https://streaming.televizor-24-tochka.ru/live/"
REFERER = "https://televizor24tochka.ru/"

START = 1
END = 1000

# Вежливый режим.
DELAY = 0.15

HEAD_TIMEOUT = 10
GET_TIMEOUT = 15

# Сколько максимум читать из ответа M3U8.
# Для поиска метаданных обычно этого более чем достаточно.
MAX_BYTES = 128 * 1024

OUTPUT_DIR = Path("televizor_scan_1_1000")

JSON_FILE = OUTPUT_DIR / "televizor_1_1000_full.json"
CSV_FILE = OUTPUT_DIR / "televizor_1_1000_report.csv"
M3U_FILE = OUTPUT_DIR / "televizor_1_1000.m3u"
MISSING_FILE = OUTPUT_DIR / "televizor_missing.txt"
LOG_FILE = OUTPUT_DIR / "scanner.log"


# ============================================================
# ЛОГИ
# ============================================================

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_FILE,
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("TELEVIZOR-SCANNER")


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),

    "Referer": REFERER,

    "Origin": "https://televizor24tochka.ru",

    "Accept": (
        "application/vnd.apple.mpegurl,"
        "application/x-mpegURL,"
        "text/plain,"
        "*/*"
    ),

    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",

    "Cache-Control": "no-cache",

    "Pragma": "no-cache",
})


# ============================================================
# URL
# ============================================================

def make_url(number):
    return f"{BASE_URL}{number}.m3u8"


# ============================================================
# ATTRIBUTE PARSER
# ============================================================

def parse_attributes(value):
    """
    Разбирает:

    BANDWIDTH=2000000,
    RESOLUTION=1920x1080,
    CODECS="avc1.640028,mp4a.40.2"
    """

    result = {}

    pattern = r'([A-Z0-9-]+)=(".*?"|[^,]*)'

    for key, val in re.findall(pattern, value):
        val = val.strip()

        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]

        result[key] = val

    return result


# ============================================================
# M3U8 ANALYZER
# ============================================================

def analyze_m3u8(text):
    """
    Максимально полезный анализ содержимого M3U8.
    """

    result = {
        "valid_m3u8": False,

        "playlist_type": None,

        "is_master": False,
        "is_media": False,

        "channel_names": [],

        "streams": [],

        "bandwidths": [],
        "resolutions": [],
        "codecs": [],
        "frame_rates": [],

        "audio_groups": [],
        "video_groups": [],
        "subtitles": [],

        "target_duration": None,
        "media_sequence": None,

        "segment_count": 0,
        "segment_durations": [],

        "uris": [],

        "raw_tags": []
    }

    if not text:
        return result

    lines = text.splitlines()

    # --------------------------------------------------------
    # Базовая проверка
    # --------------------------------------------------------

    stripped = text.lstrip()

    if not stripped.startswith("#EXTM3U"):

        if "#EXT-X-" not in text:
            return result

    result["valid_m3u8"] = True

    # --------------------------------------------------------
    # Построчный анализ
    # --------------------------------------------------------

    for index, raw_line in enumerate(lines):

        line = raw_line.strip()

        if not line:
            continue

        # ----------------------------------------------------
        # Все HLS-теги
        # ----------------------------------------------------

        if line.startswith("#EXT-X-"):

            result["raw_tags"].append(line)

        # ----------------------------------------------------
        # Master playlist
        # ----------------------------------------------------

        if line.startswith("#EXT-X-STREAM-INF:"):

            result["is_master"] = True

            attrs = parse_attributes(
                line.split(":", 1)[1]
            )

            stream = {
                "bandwidth": attrs.get("BANDWIDTH"),
                "average_bandwidth": attrs.get(
                    "AVERAGE-BANDWIDTH"
                ),
                "resolution": attrs.get(
                    "RESOLUTION"
                ),
                "codecs": attrs.get(
                    "CODECS"
                ),
                "frame_rate": attrs.get(
                    "FRAME-RATE"
                ),
                "audio": attrs.get(
                    "AUDIO"
                ),
                "video": attrs.get(
                    "VIDEO"
                ),
                "subtitles": attrs.get(
                    "SUBTITLES"
                ),
                "closed_captions": attrs.get(
                    "CLOSED-CAPTIONS"
                ),
                "uri": None
            }

            # Следующая непустая строка,
            # не являющаяся тегом — URI варианта.
            for next_index in range(
                index + 1,
                len(lines)
            ):

                next_line = lines[
                    next_index
                ].strip()

                if not next_line:
                    continue

                if next_line.startswith("#"):
                    continue

                stream["uri"] = next_line

                result["uris"].append(
                    next_line
                )

                break

            result["streams"].append(stream)

            if attrs.get("BANDWIDTH"):
                try:
                    result["bandwidths"].append(
                        int(attrs["BANDWIDTH"])
                    )
                except ValueError:
                    pass

            if attrs.get("RESOLUTION"):
                result["resolutions"].append(
                    attrs["RESOLUTION"]
                )

            if attrs.get("CODECS"):
                result["codecs"].append(
                    attrs["CODECS"]
                )

            if attrs.get("FRAME-RATE"):
                try:
                    result["frame_rates"].append(
                        float(attrs["FRAME-RATE"])
                    )
                except ValueError:
                    pass

            if attrs.get("AUDIO"):
                result["audio_groups"].append(
                    attrs["AUDIO"]
                )

            if attrs.get("VIDEO"):
                result["video_groups"].append(
                    attrs["VIDEO"]
                )

            if attrs.get("SUBTITLES"):
                result["subtitles"].append(
                    attrs["SUBTITLES"]
                )

        # ----------------------------------------------------
        # EXT-X-MEDIA
        # ----------------------------------------------------

        elif line.startswith("#EXT-X-MEDIA:"):

            attrs = parse_attributes(
                line.split(":", 1)[1]
            )

            media_type = attrs.get("TYPE")

            name = attrs.get("NAME")

            if name:
                result["channel_names"].append(
                    name
                )

            if media_type == "SUBTITLES":
                if name:
                    result["subtitles"].append(
                        name
                    )

        # ----------------------------------------------------
        # EXTINF
        # ----------------------------------------------------

        elif line.startswith("#EXTINF:"):

            result["is_media"] = True

            try:

                value = line.split(
                    ":",
                    1
                )[1]

                duration = value.split(
                    ",",
                    1
                )[0]

                result[
                    "segment_durations"
                ].append(float(duration))

            except Exception:
                pass

            # Возможное название:
            if "," in line:

                name = line.split(
                    ",",
                    1
                )[1].strip()

                if (
                    name
                    and name not in result[
                        "channel_names"
                    ]
                ):
                    result[
                        "channel_names"
                    ].append(name)

        # ----------------------------------------------------
        # TARGETDURATION
        # ----------------------------------------------------

        elif line.startswith(
            "#EXT-X-TARGETDURATION:"
        ):

            try:
                result[
                    "target_duration"
                ] = int(
                    line.split(":", 1)[1]
                )
            except ValueError:
                pass

        # ----------------------------------------------------
        # MEDIA-SEQUENCE
        # ----------------------------------------------------

        elif line.startswith(
            "#EXT-X-MEDIA-SEQUENCE:"
        ):

            try:
                result[
                    "media_sequence"
                ] = int(
                    line.split(":", 1)[1]
                )
            except ValueError:
                pass

        # ----------------------------------------------------
        # PLAYLIST-TYPE
        # ----------------------------------------------------

        elif line.startswith(
            "#EXT-X-PLAYLIST-TYPE:"
        ):

            result[
                "playlist_type"
            ] = line.split(":", 1)[1].strip()

        # ----------------------------------------------------
        # URI сегмента
        # ----------------------------------------------------

        elif not line.startswith("#"):

            result["uris"].append(line)

    # --------------------------------------------------------
    # Определяем тип
    # --------------------------------------------------------

    if result["is_master"]:
        result["playlist_type"] = (
            result["playlist_type"]
            or "MASTER"
        )

    elif result["is_media"]:
        result["playlist_type"] = (
            result["playlist_type"]
            or "MEDIA"
        )

    # --------------------------------------------------------
    # Количество сегментов
    # --------------------------------------------------------

    result["segment_count"] = len(
        result["segment_durations"]
    )

    # --------------------------------------------------------
    # Уникальные значения
    # --------------------------------------------------------

    result["channel_names"] = list(
        dict.fromkeys(
            result["channel_names"]
        )
    )

    result["bandwidths"] = sorted(
        set(result["bandwidths"])
    )

    result["resolutions"] = list(
        dict.fromkeys(
            result["resolutions"]
        )
    )

    result["codecs"] = list(
        dict.fromkeys(
            result["codecs"]
        )
    )

    result["frame_rates"] = sorted(
        set(result["frame_rates"])
    )

    result["audio_groups"] = list(
        dict.fromkeys(
            result["audio_groups"]
        )
    )

    result["video_groups"] = list(
        dict.fromkeys(
            result["video_groups"]
        )
    )

    result["subtitles"] = list(
        dict.fromkeys(
            result["subtitles"]
        )
    )

    return result


# ============================================================
# ЧТЕНИЕ ОГРАНИЧЕННОГО ОБЪЁМА
# ============================================================

def download_limited(response):
    """
    Читаем максимум MAX_BYTES.
    """

    data = bytearray()

    for chunk in response.iter_content(
        chunk_size=8192
    ):

        if not chunk:
            continue

        remaining = (
            MAX_BYTES - len(data)
        )

        if remaining <= 0:
            break

        data.extend(
            chunk[:remaining]
        )

        if len(data) >= MAX_BYTES:
            break

    return bytes(data)


# ============================================================
# ОДИН ПОТОК
# ============================================================

def scan_one(number):

    url = make_url(number)

    result = {
        "number": number,
        "filename": f"{number}.m3u8",
        "url": url,

        "status": None,
        "content_type": None,
        "content_length": None,

        "final_url": None,
        "redirect_chain": [],

        "server": None,
        "via": None,

        "response_bytes": 0,
        "elapsed_ms": None,

        "classification": None,

        "valid_m3u8": False,

        "channel_name": None,
        "channel_names": [],

        "playlist": {},

        "error": None
    }

    started = time.perf_counter()

    try:

        # ====================================================
        # HEAD
        # ====================================================

        head = session.head(
            url,
            timeout=HEAD_TIMEOUT,
            allow_redirects=True
        )

        result["status"] = head.status_code

        result["content_type"] = (
            head.headers.get(
                "Content-Type"
            )
        )

        result["content_length"] = (
            head.headers.get(
                "Content-Length"
            )
        )

        result["final_url"] = head.url

        result["server"] = (
            head.headers.get("Server")
        )

        result["via"] = (
            head.headers.get("Via")
        )

        if head.history:

            result[
                "redirect_chain"
            ] = [
                r.url
                for r in head.history
            ]

        # ====================================================
        # Даже при HEAD 200 всё равно делаем GET.
        # ====================================================

        if head.status_code >= 400:

            result["classification"] = (
                f"HTTP_{head.status_code}"
            )

            return finish_result(
                result,
                started
            )

        # ====================================================
        # GET
        # ====================================================

        response = session.get(
            url,
            timeout=GET_TIMEOUT,
            allow_redirects=True,
            stream=True
        )

        content = download_limited(
            response
        )

        result["response_bytes"] = len(
            content
        )

        result["final_url"] = (
            response.url
        )

        # ====================================================
        # Декодирование
        # ====================================================

        try:

            text = content.decode(
                "utf-8-sig"
            )

        except UnicodeDecodeError:

            text = content.decode(
                "utf-8",
                errors="replace"
            )

        # ====================================================
        # Анализ
        # ====================================================

        metadata = analyze_m3u8(
            text
        )

        result["playlist"] = metadata

        result["valid_m3u8"] = (
            metadata["valid_m3u8"]
        )

        result["channel_names"] = (
            metadata["channel_names"]
        )

        if metadata["channel_names"]:

            result["channel_name"] = (
                metadata[
                    "channel_names"
                ][0]
            )

        # ====================================================
        # Классификация
        # ====================================================

        if metadata["valid_m3u8"]:

            if metadata["is_master"]:

                result[
                    "classification"
                ] = "M3U8_MASTER"

            elif metadata["is_media"]:

                result[
                    "classification"
                ] = "M3U8_MEDIA"

            else:

                result[
                    "classification"
                ] = "M3U8"

        elif response.status_code == 404:

            result[
                "classification"
            ] = "NOT_FOUND"

        elif (
            "text/html"
            in (
                result[
                    "content_type"
                ]
                or ""
            ).lower()
        ):

            result[
                "classification"
            ] = "HTML"

        else:

            result[
                "classification"
            ] = "UNKNOWN"

    except requests.exceptions.Timeout:

        result[
            "classification"
        ] = "TIMEOUT"

        result["error"] = (
            "Request timeout"
        )

    except requests.exceptions.ConnectionError as e:

        result[
            "classification"
        ] = "CONNECTION_ERROR"

        result["error"] = str(e)

    except requests.exceptions.RequestException as e:

        result[
            "classification"
        ] = "REQUEST_ERROR"

        result["error"] = str(e)

    except Exception as e:

        result[
            "classification"
        ] = "ERROR"

        result["error"] = repr(e)

    return finish_result(
        result,
        started
    )


# ============================================================
# ВРЕМЯ
# ============================================================

def finish_result(
    result,
    started
):

    result["elapsed_ms"] = round(
        (
            time.perf_counter()
            - started
        ) * 1000,
        2
    )

    return result


# ============================================================
# CSV
# ============================================================

def save_csv(results):

    fields = [
        "number",
        "filename",
        "url",
        "status",
        "content_type",
        "content_length",
        "final_url",
        "redirect_chain",
        "server",
        "via",
        "response_bytes",
        "elapsed_ms",
        "classification",
        "valid_m3u8",
        "channel_name",
        "channel_names",

        "playlist_type",
        "is_master",
        "is_media",

        "bandwidths",
        "resolutions",
        "codecs",
        "frame_rates",

        "audio_groups",
        "video_groups",
        "subtitles",

        "target_duration",
        "media_sequence",

        "segment_count",

        "uris",

        "error"
    ]

    with open(
        CSV_FILE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for item in results:

            playlist = item.get(
                "playlist",
                {}
            )

            row = {
                "number": item["number"],
                "filename": item["filename"],
                "url": item["url"],
                "status": item["status"],
                "content_type": item[
                    "content_type"
                ],
                "content_length": item[
                    "content_length"
                ],
                "final_url": item[
                    "final_url"
                ],
                "redirect_chain": "; ".join(
                    item[
                        "redirect_chain"
                    ]
                ),
                "server": item["server"],
                "via": item["via"],
                "response_bytes": item[
                    "response_bytes"
                ],
                "elapsed_ms": item[
                    "elapsed_ms"
                ],
                "classification": item[
                    "classification"
                ],
                "valid_m3u8": item[
                    "valid_m3u8"
                ],
                "channel_name": item[
                    "channel_name"
                ],
                "channel_names": "; ".join(
                    item[
                        "channel_names"
                    ]
                ),

                "playlist_type": playlist.get(
                    "playlist_type"
                ),

                "is_master": playlist.get(
                    "is_master"
                ),

                "is_media": playlist.get(
                    "is_media"
                ),

                "bandwidths": "; ".join(
                    map(
                        str,
                        playlist.get(
                            "bandwidths",
                            []
                        )
                    )
                ),

                "resolutions": "; ".join(
                    playlist.get(
                        "resolutions",
                        []
                    )
                ),

                "codecs": "; ".join(
                    playlist.get(
                        "codecs",
                        []
                    )
                ),

                "frame_rates": "; ".join(
                    map(
                        str,
                        playlist.get(
                            "frame_rates",
                            []
                        )
                    )
                ),

                "audio_groups": "; ".join(
                    playlist.get(
                        "audio_groups",
                        []
                    )
                ),

                "video_groups": "; ".join(
                    playlist.get(
                        "video_groups",
                        []
                    )
                ),

                "subtitles": "; ".join(
                    playlist.get(
                        "subtitles",
                        []
                    )
                ),

                "target_duration": playlist.get(
                    "target_duration"
                ),

                "media_sequence": playlist.get(
                    "media_sequence"
                ),

                "segment_count": playlist.get(
                    "segment_count"
                ),

                "uris": "; ".join(
                    playlist.get(
                        "uris",
                        []
                    )
                ),

                "error": item["error"]
            }

            writer.writerow(row)


# ============================================================
# JSON
# ============================================================

def save_json(results):

    data = {
        "scanner": (
            "TELEVIZOR-24-TOCHKA "
            "M3U8 SCANNER"
        ),

        "base_url": BASE_URL,
        "referer": REFERER,

        "range": {
            "start": START,
            "end": END
        },

        "total": len(results),

        "results": results
    }

    JSON_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# ИТОГОВЫЙ M3U
# ============================================================

def save_m3u(results):

    lines = [
        "#EXTM3U",
        'url-tvg="https://iptvx.one/EPG"',
        ""
    ]

    found = [
        x
        for x in results
        if x["valid_m3u8"]
    ]

    found.sort(
        key=lambda x: x["number"]
    )

    for item in found:

        number = item["number"]

        # ----------------------------------------------------
        # Если сервер сам сообщил название,
        # используем его.
        # ----------------------------------------------------

        name = item["channel_name"]

        if not name:

            name = (
                f"Канал {number}"
            )

        display_name = (
            f"{number}.m3u8 — {name}"
        )

        lines.append(
            '#EXTINF:-1 '
            'group-title="Televizor 24",'
            + display_name
        )

        lines.append(
            f"#EXTVLCOPT:http-referrer="
            f"{REFERER}"
        )

        lines.append(
            item["url"]
        )

        lines.append("")

    M3U_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# ============================================================
# MISSING
# ============================================================

def save_missing(results):

    missing = [
        x
        for x in results
        if not x["valid_m3u8"]
    ]

    lines = []

    for item in missing:

        lines.append(
            f'{item["number"]}.m3u8 | '
            f'{item["classification"]} | '
            f'HTTP={item["status"]} | '
            f'{item["error"] or ""}'
        )

    MISSING_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# ============================================================
# CONSOLE SUMMARY
# ============================================================

def print_result(item):

    number = item["number"]

    classification = (
        item["classification"]
    )

    if item["valid_m3u8"]:

        name = (
            item["channel_name"]
            or "название не указано"
        )

        playlist = item[
            "playlist"
        ]

        resolutions = ", ".join(
            playlist.get(
                "resolutions",
                []
            )
        )

        bandwidths = ", ".join(
            map(
                str,
                playlist.get(
                    "bandwidths",
                    []
                )
            )
        )

        logger.info(
            "[%04d] FOUND | %s | "
            "%s | RES=%s | BW=%s",
            number,
            classification,
            name,
            resolutions or "-",
            bandwidths or "-"
        )

    else:

        logger.info(
            "[%04d] %s | HTTP=%s",
            number,
            classification,
            item["status"]
        )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "=============================================="
    )

    logger.info(
        "TELEVIZOR-24-TOCHKA M3U8 SCANNER"
    )

    logger.info(
        "Диапазон: %d-%d",
        START,
        END
    )

    logger.info(
        "=============================================="
    )

    results = []

    started = time.perf_counter()

    for number in range(
        START,
        END + 1
    ):

        result = scan_one(
            number
        )

        results.append(
            result
        )

        print_result(
            result
        )

        # ----------------------------------------------------
        # Вежливая задержка.
        # ----------------------------------------------------

        if DELAY > 0:

            time.sleep(
                DELAY
            )

    # --------------------------------------------------------
    # Сортировка
    # --------------------------------------------------------

    results.sort(
        key=lambda x: x["number"]
    )

    # --------------------------------------------------------
    # Сохранение
    # --------------------------------------------------------

    save_json(results)
    save_csv(results)
    save_m3u(results)
    save_missing(results)

    # --------------------------------------------------------
    # Статистика
    # --------------------------------------------------------

    found = [
        x
        for x in results
        if x["valid_m3u8"]
    ]

    master = [
        x
        for x in found
        if x["playlist"].get(
            "is_master"
        )
    ]

    media = [
        x
        for x in found
        if x["playlist"].get(
            "is_media"
        )
    ]

    named = [
        x
        for x in found
        if x["channel_name"]
    ]

    elapsed = (
        time.perf_counter()
        - started
    )

    logger.info("")
    logger.info(
        "=============================================="
    )
    logger.info(
        "СКАНИРОВАНИЕ ЗАВЕРШЕНО"
    )
    logger.info(
        "=============================================="
    )

    logger.info(
        "Проверено:              %d",
        len(results)
    )

    logger.info(
        "Найдено M3U8:           %d",
        len(found)
    )

    logger.info(
        "Master:                 %d",
        len(master)
    )

    logger.info(
        "Media:                  %d",
        len(media)
    )

    logger.info(
        "С названием:            %d",
        len(named)
    )

    logger.info(
        "Без названия:           %d",
        len(found) - len(named)
    )

    logger.info(
        "Не M3U8/ошибки:         %d",
        len(results) - len(found)
    )

    logger.info(
        "Время:                  %.1f сек.",
        elapsed
    )

    logger.info("")
    logger.info(
        "JSON:    %s",
        JSON_FILE
    )

    logger.info(
        "CSV:     %s",
        CSV_FILE
    )

    logger.info(
        "M3U:     %s",
        M3U_FILE
    )

    logger.info(
        "Missing: %s",
        MISSING_FILE
    )

    logger.info(
        "Log:     %s",
        LOG_FILE
    )


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        logger.warning(
            "Сканирование остановлено пользователем."
        )

    except Exception as e:

        logger.exception(
            "Критическая ошибка: %s",
            e
        )