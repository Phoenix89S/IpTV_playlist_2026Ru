import requests
import time
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = 4
SEGMENT_CHECK_COUNT = 3

# Шаблоны, которые NGENIX реально использует
NGENIX_PATTERNS = [
    "{channel}/index.m3u8",
    "{channel}/1/index.m3u8",
    "{channel}/2/index.m3u8",
    "{channel}/3/index.m3u8",
    "{channel}/hd/index.m3u8",
    "{channel}/sd/index.m3u8",
    "hls/CH_{channel}/variant.m3u8",
    "{channel}/tracks-v1a1/mono.m3u8",
]


# ============================
#   ПРОВЕРКА ПЛЕЙЛИСТА
# ============================

def check_m3u8(url):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return False, None
        if "EXTM3U" not in r.text:
            return False, None
        return True, r.text
    except:
        return False, None


def extract_segments(m3u8_text):
    segments = []
    for line in m3u8_text.splitlines():
        if ".ts" in line or ".m4s" in line:
            segments.append(line.strip())
    return segments


def check_segment(base_url, segment):
    if segment.startswith("http"):
        url = segment
    else:
        url = base_url.rsplit("/", 1)[0] + "/" + segment

    try:
        start = time.time()
        r = requests.get(url, timeout=TIMEOUT, stream=True)
        if r.status_code == 200:
            return True, time.time() - start
        return False, None
    except:
        return False, None


def deep_probe(url):
    ok, m3u8 = check_m3u8(url)
    if not ok:
        return "FAIL_M3U8", None

    segments = extract_segments(m3u8)
    if not segments:
        return "FAIL_SEGMENTS", None

    speeds = []
    for seg in segments[:SEGMENT_CHECK_COUNT]:
        ok, speed = check_segment(url, seg)
        if ok:
            speeds.append(speed)

    if len(speeds) == SEGMENT_CHECK_COUNT:
        return "OK", sum(speeds) / len(speeds)
    if len(speeds) > 0:
        return "PARTIAL", None
    return "FAIL_SEGMENTS", None


# ============================
#   ПРОВЕРКА УЗЛА
# ============================

def probe_node(node):
    url = f"https://{node}.cdn.ngenix.net/"
    try:
        start = time.time()
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code in (200, 403, 404):
            return True, time.time() - start
        return False, None
    except:
        return False, None


def node_scan_streams(node, channel_key):
    base = f"https://{node}.cdn.ngenix.net/"
    found = []

    for pattern in NGENIX_PATTERNS:
        url = base + pattern.format(channel=channel_key)
        ok, _ = check_m3u8(url)
        if ok:
            found.append(url)

    return found


# ============================
#   СКАНИРОВАНИЕ КАНАЛА
# ============================

def scan_channel(display_name, channel_key, url):
    node = url.split("//")[1].split(".")[0].split("-")[-1]

    orig_status, orig_speed = deep_probe(url)

    path = url.split(".cdn.ngenix.net/")[1]
    alt_url = f"https://cdn.ngenix.net/{path}"
    alt_status, alt_speed = deep_probe(alt_url)

    node_ok, node_speed = probe_node(node)

    node_streams = node_scan_streams(node, channel_key)

    return {
        "display_name": display_name,
        "channel_key": channel_key,
        "original_url": url,
        "original": (orig_status, orig_speed),
        "alt_url": alt_url,
        "alt": (alt_status, alt_speed),
        "node": node,
        "node_probe": (node_ok, node_speed),
        "node_streams": node_streams,
    }


def scan_playlist(channels):
    out = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = [ex.submit(scan_channel, disp, key, url) for disp, key, url in channels]
        for f in futs:
            out.append(f.result())
    return out


# ============================
#   РУССКИЕ СТАТУСЫ
# ============================

def status_m3u8_to_text(status, speed):
    if status == "OK" and speed is not None:
        return f"поток доступен, время={speed:.3f}с"
    if status == "OK" and speed is None:
        return "поток доступен, время не определено"
    if status == "PARTIAL":
        return "частично работает (часть сегментов недоступна)"
    if status == "FAIL_M3U8":
        return "плейлист отсутствует, данных нет"
    if status == "FAIL_SEGMENTS":
        return "сегменты отсутствуют, данных нет"
    return "данных нет"


def status_node_to_text(ok, speed):
    if ok and speed is not None:
        return f"узел отвечает, время={speed:.3f}с"
    if ok and speed is None:
        return "узел отвечает, время не определено"
    return "узел не отвечает, данных нет"


# ============================
#   ГЕНЕРАЦИЯ ОТЧЁТА ngScala.txt
# ============================

