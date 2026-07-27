# ============================================================
#  Сканер Phoenix 89S в честь Николая Смольянинова(smolnp)
#  Полный перебор русских каналов на CDN НТВ
#  Лог: СКАЛА ЧАЭС — телетайп
# ============================================================

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Каналы по семействам
CHANNEL_GROUPS = {
    "НТВ": [
        "ntv", "ntvmir", "ntvbelarus", "ntvhit", "ntvserialhd", "ntvstyle", "ntvpravo", "ntvspb", "ntv2"
    ],
    "Матч": [
        "matchtv", "match-arena", "match-igra", "match-boec", "match-strana"
    ],
    "Федеральные": [
        "perviy", "rossiya1", "pyatyi", "rossiyak", "rossiya24", "karusel", "otr", "tvc", "rentv",
        "spas", "sts", "domashniy", "tv3", "pyatnica", "zvezda", "mir", "tnt", "muztv"
    ],
    "Спортивные": [
        "khlprimehd", "boxtv", "volleyball", "udarfightclub", "start", "start-basket", "start-triumf"
    ],
    "Кино": [
        "tv1000", "tv1000_action", "tv1000_russian", "kinopremiera", "kinoserial",
        "kinosemya", "kinosvidanie", "amedia1", "amedia2", "amedia-premium",
        "fox", "foxlife", "sony-tv", "paramount", "comedy-tv"
    ],
    "Детские": [
        "mult", "tiji", "cartoonnetwork", "nickelodeon", "disney", "boomerang"
    ],
    "Документальные": [
        "discovery", "natgeo", "animalplanet", "history", "travelchannel",
        "myplanet", "doctor", "nostalgia", "eurosport", "hunterfisher"
    ],
    "Прочие": [
        "ntvplus", "ntvplus_hd"
    ]
}

CDN_PATTERNS = [
    "http://cdn.ntv.ru/{id}/index.m3u8",
    "http://cdn.ntv.ru/{id}/tracks-v1a1/mono.m3u8",
    "http://cdn2.ntv.ru/{id}/tracks-v1a1/mono.m3u8",
    "https://cdn.ntv.ru/{id}/playlist.m3u8"
]

def check_url(url: str) -> bool:
    try:
        r = requests.head(url, timeout=5)
        return r.status_code == 200
    except Exception:
        return False

def scan_channel(channel: str):
    results = []
    for pattern in CDN_PATTERNS:
        url = pattern.format(id=channel)
        if check_url(url):
            results.append(url)
    return channel, results

def main():
    start_time = time.perf_counter()
    print("===========================================================")
    print("СКАЛА-ЧАЭС: Инициализация сканера Николая Смольянинова")
    print("===========================================================\n")

    working_links = {}
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = {executor.submit(scan_channel, ch): ch for group in CHANNEL_GROUPS.values() for ch in group}
        for future in as_completed(futures):
            channel, links = future.result()
            if links:
                working_links[channel] = links
                elapsed = time.perf_counter() - start_time
                for u in links:
                    print(f"{elapsed:0.2f} — СКАЛА: Канал {channel} принят → {u}")
            else:
                elapsed = time.perf_counter() - start_time
                print(f"{elapsed:0.2f} — СКАЛА: Канал {channel} отброшен (нет рабочих ссылок)")

    # Генерация плейлиста с группами
    playlist_name = "Nikolai_Smolyaninoff.m3u"
    with open(playlist_name, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for group_name, channels in CHANNEL_GROUPS.items():
            for ch in channels:
                if ch in working_links:
                    for u in working_links[ch]:
                        f.write(f'#EXTINF:-1 group-title="{group_name}",{ch}\n{u}\n')

    print("\n===========================================================")
    print(f"СКАЛА-ЧАЭС: Финальная сборка завершена → {playlist_name}")
    print("===========================================================\n")

if __name__ == "__main__":
    main()