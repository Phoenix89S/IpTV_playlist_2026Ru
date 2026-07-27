# ============================================================
#  Сканер Николая Смольянинова — УЛЬТРА-ВАРИАНТ
#  Полный перебор + парсинг всех потоков на CDN НТВ
#  Особый приоритет: НТВ Мир + НТВ Беларусь
#  Лог: СКАЛА ЧАЭС — телетайп
# ============================================================

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import re
from urllib.parse import urljoin

# ------------------------------------------------------------
#  Метаданные каналов (название + группа)
# ------------------------------------------------------------
CHANNEL_META = {
    # === НТВ ===
    "ntv":            ("НТВ", "НТВ"),
    "ntv0":           ("НТВ HD", "НТВ"),
    "ntv1":           ("НТВ +1", "НТВ"),
    "ntv2":           ("НТВ +2", "НТВ"),
    "ntv4":           ("НТВ +4", "НТВ"),
    "ntv7":           ("НТВ +7", "НТВ"),
    "th_hit":         ("НТВ Хит", "НТВ"),
    "ntvhit":         ("НТВ Хит", "НТВ"),
    "th_serial":      ("НТВ Сериал", "НТВ"),
    "ntvserialhd":    ("НТВ Сериал", "НТВ"),
    "th_style":       ("НТВ Стиль", "НТВ"),
    "ntvstyle":       ("НТВ Стиль", "НТВ"),
    "th_pravo":       ("НТВ Право", "НТВ"),
    "ntvpravo":       ("НТВ Право", "НТВ"),
    "unknown_russia": ("Неизвестная Россия", "НТВ"),
    "ntvspb":         ("НТВ СПб", "НТВ"),

    # === НТВ Мир (международная) ===
    "ntvmir":         ("НТВ Мир", "НТВ"),
    "ntv_mir":        ("НТВ Мир", "НТВ"),
    "ntv-mir":        ("НТВ Мир", "НТВ"),
    "ntvmir_tv":      ("НТВ Мир", "НТВ"),
    "ntv_mir_tv":     ("НТВ Мир", "НТВ"),
    "ntvmirhd":       ("НТВ Мир HD", "НТВ"),

    # === НТВ Беларусь ===
    "ntvbelarus":     ("НТВ Беларусь", "НТВ"),
    "ntv_belarus":    ("НТВ Беларусь", "НТВ"),
    "ntv-belarus":    ("НТВ Беларусь", "НТВ"),
    "belarus":        ("НТВ Беларусь", "НТВ"),
    "ntvby":          ("НТВ Беларусь", "НТВ"),
    "ntvbelarus_hd":  ("НТВ Беларусь HD", "НТВ"),

    # === Матч ===
    "matchtv":        ("Матч ТВ", "Матч"),
    "match-arena":    ("Матч Арена", "Матч"),
    "match-igra":     ("Матч Игра", "Матч"),
    "match-boec":     ("Матч Боец", "Матч"),
    "match-strana":   ("Матч Страна", "Матч"),

    # === Федеральные ===
    "perviy":         ("Первый канал", "Федеральные"),
    "rossiya1":       ("Россия 1", "Федеральные"),
    "pyatyi":         ("Пятый канал", "Федеральные"),
    "rossiyak":       ("Россия К", "Федеральные"),
    "rossiya24":      ("Россия 24", "Федеральные"),
    "karusel":        ("Карусель", "Федеральные"),
    "otr":            ("ОТР", "Федеральные"),
    "tvc":            ("ТВ Центр", "Федеральные"),
    "rentv":          ("РЕН ТВ", "Федеральные"),
    "spas":           ("Спас", "Федеральные"),
    "sts":            ("СТС", "Федеральные"),
    "domashniy":      ("Домашний", "Федеральные"),
    "tv3":            ("ТВ-3", "Федеральные"),
    "pyatnica":       ("Пятница!", "Федеральные"),
    "zvezda":         ("Звезда", "Федеральные"),
    "mir":            ("Мир", "Федеральные"),
    "tnt":            ("ТНТ", "Федеральные"),
    "muztv":          ("Муз-ТВ", "Федеральные"),

    # === Спортивные ===
    "khlprimehd":     ("КХЛ Прайм HD", "Спортивные"),
    "boxtv":          ("Бокс ТВ", "Спортивные"),
    "volleyball":     ("Волейбол", "Спортивные"),
    "udarfightclub":  ("Удар", "Спортивные"),
    "start":          ("Старт", "Спортивные"),
    "start-basket":   ("Старт Баскет", "Спортивные"),
    "start-triumf":   ("Старт Триумф", "Спортивные"),

    # === Кино ===
    "tv1000":         ("TV1000", "Кино"),
    "tv1000_action":  ("TV1000 Action", "Кино"),
    "tv1000_russian": ("TV1000 Русское кино", "Кино"),
    "kinopremiera":   ("Кинопремьера", "Кино"),
    "kinoserial":     ("Киносериал", "Кино"),
    "kinosemya":      ("Киносемья", "Кино"),
    "kinosvidanie":   ("Киносвидание", "Кино"),
    "amedia1":        ("Amedia 1", "Кино"),
    "amedia2":        ("Amedia 2", "Кино"),
    "amedia-premium": ("Amedia Premium", "Кино"),
    "fox":            ("Fox", "Кино"),
    "foxlife":        ("Fox Life", "Кино"),
    "sony-tv":        ("Sony TV", "Кино"),
    "paramount":      ("Paramount", "Кино"),
    "comedy-tv":      ("Comedy TV", "Кино"),

    # === Детские ===
    "mult":           ("Мульт", "Детские"),
    "tiji":           ("TiJi", "Детские"),
    "cartoonnetwork": ("Cartoon Network", "Детские"),
    "nickelodeon":    ("Nickelodeon", "Детские"),
    "disney":         ("Disney", "Детские"),
    "boomerang":      ("Boomerang", "Детские"),

    # === Документальные ===
    "discovery":      ("Discovery", "Документальные"),
    "natgeo":         ("National Geographic", "Документальные"),
    "animalplanet":   ("Animal Planet", "Документальные"),
    "history":        ("History", "Документальные"),
    "travelchannel":  ("Travel Channel", "Документальные"),
    "myplanet":       ("Моя Планета", "Документальные"),
    "doctor":         ("Доктор", "Документальные"),
    "nostalgia":      ("Ностальгия", "Документальные"),
    "eurosport":      ("Eurosport", "Документальные"),
    "hunterfisher":   ("Охотник и рыболов", "Документальные"),

    # === Прочие ===
    "ntvplus":        ("НТВ-Плюс", "Прочие"),
    "ntvplus_hd":     ("НТВ-Плюс HD", "Прочие"),
}

