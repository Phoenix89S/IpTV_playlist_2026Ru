# ============================================================
#  Сканер Николая Смольянинова — УЛЬТРА-АГРЕССИВНЫЙ (финал)
#  20 федеральных + всё НТВ + кино + детские + спорт + документальные
#  Ищет ВСЕ потоки + парсит master/media + проверяет живость сегментов
#  Лог: СКАЛА ЧАЭС — телетайп
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
]

# ------------------------------------------------------------
#  ВСЕ ВОЗМОЖНЫЕ ПЛЕЙЛИСТЫ
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
        if not mpl_content:
            results.append((mpl_url, quality, False))
            continue

        alive = False
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

    base_id = channel_id
    for s in ("_hd", "-hd", "_sd", "-sd", "_hq", "_lq", "hd", "sd"):
        if channel_id.endswith(s) and len(channel_id) > len(s):
            base_id = channel_id[:-len(s)]
            break
    if base_id in CHANNEL_META:
        name, group = CHANNEL_META[base_id]

    for base_tmpl in CDN_BASES:
        base = base_tmpl.format(id=channel_id)
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
    print("СКАЛА-ЧАЭС УЛЬТРА-АГРЕССИВНЫЙ")
    print("Ищет ВСЕ потоки + проверяет живость каждого сегмента")
    print("Особый приоритет: НТВ Мир + НТВ Беларусь")
    print("===========================================================\n")

    all_results = []
    log_lines = []

    with ThreadPoolExecutor(max_workers=18) as pool:
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
                    status = "ЖИВОЙ" if r["alive"] else "сомнительный"
                    msg = (f"{elapsed:7.2f} — СКАЛА: {r['name']:22} "
                           f
msg = (f"{elapsed:7.2f} — СКАЛА: {r['name']:22} "
                           f"[{r['quality']:8}] [{status:12}] → {r['url']}")
                    print(msg)
                    log_lines.append(msg)
                all_results.extend(results)
            else:
                msg = f"{elapsed:7.2f} — СКАЛА: {cid:22} отброшен"
                print(msg)
                log_lines.append(msg)

    # ------------------------------------------------------------
    #  ДЕДУПЛИКАЦИЯ ПО URL (живые имеют приоритет)
    # ------------------------------------------------------------
    unique = {}
    for r in all_results:
        url = r["url"]
        if url not in unique or (r["alive"] and not unique[url]["alive"]):
            unique[url] = r
    all_results = list(unique.values())

    # ------------------------------------------------------------
    #  СОРТИРОВКА: живые → НТВ → имя → качество
    # ------------------------------------------------------------
    all_results.sort(key=lambda x: (
        0 if x["alive"] else 1,
        0 if x["group"] == "НТВ" else 1,
        x["name"],
        x["quality"]
    ))

    # ------------------------------------------------------------
    #  ГЕНЕРАЦИЯ ПЛЕЙЛИСТА
    # ------------------------------------------------------------
    playlist_name = "smolnp.m3u"
    with open(playlist_name, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for r in all_results:
            status = "ЖИВОЙ" if r["alive"] else "сомнительный"
            f.write(
                f'#EXTINF:-1 tvg-name="{r["name"]}" '
                f'group-title="{r["group"]}" '
                f'tvg-id="{r["id"]}" '
                f'tvg-status="{status}",{r["name"]}\n'
                f'{r["url"]}\n'
            )

    # ------------------------------------------------------------
    #  ГЕНЕРАЦИЯ ОТЧЁТА
    # ------------------------------------------------------------
    report_name = "smolnp.txt"
    with open(report_name, "w", encoding="utf-8") as rep:
        rep.write("СКАЛА-ЧАЭС УЛЬТРА-АГРЕССИВНЫЙ — ОТЧЁТ СКАНИРОВАНИЯ\n")
        rep.write("=" * 80 + "\n\n")

        rep.write("=== ТЕЛЕТАЙП ===\n")
        for line in log_lines:
            rep.write(line + "\n")

        rep.write("\n\n=== НАЙДЕННЫЕ ПОТОКИ ===\n")
        for r in all_results:
            status = "ЖИВОЙ" if r["alive"] else "сомнительный"
            rep.write(f"\n{r['name']} [{r['quality']}] [{status}]\n")
            rep.write(f"  ID      : {r['id']}\n")
            rep.write(f"  Группа  : {r['group']}\n")
            rep.write(f"  URL     : {r['url']}\n")
            rep.write(f"  Источник: {r['source']}\n")

        rep.write("\n\n=== ИТОГИ ===\n")
        alive_count = sum(1 for r in all_results if r["alive"])
        dead_count = len(all_results) - alive_count
        rep.write(f"Живых потоков: {alive_count}\n")
        rep.write(f"Сомнительных: {dead_count}\n")
        rep.write(f"Всего уникальных потоков: {len(all_results)}\n")

    # ------------------------------------------------------------
    #  ФИНАЛЬНЫЙ ВЫВОД
    # ------------------------------------------------------------
    print("\n===========================================================")
    print(f"Плейлист сохранён      : {playlist_name}")
    print(f"Отчёт сохранён         : {report_name}")
    print(f"Живых потоков          : {alive_count}")
    print(f"Сомнительных потоков   : {dead_count}")
    print(f"Всего уникальных потоков: {len(all_results)}")
    print("===========================================================\n")


if __name__ == "__main__":
    main()