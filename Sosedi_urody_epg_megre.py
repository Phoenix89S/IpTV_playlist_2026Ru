#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import io
import gzip
import requests
import xml.etree.ElementTree as ET

# ==========================================================
# CONFIG
# ==========================================================

M3U_URL = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/prowerka_epg.m3u"

EPG_SOURCES = [
    {
        "name": "epg.one",
        "xml": "epg2.xml",
        "gz": "http://epg.one/epg2.xml.gz",
        "priority": 1
    },
    {
        "name": "teleguide",
        "xml": "teleguide.xml",
        "gz": "http://www.teleguide.info/download/new3/xmltv.xml.gz",
        "priority": 2
    }
]

OUTPUT_FILE = "merged_epg_playlist.m3u"

# ==========================================================
# LOG
# ==========================================================

def log(msg):
    print(f"[LOG] {msg}")

def warn(msg):
    print(f"[WARN] {msg}")

def err(msg):
    print(f"[ERR] {msg}")

# ==========================================================
# DOWNLOAD
# ==========================================================

def download(url):

    log(f"DOWNLOAD -> {url}")

    r = requests.get(url, timeout=40)
    r.raise_for_status()

    return r.content

# ==========================================================
# XML CACHE
# ==========================================================

def ensure_epg_xml():

    for src in EPG_SOURCES:

        if os.path.exists(src["xml"]):

            log(f'Using cached {src["xml"]}')
            continue

        log(f'Downloading {src["name"]}')

        gz = download(src["gz"])

        with gzip.GzipFile(fileobj=io.BytesIO(gz)) as f:
            xml = f.read()

        with open(src["xml"], "wb") as out:
            out.write(xml)

        log(f'Saved {src["xml"]}')

# ==========================================================
# NORMALIZE CHANNEL NAME
# ==========================================================

REMOVE_WORDS = [
    "hd",
    "fhd",
    "uhd",
    "4k",
    "hevc",
    "h265",
    "h264",
    "50fps",
    "60fps",
    "sd"
]

SHIFT_RE = re.compile(r"\(\s*\+?(\d+)\s*\)")

CHANNEL_RE = re.compile(r"^\s*\d+\s*[\.\-]?\s*")

def extract_shift(name):

    m = SHIFT_RE.search(name)

    if m:
        return m.group(1)

    return None

def normalize_name(name):

    # убрать номер канала
    name = CHANNEL_RE.sub("", name)

    # убрать (+N)
    name = SHIFT_RE.sub("", name)

    name = name.lower()

    name = name.replace("ё", "е")

    for word in REMOVE_WORDS:

        name = re.sub(
            r"\b" + re.escape(word) + r"\b",
            "",
            name,
            flags=re.IGNORECASE
        )

    name = re.sub(r"[^\wа-я]+", " ", name)

    name = re.sub(r"\s+", " ", name)

    return name.strip()

# ==========================================================
# LOAD ALL XMLTV DATABASES
# ==========================================================

def load_epg_all():

    log("Loading EPG databases...")

    exact_names = {}      # Оригинальное имя -> (id, source)
    normalized = {}       # Нормализованное имя -> (id, source)

    total = 0

    for src in EPG_SOURCES:

        log(f'Loading {src["xml"]}')

        tree = ET.parse(src["xml"])
        root = tree.getroot()

        loaded = 0

        for channel in root.findall("channel"):

            cid = channel.get("id")

            if not cid:
                continue

            for disp in channel.findall("display-name"):

                if disp.text is None:
                    continue

                original = disp.text.strip()

                if not original:
                    continue

                norm = normalize_name(original)

                # Приоритет первой базы
                if original not in exact_names:
                    exact_names[original] = (cid, src["name"])

                if norm and norm not in normalized:
                    normalized[norm] = (cid, src["name"])

                loaded += 1

        total += loaded

        log(f'{src["name"]}: {loaded} display-name')

    log(f"Total display-name: {total}")
    log(f"Unique exact: {len(exact_names)}")
    log(f"Unique normalized: {len(normalized)}")

    return exact_names, normalized

# ==========================================================
# PARSE M3U
# ==========================================================

def parse_m3u(text):

    log("Parsing M3U...")

    channels = []

    current = None

    for line in text.splitlines():

        line = line.rstrip()

        if not line:
            continue

        if line.startswith("#EXTINF"):

            current = {
                "raw": line,
                "url": "",
                "name": "",
                "normalized": "",
                "shift": None
            }

            if "," in line:

                original_name = line.split(",", 1)[1].strip()

                current["name"] = original_name
                current["normalized"] = normalize_name(original_name)
                current["shift"] = extract_shift(original_name)

        elif line.startswith("http"):

            if current:

                current["url"] = line.strip()

                channels.append(current)

                current = None

    log(f"Channels found: {len(channels)}")

    return channels

