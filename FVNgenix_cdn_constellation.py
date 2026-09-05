#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
       NGENIX CDN CONSTELLATION v4 ULTRA
          ngSKALA / ZOYE Discovery Engine
============================================================

DISCOVERY:
  • Только наблюдаемые/зафиксированные hostname.
  • Никакого перебора sXXXXX.
  • Никакого угадывания новых hostname.
  • observed hostname × observed channel aliases.
  • Сохраняются специальные ранее найденные paths.

VALIDATION:
  1. DNS
  2. TCP/443
  3. bounded HTTP GET
  4. STUB detection
  5. STUB response parsing
  6. извлечение фактически возвращённых NGENIX URL
  7. повторная HTTP verification
  8. HTTP 200 + #EXTM3U
  9. извлечение M3U8 metadata

PLAYLIST:
  • STUB сам в playlist НЕ попадает.
  • 404/AUTH/ERROR НЕ попадают.
  • Derived URL попадает только после VERIFIED.
  • Для VERIFIED название/группа берутся из manifest,
    если они там присутствуют.
  • Все STUB / rejected / derived результаты сохраняются
    в inventory.

SAFETY:
  • Нет hostname brute force.
  • Нет sXXXXX generation.
  • Нет authorization bypass.
  • Нет credential discovery.
  • Ограниченное чтение ответов.
  • Ограниченный concurrency.
  • Один HTTP request одновременно на hostname
    посредством host locks.
============================================================
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import socket
import ssl
import time

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)

from dataclasses import (
    asdict,
    dataclass,
    field,
)

from datetime import (
    datetime,
    timezone,
)

from pathlib import Path
from threading import Lock, Semaphore
from typing import Iterable

from urllib.error import (
    HTTPError,
    URLError,
)

from urllib.parse import (
    urljoin,
    urlparse,
)

from urllib.request import (
    Request,
    urlopen,
)


# ============================================================
# ENGINE
# ============================================================

ENGINE_NAME = "ngSKALA"
ENGINE_VERSION = "NGENIX-CONSTELLATION-v5-ULTRA-2-HTTPCLASS-DREG"
CONSTELLATION_NAME = "NGENIX CDN CONSTELLATION ULTRA"

OUTPUT_M3U = "NGENIX_CDN_CONSTELLATION_2.m3u"
OUTPUT_JSON = "NGENIX_CDN_CONSTELLATION_2.json"
OUTPUT_GRAPH = "NGENIX_CDN_CONSTELLATION_GRAPH_2.json"
OUTPUT_REPORT = "NGENIX_CDN_CONSTELLATION_SKALA_2.txt"
OUTPUT_HISTORY = "NGENIX_CDN_CONSTELLATION_HISTORY_2.json"
OUTPUT_CSV = "NGENIX_CDN_CONSTELLATION_2.csv"
OUTPUT_DREG = "NGENIX_CDN_CONSTELLATION_DREG_2.txt"
OUTPUT_SCALA = "NGENIX_CDN_CONSTELLATION_SCALA_2.txt"

DEFAULT_OUTPUT_DIR = Path(
    "data/ngenix_constellation"
)

DEFAULT_REPORT_DIR = Path(
    "reports/ngenix_constellation"
)


# ============================================================
# SAFETY / BOUNDS
# ============================================================

DEFAULT_TIMEOUT = 5.0
DEFAULT_READ_LIMIT = 65536

DEFAULT_WORKERS = 16
MAX_WORKERS = 32
MIN_WORKERS = 8

DEFAULT_REQUEST_DELAY = 0.0
HOST_MIN_INTERVAL = 0.12
HOST_INFLIGHT = 2


# ============================================================
# REGEX
# ============================================================

HOST_RE = re.compile(
    r"https?://"
    r"(?P<host>[a-z0-9][a-z0-9.-]*?)"
    r"\.cdn\.ngenix\.net"
    r"(?P<path>/[^\s\"'<>]*)?",
    re.IGNORECASE,
)

SERVICE_RE = re.compile(
    r"(?<![a-z0-9-])"
    r"(s\d{5,})"
    r"(?![a-z0-9-])",
    re.IGNORECASE,
)

ACCOUNT_SERVICE_RE = re.compile(
    r"(a\d+-s\d{5,})",
    re.IGNORECASE,
)

SUPPORTED_EXTENSIONS = {
    ".m3u",
    ".m3u8",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".conf",
    ".cfg",
    ".ini",
    ".log",
    ".csv",
}


# ============================================================
# SPECIAL HOST
# ============================================================

STUB_HOST = (
    "zabava-block-htvod.cdn.ngenix.net"
)


# ============================================================
# USER AGENTS
# ============================================================

USER_AGENTS = [
    "ngSKALA-NGENIX-CONSTELLATION/4.0",
    "WINK/RT (Android)",
    "WINK/1.40.1 (AndroidTV/9) HlsWinkPlayer",
    "SmartLabs",
]


# ============================================================
# OBSERVED S-HOSTS
# ============================================================

SEED_S_HOSTS = [
    "s70378",
    "s91030",
    "s14131",
    "s20441",
    "s25617",
    "s26881",
    "s34351",
    "s37630",
    "s45177",
    "s55766",
    "s68149",
    "s78511",
    "s80718",
    "s97982",
    "s18209",
    "s92263",
    "s34776",
    "s12662",
    "s69362",
    "s12917",
    "s13511",
    "s14553",
    "s27836",
    "s68400",
    "s79369",
    "s80078",
    "s81121",
    "s84942",
    "s22674",
    "s35761",
    "s40403",
    "s41654",
    "s42963",
    "s64022",
    "s68717",
    "s70205",
    "s72169",
    "s73767",
    "s74794",
    "s95979",
    "s98217",
]


# ============================================================
# OBSERVED NAMED HOSTS
# ============================================================

SEED_NAMED_HOSTS = [
    "zabava-htlive",
    "zabava-htvod",
    "zabava-block-htvod",
    "kprf-htlive",
    "tvgubernia-htlive",
    "vgtrk-htvod",
    "ct-cdn",
    "mos-cdn",
    "rt-mos-htlive",
    "rt-nw-spb-htlive",
    "rt-nw-klgr-htlive",
    "rt-nw-pzav-htlive",
    "rt-nw-komi-htlive",
    "rt-nw-arkh-htlive",
    "rt-nw-vol-htlive",
    "rt-nw-kostroma-htlive",
    "rt-nw-novg-htlive",
    "rt-nw-murm-htlive",
    "rt-ct-tver-htlive",
    "rt-ct-orl-htlive",
    "rt-ct-bryansk-htlive",
    "rt-ct-tula-htlive",
    "rt-ct-yarl-htlive",
    "rt-ct-vlad-htlive",
    "rt-ct-ivan-htlive",
    "rt-ct-belg-htlive",
    "rt-ct-lipetsk-htlive",
    "rt-ct-ryaz-htlive",
    "rt-ct-vrzh-htlive",
    "rt-ct-kursk-htlive",
    "rt-ct-tamb-htlive",
    "rt-vlg-nn-htlive",
    "rt-vlg-samara-htlive",
    "rt-vlg-ul-htlive",
    "rt-vlg-saratov-htlive",
    "rt-vlg-kzn-htlive",
    "rt-vlg-penza-htlive",
    "rt-vlg-chr-htlive",
    "rt-vlg-kirov-htlive",
    "rt-vlg-izhsk-htlive",
    "rt-vlg-srnk-htlive",
    "rt-vlg-yola-htlive",
    "rt-ural-ekt-htlive",
    "rt-ural-chel-htlive",
    "rt-ural-sur-htlive",
    "rt-ural-tum-htlive",
    "rt-sib-omsk-htlive",
    "rt-sib-irk-htlive",
    "rt-sib-krsk-htlive",
    "rt-sib-nsk-htlive",
    "rt-sib-kem-htlive",
    "rt-sib-uude-htlive",
    "rt-sib-abakan-htlive",
    "rt-sib-bul-htlive",
    "rt-sth-krdar-htlive",
    "rt-sth-rd-htlive",
    "rt-sth-elista-htlive",
    "rt-sth-cherks-htlive",
    "rt-sth-vgrad-htlive",
]


# ============================================================
# OBSERVED LB SUFFIXES
# ============================================================

SEED_LB_SUFFIXES = [
    "rt-sib-omsk-htlive-lb",
    "rt-ural-chel-htlive-lb",
    "rt-vlg-nn-htlive-lb",
    "rt-vlg-samara-htlive-lb",
    "rt-ct-tver-htlive-lb",
    "rt-vlg-kirov-htlive-lb",
    "rt-sib-krsk-htlive-lb",
    "rt-nw-komi-htlive-lb",
    "rt-ct-bryansk-htlive-lb",
    "rt-sib-kem-htlive-lb",
    "rt-sth-krdar-htlive-lb",
]


# ============================================================
# OBSERVED ACCOUNT HOSTS
# ============================================================

SEED_ACCOUNT_HOSTS = [
    "a3569457567-s70378",
    "a3569457435-s78511",
    "a3569458063-s26881",
    "a3569455801-s26881",
    "a3569455919-s26881",
    "a3569458298-s26881",
    "a3569458677-zabava-htlive",
    "a3569458686-zabava-htlive",
    "a787200757-zabava-htlive",
    "a1566399135-s27836",
    "a1566400063-s27836",
    "a1311338307-s26881",
    "a1311338266-vgtrk-htvod",
    "a3569458506-s22674",
    "a3569457538-s72169",
    "a3569455668-s95979",
    "a3569458353-s98217",
    "a3569458406-s81121",
    "a635215904-s73767",
    "a1566400203-s35761",
    "a1566398612-s40403",
    "a775797930-rt-vlg-penza-htlive",
    "a787200748-rt-ct-kostroma-htlive",
    "a787201926-s78511",
    "a3569457767-s70378",
    "a3285275841-s70378",
    "a787200760-s91030",
    "a635216794-s91030",
    "a3285274823-s14131",
    "a3569455826-s14131",
    "a3285275592-s97982",
]


# ============================================================
# OBSERVED CHANNEL ALIASES
# ============================================================

CHANNEL_ALIASES = [
    "365_dney_tv",
    "amc",
    "amedia_1",
    "amedia_2",
    "amedia_hit",
    "amedia_premium_hd",
    "atv",
    "baby_tv",
    "bazmoc",
    "curiosity_s",
    "da_vinci",
    "dar21",
    "docubox",
    "dom_kino",
    "dom_kino_pr",
    "dom_kino_premium_hd",
    "ducktv",
    "erox",
    "euronews",
    "evrokino",
    "fashion_tv",
    "fightbox",
    "filmbox",
    "filmbox_arthouse",
    "filmzone",
    "flixsnip",
    "galaxy",
    "gulli",
    "h1",
    "h2",
    "hd_life",
    "history",
    "history_2",
    "illusion_pl",
    "illusion_plus",
    "karusel",
    "kentron",
    "kinoklub",
    "kinouzhas",
    "kitchen_tv",
    "kvn_tv",
    "kxl",
    "match_plane",
    "match_planeta",
    "mezzo",
    "mir",
    "mir_seriala",
    "mnogo_tv",
    "nashe_novoe",
    "nickelodeon",
    "nicktoons",
    "nostalgia",
    "ntv_pravo",
    "ntv_serial",
    "ocean_tv",
    "playboy",
    "rbc",
    "ren_tv",
    "rtr_planeta",
    "rtvi",
    "shant",
    "sony_channel",
    "sony_sci_fi",
    "sony_turbo",
    "telecafe",
    "terra",
    "tiji",
    "tnt_4",
    "tnt_music",
    "trace_sport",
    "trace_sport_stars",
    "tv5_monde",
    "tv_xxi",
    "viasat_explore",
    "viasat_history",
    "viasat_nature",
    "viasat_sport",
    "vip_comedy",
    "vip_megahit",
    "vip_premiere",
    "vip_serial",
    "zee_tv",
    "zoopark",
]


