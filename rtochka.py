import csv
import json
import logging
import re
import time
from pathlib import Path

import requests


# ============================================================
# RTOCHKA
# TELEVIZOR 24 TOCHKA
# M3U8 SCANNER 1-2800
# ============================================================


# ============================================================
# НАСТРОЙКИ
# ============================================================

BASE_URL = "https://streaming.televizor-24-tochka.ru/live/"
REFERER = "https://televizor24tochka.ru/"

START = 1
END = 2800

# Вежливый режим.
# Пауза между проверками.
DELAY = 0.15

HEAD_TIMEOUT = 10
GET_TIMEOUT = 15

# ВАЖНО:
# НЕТ MAX_BYTES.
#
# M3U8 читается полностью.
# Сам видеопоток и его сегменты (.ts/.m4s)
# этот скрипт НЕ скачивает.
#
# Он получает и анализирует только playlist M3U8.


# ============================================================
# ПУТИ РЕЗУЛЬТАТОВ
# ============================================================

OUTPUT_DIR = Path("televizor_scan_1_2800")

JSON_FILE = (
    OUTPUT_DIR /
    "televizor_1_2800_full.json"
)

CSV_FILE = (
    OUTPUT_DIR /
    "televizor_1_2800_report.csv"
)

M3U_FILE = (
    OUTPUT_DIR /
    "televizor_1_2800.m3u"
)

MISSING_FILE = (
    OUTPUT_DIR /
    "televizor_missing.txt"
)

LOG_FILE = (
    OUTPUT_DIR /
    "scanner.log"
)

SUMMARY_FILE = (
    OUTPUT_DIR /
    "scan_summary.json"
)


# ============================================================
# СОЗДАНИЕ ПАПКИ
# ============================================================

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# ЛОГИ
# ============================================================

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

logger = logging.getLogger(
    "RTOCHKA-TELEVIZOR-SCANNER"
)


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),

    "Referer": REFERER,

    "Origin": (
        "https://televizor24tochka.ru"
    ),

    "Accept": (
        "application/vnd.apple.mpegurl,"
        "application/x-mpegURL,"
        "text/plain,"
        "*/*"
    ),

    "Accept-Language": (
        "ru-RU,ru;q=0.9,"
        "en-US;q=0.8"
    ),

    "Cache-Control": "no-cache",

    "Pragma": "no-cache",
})


# ============================================================
# URL
# ============================================================

def make_url(number):
    return (
        f"{BASE_URL}"
        f"{number}.m3u8"
    )


# ============================================================
# ATTRIBUTE PARSER
# ============================================================

def parse_attributes(value):
    """
    Разбирает HLS-атрибуты:

    BANDWIDTH=2000000,
    RESOLUTION=1920x1080,
    CODECS="avc1.640028,mp4a.40.2"
    """

    result = {}

    pattern = (
        r'([A-Z0-9-]+)=(".*?"|[^,]*)'
    )

    for key, val in re.findall(
        pattern,
        value
    ):

        val = val.strip()

        if (
            val.startswith('"')
            and val.endswith('"')
        ):
            val = val[1:-1]

        result[key] = val

    return result


# ============================================================
# M3U8 ANALYZER
# ============================================================

