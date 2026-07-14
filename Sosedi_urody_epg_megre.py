#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import io
import gzip
import time
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
# DOWNLOAD (с 3 попытками)
# ==========================================================

def download(url, timeout=40, retries=3):

    for attempt in range(1, retries + 1):

        log(f"DOWNLOAD -> {url} (attempt {attempt}/{retries})")

        try:
            r = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            r.raise_for_status()
            return r.content

        except Exception as e:
            warn(f"FAILED attempt {attempt}: {e}")
            time.sleep(2)

    err(f"DOWNLOAD FAILED -> {url}")
    return None

# ==========================================================
# XML CACHE (устойчивый)
# ==========================================================

def ensure_epg_xml():

    for src in EPG_SOURCES:

        xml_file = src["xml"]
        gz_url = src["gz"]

        if os.path.exists(xml_file):
            log(f"Using cached {xml_file}")
            continue

        log(f"Downloading {src['name']}")

        gz = download(gz_url)

        if gz is None:
            warn(f"SKIP {src['name']} (download failed)")
            continue

        try:
            with gzip.GzipFile(fileobj=io.BytesIO(gz)) as f:
                xml = f.read()

            with open(xml_file, "wb") as out:
                out.write(xml)

            log(f"Saved {xml_file}")

        except Exception as e:
            err(f"Cannot unpack {src['name']}")
            err(str(e))

# ==========================================================
# NORMALIZE CHANNEL NAME
# ==========================================================

REMOVE_WORDS = [
    "hd", "fhd", "uhd", "4k", "hevc", "h265", "h264",
    "50fps", "60fps", "sd"
]

SHIFT_RE = re.compile(r"\(\s*\+?(\d+)\s*\)")
CHANNEL_RE = re.compile(r"^\s*\d+\s*[\.\-]?\s*")

def extract_shift(name):
    m = SHIFT_RE.search(name)
    return m.group(1) if m else None

def normalize_name(name):

    name = CHANNEL_RE.sub("", name)
    name = SHIFT_RE.sub("", name)

    name = name.lower()
    name = name.replace("ё", "е")

    for word in REMOVE_WORDS:
        name = re.sub(rf"\b{word}\b", "", name, flags=re.IGNORECASE)

    name = re.sub(r"[^\wа-я]+", " ", name)
    name = re.sub(r"\s+", " ", name)

    return name.strip()

# ==========================================================
# LOAD ALL XMLTV DATABASES (устойчивый)
# ==========================================================

def load_epg_all():

    log("Loading EPG databases...")

    exact_names = {}
    normalized = {}

    total = 0

    for src in EPG_SOURCES:

        xml_file = src["xml"]

        if not os.path.exists(xml_file):
            warn(f"{xml_file} missing -> SKIP")
            continue

        log(f"Loading {xml_file}")

        try:
            tree = ET.parse(xml_file)
        except Exception as e:
            err(f"Cannot parse {xml_file}: {e}")
            continue

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

                if original not in exact_names:
                    exact_names[original] = (cid, src["name"])

                if norm not in normalized:
                    normalized[norm] = (cid, src["name"])

                loaded += 1

        total += loaded

        log(f"{src['name']}: {loaded} display-name")

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

def match_channel(ch, exact_names, normalized):

    if ch["name"] in exact_names:
        return exact_names[ch["name"]]

    lname = ch["name"].lower()
    for epg_name, value in exact_names.items():
        if epg_name.lower() == lname:
            return value

    norm = ch["normalized"]
    if norm in normalized:
        return normalized[norm]

    return None

# ==========================================================
# TVG ATTRIBUTE HELPERS
# ==========================================================

def set_attr(line, attr, value):

    pattern = rf'{attr}="[^"]*"'

    if re.search(pattern, line):
        return re.sub(pattern, f'{attr}="{value}"', line, count=1)

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

    for ch in channels:

        raw = ch["raw"]
        result = match_channel(ch, exact_names, normalized)

        if result:

            epg_id, source = result

            raw = set_attr(raw, "tvg-id", epg_id)

            if ch["shift"]:
                raw = set_attr(raw, "tvg-shift", ch["shift"])

            matched += 1

        else:
            missed += 1
            warn(f"NOT FOUND -> {ch['name']}")

        out.append(raw)
        out.append(ch["url"])

    log(f"Matched : {matched}")
    log(f"Missed  : {missed}")

    return "\n".join(out)

# ==========================================================
# MAIN
# ==========================================================

def main():

    log("========== IPTV EPG MERGER V3 ==========")

    ensure_epg_xml()

    exact_names, normalized = load_epg_all()

    playlist = download(M3U_URL)
    if playlist is None:
        err("Cannot download playlist")
        return

    playlist = playlist.decode("utf-8", errors="ignore")

    channels = parse_m3u(playlist)

    merged = build_playlist(channels, exact_names, normalized)

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(merged)

    log(f"Saved -> {OUTPUT_FILE}")
    log("========== DONE ==========")

# ==========================================================
# START
# ==========================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err(str(e))
        # НЕ падаем — GitHub Actions должен завершиться успешно
        pass