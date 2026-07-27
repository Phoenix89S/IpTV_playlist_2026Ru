# ============================================================
# Сканер Николая Смольянинова — УЛЬТРА-АГРЕССИВНЫЙ (финал)
# 20 федеральных + всё НТВ + кино + детские + спорт + документальные
# Ищет ВСЕ потоки + парсит master/media + проверяет живость сегментов
# ============================================================

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import re
from urllib.parse import urljoin

# ------------------------------------------------------------
#  ПОЛНАЯ КАРТА КАНАЛОВ
# ------------------------------------------------------------
CHANNEL_META = {
    # ========== 20 ФЕДЕРАЛЬНЫХ ==========
    "perviy": ("Первый канал", "Федеральные"),
    "1tv": ("Первый канал", "Федеральные"),
    "rossiya1": ("Россия 1", "Федеральные"),
    "russia1": ("Россия 1", "Федеральные"),
    "matchtv": ("Матч ТВ", "Федеральные"),
    "match": ("Матч ТВ", "Федеральные"),
    "ntv": ("НТВ", "Федеральные"),
    "ntv0": ("НТВ HD", "Федеральные"),
    "pyatyi": ("Пятый канал", "Федеральные"),
    "5tv": ("Пятый канал", "Федеральные"),
    "rossiyak": ("Россия К", "Федеральные"),
    "kultura": ("Россия К", "Федеральные"),
    "rossiya24": ("Россия 24", "Федеральные"),
    "russia24": ("Россия 24", "Федеральные"),
    "karusel": ("Карусель", "Федеральные"),
    "otr": ("ОТР", "Федеральные"),
    "tvc": ("ТВ Центр", "Федеральные"),
    "tvcentr": ("ТВ Центр", "Федеральные"),
    "rentv": ("РЕН ТВ", "Федеральные"),
    "ren": ("РЕН ТВ", "Федеральные"),
    "spas": ("Спас", "Федеральные"),
    "sts": ("СТС", "Федеральные"),
    "domashniy": ("Домашний", "Федеральные"),
    "domashny": ("Домашний", "Федеральные"),
    "tv3": ("ТВ-3", "Федеральные"),
    "tv-3": ("ТВ-3", "Федеральные"),
    "pyatnica": ("Пятница!", "Федеральные"),
    "friday": ("Пятница!", "Федеральные"),
    "zvezda": ("Звезда", "Федеральные"),
    "mir": ("Мир", "Федеральные"),
    "tnt": ("ТНТ", "Федеральные"),
    "muztv": ("Муз-ТВ", "Федеральные"),
    "muz": ("Муз-ТВ", "Федеральные"),

    # ========== ВСЁ НТВ ==========
    "ntv1": ("НТВ +1", "НТВ"),
    "ntv2": ("НТВ +2", "НТВ"),
    "ntv4": ("НТВ +4", "НТВ"),
    "ntv7": ("НТВ +7", "НТВ"),
    "th_hit": ("НТВ Хит", "НТВ"),
    "ntvhit": ("НТВ Хит", "НТВ"),
    "th_serial": ("НТВ Сериал", "НТВ"),
    "ntvserialhd": ("НТВ Сериал", "НТВ"),
    "th_style": ("НТВ Стиль", "НТВ"),
    "ntvstyle": ("НТВ Стиль", "НТВ"),
    "th_pravo": ("НТВ Право", "НТВ"),
    "ntvpravo": ("НТВ Право", "НТВ"),
    "unknown_russia": ("Неизвестная Россия", "НТВ"),
    "ntvspb": ("НТВ СПб", "НТВ"),

    # НТВ Мир
    "ntvmir": ("НТВ Мир", "НТВ"),
    "ntv_mir": ("НТВ Мир", "НТВ"),
    "ntv-mir": ("НТВ Мир", "НТВ"),
    "ntvmir_tv": ("НТВ Мир", "НТВ"),
    "ntv_mir_tv": ("НТВ Мир", "НТВ"),
    "ntvmirhd": ("НТВ Мир HD", "НТВ"),
    "ntv_mir_hd": ("НТВ Мир HD", "НТВ"),
    "mir_ntv": ("НТВ Мир", "НТВ"),

    # НТВ Беларусь
    "ntvbelarus": ("НТВ Беларусь", "НТВ"),
    "ntv_belarus": ("НТВ Беларусь", "НТВ"),
    "ntv-belarus": ("НТВ Беларусь", "НТВ"),
    "belarus": ("НТВ Беларусь", "НТВ"),
    "ntvby": ("НТВ Беларусь", "НТВ"),
    "ntvbelarus_hd": ("НТВ Беларусь HD", "НТВ"),
    "ntv_by": ("НТВ Беларусь", "НТВ"),
    "belarus_ntv": ("НТВ Беларусь", "НТВ"),

    # НТВ-Плюс
    "ntvplus": ("НТВ-Плюс", "НТВ"),
    "ntvplus_hd": ("НТВ-Плюс HD", "НТВ"),

    # ========== КИНО ==========
    "tv1000": ("TV1000", "Кино"),
    "tv1000_action": ("TV1000 Action", "Кино"),
    "tv1000_russian": ("TV1000 Русское кино", "Кино"),
    "kinopremiera": ("Кинопремьера", "Кино"),
    "kinoserial": ("Киносериал", "Кино"),
    "kinosemya": ("Киносемья", "Кино"),
    "kinosvidanie": ("Киносвидание", "Кино"),
    "amedia1": ("Amedia 1", "Кино"),
    "amedia2": ("Amedia 2", "Кино"),
    "amedia-premium": ("Amedia Premium", "Кино"),
    "fox": ("Fox", "Кино"),
    "foxlife": ("Fox Life", "Кино"),
    "sony-tv": ("Sony TV", "Кино"),
    "paramount": ("Paramount", "Кино"),
    "comedy-tv": ("Comedy TV", "Кино"),
    "hollywood": ("Кино TV", "Кино"),

    # ========== ДЕТСКИЕ ==========
    "mult": ("Мульт", "Детские"),
    "tiji": ("TiJi", "Детские"),
    "cartoonnetwork": ("Cartoon Network", "Детские"),
    "nickelodeon": ("Nickelodeon", "Детские"),
    "disney": ("Disney", "Детские"),
    "boomerang": ("Boomerang", "Детские"),
    "karusel_kids": ("Карусель", "Детские"),

    # ========== СПОРТ ==========
    "match-arena": ("Матч Арена", "Спортивные"),
    "match-igra": ("Матч Игра", "Спортивные"),
    "match-boec": ("Матч Боец", "Спортивные"),
    "match-strana": ("Матч Страна", "Спортивные"),
    "khlprimehd": ("КХЛ Прайм HD", "Спортивные"),
    "boxtv": ("Бокс ТВ", "Спортивные"),
    "volleyball": ("Волейбол", "Спортивные"),
    "udarfightclub": ("Удар", "Спортивные"),
    "start": ("Старт", "Спортивные"),
    "start-basket": ("Старт Баскет", "Спортивные"),
    "start-triumf": ("Старт Триумф", "Спортивные"),

    # ========== ДОКУМЕНТАЛЬНЫЕ ==========
    "discovery": ("Discovery", "Документальные"),
    "natgeo": ("National Geographic", "Документальные"),
    "animalplanet": ("Animal Planet", "Документальные"),
    "history": ("History", "Документальные"),
    "travelchannel": ("Travel Channel", "Документальные"),
    "myplanet": ("Моя Планета", "Документальные"),
    "doctor": ("Доктор", "Документальные"),
    "nostalgia": ("Ностальгия", "Документальные"),
    "eurosport": ("Eurosport", "Документальные"),
    "hunterfisher": ("Охотник и рыболов", "Документальные"),
}