def analyze_m3u8(text):
    """
    Полный анализ полученного M3U8.

    Ничего искусственно не обрезается.
    """

    result = {
        "valid_m3u8": False,

        "playlist_type": None,

        "is_master": False,
        "is_media": False,

        "channel_names": [],

        "streams": [],

        "bandwidths": [],
        "average_bandwidths": [],

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

    # ========================================================
    # БАЗОВАЯ ПРОВЕРКА
    # ========================================================

    stripped = text.lstrip()

    if not stripped.startswith(
        "#EXTM3U"
    ):

        if "#EXT-X-" not in text:
            return result

    result["valid_m3u8"] = True


    # ========================================================
    # ПОСТРОЧНЫЙ АНАЛИЗ
    # ========================================================

    for index, raw_line in enumerate(
        lines
    ):

        line = raw_line.strip()

        if not line:
            continue


        # ====================================================
        # ВСЕ HLS TAG
        # ====================================================

        if line.startswith("#EXT-X-"):

            result[
                "raw_tags"
            ].append(line)


        # ====================================================
        # MASTER PLAYLIST
        # ====================================================

        if line.startswith(
            "#EXT-X-STREAM-INF:"
        ):

            result["is_master"] = True

            attrs = parse_attributes(
                line.split(
                    ":",
                    1
                )[1]
            )

            stream = {
                "bandwidth":
                    attrs.get(
                        "BANDWIDTH"
                    ),

                "average_bandwidth":
                    attrs.get(
                        "AVERAGE-BANDWIDTH"
                    ),

                "resolution":
                    attrs.get(
                        "RESOLUTION"
                    ),

                "codecs":
                    attrs.get(
                        "CODECS"
                    ),

                "frame_rate":
                    attrs.get(
                        "FRAME-RATE"
                    ),

                "audio":
                    attrs.get(
                        "AUDIO"
                    ),

                "video":
                    attrs.get(
                        "VIDEO"
                    ),

                "subtitles":
                    attrs.get(
                        "SUBTITLES"
                    ),

                "closed_captions":
                    attrs.get(
                        "CLOSED-CAPTIONS"
                    ),

                "uri": None
            }


            # ------------------------------------------------
            # URI ВАРИАНТА
            # ------------------------------------------------

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

                result[
                    "uris"
                ].append(
                    next_line
                )

                break


            result[
                "streams"
            ].append(stream)


            # ------------------------------------------------
            # BANDWIDTH
            # ------------------------------------------------

            if attrs.get(
                "BANDWIDTH"
            ):

                try:

                    result[
                        "bandwidths"
                    ].append(
                        int(
                            attrs[
                                "BANDWIDTH"
                            ]
                        )
                    )

                except ValueError:
                    pass


            # ------------------------------------------------
            # AVERAGE-BANDWIDTH
            # ------------------------------------------------

            if attrs.get(
                "AVERAGE-BANDWIDTH"
            ):

                try:

                    result[
                        "average_bandwidths"
                    ].append(
                        int(
                            attrs[
                                "AVERAGE-BANDWIDTH"
                            ]
                        )
                    )

                except ValueError:
                    pass


            # ------------------------------------------------
            # RESOLUTION
            # ------------------------------------------------

            if attrs.get(
                "RESOLUTION"
            ):

                result[
                    "resolutions"
                ].append(
                    attrs[
                        "RESOLUTION"
                    ]
                )


            # ------------------------------------------------
            # CODECS
            # ------------------------------------------------

            if attrs.get(
                "CODECS"
            ):

                result[
                    "codecs"
                ].append(
                    attrs[
                        "CODECS"
                    ]
                )


            # ------------------------------------------------
            # FRAME-RATE
            # ------------------------------------------------

            if attrs.get(
                "FRAME-RATE"
            ):

                try:

                    result[
                        "frame_rates"
                    ].append(
                        float(
                            attrs[
                                "FRAME-RATE"
                            ]
                        )
                    )

                except ValueError:
                    pass


            # ------------------------------------------------
            # AUDIO
            # ------------------------------------------------

            if attrs.get(
                "AUDIO"
            ):

                result[
                    "audio_groups"
                ].append(
                    attrs[
                        "AUDIO"
                    ]
                )


            # ------------------------------------------------
            # VIDEO
            # ------------------------------------------------

            if attrs.get(
                "VIDEO"
            ):

                result[
                    "video_groups"
                ].append(
                    attrs[
                        "VIDEO"
                    ]
                )


            # ------------------------------------------------
            # SUBTITLES
            # ------------------------------------------------

            if attrs.get(
                "SUBTITLES"
            ):

                result[
                    "subtitles"
                ].append(
                    attrs[
                        "SUBTITLES"
                    ]
                )


        # ====================================================
        # EXT-X-MEDIA
        # ====================================================

        elif line.startswith(
            "#EXT-X-MEDIA:"
        ):

            attrs = parse_attributes(
                line.split(
                    ":",
                    1
                )[1]
            )

            media_type = attrs.get(
                "TYPE"
            )

            name = attrs.get(
                "NAME"
            )

            if name:

                result[
                    "channel_names"
                ].append(name)


            if media_type == "SUBTITLES":

                if name:

                    result[
                        "subtitles"
                    ].append(name)


        # ====================================================
        # EXTINF
        # ====================================================

        elif line.startswith(
            "#EXTINF:"
        ):

            result[
                "is_media"
            ] = True

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
                ].append(
                    float(duration)
                )

            except Exception:
                pass


            # ------------------------------------------------
            # ВОЗМОЖНОЕ НАЗВАНИЕ
            # ------------------------------------------------

            if "," in line:

                name = line.split(
                    ",",
                    1
                )[1].strip()

                if (
                    name
                    and name not in
                    result[
                        "channel_names"
                    ]
                ):

                    result[
                        "channel_names"
                    ].append(name)


        # ====================================================
        # TARGETDURATION
        # ====================================================

        elif line.startswith(
            "#EXT-X-TARGETDURATION:"
        ):

            try:

                result[
                    "target_duration"
                ] = int(
                    line.split(
                        ":",
                        1
                    )[1]
                )

            except ValueError:
                pass


        # ====================================================
        # MEDIA-SEQUENCE
        # ====================================================

        elif line.startswith(
            "#EXT-X-MEDIA-SEQUENCE:"
        ):

            try:

                result[
                    "media_sequence"
                ] = int(
                    line.split(
                        ":",
                        1
                    )[1]
                )

            except ValueError:
                pass


        # ====================================================
        # PLAYLIST-TYPE
        # ====================================================

        elif line.startswith(
            "#EXT-X-PLAYLIST-TYPE:"
        ):

            result[
                "playlist_type"
            ] = line.split(
                ":",
                1
            )[1].strip()


        # ====================================================
        # URI СЕГМЕНТА / URI
        # ====================================================

        elif not line.startswith("#"):

            result[
                "uris"
            ].append(line)


    # ========================================================
    # ТИП PLAYLIST
    # ========================================================

    if result["is_master"]:

        result[
            "playlist_type"
        ] = (
            result[
                "playlist_type"
            ]
            or "MASTER"
        )

    elif result["is_media"]:

        result[
            "playlist_type"
        ] = (
            result[
                "playlist_type"
            ]
            or "MEDIA"
        )


    # ========================================================
    # SEGMENT COUNT
    # ========================================================

    result[
        "segment_count"
    ] = len(
        result[
            "segment_durations"
        ]
    )


    # ========================================================
    # УНИКАЛИЗАЦИЯ
    # ========================================================

    result[
        "channel_names"
    ] = list(
        dict.fromkeys(
            result[
                "channel_names"
            ]
        )
    )

    result[
        "bandwidths"
    ] = sorted(
        set(
            result[
                "bandwidths"
            ]
        )
    )

    result[
        "average_bandwidths"
    ] = sorted(
        set(
            result[
                "average_bandwidths"
            ]
        )
    )

    result[
        "resolutions"
    ] = list(
        dict.fromkeys(
            result[
                "resolutions"
            ]
        )
    )

    result[
        "codecs"
    ] = list(
        dict.fromkeys(
            result[
                "codecs"
            ]
        )
    )

    result[
        "frame_rates"
    ] = sorted(
        set(
            result[
                "frame_rates"
            ]
        )
    )

    result[
        "audio_groups"
    ] = list(
        dict.fromkeys(
            result[
                "audio_groups"
            ]
        )
    )

    result[
        "video_groups"
    ] = list(
        dict.fromkeys(
            result[
                "video_groups"
            ]
        )
    )

    result[
        "subtitles"
    ] = list(
        dict.fromkeys(
            result[
                "subtitles"
            ]
        )
    )

    return result


