#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
        NGENIX CDN CONSTELLATION
        ngSKALA / ZOYE Discovery Engine
============================================================

Полный проход по УЖЕ ОБНАРУЖЕННЫМ NGENIX endpoints.

Порядок работы:

    1. Поиск источников в репозитории.
    2. Извлечение всех *.cdn.ngenix.net URL.
    3. Нормализация и дедупликация.
    4. Формирование полного списка CDN hostname.
    5. Проверка каждого обнаруженного hostname.
    6. Проверка каждого обнаруженного stream URL.
    7. Сбор телеметрии.
    8. Формирование итогового M3U.
    9. Формирование JSON inventory.
   10. Формирование SKALA report.
   11. Обновление HISTORY.

ВАЖНО:

    Скрипт не перебирает неизвестные sXXXXX.
    Скрипт не делает brute-force.
    Скрипт не пытается обходить авторизацию.
    Проверяются только endpoints, обнаруженные
    в доступных входных данных репозитория.

============================================================
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import time

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# ============================================================
# CANON
# ============================================================

ENGINE_NAME = "ngSKALA"
CONSTELLATION_NAME = "NGENIX CDN CONSTELLATION"

OUTPUT_M3U = "NGENIX_CDN_CONSTELLATION.m3u"
OUTPUT_JSON = "NGENIX_CDN_CONSTELLATION.json"
OUTPUT_REPORT = "NGENIX_CDN_CONSTELLATION_SKALA.txt"
OUTPUT_HISTORY = "NGENIX_CDN_CONSTELLATION_HISTORY.json"

DEFAULT_OUTPUT_DIR = "data/ngenix_constellation"
DEFAULT_REPORT_DIR = "reports/ngenix_constellation"

# Ищем только реальные NGENIX URL.
HOST_RE = re.compile(
    r"https?://"
    r"(?P<host>[a-z0-9-]+)"
    r"\.cdn\.ngenix\.net"
    r"(?P<path>/[^\s\"<>]*)?",
    re.IGNORECASE,
)

SERVICE_RE = re.compile(
    r"(s\d{5})",
    re.IGNORECASE,
)

SUPPORTED_EXTENSIONS = {
    ".m3u",
    ".m3u8",
    ".txt",
    ".json",
}


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class StreamEntry:
    url: str
    hostname: str
    service_id: str | None
    path: str
    channel: str | None
    variant: str | None
    name: str | None
    group: str | None
    source: str

    node_status: str = "not_checked"
    node_ip: str | None = None
    node_latency_ms: float | None = None
    node_error: str | None = None

    stream_status: str = "not_checked"
    http_status: int | None = None
    stream_latency_ms: float | None = None
    stream_content_type: str | None = None
    stream_bytes_read: int = 0
    stream_error: str | None = None

    first_seen: str | None = None
    last_seen: str | None = None


# ============================================================
# TIME
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# SERVICE ID
# ============================================================

def extract_service_id(hostname: str) -> str | None:
    match = SERVICE_RE.search(hostname)
    return match.group(1).lower() if match else None


# ============================================================
# PATH
# ============================================================

def extract_channel(path: str) -> str | None:
    parts = [x for x in path.split("/") if x]

    if not parts:
        return None

    return parts[0]


def extract_variant(path: str) -> str | None:
    parts = [x for x in path.split("/") if x]

    if len(parts) >= 2:
        filename = parts[-1].lower()

        if filename in {
            "index.m3u8",
            "variant.m3u8",
            "master.m3u8",
        }:
            return parts[-2]

    return None


# ============================================================
# EXTINF
# ============================================================

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
# M3U PARSER
# ============================================================

def parse_m3u(
    text: str,
    source: str,
) -> list[StreamEntry]:

    entries: list[StreamEntry] = []

    current_name: str | None = None
    current_group: str | None = None

    for raw in text.splitlines():

        line = raw.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):
            current_name, current_group = parse_extinf(line)
            continue

        if line.startswith("#"):
            continue

        if not line.lower().startswith(
            ("http://", "https://")
        ):
            continue

        match = HOST_RE.search(line)

        if not match:
            current_name = None
            current_group = None
            continue

        url = line.strip()

        parsed = urlparse(url)

        hostname = (
            parsed.hostname or ""
        ).lower()

        path = parsed.path or "/"

        service_id = extract_service_id(
            hostname
        )

        now = utc_now()

        entries.append(
            StreamEntry(
                url=url,
                hostname=hostname,
                service_id=service_id,
                path=path,
                channel=extract_channel(path),
                variant=extract_variant(path),
                name=current_name,
                group=current_group,
                source=source,
                first_seen=now,
                last_seen=now,
            )
        )

        current_name = None
        current_group = None

    return entries


