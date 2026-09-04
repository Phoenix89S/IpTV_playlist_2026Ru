#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
       NGENIX CDN CONSTELLATION v4 ULTRA
          ngSKALA / ZOYE Discovery Engine
============================================================

РЕЖИМ:
  • Только наблюдаемые/зафиксированные NGENIX hostname.
  • Никакого перебора sXXXXX.
  • Никакого угадывания новых hostname.
  • Проверяются ВСЕ КОМБИНАЦИИ:
        observed hostname × 84 observed channel aliases
  • Дополнительно сохраняются специальные ранее найденные paths.
  • DNS → TCP/443 → bounded HTTP GET.
  • M3U8 проверяется только ограниченным чтением.
  • zabava-block-htvod определяется отдельно.
  • Дедупликация URL.
  • JSON / GRAPH / M3U / CSV / HISTORY / SKALA REPORT.

ВАЖНО:
  Список hostname ограничен зафиксированными наблюдаемыми узлами.
  Алиасы ниже — переданный пользователем итоговый список из 84
  уникальных алиасов.
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# ============================================================
# ENGINE
# ============================================================

ENGINE_NAME = "ngSKALA"
ENGINE_VERSION = "NGENIX-CONSTELLATION-v4-ULTRA"
CONSTELLATION_NAME = "NGENIX CDN CONSTELLATION ULTRA"

OUTPUT_M3U = "NGENIX_CDN_CONSTELLATION.m3u"
OUTPUT_JSON = "NGENIX_CDN_CONSTELLATION.json"
OUTPUT_GRAPH = "NGENIX_CDN_CONSTELLATION_GRAPH.json"
OUTPUT_REPORT = "NGENIX_CDN_CONSTELLATION_SKALA.txt"
OUTPUT_HISTORY = "NGENIX_CDN_CONSTELLATION_HISTORY.json"
OUTPUT_CSV = "NGENIX_CDN_CONSTELLATION.csv"

DEFAULT_OUTPUT_DIR = Path("data/ngenix_constellation")
DEFAULT_REPORT_DIR = Path("reports/ngenix_constellation")


# ============================================================
# SAFETY / BOUNDS
# ============================================================

DEFAULT_TIMEOUT = 5.0
DEFAULT_READ_LIMIT = 65536

# Жёсткий потолок параллелизма.
DEFAULT_WORKERS = 12
MAX_WORKERS = 24

# Минимальная пауза между стартами HTTP-проверок.
DEFAULT_REQUEST_DELAY = 0.05

# Не более одного запроса одновременно к одному hostname.
HOST_INFLIGHT = 1


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
    r"(?<![a-z0-9-])(s\d{5,})(?![a-z0-9-])",
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

STUB_HOST = "zabava-block-htvod.cdn.ngenix.net"


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
# Никакого генерирования диапазонов.
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
# 84 OBSERVED CHANNEL ALIASES
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
    node_ips: list[str] = field(default_factory=list)
    node_latency_ms: float | None = None
    node_error: str | None = None

    stream_status: str = "not_checked"
    http_status: int | None = None
    stream_latency_ms: float | None = None
    stream_content_type: str | None = None
    stream_bytes_read: int = 0
    stream_error: str | None = None

    is_stub: bool = False

    first_seen: str | None = None
    last_seen: str | None = None


# ============================================================
# GLOBAL RATE LIMITER
# ============================================================

class RequestPacer:
    def __init__(self, delay: float):
        self.delay = max(0.0, delay)
        self.lock = Lock()
        self.last_request = 0.0

    def wait(self) -> None:
        if self.delay <= 0:
            return

        with self.lock:
            now = time.monotonic()
            wait_for = self.delay - (now - self.last_request)

            if wait_for > 0:
                time.sleep(wait_for)

            self.last_request = time.monotonic()


# ============================================================
# BASIC HELPERS
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fqdn(label: str) -> str:
    label = label.lower().strip()

    if label.endswith(".cdn.ngenix.net"):
        return label

    return f"{label}.cdn.ngenix.net"


def extract_service_id(hostname: str) -> str | None:
    match = SERVICE_RE.search(hostname)

    if match:
        return match.group(1).lower()

    return None


def extract_account_id(hostname: str) -> str | None:
    match = re.search(
        r"(a\d+)-",
        hostname,
        re.IGNORECASE,
    )

    if match:
        return match.group(1).lower()

    return None


