# ============================================================
#   NADE SuperScan SKALA Engine
#   NGENIX Alias Discovery + SKALA/ДРЕГ Telemetry
#   Финальная версия
# ============================================================

import requests
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

TIMEOUT = 4
SEGMENT_CHECK_COUNT = 3
MAX_PASSES = 5
MAX_WORKERS = 40

INVENTORY_FILE = "cdn_alias_inventory.json"

# ============================================================
#   ШАБЛОНЫ NGENIX
# ============================================================

NGENIX_PATTERNS = [
    "{alias}/index.m3u8",
    "{alias}/1/index.m3u8",
    "{alias}/2/index.m3u8",
    "{alias}/3/index.m3u8",
    "{alias}/hd/index.m3u8",
    "{alias}/sd/index.m3u8",
    "{alias}/tracks-v1a1/mono.m3u8",
    "hls/CH_{alias}/variant.m3u8",
]

# ============================================================
#   СЕМЕЙСТВА КАНАЛОВ
# ============================================================

FAMILIES = {
    "ntv": ["ntv_pravo", "ntv_serial", "ntv_hit", "ntv_style"],
    "vip": ["vip_serial", "vip_comedy", "vip_megahit", "vip_premiere"],
    "amedia": ["amedia_1", "amedia_2", "amedia_hit", "amedia_premium_hd"],
    "sony": ["sony_sci_fi", "sony_turbo", "sony_channel"],
    "filmbox": ["filmbox", "filmbox_arthouse"],
    "viasat": ["viasat_nature", "viasat_explore", "viasat_history", "viasat_sport"],
    "mir": ["mir", "mir_seriala"],
    "nastroy_kino": [
        "rodnoe_kino", "nashe_novoe_kino", "kinouzhas", "kinoseriya",
        "indiyskoe_kino", "kinosvidanie", "muzhskoe_kino", "kinosemya",
        "kinopremyera", "kinomix", "kinokomediya", "kinohit",
    ],
}

# ============================================================
#   ЗАГРУЗКА / СОХРАНЕНИЕ INVENTORY
# ============================================================

def load_inventory():
    try:
        with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_inventory(inv):
    with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)

# ============================================================
#   ПРОВЕРКА M3U8
# ============================================================

def check_m3u8(url):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return False, None, r.status_code
        if "EXTM3U" not in r.text:
            return False, None, r.status_code
        return True, r.text, r.status_code
    except:
        return False, None, None

def extract_segments(m3u8_text):
    segments = []
    for line in m3u8_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ".ts" in line or ".m4s" in line:
            segments.append(line)
    return segments

def check_segment(base_url, segment):
    url = urljoin(base_url, segment)
    try:
        start = time.time()
        r = requests.get(url, timeout=TIMEOUT, stream=True)
        if r.status_code == 200:
            return True, time.time() - start
        return False, None
    except:
        return False, None

def deep_probe(url):
    ok, m3u8, http_code = check_m3u8(url)
    if not ok:
        return "FAIL_M3U8", None, http_code

    segments = extract_segments(m3u8)
    if not segments:
        return "FAIL_SEGMENTS", None, http_code

    speeds = []
    for seg in segments[:SEGMENT_CHECK_COUNT]:
        ok_seg, speed = check_segment(url, seg)
        if ok_seg and speed is not None:
            speeds.append(speed)

    if len(speeds) == SEGMENT_CHECK_COUNT:
        return "OK", sum(speeds) / len(speeds), http_code
    if len(speeds) > 0:
        return "PARTIAL", None, http_code
    return "FAIL_SEGMENTS", None, http_code

# ============================================================
#   ПРОВЕРКА УЗЛА
# ============================================================

def probe_node(node):
    url = f"https://{node}.cdn.ngenix.net/"
    try:
        start = time.time()
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code in (200, 403, 404):
            return True, time.time() - start, r.status_code
        return False, None, r.status_code
    except:
        return False, None, None

# ============================================================
#   СКАНИРОВАНИЕ ПО alias
# ============================================================

def scan_alias_on_node(alias, node):
    base = f"https://{node}.cdn.ngenix.net/"
    results = []

    for pattern in NGENIX_PATTERNS:
        url = base + pattern.format(alias=alias)
        status, speed, http_code = deep_probe(url)

        if status in ("OK", "PARTIAL"):
            alt_url = f"https://cdn.ngenix.net/{alias}/index.m3u8"
            alt_status, alt_speed, alt_code = deep_probe(alt_url)

            node_ok, node_speed, node_code = probe_node(node)

            results.append({
                "node": node,
                "url": url,
                "status": status,
                "speed": speed,
                "http_code": http_code,
                "alt_url": alt_url,
                "alt_status": alt_status,
                "alt_speed": alt_speed,
                "alt_code": alt_code,
                "node_status": node_ok,
                "node_speed": node_speed,
                "node_code": node_code,
                "node_streams": []
            })

    return results

# ============================================================
#   ГЕНЕРАЦИЯ КАНДИДАТОВ
# ============================================================

def generate_alias_candidates(alias):
    out = set()
    base = alias.lower().replace(" ", "_").replace("-", "_")

    out.add(base)
    out.add(base + "_hd")
    out.add(base + "_sd")
    out.add(base + "_plus")
    out.add(base + "_premium")
    out.add(base + "_mega")
    out.add(base + "_serial")
    out.add(base + "_hit")
    out.add(base + "_mix")

    return out