# ============================================================
# GENERIC TEXT / JSON DISCOVERY
# ============================================================

def discover_urls_in_text(
    text: str,
    source: str,
) -> list[StreamEntry]:

    entries: list[StreamEntry] = []

    for match in HOST_RE.finditer(text):

        url = match.group(0).rstrip(
            ".,;)]}>\"'"
        )

        try:
            parsed = urlparse(url)

            hostname = (
                parsed.hostname or ""
            ).lower()

            path = parsed.path or "/"

            now = utc_now()

            entries.append(
                StreamEntry(
                    url=url,
                    hostname=hostname,
                    service_id=extract_service_id(
                        hostname
                    ),
                    path=path,
                    channel=extract_channel(path),
                    variant=extract_variant(path),
                    name=None,
                    group=None,
                    source=source,
                    first_seen=now,
                    last_seen=now,
                )
            )

        except Exception:
            continue

    return entries


# ============================================================
# REPOSITORY DISCOVERY
# ============================================================

def discover_repository(
    root: Path,
) -> list[StreamEntry]:

    all_entries: list[StreamEntry] = []

    print()
    print(
        "╔══════════════════════════════════════════════════════╗"
    )
    print(
        "║             NGENIX CONSTELLATION DISCOVERY           ║"
    )
    print(
        "╚══════════════════════════════════════════════════════╝"
    )
    print()

    files_seen = 0

    for path in root.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        # Не читаем наши генерируемые результаты повторно.
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
                f"{len(found)} NGENIX URL"
            )

            all_entries.extend(found)

    print()
    print(
        f"[DISCOVERY] Проверено файлов: {files_seen}"
    )
    print(
        f"[DISCOVERY] Найдено URL: {len(all_entries)}"
    )

    return all_entries


# ============================================================
# DEDUPLICATION
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

        if item.source not in old.source:
            old.source = (
                f"{old.source}; {item.source}"
            )

    return sorted(
        database.values(),
        key=lambda x: (
            x.service_id or "zzzz",
            x.hostname,
            x.channel or "",
            x.path,
            x.url,
        ),
    )


# ============================================================
# NODE CHECK
# ============================================================

def check_node(
    hostname: str,
    timeout: float,
) -> tuple[
    str,
    str | None,
    float | None,
    str | None,
]:

    started = time.perf_counter()

    try:

        infos = socket.getaddrinfo(
            hostname,
            443,
            type=socket.SOCK_STREAM,
        )

        if not infos:
            return (
                "UNRESOLVED",
                None,
                None,
                "DNS returned no addresses",
            )

        ip = infos[0][4][0]

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
            "ONLINE",
            ip,
            latency,
            None,
        )

    except socket.gaierror as exc:

        return (
            "DNS_ERROR",
            None,
            None,
            str(exc),
        )

    except TimeoutError as exc:

        return (
            "TIMEOUT",
            None,
            round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            ),
            str(exc),
        )

    except OSError as exc:

        return (
            "NODE_ERROR",
            None,
            round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            ),
            str(exc),
        )

    except Exception as exc:

        return (
            "ERROR",
            None,
            None,
            repr(exc),
        )


# ============================================================
# STREAM CHECK
# ============================================================

def check_stream(
    item: StreamEntry,
    timeout: float,
    read_limit: int,
) -> None:

    started = time.perf_counter()

    request = Request(
        item.url,
        method="GET",
        headers={
            "User-Agent":
                "ngSKALA-NGENIX-CONSTELLATION/1.0",

            "Accept":
                "application/vnd.apple.mpegurl,"
                "application/x-mpegURL,"
                "audio/mpegurl,"
                "*/*",

            "Connection":
                "close",
        },
    )

    try:

        context = ssl.create_default_context()

        with urlopen(
            request,
            timeout=timeout,
            context=context,
        ) as response:

            item.http_status = response.status

            item.stream_content_type = (
                response.headers.get(
                    "Content-Type"
                )
            )

            payload = response.read(
                read_limit
            )

            item.stream_bytes_read = len(
                payload
            )

            item.stream_latency_ms = round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            )

            if 200 <= response.status < 400:

                item.stream_status = "ONLINE"

            else:

                item.stream_status = (
                    "HTTP_ERROR"
                )

    except HTTPError as exc:

        item.http_status = exc.code

        item.stream_status = "HTTP_ERROR"

        item.stream_latency_ms = round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            2,
        )

        item.stream_error = str(exc)

    except (URLError, TimeoutError) as exc:

        item.stream_status = "UNREACHABLE"

        item.stream_latency_ms = round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            2,
        )

        item.stream_error = str(exc)

    except Exception as exc:

        item.stream_status = "ERROR"

        item.stream_latency_ms = round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            2,
        )

        item.stream_error = repr(exc)


