#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from urllib.parse import urljoin

SRC_URL = "https://iptv-org.github.io/iptv/regions/cis.m3u"

OUT_PLAYLIST = "SNG_Ru.m3u"
OUT_PLAYLIST_OLD = "SNG_Ru_Old.m3u"
REPORT_DIR = "reports"

HEADERS = {
    "User-Agent": "Mozilla/5.0 Phoenix89S-SNG-Validator"
}

#===========================================================
# СКАЛА/ДРЭГ ЛОГГЕР
#===========================================================

def log(msg):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] :: {msg}"
    print(line)
    LOG_LINES.append(line)

def log_ok(msg):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] :: OK :: {msg}"
    print(line)
    LOG_LINES.append(line)

def log_fail(msg):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] :: FAIL :: {msg}"
    print(line)
    LOG_LINES.append(line)

def log_chernobyl(channel_name, url):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] :: {channel_name} :: сигнал потерян (!). СТОПОРНЫЕ КРАНЫ ЗАКРЫТЫ! :: {url}"
    print(line)
    LOG_LINES.append(line)

#===========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
#===========================================================

def extract_name(extinf_line):
    if "," in extinf_line:
        return extinf_line.split(",", 1)[1].strip()
    return "НЕИЗВЕСТНЫЙ_КАНАЛ"

def load_m3u(url):
    log(f"LOAD_M3U :: {url}")
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text.splitlines()

def parse_m3u(lines):
    log("PARSE_M3U :: start")
    channels = []
    current = {}

    for line in lines:
        if line.startswith("#EXTINF"):
            current = {"extinf": line, "url": None}
        elif line.startswith("http"):
            current["url"] = line.strip()
            channels.append(current)
            current = {}

    log_ok(f"PARSE_M3U :: {len(channels)} каналов разобрано")
    return channels

def is_alive(extinf, url):
    channel_name = extract_name(extinf)

    try:
        r = requests.get(url, headers=HEADERS, timeout=10, stream=True)
        if r.status_code != 200:
            log_chernobyl(channel_name, url)
            return False

        text = r.text
        segs = re.findall(r"(.*\.ts)", text)

        if segs:
            seg = segs[0]
            seg_url = urljoin(url, seg)
            log(f"SEG_CHECK :: {channel_name} :: {seg_url}")

            r2 = requests.get(seg_url, headers=HEADERS, timeout=10, stream=True)
            if r2.status_code == 200:
                log_ok(f"{channel_name} :: поток активен :: {url}")
                return True
            else:
                log_chernobyl(channel_name, seg_url)
                return False

        log_ok(f"{channel_name} :: поток активен (без сегментов) :: {url}")
        return True

    except Exception as e:
        log_chernobyl(channel_name, url)
        return False

def save_playlist(channels):
    with open(OUT_PLAYLIST, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            f.write(ch["extinf"] + "\n")
            f.write(ch["url"] + "\n")

    log_ok(f"Плейлист сохранён :: {OUT_PLAYLIST} :: {len(channels)} каналов")

def save_old_playlist():
    if os.path.exists(OUT_PLAYLIST):
        with open(OUT_PLAYLIST, "r", encoding="utf-8") as src:
            data = src.read()
        with open(OUT_PLAYLIST_OLD, "w", encoding="utf-8") as dst:
            dst.write(data)
        log_ok(f"OLD-плейлист создан :: {OUT_PLAYLIST_OLD}")

def save_report():
    if not os.path.exists(REPORT_DIR):
        os.makedirs(REPORT_DIR)

    existing = [f for f in os.listdir(REPORT_DIR) if f.startswith("SNG_Ru_Report_")]
    next_num = len(existing) + 1
    filename = f"SNG_Ru_Report_{next_num}.txt"
    path = os.path.join(REPORT_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        f.write("#==============================================\n")
        f.write("# Система ЭВМ/ЭВС СКАЛА 3 ver 9 ДРЕГ Phoenix89S edition.\n")
        f.write("#==============================================\n\n")
        for line in LOG_LINES:
            f.write(line + "\n")

    log_ok(f"Отчёт сохранён :: {path}")

#===========================================================
# MAIN
#===========================================================

def main():
    global LOG_LINES
    LOG_LINES = []

    print("#==============================================")
    print("# Система ЭВМ/ЭВС СКАЛА 3 ver 9 ДРЕГ Phoenix89S edition.")
    print("#==============================================")

    log("START :: Phoenix SNG validator")

    save_old_playlist()

    lines = load_m3u(SRC_URL)
    channels = parse_m3u(lines)

    live = []
    for ch in channels:
        extinf = ch["extinf"]
        url = ch["url"]
        name = extract_name(extinf)

        log(f"ПРОВЕРКА :: {name} :: {url}")

        if is_alive(extinf, url):
            live.append(ch)

    save_playlist(live)
    save_report()

    log("REPORT :: DONE")
    log(f"TOTAL :: {len(channels)} каналов")
    log(f"LIVE  :: {len(live)} каналов")
    log("END :: SCALA/DRÆG REPORT COMPLETE")

if __name__ == "__main__":
    main()