# ============================================================
# GENERIC OBSERVED PATHS
# ============================================================

SEED_PATHS_GENERIC = [
    "/hls/CH_1TVSD/variant.m3u8",
    "/hls/CH_1TV/variant.m3u8",
    "/hls/CH_RUSSIA1/variant.m3u8",
    "/hls/CH_NTV/variant.m3u8",
    "/hls/CH_5TV/variant.m3u8",
    "/hls/CH_MATCHTV/variant.m3u8",
    "/hls/CH_STS/variant.m3u8",
    "/hls/CH_TNT/variant.m3u8",
    "/hls/CH_KARUSEL/variant.m3u8",
    "/hls/CH_2X2/variant.m3u8",
    "/hls/CH_RUSSIAK/variant.m3u8",
    "/hls/CH_RUSSIA24/variant.m3u8",
    "/hls/CH_PYATNIZZA/variant.m3u8",
    "/hls/CH_DOMASHNIY/variant.m3u8",
    "/hls/CH_PERETZ/variant.m3u8",
    "/hls/CH_TVC/variant.m3u8",
    "/hls/CH_MIR/variant.m3u8",
    "/hls/CH_ZVEZDA/variant.m3u8",
    "/hls/CH_OTR/variant.m3u8",
    "/hls/CH_SUPER/variant.m3u8",
    "/hls/CH_SPAS/variant.m3u8",
    "/hls/CH_DISNEY/variant.m3u8",
    "/hls/CH_TV3/variant.m3u8",
    "/hls/CH_RENTV/variant.m3u8",
    "/hls/CH_CHE/variant.m3u8",
    "/hls/CH_MUZTV/variant.m3u8",
    "/hls/CH_TNT4/variant.m3u8",
    "/hls/CH_NTV_HD/variant.m3u8",
    "/hls/CH_TNTHD/variant.m3u8",
    "/hls/CH_C05_RUSSIA1HD/variant.m3u8",
    "/hls/CH_C03_IZVESTIYAHD/variant.m3u8",
    "/hls/CH_PODMOSKOVIEHD/variant.m3u8",
    "/hls/CH_MOSKVA24HD/variant.m3u8",
    "/hls/CH_VSETVHD/variant.m3u8",
    "/hls/CH_AIVAHD/variant.m3u8",
    "/hls/CH_WEAPON/variant.m3u8",
    "/hls/CH_OHOTAIRYBALKS/variant.m3u8",
    "/hls/CH_STSLOVE/variant.m3u8",
    "/hls/CH_U/variant.m3u8",
    "/hls/CH_CGTNRUS/variant.m3u8",
    "/hls/CH_FUTBALL1HD/variant.m3u8",
    "/index.m3u8",
    "/variant.m3u8",
    "/playlist.m3u8",
    "/rtk_block.m3u8",
]


# ============================================================
# OBSERVED SPECIAL PATHS
# ============================================================

SEED_PATHS_S70378 = [
    "/amedia_premium_hd/3/index.m3u8",
    "/dom_kino_premium_hd/3/index.m3u8",
    "/telecafe/2/index.m3u8",
    "/vremia/2/index.m3u8",
    "/detskij_mir/2/index.m3u8",
    "/da_vinci/2/index.m3u8",
    "/filmzone/index.m3u8",
    "/kinoklub/index.m3u8",
    "/glazami_turista/1/index.m3u8",
    "/galaxy/2/index.m3u8",
    "/rtg_hd/3/index.m3u8",
]


SEED_PATHS_SPECIAL = {
    "s55766.cdn.ngenix.net": [
        "/s55766-media-origin/rline_high/tracks-v1a1/mono.m3u8",
        "/s55766-media-origin/rline_high/index.m3u8",
    ],

    "s68149.cdn.ngenix.net": [
        "/s68149-media-origin/lvs/tvgub/tracks-v1a1/mono.m3u8",
    ],

    "s78511.cdn.ngenix.net": [
        "/open/_definst_/TVRain_noaudio/chunklist_DVR.m3u8",
    ],

    "s26881.cdn.ngenix.net": [
        "/live/smil:russiak.smil/chunklist_b1600000.m3u8",
    ],

    "s80718.cdn.ngenix.net": [
        "/hls/CH_KINOMANHD/variant.m3u8",
    ],

    "s27836.cdn.ngenix.net": [
        "/hls/radio_rus/playlist_3.m3u8",
    ],

    "s92263.cdn.ngenix.net": [
        "/hls-live/streams/channelone/channelone.m3u8",
    ],

    "kprf-htlive.cdn.ngenix.net": [
        "/live/_definst_/stream_high/playlist.m3u8?version=2",
    ],

    "tvgubernia-htlive.cdn.ngenix.net": [
        "/live/mp4:tv-gubernia-live/playlist.m3u8",
    ],

    "zabava-block-htvod.cdn.ngenix.net": [
        "/rtk_block.m3u8",
    ],
}


# ============================================================
# DERIVED URL EXTRACTION
# ============================================================

DERIVED_URL_RE = re.compile(
    r'https?://'
    r'[a-z0-9][a-z0-9.-]*'
    r'\.cdn\.ngenix\.net'
    r'(?:/[^\s<>"\'\\]+)?',
    re.IGNORECASE,
)

RELATIVE_M3U8_RE = re.compile(
    r'(?<![A-Za-z0-9])'
    r'(?:/[A-Za-z0-9._~!$&()*+,;=:@%/?#-]+\.m3u8'
    r'(?:\?[^\s<>"\']*)?)',
    re.IGNORECASE,
)



# ============================================================
# HTTP / NETWORK RESPONSE CLASSIFICATION
# Русские + английские обозначения. Коды сохраняются дословно.
# ============================================================

HTTP_CLASSIFICATION = {
    200: ("ONLINE", "ЖИВОЙ / УСПЕШНЫЙ", "Successful manifest/stream response"),
    201: ("CREATED", "СОЗДАН", "Resource created"),
    202: ("ACCEPTED", "ПРИНЯТ", "Request accepted for processing"),
    204: ("NO_CONTENT", "НЕТ СОДЕРЖИМОГО", "Successful response without body"),

    301: ("REDIRECT_PERMANENT", "ПОСТОЯННАЯ ПЕРЕАДРЕСАЦИЯ", "Permanent redirect"),
    302: ("REDIRECT_TEMPORARY", "ВРЕМЕННАЯ ПЕРЕАДРЕСАЦИЯ", "Temporary redirect"),
    303: ("REDIRECT_SEE_OTHER", "ПЕРЕАДРЕСАЦИЯ SEE OTHER", "See other resource"),
    307: ("REDIRECT_TEMPORARY_PRESERVE_METHOD", "ВРЕМЕННАЯ ПЕРЕАДРЕСАЦИЯ", "Temporary redirect, method preserved"),
    308: ("REDIRECT_PERMANENT_PRESERVE_METHOD", "ПОСТОЯННАЯ ПЕРЕАДРЕСАЦИЯ", "Permanent redirect, method preserved"),

    400: ("BAD_REQUEST", "ОШИБКА ЗАПРОСА", "Malformed or invalid request"),
    401: ("UNAUTHORIZED", "ТРЕБУЕТСЯ АВТОРИЗАЦИЯ", "Authentication required"),
    402: ("PAYMENT_REQUIRED", "ТРЕБУЕТСЯ ОПЛАТА", "Payment required"),
    403: ("FORBIDDEN", "ДОСТУП ЗАПРЕЩЁН", "Access forbidden"),
    404: ("NOT_FOUND", "НЕ НАЙДЕНО", "Resource not found"),
    405: ("METHOD_NOT_ALLOWED", "МЕТОД ЗАПРЕЩЁН", "HTTP method not allowed"),
    406: ("NOT_ACCEPTABLE", "НЕПРИЕМЛЕМО", "Requested representation not acceptable"),
    407: ("PROXY_AUTH_REQUIRED", "ТРЕБУЕТСЯ АВТОРИЗАЦИЯ ПРОКСИ", "Proxy authentication required"),
    408: ("REQUEST_TIMEOUT", "ТАЙМАУТ ЗАПРОСА", "Server-side request timeout"),
    409: ("CONFLICT", "КОНФЛИКТ", "Request conflicts with resource state"),
    410: ("GONE", "УДАЛЕНО", "Resource permanently gone"),
    411: ("LENGTH_REQUIRED", "ТРЕБУЕТСЯ CONTENT-LENGTH", "Length required"),
    412: ("PRECONDITION_FAILED", "ПРЕДУСЛОВИЕ НЕ ВЫПОЛНЕНО", "Precondition failed"),
    413: ("PAYLOAD_TOO_LARGE", "ЗАПРОС СЛИШКОМ БОЛЬШОЙ", "Payload too large"),
    414: ("URI_TOO_LONG", "URI СЛИШКОМ ДЛИННЫЙ", "URI too long"),
    415: ("UNSUPPORTED_MEDIA_TYPE", "НЕПОДДЕРЖИВАЕМЫЙ ТИП", "Unsupported media type"),
    416: ("RANGE_NOT_SATISFIABLE", "RANGE НЕВОЗМОЖЕН", "Requested range cannot be satisfied"),
    417: ("EXPECTATION_FAILED", "ОЖИДАНИЕ НЕ ВЫПОЛНЕНО", "Expectation failed"),
    418: ("IM_A_TEAPOT", "TEAPOT", "Non-standard response"),
    421: ("MISDIRECTED_REQUEST", "ЗАПРОС НАПРАВЛЕН НЕ ТУДА", "Misdirected request"),
    422: ("UNPROCESSABLE_CONTENT", "НЕОБРАБАТЫВАЕМОЕ СОДЕРЖИМОЕ", "Unprocessable content"),
    423: ("LOCKED", "ЗАБЛОКИРОВАНО", "Resource locked"),
    424: ("FAILED_DEPENDENCY", "ОШИБКА ЗАВИСИМОСТИ", "Failed dependency"),
    425: ("TOO_EARLY", "СЛИШКОМ РАНО", "Too early"),
    426: ("UPGRADE_REQUIRED", "ТРЕБУЕТСЯ ОБНОВЛЕНИЕ ПРОТОКОЛА", "Upgrade required"),
    428: ("PRECONDITION_REQUIRED", "ТРЕБУЕТСЯ ПРЕДУСЛОВИЕ", "Precondition required"),
    429: ("TOO_MANY_REQUESTS", "СЛИШКОМ МНОГО ЗАПРОСОВ", "Rate limit / throttling"),
    431: ("REQUEST_HEADER_FIELDS_TOO_LARGE", "ЗАГОЛОВКИ СЛИШКОМ БОЛЬШИЕ", "Request headers too large"),
    451: ("UNAVAILABLE_FOR_LEGAL_REASONS", "НЕДОСТУПНО ПО ЮРИДИЧЕСКИМ ПРИЧИНАМ", "Unavailable for legal reasons"),

    500: ("INTERNAL_SERVER_ERROR", "ВНУТРЕННЯЯ ОШИБКА СЕРВЕРА", "Server error"),
    501: ("NOT_IMPLEMENTED", "НЕ РЕАЛИЗОВАНО", "Not implemented"),
    502: ("BAD_GATEWAY", "ОШИБКА ШЛЮЗА", "Gateway received invalid upstream response"),
    503: ("SERVICE_UNAVAILABLE", "СЕРВИС НЕДОСТУПЕН", "Service temporarily unavailable; NOT itself proof of redirect"),
    504: ("GATEWAY_TIMEOUT", "ТАЙМАУТ ШЛЮЗА", "Gateway/upstream timeout"),
    505: ("HTTP_VERSION_NOT_SUPPORTED", "ВЕРСИЯ HTTP НЕ ПОДДЕРЖИВАЕТСЯ", "HTTP version unsupported"),
    506: ("VARIANT_ALSO_NEGOTIATES", "ОШИБКА СОГЛАСОВАНИЯ ВАРИАНТА", "Variant negotiation error"),
    507: ("INSUFFICIENT_STORAGE", "НЕДОСТАТОЧНО ХРАНИЛИЩА", "Insufficient storage"),
    508: ("LOOP_DETECTED", "ОБНАРУЖЕН ЦИКЛ", "Loop detected"),
    510: ("NOT_EXTENDED", "РАСШИРЕНИЕ ТРЕБУЕТСЯ", "Further extensions required"),
    511: ("NETWORK_AUTH_REQUIRED", "ТРЕБУЕТСЯ СЕТЕВАЯ АВТОРИЗАЦИЯ", "Network authentication required"),
}

