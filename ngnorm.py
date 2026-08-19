import os
import asyncio
import aiohttp
from aiohttp import ClientTimeout, TCPConnector
from datetime import datetime
from urllib.parse import urljoin, urlparse
import re

# ============================================================
# 1. Turbo-параметры
# ============================================================

CPU = os.cpu_count() or 1
TURBO = os.getenv("NGNORM_TURBO") == "1"

MAX_THREADS = CPU * (40 if TURBO else 10)
TIMEOUT = 1 if TURBO else 2

CACHE = {}
NODE_CACHE = {}
SESSION = None
SEMAPHORE = None

# ============================================================
# 2. Дополнительные структуры отчёта
# ============================================================

REPORT_LOG = []

FOUND_TIME = {}
FOUND_NODE = {}
FOUND_URL = {}

CDN_CHANNELS = {}
CDN_MANIFESTS = {}

LIVE_RESULTS = []
DEAD_RESULTS = []

REPORT_LOCK = asyncio.Lock()


# ============================================================
# 3. Телетайп СКАЛА ДРЕГ
# ============================================================

def current_time():
    return datetime.now().strftime("%H:%M:%S")


def current_datetime():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_report(text):
    REPORT_LOG.append(
        f"[{current_time()}] СКАЛА ДРЕГ :: {text}"
    )


def teletype(text):
    line = (
        f"[{current_time()}] "
        f"СКАЛА ДРЕГ :: {text}"
    )

    print(line, flush=True)
    log_report(text)


def teletype_ok(name, url):
    line = (
        f"[{current_time()}] "
        f"СКАЛА ДРЕГ :: [ OK ] "
        f"{name} -> {url}"
    )

    print(line, flush=True)
    log_report(
        f"[ OK ] {name} -> {url}"
    )


def teletype_dead(name, url):
    log_report(
        f"[DEAD] {name} -> {url}"
    )


# ============================================================
# 4. Универсальная нормализация домена и пути
# ============================================================

def normalize_ngenix(url):

    if not isinstance(url, str):
        return "INVALID:"

    url = url.strip()

    if not url:
        return "INVALID:"

    url = url.replace(
        "https://",
        "",
        1
    )

    url = url.replace(
        "http://",
        "",
        1
    )

    while "//" in url:
        url = url.replace(
            "//",
            "/"
        )

    if "..." in url:
        return "INVALID:" + url

    if ".cdn.ngenix.net" in url:

        parts = url.split(
            "/",
            1
        )

        host = parts[0]

        path = (
            parts[1]
            if len(parts) > 1
            else ""
        )

        if not path:
            return (
                "https://"
                + host
            )

        return (
            f"https://{host}/{path}"
        )

    return "INVALID:" + url


# ============================================================
# 5. Извлечение канала
# ============================================================

def extract_channel(url):

    marker = ".cdn.ngenix.net/"

    if marker not in url:
        return None

    try:

        value = url.split(
            marker,
            1
        )[1]

        value = value.strip("/")

        if not value:
            return None

        return value.split(
            "/",
            1
        )[0]

    except Exception:
        return None


# ============================================================
# 6. Извлечение CDN узла
# ============================================================

def extract_node(url):

    try:

        parsed = urlparse(url)

        if parsed.hostname:
            return parsed.hostname

    except Exception:
        pass

    return None


# ============================================================
# 7. Получение базового URL
# ============================================================

def get_base_url(url):

    if not url:
        return None

    if url.startswith(
        "INVALID:"
    ):
        return None

    clean = url.rstrip("/")

    without_scheme = (
        clean.replace(
            "https://",
            "",
            1
        )
        .replace(
            "http://",
            "",
            1
        )
    )

    if "/" not in without_scheme:
        return clean

    return clean.rsplit(
        "/",
        1
    )[0]


# ============================================================
# 8. Создание tvg-id
# ============================================================

def make_tvg_id(name):

    result = name.lower()

    replacements = {
        "+": "_plus",
        " ": "_",
        "-": "_",
        ".": "",
        "!": "",
        ":": "",
        "/": "_",
        "\\": "_",
    }

    for old, new in replacements.items():
        result = result.replace(
            old,
            new
        )

    while "__" in result:
        result = result.replace(
            "__",
            "_"
        )

    return result.strip("_")


