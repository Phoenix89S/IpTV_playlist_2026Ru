#!/usr/bin/env python3
# Denis IPTV Builder / iptv_parser_v3_single.py

import re
import requests
import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

# ==========================
# SCADA НУМЕРАЦИЯ КАНАЛОВ
# ==========================

def build_scada_code(channel_number: int, stream_number: int = 1) -> str:
    return f"{channel_number}.{stream_number}.E.F(00).{channel_number}.{stream_number}"

# ==========================
# ИСТОЧНИКИ
# ==========================

@dataclass
class Source:
    id: str
    playlist_url: str
    git_repo: str
    git_file: str
    commits_limit: int = 20

SOURCE_A = Source(
    id="A",
    playlist_url="https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVdonor.m3u",
    git_repo="https://api.github.com/repos/smolnp/IPTVru",
    git_file="IPTVdonor.m3u",
)

SOURCE_B = Source(
    id="B",
    playlist_url="https://raw.githubusercontent.com/smolnp/IPTVru/iptv-pro/IPTVххх.m3u",
    git_repo="https://api.github.com/repos/smolnp/IPTVru",
    git_file="IPTVххх.m3u",
)

# ==========================
# МОДЕЛИ
# ==========================

@dataclass
class StreamInfo:
    source_id: str
    url: str
    quality_score: float = 0.0
    alive: bool = False

@dataclass
class Channel:
    number: str
    name: str
    group: Optional[str] = None
    logo: Optional[str] = None
    scada_code: str = ""
    streams: List[StreamInfo] = field(default_factory=list)
    best_stream: Optional[StreamInfo] = None
    reserve_stream: Optional[StreamInfo] = None
    quality_history: List[StreamInfo] = field(default_factory=list)

# ==========================
# ЛОГГЕР
# ==========================

def log(msg: str):
    print(msg)

# ==========================
# ПАРСЕР M3U
# ==========================

EXTINF_RE = re.compile(r'#EXTINF:-1(?P<attrs>[^,]*),(?P<name>.*)')

def parse_m3u(content: str, source_id: str) -> List[Channel]:
    channels: List[Channel] = []
    current_name = None
    current_group = None
    current_logo = None
    current_number = None

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith('#EXTINF'):
            m = EXTINF_RE.match(line)
            if not m:
                continue
            attrs = m.group('attrs')
            current_name = m.group('name').strip()

            group = None
            logo = None
            number = None

            for part in attrs.split():
                if 'group-title=' in part:
                    group = part.split('=', 1)[1].strip('"')
                if 'tvg-logo=' in part:
                    logo = part.split('=', 1)[1].strip('"')
                if 'tvg-id=' in part:
                    number = part.split('=', 1)[1].strip('"')

            current_group = group
            current_logo = logo
            current_number = number or current_name

        elif line.startswith('#'):
            continue
        else:
            if current_name is None:
                continue

            try:
                num_int = int(current_number)
            except:
                num_int = 1

            scada = build_scada_code(num_int, 1)

            ch = Channel(
                number=current_number or current_name,
                name=f"{scada} {current_name}",
                group=current_group,
                logo=current_logo,
                scada_code=scada,
            )

            stream = StreamInfo(
                source_id=source_id,
                url=line,
            )
            ch.streams.append(stream)
            channels.append(ch)

            current_name = None
            current_group = None
            current_logo = None
            current_number = None

    return channels

# ==========================
# ФИЛЬТРЫ 18+
# ==========================

ADULT_CHANNELS = [
    "brazzers-tv-europe",
    "centoxcento",
    "dorcel",
    "dorcel-xxx",
    "eroxxxhd",
    "3258",
    "extasytv",
    "fuuu tv",
    "hustler-hd",
    "passionxxx",
    "penthouse-gold",
    "2779",
]

def is_adult_channel(name: str, group: Optional[str]) -> bool:
    if group and group.lower().strip() == "для взрослых":
        return True

    name_l = name.lower().strip()

    for bad in ADULT_CHANNELS:
        if bad in name_l:
            return True

    return False

# ==========================
# ФИЛЬТР МУСОРНЫХ ДОНРОВ
# ==========================

def is_bad_donor(url: str) -> bool:
    url_l = url.lower()

    if "ott.watch/stream/" in url_l:
        return True

    if "ru2.tvtm.one" in url_l and url_l.endswith("m3u8?"):
        return True

    return False

def check_stream_alive(url: str) -> bool:
    if is_bad_donor(url):
        return False
    try:
        r = requests.get(url, timeout=5, stream=True)
        if r.status_code not in (200, 206):
            return False
        ctype = r.headers.get('Content-Type', '')
        if 'text/html' in ctype.lower():
            return False
        return True
    except Exception:
        return False

# ==========================
# КАЧЕСТВО
# ==========================

def compute_quality_score(url: str) -> float:
    try:
        r = requests.get(url, timeout=5, stream=True)
        if r.status_code not in (200, 206):
            return 0.0
        base = 50.0
        latency = r.elapsed.total_seconds()
        score = base - latency * 10
        return max(0, min(score, 100))
    except Exception:
        return 0.0

# ==========================
# MERGE + НЕПРОПАДАНИЕ
# ==========================

