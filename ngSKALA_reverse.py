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

# Глобальная сессия для Turbo-режима
SESSION = requests.Session()


# ============================================================
#  ЗАГРУЗКА EPG
# ============================================================

def fetch_epg_xml(url: str) -> ET.ElementTree:
    r = SESSION.get(url, timeout=20)
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
#  ГЕНЕРАЦИЯ CDN-ВАРИАНТОВ ИМЕНИ (TURBO)
# ============================================================

def generate_cdn_variants(epg_name: str, epg_id: str):
    """
    Создаёт список возможных CDN имён.
    Turbo-режим: максимально широкий пул имён для всех каналов из EPG.
    """
    base = epg_id.lower().replace(" ", "").replace("-", "").replace("_", "")
    name = epg_name.lower()

    variants = set()

    # 1. ID как есть
    variants.add(epg_id)
    variants.add(epg_id.lower())

    # 2. ID без "_hd"
    if epg_id.endswith("_hd"):
        variants.add(epg_id[:-3])
        variants.add(epg_id[:-3].lower())

    # 3. ID без "_tv"
    if epg_id.endswith("_tv"):
        variants.add(epg_id[:-3])
        variants.add(epg_id[:-3].lower())

    # 4. ID без "_plus"
    if epg_id.endswith("_plus"):
        variants.add(epg_id[:-5])
        variants.add(epg_id[:-5].lower())

    # 5. ID без "_premium"
    if epg_id.endswith("_premium"):
        variants.add(epg_id[:-8])
        variants.add(epg_id[:-8].lower())

    # 6. Имя без пробелов
    variants.add(base)

    # 7. Имя без HD
    if "hd" in base:
        variants.add(base.replace("hd", ""))

    # 8. Имя без цифр
    variants.add("".join([c for c in base if not c.isdigit()]))

    # 9. Имя из display-name (очищенное)
    name_clean = (
        name.replace(" ", "")
            .replace("!", "")
            .replace("-", "")
            .replace("_", "")
            .replace(".", "")
            .replace(",", "")
            .replace("канал", "")
            .replace("тв", "")
            .replace("hd", "")
            .replace("plus", "")
            .replace("premium", "")
    )
    variants.add(name_clean)

    # 10. Имя с подчёркиванием (как в CDN)
    name_underscore = (
        name.lower()
            .replace(" ", "_")
            .replace(".", "")
            .replace(",", "")
            .replace("!", "")
            .replace("-", "_")
    )
    variants.add(name_underscore)

    # 11. Имя без цифр + подчёркивание
    name_underscore_no_digits = "".join(
        [c for c in name_underscore if not c.isdigit()]
    )
    variants.add(name_underscore_no_digits)

    # 12. Имя без пробелов + дефис
    name_dash = (
        name.lower()
            .replace(" ", "-")
            .replace(".", "")
            .replace(",", "")
            .replace("!", "")
    )
    variants.add(name_dash)

    # 13. Имя без спецсимволов (только буквы и цифры)
    name_alnum = "".join([c for c in name if c.isalnum()])
    variants.add(name_alnum)

    # 14. Транслитерация (универсальная)
    translit_map = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
        "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
        "й": "j", "к": "k", "л": "l", "м": "m", "н": "n",
        "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
        "ш": "sh", "щ": "sch", "ы": "y", "э": "e", "ю": "yu",
        "я": "ya"
    }

    translit = ""
    for c in name:
        translit += translit_map.get(c, c)

    translit = translit.replace(" ", "_").replace(".", "").replace(",", "")
    variants.add(translit)
    variants.add(translit.replace("_", ""))
    variants.add(translit.replace("_", "-"))

    # 15. Имя без пробелов + подчёркивание (латиница)
    translit_underscore = translit.replace(" ", "_")
    variants.add(translit_underscore)

    # 16. Имя без пробелов + дефис (латиница)
    translit_dash = translit.replace(" ", "-")
    variants.add(translit_dash)

    # 17. Имя без пробелов (латиница)
    translit_nospace = translit.replace(" ", "")
    variants.add(translit_nospace)

    # 18. Линейка Viju+ → vip_
    if "viju+" in name or "viju +" in name or "viju plus" in name:
        core = (
            name.replace("viju+", "")
                .replace("viju +", "")
                .replace("viju plus", "")
                .replace(" ", "")
                .replace("hd", "")
                .replace("tv", "")
                .replace("канал", "")
                .replace("!", "")
                .replace(",", "")
                .replace(".", "")
        )
        variants.add("vip_" + core)
        variants.add("vip" + core)
        variants.add("vip-" + core)

    # 19. Линейка Viju → viju_
    if "viju" in name:
        core = (
            name.replace("viju", "")
                .replace(" ", "")
                .replace("hd", "")
                .replace("tv", "")
                .replace("канал", "")
                .replace("!", "")
                .replace(",", "")
                .replace(".", "")
        )
        variants.add("viju_" + core)
        variants.add("viju" + core)
        variants.add("viju-" + core)

    # 20. Линейка TV1000 → viju_tv1000
    if "tv1000" in name or "tv 1000" in name:
        variants.update([
            "viju_tv1000",
            "viju_tv1000_rus",
            "viju_tv1000_action",
            "viju_tv1000_romantica",
            "viju_tv1000_novella"
        ])

    # 21. Линейка Viasat → viasat_
    if "viasat" in name or "виасат" in name:
        core = (
            name.replace("viasat", "")
                .replace("виасат", "")
                .replace(" ", "")
                .replace("hd", "")
                .replace("tv", "")
                .replace("канал", "")
                .replace("!", "")
                .replace(",", "")
                .replace(".", "")
        )
        variants.add("viasat_" + core)
        variants.add("viasat" + core)
        variants.add("viasat-" + core)

    # 22. Da Vinci
    if "da vinci" in name or "давинчи" in name:
        variants.add("da_vinci")
        variants.add("davinci")

    # 23. Sony
    if "sony" in name:
        variants.add("sony_" + name_clean)
        variants.add("sony_" + name_underscore)
        variants.add("sony" + name_clean)
        variants.add("sony" + name_underscore)

    # 24. Amedia
    if "amedia" in name:
        variants.add("amedia_" + name_clean)
        variants.add("amedia_" + name_underscore)
        variants.add("amedia" + name_clean)
        variants.add("amedia" + name_underscore)

    # 25. Paramount
    if "paramount" in name:
        variants.add("paramount_" + name_clean)
        variants.add("paramount" + name_clean)

    # 26. Universal
    if "universal" in name:
        variants.add("universal_" + name_clean)
        variants.add("universal" + name_clean)

    # 27. Федеральные каналы
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

    # 28. Дополнительные универсальные варианты
    variants.add(name.replace(" ", "_"))
    variants.add(name.replace(" ", "-"))
    variants.add(name.replace(" ", ""))

    return list(variants)


