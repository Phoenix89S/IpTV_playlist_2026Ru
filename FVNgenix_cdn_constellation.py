#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
         NGENIX CDN CONSTELLATION v3 ULTRA
            ngSKALA / ZOYE Discovery Engine
============================================================

РЕЖИМ ПО УМОЛЧАНИЮ:

        НЕ ИЩЕМ В РЕПО
        ИЩЕМ НА NGENIX
        (observed hosts + observed paths на *.cdn.ngenix.net)

НЕ делает:
  - поиск по локальному репозиторию (только если явно --repo)
  - перебор номеров sXXXXX
  - угадывание hostname
  - обход авторизации / подбор токенов

Делает:
  - прямые запросы к observed NGENIX-хостам
  - DNS + TCP/443 + HTTP GET (bounded)
  - детекция заглушки zabava-block-htvod
  - дедуп / история / graph / inventory / m3u / csv
============================================================
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ENGINE_NAME = "ngSKALA"
ENGINE_VERSION = "NGENIX-CONSTELLATION-v3-ULTRA"
CONSTELLATION_NAME = "NGENIX CDN CONSTELLATION ULTRA"

OUTPUT_M3U = "NGENIX_CDN_CONSTELLATION.m3u"
OUTPUT_JSON = "NGENIX_CDN_CONSTELLATION.json"
OUTPUT_GRAPH = "NGENIX_CDN_CONSTELLATION_GRAPH.json"
OUTPUT_REPORT = "NGENIX_CDN_CONSTELLATION_SKALA.txt"
OUTPUT_HISTORY = "NGENIX_CDN_CONSTELLATION_HISTORY.json"
OUTPUT_CSV = "NGENIX_CDN_CONSTELLATION.csv"

DEFAULT_OUTPUT_DIR = Path("data/ngenix_constellation")
DEFAULT_REPORT_DIR = Path("reports/ngenix_constellation")

HOST_RE = re.compile(
    r"https?://"
    r"(?P<host>[a-z0-9][a-z0-9.-]*?)"
    r"\.cdn\.ngenix\.net"
    r"(?P<path>/[^\s\"'<>]*)?",
    re.IGNORECASE,
)
SERVICE_RE = re.compile(r"(?<![a-z0-9-])(s\d{5,})(?![a-z0-9-])", re.IGNORECASE)
ACCOUNT_SERVICE_RE = re.compile(r"(a\d+-s\d{5,})", re.IGNORECASE)

SUPPORTED_EXTENSIONS = {
    ".m3u", ".m3u8", ".txt", ".json", ".yaml", ".yml",
    ".py", ".conf", ".cfg", ".ini", ".log", ".csv",
}

STUB_HOST = "zabava-block-htvod.cdn.ngenix.net"

USER_AGENTS = [
    "ngSKALA-NGENIX-CONSTELLATION/3.0",
    "WINK/RT (Android)",
    "WINK/1.40.1 (AndroidTV/9) HlsWinkPlayer",
    "SmartLabs",
]

# Observed hostnames only. No generated sXXXXX ranges.
SEED_S_HOSTS = [
    "s70378", "s91030", "s14131", "s20441", "s25617", "s26881",
    "s34351", "s37630", "s45177", "s55766", "s68149", "s78511",
    "s80718", "s97982", "s18209", "s92263", "s34776", "s12662",
    "s69362", "s12917", "s13511", "s14553", "s27836", "s68400",
    "s79369", "s80078", "s81121", "s84942", "s22674", "s35761",
    "s40403", "s41654", "s42963", "s64022", "s68717", "s70205",
    "s72169", "s73767", "s74794", "s95979", "s98217",
]

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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fqdn(label: str) -> str:
    label = label.lower().strip()
    if label.endswith(".cdn.ngenix.net"):
        return label
    return f"{label}.cdn.ngenix.net"


def extract_service_id(hostname: str) -> str | None:
    match = SERVICE_RE.search(hostname)
    return match.group(1).lower() if match else None


def extract_account_id(hostname: str) -> str | None:
    match = re.search(r"(a\d+)-", hostname, re.IGNORECASE)
    return match.group(1).lower() if match else None


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
    parts = [x for x in path.split("/") if x]
    if not parts:
        return None
    if parts[0].lower() == "hls" and len(parts) > 1:
        return parts[1]
    return parts[0]