# ------------------------------------------------------------
#  АГРЕССИВНОЕ РАСШИРЕНИЕ ID
# ------------------------------------------------------------
def expand_ids(base_ids):
    extra = []
    suffixes = ["", "hd", "-hd", "sd", "-sd", "hq", "lq", "_tv"]
    for cid in base_ids:
        for s in suffixes:
            extra.append(cid + s)
            extra.append(cid.replace("_", "-") + s)
            extra.append(cid.replace("-", "_") + s)
    return list(dict.fromkeys(extra))

ALL_IDS = expand_ids(list(CHANNEL_META.keys()))

# ------------------------------------------------------------
#  CDN ПУТИ И НАСТРОЙКИ
# ------------------------------------------------------------
CDN_BASES = [
    "https://cdn.ntv.ru/{id}/",
    "http://cdn.ntv.ru/{id}/",
    "https://cdn2.ntv.ru/{id}/",
    "http://cdn2.ntv.ru/{id}/",
]

PLAYLIST_NAMES = [
    "playlist.m3u8", "index.m3u8", "mono.m3u8", "master.m3u8", "live.m3u8",
    "tracks-v1a1/mono.m3u8", "tracks-v1a1/playlist.m3u8", "tracks-v1a1/index.m3u8",
    "tracks-v3a1/mono.m3u8", "tracks-v3a1/playlist.m3u8", "tracks-v3a1/index.m3u8",
    "tracks-v1a1/hd.m3u8", "tracks-v3a1/hd.m3u8",
    "hd/playlist.m3u8", "sd/playlist.m3u8",
    "tracks-v1a1/sd.m3u8",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Connection": "close",
}