# ============================================================
# NODE INVENTORY
# ============================================================

def build_nodes(
    entries: list[StreamEntry],
    timeout: float,
) -> dict[str, dict]:

    hostnames = sorted(
        {
            item.hostname
            for item in entries
            if item.hostname
        }
    )

    nodes: dict[str, dict] = {}

    print()
    print(
        "============================================================"
    )
    print(
        "PHASE 1 / CDN NODE CHECK"
    )
    print(
        "============================================================"
    )

    for hostname in hostnames:

        status, ip, latency, error = check_node(
            hostname,
            timeout,
        )

        nodes[hostname] = {
            "hostname": hostname,
            "service_id": extract_service_id(
                hostname
            ),
            "status": status,
            "ip": ip,
            "latency_ms": latency,
            "error": error,
            "checked_at": utc_now(),
        }

        print(
            f"[NODE {status:<12}] "
            f"{hostname:<45} "
            f"{ip or '-':<16} "
            f"{str(latency or '-'):>8} ms"
        )

    return nodes


# ============================================================
# APPLY NODE RESULTS
# ============================================================

def apply_node_results(
    entries: list[StreamEntry],
    nodes: dict[str, dict],
) -> None:

    for item in entries:

        result = nodes.get(
            item.hostname
        )

        if not result:
            continue

        item.node_status = result["status"]
        item.node_ip = result["ip"]
        item.node_latency_ms = result[
            "latency_ms"
        ]
        item.node_error = result["error"]


# ============================================================
# STREAM PHASE
# ============================================================

def check_all_streams(
    entries: list[StreamEntry],
    timeout: float,
    read_limit: int,
) -> None:

    print()
    print(
        "============================================================"
    )
    print(
        "PHASE 2 / STREAM CHECK"
    )
    print(
        "============================================================"
    )

    for index, item in enumerate(
        entries,
        start=1,
    ):

        check_stream(
            item,
            timeout,
            read_limit,
        )

        print(
            f"[STREAM {index:>5}/{len(entries):<5}] "
            f"{item.stream_status:<12} "
            f"{item.http_status or '-':>3} "
            f"{str(item.stream_latency_ms or '-'):>8} ms "
            f"{item.url}"
        )


# ============================================================
# INVENTORY
# ============================================================