# ============================================================
#  ПРОВЕРКА ПОТОКОВ (TURBO)
# ============================================================

def check_m3u8(url):
    try:
        r = SESSION.get(url, timeout=TIMEOUT)
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
    Turbo-режим: расширенный пул путей.
    """
    base = f"https://{node}.cdn.ngenix.net/"
    found = []

    patterns = [
        "{v}/index.m3u8",
        "{v}/1/index.m3u8",
        "{v}/2/index.m3u8",
        "{v}/3/index.m3u8",
        "{v}/4/index.m3u8",
        "{v}/5/index.m3u8",

        "{v}/hd/index.m3u8",
        "{v}/sd/index.m3u8",

        "{v}/tracks-v1a1/mono.m3u8",
        "{v}/tracks-v2a1/mono.m3u8",
        "{v}/tracks-v3a1/mono.m3u8",

        "hls/CH_{v}/variant.m3u8",
        "hls/CH_{v}_HD/variant.m3u8",
        "hls/CH_{v}_SD/variant.m3u8",

        "hls/{v}/variant.m3u8",
        "hls/{v}_hd/variant.m3u8",
        "hls/{v}_sd/variant.m3u8",

        "{v}/mono.m3u8",
        "{v}/live.m3u8",
        "{v}/playlist.m3u8",
    ]

    for p in patterns:
        url = base + p.format(v=variant)
        if check_m3u8(url):
            found.append(url)

    return found


def scan_channel_reverse(epg_name: str, epg_id: str):
    """
    Проверяет канал на всех узлах NGENIX.
    Проверка node × variant выполняется параллельно.
    Turbo-режим: многопоточный опрос по всем сгенерированным именам.
    """
    variants = generate_cdn_variants(epg_name, epg_id)
    results = []

    def scan_single(node, variant):
        streams = scan_variant_on_node(node, variant)

        if streams:
            return {
                "node": node,
                "variant": variant,
                "streams": streams
            }

        return None

    tasks = []

    with ThreadPoolExecutor(max_workers=50) as ex:
        for node in NGENIX_NODES:
            for variant in variants:
                tasks.append(
                    ex.submit(
                        scan_single,
                        node,
                        variant
                    )
                )

        for t in tasks:
            r = t.result()

            if r:
                results.append(r)

    return results


# ============================================================
#  ГЕНЕРАЦИЯ ОТЧЁТА
# ============================================================

def write_report(epg_channels, results_map):
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write("=== NGENIX CDN СКАЛА/ДРЭГ — ОБРАТНЫЙ РЕФАКТОРИНГ (TURBO) ===\n")
        f.write("РЕЖИМ: АЭС / ПОЛНЫЙ ПОИСК КАНАЛОВ ПО EPG / TURBO\n")
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

    print(f"Загружено каналов: {len(epg_channels)}")

    results_map = {}

    print("Запуск обратного рефакторинга (TURBO)...")

    def scan_epg_channel(ch):
        cid = ch["id"]
        name = ch["name"]

        print(f"Проверка: {name} ({cid})")

        results = scan_channel_reverse(name, cid)

        return cid, results

    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = [
            ex.submit(
                scan_epg_channel,
                ch
            )
            for ch in epg_channels
        ]

        for f in futures:
            cid, results = f.result()
            results_map[cid] = results

    print("Формирование отчёта...")
    write_report(epg_channels, results_map)

    print("Готово: ngSKALA_reverse.txt")


if __name__ == "__main__":
    main()