# Классифицирует HTTP-код и возвращает машинное имя, русское описание и пояснение.
def classify_http_status(status: int | None) -> tuple[str, str, str]:
    if status is None:
        return ("NO_HTTP_STATUS", "НЕТ HTTP-ОТВЕТА", "No HTTP status received")
    if status in HTTP_CLASSIFICATION:
        return HTTP_CLASSIFICATION[status]
    if 100 <= status < 200:
        return ("HTTP_1XX", "ИНФОРМАЦИОННЫЙ ОТВЕТ", "Informational response")
    if 200 <= status < 300:
        return ("HTTP_2XX", "УСПЕШНЫЙ ОТВЕТ", "Successful response")
    if 300 <= status < 400:
        return ("HTTP_3XX", "ПЕРЕАДРЕСАЦИЯ", "Redirection response")
    if 400 <= status < 500:
        return ("HTTP_4XX", "ОШИБКА КЛИЕНТА", "Client error")
    if 500 <= status < 600:
        return ("HTTP_5XX", "ОШИБКА СЕРВЕРА", "Server error")
    return ("HTTP_UNKNOWN", "НЕИЗВЕСТНЫЙ HTTP-КОД", "Unknown HTTP status")

# Классифицирует сетевое исключение и возвращает машинный код и русское описание ошибки.
def classify_transport_error(exc: BaseException) -> tuple[str, str]:
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, socket.timeout) or "timed out" in low:
        return ("TIMEOUT", "ТАЙМАУТ")
    if isinstance(exc, ssl.SSLError):
        return ("TLS_ERROR", "ОШИБКА TLS/SSL")
    if isinstance(exc, socket.gaierror):
        return ("DNS_ERROR", "ОШИБКА DNS")
    if isinstance(exc, ConnectionRefusedError):
        return ("CONNECTION_REFUSED", "СОЕДИНЕНИЕ ОТКЛОНЕНО")
    if isinstance(exc, ConnectionResetError):
        return ("CONNECTION_RESET", "СОЕДИНЕНИЕ СБРОШЕНО")
    if isinstance(exc, ConnectionError):
        return ("CONNECTION_ERROR", "ОШИБКА СОЕДИНЕНИЯ")
    if isinstance(exc, URLError):
        return ("URL_ERROR", "ОШИБКА URL/СЕТИ")
    return (name.upper(), "ОШИБКА СЕТИ/ВЫПОЛНЕНИЯ")

# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class StreamEntry:

    url: str

    hostname: str
    service_id: str | None
    account_id: str | None
    hostname_type: str

    path: str
    channel: str | None
    variant: str | None

    name: str | None
    group: str | None

    source: str

    node_status: str = "not_checked"
    node_ips: list[str] = field(
        default_factory=list
    )
    node_latency_ms: float | None = None
    node_error: str | None = None

    stream_status: str = "not_checked"

    http_status: int | None = None

    stream_latency_ms: float | None = None

    stream_content_type: str | None = None

    stream_bytes_read: int = 0

    stream_error: str | None = None

    # --------------------------------------------------------
    # STUB
    # --------------------------------------------------------

    is_stub: bool = False

    stub_target: str | None = None

    derived_urls: list[str] = field(
        default_factory=list
    )

    # --------------------------------------------------------
    # SECOND-STAGE VERIFICATION
    # --------------------------------------------------------

    verification_status: str = (
        "not_checked"
    )

    verification_http_status: int | None = None

    verification_content_type: str | None = None

    verification_bytes_read: int = 0

    verification_latency_ms: float | None = None

    verification_error: str | None = None

    # --------------------------------------------------------
    # MANIFEST METADATA
    # --------------------------------------------------------

    manifest_name: str | None = None

    manifest_group: str | None = None

    manifest_metadata: dict = field(
        default_factory=dict
    )

    # --------------------------------------------------------
    # TIMESTAMPS
    # --------------------------------------------------------

    first_seen: str | None = None

    last_seen: str | None = None


# ============================================================
# GLOBAL REQUEST PACER
# ============================================================

class RequestPacer:

    # Инициализирует объект ограничителя или планировщика запросов.
    def __init__(
        self,
        delay: float,
    ):

        self.delay = max(
            0.0,
            delay,
        )

        self.lock = Lock()

        self.last_request = 0.0

    # Ожидает необходимую паузу глобального планировщика перед следующим запросом.
    def wait(self) -> None:

        if self.delay <= 0:
            return

        with self.lock:

            now = time.monotonic()

            wait_for = (
                self.delay
                - (
                    now
                    - self.last_request
                )
            )

            if wait_for > 0:
                time.sleep(
                    wait_for
                )

            self.last_request = (
                time.monotonic()
            )


# ============================================================
# HOST LIMITERS
# ============================================================

class HostLimiter:

    # Инициализирует объект ограничителя или планировщика запросов.
    def __init__(self, inflight: int, min_interval: float):
        self.semaphore = Semaphore(max(1, inflight))
        self.min_interval = max(0.0, min_interval)
        self.lock = Lock()
        self.last_start = 0.0

    # Занимает слот хоста и соблюдает минимальный интервал между стартами запросов.
    def enter(self) -> None:
        self.semaphore.acquire()
        with self.lock:
            now = time.monotonic()
            wait_for = self.min_interval - (now - self.last_start)
            if wait_for > 0:
                time.sleep(wait_for)
            self.last_start = time.monotonic()

    # Освобождает занятый слот хоста после завершения операции.
    def leave(self) -> None:
        self.semaphore.release()

_HOST_LIMITERS: dict[str, HostLimiter] = {}
_HOST_LIMITERS_GUARD = Lock()

# Возвращает общий ограничитель запросов для указанного hostname.
def get_host_limiter(hostname: str) -> HostLimiter:
    with _HOST_LIMITERS_GUARD:
        if hostname not in _HOST_LIMITERS:
            _HOST_LIMITERS[hostname] = HostLimiter(
                HOST_INFLIGHT, HOST_MIN_INTERVAL
            )
        return _HOST_LIMITERS[hostname]


# ============================================================
# BASIC HELPERS
# ============================================================

# Возвращает текущее время в UTC в ISO-формате.
def utc_now() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


# Возвращает текущее время в часовом поясе UTC+03:00 в ISO-формате.
def msk_now() -> str:

    from datetime import timedelta, timezone as _timezone

    return datetime.now(
        _timezone(timedelta(hours=3))
    ).isoformat(timespec="milliseconds")


@dataclass
class TelemetryEvent:

    timestamp_start: str
    timestamp_end: str
    duration_ms: float
    operation: str
    suboperation: str
    node: str
    alias: str
    path: str
    result: str
    result_ru: str = ""
    result_en: str = ""
    http_status: int | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)


_TELEMETRY: list[TelemetryEvent] = []
_TELEMETRY_LOCK = Lock()


# Создаёт событие телеметрии, классифицирует результат и добавляет его в общий журнал.
def record_telemetry(
    started_monotonic: float,
    operation: str,
    suboperation: str,
    node: str = "",
    alias: str = "",
    path: str = "",
    result: str = "",
    http_status: int | None = None,
    error: str | None = None,
    extra: dict | None = None,
) -> None:

    finished = time.perf_counter()
    code_name, code_ru, code_en = classify_http_status(http_status)
    result_ru = code_ru if http_status is not None else result
    result_en = code_name if http_status is not None else result
    event = TelemetryEvent(
        timestamp_start=msk_now(),
        timestamp_end=msk_now(),
        duration_ms=round((finished - started_monotonic) * 1000, 2),
        operation=operation,
        suboperation=suboperation,
        node=node,
        alias=alias,
        path=path,
        result=result or code_name,
        result_ru=result_ru,
        result_en=result_en,
        http_status=http_status,
        error=error,
        extra=extra or {},
    )
    with _TELEMETRY_LOCK:
        _TELEMETRY.append(event)


# Приводит имя хоста к полному имени в зоне cdn.ngenix.net.
def fqdn(
    label: str,
) -> str:

    label = (
        label
        .lower()
        .strip()
    )

    if label.endswith(
        ".cdn.ngenix.net"
    ):
        return label

    return (
        f"{label}.cdn.ngenix.net"
    )


# Извлекает идентификатор sXXXXX из hostname, если он присутствует.
def extract_service_id(
    hostname: str,
) -> str | None:

    match = SERVICE_RE.search(
        hostname
    )

    if match:
        return match.group(
            1
        ).lower()

    return None


# Извлекает идентификатор аккаунта aXXXXXXXX из hostname.
def extract_account_id(
    hostname: str,
) -> str | None:

    match = re.search(
        r"(a\d+)-",
        hostname,
        re.IGNORECASE,
    )

    if match:
        return match.group(
            1
        ).lower()

    return None


# Определяет тип наблюдаемого hostname по его структуре.
def classify_hostname(
    hostname: str,
) -> str:

    hostname = hostname.lower()

    if hostname == STUB_HOST:
        return "stub"

    if ACCOUNT_SERVICE_RE.search(
        hostname
    ):
        return "account_service"

    if SERVICE_RE.search(
        hostname
    ):
        return "service"

    if "htvod" in hostname:
        return "named_htvod"

    if "htlive" in hostname:
        return "named_htlive"

    return "named_cdn"


# Извлекает имя канала из пути потока.
def extract_channel(
    path: str,
) -> str | None:

    parts = [
        x
        for x in path.split("/")
        if x
    ]

    if not parts:
        return None

    if (
        parts[0].lower() == "hls"
        and len(parts) > 1
    ):
        return parts[1]

    return parts[0]


# Извлекает вариант потока из пути.
def extract_variant(
    path: str,
) -> str | None:

    parts = [
        x
        for x in path.split("/")
        if x
    ]

    if len(parts) < 2:
        return None

    filename = (
        parts[-1]
        .lower()
        .split("?")[0]
    )

    if filename in {
        "index.m3u8",
        "variant.m3u8",
        "master.m3u8",
        "playlist.m3u8",
        "mono.m3u8",
    }:

        return parts[-2]

    return None


# Разбирает строку #EXTINF и извлекает название и группу канала.
def parse_extinf(
    line: str,
) -> tuple[
    str | None,
    str | None,
]:

    name = None
    group = None

    comma = line.find(",")

    if comma >= 0:

        name = (
            line[
                comma + 1:
            ].strip()
        )

    match = re.search(
        r'group-title="([^"]*)"',
        line,
        re.IGNORECASE,
    )

    if match:

        group = (
            match.group(1)
        )

    return name, group


# ============================================================
# ENTRY CREATION
# ============================================================

# Создаёт объект StreamEntry из URL и связанных метаданных, отбрасывая неподходящие URL.
def make_entry(
    url: str,
    source: str,
    name: str | None = None,
    group: str | None = None,
) -> StreamEntry | None:

    try:

        parsed = urlparse(url)

        hostname = (
            parsed.hostname
            or ""
        ).lower()

        if not hostname.endswith(
            ".cdn.ngenix.net"
        ):
            return None

        path = (
            parsed.path
            or "/"
        )

        if parsed.query:

            path = (
                f"{path}"
                f"?{parsed.query}"
            )

        now = utc_now()

        return StreamEntry(

            url=url,

            hostname=hostname,

            service_id=(
                extract_service_id(
                    hostname
                )
            ),

            account_id=(
                extract_account_id(
                    hostname
                )
            ),

            hostname_type=(
                classify_hostname(
                    hostname
                )
            ),

            path=path,

            channel=(
                extract_channel(
                    parsed.path
                    or "/"
                )
            ),

            variant=(
                extract_variant(
                    parsed.path
                    or "/"
                )
            ),

            name=name,

            group=group,

            source=source,

            first_seen=now,

            last_seen=now,
        )

    except Exception:

        return None


