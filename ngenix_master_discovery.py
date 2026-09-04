#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
        NGENIX MASTER DISCOVERY
        ngSKALA / ZOYE Discovery Engine
============================================================

Назначение
----------
Сбор и нормализация известных NGENIX URL из M3U/M3U8.

Функции
-------
1. Обнаружение всех *.cdn.ngenix.net hostname.
2. Поддержка:
       s70378.cdn.ngenix.net
       a3569457567-s70378.cdn.ngenix.net
       и других подобных hostname.
3. Извлечение service ID вида sXXXXX.
4. Извлечение channel/path/variant.
5. Объединение нескольких M3U.
6. Дедупликация URL.
7. Сохранение истории.
8. Формирование MASTER DISCOVERY M3U.
9. Формирование JSON inventory.
10. Формирование SKALA report.
11. Опциональный health-check только явно
    обнаруженных URL.

Важно
------
Скрипт НЕ перебирает неизвестные sXXXXX,
НЕ делает brute-force discovery и НЕ обходит
авторизацию/ограничения доступа.

============================================================
"""

from __future__ import annotations

import argparse
import json
import re
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
# CANONICAL NAMES
# ============================================================

ENGINE_NAME = "ngSKALA"

PLAYLIST_NAME = "NGENIX MASTER DISCOVERY"

OUTPUT_M3U = "NGENIX_MASTER_DISCOVERY.m3u"
OUTPUT_JSON = "NGENIX_MASTER_DISCOVERY.json"
OUTPUT_REPORT = "NGENIX_MASTER_DISCOVERY_SKALA.txt"
OUTPUT_HISTORY = "NGENIX_MASTER_DISCOVERY_HISTORY.json"


# ============================================================
# REGEX
# ============================================================

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


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class NgenixEntry:

    url: str

    hostname: str

    service_id: str | None

    path: str

    channel: str | None

    variant: str | None

    source: str

    name: str | None = None

    group: str | None = None

    status: str = "unknown"

    http_status: int | None = None

    latency_ms: float | None = None

    error: str | None = None

    first_seen: str | None = None

    last_seen: str | None = None


# ============================================================
# TIME
# ============================================================

def utc_now() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# HOST / SERVICE
# ============================================================

def extract_service_id(
    hostname: str,
) -> str | None:

    match = SERVICE_RE.search(
        hostname
    )

    if not match:
        return None

    return match.group(1).lower()


# ============================================================
# PATH
# ============================================================

def extract_channel(
    path: str,
) -> str | None:

    parts = [
        x for x in path.split("/")
        if x
    ]

    if not parts:
        return None

    return parts[0]


def extract_variant(
    path: str,
) -> str | None:

    parts = [
        x for x in path.split("/")
        if x
    ]

    if len(parts) >= 3:

        if parts[-1].lower() in {
            "index.m3u8",
            "variant.m3u8",
        }:

            return parts[-2]

    return None


# ============================================================
# EXTINF METADATA
# ============================================================

def parse_extinf(
    line: str,
) -> tuple[str | None, str | None]:

    name = None
    group = None

    comma = line.find(",")

    if comma >= 0:
        name = line[
            comma + 1:
        ].strip()

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
) -> list[NgenixEntry]:

    lines = text.splitlines()

    entries: list[NgenixEntry] = []

    current_name = None
    current_group = None

    for raw in lines:

        line = raw.strip()

        if not line:
            continue

        # ----------------------------------------------------
        # EXTINF
        # ----------------------------------------------------

        if line.startswith("#EXTINF"):

            current_name, current_group = (
                parse_extinf(line)
            )

            continue

        # ----------------------------------------------------
        # Other M3U directives
        # ----------------------------------------------------

        if line.startswith("#"):
            continue

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        if not line.lower().startswith(
            ("http://", "https://")
        ):
            continue

        match = HOST_RE.search(line)

        if not match:
            current_name = None
            current_group = None
            continue

        hostname = (
            match.group("host")
            .lower()
        )

        parsed = urlparse(line)

        path = parsed.path or "/"

        service_id = extract_service_id(
            hostname
        )

        channel = extract_channel(
            path
        )

        variant = extract_variant(
            path
        )

        now = utc_now()

        entries.append(
            NgenixEntry(

                url=line,

                hostname=hostname,

                service_id=service_id,

                path=path,

                channel=channel,

                variant=variant,

                source=source,

                name=current_name,

                group=current_group,

                first_seen=now,

                last_seen=now,
            )
        )

        current_name = None
        current_group = None

    return entries


# ============================================================
# NORMALIZATION
# ============================================================

def merge_entries(
    entries: Iterable[NgenixEntry],
) -> list[NgenixEntry]:

    database: dict[
        str,
        NgenixEntry
    ] = {}

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

    return sorted(
        database.values(),
        key=lambda x: (
            x.service_id or "zzzz",
            x.hostname,
            x.channel or "",
            x.path,
        ),
    )


# ============================================================
# INVENTORY
# ============================================================

def build_inventory(
    entries: list[NgenixEntry],
) -> dict:

    hosts = sorted(
        {
            x.hostname
            for x in entries
        }
    )

    service_ids = sorted(
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

    return {

        "engine": ENGINE_NAME,

        "playlist": PLAYLIST_NAME,

        "generated_at": utc_now(),

        "summary": {

            "hosts":
                len(hosts),

            "service_ids":
                len(service_ids),

            "channels":
                len(channels),

            "unique_urls":
                len(entries),
        },

        "hosts": hosts,

        "service_ids": service_ids,

        "channels": channels,

        "entries": [
            asdict(x)
            for x in entries
        ],
    }


# ============================================================
# HEALTH CHECK
# ============================================================

def health_check(
    item: NgenixEntry,
    timeout: float,
) -> None:

    started = time.perf_counter()

    request = Request(
        item.url,
        method="GET",
        headers={
            "User-Agent":
                "ngSKALA-NGENIX-MASTER-DISCOVERY/1.0",

            "Accept":
                "*/*",
        },
    )

    try:

        context = ssl.create_default_context()

        with urlopen(
            request,
            timeout=timeout,
            context=context,
        ) as response:

            item.http_status = (
                response.status
            )

            item.latency_ms = round(
                (
                    time.perf_counter()
                    - started
                ) * 1000,
                2,
            )

            if 200 <= response.status < 400:

                item.status = "OK"

            else:

                item.status = "HTTP_ERROR"

    except HTTPError as exc:

        item.http_status = exc.code

        item.status = "HTTP_ERROR"

        item.latency_ms = round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            2,
        )

        item.error = str(exc)

    except (URLError, TimeoutError) as exc:

        item.status = "UNREACHABLE"

        item.latency_ms = round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            2,
        )

        item.error = str(exc)

    except Exception as exc:

        item.status = "ERROR"

        item.error = repr(exc)


# ============================================================
# MASTER DISCOVERY M3U
# ============================================================

def save_master_m3u(
    entries: list[NgenixEntry],
    filename: str,
) -> None:

    lines = [

        "#EXTM3U",

        f"#PLAYLIST: {PLAYLIST_NAME}",

        f"#ENGINE: {ENGINE_NAME}",

        "#MODE: MASTER DISCOVERY",

        "#SOURCE: M3U / known NGENIX endpoints",

        f"#GENERATED-UTC: {utc_now()}",

        "",
    ]

    current_group = None

    for item in entries:

        group = item.service_id

        if not group:

            group = "OTHER"

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
            or "NGENIX endpoint"
        )

        # Добавляем hostname к имени,
        # если источники содержат несколько
        # NGENIX hostname для одного channel.

        if item.variant:

            name = (
                f"{name} "
                f"[v{item.variant}]"
            )

        lines.append(
            f'#EXTINF:-1 '
            f'tvg-id="{item.channel or ""}" '
            f'tvg-name="{name}" '
            f'group-title="{display_group}",'
            f'{name}'
        )

        lines.append(item.url)

    Path(filename).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ============================================================
# JSON
# ============================================================

def save_json(
    inventory: dict,
    filename: str,
) -> None:

    Path(filename).write_text(
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
    entries: list[NgenixEntry],
    filename: str,
) -> None:

    path = Path(filename)

    if path.exists():

        try:

            history = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

        except Exception:

            history = {
                "engine": ENGINE_NAME,
                "runs": [],
            }

    else:

        history = {
            "engine": ENGINE_NAME,
            "playlist": PLAYLIST_NAME,
            "runs": [],
        }

    snapshot = {

        "timestamp":
            utc_now(),

        "hosts": sorted(
            {
                x.hostname
                for x in entries
            }
        ),

        "service_ids": sorted(
            {
                x.service_id
                for x in entries
                if x.service_id
            }
        ),

        "urls": sorted(
            {
                x.url
                for x in entries
            }
        ),
    }

    history["runs"].append(
        snapshot
    )

    # Не позволяем history бесконечно
    # разрастаться внутри одного JSON.

    history["runs"] = history[
        "runs"
    ][-100:]

    path.write_text(
        json.dumps(
            history,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# SKALA REPORT
# ============================================================

def save_report(
    entries: list[NgenixEntry],
    filename: str,
) -> None:

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

    ok = sum(
        x.status == "OK"
        for x in entries
    )

    http_errors = sum(
        x.status == "HTTP_ERROR"
        for x in entries
    )

    unreachable = sum(
        x.status == "UNREACHABLE"
        for x in entries
    )

    lines = [

        "╔══════════════════════════════════════════════════════╗",

        "║          NGENIX MASTER DISCOVERY / SKALA            ║",

        "╠══════════════════════════════════════════════════════╣",

        f"║ Engine:              {ENGINE_NAME:<30} ║",

        f"║ Generated UTC:       {utc_now():<30} ║",

        "╠══════════════════════════════════════════════════════╣",

        f"║ CDN hostnames:       {len(hosts):<30} ║",

        f"║ Service IDs:         {len(services):<30} ║",

        f"║ Channels:            {len(channels):<30} ║",

        f"║ Unique URLs:         {len(entries):<30} ║",

        f"║ Healthy:             {ok:<30} ║",

        f"║ HTTP errors:         {http_errors:<30} ║",

        f"║ Unreachable:         {unreachable:<30} ║",

        "╚══════════════════════════════════════════════════════╝",

        "",

        "DISCOVERED HOSTNAMES",

        "--------------------",
    ]

    lines.extend(
        f"  {host}"
        for host in hosts
    )

    lines.extend(
        [
            "",
            "SERVICE IDs",
            "-----------",
        ]
    )

    lines.extend(
        f"  {service}"
        for service in services
    )

    lines.extend(
        [
            "",
            "CHANNELS",
            "--------",
        ]
    )

    lines.extend(
        f"  {channel}"
        for channel in channels
    )

    lines.extend(
        [
            "",
            "END OF REPORT",
        ]
    )

    Path(filename).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ============================================================
# CLI
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "NGENIX MASTER DISCOVERY / ngSKALA"
        )
    )

    parser.add_argument(
        "files",
        nargs="+",
        help="Input M3U/M3U8 files",
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Health-check explicitly discovered URLs"
        ),
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--output-dir",
        default=".",
    )

    args = parser.parse_args()

    output_dir = Path(
        args.output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # PARSE
    # --------------------------------------------------------

    all_entries: list[NgenixEntry] = []

    for filename in args.files:

        path = Path(filename)

        if not path.exists():

            print(
                f"[WARN] Не найден файл: "
                f"{filename}"
            )

            continue

        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        entries = parse_m3u(
            text,
            str(path),
        )

        print(
            f"[DISCOVERY] "
            f"{filename}: "
            f"{len(entries)} NGENIX URL"
        )

        all_entries.extend(entries)

    # --------------------------------------------------------
    # MERGE
    # --------------------------------------------------------

    entries = merge_entries(
        all_entries
    )

    print()
    print(
        f"[MASTER] "
        f"Уникальных URL: {len(entries)}"
    )

    # --------------------------------------------------------
    # HEALTH
    # --------------------------------------------------------

    if args.check:

        print(
            "[SKALA] "
            "Запуск controlled health-check..."
        )

        for item in entries:

            health_check(
                item,
                args.timeout,
            )

            print(
                f"[{item.status:<12}] "
                f"{item.http_status or '-':>3} "
                f"{str(item.latency_ms or '-'):>8} ms "
                f"{item.url}"
            )

    # --------------------------------------------------------
    # OUTPUTS
    # --------------------------------------------------------

    m3u_path = (
        output_dir / OUTPUT_M3U
    )

    json_path = (
        output_dir / OUTPUT_JSON
    )

    report_path = (
        output_dir / OUTPUT_REPORT
    )

    history_path = (
        output_dir / OUTPUT_HISTORY
    )

    inventory = build_inventory(
        entries
    )

    save_master_m3u(
        entries,
        str(m3u_path),
    )

    save_json(
        inventory,
        str(json_path),
    )

    save_report(
        entries,
        str(report_path),
    )

    update_history(
        entries,
        str(history_path),
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    print()
    print(
        "╔════════════════════════════════════════════╗"
    )

    print(
        "║      NGENIX MASTER DISCOVERY COMPLETE      ║"
    )

    print(
        "╠════════════════════════════════════════════╣"
    )

    print(
        f"║ Hosts:        {len(inventory['hosts']):<26} ║"
    )

    print(
        f"║ Services:     {len(inventory[