#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import gzip
import io
import xml.etree.ElementTree as ET
import os
import re

M3U_URL = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/prowerka_epg.m3u"

EPG_SOURCES = [
    ("epg2.xml", "http://epg.one/epg2.xml.gz"),
    ("teleguide.xml", "http://www.teleguide.info/download/new3/xmltv.xml.gz")
]

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
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.content

# ---------------------------------------------------------
# UNPACK & SAVE XML
# ---------------------------------------------------------

def ensure_epg_xml():

    for xml_file, gz_url in EPG_SOURCES:

        if os.path.exists(xml_file):
            log(f"EPG XML FOUND → {xml_file}")
            continue

        log(f"Downloading {gz_url}")

        gz_bytes = download(gz_url)

        with gzip.GzipFile(fileobj=io.BytesIO(gz_bytes)) as f:
            xml_data = f.read()

        with open(xml_file, "wb") as out:
            out.write(xml_data)

        log(f"Saved {xml_file}")

# ---------------------------------------------------------
# LOAD ALL XMLTV
# ---------------------------------------------------------

def load_epg_all():

    epg_channels = {}

    for xml_file, _ in EPG_SOURCES:

        log(f"Loading {xml_file}")

        tree = ET.parse(xml_file)
        root = tree.getroot()

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

                # Приоритет первой базы
                if name not in epg_channels:
                    epg_channels[name] = cid

    log(f"Loaded {len(epg_channels)} names.")

    return epg_channels

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
                current["name"] = line.split(",", 1)[1].strip()

        elif line.startswith("http"):

            if current:
                current["url"] = line
                channels.append(current)
                current = None

    log(f"Found {len(channels)} channels.")

    return channels

# ---------------------------------------------------------
# CHANNEL MATCH
# ---------------------------------------------------------

def match_channel_strict(ch, epg_channels):

    name = ch["name"]

    # Точное совпадение
    if name in epg_channels:
        return epg_channels[name]

    # Совпадение без учёта регистра
    lname = name.lower()

    for epg_name, epg_id in epg_channels.items():
        if epg_name.lower() == lname:
            return epg_id

    return None

# ---------------------------------------------------------
# BUILD NEW M3U
# ---------------------------------------------------------

def build(channels, epg_channels):

    log("BUILD → constructing merged playlist...")

    out = [
        '#EXTM3U url-tvg="http://epg.one/epg2.xml.gz,http://www.teleguide.info/download/new3/xmltv.xml.gz"'
    ]

    matched = 0
    missed = 0

    for ch in channels:

        raw = ch["raw"]
        url = ch["url"]

        epg_id = match_channel_strict(ch, epg_channels)

        if epg_id:

            matched += 1

            # заменить существующий tvg-id
            if re.search(r'tvg-id="[^"]*"', raw):

                raw = re.sub(
                    r'tvg-id="[^"]*"',
                    f'tvg-id="{epg_id}"',
                    raw,
                    count=1
                )

            # добавить отсутствующий tvg-id
            else:

                raw = raw.replace(
                    "#EXTINF:-1",
                    f'#EXTINF:-1 tvg-id="{epg_id}"',
                    1
                )

        else:

            missed += 1
            log(f"NO MATCH → {ch['name']}")

        out.append(raw)
        out.append(url)

    log(f"Matched : {matched}")
    log(f"Missed  : {missed}")

    return "\n".join(out)

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    log("START")

    # Загружаем и распаковываем XML-базы EPG при необходимости
    ensure_epg_xml()

    # Загружаем все display-name из обеих баз
    epg_channels = load_epg_all()

    # Загружаем плейлист
    log("Downloading M3U playlist...")
    m3u_bytes = download(M3U_URL)
    m3u_text = m3u_bytes.decode("utf-8", errors="ignore")

    # Парсим плейлист
    channels = parse_m3u(m3u_text)

    # Собираем новый M3U
    merged = build(channels, epg_channels)

    # Сохраняем
    output_file = "merged_epg_playlist.m3u"

    with open(output_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(merged)

    log(f"DONE → {output_file} created.")

# ---------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_err("Interrupted by user.")
    except Exception as e:
        log_err(f"Fatal error: {e}")
        raise