def extract_variant(path: str) -> str | None:
    parts = [x for x in path.split("/") if x]
    if len(parts) < 2:
        return None
    filename = parts[-1].lower().split("?")[0]
    if filename in {"index.m3u8", "variant.m3u8", "master.m3u8", "playlist.m3u8", "mono.m3u8"}:
        return parts[-2]
    return None


def parse_extinf(line: str) -> tuple[str | None, str | None]:
    name = None
    group = None
    comma = line.find(",")
    if comma >= 0:
        name = line[comma + 1:].strip()
    match = re.search(r'group-title="([^"]*)"', line, re.IGNORECASE)
    if match:
        group = match.group(1)
    return name, group


def make_entry(
    url: str,
    source: str,
    name: str | None = None,
    group: str | None = None,
) -> StreamEntry | None:
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if not hostname.endswith(".cdn.ngenix.net"):
            return None
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        now = utc_now()
        return StreamEntry(
            url=url,
            hostname=hostname,
            service_id=extract_service_id(hostname),
            account_id=extract_account_id(hostname),
            hostname_type=classify_hostname(hostname),
            path=path,
            channel=extract_channel(parsed.path or "/"),
            variant=extract_variant(parsed.path or "/"),
            name=name,
            group=group,
            source=source,
            first_seen=now,
            last_seen=now,
        )
    except Exception:
        return None


def parse_m3u(text: str, source: str) -> list[StreamEntry]:
    entries = []
    current_name = None
    current_group = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            current_name, current_group = parse_extinf(line)
            continue
        if line.startswith("#"):
            continue
        if not line.lower().startswith(("http://", "https://")):
            continue
        if not HOST_RE.search(line):
            continue
        url = line.rstrip(".,;)]}>\"'")
        item = make_entry(url, source, current_name, current_group)
        if item:
            entries.append(item)
        current_name = None
        current_group = None
    return entries


def discover_urls_in_text(text: str, source: str) -> list[StreamEntry]:
    entries = []
    for match in HOST_RE.finditer(text):
        url = match.group(0).rstrip(".,;)]}>\"'")
        item = make_entry(url, source)
        if item:
            entries.append(item)
    return entries


def discover_repository(root: Path) -> list[StreamEntry]:
    entries = []
    files_seen = 0
    print()
    print("=" * 70)
    print(" PHASE 0 / REPOSITORY DISCOVERY")
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
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"[READ-ERROR] {path}: {exc}")
            continue
        if path.suffix.lower() in {".m3u", ".m3u8"}:
            found = parse_m3u(text, str(path))
        else:
            found = discover_urls_in_text(text, str(path))
        if found:
            print(f"[DISCOVERY] {path} -> {len(found)} endpoints")
            entries.extend(found)
    print(f"[DISCOVERY] files: {files_seen}")
    print(f"[DISCOVERY] raw endpoints: {len(entries)}")
    return entries


def seed_entries() -> list[StreamEntry]:
    entries = []
    labels = SEED_S_HOSTS + SEED_NAMED_HOSTS + SEED_LB_SUFFIXES + SEED_ACCOUNT_HOSTS
    for label in labels:
        host = fqdn(label)
        paths = list(SEED_PATHS_SPECIAL.get(host, []))
        if "s70378" in host:
            paths.extend(SEED_PATHS_S70378)
        if "htlive" in host or host.startswith("s"):
            paths.extend(SEED_PATHS_GENERIC)
        if not paths:
            paths = list(SEED_PATHS_GENERIC)
        seen_paths = []
        for path in paths:
            if path not in seen_paths:
                seen_paths.append(path)
        for path in seen_paths:
            url = f"https://{host}{path}"
            item = make_entry(url, "seed:observed")
            if item:
                entries.append(item)
    print(f"[SEED] observed host×path combos: {len(entries)}")
    return entries


def merge_entries(entries: Iterable[StreamEntry]) -> list[StreamEntry]:
    database = {}
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
        old_sources = set(old.source.split("; "))
        for source in item.source.split("; "):
            old_sources.add(source)
        old.source = "; ".join(sorted(old_sources))
    return sorted(
        database.values(),
        key=lambda x: (x.service_id or "zzzz", x.hostname, x.path, x.url),
    )