# ============================================================
# 9. Название для M3U
# ============================================================

def make_m3u_name(name):

    result = name.lower()

    result = result.replace(
        "+",
        "_plus"
    )

    result = result.replace(
        " ",
        "_"
    )

    result = result.replace(
        "-",
        "_"
    )

    while "__" in result:
        result = result.replace(
            "__",
            "_"
        )

    return result.strip("_")


# ============================================================
# 10. Асинхронная загрузка текста
# ============================================================

async def fetch_text(url):

    global SESSION
    global SEMAPHORE

    if not url:
        return None

    if url.startswith(
        "INVALID:"
    ):
        return None

    async with SEMAPHORE:

        try:

            async with SESSION.get(
                url,
                allow_redirects=True,
                headers={
                    "User-Agent":
                        "SKALA-DREG/1.0",
                    "Accept":
                        "*/*",
                    "Connection":
                        "keep-alive",
                },
            ) as response:

                if response.status != 200:
                    return None

                try:

                    text = await response.text(
                        errors="ignore"
                    )

                except Exception:

                    raw = await response.read()

                    text = raw.decode(
                        "utf-8",
                        errors="ignore"
                    )

                return text

        except (
            asyncio.TimeoutError,
            aiohttp.ClientError,
            ConnectionError,
            OSError,
        ):

            return None

        except Exception:

            return None


# ============================================================
# 11. Асинхронная проверка URL
# ============================================================

async def fetch(url):

    if not url:
        return None

    if url.startswith(
        "INVALID:"
    ):
        return None

    if url in CACHE:
        return CACHE[url]

    text = await fetch_text(url)

    if text is None:

        CACHE[url] = None

        return None

    if (
        "#EXTM3U" in text
        or "#EXT-X-" in text
    ):

        CACHE[url] = url

        return url

    CACHE[url] = None

    return None


# ============================================================
# 12. Проверка HLS-потока
# ============================================================

async def check_stream(base):

    if not base:
        return None

    tests = [

        f"{base}/index.m3u8",

        f"{base}/playlist.m3u8",

        f"{base}/master.m3u8",

        f"{base}/1/index.m3u8",

        f"{base}/2/index.m3u8",

    ]

    tasks = [
        asyncio.create_task(
            fetch(url)
        )
        for url in tests
    ]

    try:

        results = await asyncio.gather(
            *tasks
        )

        for result in results:

            if result:
                return result

    finally:

        for task in tasks:

            if not task.done():
                task.cancel()

    return None


# ============================================================
# 13. Разбор URL внутри M3U8
# ============================================================

def parse_m3u8_urls(
    manifest_url,
    text
):

    urls = []

    if not text:
        return urls

    for raw_line in text.splitlines():

        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if (
            ".m3u8" not in line.lower()
        ):
            continue

        full_url = urljoin(
            manifest_url,
            line
        )

        if full_url not in urls:

            urls.append(
                full_url
            )

    return urls


# ============================================================
# 14. Извлечение названий из EXTINF
# ============================================================

def parse_extinf_entries(
    manifest_url,
    text
):

    entries = []

    if not text:
        return entries

    lines = text.splitlines()

    current_inf = None

    for line in lines:

        line = line.strip()

        if line.startswith(
            "#EXTINF:"
        ):

            current_inf = line

            continue

        if (
            current_inf
            and line
            and not line.startswith("#")
        ):

            if (
                ".m3u8" in line.lower()
            ):

                name = (
                    current_inf
                    .split(",", 1)[-1]
                    .strip()
                )

                tvg_match = re.search(
                    r'tvg-id="([^"]*)"',
                    current_inf
                )

                group_match = re.search(
                    r'group-title="([^"]*)"',
                    current_inf
                )

                tvg_id = (
                    tvg_match.group(1)
                    if tvg_match
                    else make_tvg_id(name)
                )

                group = (
                    group_match.group(1)
                    if group_match
                    else "Эфирные ТВ Плюс"
                )

                entries.append(
                    {
                        "name": name,
                        "tvg_id": tvg_id,
                        "group": group,
                        "url": urljoin(
                            manifest_url,
                            line
                        ),
                    }
                )

            current_inf = None

    return entries


# ============================================================
# 15. Полный разбор доступного CDN manifest
# ============================================================

