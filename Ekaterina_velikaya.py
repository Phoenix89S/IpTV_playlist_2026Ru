# ================================================================
#   CANON + SCALA NGENIX FINDER
#   Версия 3.9.0 — Высокопроизводительный валидатор HLS-потоков
# ================================================================

import time
import re
from typing import Dict, Tuple, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    requests = None


# -------------------------------
# Словари сопоставления (CHANNEL_META)
# -------------------------------

CHANNEL_META = {
    # ------------------------------------------------------------
    #  ФЕДЕРАЛЬНЫЕ
    # ------------------------------------------------------------
    "perviy": ("Первый канал", "Федеральные"),
    "rossiya_1": ("Россия 1", "Федеральные"),
    "match_tv": ("Матч ТВ", "Федеральные"),
    "ntv": ("НТВ", "Федеральные"),
    "pyatyi": ("Пятый канал", "Федеральные"),
    "rossiya_k": ("Россия К", "Федеральные"),
    "rossiya_24": ("Россия 24", "Федеральные"),
    "karusel": ("Карусель", "Федеральные"),
    "otr": ("ОТР", "Федеральные"),
    "tvc": ("ТВ Центр", "Федеральные"),
    "rentv": ("РЕН ТВ", "Федеральные"),
    "spas": ("Спас", "Федеральные"),
    "sts": ("СТС", "Федеральные"),
    "domashniy": ("Домашний", "Федеральные"),
    "tv3": ("ТВ-3", "Федеральные"),
    "pyatnica": ("Пятница!", "Федеральные"),
    "zvezda": ("Звезда", "Федеральные"),
    "mir": ("Мир", "Федеральные"),
    "tnt": ("ТНТ", "Федеральные"),
    "muz_tv": ("Муз-ТВ", "Федеральные"),
}


def generate_slug_candidates(key: str) -> List[str]:
    candidates = [key]
    custom_map = {
        "perviy": ["1tv", "ch_1tv", "pervy", "perviy_kanal"],
        "rossiya_1": ["ch_russia1", "rossiya1", "russia1", "russia_1"],
        "match_tv": ["ch_matchtv", "match", "matchtv"],
        "ntv": ["ch_ntv", "ntv_hd"],
        "pyatyi": ["ch_5tv", "5tv", "5kanal"],
        "rossiya_k": ["ch_russiak", "rossiya_kultura", "kultura"],
        "rossiya_24": ["ch_russia24", "rossiya24", "russia24"],
        "karusel": ["ch_karusel", "karusel_tv"],
        "otr": ["ch_otr", "otr_tv"],
        "tvc": ["ch_tvc", "tvcentr", "tv_center"],
        "rentv": ["ch_rentv", "ren", "ren_tv"],
        "spas": ["ch_spas", "spas_tv"],
        "sts": ["ch_sts", "ctc"],
        "domashniy": ["ch_domashniy", "domashny", "domashniy_2"],
        "tv3": ["ch_tv3", "tv_3"],
        "pyatnica": ["ch_friday", "friday", "pyatnitsa"],
        "zvezda": ["ch_zvezda", "zvezda_tv"],
        "mir": ["ch_mir", "mirtv"],
        "tnt": ["ch_tnt", "tnt_hd"],
        "muz_tv": ["ch_muztv", "muz", "muztv"]
    }
    
    if key in custom_map:
        candidates.extend(custom_map[key])
        
    return list(dict.fromkeys(candidates))


# -------------------------------
# Улучшенный валидатор HLS-потока
# -------------------------------

def probe_url(session: Any, url: str, timeout: float = 2.5, user_agent: str = "HlsWinkPlayer") -> Tuple[bool, int, float, str]:
    if session is None:
        return False, 0, 0.0, "requests_not_installed"
        
    headers = {"User-Agent": user_agent}
    start = time.time()
    
    try:
        # Использование контекстного менеджера с сессией
        with session.get(url, headers=headers, stream=True, timeout=timeout) as resp:
            latency = (time.time() - start) * 1000.0
            
            # Принимаем статус-коды 200 и 206
            if resp.status_code in (200, 206):
                # Читаем первые 512 байт для валидации заголовков HLS
                chunk = resp.raw.read(512).decode('utf-8', errors='ignore')
                if "#EXTM3U" in chunk or "#EXT-X-" in chunk:
                    return True, resp.status_code, latency, ""
                else:
                    return False, resp.status_code, latency, "not_a_valid_m3u8_payload"
            
            return False, resp.status_code, latency, f"HTTP {resp.status_code}"
            
    except Exception as e:
        latency = (time.time() - start) * 1000.0
        return False, 0, latency, str(e)