# ============================================================
# M3U PARSER
# ============================================================

# Разбирает M3U-текст и формирует список наблюдаемых записей потоков.
def parse_m3u(
    text: str,
    source: str,
) -> list[StreamEntry]:

    entries = []

    current_name = None
    current_group = None

    for raw in text.splitlines():

        line = raw.strip()

        if not line:
            continue

        if line.startswith(
            "#EXTINF"
        ):

            (
                current_name,
                current_group,
            ) = parse_extinf(
                line
            )

            continue

        if line.startswith("#"):
            continue

        if not line.lower().startswith(
            (
                "http://",
                "https://",
            )
        ):
            continue

        if not HOST_RE.search(
            line
        ):
            continue

        url = line.rstrip(
            ".,;)]}>\"'"
        )

        item = make_entry(
            url,
            source,
            current_name,
            current_group,
        )

        if item:
            entries.append(
                item
            )

        current_name = None
        current_group = None

    return entries


# ============================================================
# TEXT DISCOVERY
# ============================================================

# Находит NGENIX URL внутри произвольного текста и преобразует их в записи.
def discover_urls_in_text(
    text: str,
    source: str,
) -> list[StreamEntry]:

    entries = []

    for match in HOST_RE.finditer(
        text
    ):

        url = match.group(
            0
        ).rstrip(
            ".,;)]}>\"'"
        )

        item = make_entry(
            url,
            source,
        )

        if item:
            entries.append(
                item
            )

    return entries


# ============================================================
# OPTIONAL REPOSITORY DISCOVERY
# ============================================================

# Просматривает поддерживаемые файлы репозитория и собирает наблюдаемые NGENIX URL.
def discover_repository(
    root: Path,
) -> list[StreamEntry]:

    entries = []

    files_seen = 0

    print()
    print("=" * 70)
    print(
        " PHASE 0 / OPTIONAL REPOSITORY DISCOVERY"
    )
    print("=" * 70)

    for path in root.rglob("*"):

        if not path.is_file():
            continue

        if (
            path.suffix.lower()
            not in SUPPORTED_EXTENSIONS
        ):
            continue

        if (
            "ngenix_constellation"
            in path.parts
        ):
            continue

        files_seen += 1

        try:

            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )

        except Exception as exc:

            print(
                f"[READ-ERROR] "
                f"{path}: {exc}"
            )

            continue

        if (
            path.suffix.lower()
            in {
                ".m3u",
                ".m3u8",
            }
        ):

            found = parse_m3u(
                text,
                str(path),
            )

        else:

            found = (
                discover_urls_in_text(
                    text,
                    str(path),
                )
            )

        if found:

            print(
                f"[DISCOVERY] "
                f"{path} -> "
                f"{len(found)} endpoints"
            )

            entries.extend(
                found
            )

    print(
        f"[DISCOVERY] files: "
        f"{files_seen}"
    )

    print(
        f"[DISCOVERY] raw endpoints: "
        f"{len(entries)}"
    )

    return entries


# ============================================================
# OBSERVED HOSTS
# ============================================================

# Формирует объединённый список ранее наблюдавшихся hostname без их генерации.
def observed_hosts() -> list[str]:

    labels = (
        SEED_S_HOSTS
        + SEED_NAMED_HOSTS
        + SEED_LB_SUFFIXES
        + SEED_ACCOUNT_HOSTS
    )

    result = []

    seen = set()

    for label in labels:

        host = fqdn(
            label
        )

        if host in seen:
            continue

        seen.add(host)

        result.append(
            host
        )

    return sorted(
        result
    )


# ============================================================
# OBSERVED HOST × ALIAS
# ============================================================

# Строит матрицу наблюдаемых хостов и наблюдаемых алиасов каналов.
def build_alias_matrix() -> list[StreamEntry]:

    entries = []

    hosts = observed_hosts()

    aliases = list(
        dict.fromkeys(
            alias.lower().strip()
            for alias in CHANNEL_ALIASES
            if alias.strip()
        )
    )

    print()
    print("=" * 70)
    print(
        " PHASE 0A / OBSERVED HOST × ALIAS MATRIX"
    )
    print("=" * 70)

    print(
        f"[MATRIX] observed hosts : "
        f"{len(hosts)}"
    )

    print(
        f"[MATRIX] aliases        : "
        f"{len(aliases)}"
    )

    total = (
        len(hosts)
        * len(aliases)
    )

    print(
        f"[MATRIX] combinations   : "
        f"{total}"
    )

    for hostname in hosts:

        for alias in aliases:

            url = (
                f"https://{hostname}"
                f"/hls/{alias}/variant.m3u8"
            )

            item = make_entry(
                url,
                "matrix:observed-host×alias",
                name=alias,
                group=(
                    "NGENIX • "
                    "ALIAS MATRIX"
                ),
            )

            if item:

                entries.append(
                    item
                )

    print(
        "[MATRIX] generated "
        f"candidates: {len(entries)}"
    )

    return entries


# ============================================================
# CLUSTER CORRELATION HYPOTHESIS — NOT CANONICAL DISCOVERY
# ============================================================

CLUSTER_HYPOTHESIS_ANCHOR = "s70378"
CLUSTER_HYPOTHESIS_NEIGHBORS = [f"s{x}" for x in range(70379, 70389)]

# Формирует записи на основе кластерной гипотезы, не создавая новые hostname вне наблюдаемого набора.
def build_cluster_hypothesis_entries() -> list[StreamEntry]:
    """Probe adjacent service labels only as a hypothesis test.

    These hosts are NOT added to observed_hosts() and therefore do not
    become canonical inventory merely because the probe succeeds.
    """
    entries = []
    aliases = list(dict.fromkeys(a.lower().strip() for a in CHANNEL_ALIASES if a.strip()))
    for label in CLUSTER_HYPOTHESIS_NEIGHBORS:
        hostname = fqdn(label)
        for alias in aliases:
            item = make_entry(
                f"https://{hostname}/hls/{alias}/variant.m3u8",
                "hypothesis:neighbor-cluster:s70378",
                name=alias,
                group="NGENIX • CLUSTER HYPOTHESIS",
            )
            if item:
                entries.append(item)
    return entries


# ============================================================
# SPECIAL / LEGACY PATHS
# ============================================================

# Добавляет заранее зафиксированные специальные пути к соответствующим наблюдаемым хостам.
def seed_special_entries() -> list[StreamEntry]:

    entries = []

    hosts = observed_hosts()

    for hostname in hosts:

        paths = list(
            SEED_PATHS_SPECIAL.get(
                hostname,
                [],
            )
        )

        if "s70378" in hostname:

            paths.extend(
                SEED_PATHS_S70378
            )

        if (
            "htlive" in hostname
            or hostname.startswith("s")
        ):

            paths.extend(
                SEED_PATHS_GENERIC
            )

        if not paths:

            paths = list(
                SEED_PATHS_GENERIC
            )

        unique_paths = list(
            dict.fromkeys(
                paths
            )
        )

        for path in unique_paths:

            url = (
                f"https://{hostname}"
                f"{path}"
            )

            item = make_entry(
                url,
                "seed:observed-path",
            )

            if item:

                entries.append(
                    item
                )

    print(
        "[SEED] observed "
        "special/generic "
        "host×path combinations: "
        f"{len(entries)}"
    )

    return entries


# ============================================================
# MERGE / DEDUP
# ============================================================

# Объединяет записи, удаляя дубликаты по каноническому URL и сохраняя полезные метаданные.
def merge_entries(
    entries: Iterable[StreamEntry],
) -> list[StreamEntry]:

    database: dict[
        str,
        StreamEntry,
    ] = {}

    for item in entries:

        key = item.url.rstrip()

        if key not in database:

            database[key] = item

            continue

        old = database[key]

        old.last_seen = utc_now()

        if (
            not old.name
            and item.name
        ):

            old.name = item.name

        if (
            not old.group
            and item.group
        ):

            old.group = item.group

        old_sources = set(
            old.source.split(
                "; "
            )
        )

        for source in item.source.split(
            "; "
        ):

            old_sources.add(
                source
            )

        old.source = "; ".join(
            sorted(
                old_sources
            )
        )

    return sorted(
        database.values(),
        key=lambda x: (
            x.service_id
            or "zzzz",

            x.hostname,

            x.path,

            x.url,
        ),
    )


# ============================================================
# DNS
# ============================================================

# Выполняет полный этап разрешения и проверки DNS/TCP для набора потоков.
def resolve_all(
    hostname: str,
    timeout: float,
) -> tuple[
    list[str],
    float | None,
    str | None,
]:

    started = (
        time.perf_counter()
    )

    old_timeout = (
        socket.getdefaulttimeout()
    )

    try:

        socket.setdefaulttimeout(
            timeout
        )

        infos = socket.getaddrinfo(
            hostname,
            443,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )

        ips = sorted(
            {
                info[4][0]
                for info in infos
            }
        )

        latency = round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            2,
        )

        if not ips:

            return (
                [],
                latency,
                "DNS returned no addresses",
            )

        return (
            ips,
            latency,
            None,
        )

    except socket.gaierror as exc:

        return (
            [],
            None,
            str(exc),
        )

    except Exception as exc:

        return (
            [],
            None,
            repr(exc),
        )

    finally:

        socket.setdefaulttimeout(
            old_timeout
        )


# ============================================================
# TCP
# ============================================================

# Проверяет доступность конкретного IP узла и измеряет сетевую задержку.
def check_ip(
    ip: str,
    timeout: float,
) -> tuple[
    bool,
    float | None,
    str | None,
]:

    started = (
        time.perf_counter()
    )

    try:

        with socket.create_connection(
            (ip, 443),
            timeout=timeout,
        ):
            pass

        latency = round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            2,
        )

        return (
            True,
            latency,
            None,
        )

    except Exception as exc:

        return (
            False,
            round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            ),
            str(exc),
        )


# ============================================================
# NODE DISCOVERY
# ============================================================

# Строит список узлов из наблюдаемых hostname и результатов DNS/TCP-проверок.
def build_nodes(
    entries: list[StreamEntry],
    timeout: float,
) -> dict:

    hostnames = sorted(
        {
            item.hostname
            for item in entries
            if item.hostname
        }
    )

    nodes = {}

    print()
    print("=" * 70)
    print(
        " PHASE 1 / DNS + TCP NODE DISCOVERY"
    )
    print("=" * 70)

    for hostname in hostnames:

        dns_started = time.perf_counter()
        (
            ips,
            dns_latency,
            dns_error,
        ) = resolve_all(
            hostname,
            timeout,
        )
        record_telemetry(
            dns_started, "NODE", "DNS", hostname, "", "/",
            "OK" if ips else "ERROR", None, dns_error,
            {"addresses": ips},
        )

        reachable = []

        ip_results = {}

        for ip in ips:

            tcp_started = time.perf_counter()
            (
                online,
                latency,
                error,
            ) = check_ip(
                ip,
                timeout,
            )
            record_telemetry(
                tcp_started, "NODE", "TCP_443", hostname, "", "/",
                "ONLINE" if online else "NO_TCP", None, error,
                {"ip": ip},
            )

            ip_results[ip] = {
                "online": online,
                "latency_ms": latency,
                "error": error,
                "checked_at": utc_now(),
            }

            if online:
                reachable.append(
                    ip
                )

        if dns_error:

            status = "DNS_ERROR"

        elif not ips:

            status = "UNRESOLVED"

        elif reachable:

            status = "ONLINE"

        else:

            status = "NO_TCP"

        nodes[hostname] = {

            "hostname": hostname,

            "hostname_type": (
                classify_hostname(
                    hostname
                )
            ),

            "service_id": (
                extract_service_id(
                    hostname
                )
            ),

            "account_id": (
                extract_account_id(
                    hostname
                )
            ),

            "status": status,

            "dns": {
                "addresses": ips,
                "latency_ms": dns_latency,
                "error": dns_error,
            },

            "ip_results": ip_results,

            "checked_at": utc_now(),
        }

        print(
            f"[NODE {status:<12}] "
            f"{hostname:<58} "
            f"A={len(ips):>2} "
            f"ONLINE={len(reachable):>2}"
        )

    return nodes


