# ============================================================
#  Сканер Николая Смольянинова — УЛЬТРА-АГРЕССИВНЫЙ (НТВ Мир)
#  Проверяет все алиасы НТВ Мир + Беларусь
#  Лог: СКАЛА ЧАЭС — телетайп
# ============================================================

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from urllib.parse import urljoin

# ------------------------------------------------------------
#  КАРТА КАНАЛОВ (только НТВ Мир + Беларусь)
# ------------------------------------------------------------
CHANNEL_META = {
    "ntvmir":        ("НТВ Мир", "НТВ"),
    "ntv_mir":       ("НТВ Мир", "НТВ"),
    "ntv-mir":       ("НТВ Мир", "НТВ"),
    "ntvmir_tv":     ("НТВ Мир", "НТВ"),
    "ntv_mir_tv":    ("НТВ Мир", "НТВ"),
    "ntvmirhd":      ("НТВ Мир HD", "НТВ"),
    "ntv_mir_hd":    ("НТВ Мир HD", "НТВ"),
    "mir_ntv":       ("НТВ Мир", "НТВ"),
    "th_mir":        ("НТВ Мир", "НТВ"),

    # Беларусь
    "ntvbelarus":    ("НТВ Беларусь", "НТВ"),
    "ntv_belarus":   ("НТВ Беларусь", "НТВ"),
    "ntv-belarus":   ("НТВ Беларусь", "НТВ"),
    "belarus":       ("НТВ Беларусь", "НТВ"),
    "ntvby":         ("НТВ Беларусь", "НТВ"),
    "ntvbelarus_hd": ("НТВ Беларусь HD", "НТВ"),
    "ntv_by":        ("НТВ Беларусь", "НТВ"),
    "belarus_ntv":   ("НТВ Беларусь", "НТВ"),
}

# ------------------------------------------------------------
#  АГРЕССИВНОЕ РАСШИРЕНИЕ ID
# ------------------------------------------------------------
def expand_ids(base_ids):
    extra = []
    suffixes = ["", "_hd", "-hd", "_sd", "-sd", "_hq", "_lq", "hd", "sd", "_tv"]
    for cid in base_ids:
        for s in suffixes:
            extra.append(cid + s)
            extra.append(cid.replace("_", "-") + s)
            extra.append(cid.replace("-", "_") + s)
    return list(dict.fromkeys(extra))

ALL_IDS = expand_ids(list(CHANNEL_META.keys()))

# ------------------------------------------------------------
#  CDN ПУТИ
# ------------------------------------------------------------
CDN_BASES = [
    "https://cdn.ntv.ru/{id}/",
    "http://cdn.ntv.ru/{id}/",
    "https://cdn2.ntv.ru/{id}/",
    "http://cdn2.ntv.ru/{id}/",
    "http://fs.uplink.kz/{id}/",
]

# ------------------------------------------------------------
#  ПЛЕЙЛИСТЫ
# ------------------------------------------------------------
PLAYLIST_NAMES = [
    "playlist.m3u8", "index.m3u8", "mono.m3u8", "master.m3u8", "live.m3u8",
    "tracks-v1a1/mono.m3u8", "tracks-v1a1/playlist.m3u8", "tracks-v1a1/index.m3u8",
    "tracks-v3a1/mono.m3u8", "tracks-v3a1/playlist.m3u8", "tracks-v3a1/index.m3u8",
    "tracks-v1a1/hd.m3u8", "tracks-v3a1/hd.m3u8",
    "hd/playlist.m3u8", "sd/playlist.m3u8",
    "tracks-v1a1/sd.m3u8",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Connection": "close",
}

session = requests.Session()
session.headers.update(HEADERS)

# ------------------------------------------------------------
#  ФУНКЦИИ ПРОВЕРКИ
# ------------------------------------------------------------
def is_m3u8(text: str) -> bool:
    return bool(text) and ("#EXTM3U" in text or "#EXT-X-" in text)

def fetch_text(url: str, timeout: float = 6.0) -> str | None:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and is_m3u8(r.text):
            return r.text
    except Exception:
        pass
    return None

def check_segment_alive(segment_url: str, timeout: float = 4.0) -> bool:
    try:
        r = session.head(segment_url, timeout=timeout, allow_redirects=True)
        if r.status_code in (200, 206):
            return True
        r = session.get(segment_url, timeout=timeout, stream=True, allow_redirects=True)
        if r.status_code in (200, 206):
            next(r.iter_content(chunk_size=64), None)
            return True
    except Exception:
        pass
    return False

def extract_and_verify_streams(master_url: str, content: str):
    results = []
    lines = content.splitlines()
    media_playlists = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ".m3u8" in line.lower():
            full = urljoin(master_url, line)
            quality = "unknown"
            low = line.lower()
            if "hd" in low or "1080" in low or "720" in low:
                quality = "HD"
            elif "sd" in low or "576" in low or "480" in low:
                quality = "SD"
            elif "mono" in low:
                quality = "mono"
            media_playlists.append((full, quality))

    if not media_playlists and is_m3u8(content):
        media_playlists.append((master_url, "direct"))

    for mpl_url, quality in media_playlists:
        mpl_content = fetch_text(mpl_url, timeout=5)
        alive = False
        if mpl_content:
            for line in mpl_content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Проверяем сегменты (любые урлы после директив)
                seg_url = urljoin(mpl_url, line)
                if check_segment_alive(seg_url):
                    alive = True
                    break
            if not alive and ("#EXTINF" in mpl_content or "#EXT-X-MEDIA-SEQUENCE" in mpl_content):
                alive = True
        results.append((mpl_url, quality, alive))
    return results

# ------------------------------------------------------------
#  СКАНИРОВАНИЕ
# ------------------------------------------------------------
def scan_one_id(channel_id: str):
    found = []
    name, group = CHANNEL_META.get(channel_id, (channel_id, "Прочие"))

    for base_tmpl in CDN_BASES:
        base = base_tmpl.format(id=channel_id)
        for pl_name in PLAYLIST_NAMES:
            url = urljoin(base, pl_name)
            content = fetch_text(url)
            if not content:
                continue

            streams = extract_and_verify_streams(url, content)
            for stream_url, quality, alive in streams:
                found.append({
                    "id": channel_id,
                    "name": name,
                    "group": group,
                    "url": stream_url,
                    "quality": quality,
                    "alive": alive,
                    "source": url,
                })
    return found

# ------------------------------------------------------------
#  ОСНОВНОЙ ЗАПУСК
# ------------------------------------------------------------
def main():
    start = time.perf_counter()
    print("=== СКАЛА-ЧАЭС: НТВ МИР ===")

    all_results = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(scan_one_id, cid): cid for cid in ALL_IDS}
        for future in as_completed(futures):
            cid = futures[future]
            try:
                results = future.result()
            except Exception as e:
                results = []
                print(f"Ошибка {cid}: {e}")

            elapsed = time.perf_counter() - start
            if results:
                for r in results:
                    status = "ЖИВОЙ" if r["alive"] else "СОМНИТЕЛЬНЫЙ"
                    print(f"[{elapsed:6.2f}s] [{status}] {r['name']} ({r['quality']}) -> {r['url']}")
                    all_results.append(r)

    print(f"\n Сканирование завершено за {time.perf_counter() - start:.2f} сек.")
    print(f" Найдено уникальных рабочих потоков: {len([x for x in all_results if x['alive']])}")

if __name__ == "__main__":
    main()
