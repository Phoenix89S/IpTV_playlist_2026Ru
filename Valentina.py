# ====================================================================
# Valentina.py
# Универсальный сканер CDN NGENIX + Тотальный Брутфорс-генератор
# Режим: СКАЛА.3 FULL SIMULATION + Расширенная диагностика CDN
# Автор: Phoenix + Copilot + Gemini
# Стиль отчёта: СКАЛА (телетайп)
# ====================================================================

import requests
import re
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import os
import urllib3
import random

USER_AGENT = "HlsWinkPlayer"
BASE = "https://s70378.cdn.ngenix.net"
EPG_URL = "https://epg.one/epg2.xml.gz"
LOCAL_EPG = "epg2.xml.gz"

# Диапазон узлов NGENIX для перебора
NODES = [f"s{n}" for n in range(10000, 80000, 1000)]  # шаг 1000

# Подпапки качества — расширенный набор
SUBDIRS = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8",
    "720", "1080", "adaptive", "live", "hls"
]

# Фиксируем МСК (UTC+3) и Калининград (UTC+2)
MSK = timezone(timedelta(hours=3))
KLG = timezone(timedelta(hours=2))

# Подавляем варнинги SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Глобальная сессия с пулом соединений
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
session.mount('https://', adapter)
session.mount('http://', adapter)

# Глобальная статистика HTTP
HTTP_STATS = {
    "total": 0,
    "200": 0,
    "302": 0,
    "403": 0,
    "404": 0,
    "500": 0,
    "other": 0
}

# ====================================================================
# Базовые функции EPG / HLS
# ====================================================================

def log_http(url, response):
    """Логирование HTTP-ответа: URL, статус, первые символы."""
    code = response.status_code
    HTTP_STATS["total"] += 1
    if str(code) in HTTP_STATS:
        HTTP_STATS[str(code)] += 1
    else:
        HTTP_STATS["other"] += 1

    head = response.text[:160].replace("\n", " ").replace("\r", " ")
    print(f"[HTTP] {url} -> {code} :: {head}")

def download_epg(url=EPG_URL, local_file=LOCAL_EPG):
    print("[*] Скачивание EPG словаря...")
    try:
        r = session.get(url, timeout=(5, 20), verify=False, allow_redirects=True)
        r.raise_for_status()
        with open(local_file, "wb") as f:
            f.write(r.content)
        print(f"[+] EPG сохранён локально: {local_file}")
        return local_file
    except Exception as e:
        print(f"[-] Ошибка загрузки EPG: {e}")
        if os.path.exists(local_file):
            print("[*] Использую локальную копию EPG")
            return local_file
        else:
            raise RuntimeError("Нет доступа к EPG и локальной копии")

def load_channels_from_epg(local_file=LOCAL_EPG):
    print("[*] Декомпрессия и парсинг EPG...")
    with gzip.open(local_file, "rb") as f:
        data = f.read()
    root = ET.fromstring(data)
    channels = []
    for ch in root.findall("channel"):
        cid = ch.get("id")
        names = [dn.text.strip() for dn in ch.findall("display-name") if dn.text]
        if cid and names:
            channels.append({"id": cid, "names": names})
    print(f"[+] Каналов в EPG: {len(channels)}")
    return channels

def fetch_playlist(path, subdir="2"):
    safe_path = quote(path)
    url = f"{BASE}/{safe_path}/{subdir}/index.m3u8"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = session.get(url, headers=headers, timeout=(5, 15), verify=False, allow_redirects=True)
        log_http(url, r)
        if r.status_code == 200:
            text = r.text
            if "#EXT" in text:
                return f"{path}/{subdir}", text, datetime.now(MSK)
            else:
                return f"{path}/{subdir}", None, datetime.now(MSK)
        else:
            return f"{path}/{subdir}", None, datetime.now(MSK)
    except Exception as e:
        print(f"[ERR] {url} :: {e}")
        return f"{path}/{subdir}", None, datetime.now(MSK)