def write_ngslala(report, filename="ngScala.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("=== NGENIX CDN СКАЛА/ДРЭГ ТЕЛЕМЕТРИЯ ===\n")
        f.write("РЕЖИМ: АЭС / КАНАЛЬНЫЙ МОНИТОРИНГ\n")
        f.write("------------------------------------------------------------\n\n")

        for item in report:
            ch_disp = item["display_name"]
            node = item["node"]

            node_ok, node_speed = item["node_probe"]
            node_status_text = status_node_to_text(node_ok, node_speed)

            orig_status, orig_speed = item["original"]
            orig_status_text = status_m3u8_to_text(orig_status, orig_speed)

            alt_status, alt_speed = item["alt"]
            alt_status_text = status_m3u8_to_text(alt_status, alt_speed)

            f.write(f"[КАНАЛ] {ch_disp}\n")
            f.write(f"  [УЗЕЛ] s{node}.cdn.ngenix.net\n")
            f.write(f"  [NODE] СТАТУС: {node_status_text}\n\n")

            f.write(f"  [ОРИГИНАЛ] {item['original_url']}\n")
            f.write(f"             СТАТУС: {orig_status_text}\n\n")

            f.write(f"  [АЛЬТЕРНАТИВА] {item['alt_url']}\n")
            f.write(f"                 СТАТУС: {alt_status_text}\n\n")

            f.write("  [ПОТОКИ УЗЛА]\n")
            if item["node_streams"]:
                for s_url in item["node_streams"]:
                    f.write(f"    -> {s_url}\n")
            else:
                f.write("    -> активные потоки не обнаружены\n")

            f.write("------------------------------------------------------------\n\n")


# ============================
#   ПОЛНЫЙ СПИСОК КАНАЛОВ
# ============================

channels = [
    # ФИЛЬМЫ И СЕРИАЛЫ
    ("filmzone HD", "filmzone", "https://a3569457567-s70378.cdn.ngenix.net/filmzone/index.m3u8"),
    ("bazmoc HD", "bazmoc", "https://a3569457567-s70378.cdn.ngenix.net/bazmoc/index.m3u8"),
    ("sony_sci_fi", "sony_sci_fi", "https://a3569457567-s70378.cdn.ngenix.net/sony_sci_fi/index.m3u8"),
    ("ntv_serial", "ntv_serial", "https://a3569457567-s70378.cdn.ngenix.net/ntv_serial/index.m3u8"),
    ("mir_seriala", "mir_seriala", "https://a3569457567-s70378.cdn.ngenix.net/mir_seriala/index.m3u8"),
    ("sony_turbo", "sony_turbo", "https://a3569457567-s70378.cdn.ngenix.net/sony_turbo/index.m3u8"),
    ("vip_serial", "vip_serial", "https://a3569457567-s70378.cdn.ngenix.net/vip_serial/index.m3u8"),
    ("amc", "amc", "https://a3569457567-s70378.cdn.ngenix.net/amc/index.m3u8"),
    ("filmbox", "filmbox", "https://a3569457567-s70378.cdn.ngenix.net/filmbox/index.m3u8"),
    ("kinouzhas", "kinouzhas", "https://a3569457567-s70378.cdn.ngenix.net/kinouzhas/index.m3u8"),
    ("evrokino", "evrokino", "https://a3569457567-s70378.cdn.ngenix.net/evrokino/index.m3u8"),
    ("amedia_2", "amedia_2", "https://a3569457567-s70378.cdn.ngenix.net/amedia_2/index.m3u8"),
    ("dom_kino", "dom_kino", "https://a3569457567-s70378.cdn.ngenix.net/dom_kino/index.m3u8"),
    ("dom_kino_premium_hd HD", "dom_kino_pr", "https://a3569457567-s70378.cdn.ngenix.net/dom_kino_pr/index.m3u8"),
    ("nashe_novoe_kino", "nashe_novoe", "https://a3569457567-s70378.cdn.ngenix.net/nashe_novoe/index.m3u8"),
    ("mnogo_tv", "mnogo_tv", "https://a3569457567-s70378.cdn.ngenix.net/mnogo_tv/index.m3u8"),
    ("kinoklub", "kinoklub", "https://a3569457567-s70378.cdn.ngenix.net/kinoklub/index.m3u8"),
    ("illusion_plus", "illusion_pl", "https://a3569457567-s70378.cdn.ngenix.net/illusion_pl/index.m3u8"),
    ("flixsnip", "flixsnip", "https://a3569457567-s70378.cdn.ngenix.net/flixsnip/index.m3u8"),

    # ПОЗНАВАТЕЛЬНЫЕ
    ("hd_life", "hd_life", "https://a3569457567-s70378.cdn.ngenix.net/hd_life/index.m3u8"),
    ("docubox", "docubox", "https://a3569457567-s70378.cdn.ngenix.net/docubox/index.m3u8"),
    ("curiosity_stream", "curiosity_s", "https://a3569457567-s70378.cdn.ngenix.net/curiosity_s/index.m3u8"),
    ("ocean_tv", "ocean_tv", "https://a3569457567-s70378.cdn.ngenix.net/ocean_tv/index.m3u8"),
    ("history", "history", "https://a3569457567-s70378.cdn.ngenix.net/history/index.m3u8"),
    ("zoopark", "zoopark", "https://a3569457567-s70378.cdn.ngenix.net/zoopark/index.m3u8"),
    ("galaxy", "galaxy", "https://a3569457567-s70378.cdn.ngenix.net/galaxy/index.m3u8"),
    ("terra", "terra", "https://a3569457567-s70378.cdn.ngenix.net/terra/index.m3u8"),

    # ДЕТСКИЕ
    ("nicktoons", "nicktoons", "https://a3569457567-s70378.cdn.ngenix.net/nicktoons/index.m3u8"),
    ("ducktv", "ducktv", "https://a3569457567-s70378.cdn.ngenix.net/ducktv/index.m3u8"),
    ("karusel", "karusel", "https://a3569457567-s70378.cdn.ngenix.net/karusel/index.m3u8"),
    ("tiji", "tiji", "https://a3569457567-s70378.cdn.ngenix.net/tiji/index.m3u8"),
    ("nickelodeon", "nickelodeon", "https://a3569457567-s70378.cdn.ngenix.net/nickelodeon/index.m3u8"),
    ("gulli", "gulli", "https://a3569457567-s70378.cdn.ngenix.net/gulli/index.m3u8"),

    # СПОРТ
    ("trace_sport_stars", "trace_sport", "https://a3569457567-s70378.cdn.ngenix.net/trace_sport/index.m3u8"),
    ("match_planeta", "match_plane", "https://a3569457567-s70378.cdn.ngenix.net/match_plane/index.m3u8"),
    ("kxl", "kxl", "https://a3569457567-s70378.cdn.ngenix.net/kxl/index.m3u8"),

    # МУЗЫКА
    ("tnt_music", "tnt_music", "https://a3569457567-s70378.cdn.ngenix.net/tnt_music/index.m3u8"),
    ("mezzo", "mezzo", "https://a3569457567-s70378.cdn.ngenix.net/mezzo/index.m3u8"),

    # НОВОСТИ И ОБЩИЕ
    ("rtr_planeta", "rtr_planeta", "https://a3569457567-s70378.cdn.ngenix.net/rtr_planeta/index.m3u8"),
    ("ntv_pravo", "ntv_pravo", "https://a3569457567-s70378.cdn.ngenix.net/ntv_pravo/index.m3u8"),
    ("mir", "mir", "https://a3569457567-s70378.cdn.ngenix.net/mir/index.m3u8"),
    ("rtvi", "rtvi", "https://a3569457567-s70378.cdn.ngenix.net/rtvi/index.m3u8"),
    ("ren_tv", "ren_tv", "https://a3569457567-s70378.cdn.ngenix.net/ren_tv/index.m3u8"),
    ("rbc", "rbc", "https://a3569457567-s70378.cdn.ngenix.net/rbc/index.m3u8"),
    ("euronews", "euronews", "https://a3569457567-s70378.cdn.ngenix.net/euronews/index.m3u8"),

    # РАЗВЛЕКАТЕЛЬНЫЕ И ЛАЙФСТАЙЛ
    ("tnt_4", "tnt_4", "https://a3569457567-s70378.cdn.ngenix.net/tnt_4/index.m3u8"),
    ("kvn_tv", "kvn_tv", "https://a3569457567-s70378.cdn.ngenix.net/kvn_tv/index.m3u8"),
    ("nostalgia", "nostalgia", "https://a3569457567-s70378.cdn.ngenix.net/nostalgia/index.m3u8"),
    ("tv_3", "tv_3", "https://a3569457567-s70378.cdn.ngenix.net/tv_3/index.m3u8"),
    ("telecafe", "telecafe", "https://a3569457567-s70378.cdn.ngenix.net/telecafe/index.m3u8"),

    # РЕГИОНАЛЬНЫЕ
    ("h1 HD", "h1", "https://a3569457567-s70378.cdn.ngenix.net/h1/index.m3u8"),
    ("h2", "h2", "https://a3569457567-s70378.cdn.ngenix.net/h2/index.m3u8"),
    ("zee_tv", "zee_tv", "https://a3569457567-s70378.cdn.ngenix.net/zee_tv/index.m3u8"),
    ("shant HD", "shant", "https://a3569457567-s70378.cdn.ngenix.net/shant/index.m3u8"),
    ("kentron", "kentron", "https://a3569457567-s70378.cdn.ngenix.net/kentron/index.m3u8"),
    ("dar21", "dar21", "https://a3569457567-s70378.cdn.ngenix.net/dar21/index.m3u8"),
    ("atv HD", "atv", "https://a3569457567-s70378.cdn.ngenix.net/atv/index.m3u8"),

    # ДЛЯ ВЗРОСЛЫХ (18+)
    ("erox", "erox", "https://a3569457567-s70378.cdn.ngenix.net/erox/index.m3u8"),
    ("playboy", "playboy", "https://a3569457567-s70378.cdn.ngenix.net/playboy/index.m3u8"),
]


# ============================
#   ЗАПУСК
# ============================

if __name__ == "__main__":
    report = scan_playlist(channels)
    write_ngslala(report, "ngScala.txt")