#!/usr/bin/env python3
# Denis IPTV Builder / iptv_parser_v4_commits_extended.py

import re, requests, os, shutil, base64
from dataclasses import dataclass, field
from typing import List, Optional

# ==========================
# SCADA НУМЕРАЦИЯ КАНАЛОВ
# ==========================

def build_scada_code(global_number: int, sub_number: int = 1) -> str:
    return f"{global_number}.{sub_number}.E.F(00).{global_number}.{sub_number}"

# ==========================
# SAFE INT
# ==========================

def safe_int(value):
    try:
        return int(value)
    except:
        digits = ''.join(ch for ch in str(value) if ch.isdigit())
        if digits:
            return int(digits)
        return 1

# ==========================
# НОРМАЛИЗАЦИЯ
# ==========================

def normalize_channel_name(name: str) -> str:
    name = name.lstrip(" ,")  # убираем запятую и пробелы, точку НЕ трогаем
    name = name.replace("–", "-").replace("—", "-")
    name = re.sub(r"\s+", " ", name)
    name = name.replace("(", "").replace(")", "")
    return name.strip()

def normalize_tvg_id(tvg: str) -> str:
    if not tvg:
        return "1"
    tvg = tvg.strip()
    tvg = tvg.lstrip(" ,")  # точку НЕ трогаем
    return tvg

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
# ФОРСИРОВАННЫЕ КАНАЛЫ
# ==========================

FORCED_CHANNELS = {
    "День Победы": "http://iptv.mega.net.ru:8888/Den_pobedy_hd/index.m3u8"
}

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
            raw_name = m.group('name')
            current_name = normalize_channel_name(raw_name)

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
            current_number = normalize_tvg_id(number or current_name)

        elif line.startswith('#'):
            continue
        else:
            if current_name is None:
                continue

            ch = Channel(
                number=current_number,
                name=current_name,
                group=current_group,
                logo=current_logo,
            )

            stream = StreamInfo(source_id=source_id, url=line)
            ch.streams.append(stream)
            channels.append(ch)

            current_name = None
            current_group = None
            current_logo = None
            current_number = None

    return channels

# ==========================
# ЗАГРУЗКА КОММИТОВ
# ==========================

def load_commits(source: Source) -> List[Channel]:
    url = f"{source.git_repo}/commits?path={source.git_file}&per_page={source.commits_limit}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    commits = r.json()

    channels = []
    for commit in commits:
        sha = commit['sha']
        file_url = f"{source.git_repo}/contents/{source.git_file}?ref={sha}"
        r2 = requests.get(file_url, timeout=10)
        r2.raise_for_status()
        content = r2.json()
        decoded = base64.b64decode(content['content']).decode('utf-8')
        channels.extend(parse_m3u(decoded, source.id))
    return channels

# ==========================
# MERGE
# ==========================

def merge_channels(srcA_channels, srcB_channels, commitsA, commitsB):
    result: List[Channel] = []
    global_counter = 1

    all_channels = srcA_channels + srcB_channels + commitsA + commitsB

    for ch in all_channels:
        sub_counter = 1
        scada = build_scada_code(global_counter, sub_counter)

        # форсированные каналы
        if ch.name in FORCED_CHANNELS:
            forced_url = FORCED_CHANNELS[ch.name]
            stream = StreamInfo(source_id="FORCED", url=forced_url, alive=True, quality_score=100)
            ch.streams.append(stream)

        ch.scada_code = scada
        ch.name = f"{scada} {ch.name}"
        ch.best_stream = ch.streams[0] if ch.streams else None

        result.append(ch)
        global_counter += 1

    return result

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
    srcA_channels = parse_m3u(requests.get(SOURCE_A.playlist_url).text, SOURCE_A.id)
    srcB_channels = parse_m3u(requests.get(SOURCE_B.playlist_url).text, SOURCE_B.id)

    commitsA = load_commits(SOURCE_A)
    commitsB = load_commits(SOURCE_B)

    merged = merge_channels(srcA_channels, srcB_channels, commitsA, commitsB)

    write_m3u("stable_new.m3u", merged)
    log("[DONE] stable_new.m3u written")

if __name__ == "__main__":
    main()