def fetch_playlist_node(node, path, subdir="2"):
    safe_path = quote(path)
    url = f"https://{node}.cdn.ngenix.net/{safe_path}/{subdir}/index.m3u8"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = session.get(url, headers=headers, timeout=(5, 15), verify=False, allow_redirects=True)
        log_http(url, r)
        if r.status_code == 200:
            text = r.text
            if "#EXT" in text:
                return node, f"{path}/{subdir}", text, datetime.now(MSK)
            else:
                return node, f"{path}/{subdir}", None, datetime.now(MSK)
        else:
            return node, f"{path}/{subdir}", None, datetime.now(MSK)
    except Exception as e:
        print(f"[ERR] {url} :: {e}")
        return node, f"{path}/{subdir}", None, datetime.now(MSK)

def parse_hls_features(playlist_text):
    if not playlist_text:
        return []
    resolutions = re.findall(r'RESOLUTION=(\d+x\d+)', playlist_text)
    if resolutions:
        return [f"{res}" for res in sorted(set(resolutions), reverse=True)]
    if "#EXTINF:" in playlist_text:
        return ["Media Stream"]
    return ["M3U8 OK"]

# ====================================================================
# Внутренний сканер каналов (для реальных данных)
# ====================================================================

def scan_all(channels):
    results = {}
    unique_paths = set()
    for ch in channels:
        for name in ch["names"]:
            cleaned_path = name.strip().lower()
            if cleaned_path:
                unique_paths.add(cleaned_path)
    print("----- начало процесса -----")
    print(f"[*] Запущен внутренний перебор каналов и подпапок... ({len(unique_paths)} уникальных направлений)")
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = []
        for path in unique_paths:
            for subdir in SUBDIRS:
                futures.append(executor.submit(fetch_playlist, path, subdir))
        for future in concurrent.futures.as_completed(futures):
            path, text, ts = future.result()
            features = parse_hls_features(text)
            is_alive = bool(text)
            results[path] = {"features": features, "alive": is_alive, "timestamp": ts}
    print("[*] Внутренний перебор каналов завершён.")
    print("----")
    return results

# ====================================================================
# Перебор узлов CDN (реальное использование NODES)
# ====================================================================

