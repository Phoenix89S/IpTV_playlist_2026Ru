import requests
import time
import re
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


def node_scan_streams(node, channel):
    base = f"https://{node}.cdn.ngenix.net/"
    found = []

    for pattern in NGENIX_PATTERNS:
        url = base + pattern.format(channel=channel)
        ok, _ = check_m3u8(url)
        if ok:
            found.append(url)

    return found


# ============================
#   СКАНИРОВАНИЕ КАНАЛА
# ============================

def scan_channel(name, url):
    node = url.split("//")[1].split(".")[0].split("-")[-1]

    orig_status, orig_speed = deep_probe(url)

    path = url.split(".cdn.ngenix.net/")[1]
    alt_url = f"https://cdn.ngenix.net/{path}"
    alt_status, alt_speed = deep_probe(alt_url)

    node_ok, node_speed = probe_node(node)

    node_streams = node_scan_streams(node, name)

    return {
        "channel": name,
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
        futs = [ex.submit(scan_channel, name, url) for name, url in channels]
        for f in futs:
            out.append(f.result())
    return out


# ============================
#   РУССКИЕ СТАТУСЫ
# ============================

def status_m3u8_to_text(status, speed):
    if status == "OK":
        return f"поток доступен, время={speed:.3f}с"
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
#   ГЕНЕРАЦИЯ ОТЧЁТА ngSlala.txt
# ============================

def write_ngslala(report, filename="ngSlala.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("=== NGENIX CDN СКАЛА/ДРЭГ ТЕЛЕМЕТРИЯ ===\n")
        f.write("РЕЖИМ: АЭС / КАНАЛЬНЫЙ МОНИТОРИНГ\n")
        f.write("------------------------------------------------------------\n\n")

        for item in report:
            ch = item["channel"]
            node = item["node"]

            node_ok, node_speed = item["node_probe"]
            node_status_text = status_node_to_text(node_ok, node_speed)

            orig_status, orig_speed = item["original"]
            orig_status_text = status_m3u8_to_text(orig_status, orig_speed)

            alt_status, alt_speed = item["alt"]
            alt_status_text = status_m3u8_to_text(alt_status, alt_speed)

            f.write(f"[КАНАЛ] {ch}\n")
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
#   ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================

if __name__ == "__main__":
    channels = [
        ("filmzone", "https://a3569457567-s70378.cdn.ngenix.net/filmzone/index.m3u8"),
        ("karusel", "https://a3569457567-s70378.cdn.ngenix.net/karusel/index.m3u8"),
        ("ntv_serial", "https://a3569457567-s70378.cdn.ngenix.net/ntv_serial/index.m3u8"),
        ("amc", "https://a3569457567-s70378.cdn.ngenix.net/amc/index.m3u8"),
        # сюда вставляешь весь свой список
    ]

    report = scan_playlist(channels)
    write_ngslala(report, "ngSlala.txt")