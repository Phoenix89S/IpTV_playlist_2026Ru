#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import gzip
import io
import re
import xml.etree.ElementTree as ET

NGSCALA_URL = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/ngScala.txt"
EPG_URL = "http://epg.one/epg2.xml.gz"
OUTPUT_PLAYLIST = "ngSKALA.m3u"


def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.text


def fetch_epg_xml(url: str) -> ET.ElementTree:
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    gz = gzip.GzipFile(fileobj=io.BytesIO(r.content))
    data = gz.read()
    return ET.ElementTree(ET.fromstring(data))


def parse_ngscala(text: str):
    """
    Возвращает список каналов:
    [
      {
        "name": "filmzone HD",
        "node_streams": [
            "https://s70378.cdn.ngenix.net/filmzone/index.m3u8",
            "https://s70378.cdn.ngenix.net/filmzone/1/index.m3u8",
            ...
        ]
      },
      ...
    ]
    """
    channels = []
    current = None

    lines = text.splitlines()
    for line in lines:
        line = line.rstrip("\n")

        m_ch = re.match(r"\[КАНАЛ\]\s+(.*)", line)
        if m_ch:
            # новый канал
            if current:
                channels.append(current)
            current = {
                "name": m_ch.group(1).strip(),
                "node_streams": []
            }
            continue

        if current is None:
            continue

        if line.strip().startswith("[ПОТОКИ УЗЛА]"):
            # дальше идут потоки
            continue

        m_stream = re.match(r"\s*->\s+(https?://\S+)", line)
        if m_stream and current is not None:
            url = m_stream.group(1).strip()
            current["node_streams"].append(url)

    if current:
        channels.append(current)

    # фильтруем только те, у кого есть рабочие потоки
    channels = [c for c in channels if c["node_streams"]]
    return channels


def build_epg_map(tree: ET.ElementTree):
    """
    Строим карту каналов по EPG:
    { "filmzone HD": {"id": "...", "name": "...", "logo": "..."} }
    """
    root = tree.getroot()
    epg_map = {}

    for ch in root.findall("channel"):
        cid = ch.get("id", "").strip()
        display_name = None
        logo = None

        for e in ch:
            if e.tag == "display-name":
                if display_name is None:
                    display_name = (e.text or "").strip()
            if e.tag == "icon":
                logo = e.get("src", "").strip()

        if display_name:
            epg_map[display_name] = {
                "id": cid,
                "name": display_name,
                "logo": logo
            }

    return epg_map


def match_epg_for_channel(channel_name: str, epg_map: dict):
    """
    Простейшее сопоставление: точное совпадение.
    Если нет — возвращаем None.
    """
    if channel_name in epg_map:
        return epg_map[channel_name]

    # пробуем без " HD" в конце
    if channel_name.endswith(" HD"):
        base = channel_name[:-3].strip()
        if base in epg_map:
            return epg_map[base]

    # пробуем наоборот: если в epg есть "XXX HD", а у нас "XXX"
    for name, info in epg_map.items():
        if name.endswith(" HD"):
            base = name[:-3].strip()
            if base == channel_name:
                return info

    return None


def generate_playlist(channels, epg_map):
    """
    Генерируем плейлист ngSKALA.m3u:
    - для каждого канала:
      * первая рабочая ссылка -> группа "ЭФИРНЫЕ ТВ Ngenix 1"
      * последняя рабочая ссылка -> группа "ЭФИРНЫЕ ТВ Ngenix N"
    """
    lines = []
    lines.append("#EXTM3U")

    for ch in channels:
        name = ch["name"]
        streams = ch["node_streams"]
        if not streams:
            continue

        first_url = streams[0]
        last_url = streams[-1]

        epg_info = match_epg_for_channel(name, epg_map)
        if epg_info:
            tvg_id = epg_info["id"]
            tvg_name = epg_info["name"]
            tvg_logo = epg_info["logo"] or ""
        else:
            tvg_id = ""
            tvg_name = name
            tvg_logo = ""

        # Группа 1 — первый рабочий поток
        extinf_1 = (
            '#EXTINF:-1 tvg-id="{id}" tvg-name="{tname}" tvg-logo="{logo}" '
            'group-title="ЭФИРНЫЕ ТВ Ngenix 1",{name}'
        ).format(
            id=tvg_id,
            tname=tvg_name,
            logo=tvg_logo,
            name=name
        )
        lines.append(extinf_1)
        lines.append(first_url)

        # Группа N — последний рабочий поток
        extinf_n = (
            '#EXTINF:-1 tvg-id="{id}" tvg-name="{tname}" tvg-logo="{logo}" '
            'group-title="ЭФИРНЫЕ ТВ Ngenix N",{name}'
        ).format(
            id=tvg_id,
            tname=tvg_name,
            logo=tvg_logo,
            name=name
        )
        lines.append(extinf_n)
        lines.append(last_url)

    return "\n".join(lines) + "\n"


def main():
    # 1. Загружаем отчёт ngScala.txt
    ngscala_text = fetch_text(NGSCALA_URL)

    # 2. Парсим каналы и рабочие потоки
    channels = parse_ngscala(ngscala_text)

    # 3. Загружаем EPG
    epg_tree = fetch_epg_xml(EPG_URL)
    epg_map = build_epg_map(epg_tree)

    # 4. Генерируем плейлист
    playlist_text = generate_playlist(channels, epg_map)

    # 5. Сохраняем
    with open(OUTPUT_PLAYLIST, "w", encoding="utf-8") as f:
        f.write(playlist_text)


if __name__ == "__main__":
    main()