async def inspect_cdn_manifest(
    node,
    seed_url
):

    if not node:
        return []

    if node in CDN_CHANNELS:

        return CDN_CHANNELS[node]

    discovered = []

    discovered_info = []

    queue = [
        seed_url
    ]

    visited = set()

    maximum_manifests = 100

    while (
        queue
        and len(visited)
        < maximum_manifests
    ):

        current = queue.pop(0)

        if current in visited:
            continue

        visited.add(current)

        text = await fetch_text(
            current
        )

        if not text:
            continue

        CDN_MANIFESTS[
            current
        ] = text

        entries = parse_extinf_entries(
            current,
            text
        )

        for entry in entries:

            url = entry["url"]

            if url not in discovered:

                discovered.append(
                    url
                )

                discovered_info.append(
                    {
                        "name":
                            entry["name"],
                        "tvg_id":
                            entry["tvg_id"],
                        "group":
                            entry["group"],
                        "url":
                            url,
                        "node":
                            node,
                        "found_at":
                            current_datetime(),
                    }
                )

        urls = parse_m3u8_urls(
            current,
            text
        )

        for url in urls:

            if url not in visited:

                queue.append(
                    url
                )

    CDN_CHANNELS[
        node
    ] = discovered_info

    log_report(
        f"CDN MANIFEST :: "
        f"{node} :: "
        f"обнаружено={len(discovered_info)}"
    )

    return discovered_info


# ============================================================
# 16. Опрос доступного CDN по найденному URL
# ============================================================

async def inspect_found_cdn(
    node,
    url
):

    if not node:
        return []

    log_report(
        f"ОПРОС CDN :: "
        f"{node}"
    )

    return await inspect_cdn_manifest(
        node,
        url
    )


# ============================================================
# 17. Проверка одного указанного узла
# ============================================================

async def check_node(
    node,
    channel
):

    base = (
        f"https://"
        f"{node}.cdn.ngenix.net/"
        f"{channel}"
    )

    result = await check_stream(
        base
    )

    if result:

        return (
            node,
            result
        )

    return None


# ============================================================
# 18. Поиск узла среди явно указанных
# ============================================================

async def scan_all_nodes(channel):

    if not channel:
        return None

    if channel in NODE_CACHE:

        return NODE_CACHE[
            channel
        ]

    # --------------------------------------------------------
    # ВАЖНО:
    #
    # Здесь намеренно используются только CDN-хосты,
    # которые уже присутствуют в исходном CHANNELS.
    #
    # Массовый перебор десятков тысяч произвольных узлов
    # не выполняется.
    # --------------------------------------------------------

    known_nodes = set()

    for raw in CHANNELS.values():

        normalized = normalize_ngenix(
            raw
        )

        if normalized.startswith(
            "INVALID:"
        ):
            continue

        node = extract_node(
            normalized
        )

        if node:
            known_nodes.add(
                node
            )

    if not known_nodes:

        NODE_CACHE[channel] = None

        return None

    log_report(
        f"ПОИСК УЗЛА :: "
        f"канал={channel} :: "
        f"известных CDN={len(known_nodes)}"
    )

    tasks = []

    for node in known_nodes:

        tasks.append(
            asyncio.create_task(
                check_node(
                    node,
                    channel
                )
            )
        )

    try:

        results = await asyncio.gather(
            *tasks
        )

    finally:

        for task in tasks:

            if not task.done():
                task.cancel()

    for result in results:

        if result:

            node, found_url = result

            NODE_CACHE[
                channel
            ] = (
                node,
                found_url
            )

            log_report(
                f"УЗЕЛ НАЙДЕН :: "
                f"{node} :: "
                f"{found_url}"
            )

            return (
                node,
                found_url
            )

    NODE_CACHE[channel] = None

    log_report(
        f"УЗЕЛ НЕ НАЙДЕН :: "
        f"{channel}"
    )

    return None


# ============================================================
# 19. Основной worker
# ============================================================