# ============================================================
# ОДИН ПОТОК / ОДНА ССЫЛКА
# ============================================================

def scan_one(number):

    url = make_url(number)

    result = {
        "number": number,

        "filename":
            f"{number}.m3u8",

        "url": url,

        "status": None,

        "get_status": None,

        "content_type": None,

        "content_length": None,

        "get_content_length": None,

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

        try:

            head = session.head(
                url,
                timeout=HEAD_TIMEOUT,
                allow_redirects=True
            )

            result[
                "status"
            ] = head.status_code

            result[
                "content_type"
            ] = head.headers.get(
                "Content-Type"
            )

            result[
                "content_length"
            ] = head.headers.get(
                "Content-Length"
            )

            result[
                "final_url"
            ] = head.url

            result[
                "server"
            ] = head.headers.get(
                "Server"
            )

            result[
                "via"
            ] = head.headers.get(
                "Via"
            )

            if head.history:

                result[
                    "redirect_chain"
                ] = [
                    r.url
                    for r in head.history
                ]

        except requests.exceptions.RequestException as e:

            logger.debug(
                "HEAD failed for %s: %s",
                url,
                e
            )


        # ====================================================
        # GET
        #
        # ВАЖНО:
        # GET выполняется независимо от HEAD.
        # ====================================================

        response = session.get(
            url,
            timeout=GET_TIMEOUT,
            allow_redirects=True
        )

        result[
            "get_status"
        ] = response.status_code

        result[
            "status"
        ] = response.status_code

        result[
            "content_type"
        ] = (
            response.headers.get(
                "Content-Type"
            )
            or result[
                "content_type"
            ]
        )

        result[
            "get_content_length"
        ] = response.headers.get(
            "Content-Length"
        )

        result[
            "final_url"
        ] = response.url

        result[
            "server"
        ] = (
            response.headers.get(
                "Server"
            )
            or result[
                "server"
            ]
        )

        result[
            "via"
        ] = (
            response.headers.get(
                "Via"
            )
            or result[
                "via"
            ]
        )

        if response.history:

            result[
                "redirect_chain"
            ] = [
                r.url
                for r in response.history
            ]


        # ====================================================
        # ПОЛНЫЙ BODY M3U8
        # ====================================================

        content = response.content

        result[
            "response_bytes"
        ] = len(content)


        # ====================================================
        # ДЕКОДИРОВАНИЕ
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
        # АНАЛИЗ
        # ====================================================

        metadata = analyze_m3u8(
            text
        )

        result[
            "playlist"
        ] = metadata

        result[
            "valid_m3u8"
        ] = metadata[
            "valid_m3u8"
        ]

        result[
            "channel_names"
        ] = metadata[
            "channel_names"
        ]


        if metadata[
            "channel_names"
        ]:

            result[
                "channel_name"
            ] = metadata[
                "channel_names"
            ][0]


        # ====================================================
        # КЛАССИФИКАЦИЯ
        # ====================================================

        if metadata[
            "valid_m3u8"
        ]:

            if metadata[
                "is_master"
            ]:

                result[
                    "classification"
                ] = "M3U8_MASTER"

            elif metadata[
                "is_media"
            ]:

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

        elif response.status_code >= 400:

            result[
                "classification"
            ] = (
                f"HTTP_"
                f"{response.status_code}"
            )

        else:

            result[
                "classification"
            ] = "UNKNOWN"


    except requests.exceptions.Timeout:

        result[
            "classification"
        ] = "TIMEOUT"

        result[
            "error"
        ] = "Request timeout"


    except requests.exceptions.ConnectionError as e:

        result[
            "classification"
        ] = "CONNECTION_ERROR"

        result[
            "error"
        ] = str(e)


    except requests.exceptions.RequestException as e:

        result[
            "classification"
        ] = "REQUEST_ERROR"

        result[
            "error"
        ] = str(e)


    except Exception as e:

        result[
            "classification"
        ] = "ERROR"

        result[
            "error"
        ] = repr(e)


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

    result[
        "elapsed_ms"
    ] = round(
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
        "get_status",

        "content_type",

        "content_length",
        "get_content_length",

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
        "average_bandwidths",

        "resolutions",

        "codecs",

        "frame_rates",

        "audio_groups",
        "video_groups",

        "subtitles",

        "target_duration",

        "media_sequence",

        "segment_count",

        "segment_durations",

        "uris",

        "raw_tags",

        "streams",

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

                "number":
                    item[
                        "number"
                    ],

                "filename":
                    item[
                        "filename"
                    ],

                "url":
                    item[
                        "url"
                    ],

                "status":
                    item[
                        "status"
                    ],

                "get_status":
                    item[
                        "get_status"
                    ],

                "content_type":
                    item[
                        "content_type"
                    ],

                "content_length":
                    item[
                        "content_length"
                    ],

                "get_content_length":
                    item[
                        "get_content_length"
                    ],

                "final_url":
                    item[
                        "final_url"
                    ],

                "redirect_chain":
                    "; ".join(
                        item[
                            "redirect_chain"
                        ]
                    ),

                "server":
                    item[
                        "server"
                    ],

                "via":
                    item[
                        "via"
                    ],

                "response_bytes":
                    item[
                        "response_bytes"
                    ],

                "elapsed_ms":
                    item[
                        "elapsed_ms"
                    ],

                "classification":
                    item[
                        "classification"
                    ],

                "valid_m3u8":
                    item[
                        "valid_m3u8"
                    ],

                "channel_name":
                    item[
                        "channel_name"
                    ],

                "channel_names":
                    "; ".join(
                        item[
                            "channel_names"
                        ]
                    ),

                "playlist_type":
                    playlist.get(
                        "playlist_type"
                    ),

                "is_master":
                    playlist.get(
                        "is_master"
                    ),

                "is_media":
                    playlist.get(
                        "is_media"
                    ),

                "bandwidths":
                    "; ".join(
                        map(
                            str,
                            playlist.get(
                                "bandwidths",
                                []
                            )
                        )
                    ),

                "average_bandwidths":
                    "; ".join(
                        map(
                            str,
                            playlist.get(
                                "average_bandwidths",
                                []
                            )
                        )
                    ),

                "resolutions":
                    "; ".join(
                        playlist.get(
                            "resolutions",
                            []
                        )
                    ),

                "codecs":
                    "; ".join(
                        playlist.get(
                            "codecs",
                            []
                        )
                    ),

                "frame_rates":
                    "; ".join(
                        map(
                            str,
                            playlist.get(
                                "frame_rates",
                                []
                            )
                        )
                    ),

                "audio_groups":
                    "; ".join(
                        playlist.get(
                            "audio_groups",
                            []
                        )
                    ),

                "video_groups":
                    "; ".join(
                        playlist.get(
                            "video_groups",
                            []
                        )
                    ),

                "subtitles":
                    "; ".join(
                        playlist.get(
                            "subtitles",
                            []
                        )
                    ),

                "target_duration":
                    playlist.get(
                        "target_duration"
                    ),

                "media_sequence":
                    playlist.get(
                        "media_sequence"
                    ),

                "segment_count":
                    playlist.get(
                        "segment_count"
                    ),

                "segment_durations":
                    "; ".join(
                        map(
                            str,
                            playlist.get(
                                "segment_durations",
                                []
                            )
                        )
                    ),

                "uris":
                    "; ".join(
                        playlist.get(
                            "uris",
                            []
                        )
                    ),

                "raw_tags":
                    " | ".join(
                        playlist.get(
                            "raw_tags",
                            []
                        )
                    ),

                "streams":
                    json.dumps(
                        playlist.get(
                            "streams",
                            []
                        ),
                        ensure_ascii=False
                    ),

                "error":
                    item[
                        "error"
                    ]
            }

            writer.writerow(row)


# ============================================================
# JSON
# ============================================================

def save_json(results):

    data = {

        "scanner":
            "RTOCHKA / "
            "TELEVIZOR-24-TOCHKA "
            "M3U8 SCANNER",

        "base_url":
            BASE_URL,

        "referer":
            REFERER,

        "range": {
            "start":
                START,

            "end":
                END
        },

        "total":
            len(results),

        "generated_urls":
            END - START + 1,

        "results":
            results
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
#
# ВАЖНО:
# ВСЕ 2800 ССЫЛОК.
#
# Не только найденные.
# ============================================================

def save_m3u(results):

    result_by_number = {
        item[
            "number"
        ]: item
        for item in results
    }


    lines = [

        "#EXTM3U",

        'url-tvg="https://iptvx.one/EPG"',

        ""
    ]


    for number in range(
        START,
        END + 1
    ):

        item = result_by_number.get(
            number
        )


        # ----------------------------------------------------
        # Название только если сервер реально его сообщил.
        # ----------------------------------------------------

        name = None

        if item:

            name = item.get(
                "channel_name"
            )


        if not name:

            name = (
                f"Канал {number}"
            )


        display_name = (
            f"{number}.m3u8 — "
            f"{name}"
        )


        # ----------------------------------------------------
        # EXTINF
        # ----------------------------------------------------

        lines.append(
            '#EXTINF:-1 '
            'group-title="Televizor 24",'
            + display_name
        )


        # ----------------------------------------------------
        # HTTP REFERRER
        # ----------------------------------------------------

        lines.append(
            "#EXTVLCOPT:"
            "http-referrer="
            + REFERER
        )


        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        lines.append(
            make_url(number)
        )

        lines.append("")


    M3U_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# ============================================================
# MISSING / ERRORS
# ============================================================

def save_missing(results):

    missing = [
        x
        for x in results
        if not x[
            "valid_m3u8"
        ]
    ]


    lines = []

    for item in missing:

        lines.append(
            f'{item["number"]}.m3u8 | '
            f'{item["classification"]} | '
            f'HEAD={item["status"]} | '
            f'GET={item["get_status"]} | '
            f'{item["error"] or ""} | '
            f'{item["url"]}'
        )


    MISSING_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# ============================================================
# SUMMARY JSON
# ============================================================

def save_summary(results, elapsed):

    found = [
        x
        for x in results
        if x[
            "valid_m3u8"
        ]
    ]


    master = [
        x
        for x in found
        if x[
            "playlist"
        ].get(
            "is_master"
        )
    ]


    media = [
        x
        for x in found
        if x[
            "playlist"
        ].get(
            "is_media"
        )
    ]


    named = [
        x
        for x in found
        if x[
            "channel_name"
        ]
    ]


    classifications = {}

    for item in results:

        classification = (
            item[
                "classification"
            ]
            or "UNKNOWN"
        )

        classifications[
            classification
        ] = (
            classifications.get(
                classification,
                0
            ) + 1
        )


    summary = {

        "scanner":
            "RTOCHKA",

        "target":
            "TELEVIZOR-24-TOCHKA",

        "base_url":
            BASE_URL,

        "referer":
            REFERER,

        "start":
            START,

        "end":
            END,

        "generated_urls":
            END - START + 1,

        "checked":
            len(results),

        "valid_m3u8":
            len(found),

        "master":
            len(master),

        "media":
            len(media),

        "with_name":
            len(named),

        "without_name":
            len(found) - len(named),

        "not_valid_m3u8":
            len(results) - len(found),

        "classifications":
            classifications,

        "elapsed_seconds":
            round(
                elapsed,
                2
            )
    }


    SUMMARY_FILE.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# CONSOLE SUMMARY
# ============================================================

def print_result(item):

    number = item[
        "number"
    ]

    classification = item[
        "classification"
    ]


    if item[
        "valid_m3u8"
    ]:

        name = (
            item[
                "channel_name"
            ]
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
            "[%04d] %s | "
            "HEAD=%s | GET=%s",
            number,
            classification,
            item[
                "status"
            ],
            item[
                "get_status"
            ]
        )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "=============================================="
    )

    logger.info(
        "RTOCHKA"
    )

    logger.info(
        "TELEVIZOR-24-TOCHKA "
        "M3U8 SCANNER"
    )

    logger.info(
        "Диапазон: %d-%d",
        START,
        END
    )

    logger.info(
        "Всего URL будет сгенерировано: %d",
        END - START + 1
    )

    logger.info(
        "M3U8 читается полностью: YES"
    )

    logger.info(
        "Видеосегменты не скачиваются: YES"
    )

    logger.info(
        "HTTP Referrer: %s",
        REFERER
    )

    logger.info(
        "=============================================="
    )


    results = []

    started = time.perf_counter()


    # ========================================================
    # СКАНИРОВАНИЕ
    # ========================================================

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


        if DELAY > 0:

            time.sleep(
                DELAY
            )


    # ========================================================
    # СОРТИРОВКА
    # ========================================================

    results.sort(
        key=lambda x:
        x["number"]
    )


    # ========================================================
    # СОХРАНЕНИЕ
    # ========================================================

    save_json(
        results
    )

    save_csv(
        results
    )

    save_m3u(
        results
    )

    save_missing(
        results
    )


    # ========================================================
    # ИТОГ
    # ========================================================

    elapsed = (
        time.perf_counter()
        - started
    )


    found = [
        x
        for x in results
        if x[
            "valid_m3u8"
        ]
    ]


    master = [
        x
        for x in found
        if x[
            "playlist"
        ].get(
            "is_master"
        )
    ]


    media = [
        x
        for x in found
        if x[
            "playlist"
        ].get(
            "is_media"
        )
    ]


    named = [
        x
        for x in found
        if x[
            "channel_name"
        ]
    ]


    save_summary(
        results,
        elapsed
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
        "Сгенерировано URL:      %d",
        END - START + 1
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
        "Summary: %s",
        SUMMARY_FILE
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
            "Сканирование остановлено "
            "пользователем."
        )

    except Exception as e:

        logger.exception(
            "Критическая ошибка: %s",
            e
        )

        raise