# Все ID для перебора
ALL_IDS = list(CHANNEL_META.keys())

# Базовые адреса CDN
CDN_BASES = [
    "https://cdn.ntv.ru/{id}/",
    "http://cdn.ntv.ru/{id}/",
    "https://cdn2.ntv.ru/{id}/",
    "http://cdn2.ntv.ru/{id}/",
]

# Возможные имена плейлистов
PLAYLIST_NAMES = [
    "playlist.m3u8",
    "index.m3u8",
    "mono.m3u8",
    "tracks-v1a1/mono.m3u8",
    "tracks-v1a1/playlist.m3u8",
    "tracks-v3a1/mono.m3u8",
    "tracks-v3a1/playlist.m3u8",
    "tracks-v1a1/index.m3u8",
    "hd/playlist.m3u8",
    "sd/playlist.m3u8",
    "tracks-v1a1/hd.m3u8",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "close",
}

session = requests.Session()
session.headers.update(HEADERS)


def is_m3u8(text: str) -> bool:
    return "#EXTM3U" in text or "#EXT-X-" in text


def fetch_text(url: str, timeout: int = 7) -> str | None:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and is_m3u8(r.text):
            return r.text
    except Exception:
        pass
    return None


def extract_streams(master_url: str, content: str) -> list[tuple[str, str]]:
    """Парсит master-плейлист и возвращает [(url, quality), ...]"""
    streams = []
    base = master_url.rsplit("/", 1)[0] + "/"
    lines = content.splitlines()

    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ".m3u8" in line.lower():
            full = urljoin(base, line)
            quality = "unknown"

            # Смотрим предыдущую строку на RESOLUTION / BANDWIDTH
            if i > 0:
                prev = lines[i - 1]
                m_res = re.search(r"RESOLUTION=(\d+x\d+)", prev)
                if m_res:
                    quality = m_res.group(1)
                else:
                    m_bw = re.search(r"BANDWIDTH=(\d+)", prev)
                    if m_bw:
                        bw = int(m_bw.group(1))
                        if bw >= 4000000:
                            quality = "HD"
                        elif bw >= 1500000:
                            quality = "SD+"
                        else:
                            quality = "SD"

            # Дополнительно из имени файла
            low = line.lower()
            if any(x in low for x in ("hd", "1080", "720", "v3a1")):
                quality = "HD" if quality == "unknown" else quality
            elif any(x in low for x in ("sd", "576", "480")):
                quality = "SD" if quality == "unknown" else quality
            elif "mono" in low:
                quality = "mono" if quality == "unknown" else quality

            streams.append((full, quality))

    # Если это уже media-плейлист
    if not streams and is_m3u8(content):
        streams.append((master_url, "direct"))

    return streams


