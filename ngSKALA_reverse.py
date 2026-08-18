#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import gzip
import io
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = 4
SEGMENT_CHECK_COUNT = 3

EPG_URL = "http://epg.one/epg2.xml.gz"
OUTPUT_REPORT = "ngSKALA_reverse.txt"

# CDN узлы, которые реально существуют
NGENIX_NODES = [
    "s70378",
    "s70379",
    "s70380",
    "s70381",
    "s70382",
    "s70383",
    "s70384",
    "s70385",
    "s70386",
    "s70387",
    "s70388",
    "s70389",
    "s70390",
]


# ============================================================
#  ЗАГРУЗКА EPG
# ============================================================

def fetch_epg_xml(url: str) -> ET.ElementTree:
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    gz = gzip.GzipFile(fileobj=io.BytesIO(r.content))
    data = gz.read()
    return ET.ElementTree(ET.fromstring(data))


def build_epg_list(tree: ET.ElementTree):
    """
    Возвращает список каналов:
    [
      {
        "id": "kluch_hd",
        "name": "Ключ HD",
        "logo": "http://...",
      },
      ...
    ]
    """
    root = tree.getroot()
    out = []

    for ch in root.findall("channel"):
        cid = ch.get("id", "").strip()
        display_name = None
        logo = None

        for e in ch:
            if e.tag == "display-name":
                if display_name is None:
                    display_name = (e.text or "").strip()
            if e.tag == "icon":
                logo = e.get("src", "").strip()

        if display_name:
            out.append({
                "id": cid,
                "name": display_name,
                "logo": logo
            })

    return out


# ============================================================
#  ГЕНЕРАЦИЯ CDN-ВАРИАНТОВ ИМЕНИ
# ============================================================

def generate_cdn_variants(epg_name: str, epg_id: str):
    """
    Создаёт список возможных CDN имён.
    """
    base = epg_id.lower().replace(" ", "").replace("-", "").replace("_", "")
    name = epg_name.lower()

    variants = set()

    # 1. ID как есть
    variants.add(epg_id)

    # 2. ID без "_hd"
    if epg_id.endswith("_hd"):
        variants.add(epg_id[:-3])

    # 3. ID без "_tv"
    if epg_id.endswith("_tv"):
        variants.add(epg_id[:-3])

    # 4. ID без "_plus"
    if epg_id.endswith("_plus"):
        variants.add(epg_id[:-5])

    # 5. ID без "_premium"
    if epg_id.endswith("_premium"):
        variants.add(epg_id[:-8])

    # 6. Имя без пробелов
    variants.add(base)

    # 7. Имя без HD
    if "hd" in base:
        variants.add(base.replace("hd", ""))

    # 8. Имя без цифр
    variants.add("".join([c for c in base if not c.isdigit()]))

    # 9. Имя из display-name
    name_clean = (
        name.replace(" ", "")
            .replace("!", "")
            .replace("-", "")
            .replace("_", "")
            .replace("канал", "")
            .replace("тв", "")
            .replace("hd", "")
            .replace("plus", "")
            .replace("premium", "")
    )
    variants.add(name_clean)

    # 10. Специальные федеральные варианты
    if "перв" in name:
        variants.update(["1tv", "pervyj", "perviy"])
    if "россия 1" in name or "россия1" in name:
        variants.update(["rossiya1", "russia1"])
    if "матч" in name:
        variants.update(["match", "matchtv", "match_tv"])
    if "нтв" in name:
        variants.update(["ntv", "ntv_hd"])
    if "твр" in name or "тц" in name or "тцв" in name or "твц" in name:
        variants.update(["tvc", "tvc_hd"])

    return list(variants)


# ============================================================
#  ПРОВЕРКА ПОТОКОВ
# ============================================================

def check_m3u8(url):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return False
        if "EXTM3U" not in r.text:
            return False
        return True
    except:
        return False


def scan_variant_on_node(node: str, variant: str):
    """
    Проверяет все шаблоны NGENIX для данного варианта.
    """
    base = f"https://{node}.cdn.ngenix.net/"
    found = []

    patterns = [
        "{v}/index.m3u8",
        "{v}/1/index.m3u8",
        "{v}/2/index.m3u8",
        "{v}/3/index.m3u8",
        "{v}/hd/index.m3u8",
        "{v}/sd/index.m3u8",
        "hls/CH_{v}/variant.m3u8",
        "{v}/tracks-v1a1/mono.m3u8",
    ]

    for p in patterns:
        url = base + p.format(v=variant)
        if check_m3u8(url):
            found.append(url)

    return found


def scan_channel_reverse(epg_name: str, epg_id: str):
    """
    Проверяет канал на всех узлах NGENIX.
    """
    variants = generate_cdn_variants(epg_name, epg_id)
    results = []

    for node in NGENIX_NODES:
        for variant in variants:
            streams = scan_variant_on_node(node, variant)
            if streams:
                results.append({
                    "node": node,
                    "variant": variant,
                    "streams": streams
                })

    return results


# ============================================================
#  ГЕНЕРАЦИЯ ОТЧЁТА
# ============================================================

def write_report(epg_channels, results_map):
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write("=== NGENIX CDN СКАЛА/ДРЭГ — ОБРАТНЫЙ РЕФАКТОРИНГ ===\n")
        f.write("РЕЖИМ: АЭС / ПОЛНЫЙ ПОИСК КАНАЛОВ ПО EPG\n")
        f.write("------------------------------------------------------------\n\n")

        for ch in epg_channels:
            name = ch["name"]
            cid = ch["id"]

            f.write(f"[КАНАЛ] {name}\n")
            f.write(f"[EPG-ID] {cid}\n")

            if cid not in results_map or not results_map[cid]:
                f.write("  СТАТУС: рабочие потоки не обнаружены\n")
                f.write("------------------------------------------------------------\n\n")
                continue

            for item in results_map[cid]:
                f.write(f"  [УЗЕЛ] {item['node']}.cdn.ngenix.net\n")
                f.write(f"  [ВАРИАНТ] {item['variant']}\n")
                f.write("  [ПОТОКИ]\n")
                for s in item["streams"]:
                    f.write(f"    -> {s}\n")
                f.write("\n")

            f.write("------------------------------------------------------------\n\n")


# ============================================================
#  MAIN
# ============================================================

def main():
    print("Загрузка EPG...")
    epg_tree = fetch_epg_xml(EPG_URL)
    epg_channels = build_epg_list(epg_tree)

    results_map = {}

    print("Запуск обратного рефакторинга...")
    for ch in epg_channels:
        cid = ch["id"]
        name = ch["name"]

        print(f"Проверка: {name} ({cid})")
        results = scan_channel_reverse(name, cid)
        results_map[cid] = results

    print("Формирование отчёта...")
    write_report(epg_channels, results_map)

    print("Готово: ngSKALA_reverse.txt")


if __name__ == "__main__":
    main()