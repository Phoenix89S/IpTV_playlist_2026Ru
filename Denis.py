#!/usr/bin/env python3
# Denis IPTV Builder / iptv_parser_v9_full_turbo.py

import re
import requests
import os
import shutil
import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional

# ==========================
# TURBO режим
# ==========================
TURBO = True  # флаг включения турбо-режима

async def turbo_load_sources():
    with ThreadPoolExecutor(max_workers=4) as pool:
        loop = asyncio.get_event_loop()
        srcA_future = loop.run_in_executor(pool, lambda: requests.get(SOURCE_A.playlist_url, timeout=10).text)
        srcB_future = loop.run_in_executor(pool, lambda: requests.get(SOURCE_B.playlist_url, timeout=10).text)
        srcA_text, srcB_text = await asyncio.gather(srcA_future, srcB_future)
        srcA_channels = parse_m3u(srcA_text, SOURCE_A.id)
        srcB_channels = parse_m3u(srcB_text, SOURCE_B.id)
        commitsA_future = loop.run_in_executor(pool, lambda: load_commits(SOURCE_A))
        commitsB_future = loop.run_in_executor(pool, lambda: load_commits(SOURCE_B))
        commitsA, commitsB = await asyncio.gather(commitsA_future, commitsB_future)
    return srcA_channels, srcB_channels, commitsA, commitsB

# ==========================
# ЛОГГЕР
# ==========================
def log(msg: str):
    print(msg)

# ==========================
# SCADA НУМЕРАЦИЯ
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
    name = name.lstrip(" ,")
    name = name.replace("–", "-").replace("—", "-")
    name = re.sub(r"\s+", " ", name)
    name = name.replace("(", "").replace(")", "")
    return name.strip()

def normalize_tvg_id(tvg: str) -> str:
    if not tvg:
        return "1"
    tvg = tvg.strip().lstrip(" ,")
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

SOURCE_A = Source("A",
    "https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVdonor.m3u",
    "https://api.github.com/repos/smolnp/IPTVru","IPTVdonor.m3u")

SOURCE_B = Source("B",
    "https://raw.githubusercontent.com/smolnp/IPTVru/iptv-pro/IPTVххх.m3u",
    "https://api.github.com/repos/smolnp/IPTVru","IPTVхх.m3u")

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
# ФИЛЬТРЫ
# ==========================
ADULT_CHANNELS = ["brazzers","dorcel","hustler","penthouse","xxx","eroxxxhd","extasytv"]

def is_adult_channel(name: str, group: Optional[str]) -> bool:
    if group and group.lower().strip() == "для взрослых":
        return True
    name_l = name.lower().strip()
    return any(bad in name_l for bad in ADULT_CHANNELS)

def is_bad_donor(url: str) -> bool:
    url_l = url.lower()
    return "ott.watch/stream/" in url_l or ("ru2.tvtm.one" in url_l and url_l.endswith("m3u8?"))

def check_stream_alive(url: str) -> bool:
    if is_bad_donor(url): return False
    try:
        r = requests.get(url, timeout=5, stream=True)
        if r.status_code not in (200,206): return False
        if 'text/html' in r.headers.get('Content-Type','').lower(): return False
        return True
    except: return False

def compute_quality_score(url: str) -> float:
    try:
        r = requests.get(url, timeout=5, stream=True)
        if r.status_code not in (200,206): return 0.0
        return max(0,min(50.0 - r.elapsed.total_seconds()*10,100))
    except: return 0.0

# ==========================
# ПАРСЕР M3U
# ==========================
EXTINF_RE = re.compile(r'#EXTINF:-1(?P<attrs>[^,]*),(?P<name>.*)')

def parse_m3u(content: str, source_id: str) -> List[Channel]:
    channels=[]; current_name=None; current_group=None; current_logo=None; current_number=None
    for line in content.splitlines():
        line=line.strip()
        if not line: continue
        if line.startswith('#EXTINF'):
            m=EXTINF_RE.match(line)
            if not m: continue
            attrs=m.group('attrs'); raw_name=m.group('name')
            current_name=normalize_channel_name(raw_name)
            group=None; logo=None; number=None
            for part in attrs.split():
                if 'group-title=' in part: group=part.split('=',1)[1].strip('"')
                if 'tvg-logo=' in part: logo=part.split('=',1)[1].strip('"')
                if 'tvg-id=' in part: number=part.split('=',1)[1].strip('"')
            current_group=group; current_logo=logo; current_number=normalize_tvg_id(number or current_name)
        elif line.startswith('#'): continue
        else:
            if current_name is None: continue
            ch=Channel(number=current_number,name=current_name,group=current_group,logo=current_logo)
            ch.streams.append(StreamInfo(source_id=source_id,url=line))
            channels.append(ch)
            current_name=None; current_group=None; current_logo=None; current_number=None
    return channels