def generate_family_candidates():
    out = set()
    for fam, items in FAMILIES.items():
        for alias in items:
            out.add(alias)
    return out

def build_candidate_aliases(seed_aliases):
    candidates = set(seed_aliases)
    candidates |= generate_family_candidates()
    for a in list(candidates):
        candidates |= generate_alias_candidates(a)
    return candidates

# ============================================================
#   DISCOVERY PASS
# ============================================================

def discovery_pass(inv, nodes, seed_aliases):
    candidates = build_candidate_aliases(seed_aliases)
    new_inventory_entries = {}

    alive_nodes = []
    for node in nodes:
        ok, node_speed, node_code = probe_node(node)
        if ok:
            alive_nodes.append(node)

    if not alive_nodes:
        return {}, set()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = []
        for alias in candidates:
            for node in alive_nodes:
                futures.append(ex.submit(scan_alias_on_node, alias, node))

        for fut in as_completed(futures):
            res = fut.result()
            for item in res:
                alias = item["url"].split(".cdn.ngenix.net/")[1].split("/")[0]
                if alias not in new_inventory_entries:
                    new_inventory_entries[alias] = []
                new_inventory_entries[alias].append(item)

    new_aliases = set(new_inventory_entries.keys()) - set(inv.keys())
    return new_inventory_entries, new_aliases

# ============================================================
#   ЛОГИ: ЧЕЛОВЕЧЕСКИЙ, МАШИННЫЙ, M3U
# ============================================================

def write_skala_human(inv, filename="ngSuperscan_SKALA.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("=== NGENIX CDN SUPER-SCAN / SKALA / ДРЕГ ===\n")
        f.write("РЕЖИМ: ПОЛНАЯ ТЕЛЕМЕТРИЯ\n")
        f.write("------------------------------------------------------------\n\n")

        for alias, items in inv.items():
            f.write(f"[КАНАЛ] {alias}\n")
            for item in items:
                node = item["node"]
                f.write(f"  [УЗЕЛ] {node}.cdn.ngenix.net\n")
                f.write(f"  [NODE] STATUS={item['node_status']} time={item['node_speed']}\n\n")

                f.write(f"  [ОРИГИНАЛ] {item['url']}\n")
                f.write(f"             STATUS={item['status']} speed={item['speed']}\n\n")

                f.write(f"  [АЛЬТЕРНАТИВА] {item['alt_url']}\n")
                f.write(f"                 STATUS={item['alt_status']} speed={item['alt_speed']}\n\n")

                f.write("  [ПОТОКИ УЗЛА]\n")
                for s in item.get("node_streams", []):
                    f.write(f"    -> {s}\n")

                f.write("------------------------------------------------------------\n\n")

def write_skala_machine_txt(inv, filename="ngSuperscan_SKALA_machine.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)

def write_skala_m3u(inv, filename="ngSuperscan_SKALA.m3u"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for alias, items in inv.items():
            best = sorted(items, key=lambda x: (x["status"] != "OK", x["status"] != "PARTIAL"))[0]
            f.write(f'#EXTINF:-1 tvg-id="{alias}" group-title="NGENIX SuperScan",{alias}\n')
            f.write(best["url"] + "\n\n")

# ============================================================
#   SUPERPROBE ENGINE
# ============================================================

def superprobe(seed_aliases):
    nodes = ["s70378", "s70379", "s70380", "s70381", "s70382"]

    inv = load_inventory()
    print(f"Загружено alias из inventory: {len(inv)}")

    for p in range(1, MAX_PASSES + 1):
        print(f"\n=== PASS {p} ===")

        new_entries, new_aliases = discovery_pass(inv, nodes, seed_aliases)
        print(f"Найдено новых alias: {len(new_aliases)}")

        if not new_aliases:
            print("Новых alias нет — остановка.")
            break

        for alias, items in new_entries.items():
            if alias not in inv:
                inv[alias] = []
            inv[alias].extend(items)

        save_inventory(inv)

    print("\nИТОГОВЫЙ INVENTORY:", len(inv))

    write_skala_human(inv)
    write_skala_machine_txt(inv)
    write_skala_m3u(inv)

    return inv

# ============================================================
#   ЗАПУСК
# ============================================================

if __name__ == "__main__":
    seed_aliases = {
        "filmzone", "bazmoc", "sony_sci_fi", "ntv_serial", "mir_seriala",
        "sony_turbo", "vip_serial", "amc", "filmbox", "kinouzhas", "evrokino",
        "amedia_2", "dom_kino", "dom_kino_pr", "nashe_novoe", "mnogo_tv",
        "kinoklub", "illusion_pl", "flixsnip", "hd_life", "docubox",
        "curiosity_s", "ocean_tv", "history", "zoopark", "galaxy", "terra",
        "nicktoons", "ducktv", "karusel", "tiji", "nickelodeon", "gulli",
        "trace_sport", "match_plane", "kxl", "tnt_music", "mezzo",
        "rtr_planeta", "ntv_pravo", "mir", "rtvi", "ren_tv", "rbc",
        "euronews", "tnt_4", "kvn_tv", "nostalgia", "tv_3", "telecafe",
        "h1", "h2", "zee_tv", "shant", "kentron", "dar21", "atv",
        "erox", "playboy",
        "eda", "kluch", "trash_hd", "scream", "sumiko_hd",
    }

    superprobe(seed_aliases)