import os
import asyncio
import aiohttp
from aiohttp import ClientTimeout

# ============================================================
# 1. Turbo‑параметры
# ============================================================

CPU = os.cpu_count()
TURBO = os.getenv("NGNORM_TURBO") == "1"

MAX_THREADS = CPU * (40 if TURBO else 10)
TIMEOUT = 1 if TURBO else 2

CACHE = {}          # turbo cache
NODE_CACHE = {}     # cache узлов
SESSION = None      # aiohttp session


# ============================================================
# 2. Универсальная нормализация домена и пути
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
# 3. Асинхронная проверка URL
# ============================================================

async def fetch(url):
    if url in CACHE:
        return CACHE[url]

    try:
        async with SESSION.get(url) as r:
            if r.status == 200:
                CACHE[url] = url
                return url
    except:
        pass

    CACHE[url] = None
    return None


# ============================================================
# 4. Turbo‑проверка потока
# ============================================================

async def check_stream(base):
    tests = [
        f"{base}/index.m3u8",
        f"{base}/playlist.m3u8",
        f"{base}/master.m3u8",
        f"{base}/segment0.ts",
        f"{base}/1/index.m3u8",
        f"{base}/2/index.m3u8",
    ]

    tasks = [fetch(u) for u in tests]
    results = await asyncio.gather(*tasks)

    for r in results:
        if r:
            return r

    return None


# ============================================================
# 5. Turbo‑сканирование всех узлов NGENIX
# ============================================================

async def scan_all_nodes(channel):
    if channel in NODE_CACHE:
        return NODE_CACHE[channel]

    ranges = [(50000,60000),(60000,70000),(70000,80000)]
    nodes = [f"s{x}" for start, end in ranges for x in range(start, end)]

    async def check_node(node):
        base = f"https://{node}.cdn.ngenix.net/{channel}"
        return await check_stream(base)

    tasks = [check_node(n) for n in nodes]
    results = await asyncio.gather(*tasks)

    for node, result in zip(nodes, results):
        if result:
            NODE_CACHE[channel] = (node, result)
            return (node, result)

    NODE_CACHE[channel] = None
    return None


# ============================================================
# 6. Основной worker
# ============================================================

async def worker(name, raw):
    url = normalize_ngenix(raw)

    try:
        channel = url.split(".cdn.ngenix.net/")[1].split("/")[0]
    except:
        return name, url, False

    base = url.rsplit("/", 1)[0]
    live = await check_stream(base)

    if live:
        return name, live, True

    node_result = await scan_all_nodes(channel)
    if node_result:
        node, found_url = node_result
        return name, found_url, True

    return name, url, False


# ============================================================
# 7. Полный список каналов (все объединено)
# ============================================================

CHANNELS = {

    # ===== viju+ =====
    "viju+ Premiere": "s70378.cdn.ngenix.net/vip_premiere/index.m3u8",
    "viju+ Megahit": "s70378.cdn.ngenix.net/vip_megahit/index.m3u8",
    "viju+ Comedy": "s70378.cdn.ngenix.net/vip_comedy/index.m3u8",
    "viju+ Serial": "s70378.cdn.ngenix.net/vip_serial/index.m3u8",
    "viju+ Planet": "s70378.cdn.ngenix.net/vip_planet/index.m3u8",
    "viju+ Sport": "s70378.cdn.ngenix.net/vip_sport/index.m3u8",
    "viju+ Novella": "s70378.cdn.ngenix.net/vip_novella/index.m3u8",
    "viju+ Romance": "s70378.cdn.ngenix.net/vip_romance/index.m3u8",

    # ===== Horror pack =====
    "Страшное HD": "s70378.cdn.ngenix.net/horror/strashnoe_hd/index.m3u8",
    "Страх HD": "s70378.cdn.ngenix.net/horror/strakh_hd/index.m3u8",
    "TRASH HD": "s70378.cdn.ngenix.net/trash/trash_hd/index.m3u8",
    "Scream": "s70378.cdn.ngenix.net/horror/scream/index.m3u8",

    # ===== Еда =====
    "Еда": "s70378.cdn.ngenix.net/eda/index.m3u8",

    # ===== Ключ =====
    "Ключ": "s70378.cdn.ngenix.net/misc/kluch/index.m3u8",
    "Ключ HD": "s70378.cdn.ngenix.net/misc/kluch_hd/index.m3u8",
    "Ключ ТВ": "s70378.cdn.ngenix.net/misc/kluch_tv/index.m3u8",

    # ===== ВСЕ каналы, которые были ранее =====
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
    "Еврокино": "a3569457567-s70378.cdn.ngenix.net/evrokино/1...",
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
# 8. Запуск
# ============================================================

async def main():
    global SESSION
    SESSION = aiohttp.ClientSession(timeout=ClientTimeout(total=TIMEOUT))

    tasks = [worker(name, raw) for name, raw in CHANNELS.items()]
    results = await asyncio.gather(*tasks)

    await SESSION.close()

    with open("ngenix_report.txt", "w", encoding="utf-8") as f:
        for name, url, live in results:
            if live:
                f.write(f"#EXTINF:-1,{name}\n{url}\n")
            else:
                f.write(f"#EXTINF:-1,{name} (DEAD)\n{url}\n")

    print("Готово: ngenix_report.txt")


if __name__ == "__main__":
    asyncio.run(main())