# ==========================
# ЗАГРУЗКА КОММИТОВ
# ==========================
def load_commits(source: Source) -> List[Channel]:
    url=f"{source.git_repo}/commits?path={source.git_file}&per_page={source.commits_limit}"
    r=requests.get(url,timeout=10); r.raise_for_status()
    commits=r.json(); channels=[]
    for commit in commits:
        sha=commit['sha']
        file_url=f"{source.git_repo}/contents/{source.git_file}?ref={sha}"
        r2=requests.get(file_url,timeout=10); r2.raise_for_status()
        content=r2.json()
        decoded=base64.b64decode(content['content']).decode('utf-8')
        channels.extend(parse_m3u(decoded,source.id))
    return channels

# ==========================
# ФОРСИРОВАННЫЕ КАНАЛЫ
# ==========================
FORCED_CHANNELS={"День Победы":"http://iptv.mega.net.ru:8888/Den_pobedy_hd/index.m3u8"}

# ==========================
# MERGE
# ==========================
def merge_channels(old_channels,srcA_channels,srcB_channels,commitsA,commitsB):
    result=[]; global_counter=1; index={}
    def add_or_merge_channel(ch:Channel):
        key=ch.number
        if key in index:
            existing=index[key]
            existing.streams.extend(ch.streams)
            if not existing.group and ch.group: existing.group=ch.group
            if not existing.logo and ch.logo: existing.logo=ch.logo
        else: index[key]=ch
    for ch in srcA_channels: add_or_merge_channel(ch)
    for ch in srcB_channels: add_or_merge_channel(ch)
    for ch in commitsA: add_or_merge_channel(ch)
    for ch in commitsB: add_or_merge_channel(ch)
    for ch in old_channels: add_or_merge_channel(ch)
    for number,ch in sorted(index.items(), key=lambda kv: safe_int(kv[0])):
        if is_adult_channel(ch.name,ch.group):
            log(f"[FILTER] Adult skipped: {ch.name}")
            continue
for number,ch in sorted(index.items(), key=lambda kv: safe_int(kv[0])):
        # фильтр 18+
        if is_adult_channel(ch.name,ch.group):
            log(f"[FILTER] Adult skipped: {ch.name}")
            continue

        # форсированные каналы
        if ch.name in FORCED_CHANNELS:
            forced_url=FORCED_CHANNELS[ch.name]
            forced_stream=StreamInfo(source_id="FORCED",url=forced_url,alive=True,quality_score=100.0)
            ch.streams.append(forced_stream)

        # проверка живости и оценка качества
        streams_candidates=[]
        for s in ch.streams:
            if check_stream_alive(s.url):
                s.alive=True
                s.quality_score=compute_quality_score(s.url)
                streams_candidates.append(s)

        # если все потоки мёртвые, но канал был в OLD — не удаляем
        if not streams_candidates and old_channels:
            old_match=next((o for o in old_channels if o.number==ch.number),None)
            if old_match and old_match.best_stream:
                ch.best_stream=old_match.best_stream
                log(f"[KEEP] Channel {ch.number}: sources invalid, keeping OLD best_stream")
            elif old_match and old_match.streams:
                ch.best_stream=old_match.streams[0]
                log(f"[KEEP] Channel {ch.number}: sources invalid, keeping OLD first stream")
            else:
                log(f"[WARN] Channel {ch.number}: no alive streams and no valid OLD, keeping channel without best_stream")
        else:
            streams_sorted=sorted(streams_candidates,key=lambda s:s.quality_score)
            if streams_sorted:
                best=streams_sorted[-1]
                ch.best_stream=best
                reserve=streams_sorted[-2] if len(streams_sorted)>1 else None
                ch.reserve_stream=reserve
                ch.quality_history.extend(streams_sorted)
                log(f"[QUALITY] Channel {ch.number}: best={best.quality_score}, count={len(streams_sorted)}")

        # SCADA‑нумерация
        scada=build_scada_code(global_counter,1)
        ch.scada_code=scada
        ch.name=f"{scada} {ch.name}"

        result.append(ch)
        global_counter+=1

    return result

