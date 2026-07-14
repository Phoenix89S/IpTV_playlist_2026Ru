#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import io
import gzip
import requests
import xml.etree.ElementTree as ET

M3U_URL = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/prowerka_epg.m3u"
EPG_GZ_URL = "http://epg.one/epg2.xml.gz"
EPG_XML_FILE = "epg2.xml"

# ---------------------------------------------------------
# LOGGING
# ---------------------------------------------------------

def log(msg):
    print(f"[LOG] {msg}")

def log_err(msg):
    print(f"[ERR] {msg}")

# ---------------------------------------------------------
# DOWNLOAD
# ---------------------------------------------------------

def download(url):
    log(f"DOWNLOAD → {url}")

    r = requests.get(url, timeout=30)
    r.raise_for_status()

    return r.content

# ---------------------------------------------------------
# DOWNLOAD / UNPACK XML
# ---------------------------------------------------------

def ensure_epg_xml():

    if os.path.exists(EPG_XML_FILE):
        log(f"Using local {EPG_XML_FILE}")
        return

    log("Downloading EPG...")

    gz = download(EPG_GZ_URL)

    with gzip.GzipFile(fileobj=io.BytesIO(gz)) as f:
        xml = f.read()

    with open(EPG_XML_FILE, "wb") as out:
        out.write(xml)

    log("EPG saved.")

# ---------------------------------------------------------
# LOAD XMLTV
# ---------------------------------------------------------

def load_epg_local():

    log("Loading XMLTV...")

    tree = ET.parse(EPG_XML_FILE)
    root = tree.getroot()

    epg = {}

    for channel in root.findall("channel"):

        cid = channel.get("id")

        if not cid:
            continue

        for disp in channel.findall("display-name"):

            if disp.text is None:
                continue

            name = disp.text.strip()

            if not name:
                continue

            # Сохраняем ВСЕ display-name как есть
            epg[name] = cid

    log(f"Loaded {len(epg)} names.")

    return epg

# ---------------------------------------------------------
# PARSE M3U
# ---------------------------------------------------------

def parse_m3u(text):

    log("Parsing playlist...")

    channels = []

    current = None

    for line in text.splitlines():

        line = line.rstrip()

        if line.startswith("#EXTINF"):

            current = {
                "raw": line,
                "url": "",
                "name": ""
            }

            if "," in line:
                current["name"] = line.split(",",1)[1].strip()

        elif line.startswith("http"):

            if current:
                current["url"] = line
                channels.append(current)
                current = None

    log(f"Found {len(channels)} channels.")

    return channels

# ---------------------------------------------------------
# MATCH CHANNEL
# ---------------------------------------------------------

def match_channel(ch, epg):

    name = ch["name"]

    # Строгое совпадение имени
    if name in epg:
        return epg[name]

    # Без учёта регистра
    lower = name.lower()
    for epg_name, epg_id in epg.items():
        if epg_name.lower() == lower:
            return epg_id

    return None

# ---------------------------------------------------------
# BUILD PLAYLIST
# ---------------------------------------------------------

def build(channels, epg):

    log("Building playlist...")

    out = [
        '#EXTM3U url-tvg="http://epg.one/epg2.xml.gz"'
    ]

    matched = 0

    for ch in channels:

        raw = ch["raw"]
        url = ch["url"]

        epg_id = match_channel(ch, epg)

        if epg_id:

            matched += 1

            # Если tvg-id уже есть — заменить
            if 'tvg-id="' in raw:

                raw = re.sub(
                    r'tvg-id="[^"]*"',
                    f'tvg-id="{epg_id}"',
                    raw,
                    count=1
                )

            # Если нет — добавить сразу после #EXTINF:-1
            else:

                raw = raw.replace(
                    "#EXTINF:-1",
                    f'#EXTINF:-1 tvg-id="{epg_id}"',
                    1
                )

        out.append(raw)
        out.append(url)

    log(f"Matched {matched} of {len(channels)} channels.")

    return "\n".join(out)

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    ensure_epg_xml()

    epg = load_epg_local()

    log("Downloading playlist...")

    m3u = download(M3U_URL).decode(
        "utf-8",
        errors="ignore"
    )

    channels = parse_m3u(m3u)

    merged = build(channels, epg)

    output = "merged_epg_playlist.m3u"

    with open(output, "w", encoding="utf-8") as f:
        f.write(merged)

    log(f"Playlist saved: {output}")

# ---------------------------------------------------------
# START
# ---------------------------------------------------------

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_err("Interrupted by user.")
    except Exception as e:
        log_err(str(e))