# ============================================================
# APPLY NODE RESULTS
# ============================================================

# Применяет результаты проверки узлов к соответствующим StreamEntry.
def apply_node_results(
    entries: list[StreamEntry],
    nodes: dict,
) -> None:

    for item in entries:

        result = nodes.get(
            item.hostname
        )

        if not result:
            continue

        item.node_status = (
            result["status"]
        )

        item.node_ips = (
            result["dns"]["addresses"]
        )

        item.node_latency_ms = (
            result["dns"]["latency_ms"]
        )

        item.node_error = (
            result["dns"]["error"]
        )


# ============================================================
# STUB DETECTION
# ============================================================

# Определяет, является ли HTTP-ответ заглушкой, и фиксирует её целевой адрес.
def detect_stub(
    response_url: str,
    body: str,
) -> bool:

    final_host = (
        urlparse(
            response_url
        ).hostname
        or ""
    ).lower()

    lower_url = response_url.lower()
    lower_body = body.lower()

    return (
        final_host == STUB_HOST
        or STUB_HOST in lower_url
        or "zabava-block" in lower_url
        or "rtk_block" in lower_url
        or STUB_HOST in lower_body
        or "zabava-block" in lower_body
    )


# ============================================================
# DERIVED URL EXTRACTION
# ============================================================

# Извлекает фактически возвращённые NGENIX URL из содержимого заглушки или манифеста.
def extract_derived_urls(
    text: str,
    source_url: str,
) -> list[str]:

    found: list[str] = []

    # --------------------------------------------------------
    # Absolute NGENIX URLs
    # --------------------------------------------------------

    for match in DERIVED_URL_RE.finditer(
        text
    ):

        url = match.group(
            0
        ).rstrip(
            ".,;)]}>\"'"
        )

        try:

            parsed = urlparse(
                url
            )

            hostname = (
                parsed.hostname
                or ""
            ).lower()

            if not hostname.endswith(
                ".cdn.ngenix.net"
            ):
                continue

            found.append(
                url
            )

        except Exception:
            continue

    # --------------------------------------------------------
    # Relative M3U8 references
    # --------------------------------------------------------

    for match in RELATIVE_M3U8_RE.finditer(
        text
    ):

        raw = match.group(
            0
        )

        try:

            absolute = urljoin(
                source_url,
                raw,
            )

            parsed = urlparse(
                absolute
            )

            hostname = (
                parsed.hostname
                or ""
            ).lower()

            if not hostname.endswith(
                ".cdn.ngenix.net"
            ):
                continue

            found.append(
                absolute
            )

        except Exception:
            continue

    return list(
        dict.fromkeys(
            found
        )
    )


# ============================================================
# MANIFEST METADATA
# ============================================================

# Разбирает текст M3U8 и извлекает доступные метаданные манифеста.
def parse_manifest_metadata(
    text: str,
) -> tuple[
    str | None,
    str | None,
]:

    name = None
    group = None

    for line in text.splitlines():

        line = line.strip()

        if line.startswith(
            "#EXTINF"
        ):

            (
                parsed_name,
                parsed_group,
            ) = parse_extinf(
                line
            )

            if parsed_name:
                name = parsed_name

            if parsed_group:
                group = parsed_group

            break

    if not name:

        match = re.search(
            r'NAME="([^"]+)"',
            text,
            re.IGNORECASE,
        )

        if match:

            name = (
                match.group(
                    1
                ).strip()
            )

    if not group:

        match = re.search(
            r'GROUP-ID="([^"]+)"',
            text,
            re.IGNORECASE,
        )

        if match:

            group = (
                match.group(
                    1
                ).strip()
            )

    return (
        name,
        group,
    )


# Извлекает имя, группу и дополнительные поля из найденного манифеста.
def extract_manifest_metadata(
    text: str,
) -> dict:

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    return {

        "is_m3u8": (
            "#EXTM3U"
            in text
        ),

        "has_stream_inf": (
            "#EXT-X-STREAM-INF"
            in text
        ),

        "has_media_sequence": (
            "#EXT-X-MEDIA-SEQUENCE"
            in text
        ),

        "has_target_duration": (
            "#EXT-X-TARGETDURATION"
            in text
        ),

        "has_extinf": (
            "#EXTINF"
            in text
        ),

        "line_count": len(
            lines
        ),
    }


# ============================================================
# READ HTTP BODY
# ============================================================

# Выполняет ограниченный HTTP GET с таймаутом и лимитом чтения, возвращая нормализованный результат.
def http_get_bounded(
    url: str,
    timeout: float,
    read_limit: int,
    pacer: RequestPacer,
) -> tuple[
    int,
    str,
    str,
    str,
    float,
]:

    pacer.wait()

    parsed = urlparse(
        url
    )

    hostname = (
        parsed.hostname
        or ""
    ).lower()

    host_limiter = get_host_limiter(hostname)
    host_limiter.enter()

    try:
        started = (
            time.perf_counter()
        )

        request = Request(
            url,
            method="GET",
            headers={
                "User-Agent": USER_AGENTS[0],
                "Accept": (
                    "application/vnd.apple.mpegurl,"
                    "application/x-mpegURL,"
                    "audio/mpegurl,"
                    "*/*"
                ),
                "Connection": "close",
            },
        )

        context = (
            ssl.create_default_context()
        )

        with urlopen(
            request,
            timeout=timeout,
            context=context,
        ) as response:

            payload = response.read(
                read_limit
            )

            elapsed = round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            )

            return (
                response.status,
                (
                    response.geturl()
                    or url
                ),
                (
                    response.headers.get(
                        "Content-Type"
                    )
                    or ""
                ),
                payload.decode(
                    "utf-8",
                    errors="replace",
                ),
                elapsed,
            )

    finally:
        host_limiter.leave()


# ============================================================
# PRIMARY STREAM CHECK
# ============================================================

# Выполняет первичную проверку потока с классификацией HTTP/сетевого результата.
def check_stream(
    item: StreamEntry,
    timeout: float,
    read_limit: int,
    pacer: RequestPacer,
) -> None:

    operation_started = time.perf_counter()

    try:

        (
            status,
            final_url,
            content_type,
            text,
            elapsed,
        ) = http_get_bounded(
            item.url,
            timeout,
            read_limit,
            pacer,
        )

        item.http_status = status

        item.stream_content_type = (
            content_type
        )

        item.stream_bytes_read = (
            len(
                text.encode(
                    "utf-8",
                    errors="replace",
                )
            )
        )

        item.stream_latency_ms = (
            elapsed
        )

        item.stub_target = (
            final_url
        )

        item.is_stub = detect_stub(
            final_url,
            text,
        )

        # ----------------------------------------------------
        # STUB
        # ----------------------------------------------------

        if item.is_stub:

            item.stream_status = (
                "STUB"
            )

            item.derived_urls = (
                extract_derived_urls(
                    text,
                    final_url,
                )
            )

            return

        # ----------------------------------------------------
        # NORMAL HTTP
        # ----------------------------------------------------

        if status == 200:

            if "#EXTM3U" in text:

                item.stream_status = (
                    "ONLINE"
                )

            else:

                item.stream_status = (
                    "HTTP_OK"
                )

        elif (
            200
            <= status
            < 400
        ):

            item.stream_status = (
                "HTTP_OK"
            )

        else:

            item.stream_status = (
                "HTTP_ERROR"
            )

    except HTTPError as exc:

        item.http_status = (
            exc.code
        )

        item.stream_status = (
            "AUTH"
            if exc.code in {
                401,
                403,
            }
            else (
                "NOT_FOUND"
                if exc.code == 404
                else "HTTP_ERROR"
            )
        )

        item.stream_error = str(
            exc
        )

    except (
        URLError,
        TimeoutError,
        socket.timeout,
    ) as exc:

        item.stream_status = (
            "UNREACHABLE"
        )

        item.stream_error = str(
            exc
        )

    except Exception as exc:

        item.stream_status = (
            "ERROR"
        )

        item.stream_error = repr(
            exc
        )

    finally:
        record_telemetry(
            operation_started,
            "HTTP",
            "primary_stream",
            item.hostname,
            item.channel or "",
            item.path,
            item.stream_status,
            item.http_status,
            item.stream_error,
            {"url": item.url},
        )


# ============================================================
# PRIMARY STREAM CHECKS
# ============================================================

# Адаптирует число рабочих потоков по накопленной статистике ошибок.
def _adaptive_workers(current: int, completed: int, failures: int) -> int:

    if completed < 10:
        return current
    ratio = failures / max(1, completed)
    if ratio > 0.25:
        return max(MIN_WORKERS, current - 2)
    if ratio < 0.05:
        return min(MAX_WORKERS, current + 2)
    return current