async def worker(
    name,
    raw
):

    url = normalize_ngenix(
        raw
    )

    found_at = current_datetime()

    if url.startswith(
        "INVALID:"
    ):

        teletype_dead(
            name,
            raw
        )

        return {
            "name": name,
            "url": raw,
            "live": False,
            "time": found_at,
            "node": None,
            "cdn": [],
        }

    channel = extract_channel(
        url
    )

    if not channel:

        teletype_dead(
            name,
            url
        )

        return {
            "name": name,
            "url": url,
            "live": False,
            "time": found_at,
            "node": None,
            "cdn": [],
        }

    base = get_base_url(
        url
    )

    if not base:

        teletype_dead(
            name,
            url
        )

        return {
            "name": name,
            "url": url,
            "live": False,
            "time": found_at,
            "node": None,
            "cdn": [],
        }

    log_report(
        f"ПРОВЕРКА :: "
        f"{name} :: "
        f"{base}"
    )

    live = await check_stream(
        base
    )

    if live:

        node = extract_node(
            live
        )

        FOUND_TIME[
            name
        ] = found_at

        FOUND_NODE[
            name
        ] = node

        FOUND_URL[
            name
        ] = live

        teletype_ok(
            name,
            live
        )

        cdn = await inspect_found_cdn(
            node,
            live
        )

        return {
            "name": name,
            "url": live,
            "live": True,
            "time": found_at,
            "node": node,
            "cdn": cdn,
        }

    log_report(
        f"ПРЯМОЙ URL НЕ ОТВЕТИЛ :: "
        f"{name}"
    )

    node_result = await scan_all_nodes(
        channel
    )

    if node_result:

        node, found_url = node_result

        FOUND_TIME[
            name
        ] = found_at

        FOUND_NODE[
            name
        ] = node

        FOUND_URL[
            name
        ] = found_url

        teletype_ok(
            name,
            found_url
        )

        cdn = await inspect_found_cdn(
            node,
            found_url
        )

        return {
            "name": name,
            "url": found_url,
            "live": True,
            "time": found_at,
            "node": node,
            "cdn": cdn,
        }

    teletype_dead(
        name,
        url
    )

    return {
        "name": name,
        "url": url,
        "live": False,
        "time": found_at,
        "node": None,
        "cdn": [],
    }


# ============================================================
# 20. Полный список каналов
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
    ".sci-fi": "a3569457567-s70378.cdn.ngenix.net/sony_sci-f...",
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
# 21. Формирование M3U блока
# ============================================================

def build_m3u_block(results):

    lines = []

    number = 1

    for item in results:

        if not item["live"]:
            continue

        name = item["name"]
        url = item["url"]

        tvg_id = make_tvg_id(
            name
        )

        m3u_name = make_m3u_name(
            name
        )

        lines.append(
            f'#EXTINF:-1 '
            f'tvg-id="{tvg_id}" '
            f'group-title="Эфирные ТВ Плюс",'
            f'{number}. {m3u_name}'
        )

        lines.append(
            url
        )

        lines.append("")

        number += 1

    return "\n".join(
        lines
    )


# ============================================================
# 22. Формирование блока CDN
# ============================================================

def build_cdn_block():

    lines = []

    for node, channels in CDN_CHANNELS.items():

        lines.append(
            f"CDN: {node}"
        )

        lines.append(
            "-" * 60
        )

        if not channels:

            lines.append(
                "Дополнительных каналов не обнаружено."
            )

            lines.append("")

            continue

        for number, item in enumerate(
            channels,
            1
        ):

            lines.append(
                f"{number:03}. "
                f"{item['name']}"
            )

            lines.append(
                f"    tvg-id: "
                f"{item['tvg_id']}"
            )

            lines.append(
                f"    group: "
                f"{item['group']}"
            )

            lines.append(
                f"    URL: "
                f"{item['url']}"
            )

            lines.append(
                f"    FOUND: "
                f"{item['found_at']}"
            )

            lines.append("")

    return "\n".join(
        lines
    )


# ============================================================
# 23. Запись полного отчёта
# ============================================================