def resolve_all(hostname: str, timeout: float) -> tuple[list[str], float | None, str | None]:
    started = time.perf_counter()
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(hostname, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        ips = sorted({info[4][0] for info in infos})
        latency = round((time.perf_counter() - started) * 1000, 2)
        if not ips:
            return [], latency, "DNS returned no addresses"
        return ips, latency, None
    except socket.gaierror as exc:
        return [], None, str(exc)
    except Exception as exc:
        return [], None, repr(exc)
    finally:
        socket.setdefaulttimeout(old_timeout)


def check_ip(ip: str, timeout: float) -> tuple[bool, float | None, str | None]:
    started = time.perf_counter()
    try:
        with socket.create_connection((ip, 443), timeout=timeout):
            pass
        latency = round((time.perf_counter() - started) * 1000, 2)
        return True, latency, None
    except Exception as exc:
        return False, round((time.perf_counter() - started) * 1000, 2), str(exc)


def build_nodes(entries: list[StreamEntry], timeout: float) -> dict:
    hostnames = sorted({item.hostname for item in entries if item.hostname})
    nodes = {}
    print()
    print("=" * 70)
    print(" PHASE 1 / DNS + NODE DISCOVERY")
    print("=" * 70)
    for hostname in hostnames:
        ips, dns_latency, dns_error = resolve_all(hostname, timeout)
        reachable = []
        ip_results = {}
        for ip in ips:
            online, latency, error = check_ip(ip, timeout)
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
            "hostname_type": classify_hostname(hostname),
            "service_id": extract_service_id(hostname),
            "account_id": extract_account_id(hostname),
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
            f"[NODE {status:<12}] {hostname:<55} "
            f"A={len(ips):>2} ONLINE={len(reachable):>2}"
        )
    return nodes


def apply_node_results(entries: list[StreamEntry], nodes: dict) -> None:
    for item in entries:
        result = nodes.get(item.hostname)
        if not result:
            continue
        item.node_status = result["status"]
        item.node_ips = result["dns"]["addresses"]
        item.node_latency_ms = result["dns"]["latency_ms"]
        item.node_error = result["dns"]["error"]


def check_stream(item: StreamEntry, timeout: float, read_limit: int) -> None:
    started = time.perf_counter()
    last_error = None
    for ua in USER_AGENTS:
        request = Request(
            item.url,
            method="GET",
            headers={
                "User-Agent": ua,
                "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,audio/mpegurl,*/*",
                "Connection": "close",
            },
        )
        try:
            context = ssl.create_default_context()
            with urlopen(request, timeout=timeout, context=context) as response:
                item.http_status = response.status
                item.stream_content_type = response.headers.get("Content-Type")
                final_url = response.geturl() or item.url
                payload = response.read(read_limit)
                text_head = payload.decode("utf-8", errors="replace")[:256]
                item.stream_bytes_read = len(payload)
                item.stream_latency_ms = round((time.perf_counter() - started) * 1000, 2)
                item.is_stub = STUB_HOST in final_url or "rtk_block" in final_url
                if item.is_stub:
                    item.stream_status = "STUB"
                elif 200 <= response.status < 400 and "#EXTM3U" in text_head:
                    item.stream_status = "ONLINE"
                elif 200 <= response.status < 400:
                    item.stream_status = "HTTP_OK"
                else:
                    item.stream_status = "HTTP_ERROR"
                return
        except HTTPError as exc:
            item.http_status = exc.code
            item.stream_latency_ms = round((time.perf_counter() - started) * 1000, 2)
            item.stream_error = str(exc)
            if exc.code in {401, 403}:
                item.stream_status = "AUTH"
                return
            if exc.code == 404:
                item.stream_status = "NOT_FOUND"
                return
            last_error = str(exc)
            item.stream_status = "HTTP_ERROR"
        except (URLError, TimeoutError) as exc:
            last_error = str(exc)
            item.stream_status = "UNREACHABLE"
            item.stream_latency_ms = round((time.perf_counter() - started) * 1000, 2)
            item.stream_error = str(exc)
            break
        except Exception as exc:
            last_error = repr(exc)
            item.stream_status = "ERROR"
            item.stream_latency_ms = round((time.perf_counter() - started) * 1000, 2)
            item.stream_error = repr(exc)
            break
    if last_error and not item.stream_error:
        item.stream_error = last_error


def check_all_streams(entries: list[StreamEntry], timeout: float, read_limit: int, workers: int) -> None:
    print()
    print("=" * 70)
    print(" PHASE 2 / STREAM CHECK")
    print("=" * 70)
    total = len(entries)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(check_stream, item, timeout, read_limit): item
            for item in entries
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                future.result()
            except Exception as exc:
                item.stream_status = "ERROR"
                item.stream_error = repr(exc)
            done += 1
            print(
                f"[STREAM {done:>5}/{total:<5}] "
                f"{item.stream_status:<12} "
                f"{str(item.http_status or '-'):>3} "
                f"{item.url}"
            )


def build_graph(entries: list[StreamEntry], nodes: dict) -> dict:
    services = {}
    hostnames = {}
    ips = {}
    for item in entries:
        service = item.service_id
        if service:
            services.setdefault(service, {"service_id": service, "hostnames": set(), "streams": set()})
            services[service]["hostnames"].add(item.hostname)
            services[service]["streams"].add(item.url)
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
        hostnames[item.hostname]["streams"].add(item.url)
        for ip in item.node_ips:
            hostnames[item.hostname]["ips"].add(ip)
            ips.setdefault(ip, {"ip": ip, "hostnames": set(), "streams": set()})
            ips[ip]["hostnames"].add(item.hostname)
            ips[ip]["streams"].add(item.url)

    def normalize(value):
        result = {}
        for key, item in value.items():
            result[key] = {}
            for field_name, field_value in item.items():
                result[key][field_name] = sorted(field_value) if isinstance(field_value, set) else field_value
        return result

    return {
        "generated_at": utc_now(),
        "services": normalize(services),
        "hostnames": normalize(hostnames),
        "ips": normalize(ips),
        "nodes": nodes,
    }


def build_inventory(entries: list[StreamEntry], nodes: dict) -> dict:
    hosts = sorted({x.hostname for x in entries})
    services = sorted({x.service_id for x in entries if x.service_id})
    accounts = sorted({x.account_id for x in entries if x.account_id})
    channels = sorted({x.channel for x in entries if x.channel})
    ips = sorted({ip for x in entries for ip in x.node_ips})
    return {
        "engine": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "constellation": CONSTELLATION_NAME,
        "generated_at": utc_now(),
        "method": {
            "discovery": "NGENIX DIRECT observed hosts/paths",
            "service_id_guessing": False,
            "hostname_bruteforce": False,
            "authorization_bypass": False,
        },
        "summary": {
            "cdn_hostnames": len(hosts),
            "service_ids": len(services),
            "account_ids": len(accounts),
            "ips": len(ips),
            "channels": len(channels),
            "unique_streams": len(entries),
            "online_nodes": sum(x["status"] == "ONLINE" for x in nodes.values()),
            "online_streams": sum(x.stream_status == "ONLINE" for x in entries),
            "auth_streams": sum(x.stream_status == "AUTH" for x in entries),
            "stub_streams": sum(x.is_stub for x in entries),
        },
        "nodes": list(nodes.values()),
        "entries": [asdict(x) for x in entries],
    }