def build_inventory(
    entries: list[StreamEntry],
    nodes: dict[str, dict],
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

    channels = sorted(
        {
            x.channel
            for x in entries
            if x.channel
        }
    )

    online_nodes = sum(
        x["status"] == "ONLINE"
        for x in nodes.values()
    )

    online_streams = sum(
        x.stream_status == "ONLINE"
        for x in entries
    )

    return {
        "engine": ENGINE_NAME,
        "constellation": CONSTELLATION_NAME,
        "generated_at": utc_now(),

        "method": {
            "discovery": (
                "repository-known-endpoints"
            ),
            "node_check": "DNS + TCP/443",
            "stream_check": (
                "HTTP GET with bounded read"
            ),
            "bruteforce": False,
            "authorization_bypass": False,
        },

        "summary": {
            "cdn_hostnames": len(hosts),
            "service_ids": len(services),
            "channels": len(channels),
            "unique_streams": len(entries),
            "online_nodes": online_nodes,
            "online_streams": online_streams,
        },

        "nodes": list(nodes.values()),

        "entries": [
            asdict(x)
            for x in entries
        ],
    }


# ============================================================
# M3U
# ============================================================

def save_playlist(
    entries: list[StreamEntry],
    filename: Path,
) -> None:

    lines = [
        "#EXTM3U",
        f"#PLAYLIST: {CONSTELLATION_NAME}",
        f"#ENGINE: {ENGINE_NAME}",
        "#MODE: DISCOVER → NODE → STREAM",
        "#DISCOVERY: known repository endpoints",
        "#GENERATED-UTC: " + utc_now(),
        "",
    ]

    current_group = None

    for item in entries:

        group = (
            item.service_id
            or "NGENIX-OTHER"
        )

        display_group = (
            f"NGENIX • {group}"
        )

        if display_group != current_group:

            lines.extend(
                [
                    "",
                    f"#GROUP: {display_group}",
                ]
            )

            current_group = display_group

        name = (
            item.name
            or item.channel
            or "NGENIX stream"
        )

        status = item.stream_status

        if item.variant:
            name = (
                f"{name} "
                f"[{item.variant}]"
            )

        lines.append(
            f'#EXTINF:-1 '
            f'tvg-id="{item.channel or ""}" '
            f'tvg-name="{name}" '
            f'group-title="{display_group}" '
            f'x-ngenix-node="{item.hostname}" '
            f'x-ngenix-status="{status}",'
            f'{name}'
        )

        lines.append(item.url)

    filename.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ============================================================
# JSON
# ============================================================

def save_json(
    inventory: dict,
    filename: Path,
) -> None:

    filename.write_text(
        json.dumps(
            inventory,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# HISTORY
# ============================================================

def update_history(
    inventory: dict,
    filename: Path,
) -> None:

    if filename.exists():

        try:
            history = json.loads(
                filename.read_text(
                    encoding="utf-8"
                )
            )

        except Exception:

            history = {
                "engine": ENGINE_NAME,
                "constellation": CONSTELLATION_NAME,
                "runs": [],
            }

    else:

        history = {
            "engine": ENGINE_NAME,
            "constellation": CONSTELLATION_NAME,
            "runs": [],
        }

    snapshot = {
        "timestamp": utc_now(),
        "summary": inventory["summary"],
        "nodes": inventory["nodes"],
        "streams": [
            {
                "url": x["url"],
                "hostname": x["hostname"],
                "service_id": x["service_id"],
                "channel": x["channel"],
                "node_status": x["node_status"],
                "stream_status": x["stream_status"],
                "http_status": x["http_status"],
                "node_latency_ms": x[
                    "node_latency_ms"
                ],
                "stream_latency_ms": x[
                    "stream_latency_ms"
                ],
            }
            for x in inventory["entries"]
        ],
    }

    history["runs"].append(snapshot)

    history["runs"] = history["runs"][-100:]

    filename.write_text(
        json.dumps(
            history,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# SKALA
# ============================================================

def save_skala_report(
    inventory: dict,
    filename: Path,
) -> None:

    summary = inventory["summary"]

    nodes = inventory["nodes"]
    entries = inventory["entries"]

    node_online = sum(
        x["status"] == "ONLINE"
        for x in nodes
    )

    node_failed = len(nodes) - node_online

    stream_online = sum(
        x["stream_status"] == "ONLINE"
        for x in entries
    )

    stream_http_error = sum(
        x["stream_status"] == "HTTP_ERROR"
        for x in entries
    )

    stream_unreachable = sum(
        x["stream_status"] == "UNREACHABLE"
        for x in entries
    )

    lines = [

        "╔══════════════════════════════════════════════════════════════╗",
        "║                  NGENIX CDN CONSTELLATION                    ║",
        "║                     ngSKALA TELEMETRY                        ║",
        "╠══════════════════════════════════════════════════════════════╣",

        f"║ Engine:              {ENGINE_NAME:<36} ║",
        f"║ Generated UTC:       {inventory['generated_at']:<36} ║",

        "╠══════════════════════════════════════════════════════════════╣",

        f"║ CDN HOSTNAMES:       {summary['cdn_hostnames']:<36} ║",
        f"║ SERVICE IDs:         {summary['service_ids']:<36} ║",
        f"║ CHANNELS:            {summary['channels']:<36} ║",
        f"║ UNIQUE STREAMS:      {summary['unique_streams']:<36} ║",

        "╠══════════════════════════════════════════════════════════════╣",

        f"║ ONLINE NODES:        {summary['online_nodes']:<36} ║",
        f"║ ONLINE STREAMS:      {summary['online_streams']:<36} ║",

        "╠══════════════════════════════════════════════════════════════╣",

        f"║ NODE CHECK:          {node_online} ONLINE / {node_failed} FAILED{' ' * (24 - len(str(node_online)) - len(str(node_failed)))}║",
        f"║ STREAM ONLINE:       {stream_online:<36} ║",
        f"║ STREAM HTTP ERROR:   {stream_http_error:<36} ║",
        f"║ STREAM UNREACHABLE:  {stream_unreachable:<36} ║",

        "╚══════════════════════════════════════════════════════════════╝",

        "",
        "==================== CDN NODES ====================",
        "",
    ]

    for node in nodes:

        lines.append(
            f"[{node['status']:<12}] "
            f"{node['hostname']} "
            f"| IP={node['ip'] or '-'} "
            f"| {node['latency_ms'] or '-'} ms"
        )

    lines.extend(
        [
            "",
            "==================== STREAMS =====================",
            "",
        ]
    )

    for item in entries:

        lines.append(
            f"[{item['stream_status']:<12}] "
            f"{item['hostname']} "
            f"| {item['channel'] or '-'} "
            f"| HTTP={item['http_status'] or '-'} "
            f"| {item['stream_latency_ms'] or '-'} ms"
        )

        lines.append(
            f"    {item['url']}"
        )

    lines.extend(
        [
            "",
            "==================== END SKALA ===================",
        ]
    )

    filename.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "NGENIX CDN CONSTELLATION / ngSKALA"
        )
    )

    parser.add_argument(
        "--root",
        default=".",
        help=(
            "Root directory for automatic "
            "NGENIX discovery"
        ),
    )

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
    )

    parser.add_argument(
        "--report-dir",
        default=DEFAULT_REPORT_DIR,
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--read-limit",
        type=int,
        default=8192,
        help=(
            "Maximum bytes read from each "
            "stream manifest"
        ),
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()

    output_dir = Path(
        args.output_dir
    )

    report_dir = Path(
        args.report_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print(
        "============================================================"
    )
    print(
        " NGENIX CDN CONSTELLATION / ngSKALA"
    )
    print(
        "============================================================"
    )

    print(
        f"[ROOT] {root}"
    )

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    raw_entries = discover_repository(
        root
    )

    entries = merge_entries(
        raw_entries
    )

    print()
    print(
        f"[MASTER] Уникальных NGENIX streams: "
        f"{len(entries)}"
    )

    # --------------------------------------------------------
    # NOTHING FOUND
    # --------------------------------------------------------

    if not entries:

        raise SystemExit(
            "ERROR: NGENIX endpoints were not discovered."
        )

    # --------------------------------------------------------
    # NODE PHASE
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
    # STREAM PHASE
    # --------------------------------------------------------

    check_all_streams(
        entries,
        args.timeout,
        args.read_limit,
    )

    # --------------------------------------------------------
    # INVENTORY
    # --------------------------------------------------------

    inventory = build_inventory(
        entries,
        nodes,
    )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    m3u_path = (
        output_dir / OUTPUT_M3U
    )

    json_path = (
        output_dir / OUTPUT_JSON
    )

    history_path = (
        output_dir / OUTPUT_HISTORY
    )

    report_path = (
        report_dir / OUTPUT_REPORT
    )

    save_playlist(
        entries,
        m3u_path,
    )

    save_json(
        inventory,
        json_path,
    )

    update_history(
        inventory,
        history_path,
    )

    save_skala_report(
        inventory,
        report_path,
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    summary = inventory["summary"]

    print()
    print(
        "╔══════════════════════════════════════════════════════════════╗"
    )
    print(
        "║              NGENIX CDN CONSTELLATION COMPLETE               ║"
    )
    print(
        "╠══════════════════════════════════════════════════════════════╣"
    )

    print(
        f"║ Nodes:            {summary['cdn_hostnames']:<40} ║"
    )

    print(
        f"║ Services:         {summary['service_ids']:<40} ║"
    )

    print(
        f"║ Channels:         {summary['channels']:<40} ║"
    )

    print(
        f"║ Streams:          {summary['unique_streams']:<40} ║"
    )

    print(
        f"║ Online nodes:     {summary['online_nodes']:<40} ║"
    )

    print(
        f"║ Online streams:   {summary['online_streams']:<40} ║"
    )

    print(
        "╠══════════════════════════════════════════════════════════════╣"
    )

    print(
        f"║ PLAYLIST:         {m3u_path.name:<40} ║"
    )

    print(
        f"║ INVENTORY:        {json_path.name:<40} ║"
    )

    print(
        f"║ SKALA:            {report_path.name:<40} ║"
    )

    print(
        f"║ HISTORY:          {history_path.name:<40} ║"
    )

    print(
        "╚══════════════════════════════════════════════════════════════╝"
    )


if __name__ == "__main__":
    main()