def classify_hostname(hostname: str) -> str:
    hostname = hostname.lower()

    if hostname == STUB_HOST:
        return "stub"

    if ACCOUNT_SERVICE_RE.search(hostname):
        return "account_service"

    if SERVICE_RE.search(hostname):
        return "service"

    if "htvod" in hostname:
        return "named_htvod"

    if "htlive" in hostname:
        return "named_htlive"

    return "named_cdn"


def extract_channel(path: str) -> str | None:
    parts = [
        x for x in path.split("/")
        if x
    ]

    if not parts:
        return None

    if parts[0].lower() == "hls" and len(parts) > 1:
        return parts[1]

    return parts[0]


def extract_variant(path: str) -> str | None:
    parts = [
        x for x in path.split("/")
        if x
    ]

    if len(parts) < 2:
        return None

    filename = parts[-1].lower().split("?")[0]

    if filename in {
        "index.m3u8",
        "variant.m3u8",
        "master.m3u8",
        "playlist.m3u8",
        "mono.m3u8",
    }:
        return parts[-2]

    return None


def parse_extinf(
    line: str,
) -> tuple[str | None, str | None]:

    name = None
    group = None

    comma = line.find(",")

    if comma >= 0:
        name = line[comma + 1:].strip()

    match = re.search(
        r'group-title="([^"]*)"',
        line,
        re.IGNORECASE,
    )

    if match:
        group = match.group(1)

    return name, group


# ============================================================
# ENTRY CREATION
# ============================================================