# ==========================================================
# MATCH CHANNEL
# ==========================================================

def match_channel(channel, exact_names, normalized):

    # 1. Точное совпадение
    if channel["name"] in exact_names:
        return exact_names[channel["name"]]

    # 2. Без учёта регистра
    lname = channel["name"].lower()

    for epg_name, value in exact_names.items():
        if epg_name.lower() == lname:
            return value

    # 3. По нормализованному имени
    norm = channel["normalized"]

    if norm in normalized:
        return normalized[norm]

    return None

# ==========================================================
# TVG ATTRIBUTE HELPERS
# ==========================================================

def set_attr(line, attr, value):

    pattern = rf'{re.escape(attr)}="[^"]*"'

    if re.search(pattern, line):
        return re.sub(
            pattern,
            f'{attr}="{value}"',
            line,
            count=1
        )

    return line.replace(
        "#EXTINF:-1",
        f'#EXTINF:-1 {attr}="{value}"',
        1
    )

# ==========================================================
# BUILD PLAYLIST
# ==========================================================

def build_playlist(channels, exact_names, normalized):

    log("Building playlist...")

    out = [
        '#EXTM3U url-tvg="http://epg.one/epg2.xml.gz,http://www.teleguide.info/download/new3/xmltv.xml.gz"'
    ]

    matched = 0
    missed = 0

    source_stat = {
        "epg.one": 0,
        "teleguide": 0
    }

    for ch in channels:

        raw = ch["raw"]

        result = match_channel(ch, exact_names, normalized)

        if result:

            epg_id, source = result

            matched += 1

            if source in source_stat:
                source_stat[source] += 1

            # tvg-id
            raw = set_attr(raw, "tvg-id", epg_id)

            # tvg-shift
            if ch["shift"]:
                raw = set_attr(raw, "tvg-shift", ch["shift"])

        else:

            missed += 1
            warn(f'NOT FOUND: {ch["name"]}')

        out.append(raw)
        out.append(ch["url"])

    log(f"Matched : {matched}")
    log(f"Missed  : {missed}")
    log(f"EPG.ONE : {source_stat['epg.one']}")
    log(f"GUIDE   : {source_stat['teleguide']}")

    return "\n".join(out)

# ==========================================================
# BUILD SEARCH INDEX
# ==========================================================

def build_indexes():

    exact_names, normalized = load_epg_all()

    # индекс без учёта регистра
    lower_names = {}

    for name, value in exact_names.items():
        lower_names[name.lower()] = value

    return exact_names, lower_names, normalized

# ==========================================================
# FAST MATCH
# ==========================================================

def match_channel(channel, exact_names, lower_names, normalized):

    # 1. Точное совпадение
    result = exact_names.get(channel["name"])
    if result:
        return result

    # 2. Без учёта регистра
    result = lower_names.get(channel["name"].lower())
    if result:
        return result

    # 3. По очищенному имени
    result = normalized.get(channel["normalized"])
    if result:
        return result

    return None

# ==========================================================
# MAIN
# ==========================================================

def main():

    log("========== IPTV EPG MERGER V2 ==========")

    ensure_epg_xml()

    exact_names, lower_names, normalized = build_indexes()

    log("Downloading playlist...")

    playlist = download(M3U_URL).decode(
        "utf-8",
        errors="ignore"
    )

    channels = parse_m3u(playlist)

    # используем новый быстрый поиск
    def build_playlist_fast():

        out = [
            '#EXTM3U url-tvg="http://epg.one/epg2.xml.gz,http://www.teleguide.info/download/new3/xmltv.xml.gz"'
        ]

        matched = 0
        missed = 0

        for ch in channels:

            raw = ch["raw"]

            result = match_channel(
                ch,
                exact_names,
                lower_names,
                normalized
            )

            if result:

                epg_id, source = result

                raw = set_attr(raw, "tvg-id", epg_id)

                if ch["shift"]:
                    raw = set_attr(raw, "tvg-shift", ch["shift"])

                matched += 1

            else:

                missed += 1
                warn(f'NOT FOUND -> {ch["name"]}')

            out.append(raw)
            out.append(ch["url"])

        log(f"Matched : {matched}")
        log(f"Missed  : {missed}")

        return "\n".join(out)

    merged = build_playlist_fast()

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="\n"
    ) as f:

        f.write(merged)

    log(f"Saved -> {OUTPUT_FILE}")

    log("========== DONE ==========")

# ==========================================================
# START
# ==========================================================

if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:
        warn("Interrupted by user")

    except Exception as e:
        err(str(e))
        raise