# Проверяет все кандидаты пакетами с ограниченным concurrency и адаптивным числом workers.
def check_all_streams(
    entries: list[StreamEntry],
    timeout: float,
    read_limit: int,
    workers: int,
    request_delay: float,
) -> None:

    print()
    print("=" * 70)
    print(" PHASE 2 / PRIMARY STREAM CHECK / ADAPTIVE")
    print("=" * 70)

    total = len(entries)
    current_workers = max(MIN_WORKERS, min(workers, MAX_WORKERS))
    pacer = RequestPacer(request_delay)
    print(f"[CHECK] candidates : {total}")
    print(f"[CHECK] workers    : {current_workers} (adaptive {MIN_WORKERS}-{MAX_WORKERS})")
    print(f"[CHECK] host inflight: {HOST_INFLIGHT} / host interval: {HOST_MIN_INTERVAL:.3f}s")
    print("[CHECK] console output: COMPACT; full operation telemetry -> DREG TXT")
    print("[CHECK] statuses: 200/3xx/4xx/5xx + transport errors are classified in full")


    # Bounded batches allow the worker count to adapt without creating a
    # second unbounded queue. Every submitted task is an actual operation.
    index = 0
    completed = 0
    failures = 0
    while index < total:
        batch = entries[index:index + max(current_workers * 4, current_workers)]
        with ThreadPoolExecutor(max_workers=current_workers) as pool:
            futures = {
                pool.submit(check_stream, item, timeout, read_limit, pacer): item
                for item in batch
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    failures += 1
                    item.stream_status = "ERROR"
                    item.stream_error = repr(exc)
                completed += 1
                if item.stream_status in {"ERROR", "UNREACHABLE"}:
                    failures += 1
        index += len(batch)
        current_workers = _adaptive_workers(current_workers, completed, failures)


# ============================================================
# STUB → DERIVED → VERIFIED
# ============================================================

# Повторно проверяет URL, полученный из заглушки, и возвращает подтверждённую запись.
def verify_derived_url(
    stub: StreamEntry,
    derived_url: str,
    timeout: float,
    read_limit: int,
    pacer: RequestPacer,
) -> StreamEntry | None:

    operation_started = time.perf_counter()

    candidate = make_entry(
        derived_url,
        source=(
            "derived-from-stub:"
            + stub.url
        ),
    )

    if candidate is None:
        return None

    try:

        (
            status,
            final_url,
            content_type,
            text,
            elapsed,
        ) = http_get_bounded(
            derived_url,
            timeout,
            read_limit,
            pacer,
        )

        candidate.verification_http_status = (
            status
        )

        candidate.verification_content_type = (
            content_type
        )

        candidate.verification_bytes_read = (
            len(
                text.encode(
                    "utf-8",
                    errors="replace",
                )
            )
        )

        candidate.verification_latency_ms = (
            elapsed
        )

        # ----------------------------------------------------
        # NEVER automatically trust derived URL.
        # Must be HTTP 200.
        # ----------------------------------------------------

        if status != 200:

            candidate.verification_status = (
                "REJECTED_HTTP"
            )

            candidate.verification_error = (
                f"HTTP {status}"
            )

            return candidate

        # ----------------------------------------------------
        # Must actually be an M3U8 manifest.
        # ----------------------------------------------------

        if "#EXTM3U" not in text:

            candidate.verification_status = (
                "REJECTED_NOT_M3U8"
            )

            candidate.verification_error = (
                "HTTP 200 but #EXTM3U absent"
            )

            return candidate

        # ----------------------------------------------------
        # Reject a derived URL that resolves to STUB.
        # ----------------------------------------------------

        if detect_stub(
            final_url,
            text,
        ):

            candidate.verification_status = (
                "REJECTED_STUB"
            )

            candidate.verification_error = (
                "Derived URL resolved to STUB"
            )

            return candidate

        # ----------------------------------------------------
        # VERIFIED
        # ----------------------------------------------------

        (
            manifest_name,
            manifest_group,
        ) = parse_manifest_metadata(
            text
        )

        candidate.manifest_name = (
            manifest_name
        )

        candidate.manifest_group = (
            manifest_group
        )

        candidate.manifest_metadata = (
            extract_manifest_metadata(
                text
            )
        )

        candidate.verification_status = (
            "VERIFIED"
        )

        candidate.stream_status = (
            "ONLINE"
        )

        candidate.http_status = (
            status
        )

        candidate.stream_content_type = (
            content_type
        )

        candidate.stream_bytes_read = (
            candidate.verification_bytes_read
        )

        candidate.stream_latency_ms = (
            elapsed
        )

        # ----------------------------------------------------
        # Preserve node information from STUB.
        # ----------------------------------------------------

        candidate.node_status = (
            stub.node_status
        )

        candidate.node_ips = (
            stub.node_ips.copy()
        )

        candidate.node_latency_ms = (
            stub.node_latency_ms
        )

        candidate.node_error = (
            stub.node_error
        )

        return candidate

    except HTTPError as exc:

        candidate.verification_status = (
            "REJECTED_HTTP"
        )

        candidate.verification_http_status = (
            exc.code
        )

        candidate.verification_error = (
            str(exc)
        )

        return candidate

    except (
        URLError,
        TimeoutError,
        socket.timeout,
    ) as exc:

        candidate.verification_status = (
            "REJECTED_UNREACHABLE"
        )

        candidate.verification_error = (
            str(exc)
        )

        return candidate

    except Exception as exc:

        candidate.verification_status = (
            "REJECTED_ERROR"
        )

        candidate.verification_error = (
            repr(exc)
        )

        return candidate

    finally:
        record_telemetry(
            operation_started,
            "HTTP",
            "derived_verification",
            candidate.hostname,
            candidate.channel or "",
            candidate.path,
            candidate.verification_status,
            candidate.verification_http_status,
            candidate.verification_error,
            {"url": derived_url, "stub": stub.url},
        )


# Обрабатывает заглушки, извлекает derived URL и запускает их вторичную проверку.
def resolve_stubs(
    entries: list[StreamEntry],
    timeout: float,
    read_limit: int,
    workers: int,
    request_delay: float,
) -> list[StreamEntry]:

    stubs = [
        item
        for item in entries
        if item.is_stub
    ]

    if not stubs:

        print()
        print(
            "[STUB] No STUB endpoints detected."
        )

        return []

    print()
    print("=" * 70)
    print(
        " PHASE 3 / STUB → DERIVED → VERIFICATION"
    )
    print("=" * 70)

    print(
        f"[STUB] detected: "
        f"{len(stubs)}"
    )

    derived_pairs = []

    for stub in stubs:

        print()
        print(
            "[STUB]"
            f" {stub.url}"
        )

        print(
            f"       target: "
            f"{stub.stub_target}"
        )

        print(
            f"       derived: "
            f"{len(stub.derived_urls)}"
        )

        for url in (
            stub.derived_urls
        ):

            derived_pairs.append(
                (
                    stub,
                    url,
                )
            )

            print(
                f"       → {url}"
            )

    if not derived_pairs:

        print(
            "[STUB] No derived URLs."
        )

        return []

    workers = max(
        1,
        min(
            workers,
            MAX_WORKERS,
        ),
    )

    pacer = RequestPacer(
        request_delay
    )

    results = []

    with ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        futures = {

            pool.submit(
                verify_derived_url,
                stub,
                url,
                timeout,
                read_limit,
                pacer,
            ): (
                stub,
                url,
            )

            for stub, url
            in derived_pairs
        }

        done = 0

        total = len(
            futures
        )

        for future in as_completed(
            futures
        ):

            stub, url = futures[
                future
            ]

            try:

                result = (
                    future.result()
                )

            except Exception as exc:

                print(
                    "[STUB VERIFY ERROR]"
                    f" {url}: {exc!r}"
                )

                result = None

            if result:

                results.append(
                    result
                )

                print(
                    f"[STUB VERIFY] "
                    f"{result.verification_status:<24} "
                    f"{url}"
                )

            done += 1

            print(
                f"[STUB VERIFY "
                f"{done}/{total}]"
            )

    verified = [
        x
        for x in results
        if (
            x.verification_status
            == "VERIFIED"
        )
    ]

    print()
    print(
        f"[STUB] derived candidates : "
        f"{len(results)}"
    )

    print(
        f"[STUB] VERIFIED            : "
        f"{len(verified)}"
    )

    print(
        f"[STUB] REJECTED             : "
        f"{len(results) - len(verified)}"
    )

    return results


# ============================================================
# PLAYLIST ELIGIBILITY
# ============================================================

# Определяет, может ли подтверждённая запись попасть в итоговый плейлист.
def is_playlist_eligible(
    item: StreamEntry,
) -> bool:

    # --------------------------------------------------------
    # Direct observed endpoint
    # --------------------------------------------------------

    if item.source.startswith("hypothesis:"):
        return False

    if (
        not item.is_stub
        and item.stream_status
        == "ONLINE"
        and item.http_status
        == 200
        and item.verification_status
        in {
            "not_checked",
            "VERIFIED",
        }
    ):

        return True

    # --------------------------------------------------------
    # Derived endpoint from STUB
    # --------------------------------------------------------

    if (
        item.verification_status
        == "VERIFIED"
        and item.verification_http_status
        == 200
        and item.manifest_metadata.get(
            "is_m3u8",
            False,
        )
    ):

        return True

    return False


# ============================================================
# GRAPH
# ============================================================

# Строит граф связей между hostname, сервисами, алиасами и потоками.
def build_graph(
    entries: list[StreamEntry],
    nodes: dict,
) -> dict:

    services = {}
    hostnames = {}
    ips = {}
    aliases = {}

    for item in entries:

        service = (
            item.service_id
        )

        if service:

            services.setdefault(
                service,
                {
                    "service_id": service,
                    "hostnames": set(),
                    "streams": set(),
                },
            )

            services[
                service
            ][
                "hostnames"
            ].add(
                item.hostname
            )

            services[
                service
            ][
                "streams"
            ].add(
                item.url
            )

        if item.channel:

            aliases.setdefault(
                item.channel,
                {
                    "alias": item.channel,
                    "hostnames": set(),
                    "streams": set(),
                    "online": 0,
                    "verified": 0,
                    "stubs": 0,
                },
            )

            aliases[
                item.channel
            ][
                "hostnames"
            ].add(
                item.hostname
            )

            aliases[
                item.channel
            ][
                "streams"
            ].add(
                item.url
            )

            if (
                item.stream_status
                == "ONLINE"
            ):

                aliases[
                    item.channel
                ][
                    "online"
                ] += 1

            if (
                item.verification_status
                == "VERIFIED"
            ):

                aliases[
                    item.channel
                ][
                    "verified"
                ] += 1

            if item.is_stub:

                aliases[
                    item.channel
                ][
                    "stubs"
                ] += 1

        hostnames.setdefault(
            item.hostname,
            {
                "hostname": item.hostname,
                "service_id": item.service_id,
                "account_id": item.account_id,
                "ips": set(),
                "streams": set(),
            },
        )

        hostnames[
            item.hostname
        ][
            "streams"
        ].add(
            item.url
        )

        for ip in item.node_ips:

            hostnames[
                item.hostname
            ][
                "ips"
            ].add(
                ip
            )

            ips.setdefault(
                ip,
                {
                    "ip": ip,
                    "hostnames": set(),
                    "streams": set(),
                },
            )

            ips[
                ip
            ][
                "hostnames"
            ].add(
                item.hostname
            )

            ips[
                ip
            ][
                "streams"
            ].add(
                item.url
            )

    # Нормализует значение для безопасного хранения и сравнения в отчётах.
    def normalize(
        value: dict,
    ) -> dict:

        result = {}

        for key, item in value.items():

            result[key] = {}

            for (
                field_name,
                field_value,
            ) in item.items():

                if isinstance(
                    field_value,
                    set,
                ):

                    result[key][
                        field_name
                    ] = sorted(
                        field_value
                    )

                else:

                    result[key][
                        field_name
                    ] = field_value

        return result

    return {

        "generated_at": utc_now(),

        "services": normalize(
            services
        ),

        "aliases": normalize(
            aliases
        ),

        "hostnames": normalize(
            hostnames
        ),

        "ips": normalize(
            ips
        ),

        "nodes": nodes,
    }


# ============================================================
# INVENTORY
# ============================================================

# Формирует итоговый inventory со сводкой, узлами, записями и временными метками.
def build_inventory(
    entries: list[StreamEntry],
    nodes: dict,
) -> dict:

    hosts = sorted(
        {
            x.hostname
            for x in entries
        }
    )

    services = sorted(
        {
            x.service_id
            for x in entries
            if x.service_id
        }
    )

    accounts = sorted(
        {
            x.account_id
            for x in entries
            if x.account_id
        }
    )

    channels = sorted(
        {
            x.channel
            for x in entries
            if x.channel
        }
    )

    ips = sorted(
        {
            ip
            for x in entries
            for ip in x.node_ips
        }
    )

    online_entries = [
        x
        for x in entries
        if x.stream_status
        == "ONLINE"
    ]

    verified_entries = [
        x
        for x in entries
        if x.verification_status
        == "VERIFIED"
    ]

    stub_entries = [
        x
        for x in entries
        if x.is_stub
    ]

    rejected_derived = [
        x
        for x in entries
        if (
            x.source.startswith(
                "derived-from-stub:"
            )
            and x.verification_status
            != "VERIFIED"
        )
    ]

    return {

        "engine": ENGINE_NAME,

        "version": ENGINE_VERSION,

        "constellation": CONSTELLATION_NAME,

        "generated_at": utc_now(),

        "method": {

            "discovery": (
                "NGENIX DIRECT "
                "observed hostname matrix"
            ),

            "matrix": (
                "observed hostname × "
                "observed aliases"
            ),

            "service_id_guessing": False,

            "hostname_bruteforce": False,

            "authorization_bypass": False,

            "stub_resolution": (
                "observed response only"
            ),

            "derived_url_generation": False,

            "derived_url_verification": True,

            "playlist_policy": (
                "HTTP 200 + valid M3U8"
            ),
        },

        "alias_inventory": {

            "count": len(
                CHANNEL_ALIASES
            ),

            "aliases": CHANNEL_ALIASES,
        },

        "summary": {

            "cdn_hostnames": len(
                hosts
            ),

            "service_ids": len(
                services
            ),

            "account_ids": len(
                accounts
            ),

            "ips": len(
                ips
            ),

            "channels": len(
                channels
            ),

            "unique_streams": len(
                entries
            ),

            "online_nodes": sum(
                x["status"]
                == "ONLINE"
                for x in nodes.values()
            ),

            "online_streams": sum(
                x.stream_status
                == "ONLINE"
                for x in entries
            ),

            "http_ok_streams": sum(
                x.stream_status
                == "HTTP_OK"
                for x in entries
            ),

            "auth_streams": sum(
                x.stream_status
                == "AUTH"
                for x in entries
            ),

            "not_found_streams": sum(
                x.stream_status
                == "NOT_FOUND"
                for x in entries
            ),

            "stub_streams": len(
                stub_entries
            ),

            "derived_candidates": sum(
                bool(
                    x.derived_urls
                )
                for x in entries
            ),

            "verified_derived": len(
                verified_entries
            ),

            "rejected_derived": len(
                rejected_derived
            ),

            "playlist_entries": sum(
                is_playlist_eligible(
                    x
                )
                for x in entries
            ),
        },

        "online_aliases": sorted(
            {
                (
                    x.manifest_name
                    or x.channel
                )
                for x in online_entries
                if (
                    x.manifest_name
                    or x.channel
                )
            }
        ),

        "nodes": list(
            nodes.values()
        ),

        "entries": [
            asdict(x)
            for x in entries
        ],
    }


# ============================================================
# M3U OUTPUT
# ============================================================

# Сохраняет подтверждённые записи в итоговый M3U-плейлист.
def save_playlist(
    entries: list[StreamEntry],
    filename: Path,
) -> None:

    lines = [

        "#EXTM3U",

        f"#PLAYLIST:"
        f"{CONSTELLATION_NAME}",

        f"#ENGINE:"
        f"{ENGINE_NAME}",

        f"#VERSION:"
        f"{ENGINE_VERSION}",

        (
            "#DISCOVERY:"
            "NGENIX DIRECT observed hosts × aliases"
        ),

        "#PLAYLIST_POLICY:"
        "HTTP 200 + valid M3U8",

        "#GENERATED-UTC:"
        + utc_now(),

        "",
    ]

    current_group = None

    playlist_entries = [
        item
        for item in entries
        if is_playlist_eligible(
            item
        )
    ]

    # --------------------------------------------------------
    # Deterministic order
    # --------------------------------------------------------

    playlist_entries = sorted(
        playlist_entries,
        key=lambda x: (
            (
                x.manifest_group
                or x.group
                or ""
            ).lower(),

            (
                x.manifest_name
                or x.name
                or x.channel
                or ""
            ).lower(),

            x.url,
        ),
    )

    for item in playlist_entries:

        group = (
            item.manifest_group
            or item.group
            or (
                f"NGENIX • "
                f"{item.service_id}"
                if item.service_id
                else "NGENIX • OTHER"
            )
        )

        name = (
            item.manifest_name
            or item.name
            or item.channel
            or item.path
        )

        if group != current_group:

            lines.extend(
                [
                    "",
                    f"#GROUP:{group}",
                ]
            )

            current_group = group

        # ----------------------------------------------------
        # Metadata/provenance is preserved in EXTINF.
        # ----------------------------------------------------

        display_name = (
            f"{name} "
            f"[{item.hostname}]"
        )

        lines.append(
            f'#EXTINF:-1 '
            f'group-title="{group}",'
            f'{display_name}'
        )

        lines.append(
            item.url
        )

    filename.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename.write_text(
        "\n".join(
            lines
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"[PLAYLIST] eligible: "
        f"{len(playlist_entries)}"
    )


# ============================================================
# JSON
# ============================================================

# Сохраняет inventory или другую структуру данных в JSON с читаемым форматированием.
def save_json(
    data: dict,
    filename: Path,
) -> None:

    filename.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# CSV
# ============================================================

# Сохраняет записи потоков в табличный CSV с сериализацией вложенных структур.
def save_csv(
    entries: list[StreamEntry],
    filename: Path,
) -> None:

    filename.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fields = [

        "url",

        "hostname",

        "service_id",

        "account_id",

        "hostname_type",

        "path",

        "channel",

        "variant",

        "node_status",

        "stream_status",

        "http_status",

        "stream_latency_ms",

        "stream_content_type",

        "stream_bytes_read",

        "is_stub",

        "stub_target",

        "derived_urls",

        "verification_status",

        "verification_http_status",

        "verification_content_type",

        "verification_bytes_read",

        "verification_latency_ms",

        "manifest_name",

        "manifest_group",

        "manifest_metadata",

        "source",

        "stream_error",

        "verification_error",
    ]

    with filename.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as fh:

        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
        )

        writer.writeheader()

        for item in entries:

            row = asdict(
                item
            )

            for field_name in [
                "derived_urls",
                "manifest_metadata",
            ]:

                if isinstance(
                    row.get(
                        field_name
                    ),
                    (
                        list,
                        dict,
                    ),
                ):

                    row[
                        field_name
                    ] = json.dumps(
                        row[
                            field_name
                        ],
                        ensure_ascii=False,
                    )

            writer.writerow(
                {
                    field_name:
                    row.get(
                        field_name,
                        "",
                    )
                    for field_name
                    in fields
                }
            )


# ============================================================
# HISTORY
# ============================================================

# Добавляет текущую сводку inventory в исторический JSON-журнал.
def update_history(
    inventory: dict,
    filename: Path,
) -> None:

    history = []

    if filename.exists():

        try:

            history = json.loads(
                filename.read_text(
                    encoding="utf-8"
                )
            )

            if not isinstance(
                history,
                list,
            ):

                history = []

        except Exception:

            history = []

    history.append(
        {

            "timestamp":
                inventory[
                    "generated_at"
                ],

            "summary":
                inventory[
                    "summary"
                ],
        }
    )

    filename.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename.write_text(
        json.dumps(
            history,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# SCALA / DREG TELEMETRY OUTPUT
# ============================================================

# Сохраняет полный журнал ДРЕГ-телеметрии в текстовый файл.
def save_telemetry_txt(filename: Path, compact: bool = False) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    with _TELEMETRY_LOCK:
        events = list(_TELEMETRY)
    lines = [
        "============================================================",
        "NGENIX CDN CONSTELLATION / ДРЕГ TELEMETRY",
        "============================================================",
        f"TIMEZONE: MSK (UTC+03:00)",
        f"EVENTS: {len(events)}",
        "",
    ]
    for e in events:
        lines.append(
            f"{e.timestamp_start} -> {e.timestamp_end} | "
            f"{e.duration_ms:8.2f} ms | {e.operation}/{e.suboperation} | "
            f"node={e.node} | alias={e.alias} | path={e.path} | "
            f"RESULT={e.result_en} / {e.result_ru} | "
            f"HTTP={e.http_status if e.http_status is not None else '-'} | "
            f"error={e.error or '-'} | extra={json.dumps(e.extra, ensure_ascii=False, separators=(",",":"))}"
        )
    filename.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Сохраняет компактное представление SCALA-телеметрии для быстрого просмотра.
def save_scala_compact(filename: Path) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    with _TELEMETRY_LOCK:
        events = list(_TELEMETRY)
    by_result = {}
    for e in events:
        by_result[e.result] = by_result.get(e.result, 0) + 1
    lines = [
        "SCALA NGENIX TELEMETRY",
        f"MSK: {msk_now()}",
        f"OPERATIONS: {len(events)}",
        "RESULTS: " + ", ".join(f"{k}={v}" for k,v in sorted(by_result.items())),
        f"HYPOTHESIS EVENTS: {sum(1 for e in events if e.node in [fqdn(x) for x in CLUSTER_HYPOTHESIS_NEIGHBORS])}",
    ]
    filename.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# REPORT
# ============================================================

# Формирует подробный SKALA-отчёт по результатам сканирования и верификации.
def build_report(
    inventory: dict,
    graph: dict,
) -> str:

    summary = (
        inventory[
            "summary"
        ]
    )

    lines = [

        "============================================================",

        "       NGENIX CDN CONSTELLATION / ULTRA SKALA REPORT / v5",

        "============================================================",

        (
            "Generated UTC : "
            f"{inventory['generated_at']}"
        ),

        (
            "Engine        : "
            f"{inventory['engine']}"
        ),

        (
            "Version       : "
            f"{inventory['version']}"
        ),

        "",

        "DISCOVERY METHOD",

        "------------------------------------------------------------",

        "Observed hostname matrix × observed aliases; neighbor cluster is hypothesis-only",

        "No sXXXXX brute force",

        "No hostname generation",

        "No authorization bypass",

        "STUB URLs preserved in inventory",

        "Derived URLs extracted only from observed STUB response",

        "Derived URLs require second-stage verification",

        "Playlist requires HTTP 200 + #EXTM3U",

        "",

        "SUMMARY",

        "------------------------------------------------------------",

        (
            f"CDN hostnames : "
            f"{summary['cdn_hostnames']}"
        ),

        (
            f"Service IDs   : "
            f"{summary['service_ids']}"
        ),

        (
            f"Account IDs   : "
            f"{summary['account_ids']}"
        ),

        (
            f"Unique IPs    : "
            f"{summary['ips']}"
        ),

        (
            f"Channels      : "
            f"{summary['channels']}"
        ),

        (
            f"Streams       : "
            f"{summary['unique_streams']}"
        ),

        (
            f"Online nodes  : "
            f"{summary['online_nodes']}"
        ),

        (
            f"Online streams: "
            f"{summary['online_streams']}"
        ),

        (
            f"HTTP OK       : "
            f"{summary['http_ok_streams']}"
        ),

        (
            f"Auth/403      : "
            f"{summary['auth_streams']}"
        ),

        (
            f"404           : "
            f"{summary['not_found_streams']}"
        ),

        (
            f"STUB          : "
            f"{summary['stub_streams']}"
        ),

        (
            f"Derived       : "
            f"{summary['derived_candidates']}"
        ),

        (
            f"Verified      : "
            f"{summary['verified_derived']}"
        ),

        (
            f"Rejected      : "
            f"{summary['rejected_derived']}"
        ),

        (
            f"Playlist      : "
            f"{summary['playlist_entries']}"
        ),

        "",

        "ALIASES WITH ONLINE / VERIFIED STREAMS",

        "------------------------------------------------------------",
    ]

    for alias in inventory[
        "online_aliases"
    ]:

        lines.append(
            f"  {alias}"
        )

    lines.extend(
        [
            "",
            "SERVICE MAP",
            "------------------------------------------------------------",
        ]
    )

    for (
        service_id,
        data,
    ) in sorted(
        graph[
            "services"
        ].items()
    ):

        lines.append("")

        lines.append(
            f"[{service_id}]"
        )

        lines.append(
            "  HOSTS:"
        )

        for hostname in data[
            "hostnames"
        ]:

            lines.append(
                f"    {hostname}"
            )

    lines.extend(
        [
            "",
            "ALIAS MAP",
            "------------------------------------------------------------",
        ]
    )

    for (
        alias,
        data,
    ) in sorted(
        graph[
            "aliases"
        ].items()
    ):

        lines.append(
            f"{alias:<30} "
            f"online={data['online']:<4} "
            f"verified={data['verified']:<4} "
            f"stubs={data['stubs']:<4} "
            f"hosts={len(data['hostnames']):<4} "
            f"streams={len(data['streams'])}"
        )

    lines.extend(
        [
            "",
            "STUB MAP",
            "------------------------------------------------------------",
        ]
    )

    for item in sorted(
        (
            x
            for x in inventory[
                "entries"
            ]
            if x["is_stub"]
        ),
        key=lambda x: x["url"],
    ):

        lines.append(
            f"STUB: {item['url']}"
        )

        if item[
            "stub_target"
        ]:

            lines.append(
                f"  TARGET: "
                f"{item['stub_target']}"
            )

        for derived in item[
            "derived_urls"
        ]:

            lines.append(
                f"  DERIVED: "
                f"{derived}"
            )

    lines.extend(
        [
            "",
            "CLUSTER HYPOTHESIS",
            "------------------------------------------------------------",
            f"Anchor: {CLUSTER_HYPOTHESIS_ANCHOR}",
            "Neighbors: " + ", ".join(CLUSTER_HYPOTHESIS_NEIGHBORS),
            "Classification: hypothesis_only (not canonical discovery)",
        ]
    )

    lines.extend(
        [
            "",
            "============================================================",
        ]
    )

    return "\n".join(
        lines
    )


# ============================================================
# MATRIX SUMMARY
# ============================================================

# Печатает сводку построенной матрицы наблюдаемых хостов и алиасов.
def print_matrix_summary(
    entries: list[StreamEntry],
) -> None:

    matrix_entries = [

        x

        for x in entries

        if (
            "matrix:observed-host×alias"
            in x.source
        )
    ]

    online = [

        x

        for x in matrix_entries

        if x.stream_status
        == "ONLINE"
    ]

    http_ok = [

        x

        for x in matrix_entries

        if x.stream_status
        == "HTTP_OK"
    ]

    auth = [

        x

        for x in matrix_entries

        if x.stream_status
        == "AUTH"
    ]

    not_found = [

        x

        for x in matrix_entries

        if x.stream_status
        == "NOT_FOUND"
    ]

    stubs = [

        x

        for x in matrix_entries

        if x.is_stub
    ]

    print()
    print("=" * 70)
    print(
        " MATRIX RESULT"
    )
    print("=" * 70)

    print(
        f"Matrix candidates : "
        f"{len(matrix_entries)}"
    )

    print(
        f"ONLINE            : "
        f"{len(online)}"
    )

    print(
        f"HTTP_OK           : "
        f"{len(http_ok)}"
    )

    print(
        f"AUTH/403          : "
        f"{len(auth)}"
    )

    print(
        f"NOT_FOUND         : "
        f"{len(not_found)}"
    )

    print(
        f"STUB              : "
        f"{len(stubs)}"
    )

    print()
    print(
        "ONLINE ALIASES:"
    )

    online_aliases = sorted(
        {
            x.channel
            for x in online
            if x.channel
        }
    )

    for alias in online_aliases:

        hosts = sorted(
            {
                x.hostname
                for x in online
                if x.channel
                == alias
            }
        )

        print(
            f"  {alias:<30} "
            f"hosts={len(hosts)}"
        )

    if stubs:

        print()
        print(
            "STUB ENDPOINTS:"
        )

        for item in sorted(
            stubs,
            key=lambda x: x.url,
        ):

            print(
                f"  {item.url}"
            )

            if item.stub_target:

                print(
                    f"      target="
                    f"{item.stub_target}"
                )

            for derived in (
                item.derived_urls
            ):

                print(
                    f"      derived="
                    f"{derived}"
                )


# ============================================================
# MAIN
# ============================================================

# Разбирает аргументы командной строки, запускает этапы движка и сохраняет результаты.
def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "NGENIX CDN CONSTELLATION "
            "v4 ULTRA"
        )
    )

    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help=(
            "Корень репозитория; "
            "используется только с --repo"
        ),
    )

    parser.add_argument(
        "--output-dir",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
    )

    parser.add_argument(
        "--report-dir",
        default=str(
            DEFAULT_REPORT_DIR
        ),
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
    )

    parser.add_argument(
        "--read-limit",
        type=int,
        default=DEFAULT_READ_LIMIT,
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
    )

    parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY,
        help=(
            "Минимальная пауза между "
            "HTTP GET"
        ),
    )

    parser.add_argument(
        "--repo",
        action="store_true",
        help=(
            "Дополнительно искать "
            "NGENIX URL в локальном "
            "репозитории"
        ),
    )

    parser.add_argument(
        "--no-matrix",
        action="store_true",
        help=(
            "Не строить observed "
            "hostname × alias matrix"
        ),
    )

    parser.add_argument(
        "--cluster-hypothesis",
        action="store_true",
        help=(
            "Проверить соседние s70379-s70388 как отдельную "
            "гипотезу; не считать их наблюдаемыми автоматически"
        ),
    )

    parser.add_argument(
        "--no-special",
        action="store_true",
        help=(
            "Не добавлять старые "
            "special/generic paths"
        ),
    )

    parser.add_argument(
        "--no-seed",
        action="store_true",
        help=(
            "Отключить весь observed "
            "hostname seed"
        ),
    )

    parser.add_argument(
        "--no-stream-check",
        action="store_true",
        help=(
            "Только DNS/TCP без HTTP "
            "проверки потоков"
        ),
    )

    args = parser.parse_args()

    output_dir = Path(
        args.output_dir
    )

    report_dir = Path(
        args.report_dir
    )

    print()
    print("=" * 70)
    print(
        " MODE: NGENIX DIRECT / "
        "OBSERVED HOST × ALIASES"
    )
    print("=" * 70)

    print(
        "[MODE] sXXXXX brute force: OFF"
    )

    print(
        "[MODE] hostname generation: OFF"
    )

    print(
        "[MODE] authorization bypass: OFF"
    )

    print(
        f"[MODE] aliases loaded: "
        f"{len(CHANNEL_ALIASES)}"
    )

    entries: list[
        StreamEntry
    ] = []

    hypothesis_entries: list[StreamEntry] = []

    # ========================================================
    # OPTIONAL REPOSITORY
    # ========================================================

    if args.repo:

        root = Path(
            args.root or "."
        ).resolve()

        print(
            f"[MODE] repository scan: "
            f"{root}"
        )

        entries.extend(
            discover_repository(
                root
            )
        )

    else:

        print(
            "[MODE] repository scan: OFF"
        )

    # ========================================================
    # OBSERVED HOST SEED
    # ========================================================

    if not args.no_seed:

        hosts = observed_hosts()

        print()
        print(
            "[OBSERVED] hostnames: "
            f"{len(hosts)}"
        )

        # ----------------------------------------------------
        # MATRIX
        # ----------------------------------------------------

        if not args.no_matrix:

            entries.extend(
                build_alias_matrix()
            )

        # ----------------------------------------------------
        # OBSERVED PATHS
        # ----------------------------------------------------

        if not args.no_special:

            entries.extend(
                seed_special_entries()
            )

    else:

        print(
            "[MODE] observed seed: OFF"
        )

    # ========================================================
    # CLUSTER HYPOTHESIS (SEPARATE CLASS)
    # ========================================================

    if args.cluster_hypothesis:
        hypothesis_entries = build_cluster_hypothesis_entries()
        print(
            f"[HYPOTHESIS] adjacent-node candidates: "
            f"{len(hypothesis_entries)}"
        )

    # ========================================================
    # DEDUP
    # ========================================================

    entries = merge_entries(
        entries
    )

    print()
    print("=" * 70)
    print(
        " CANONICAL INVENTORY"
    )
    print("=" * 70)

    print(
        "[CANON] Unique NGENIX endpoints: "
        f"{len(entries)}"
    )

    services = sorted(
        {
            x.service_id
            for x in entries
            if x.service_id
        }
    )

    hosts = sorted(
        {
            x.hostname
            for x in entries
        }
    )

    aliases = sorted(
        {
            x.channel
            for x in entries
            if x.channel
        }
    )

    print(
        f"[CANON] Hostnames : "
        f"{len(hosts)}"
    )

    print(
        f"[CANON] sXXXXX    : "
        f"{len(services)}"
    )

    print(
        f"[CANON] Aliases   : "
        f"{len(aliases)}"
    )

    print(
        f"[CANON] Streams   : "
        f"{len(entries)}"
    )

    print()
    print(
        "[CANON] OBSERVED sXXXXX:"
    )

    for service in services:

        print(
            f"    {service}"
        )

    # ========================================================
    # DNS / TCP
    # ========================================================

    check_entries = entries + hypothesis_entries

    nodes = build_nodes(
        check_entries,
        args.timeout,
    )

    apply_node_results(
        check_entries,
        nodes,
    )

    # ========================================================
    # HTTP
    # ========================================================

    if not args.no_stream_check:

        check_all_streams(
            check_entries,
            args.timeout,
            args.read_limit,
            args.workers,
            args.request_delay,
        )

        # ====================================================
        # STUB SECOND STAGE
        # ====================================================

        derived_results = (
            resolve_stubs(
                entries,
                args.timeout,
                args.read_limit,
                args.workers,
                args.request_delay,
            )
        )

        if derived_results:

            entries.extend(
                derived_results
            )

            entries = merge_entries(
                entries
            )

    else:

        print()
        print(
            "[MODE] stream HTTP check: OFF"
        )

    # ========================================================
    # GRAPH / INVENTORY
    # ========================================================

    inventory = build_inventory(
        entries,
        nodes,
    )

    inventory["cluster_hypothesis"] = {
        "anchor": CLUSTER_HYPOTHESIS_ANCHOR,
        "neighbors": CLUSTER_HYPOTHESIS_NEIGHBORS,
        "classification": "hypothesis_only",
        "canonical": False,
        "entries": [asdict(x) for x in hypothesis_entries],
    }

    graph = build_graph(
        entries,
        nodes,
    )

    # ========================================================
    # OUTPUT
    # ========================================================

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    save_json(
        inventory,
        output_dir
        / OUTPUT_JSON,
    )

    save_json(
        graph,
        output_dir
        / OUTPUT_GRAPH,
    )

    save_playlist(
        entries,
        output_dir
        / OUTPUT_M3U,
    )

    save_csv(
        entries,
        output_dir
        / OUTPUT_CSV,
    )

    save_telemetry_txt(
        report_dir / OUTPUT_DREG
    )

    save_scala_compact(
        report_dir / OUTPUT_SCALA
    )

    update_history(
        inventory,
        output_dir
        / OUTPUT_HISTORY,
    )

    report_path = (
        report_dir
        / OUTPUT_REPORT
    )

    report_path.write_text(
        build_report(
            inventory,
            graph,
        ),
        encoding="utf-8",
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print_matrix_summary(
        entries
    )

    # ========================================================
    # FINAL
    # ========================================================

    print()
    print("=" * 70)
    print(
        " NGENIX CONSTELLATION v4 ULTRA COMPLETE"
    )
    print("=" * 70)

    summary = inventory[
        "summary"
    ]

    print(
        f"Hostnames : "
        f"{summary['cdn_hostnames']}"
    )

    print(
        f"sXXXXX    : "
        f"{summary['service_ids']}"
    )

    print(
        f"Aliases   : "
        f"{summary['channels']}"
    )

    print(
        f"Streams   : "
        f"{summary['unique_streams']}"
    )

    print(
        f"Node OK   : "
        f"{summary['online_nodes']}"
    )

    print(
        f"Stream OK : "
        f"{summary['online_streams']}"
    )

    print(
        f"HTTP OK   : "
        f"{summary['http_ok_streams']}"
    )

    print(
        f"Auth/403  : "
        f"{summary['auth_streams']}"
    )

    print(
        f"404       : "
        f"{summary['not_found_streams']}"
    )

    print(
        f"Stub      : "
        f"{summary['stub_streams']}"
    )

    print(
        f"Derived   : "
        f"{summary['derived_candidates']}"
    )

    print(
        f"Verified  : "
        f"{summary['verified_derived']}"
    )

    print(
        f"Rejected  : "
        f"{summary['rejected_derived']}"
    )

    print(
        f"Playlist  : "
        f"{summary['playlist_entries']}"
    )

    print()

    print(
        f"[OUTPUT] "
        f"{output_dir / OUTPUT_JSON}"
    )

    print(
        f"[OUTPUT] "
        f"{output_dir / OUTPUT_GRAPH}"
    )

    print(
        f"[OUTPUT] "
        f"{output_dir / OUTPUT_M3U}"
    )

    print(
        f"[OUTPUT] "
        f"{output_dir / OUTPUT_CSV}"
    )

    print(
        f"[OUTPUT] "
        f"{output_dir / OUTPUT_HISTORY}"
    )

    print(
        f"[REPORT] "
        f"{report_path}"
    )

    print(
        f"[DREG] "
        f"{report_dir / OUTPUT_DREG}"
    )

    print(
        f"[SCALA] "
        f"{report_dir / OUTPUT_SCALA}"
    )


if __name__ == "__main__":
    main()