def make_entry(
    url: str,
    source: str,
    name: str | None = None,
    group: str | None = None,
) -> StreamEntry | None:

    try:
        parsed = urlparse(url)

        hostname = (
            parsed.hostname or ""
        ).lower()

        if not hostname.endswith(
            ".cdn.ngenix.net"
        ):
            return None

        path = parsed.path or "/"

        if parsed.query:
            path = (
                f"{path}?{parsed.query}"
            )

        now = utc_now()

        return StreamEntry(
            url=url,
            hostname=hostname,
            service_id=extract_service_id(
                hostname
            ),
            account_id=extract_account_id(
                hostname
            ),
            hostname_type=classify_hostname(
                hostname
            ),
            path=path,
            channel=extract_channel(
                parsed.path or "/"
            ),
            variant=extract_variant(
                parsed.path or "/"
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

        if line.startswith("#EXTINF"):

            current_name, current_group = (
                parse_extinf(line)
            )

            continue

        if line.startswith("#"):
            continue

        if not line.lower().startswith(
            ("http://", "https://")
        ):
            continue

        if not HOST_RE.search(line):
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
            entries.append(item)

        current_name = None
        current_group = None

    return entries


# ============================================================
# TEXT DISCOVERY
# ============================================================

def discover_urls_in_text(
    text: str,
    source: str,
) -> list[StreamEntry]:

    entries = []

    for match in HOST_RE.finditer(text):

        url = match.group(0).rstrip(
            ".,;)]}>\"'"
        )

        item = make_entry(
            url,
            source,
        )

        if item:
            entries.append(item)

    return entries


# ============================================================
# OPTIONAL REPOSITORY DISCOVERY
# ============================================================

def discover_repository(
    root: Path,
) -> list[StreamEntry]:

    entries = []
    files_seen = 0

    print()
    print("=" * 70)
    print(" PHASE 0 / OPTIONAL REPOSITORY DISCOVERY")
    print("=" * 70)

    for path in root.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        if "ngenix_constellation" in path.parts:
            continue

        files_seen += 1

        try:
            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:

            print(
                f"[READ-ERROR] {path}: {exc}"
            )

            continue

        if path.suffix.lower() in {
            ".m3u",
            ".m3u8",
        }:

            found = parse_m3u(
                text,
                str(path),
            )

        else:

            found = discover_urls_in_text(
                text,
                str(path),
            )

        if found:

            print(
                f"[DISCOVERY] {path} -> "
                f"{len(found)} endpoints"
            )

            entries.extend(found)

    print(
        f"[DISCOVERY] files: {files_seen}"
    )

    print(
        f"[DISCOVERY] raw endpoints: "
        f"{len(entries)}"
    )

    return entries


# ============================================================
# ALL OBSERVED HOSTS
# ============================================================

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

        host = fqdn(label)

        if host in seen:
            continue

        seen.add(host)
        result.append(host)

    return sorted(result)


# ============================================================
# BUILD ALL HOST × 84 ALIAS COMBINATIONS
# ============================================================

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
    print(" PHASE 0A / OBSERVED HOST × ALIAS MATRIX")
    print("=" * 70)

    print(
        f"[MATRIX] observed hosts : {len(hosts)}"
    )

    print(
        f"[MATRIX] aliases        : {len(aliases)}"
    )

    total = len(hosts) * len(aliases)

    print(
        f"[MATRIX] combinations   : {total}"
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
                group="NGENIX • ALIAS MATRIX",
            )

            if item:
                entries.append(item)

    print(
        f"[MATRIX] generated candidates: "
        f"{len(entries)}"
    )

    return entries


# ============================================================
# SPECIAL / LEGACY OBSERVED PATHS
# ============================================================

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
            dict.fromkeys(paths)
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
                entries.append(item)

    print(
        f"[SEED] observed special/generic "
        f"host×path combinations: "
        f"{len(entries)}"
    )

    return entries


# ============================================================
# MERGE / DEDUP
# ============================================================

def merge_entries(
    entries: Iterable[StreamEntry],
) -> list[StreamEntry]:

    database: dict[str, StreamEntry] = {}

    for item in entries:

        key = item.url.rstrip()

        if key not in database:

            database[key] = item
            continue

        old = database[key]

        old.last_seen = utc_now()

        if not old.name and item.name:
            old.name = item.name

        if not old.group and item.group:
            old.group = item.group

        old_sources = set(
            old.source.split("; ")
        )

        for source in item.source.split(
            "; "
        ):
            old_sources.add(source)

        old.source = "; ".join(
            sorted(old_sources)
        )

    return sorted(
        database.values(),
        key=lambda x: (
            x.service_id or "zzzz",
            x.hostname,
            x.path,
            x.url,
        ),
    )


# ============================================================
# DNS
# ============================================================

def resolve_all(
    hostname: str,
    timeout: float,
) -> tuple[
    list[str],
    float | None,
    str | None,
]:

    started = time.perf_counter()

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
# TCP CHECK
# ============================================================

def check_ip(
    ip: str,
    timeout: float,
) -> tuple[
    bool,
    float | None,
    str | None,
]:

    started = time.perf_counter()

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
    print(" PHASE 1 / DNS + TCP NODE DISCOVERY")
    print("=" * 70)

    for hostname in hostnames:

        ips, dns_latency, dns_error = (
            resolve_all(
                hostname,
                timeout,
            )
        )

        reachable = []
        ip_results = {}

        for ip in ips:

            online, latency, error = (
                check_ip(
                    ip,
                    timeout,
                )
            )

            ip_results[ip] = {
                "online": online,
                "latency_ms": latency,
                "error": error,
                "checked_at": utc_now(),
            }

            if online:
                reachable.append(ip)

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
            "hostname_type": classify_hostname(
                hostname
            ),
            "service_id": extract_service_id(
                hostname
            ),
            "account_id": extract_account_id(
                hostname
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

        item.node_status = result[
            "status"
        ]

        item.node_ips = result[
            "dns"
        ]["addresses"]

        item.node_latency_ms = result[
            "dns"
        ]["latency_ms"]

        item.node_error = result[
            "dns"
        ]["error"]


# ============================================================
# STREAM CHECK
# ============================================================

def check_stream(
    item: StreamEntry,
    timeout: float,
    read_limit: int,
    pacer: RequestPacer,
) -> None:

    started = time.perf_counter()

    last_error = None

    for ua in USER_AGENTS:

        pacer.wait()

        request = Request(
            item.url,
            method="GET",
            headers={
                "User-Agent": ua,
                "Accept": (
                    "application/vnd.apple.mpegurl,"
                    "application/x-mpegURL,"
                    "audio/mpegurl,"
                    "*/*"
                ),
                "Connection": "close",
            },
        )

        try:

            context = (
                ssl.create_default_context()
            )

            with urlopen(
                request,
                timeout=timeout,
                context=context,
            ) as response:

                item.http_status = (
                    response.status
                )

                item.stream_content_type = (
                    response.headers.get(
                        "Content-Type"
                    )
                )

                final_url = (
                    response.geturl()
                    or item.url
                )

                payload = response.read(
                    read_limit
                )

                text_head = (
                    payload
                    .decode(
                        "utf-8",
                        errors="replace",
                    )[:4096]
                )

                item.stream_bytes_read = (
                    len(payload)
                )

                item.stream_latency_ms = round(
                    (
                        time.perf_counter()
                        - started
                    ) * 1000,
                    2,
                )

                final_host = (
                    urlparse(
                        final_url
                    ).hostname
                    or ""
                ).lower()

                item.is_stub = (
                    final_host == STUB_HOST
                    or STUB_HOST in final_url
                    or "rtk_block" in final_url
                    or "zabava-block" in final_url
                )

                if item.is_stub:

                    item.stream_status = (
                        "STUB"
                    )

                elif (
                    200
                    <= response.status
                    < 400
                    and "#EXTM3U"
                    in text_head
                ):

                    item.stream_status = (
                        "ONLINE"
                    )

                elif (
                    200
                    <= response.status
                    < 400
                ):

                    item.stream_status = (
                        "HTTP_OK"
                    )

                else:

                    item.stream_status = (
                        "HTTP_ERROR"
                    )

                return

        except HTTPError as exc:

            item.http_status = exc.code

            item.stream_latency_ms = round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            )

            item.stream_error = str(
                exc
            )

            if exc.code in {
                401,
                403,
            }:

                item.stream_status = (
                    "AUTH"
                )

                return

            if exc.code == 404:

                item.stream_status = (
                    "NOT_FOUND"
                )

                return

            last_error = str(exc)

            item.stream_status = (
                "HTTP_ERROR"
            )

        except (
            URLError,
            TimeoutError,
            socket.timeout,
        ) as exc:

            last_error = str(exc)

            item.stream_status = (
                "UNREACHABLE"
            )

            item.stream_latency_ms = round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            )

            item.stream_error = str(
                exc
            )

            break

        except Exception as exc:

            last_error = repr(exc)

            item.stream_status = (
                "ERROR"
            )

            item.stream_latency_ms = round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            )

            item.stream_error = repr(
                exc
            )

            break

    if (
        last_error
        and not item.stream_error
    ):
        item.stream_error = last_error