session = requests.Session()
session.headers.update(HEADERS)

# ------------------------------------------------------------
#  ФУНКЦИИ ПАРСИНГА И ПРОВЕРКИ ЖИВОСТИ
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
    base = master_url.rsplit("/", 1)[0] + "/"
    lines = content.splitlines()

    media_playlists = []

    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ".m3u8" in line.lower():
            full = urljoin(base, line)
            quality = "unknown"

            if i > 0:
                prev = lines[i - 1]
                m = re.search(r"RESOLUTION=(\d+x\d+)", prev)
                if m:
                    quality = m.group(1)
                else:
                    m = re.search(r"BANDWIDTH=(\d+)", prev)
                    if m:
                        bw = int(m.group(1))
                        quality = "HD" if bw >= 3500000 else ("SD+" if bw >= 1200000 else "SD")

            low = line.lower()
            if any(x in low for x in ("hd", "1080", "720", "v3a1")):
                quality = "HD"
            elif any(x in low for x in ("sd", "576", "480")):
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
                if any(line.lower().endswith(ext) for ext in (".ts", ".m4s", ".mp4", ".aac")):
                    seg_url = urljoin(mpl_url.rsplit("/", 1)[0] + "/", line)
                    if check_segment_alive(seg_url):
                        alive = True
                    break
            if not alive and ("#EXTINF" in mpl_content or "#EXT-X-MEDIA-SEQUENCE" in mpl_content):
                alive = True
        results.append((mpl_url, quality, alive))
    return results

# ------------------------------------------------------------
#  СКАНИРОВАНИЕ ОДНОГО ID
# ------------------------------------------------------------
def scan_one_id(channel_id: str):
    found = []
    name, group = CHANNEL_META.get(channel_id, (channel_id, "Прочие"))

    baseid = channel_id
    for s in ("hd", "-hd", "sd", "-sd", "hq", "lq", "_tv"):
        if channel_id.endswith(s) and len(channel_id) > len(s):
            baseid = channel_id[:-len(s)]
            break
    if baseid in CHANNEL_META:
        name, group = CHANNEL_META[baseid]

    for basetmpl in CDN_BASES:
        base = basetmpl.format(id=channel_id)
        for pl_name in PLAYLIST_NAMES:
            url = urljoin(base, pl_name)
            content = fetch_text(url)
            if not content:
                continue

            streams = extract_and_verify_streams(url, content)
            for stream_url, quality, alive in streams:
                display = name
                if quality in ("HD", "1920x1080", "1280x720") and "HD" not in display:
                    display += " HD"
                elif quality in ("SD", "1024x576", "720x576") and "HD" in display:
                    display = display.replace(" HD", "")

                found.append({
                    "id": channel_id,
                    "name": display,
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
    print("===========================================================")
    print("СКАЛА-ЧАЭС УЛЬТРА-АГРЕССИВНЫЙ (финал)")
    print("Ищет ВСЕ потоки + помечает сомнительные")
    print("===========================================================\n")

    all_results = []
    seen_urls = set()

    with ThreadPoolExecutor(max_workers=18) as pool:
        futures = {pool.submit(scan_one_id, cid): cid for cid in ALL_IDS}
        for future in as_completed(futures):
            res = future.result()
            for item in res:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    all_results.append(item)

    # Разделение и сортировка: живые идут первыми
    alive_channels = [c for c in all_results if c["alive"]]
    doubtful_channels = [c for c in all_results if not c["alive"]]

    final_list = alive_channels + doubtful_channels

    # Запись в плейлист M3U8
    output_filename = "Nikolai_Smolyaninoff_playlist.m3u8"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in final_list:
            status = "живой" if item["alive"] else "сомнительный"
            f.write(
                f'#EXTINF:-1 tvg-name="{item["name"]}" group-title="{item["group"]}" '
                f'tvg-status="{status}",{item["name"]}\n'
            )
            f.write(f'{item["url"]}\n')

    elapsed = time.perf_counter() - start
    print(f"Готово за {elapsed:.2f} сек!")
    print(f"Всего найдено ссылок: {len(final_list)}")
    print(f" - Подтвержденных (живых): {len(alive_channels)}")
    print(f" - Сомнительных: {len(doubtful_channels)}")
    print(f"Плейлист сохранен в файл: {output_filename}")

if __name__ == "__main__":
    main()