def scan_one_id(channel_id: str) -> list[dict]:
    """Ищет все рабочие потоки для одного ID"""
    found = []
    name, group = CHANNEL_META.get(channel_id, (channel_id, "Прочие"))

    for base_tmpl in CDN_BASES:
        base = base_tmpl.format(id=channel_id)

        for pl_name in PLAYLIST_NAMES:
            url = urljoin(base, pl_name)
            content = fetch_text(url)
            if not content:
                continue

            streams = extract_streams(url, content)

            for stream_url, quality in streams:
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
                    "source": url,
                })

    return found


def main():
    start = time.perf_counter()
    print("===========================================================")
    print("СКАЛА-ЧАЭС УЛЬТРА: Сканер Николая Смольянинова")
    print("Полный перебор + парсинг всех tracks на CDN НТВ")
    print("Особый приоритет: НТВ Мир + НТВ Беларусь")
    print("===========================================================\n")

    all_results = []
    log_lines = []

    with ThreadPoolExecutor(max_workers=22) as pool:
        futures = {pool.submit(scan_one_id, cid): cid for cid in ALL_IDS}

        for future in as_completed(futures):
            cid = futures[future]
            try:
                results = future.result()
            except Exception as e:
                results = []
                print(f"Ошибка на {cid}: {e}")

            elapsed = time.perf_counter() - start

            if results:
                for r in results:
                    msg = (f"{elapsed:7.2f} — СКАЛА: {r['name']:22} "
                           f"[{r['quality']:8}] → {r['url']}")
                    print(msg)
                    log_lines.append(msg)
                all_results.extend(results)
            else:
                msg = f"{elapsed:7.2f} — СКАЛА: {cid:22} отброшен"
                print(msg)
                log_lines.append(msg)

    # Убираем дубликаты по URL
    unique = {r["url"]: r for r in all_results}
    all_results = list(unique.values())

    # Сортировка: сначала НТВ, потом по имени
    all_results.sort(key=lambda x: (
        0 if x["group"] == "НТВ" else 1,
        x["name"],
        x["quality"]
    ))

    # ---------- Плейлист ----------
    playlist_name = "smolnp.m3u"
    with open(playlist_name, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for r in all_results:
            f.write(
                f'#EXTINF:-1 tvg-name="{r["name"]}" '
                f'group-title="{r["group"]}",{r["name"]}\n'
                f'{r["url"]}\n'
            )

    # ---------- Отчёт ----------
    report_name = "smolnp.txt"
    with open(report_name, "w", encoding="utf-8") as rep:
        rep.write("СКАЛА-ЧАЭС УЛЬТРА — Отчёт сканирования\n")
        rep.write("=" * 70 + "\n\n")
        for line in log_lines:
            rep.write(line + "\n")

        rep.write("\n\n=== Найденные рабочие потоки ===\n")
        for r in all_results:
            rep.write(f"\n{r['name']} [{r['quality']}]\n")
            rep.write(f"  ID      : {r['id']}\n")
            rep.write(f"  Группа  : {r['group']}\n")
            rep.write(f"  URL     : {r['url']}\n")
            rep.write(f"  Источник: {r['source']}\n")

    print("\n===========================================================")
    print(f"Плейлист сохранён      : {playlist_name}")
    print(f"Отчёт сохранён         : {report_name}")
    print(f"Всего уникальных потоков: {len(all_results)}")
    print("===========================================================\n")


if __name__ == "__main__":
    main()