# ============================================================
# ALL STREAM CHECKS
# ============================================================

def check_all_streams(
    entries: list[StreamEntry],
    timeout: float,
    read_limit: int,
    workers: int,
    request_delay: float,
) -> None:

    print()
    print("=" * 70)
    print(" PHASE 2 / COMPLETE HOST × ALIAS STREAM CHECK")
    print("=" * 70)

    total = len(entries)

    done = 0

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

    print(
        f"[CHECK] candidates : {total}"
    )

    print(
        f"[CHECK] workers    : {workers}"
    )

    print(
        f"[CHECK] delay      : "
        f"{request_delay:.3f}s"
    )

    with ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        futures = {
            pool.submit(
                check_stream,
                item,
                timeout,
                read_limit,
                pacer,
            ): item
            for item in entries
        }

        for future in as_completed(
            futures
        ):

            item = futures[
                future
            ]

            try:

                future.result()

            except Exception as exc:

                item.stream_status = (
                    "ERROR"
                )

                item.stream_error = (
                    repr(exc)
                )

            done += 1

            print(
                f"[STREAM "
                f"{done:>5}/{total:<5}] "
                f"{item.stream_status:<12} "
                f"{str(item.http_status or '-'):>3} "
                f"{item.hostname:<45} "
                f"{item.path}"
            )


# ============================================================
# GRAPH
# ============================================================

