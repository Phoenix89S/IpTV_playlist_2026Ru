#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NGENIX NODE PROBE
Безопасный опрос HLS-узла по известной точке входа.

Что делает:
  - запрашивает исходный M3U8;
  - определяет MASTER или MEDIA playlist;
  - извлекает EXT-X-STREAM-INF;
  - извлекает EXT-X-MEDIA;
  - сохраняет обнаруженные HLS URL;
  - пытается получить метаданные из playlist;
  - формирует M3U;
  - не выполняет brute-force перебор URL;
  - ограничивает количество HTTP-запросов;
  - использует небольшой интервал между запросами;
  - не скачивает сегменты .ts/.m4s.

Запуск:
    python3 ngenix_node_probe.py

Или:
    python3 ngenix_node_probe.py URL
"""

from __future__ import annotations

import sys
import re
import time
import html
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from typing import Optional

import requests


# ============================================================
# CONFIG
# ============================================================

DEFAULT_URL = (
    "https://s55766.cdn.ngenix.net/"
    "s55766-media-origin/rline_high/"
    "tracks-v1a1/mono.m3u8"
)

OUTPUT_M3U = "ngenix_s55766_discovered.m3u"

REQUEST_TIMEOUT = 6

# Максимальное количество playlist-запросов.
# Это именно запросы к playlist, сегменты НЕ запрашиваются.
MAX_PLAYLIST_REQUESTS = 32

# Минимальная пауза между запросами.
REQUEST_DELAY = 0.35

USER_AGENT = (
    "NGENIX-Node-Probe/1.0 "
    "(HLS playlist discovery; low-rate; no segment download)"
)


# ============================================================
# DATA
# ============================================================

@dataclass
class Channel:
    url: str
    name: str = ""
    tvg_id: str = ""
    tvg_logo: str = ""
    group: str = ""
    source: str = ""
    bandwidth: Optional[int] = None
    resolution: str = ""
    codecs: str = ""
    language: str = ""
    attributes: dict = field(default_factory=dict)


# ============================================================
# HTTP
# ============================================================

class SafeSession:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/vnd.apple.mpegurl,"
                "application/x-mpegURL,"
                "application/octet-stream,"
                "*/*"
            ),
            "Connection": "close",
        })

        self.requests_made = 0
        self.last_request = 0.0

    def get_playlist(self, url: str) -> Optional[str]:
        if self.requests_made >= MAX_PLAYLIST_REQUESTS:
            print("[LIMIT] Максимум playlist-запросов достигнут.")
            return None

        # Rate-limit
        elapsed = time.monotonic() - self.last_request

        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)

        self.last_request = time.monotonic()
        self.requests_made += 1

        print(
            f"[HTTP {self.requests_made:02d}/"
            f"{MAX_PLAYLIST_REQUESTS}] {url}"
        )

        try:
            response = self.session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            print(
                f"        HTTP {response.status_code} | "
                f"{len(response.content)} bytes"
            )

            if response.status_code != 200:
                return None

            content_type = response.headers.get(
                "Content-Type",
                ""
            )

            # HLS обычно содержит #EXTM3U независимо от MIME.
            text = response.text

            if "#EXTM3U" not in text[:4096]:
                print(
                    f"        [SKIP] Это не похоже на M3U8 "
                    f"(Content-Type: {content_type})"
                )
                return None

            return text

        except requests.RequestException as exc:
            print(f"        [ERROR] {exc}")
            return None


# ============================================================
# HLS PARSER
# ============================================================

ATTRIBUTE_RE = re.compile(
    r'([A-Z0-9-]+)=(".*?"|[^,]*)'
)


def parse_attributes(line: str) -> dict:
    """
    Разбирает:
      BANDWIDTH=123456,
      RESOLUTION=1920x1080,
      CODECS="avc1..."
    """

    result = {}

    for match in ATTRIBUTE_RE.finditer(line):
        key = match.group(1)
        value = match.group(2).strip()

        if len(value) >= 2:
            if value[0] == '"' and value[-1] == '"':
                value = value[1:-1]

        result[key] = value

    return result


def is_master_playlist(text: str) -> bool:
    return (
        "#EXT-X-STREAM-INF" in text
        or "#EXT-X-MEDIA:" in text
    )


def extract_master_entries(
    text: str,
    base_url: str,
) -> list[Channel]:

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    channels: list[Channel] = []

    pending_attrs = None

    for line in lines:

        if line.startswith("#EXT-X-STREAM-INF:"):
            attr_text = line.split(
                ":", 1
            )[1]

            pending_attrs = parse_attributes(attr_text)
            continue

        if pending_attrs is not None:

            if line.startswith("#"):
                continue

            stream_url = urljoin(
                base_url,
                line
            )

            bandwidth = None

            if pending_attrs.get("BANDWIDTH"):
                try:
                    bandwidth = int(
                        pending_attrs["BANDWIDTH"]
                    )
                except ValueError:
                    pass

            channels.append(
                Channel(
                    url=stream_url,
                    name=(
                        pending_attrs.get("NAME")
                        or pending_attrs.get("VIDEO")
                        or ""
                    ),
                    bandwidth=bandwidth,
                    resolution=pending_attrs.get(
                        "RESOLUTION", ""
                    ),
                    codecs=pending_attrs.get(
                        "CODECS", ""
                    ),
                    language=pending_attrs.get(
                        "LANGUAGE", ""
                    ),
                    source=base_url,
                    attributes=pending_attrs.copy(),
                )
            )

            pending_attrs = None

    return channels


def extract_media_entries(
    text: str,
    base_url: str,
) -> list[Channel]:

    channels: list[Channel] = []

    for line in text.splitlines():

        line = line.strip()

        if not line.startswith("#EXT-X-MEDIA:"):
            continue

        attr_text = line.split(
            ":",
            1
        )[1]

        attrs = parse_attributes(attr_text)

        uri = attrs.get("URI")

        if not uri:
            continue

        url = urljoin(base_url, uri)

        channels.append(
            Channel(
                url=url,
                name=(
                    attrs.get("NAME")
                    or attrs.get("GROUP-ID")
                    or ""
                ),
                language=attrs.get(
                    "LANGUAGE",
                    ""
                ),
                source=base_url,
                attributes=attrs.copy(),
            )
        )

    return channels


# ============================================================
# MEDIA PLAYLIST METADATA
# ============================================================

def extract_media_info(
    text: str,
) -> dict:

    result = {}

    # EXT-X-PLAYLIST-TYPE
    match = re.search(
        r"#EXT-X-PLAYLIST-TYPE:([^\r\n]+)",
        text
    )

    if match:
        result["playlist_type"] = (
            match.group(1).strip()
        )

    # EXT-X-TARGETDURATION
    match = re.search(
        r"#EXT-X-TARGETDURATION:(\d+)",
        text
    )

    if match:
        result["target_duration"] = int(
            match.group(1)
        )

    # EXT-X-VERSION
    match = re.search(
        r"#EXT-X-VERSION:(\d+)",
        text
    )

    if match:
        result["hls_version"] = int(
            match.group(1)
        )

    # наличие сегментов
    result["has_segments"] = bool(
        re.search(
            r"(?m)^(?!#).+\.(?:ts|m4s)(?:\?.*)?$",
            text
        )
    )

    return result


# ============================================================
# URL / NAME
# ============================================================

def channel_name_from_url(url: str) -> str:

    parsed = urlparse(url)

    path = parsed.path.strip("/")

    if not path:
        return ""

    parts = path.split("/")

    # Например:
    #
    # s55766-media-origin/rline_high/
    # tracks-v1a1/mono.m3u8
    #
    # => rline_high
    #

    for part in reversed(parts):
        if part.lower().endswith(".m3u8"):
            continue

        if part.startswith("tracks-"):
            continue

        if part:
            return part

    return ""


def clean_name(name: str) -> str:

    name = html.unescape(name)
    name = name.replace("_", " ")
    name = re.sub(r"\s+", " ", name)

    return name.strip()


# ============================================================
# DEDUP
# ============================================================

def deduplicate_channels(
    channels: list[Channel],
) -> list[Channel]:

    result = []
    seen = set()

    for channel in channels:

        key = channel.url.split("?", 1)[0]

        if key in seen:
            continue

        seen.add(key)
        result.append(channel)

    return result


# ============================================================
# ENRICH
# ============================================================

def enrich_channels(
    session: SafeSession,
    channels: list[Channel],
) -> list[Channel]:

    enriched = []

    for index, channel in enumerate(channels, 1):

        if session.requests_made >= MAX_PLAYLIST_REQUESTS:
            break

        print(
            f"\n[DISCOVER {index}/{len(channels)}]"
        )

        text = session.get_playlist(
            channel.url
        )

        if text is None:
            enriched.append(channel)
            continue

        # Если найденный URL снова оказался master,
        # извлекаем уже его дочерние потоки.
        if is_master_playlist(text):

            nested = extract_master_entries(
                text,
                channel.url
            )

            for item in nested:
                if not item.name:
                    item.name = channel.name

            enriched.extend(nested)

        else:
            info = extract_media_info(text)

            channel.attributes.update(info)

            enriched.append(channel)

    return enriched


# ============================================================
# M3U
# ============================================================

def make_extinf(channel: Channel) -> str:

    name = clean_name(channel.name)

    if not name:
        name = clean_name(
            channel_name_from_url(channel.url)
        )

    if not name:
        name = "NGENIX Channel"

    tvg_id = channel.tvg_id

    # Если сервер не предоставляет tvg-id,
    # используем стабильное имя.
    if not tvg_id:
        tvg_id = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "_",
            name.lower()
        ).strip("_")

    group = channel.group or "NGENIX"

    attrs = [
        f'tvg-id="{tvg_id}"',
        f'tvg-name="{name}"',
        f'group-title="{group}"',
    ]

    if channel.tvg_logo:
        attrs.append(
            f'tvg-logo="{channel.tvg_logo}"'
        )

    return (
        "#EXTINF:-1 "
        + " ".join(attrs)
        + ","
        + name
    )


def write_m3u(
    channels: list[Channel],
    filename: str,
) -> None:

    with open(
        filename,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:

        f.write("#EXTM3U\n")

        for channel in channels:

            f.write(
                make_extinf(channel)
                + "\n"
            )

            f.write(
                channel.url
                + "\n"
            )


# ============================================================
# REPORT
# ============================================================

def print_report(
    start_url: str,
    channels: list[Channel],
    requests_made: int,
) -> None:

    parsed = urlparse(start_url)

    print("\n")
    print("=" * 70)
    print(" NGENIX NODE PROBE RESULT")
    print("=" * 70)

    print(f"Node:      {parsed.hostname}")
    print(f"Start URL: {start_url}")
    print(f"Requests:  {requests_made}")
    print(f"Channels:  {len(channels)}")
    print("-" * 70)

    for number, channel in enumerate(
        channels,
        1,
    ):

        name = clean_name(channel.name)

        if not name:
            name = channel_name_from_url(
                channel.url
            )

        print(
            f"{number:03d}. "
            f"{name or 'Unknown'}"
        )

        if channel.resolution:
            print(
                f"      resolution: "
                f"{channel.resolution}"
            )

        if channel.bandwidth:
            print(
                f"      bandwidth: "
                f"{channel.bandwidth}"
            )

        print(
            f"      {channel.url}"
        )

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    start_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else DEFAULT_URL
    )

    print("=" * 70)
    print(" NGENIX SAFE NODE PROBE")
    print("=" * 70)
    print(f"Starting point:")
    print(start_url)
    print()
    print(
        "Режим: только опубликованные HLS playlist."
    )
    print(
        "Brute-force URL discovery: OFF."
    )
    print(
        "Segment download: OFF."
    )
    print(
        f"Max playlist requests: "
        f"{MAX_PLAYLIST_REQUESTS}"
    )
    print(
        f"Request delay: "
        f"{REQUEST_DELAY}s"
    )
    print("=" * 70)

    session = SafeSession()

    # --------------------------------------------------------
    # FIRST REQUEST
    # --------------------------------------------------------

    text = session.get_playlist(
        start_url
    )

    if text is None:
        print(
            "\n[FAIL] Исходный playlist "
            "не удалось получить."
        )
        return 1

    # --------------------------------------------------------
    # PARSE
    # --------------------------------------------------------

    channels: list[Channel] = []

    if is_master_playlist(text):

        print(
            "\n[TYPE] MASTER playlist"
        )

        channels.extend(
            extract_master_entries(
                text,
                start_url,
            )
        )

        channels.extend(
            extract_media_entries(
                text,
                start_url,
            )
        )

    else:

        print(
            "\n[TYPE] MEDIA playlist"
        )

        # Сам исходный URL уже является
        # обнаруженным потоком.
        channels.append(
            Channel(
                url=start_url,
                name=channel_name_from_url(
                    start_url
                ),
                source=start_url,
            )
        )

    channels = deduplicate_channels(
        channels
    )

    print(
        f"\n[FOUND] Первичных playlist: "
        f"{len(channels)}"
    )

    # --------------------------------------------------------
    # ENRICH
    # --------------------------------------------------------

    if channels:

        channels = enrich_channels(
            session,
            channels,
        )

        channels = deduplicate_channels(
            channels
        )

    # --------------------------------------------------------
    # WRITE
    # --------------------------------------------------------

    if channels:

        write_m3u(
            channels,
            OUTPUT_M3U,
        )

        print(
            f"\n[OK] M3U создан: "
            f"{OUTPUT_M3U}"
        )

    else:

        print(
            "\n[INFO] Дополнительных каналов "
            "не обнаружено."
        )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    print_report(
        start_url,
        channels,
        session.requests_made,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )