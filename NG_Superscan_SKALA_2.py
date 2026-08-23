import os
import re
import json
import time
import requests
from datetime import datetime
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
#   НАСТРОЙКИ
# ============================================================

TIMEOUT = 4
SEGMENT_CHECK_COUNT = 3
MAX_WORKERS = 40
MAX_PASSES = 5

INVENTORY_FILE = "cdn_alias_inventory.json"

NODES = ["s70378", "s70379", "s70380", "s70381", "s70382"]

PATTERNS = [
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
#   ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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

def extract_segments(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ".ts" in line or ".m4s" in line:
            out.append(line)
    return out

def check_segment(base, seg):
    url = urljoin(base, seg)
    try:
        start = time.time()
        r = requests.get(url, timeout=TIMEOUT, stream=True)
        if r.status_code == 200:
            return True, time.time() - start
        return False, None
    except:
        return False, None

def deep_probe(url):
    ok, text, code = check_m3u8(url)
    if not ok:
        return "FAIL_M3U8", None, code

    segs = extract_segments(text)
    if not segs:
        return "FAIL_SEGMENTS", None, code

    speeds = []
    for s in segs[:SEGMENT_CHECK_COUNT]:
        ok_s, sp = check_segment(url, s)
        if ok_s and sp is not None:
            speeds.append(sp)

    if len(speeds) == SEGMENT_CHECK_COUNT:
        return "OK", sum(speeds) / len(speeds), code
    if len(speeds) > 0:
        return "PARTIAL", None, code
    return "FAIL_SEGMENTS", None, code

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
#   СКАНИРОВАНИЕ ПОТОКОВ
# ============================================================

def scan_alias_on_node(alias, node):
    base = f"https://{node}.cdn.ngenix.net/"
    out = []

    for p in PATTERNS:
        url = base + p.format(alias=alias)
        status, speed, code = deep_probe(url)

        if status in ("OK", "PARTIAL"):
            alt = f"https://cdn.ngenix.net/{alias}/index.m3u8"
            alt_status, alt_speed, alt_code = deep_probe(alt)

            node_ok, node_speed, node_code = probe_node(node)

            out.append({
                "node": node,
                "url": url,
                "status": status,
                "speed": speed,
                "http_code": code,
                "alt_url": alt,
                "alt_status": alt_status,
                "alt_speed": alt_speed,
                "alt_code": alt_code,
                "node_status": node_ok,
                "node_speed": node_speed,
                "node_code": node_code,
            })

    return out

# ============================================================
#   ПОЛНЫЙ ОПРОС УЗЛА
# ============================================================

def node_full_discovery(node):
    base = f"https://{node}.cdn.ngenix.net/"
    try:
        r = requests.get(base, timeout=TIMEOUT)
        if r.status_code not in (200, 403, 404):
            return {}
    except:
        return {}

    html = r.text
    aliases = set(re.findall(r'href="([^"/]+)/"', html))

    out = {}
    for a in aliases:
        res = scan_alias_on_node(a, node)
        if res:
            out[a] = res

    return out

# ============================================================
#   DISCOVERY PASS
# ============================================================

def discovery_pass(inv, nodes, seeds):
    alive = []
    for n in nodes:
        ok, _, _ = probe_node(n)
        if ok:
            alive.append(n)

    if not alive:
        return {}, set()

    new_entries = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = []
        for alias in seeds:
            for node in alive:
                futs.append(ex.submit(scan_alias_on_node, alias, node))

        for f in as_completed(futs):
            res = f.result()
            for item in res:
                alias = item["url"].split(".cdn.ngenix.net/")[1].split("/")[0]
                new_entries.setdefault(alias, []).append(item)

    new_aliases = set(new_entries.keys()) - set(inv.keys())
    return new_entries, new_aliases

# ============================================================
#   ИНДЕКСАЦИЯ
# ============================================================

def get_last_index(prefix):
    nums = []
    for fn in os.listdir("."):
        if fn.startswith(prefix + "_"):
            try:
                n = int(fn.split("_")[-1].split(".")[0])
                nums.append(n)
            except:
                pass
    return max(nums) if nums else 0

def read_last_log(prefix):
    idx = get_last_index(prefix)
    if idx == 0:
        return None
    fname = f"{prefix}_{idx}.txt"
    try:
        with open(fname, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return None

# ============================================================
#   ЛОГИ
# ============================================================

def generate_human_log(inv):
    out = []
    out.append("=== NGENIX CDN SUPER-SCAN / SKALA / ДРЕГ ===")
    out.append("РЕЖИМ: ПОЛНАЯ ТЕЛЕМЕТРИЯ")
    out.append("------------------------------------------------------------\n")

    for alias, items in inv.items():
        out.append(f"[КАНАЛ] {alias}")
        for it in items:
            out.append(f"  [УЗЕЛ] {it['node']}.cdn.ngenix.net")
            out.append(f"  [NODE] STATUS={it['node_status']} time={it['node_speed']}\n")
            out.append(f"  [ОРИГИНАЛ] {it['url']}")
            out.append(f"             STATUS={it['status']} speed={it['speed']}\n")
            out.append(f"  [АЛЬТЕРНАТИВА] {it['alt_url']}")
            out.append(f"                 STATUS={it['alt_status']} speed={it['alt_speed']}\n")
            out.append("------------------------------------------------------------\n")

    return "\n".join(out)

def write_human(inv, fname):
    with open(fname, "w", encoding="utf-8") as f:
        f.write(generate_human_log(inv))

def write_machine(inv, fname):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(fname, "w", encoding="utf-8") as f:
        for alias, items in inv.items():
            best = sorted(items, key=lambda x: (x["status"] != "OK", x["status"] != "PARTIAL"))[0]
            f.write(f"NAME={alias}\n")
            f.write(f"ALIAS={alias}\n")
            f.write(f"URL={best['url']}\n")
            f.write(f"STATUS={best['http_code']}\n")
            f.write(f"SOURCE=ALIAS_MODULE\n")
            f.write(f"FOUND={now}\n\n")

def write_m3u(inv, fname):
    with open(fname, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for alias, items in inv.items():
            best = sorted(items, key=lambda x: (x["status"] != "OK", x["status"] != "PARTIAL"))[0]
            f.write(f'#EXTINF:-1 tvg-id="{alias}" group-title="NGENIX SuperScan",{alias}\n')
            f.write(best["url"] + "\n\n")

# ============================================================
#   SUPERPROBE
# ============================================================

def superprobe(seed_aliases):
    inv = load_inventory()
    print(f"Загружено alias из inventory: {len(inv)}")

    # PASS discovery
    for p in range(1, MAX_PASSES + 1):
        print(f"\n=== PASS {p} ===")
        new_entries, new_aliases = discovery_pass(inv, NODES, seed_aliases)
        print(f"Найдено новых alias: {len(new_aliases)}")

        if not new_aliases:
            print("Новых alias нет — остановка.")
            break

        for a, items in new_entries.items():
            inv.setdefault(a, []).extend(items)

        save_inventory(inv)

    # FULL NODE DISCOVERY
    print("\n=== ПОЛНЫЙ ОПРОС УЗЛОВ ===")
    for node in NODES:
        print(f"Опрос узла: {node}")
        res = node_full_discovery(node)
        for a, items in res.items():
            inv.setdefault(a, []).extend(items)

    save_inventory(inv)

    # Генерация логов
    new_log = generate_human_log(inv)
    old_log = read_last_log("ngSuperscan_SKALA")

    if old_log is not None and old_log == new_log:
        print("\nИзменений нет — новый набор файлов не создаётся.")
        return inv

    idx = get_last_index("ngSuperscan_SKALA") + 1

    write_human(inv, f"ngSuperscan_SKALA_{idx}.txt")
    write_machine(inv, f"ngSuperscan_SKALA_machine_{idx}.txt")
    write_m3u(inv, f"ngSuperscan_SKALA_{idx}.m3u")

    print(f"\nСоздан новый набор файлов с индексом {idx}")

    return inv

# ============================================================
#   ЗАПУСК
# ============================================================

if __name__ == "__main__":
    seed_aliases = {
        "filmzone", "bazmoc", "sony_sci_fi", "ntv_serial", "mir_seriala",
        "sony_turbo", "vip_serial", "amc", "filmbox", "kinouzhas",
        "evrokino", "amedia_2", "dom_kino", "dom_kino_pr", "nashe_novoe",
        "mnogo_tv", "kinoklub", "illusion_pl", "flixsnip", "hd_life",
        "docubox", "curiosity_s", "ocean_tv", "history", "zoopark",
        "galaxy", "terra", "nicktoons", "ducktv", "karusel", "tiji",
        "nickelodeon", "gulli", "trace_sport", "match_plane", "kxl",
        "tnt_music", "mezzo", "rtr_planeta"
    }

    superprobe(seed_aliases)