def build_graph(
    entries: list[StreamEntry],
    nodes: dict,
) -> dict:

    services = {}
    hostnames = {}
    ips = {}
    aliases = {}

    for item in entries:

        service = item.service_id

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

            if item.stream_status == "ONLINE":

                aliases[
                    item.channel
                ][
                    "online"
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

    def normalize(
        value: dict,
    ) -> dict:

        result = {}

        for key, item in value.items():

            result[key] = {}

            for field_name, field_value in (
                item.items()
            ):

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
        if x.stream_status == "ONLINE"
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
                "84 observed aliases"
            ),
            "service_id_guessing": False,
            "hostname_bruteforce": False,
            "authorization_bypass": False,
        },

        "alias_inventory": {
            "count": len(CHANNEL_ALIASES),
            "aliases": CHANNEL_ALIASES,
        },

        "summary": {
            "cdn_hostnames": len(hosts),
            "service_ids": len(services),
            "account_ids": len(accounts),
            "ips": len(ips),
            "channels": len(channels),
            "unique_streams": len(entries),

            "online_nodes": sum(
                x["status"] == "ONLINE"
                for x in nodes.values()
            ),

            "online_streams": sum(
                x.stream_status == "ONLINE"
                for x in entries
            ),

            "http_ok_streams": sum(
                x.stream_status == "HTTP_OK"
                for x in entries
            ),

            "auth_streams": sum(
                x.stream_status == "AUTH"
                for x in entries
            ),

            "not_found_streams": sum(
                x.stream_status
                == "NOT_FOUND"
                for x in entries
            ),

            "stub_streams": sum(
                x.is_stub
                for x in entries
            ),
        },

        "online_aliases": sorted(
            {
                x.channel
                for x in online_entries
                if x.channel
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

def save_playlist(
    entries: list[StreamEntry],
    filename: Path,
) -> None:

    lines = [
        "#EXTM3U",
        f"#PLAYLIST:{CONSTELLATION_NAME}",
        f"#ENGINE:{ENGINE_NAME}",
        f"#VERSION:{ENGINE_VERSION}",
        "#DISCOVERY:NGENIX DIRECT observed hosts × aliases",
        "#GENERATED-UTC:" + utc_now(),
        "",
    ]

    current_group = None

    for item in entries:

        if item.stream_status not in {
            "ONLINE",
            "HTTP_OK",
            "not_checked",
        }:
            continue

        if item.is_stub:
            continue

        group = (
            item.group
            or (
                f"NGENIX • "
                f"{item.service_id}"
                if item.service_id
                else "NGENIX • OTHER"
            )
        )

        if group != current_group:

            lines.extend(
                [
                    "",
                    f"#GROUP:{group}",
                ]
            )

            current_group = group

        name = (
            item.name
            or item.channel
            or item.path
        )

        lines.append(
            f'#EXTINF:-1 '
            f'group-title="{group}",'
            f'{name} '
            f'[{item.hostname}]'
        )

        lines.append(
            item.url
        )

    filename.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ============================================================
# JSON
# ============================================================

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
        "source",
        "stream_error",
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

            row = asdict(item)

            writer.writerow(
                {
                    field_name: row.get(
                        field_name,
                        "",
                    )
                    for field_name in fields
                }
            )


# ============================================================
# HISTORY
# ============================================================

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
            "timestamp": inventory[
                "generated_at"
            ],
            "summary": inventory[
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
# REPORT
# ============================================================

def build_report(
    inventory: dict,
    graph: dict,
) -> str:

    summary = inventory[
        "summary"
    ]

    lines = [
        "============================================================",
        "       NGENIX CDN CONSTELLATION / ULTRA SKALA REPORT",
        "============================================================",
        f"Generated UTC : {inventory['generated_at']}",
        f"Engine        : {inventory['engine']}",
        f"Version       : {inventory['version']}",
        "",
        "DISCOVERY METHOD",
        "------------------------------------------------------------",
        "Observed hostname matrix × 84 channel aliases",
        "No sXXXXX brute force",
        "No hostname generation",
        "No authorization bypass",
        "",
        "SUMMARY",
        "------------------------------------------------------------",
        f"CDN hostnames : {summary['cdn_hostnames']}",
        f"Service IDs   : {summary['service_ids']}",
        f"Account IDs   : {summary['account_ids']}",
        f"Unique IPs    : {summary['ips']}",
        f"Channels      : {summary['channels']}",
        f"Streams       : {summary['unique_streams']}",
        f"Online nodes  : {summary['online_nodes']}",
        f"Online streams: {summary['online_streams']}",
        f"HTTP OK       : {summary['http_ok_streams']}",
        f"Auth/403      : {summary['auth_streams']}",
        f"404           : {summary['not_found_streams']}",
        f"Stub          : {summary['stub_streams']}",
        "",
        "ALIASES WITH ONLINE STREAMS",
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

    for service_id, data in sorted(
        graph["services"].items()
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

    for alias, data in sorted(
        graph["aliases"].items()
    ):

        lines.append(
            f"{alias:<30} "
            f"online={data['online']:<4} "
            f"hosts={len(data['hostnames']):<4} "
            f"streams={len(data['streams'])}"
        )

    lines.append("")
    lines.append(
        "============================================================"
    )

    return "\n".join(lines)


# ============================================================
# CONSOLE SUMMARY
# ============================================================

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
        if x.stream_status == "ONLINE"
    ]

    http_ok = [
        x
        for x in matrix_entries
        if x.stream_status == "HTTP_OK"
    ]

    auth = [
        x
        for x in matrix_entries
        if x.stream_status == "AUTH"
    ]

    not_found = [
        x
        for x in matrix_entries
        if x.stream_status == "NOT_FOUND"
    ]

    stubs = [
        x
        for x in matrix_entries
        if x.is_stub
    ]

    print()
    print("=" * 70)
    print(" MATRIX RESULT")
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
                if x.channel == alias
            }
        )

        print(
            f"  {alias:<30} "
            f"hosts={len(hosts)}"
        )


# ============================================================
# MAIN
# ============================================================

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
        "OBSERVED HOST × 84 ALIASES"
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

    # --------------------------------------------------------
    # OPTIONAL REPOSITORY
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # OBSERVED HOST SEED
    # --------------------------------------------------------

    if not args.no_seed:

        hosts = observed_hosts()

        print()
        print(
            "[OBSERVED] hostnames: "
            f"{len(hosts)}"
        )

        # ----------------------------------------------------
        # ALL OBSERVED HOST × ALIAS
        # ----------------------------------------------------

        if not args.no_matrix:

            entries.extend(
                build_alias_matrix()
            )

        # ----------------------------------------------------
        # OLD OBSERVED PATHS
        # ----------------------------------------------------

        if not args.no_special:

            entries.extend(
                seed_special_entries()
            )

    else:

        print(
            "[MODE] observed seed: OFF"
        )

    # --------------------------------------------------------
    # DEDUP
    # --------------------------------------------------------

    entries = merge_entries(
        entries
    )

    print()
    print("=" * 70)
    print(" CANONICAL INVENTORY")
    print("=" * 70)

    print(
        f"[CANON] Unique NGENIX endpoints: "
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

    # --------------------------------------------------------
    # SERVICE LIST
    # --------------------------------------------------------

    print()
    print(
        "[CANON] OBSERVED sXXXXX:"
    )

    for service in services:

        print(
            f"    {service}"
        )

    # --------------------------------------------------------
    # DNS / TCP
    # --------------------------------------------------------

    nodes = build_nodes(
        entries,
        args.timeout,
    )

    apply_node_results(
        entries,
        nodes,
    )

    # --------------------------------------------------------
    # HTTP / M3U8
    # --------------------------------------------------------

    if not args.no_stream_check:

        check_all_streams(
            entries,
            args.timeout,
            args.read_limit,
            args.workers,
            args.request_delay,
        )

    else:

        print()
        print(
            "[MODE] stream HTTP check: OFF"
        )

    # --------------------------------------------------------
    # GRAPH
    # --------------------------------------------------------

    inventory = build_inventory(
        entries,
        nodes,
    )

    graph = build_graph(
        entries,
        nodes,
    )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

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
        output_dir / OUTPUT_JSON,
    )

    save_json(
        graph,
        output_dir / OUTPUT_GRAPH,
    )

    save_playlist(
        entries,
        output_dir / OUTPUT_M3U,
    )

    save_csv(
        entries,
        output_dir / OUTPUT_CSV,
    )

    update_history(
        inventory,
        output_dir / OUTPUT_HISTORY,
    )

    report_path = (
        report_dir / OUTPUT_REPORT
    )

    report_path.write_text(
        build_report(
            inventory,
            graph,
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # MATRIX SUMMARY
    # --------------------------------------------------------

    print_matrix_summary(
        entries
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print(
        " NGENIX CONSTELLATION v4 ULTRA COMPLETE"
    )
    print("=" * 70)

    print(
        f"Hostnames : "
        f"{inventory['summary']['cdn_hostnames']}"
    )

    print(
        f"sXXXXX    : "
        f"{inventory['summary']['service_ids']}"
    )

    print(
        f"Aliases   : "
        f"{inventory['summary']['channels']}"
    )

    print(
        f"Streams   : "
        f"{inventory['summary']['unique_streams']}"
    )

    print(
        f"Node OK   : "
        f"{inventory['summary']['online_nodes']}"
    )

    print(
        f"Stream OK : "
        f"{inventory['summary']['online_streams']}"
    )

    print(
        f"HTTP OK   : "
        f"{inventory['summary']['http_ok_streams']}"
    )

    print(
        f"Auth/403  : "
        f"{inventory['summary']['auth_streams']}"
    )

    print(
        f"404       : "
        f"{inventory['summary']['not_found_streams']}"
    )

    print(
        f"Stub      : "
        f"{inventory['summary']['stub_streams']}"
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


if __name__ == "__main__":
    main()