def scan_nodes(channels):
    results = {}
    live_count = 0
    node_count = 0
    print(f"[*] Запущен перебор узлов CDN ({len(NODES)} шт.)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        for node in NODES:
            node_count += 1
            print(f"[*] Проверка узла {node}.cdn.ngenix.net начата")
            futures = []
            for ch in channels:
                for name in ch["names"]:
                    cleaned_path = name.strip().lower()
                    if cleaned_path:
                        for subdir in SUBDIRS:
                            futures.append(executor.submit(fetch_playlist_node, node, cleaned_path, subdir))
            node_results = {}
            for f in concurrent.futures.as_completed(futures):
                node_name, path, text, ts = f.result()
                features = parse_hls_features(text)
                is_alive = bool(text)
                if is_alive:
                    live_count += 1
                node_results[path] = {"features": features, "alive": is_alive, "timestamp": ts}
            results[node] = node_results
            print(f"[*] Проверка узла {node}.cdn.ngenix.net завершена")
            print("====")
    print("[*] Перебор узлов завершён.")
    print(f"[*] Проверено узлов: {node_count}")
    print(f"[*] Найдено живых потоков (по всем узлам): {live_count}")
    return results

# ====================================================================
# Телетайп-отчёт (СКАЛА)
# ====================================================================

def write_skala_report(results, filename="NgenixScan_report.txt"):
    print(f"[*] Запись телетайп-отчёта: {filename} ...")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("СКАЛА-ТЕЛЕТАЙП ОТЧЁТ CDN NGENIX\n")
        f.write(f"Дата генерации: {datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')} (МСК)\n")
        f.write("="*60 + "\n")
        live_lines = 0
        dead_lines = 0
        for ch, meta in sorted(results.items()):
            ts = meta.get("timestamp", datetime.now(MSK))
            if meta["alive"]:
                info = ", ".join(meta["features"]) if meta["features"] else "Доступен"
                f.write(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} [LIVE] {ch} :: {info}\n")
                live_lines += 1
            else:
                f.write(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} [DEAD] {ch}\n")
                dead_lines += 1
        f.write("="*60 + "\n")
        f.write(f"ИТОГО: LIVE={live_lines}, DEAD={dead_lines}\n")
        f.write("КОНЕЦ ОТЧЁТА\n")
    print(f"[+] Телетайп-отчёт готов: LIVE={live_lines}, DEAD={dead_lines}")

# ====================================================================
# LIVE-плейлист (проверенные каналы)
# ====================================================================

def write_m3u(results, filename="NgenixScan.m3u"):
    print(f"[*] Запись LIVE-плейлиста: {filename} ...")
    lines = ["#EXTM3U"]
    live_count = 0
    for ch_key, meta in sorted(results.items()):
        if meta["alive"]:
            live_count += 1
            if '/' in ch_key:
                parts = ch_key.split('/')
                subdir = parts[-1]
                path = "/".join(parts[:-1])
            else:
                path = ch_key
                subdir = "2"
            display_name = f"{path.upper()} [Quality {subdir}]"
            features_str = f" [{', '.join(meta['features'])}]" if meta['features'] else ""
            lines.append(f'#EXTINF:-1 http-user-agent="{USER_AGENT}",{display_name}{features_str}')
            lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
            lines.append(f"{BASE}/{quote(path)}/{subdir}/index.m3u8")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[+] LIVE-плейлист готов, каналов: {live_count}")

# ====================================================================
# Тотальный брутфорс-список (сырой плейлист)
# ====================================================================

def write_full_bruteforce_m3u(channels, filename="Ngenix_Full_Bruteforce.m3u"):
    lines = ["#EXTM3U", "# СКАЛА.3 — Полный брутфорс-список для внешнего сканирования"]
    unique_paths = set()
    for ch in channels:
        for name in ch["names"]:
            cleaned_path = name.strip().lower()
            if cleaned_path:
                unique_paths.add(cleaned_path)
    print(f"[*] Сборка тотального плейлиста подстановок: {len(unique_paths)} каналов x {len(SUBDIRS)} папок качества...")
    for path in sorted(unique_paths):
        for subdir in SUBDIRS:
            pretty_name = f"{path.upper()} [БРУТФОРС Q:{subdir}]"
            lines.append(f'#EXTINF:-1 http-user-agent="{USER_AGENT}" tvg-id="{path}", {pretty_name}')
            lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
            lines.append(f"{BASE}/{quote(path)}/{subdir}/index.m3u8")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[+] Тотальный брутфорс-плейлист готов: {filename}")

# ====================================================================
# СКАЛА.3 FULL SIMULATION — эмуляция ленты ДРЭГ
# ====================================================================

SIM_EVENTS = [
    ("118Я", "*K07L053=1", "СТОПОРНЫЕ КЛАПАНЫ ЗАКРЫТЫ"),
    ("121Я", "*K07L022=0", "СНЯТИЕ СИГНАЛА ПО НЕИСПРАВНОСТИ АР"),
    ("128Я", "*K10L064=0", "СНИЖ. ДАВЛ. ВОДЫ ВПРС-6 В КНД1"),
    ("134Я", "*K06L051=1", "АЗ-5 СУЗ"),
    ("134Д", "*K06L042=1", "СТЕРЖНИ СОШЛИ С ВК"),
    ("134Д", "*K06L151=1", "РЕГУЛЯТОР П2-1332 ПОДКЛЮЧЕН ПРИ АЗ"),
    ("135Я", "*K06L017=1", "АЗСР (СНИЖ. ПЕРИОДА В ОСНОВН. ДИАПАЗОНЕ)"),
    ("135Я", "*K06L051=1", "АЗМ (ПРЕВ. МОЩН. В ОСНОВН. ДИАПАЗОНЕ)"),
    ("135Я", "*K06L052=1", "ПРЕВЫШЕНИЕ N АВАРИЙНЫЙ В ЗУЗМ-1"),
    ("136Я", "*K06L201=1", "АВАРИЙНОЕ ОТКЛОНЕНИЕ УРОВНЯ В ВС"),
    ("136Я", "*K06L176=1", "ПРЕВЫШЕНИЕ ДАВЛЕНИЯ В ВС ПРАВ."),
    ("136Я", "*K06L177=1", "ПРЕВЫШЕНИЕ ДАВЛЕНИЯ В ВС ЛЕВ."),
    ("137Я", "*K10L045=0", "РАЗГРУЗКА ТГ ПРИ АЗ-5"),
    ("137Я", "*K06L005=1", "СРАБАТЫВАНИЕ ВРУ-К1"),
    ("138Я", "*K06L034=1", "РОСТ ДАВЛЕНИЯ В РП1"),
    ("138Я", "*K06L034=1", "НЕТ НАПРЯЖЕНИЯ 48В 1СШ"),
]

def simulate_skala3_full():
    print("==== СКАЛА.3 FULL SIMULATION — ЭМУЛЯЦИЯ ЛЕНТЫ ДРЭГ ====")
    base_time = datetime.now(MSK).replace(microsecond=0)
    for idx, (cycle_code, kcode, desc) in enumerate(SIM_EVENTS):
        t = base_time + timedelta(seconds=idx * 2)
        time_str = t.strftime("%H.%M.%S")
        interval = f"({10+idx}-{11+idx})"
        grp11 = f"{random.uniform(4.0, 8.0):.2f}"
        grp12 = f"{random.uniform(4.0, 8.0):.2f}"
        grp13 = f"{random.uniform(4.0, 8.0):.2f}"
        grp14 = f"{random.uniform(4.0, 8.0):.2f}"
        print(f"{cycle_code}  {time_str} {interval} {kcode} {desc}")
        if idx % 4 == 0:
            print(f"{cycle_code}А  {time_str}  {grp11}  {grp12}  {grp13}  {grp14}")
    print("==== КОНЕЦ ЭМУЛЯЦИИ ЛЕНТЫ ДРЭГ ====")

# ====================================================================
# MAIN
# ====================================================================

if __name__ == "__main__":
    print("#==== СКАЛА.3. IPTV edition ===")
    print(f"Дата (МСК): {datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Дата (КЛГ): {datetime.now(KLG).strftime('%Y-%m-%d %H:%M:%S')}")
    print("Машина: Python script Valentina.py")
    print("Цель: Поиск и мониторинг потоков каналов IPTV + Эмуляция ленты ДРЭГ")
    print("#" + "="*30)

    try:
        epg_file = download_epg()
        channels = load_channels_from_epg(epg_file)

        write_full_bruteforce_m3u(channels)
        scan_results = scan_all(channels)
        write_skala_report(scan_results)
        write_m3u(scan_results)

        nodes_results = scan_nodes(channels)
        for node, node_data in nodes_results.items():
            write_skala_report(node_data, filename=f"NgenixScan_report_{node}.txt")

        print("#" + "="*30)
        print("[*] HTTP-статистика:")
        print(f"[*] Проверено запросов: {HTTP_STATS['total']}")
        print(f"[*] 200: {HTTP_STATS['200']}")
        print(f"[*] 302: {HTTP_STATS['302']}")
        print(f"[*] 403: {HTTP_STATS['403']}")
        print(f"[*] 404: {HTTP_STATS['404']}")
        print(f"[*] 500: {HTTP_STATS['500']}")
        print(f"[*] Прочие: {HTTP_STATS['other']}")

        simulate_skala3_full()

        print("#" + "="*30)
        print("[+] Работа скрипта Валентина успешно завершена. Все файлы готовы.")

    except Exception as main_err:
        print(f"\n[!] КРИТИЧЕСКАЯ ОШИБКА ВЫПОЛНЕНИЯ: {main_err}")