# ==========================
# ВЫВОД M3U
# ==========================
def write_m3u(path:str,channels:List[Channel]):
    with open(path,"w",encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            best=ch.best_stream or (ch.streams[0] if ch.streams else None)
            if not best: continue
            attrs=[]
            if ch.group: attrs.append(f'group-title="{ch.group}"')
            if ch.logo: attrs.append(f'tvg-logo="{ch.logo}"')
            attrs.append(f'tvg-id="{ch.number}"')
            attrs.append(f'E-Kanon="{ch.scada_code}"')
            attr_str=" "+" ".join(attrs) if attrs else ""
            f.write(f'#EXTINF:-1{attr_str},{ch.name}\n')
            f.write(best.url+"\n")

# ==========================
# MAIN
# ==========================
def main():
    stable_files=[f for f in os.listdir('.') if f.startswith("Denis_iptv_stable_") and f.endswith(".m3u")]
    old_files=[f for f in os.listdir('.') if f.startswith("Denis_iptv_stable_Old_") and f.endswith(".m3u")]

    if old_files:
        old_files.sort(); last_old=old_files[-1]
        log(f"[INFO] Using OLD playlist: {last_old}")
        with open(last_old,"r",encoding="utf-8") as f:
            old_channels=parse_m3u(f.read(),"OLD")
        old_exists=True
    elif stable_files:
        stable_files.sort(); last_stable=stable_files[-1]
        log(f"[INFO] No OLD yet, using last STABLE as base: {last_stable}")
        with open(last_stable,"r",encoding="utf-8") as f:
            old_channels=parse_m3u(f.read(),"OLD")
        old_exists=False
    else:
        log("[INFO] First run → no STABLE and no OLD")
        old_channels=[]; old_exists=False

    if TURBO:
        log("[INFO] TURBO mode enabled → parallel loading")
        srcA_channels,srcB_channels,commitsA,commitsB=asyncio.run(turbo_load_sources())
    else:
        log("[INFO] Loading source A playlist")
        srcA_text=requests.get(SOURCE_A.playlist_url,timeout=10).text
        srcA_channels=parse_m3u(srcA_text,SOURCE_A.id)
        log("[INFO] Loading source B playlist")
        srcB_text=requests.get(SOURCE_B.playlist_url,timeout=10).text
        srcB_channels=parse_m3u(srcB_text,SOURCE_B.id)
        log("[INFO] Loading commits for source A")
        commitsA=load_commits(SOURCE_A)
        log("[INFO] Loading commits for source B")
        commitsB=load_commits(SOURCE_B)

    log("[INFO] Merging channels with persistence")
    merged=merge_channels(old_channels,srcA_channels,srcB_channels,commitsA,commitsB)

    if TURBO:
        qualities=np.array([ch.best_stream.quality_score for ch in merged if ch.best_stream])
        if qualities.size>0:
            log(f"[QUALITY] TURBO summary: max={np.max(qualities)}, avg={np.mean(qualities):.2f}, channels={len(qualities)}")

    write_m3u("stable_new.m3u",merged)
    log("[DONE] stable_new.m3u written")

    if stable_files:
        stable_files.sort(); last_stable=stable_files[-1]
        try: num=int(last_stable.split("_")[-1].split(".")[0])
        except Exception: num=0
        next_stable_num=num+1
    else:
        next_stable_num=1

    new_stable_name=f"Denis_iptv_stable_{next_stable_num:03d}.m3u"
    shutil.copy("stable_new.m3u",new_stable_name)
    log(f"[INFO] NEW STABLE created: {new_stable_name}")

    if old_files:
        old_files.sort(); last_old=old_files[-1]
        try: num=int(last_old.split("_")[-1].split(".")[0])
        except Exception: num=0
        next_old_num=num+1
        new_old_name=f"Denis_iptv_stable_Old_{next_old_num:03d}.m3u"
        shutil.copy("stable_new.m3u",new_old_name)
        log(f"[INFO] NEW OLD created: {new_old_name}")
    elif stable_files:
        new_old_name="Denis_iptv_stable_Old_001.m3u"
        shutil.copy("stable_new.m3u",new_old_name)
        log(f"[INFO] FIRST OLD created: {new_old_name}")
    else:
        log("[INFO] First run → OLD not created (will be created from second run)")

if __name__=="__main__":
    main() 