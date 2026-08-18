import requests
from concurrent.futures import ThreadPoolExecutor

# ============================================================
# 1. Универсальная нормализация домена и пути
# ============================================================

def normalize_ngenix(url):
    url = url.replace("https://", "").replace("http://", "")
    url = url.replace("..", "")

    while "//" in url:
        url = url.replace("//", "/")

    if url.startswith("s") and ".cdn.ngenix.net" in url:
        parts = url.split("/", 1)
        node = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        return f"https://{node}.cdn.ngenix.net/{path}"

    if ".cdn.ngenix.net" in url:
        parts = url.split("/", 1)
        node = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        return f"https://{node}/{path}"

    return "INVALID:" + url


# ============================================================
# 2. Проверка конкретного потока
# ============================================================

def check_stream(base):
    tests = [
        f"{base}/index.m3u8",
        f"{base}/playlist.m3u8",
        f"{base}/master.m3u8",
        f"{base}/segment0.ts",
        f"{base}/1/index.m3u8",
        f"{base}/2/index.m3u8",
    ]

    for url in tests:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return url
        except:
            pass
    return None


# ============================================================
# 3. Сканирование всех узлов NGENIX
# ============================================================

def scan_all_nodes(channel):
    nodes = [f"s{x}" for x in range(10000, 80000)]
    results = []

    def worker(node):
        base = f"https://{node}.cdn.ngenix.net/{channel}"
        found = check_stream(base)
        if found:
            return node, found
        return None

    with ThreadPoolExecutor(max_workers=50) as ex:
        for res in ex.map(worker, nodes):
            if res:
                results.append(res)

    return results


# ============================================================
# 4. Основной worker
# ============================================================

def worker(item):
    name, raw = item

    url = normalize_ngenix(raw)

    try:
        channel = url.split(".cdn.ngenix.net/")[1].split("/")[0]
    except:
        return name, url, False

    base = url.rsplit("/", 1)[0]
    live = check_stream(base)

    if live:
        return name, live, True

    nodes = scan_all_nodes(channel)
    if nodes:
        node, found_url = nodes[0]
        return name, found_url, True

    return name, url, False


# ============================================================
# 5. Полный список каналов
# ============================================================