def write_report(results):

    alive = [
        x
        for x in results
        if x["live"]
    ]

    dead = [
        x
        for x in results
        if not x["live"]
    ]

    with open(
        "ngnorm_report.txt",
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "============================================================\n"
        )

        f.write(
            "СКАЛА ДРЕГ :: NGENIX NORMALIZER\n"
        )

        f.write(
            "ПОЛНЫЙ ОТЧЁТ\n"
        )

        f.write(
            "============================================================\n\n"
        )

        f.write(
            f"Время запуска: "
            f"{current_datetime()}\n"
        )

        f.write(
            f"CPU: {CPU}\n"
        )

        f.write(
            f"TURBO: {TURBO}\n"
        )

        f.write(
            f"MAX_THREADS: {MAX_THREADS}\n"
        )

        f.write(
            f"TIMEOUT: {TIMEOUT}\n"
        )

        f.write(
            f"Всего записей: "
            f"{len(results)}\n"
        )

        f.write(
            f"Рабочих: "
            f"{len(alive)}\n"
        )

        f.write(
            f"Нерабочих: "
            f"{len(dead)}\n\n"
        )

        # ----------------------------------------------------
        # РАБОЧИЕ
        # ----------------------------------------------------

        f.write(
            "============================================================\n"
        )

        f.write(
            "РАБОЧИЕ КАНАЛЫ\n"
        )

        f.write(
            "============================================================\n\n"
        )

        if alive:

            for number, item in enumerate(
                alive,
                1
            ):

                f.write(
                    f"{number:03}. "
                    f"{item['name']}\n"
                )

                f.write(
                    f"    ВРЕМЯ НАХОЖДЕНИЯ: "
                    f"{item['time']}\n"
                )

                f.write(
                    f"    CDN: "
                    f"{item['node']}\n"
                )

                f.write(
                    f"    URL: "
                    f"{item['url']}\n"
                )

                f.write("\n")

        else:

            f.write(
                "Рабочих каналов не найдено.\n\n"
            )

        # ----------------------------------------------------
        # M3U
        # ----------------------------------------------------

        f.write(
            "============================================================\n"
        )

        f.write(
            "БЛОК ДЛЯ ВСТАВКИ В M3U PLAYLIST\n"
        )

        f.write(
            "============================================================\n\n"
        )

        m3u_block = build_m3u_block(
            results
        )

        if m3u_block:

            f.write(
                m3u_block
            )

            f.write("\n")

        else:

            f.write(
                "Рабочих каналов нет.\n"
            )

        f.write("\n")

        # ----------------------------------------------------
        # DEAD
        # ----------------------------------------------------

        f.write(
            "============================================================\n"
        )

        f.write(
            "НЕРАБОЧИЕ КАНАЛЫ\n"
        )

        f.write(
            "============================================================\n\n"
        )

        if dead:

            for number, item in enumerate(
                dead,
                1
            ):

                f.write(
                    f"{number:03}. "
                    f"{item['name']}\n"
                )

                f.write(
                    f"    URL: "
                    f"{item['url']}\n"
                )

                f.write("\n")

        else:

            f.write(
                "Нерабочих каналов нет.\n"
            )

        f.write("\n")

        # ----------------------------------------------------
        # CDN
        # ----------------------------------------------------

        f.write(
            "============================================================\n"
        )

        f.write(
            "КАНАЛЫ, КОТОРЫЕ ОТДАЁТ CDN\n"
        )

        f.write(
            "============================================================\n\n"
        )

        if CDN_CHANNELS:

            f.write(
                build_cdn_block()
            )

            f.write("\n")

        else:

            f.write(
                "CDN manifest не вернул "
                "дополнительных каналов.\n\n"
            )

        # ----------------------------------------------------
        # CDN M3U
        # ----------------------------------------------------

        f.write(
            "============================================================\n"
        )

        f.write(
            "M3U БЛОК КАНАЛОВ, ОБНАРУЖЕННЫХ В CDN\n"
        )

        f.write(
            "============================================================\n\n"
        )

        cdn_number = 1

        for node, channels in CDN_CHANNELS.items():

            for item in channels:

                f.write(
                    f'#EXTINF:-1 '
                    f'tvg-id="{item["tvg_id"]}" '
                    f'group-title="{item["group"]}",'
                    f'{cdn_number}. '
                    f'{make_m3u_name(item["name"])}\n'
                )

                f.write(
                    f"{item['url']}\n\n"
                )

                cdn_number += 1

        if cdn_number == 1:

            f.write(
                "Ничего дополнительно не обнаружено.\n\n"
            )

        # ----------------------------------------------------
        # ТЕЛЕТАЙП
        # ----------------------------------------------------

        f.write(
            "============================================================\n"
        )

        f.write(
            "ПОЛНЫЙ ТЕЛЕТАЙП СКАЛА ДРЕГ\n"
        )

        f.write(
            "============================================================\n\n"
        )

        for line in REPORT_LOG:

            f.write(
                line
                + "\n"
            )

        # ----------------------------------------------------
        # ЗАВЕРШЕНИЕ
        # ----------------------------------------------------

        f.write("\n")

        f.write(
            "============================================================\n"
        )

        f.write(
            "КОНЕЦ ОТЧЁТА\n"
        )

        f.write(
            "============================================================\n"
        )