def merge_with_persistence(old_channels, srcA_channels, srcB_channels):
    result = []

    index_old = {ch.number: ch for ch in old_channels}
    index_A = {ch.number: ch for ch in srcA_channels}
    index_B = {ch.number: ch for ch in srcB_channels}

    for number, old_ch in index_old.items():
        ch = Channel(
            number=old_ch.number,
            name=old_ch.name,
            group=old_ch.group,
            logo=old_ch.logo,
            scada_code=old_ch.scada_code,
        )

        old_streams = [s for s in old_ch.streams if s.source_id == "OLD"]
        ch.streams.extend(old_streams)

        srcA = index_A.get(number)
        srcB = index_B.get(number)

        streams_candidates = []

        if srcA and not is_adult_channel(srcA.name, srcA.group):
            for s in srcA.streams:
                if check_stream_alive(s.url):
                    s.alive = True
                    s.quality_score = compute_quality_score(s.url)
                    streams_candidates.append(s)

        if srcB and not is_adult_channel(srcB.name, srcB.group):
            for s in srcB.streams:
                if check_stream_alive(s.url):
                    s.alive = True
                    s.quality_score = compute_quality_score(s.url)
                    streams_candidates.append(s)

        if not streams_candidates:
            ch.best_stream = old_streams[0] if old_streams else None
            log(f"[KEEP] Channel {number}: both sources invalid, keeping OLD")
        else:
            streams_sorted = sorted(streams_candidates, key=lambda s: s.quality_score)
            ch.quality_history.extend(streams_sorted)
            best = streams_sorted[-1]
            ch.best_stream = best
            reserve = streams_sorted[-2] if len(streams_sorted) > 1 else (old_streams[0] if old_streams else None)
            ch.reserve_stream = reserve
            ch.streams.extend(streams_candidates)
            log(f"[QUALITY] Channel {number}: {streams_sorted[0].quality_score} → {best.quality_score} (improved)")

        result.append(ch)

    return result

# ==========================
# ЗАГРУЗКА
# ==========================

def load_m3u_file(path, source_id):
    with open(path, 'r', encoding='utf-8') as f:
        return parse_m3u(f.read(), source_id)

def load_m3u_url(url, source_id):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return parse_m3u(r.text, source_id)

def load_old_playlist():
    old_files = [f for f in os.listdir('.') if f.startswith("Denis_iptv_stable_Old_") and f.endswith(".m3u")]

    if not old_files:
        log("[INFO] OLD playlist not found → first run mode (no OLD)")
        return []

    old_files.sort()
    last_old = old_files[-1]
    log(f"[INFO] Using OLD playlist: {last_old}")

    with open(last_old, 'r', encoding='utf-8') as f:
        return parse_m3u(f.read(), "OLD")

# ==========================
# STABLE / OLD СОХРАНЕНИЕ
# ==========================

def save_new_old():
    old_files = [f for f in os.listdir('.') if f.startswith("Denis_iptv_stable_Old_") and f.endswith(".m3u")]

    if not old_files:
        next_num = 1
    else:
        old_files.sort()
        last = old_files[-1]
        num = int(last.split("_")[-1].split(".")[0])
        next_num = num + 1

    new_old_name = f"Denis_iptv_stable_Old_{next_num:03d}.m3u"
    shutil.copy("stable_new.m3u", new_old_name)
    log(f"[INFO] NEW OLD created: {new_old_name}")

def save_new_stable():
    stable_files = [f for f in os.listdir('.') if f.startswith("Denis_iptv_stable_") and f.endswith(".m3u")]

    if not stable_files:
        next_num = 1
    else:
        stable_files.sort()
        last = stable_files[-1]
        num = int(last.split("_")[-1].split(".")[0])
        next_num = num + 1

    new_stable_name = f"Denis_iptv_stable_{next_num:03d}.m3u"
    shutil.copy("stable_new.m3u", new_stable_name)
    log(f"[INFO] NEW STABLE created: {new_stable_name}")

# ==========================
# ВЫВОД
# ==========================

def write_m3u(path, channels):
    with open(path, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            best = ch.best_stream or (ch.streams[0] if ch.streams else None)
            if not best:
                continue
            attrs = []
            if ch.group:
                attrs.append(f'group-title="{ch.group}"')
            if ch.logo:
                attrs.append(f'tvg-logo="{ch.logo}"')
            attrs.append(f'tvg-id="{ch.number}"')
            attrs.append(f'E-Kanon="{ch.scada_code}"')
            attr_str = " " + " ".join(attrs)
            f.write(f'#EXTINF:-1{attr_str},{ch.name}\n')
            f.write(best.url + "\n")

# ==========================
# MAIN
# ==========================

def main():
    old_channels = load_old_playlist()

    srcA_channels = load_m3u_url(SOURCE_A.playlist_url, SOURCE_A.id)
    srcB_channels = load_m3u_url(SOURCE_B.playlist_url, SOURCE_B.id)

    merged = merge_with_persistence(old_channels, srcA_channels, srcB_channels)

    write_m3u("stable_new.m3u", merged)
    log("[DONE] stable_new.m3u written")

    if old_channels:
        save_new_old()
        save_new_stable()

if __name__ == "__main__":
    main()