CHANNELS = {
    ".sci-fi": "a3569457567-s70378.cdn.ngenix.net/sony_sci_f...",
    "РЕН ТВ International": "a3569457567-s70378.cdn.ngenix.net/ren_tv/1/i...",
    "НТВ Право": "a3569457567-s70378.cdn.ngenix.net/ntv_pravo/...",
    "НТВ Сериал": "a3569457567-s70378.cdn.ngenix.net/ntv_serial...",
    "National geographic": "a3569457567-s70378.cdn.ngenix.net/national_g...",
    "Terra": "a3569457567-s70378.cdn.ngenix.net/terra/2/in...",
    "Ocean TV": "a3569457567-s70378.cdn.ngenix.net/ocean_tv/1...",
    "Точка РФ": "a3569457567-s70378.cdn.ngenix.net/hd_life/1/...",
    "History": "a3569457567-s70378.cdn.ngenix.net/history/1/...",
    "H2": "a3569457567-s70378.cdn.ngenix.net/history_2/...",
    "Дикий": "a3569457567-s70378.cdn.ngenix.net/dikiy/1/in...",
    "RTG HD": "a3569457567-s70378.cdn.ngenix.net/rtg_hd/1/i...",
    "DocuBox": "a3569457567-s70378.cdn.ngenix.net/docubox/1/...",
    "Galaxy TV": "a3569457567-s70378.cdn.ngenix.net/galaxy/1/i...",
    "Глазами туриста": "a3569457567-s70378.cdn.ngenix.net/glazami_tu...",
    "Travel+Adventure": "a3569457567-s70378.cdn.ngenix.net/travel_and...",
    "The explorers": "a3569457567-s70378.cdn.ngenix.net/the_explor...",
    "Viasat Explore": "a3569457567-s70378.cdn.ngenix.net/viasat_exp...",
    "Viasat History": "a3569457567-s70378.cdn.ngenix.net/viasat_his...",
    "Viasat Nature": "a3569457567-s70378.cdn.ngenix.net/viasat_nat...",
    "365 дней": "a3569457567-s70378.cdn.ngenix.net/365_dney_t...",
    "Hollywood HD": "a3569457567-s70378.cdn.ngenix.net/amc/2/inde...",
    "Amedia 1": "a3569457567-s70378.cdn.ngenix.net/amedia_1/2...",
    "Amedia 2": "a3569457567-s70378.cdn.ngenix.net/amedia_2/2...",
    "Amedia Hit": "a3569457567-s70378.cdn.ngenix.net/amedia_hit...",
    "Amedia Premium HD": "a3569457567-s70378.cdn.ngenix.net/amedia_pre...",
    "Bloomberg": "a3569457567-s70378.cdn.ngenix.net/bloomberg/...",
    "Shoghakat": "a3569457567-s70378.cdn.ngenix.net/shoghakat/...",
    ".Black": "a3569457567-s70378.cdn.ngenix.net/sony_turbo...",
    "Телекафе": "a3569457567-s70378.cdn.ngenix.net/telecafe/2...",
    "Индийское кино": "a3569457567-s70378.cdn.ngenix.net/india_tv/1...",
    "Индия": "a3569457567-s70378.cdn.ngenix.net/zee_tv/2/i...",
    "Наше новое кино": "a3569457567-s70378.cdn.ngenix.net/nashe_novo...",
    "Киноужас": "a3569457567-s70378.cdn.ngenix.net/kinouzhas/...",
    "Киносерия": "a3569457567-s70378.cdn.ngenix.net/mnogo_tv/1...",
    "Киносвидание": "a3569457567-s70378.cdn.ngenix.net/kinoklub/1...",
    "Дом Кино Премиум": "a3569457567-s70378.cdn.ngenix.net/dom_kino_p...",
    "ТВ3": "a3569457567-s70378.cdn.ngenix.net/tv_3/2/ind...",
    "TV XXI": "a3569457567-s70378.cdn.ngenix.net/tv_xxi/2/i...",
    "VIP Comedy": "a3569457567-s70378.cdn.ngenix.net/vip_comedy...",
    "VIP Megahit": "a3569457567-s70378.cdn.ngenix.net/vip_megahi...",
    "VIP Premiere": "a3569457567-s70378.cdn.ngenix.net/vip_premie...",
    "VIP Serial": "a3569457567-s70378.cdn.ngenix.net/vip_serial...",
    "Время": "a3569457567-s70378.cdn.ngenix.net/vremia/2/i...",
    "Дом Кино": "a3569457567-s70378.cdn.ngenix.net/dom_kino/1...",
    "Euronews": "a3569457567-s70378.cdn.ngenix.net/euronews/1...",
    "Еврокино": "a3569457567-s70378.cdn.ngenix.net/evrokino/1...",
    "Мир сериала": "a3569457567-s70378.cdn.ngenix.net/mir_serial...",
    "FashionBox": "a3569457567-s70378.cdn.ngenix.net/fashion_bo...",
    "Filmbox": "a3569457567-s70378.cdn.ngenix.net/filmbox/1/...",
    "Filmbox Arthouse": "a3569457567-s70378.cdn.ngenix.net/filmbox_ar...",
    "Flixsnip": "a3569457567-s70378.cdn.ngenix.net/flixsnip/1...",
    "Fox life": "a3569457567-s70378.cdn.ngenix.net/fox_life/1...",
    "Иллюзион+": "a3569457567-s70378.cdn.ngenix.net/illusion_p...",
    "Зоопарк": "a3569457567-s70378.cdn.ngenix.net/zoopark/2/...",
    "Armenia 1": "a3569457567-s70378.cdn.ngenix.net/h1/1/index...",
    "Armenia 2": "a3569457567-s70378.cdn.ngenix.net/h2/1/index...",
    "Известия": "a3569457567-s70378.cdn.ngenix.net/izvestiya/...",
    "Живи": "a3569457567-s70378.cdn.ngenix.net/jivi/1/ind...",
    "ATV Kinoman HD AM": "a3569457567-s70378.cdn.ngenix.net/kinoman/1/...",
    "КВН ТВ": "a3569457567-s70378.cdn.ngenix.net/kvn_tv/1/i...",
    "Мир 24": "a3569457567-s70378.cdn.ngenix.net/mir_24/1/i...",
    "Мир": "a3569457567-s70378.cdn.ngenix.net/mir/1/inde...",
    "Ностальгия": "a3569457567-s70378.cdn.ngenix.net/nostalgia/...",
    "РБК": "a3569457567-s70378.cdn.ngenix.net/rbc/1/inde...",
    "RTVI": "a3569457567-s70378.cdn.ngenix.net/rtvi/1/ind...",
    "shant serial": "a3569457567-s70378.cdn.ngenix.net/shant_seri...",
    "shant premium": "a3569457567-s70378.cdn.ngenix.net/shant_prem...",
    "21TV AM": "a3569457567-s70378.cdn.ngenix.net/dar21/1/in...",
    "Mezzo": "a3569457567-s70378.cdn.ngenix.net/mezzo/1/in...",
    "Muzzone": "a3569457567-s70378.cdn.ngenix.net/muzzone/1/...",
    "Shant music": "a3569457567-s70378.cdn.ngenix.net/shant_musi...",
    "Baby TV": "a3569457567-s70378.cdn.ngenix.net/baby_tv/2/...",
    "Tiji": "a3569457567-s70378.cdn.ngenix.net/tiji/2/ind...",
    "СТС Kids": "a3569457567-s70378.cdn.ngenix.net/ctc_kids/1...",
    "Nickelodeon": "a3569457567-s70378.cdn.ngenix.net/nickelodeo...",
    "Nicktoons": "a3569457567-s70378.cdn.ngenix.net/nicktoons/...",
    "Малыш": "a3569457567-s70378.cdn.ngenix.net/malish/1/i...",
    "Gulli Girl": "a3569457567-s70378.cdn.ngenix.net/gulli/1/in...",
    "Карусель": "a3569457567-s70378.cdn.ngenix.net/karusel/1/...",
    "Da Vinci": "a3569457567-s70378.cdn.ngenix.net/da_vinci/1...",
    "Детский мир": "a3569457567-s70378.cdn.ngenix.net/detskij_mi...",
    "UFC": "a3569457567-s70378.cdn.ngenix.net/ufc/2/inde...",
    "Viasat sport": "a3569457567-s70378.cdn.ngenix.net/viasat_spo...",
    "Бокс ТВ": "a3569457567-s70378.cdn.ngenix.net/boks_tv/1/...",
    "Матч! Планета": "a3569457567-s70378.cdn.ngenix.net/match_plan...",
    "KHL": "a3569457567-s70378.cdn.ngenix.net/kxl/1/inde...",
    "MMA-TV.com": "a3569457567-s70378.cdn.ngenix.net/m1_global/...",
}


# ============================================================
# 6. Запуск
# ============================================================

results = []

with ThreadPoolExecutor(max_workers=30) as ex:
    for res in ex.map(worker, CHANNELS.items()):
        results.append(res)

with open("ngenix_report.txt", "w", encoding="utf-8") as f:
    for name, url, live in results:
        if live:
            f.write(f"#EXTINF:-1,{name}\n{url}\n")
        else:
            f.write(f"#EXTINF:-1,{name} (DEAD)\n{url}\n")

print("Готово: ngenix_report.txt")