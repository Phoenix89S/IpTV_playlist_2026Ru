#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import gzip
import io
import xml.etree.ElementTree as ET
import os

M3U_URL = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/prowerka_epg.m3u"
EPG_GZ_URL = "http://epg.one/epg2.xml.gz"
EPG_XML_FILE = "epg2.xml"   # локальная база

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
# STAGE 1 — UNPACK & SAVE XML
# ---------------------------------------------------------

def ensure_epg_xml():
    if os.path.exists(EPG_XML_FILE):
        log(f"EPG XML FOUND → using local {EPG_XML_FILE}")
        return

    log("EPG XML NOT FOUND → downloading epg2.xml.gz...")
    gz_bytes = download(EPG_GZ_URL)

    log("EPG → decompressing...")
    with gzip.GzipFile(fileobj=io.BytesIO(gz_bytes)) as f:
        xml_data = f.read()

    log(f"EPG → saving XML → {EPG_XML_FILE}")
    with open(EPG_XML_FILE, "wb") as f:
        f.write(xml_data)

    log("EPG XML SAVED.")

# ---------------------------------------------------------
# STAGE 2 — LOAD LOCAL XMLTV
# ---------------------------------------------------------

def load_epg_local():
    log(f"EPG → loading local XML {EPG_XML_FILE}")
    with open(EPG_XML_FILE, "rb") as f:
        xml = f.read()

    root = ET.fromstring(xml)

    epg_channels = {}  # name → id

    for ch in root.findall("channel"):
        cid = ch.get("id")
        name = ch.findtext("display-name") or ""
        name = name.strip()

        if name:
            epg_channels[name] = cid

    log(f"EPG → loaded {len(epg_channels)} channels from local XML")
    return epg_channels

# ---------------------------------------------------------
# PARSE M3U
# ---------------------------------------------------------

def parse_m3u(text):
    log("M3U → parsing playlist...")
    lines = text.splitlines()
    channels = []
    current = None

    for line in lines:
        if line.startswith("#EXTINF"):
            current = {"raw": line, "url": None, "name": None}

            if "," in line:
                current["name"] = line.split(",", 1)[1].strip()

        elif line.startswith("http"):
            if current:
                current["url"] = line.strip()
                channels.append(current)
                current = None

    log(f"M3U → found {len(channels)} channels")
    return channels

# ---------------------------------------------------------
# STRICT NAME MATCH
# ---------------------------------------------------------

def match_channel_strict(ch, epg_channels):
    """
    Строгое совпадение имени канала.
    Если имя из M3U == имени из EPG → возвращаем id.
    """
    name = ch["name"]
    if name in epg_channels:
        return epg_channels[name]
    return None

# ---------------------------------------------------------
# BUILD NEW M3U
# ---------------------------------------------------------

def build(channels, epg_channels):
    log("BUILD → constructing merged playlist...")

    out = []
    out.append('#EXTM3U url-tvg="http://epg.one/epg2.xml.gz"')

    for ch in channels:
        epg_id = match_channel_strict(ch, epg_channels)
        name = ch["name"]
        url = ch["url"]

        if epg_id:
            extinf = f'#EXTINF:-1 tvg-id="{epg_id}",{name}'
        else:
            extinf = f'#EXTINF:-1,{name}'

        out.append(extinf)
        out.append(url)

    return "\n".join(out)

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    ensure_epg_xml()

    epg_channels = load_epg_local()

    log("START → downloading M3U...")
    m3u_bytes = download(M3U_URL)
    m3u_text = m3u_bytes.decode("utf-8", errors="ignore")

    channels = parse_m3u(m3u_text)

    merged = build(channels, epg_channels)

    with open("merged_epg_playlist.m3u", "w", encoding="utf-8") as f:
        f.write(merged)

    log("DONE → merged_epg_playlist.m3u created.")

if __name__ == "__main__":
    main()