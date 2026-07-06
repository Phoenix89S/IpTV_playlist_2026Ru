# ====================================================================
# Valentina.py
# Универсальный сканер CDN NGENIX + Тотальный Брутфорс-генератор
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

USER_AGENT = "HlsWinkPlayer"
BASE = "https://s70378.cdn.ngenix.net"
EPG_URL = "https://epg.one/epg2.xml.gz"
LOCAL_EPG = "epg2.xml.gz"

# Диапазон узлов NGENIX для перебора (если используется scan_nodes)
NODES = [f"s{n}" for n in range(10000, 80000, 1000)]  # шаг 1000
# Подпапки качества
SUBDIRS = ["1", "2", "3", "4"]

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

def download_epg(url=EPG_URL, local_file=LOCAL_EPG):
    print("[*] Скачивание EPG словаря...")
    try:
        r = session.get(url, timeout=20, verify=False)
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
    return channels

def fetch_playlist(path, subdir="2"):
    safe_path = quote(path)
    url = f"{BASE}/{safe_path}/{subdir}/index.m3u8"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = session.get(url, headers=headers, timeout=5, verify=False)
        if r.status_code == 200 and "#EXTM3U" in r.text:
            return f"{path}/{subdir}", r.text, datetime.now(MSK)
    except Exception:
        return f"{path}/{subdir}", None, datetime.now(MSK)
    return f"{path}/{subdir}", None, datetime.now(MSK)

def parse_hls_features(playlist_text):
    if not playlist_text:
        return []
    resolutions = re.findall(r'RESOLUTION=(\d+x\d+)', playlist_text)
    if resolutions:
        return [f"{res}" for res in sorted(set(resolutions), reverse=True)]
    if "#EXTINF:" in playlist_text:
        return ["Media Stream"]
    return ["M3U8 OK"]

def scan_all(channels):
    results = {}
    unique_paths = set()
    for ch in channels:
        for name in ch["names"]:
            cleaned_path = name.strip().lower()
            if cleaned_path:
                unique_paths.add(cleaned_path)
    print("----- начало процесса -----")
    print("[*] Запущен внутренний перебор каналов и подпапок...")
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

def write_skala_report(results, filename="NgenixScan_report.txt"):
    print("[*] Запись телетайп-отчёта...")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("СКАЛА-ТЕЛЕТАЙП ОТЧЁТ CDN NGENIX\n")
        f.write(f"Дата генерации: {datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')} (МСК)\n")
        f.write("="*60 + "\n")
        for ch, meta in sorted(results.items()):
            ts = meta.get("timestamp", datetime.now(MSK))
            if meta["alive"]:
                info = ", ".join(meta["features"]) if meta["features"] else "Доступен"
                f.write(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} [LIVE] {ch} :: {info}\n")
            else:
                f.write(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} [DEAD] {ch}\n")
        f.write("="*60 + "\n")
        f.write("КОНЕЦ ОТЧЁТА\n")

def write_m3u(results, filename="NgenixScan.m3u"):
    """ Генерирует плейлист только из УСПЕШНО проверенных скриптом каналов """
    print("[*] Запись LIVE-плейлиста...")
    lines = ["#EXTM3U"]
    for ch_key, meta in sorted(results.items()):
        if meta["alive"]:
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

def write_full_bruteforce_m3u(channels, filename="Ngenix_Full_Bruteforce.m3u"):
    """ 
    Генерирует ТОТАЛЬНЫЙ сырой плейлист подстановок.
    Сюда идут абсолютно ВСЕ каналы из EPG по ВСЕМ папкам качества (без проверки на LIVE).
    Идеально для внешних чекеров.
    """
    lines = ["#EXTM3U\n# СКАЛА.3 — Полный брутфорс-список для внешнего сканирования\n"]
    
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
            lines.append(f"{BASE}/{quote(path)}/{subdir}/index.m3u8\n")
            
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    # Шапка СКАЛА.3
    print("#==== СКАЛА.3. IPTV edition ===")
    print(f"Дата (МСК): {datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Дата (КЛГ): {datetime.now(KLG).strftime('%Y-%m-%d %H:%M:%S')}")
    print("#" + "="*30)

    try:
        # 1. Загрузка EPG
        epg_file = download_epg()
        channels = load_channels_from_epg(epg_file)
        print(f"[+] Успешно загружено каналов из EPG: {len(channels)}")

        # 2. ГЕНЕРАЦИЯ ТОТАЛЬНОГО СПИСКА (Полный брутфорс-лист подстановок)
        write_full_bruteforce_m3u(channels)
        print("[+] Тотальный брутфорс-плейлист успешно создан (Ngenix_Full_Bruteforce.m3u).")
        print("-" * 30)

        # 3. Запуск встроенного сканера (проверяет живые ссылки сам)
        scan_results = scan_all(channels)

        # 4. Запись результатов встроенной проверки
        write_skala_report(scan_results)
        print("[+] Отчёт СКАЛА-ТЕЛЕТАЙП успешно сгенерирован (NgenixScan_report.txt).")

        write_m3u(scan_results)
        print("[+] Плейлист проверенных LIVE-каналов успешно сгенерирован (NgenixScan.m3u).")

        print("#" + "="*30)
        print("[+] Работа скрипта Валентина успешно завершена. Все файлы готовы.")

    except Exception as main_err:
        print(f"\n[!] КРИТИЧЕСКАЯ ОШИБКА ВЫПОЛНЕНИЯ: {main_err}")