def save_playlist(entries: list[StreamEntry], filename: Path) -> None:
    lines = [
        "#EXTM3U",
        f"#PLAYLIST:{CONSTELLATION_NAME}",
        f"#ENGINE:{ENGINE_NAME}",
        f"#VERSION:{ENGINE_VERSION}",
        "#DISCOVERY:NGENIX DIRECT observed seeds only",
        "#GENERATED-UTC:" + utc_now(),
        "",
    ]
    current_group = None
    for item in entries:
        if item.stream_status not in {"ONLINE", "HTTP_OK", "not_checked"}:
            continue
        if item.is_stub:
            continue
        group = item.group or (f"NGENIX • {item.service_id}" if item.service_id else "NGENIX • OTHER")
        if group != current_group:
            lines.extend(["", f"#GROUP:{group}"])
            current_group = group
        name = item.name or item.channel or item.path
        lines.append(f'#EXTINF:-1 group-title="{group}",{name}')
        lines.append(item.url)
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_json(data: dict, filename: Path) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_csv(entries: list[StreamEntry], filename: Path) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "url", "hostname", "service_id", "account_id", "hostname_type",
        "path", "channel", "variant", "node_status", "stream_status",
        "http_status", "is_stub", "source",
    ]
    lines = [",".join(fields)]
    for item in entries:
        row = asdict(item)
        values = []
        for field_name in fields:
            value = str(row.get(field_name, "")).replace(",", " ")
            values.append(value)
        lines.append(",".join(values))
    filename.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_history(inventory: dict, filename: Path) -> None:
    history = []
    if filename.exists():
        try:
            history = json.loads(filename.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
    history.append({"timestamp": inventory["generated_at"], "summary": inventory["summary"]})
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def build_report(inventory: dict, graph: dict) -> str:
    summary = inventory["summary"]
    lines = [
        "============================================================",
        "   NGENIX CDN CONSTELLATION / ULTRA SKALA REPORT",
        "============================================================",
        f"Generated UTC: {inventory['generated_at']}",
        f"Engine: {inventory['engine']}",
        f"Version: {inventory['version']}",
        "",
        "SUMMARY",
        f"CDN hostnames : {summary['cdn_hostnames']}",
        f"Service IDs   : {summary['service_ids']}",
        f"Account IDs   : {summary['account_ids']}",
        f"Unique IPs    : {summary['ips']}",
        f"Channels      : {summary['channels']}",
        f"Streams       : {summary['unique_streams']}",
        f"Online nodes  : {summary['online_nodes']}",
        f"Online streams: {summary['online_streams']}",
        f"Auth/403      : {summary['auth_streams']}",
        f"Stub          : {summary['stub_streams']}",
        "",
        "SERVICE MAP",
    ]
    for service_id, data in sorted(graph["services"].items()):
        lines.append("")
        lines.append(f"[{service_id}]")
        lines.append("  HOSTS:")
        for hostname in data["hostnames"]:
            lines.append(f"    {hostname}")
    lines.append("")
    lines.append("============================================================")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="NGENIX CDN CONSTELLATION v3 ULTRA")
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="Не используется по умолчанию. Нужен только с --repo",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--read-limit", type=int, default=65536)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument(
        "--repo",
        action="store_true",
        help="Опционально: дополнительно искать URL в локальном репозитории",
    )
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--no-stream-check", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir)

    print()
    print("=" * 70)
    print(" MODE: NGENIX DIRECT  |  repo search: OFF (default)")
    print("=" * 70)

    entries: list[StreamEntry] = []
    if args.repo:
        root = Path(args.root or ".").resolve()
        print(f"[MODE] extra repo scan enabled: {root}")
        entries.extend(discover_repository(root))
    else:
        print("[MODE] репозиторий не сканируем")
        print("[MODE] цель: *.cdn.ngenix.net")

    if not args.no_seed:
        entries.extend(seed_entries())
    entries = merge_entries(entries)

    print()
    print(f"[CANON] Unique NGENIX endpoints: {len(entries)}")
    services = sorted({x.service_id for x in entries if x.service_id})
    print(f"[CANON] sXXXXX services: {len(services)}")
    for service in services:
        print(f"    {service}")

    nodes = build_nodes(entries, args.timeout)
    apply_node_results(entries, nodes)

    if not args.no_stream_check:
        check_all_streams(entries, args.timeout, args.read_limit, args.workers)

    inventory = build_inventory(entries, nodes)
    graph = build_graph(entries, nodes)

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    save_json(inventory, output_dir / OUTPUT_JSON)
    save_json(graph, output_dir / OUTPUT_GRAPH)
    save_playlist(entries, output_dir / OUTPUT_M3U)
    save_csv(entries, output_dir / OUTPUT_CSV)
    update_history(inventory, output_dir / OUTPUT_HISTORY)
    (report_dir / OUTPUT_REPORT).write_text(build_report(inventory, graph), encoding="utf-8")

    print()
    print("=" * 70)
    print(" NGENIX CONSTELLATION ULTRA COMPLETE")
    print("=" * 70)
    print(f"Hostnames : {inventory['summary']['cdn_hostnames']}")
    print(f"sXXXXX    : {inventory['summary']['service_ids']}")
    print(f"Streams   : {inventory['summary']['unique_streams']}")
    print(f"Node OK   : {inventory['summary']['online_nodes']}")
    print(f"Stream OK : {inventory['summary']['online_streams']}")
    print(f"Auth/403  : {inventory['summary']['auth_streams']}")
    print(f"Stub      : {inventory['summary']['stub_streams']}")
    print()
    print(f"[OUTPUT] {output_dir / OUTPUT_JSON}")
    print(f"[OUTPUT] {output_dir / OUTPUT_GRAPH}")
    print(f"[OUTPUT] {output_dir / OUTPUT_M3U}")
    print(f"[OUTPUT] {output_dir / OUTPUT_CSV}")
    print(f"[REPORT] {report_dir / OUTPUT_REPORT}")


if __name__ == "__main__":
    main()