# ============================================================
# 24. Главный запуск
# ============================================================

async def main():

    global SESSION
    global SEMAPHORE

    SEMAPHORE = asyncio.Semaphore(
        max(
            1,
            MAX_THREADS
        )
    )

    connector = TCPConnector(

        limit=max(
            1,
            MAX_THREADS
        ),

        limit_per_host=max(
            1,
            MAX_THREADS // 4
        ),

        ttl_dns_cache=300,

        enable_cleanup_closed=True,
    )

    SESSION = aiohttp.ClientSession(

        connector=connector,

        timeout=ClientTimeout(
            total=TIMEOUT,
            connect=TIMEOUT,
            sock_connect=TIMEOUT,
            sock_read=TIMEOUT,
        )
    )

    teletype(
        "=========================================="
    )

    teletype(
        "СКАЛА ДРЕГ :: NGENIX NORMALIZER"
    )

    teletype(
        "=========================================="
    )

    teletype(
        f"CPU={CPU} "
        f"TURBO={TURBO} "
        f"THREADS={MAX_THREADS}"
    )

    teletype(
        f"КАНАЛОВ К ПРОВЕРКЕ={len(CHANNELS)}"
    )

    teletype(
        "ПОДРОБНЫЙ ОТЧЁТ -> ngnorm_report.txt"
    )

    try:

        tasks = [

            asyncio.create_task(
                worker(
                    name,
                    raw
                )
            )

            for name, raw
            in CHANNELS.items()

        ]

        results = []

        total = len(tasks)

        completed = 0

        for task in asyncio.as_completed(
            tasks
        ):

            try:

                result = await task

                results.append(
                    result
                )

            except Exception as error:

                log_report(
                    f"WORKER ERROR :: "
                    f"{repr(error)}"
                )

            completed += 1

            # ------------------------------------------------
            # В КОНСОЛЬ ТОЛЬКО ВЫБОРОЧНЫЙ ПРОГРЕСС
            # ------------------------------------------------

            if (
                completed == 1
                or completed % 10 == 0
                or completed == total
            ):

                teletype(
                    f"ПРОГРЕСС :: "
                    f"{completed}/{total}"
                )

        # ----------------------------------------------------
        # Сохраняем исходный порядок
        # ----------------------------------------------------

        order = {

            name: position

            for position, name
            in enumerate(
                CHANNELS.keys()
            )

        }

        results.sort(
            key=lambda item:
                order.get(
                    item["name"],
                    999999
                )
        )

        LIVE_RESULTS.clear()
        DEAD_RESULTS.clear()

        LIVE_RESULTS.extend(
            x
            for x in results
            if x["live"]
        )

        DEAD_RESULTS.extend(
            x
            for x in results
            if not x["live"]
        )

        # ----------------------------------------------------
        # Полный отчёт
        # ----------------------------------------------------

        write_report(
            results
        )

        teletype(
            "------------------------------------------"
        )

        teletype(
            f"LIVE :: "
            f"{len(LIVE_RESULTS)}"
        )

        teletype(
            f"DEAD :: "
            f"{len(DEAD_RESULTS)}"
        )

        teletype(
            f"CDN MANIFEST CHANNELS :: "
            f"{sum(len(x) for x in CDN_CHANNELS.values())}"
        )

        teletype(
            "ПОЛНЫЙ ОТЧЁТ СОХРАНЁН:"
        )

        teletype(
            "ngnorm_report.txt"
        )

        teletype(
            "СКАЛА ДРЕГ :: ГОТОВО"
        )

        teletype(
            "------------------------------------------"
        )

    finally:

        if SESSION:

            await SESSION.close()

            SESSION = None


# ============================================================
# 25. Точка входа
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print()

        teletype(
            "ОСТАНОВКА ПОЛЬЗОВАТЕЛЕМ"
        )