# -------------------------------
# Оптимизированный движок
# -------------------------------

def scan_ngenix_node(
    cdn_host: str = "s70378.cdn.ngenix.net", 
    meta_dict: Dict[str, Tuple[str, str]] = CHANNEL_META,
    start_index: int = 1,
    group_override: str = "Дополнительные Эфирные ТВ Плюс",
    timeout: float = 2.0,
    max_workers: int = 20
):
    print(f"=== [СКАЛА] Запуск профессионального валидатора: {cdn_host} ===")
    
    tasks = []
    path_templates = [
        "/{slug}/2/index.m3u8",
        "/{slug}/1/index.m3u8",
        "/hls/{slug}/variant.m3u8"
    ]

    for key, (title, group) in meta_dict.items():
        slugs = generate_slug_candidates(key)
        for slug in slugs:
            for path_tmpl in path_templates:
                url = f"https://{cdn_host}" + path_tmpl.format(slug=slug)
                tasks.append({
                    "key": key,
                    "title": title,
                    "group": group_override if group_override else group,
                    "slug": slug,
                    "url": url
                })

    found_channels = []
    found_keys = set()  # Множество O(1) для быстрого поиска дубликатов
    scanned_logs = []

    # Используемrequests.Session для повторного использования TCP-соединений
    session = requests.Session() if requests else None

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(probe_url, session, item["url"], timeout): item 
                for item in tasks
            }

            for future in as_completed(future_map):
                item = future_map[future]
                ok, status, latency, err = future.result()
                
                log_entry = {
                    "title": item["title"],
                    "key": item["key"],
                    "url": item["url"],
                    "ok": ok,
                    "status": status,
                    "latency": latency,
                    "error": err
                }
                scanned_logs.append(log_entry)

                if ok and item["key"] not in found_keys:
                    found_keys.add(item["key"])
                    found_channels.append(item)
                    print(f"[НАЙДЕН HLS] {item['title']} -> {item['url']} ({int(latency)} ms)")

    finally:
        if session:
            session.close()

    # Сортировка по порядку CHANNEL_META
    meta_keys = list(meta_dict.keys())
    found_channels.sort(key=lambda x: meta_keys.index(x["key"]))

    # 1. Запись отчета СКАЛА
    with open("ngenix_report.txt", "w", encoding="utf-8") as f:
        f.write("СКАЛА кант Вер 3.9.0 — NGENIX FINDER REPORT\n")
        f.write("=========================================\n")
        f.write(f"Проверено комбинаций URL: {len(tasks)}\n")
        f.write(f"Успешно найдено каналов: {len(found_channels)}\n")
        f.write("=========================================\n\n")

        for log in scanned_logs:
            tag = "OK" if log["ok"] else "FAIL"
            f.write(f"[СКАЛА] [{tag}] Канал: {log['title']} | Key: {log['key']}\n")
            f.write(f"        URL: {log['url']}\n")
            f.write(f"        Статус: {log['status']} | Latency: {int(log['latency'])} ms | Error: {log['error']}\n\n")

    # 2. Выгрузка M3U8
    with open("ngenix_found.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, ch in enumerate(found_channels, start=start_index):
            f.write(f'#EXTINF:-1 tvg-id="{ch["key"]}" group-title="{ch["group"]}",{i}. {ch["title"]}\n')
            f.write(f'{ch["url"]}\n')

    print("\n[СКАЛА] Поиск завершён успешно!")
    print(" — Отчёт: ngenix_report.txt")
    print(" — Сгенерирован рабочий M3U: ngenix_found.m3u")


if __name__ == "__main__":
    scan_ngenix_node(
        cdn_host="s70378.cdn.ngenix.net", 
        meta_dict=CHANNEL_META,
        start_index=1,
        group_override="Эфирные ТВ Плюс",
        timeout=2.5,
        max_workers=20
    )
