# Valentina.py
# Универсальный сканер CDN NGENIX
# Автор: Phoenix + Copilot + Gemini
# Стиль отчёта: СКАЛА (телетайп)

import requests
import re
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures
from datetime import datetime
from urllib.parse import quote
import os

USER_AGENT = "HlsWinkPlayer"
BASE = "https://s70378.cdn.ngenix.net"
EPG_URL = "https://epg.one/epg2.xml.gz"
LOCAL_EPG = "epg2.xml.gz"

def download_epg(url=EPG_URL, local_file=LOCAL_EPG):
    print("[*] Скачивание EPG словаря...")
    try:
        r = requests.get(url, timeout=20)
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

def fetch_playlist(path):
    safe_path = quote(path)
    url = f"{BASE}/{safe_path}/2/index.m3u8"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200 and "#EXTM3U" in r.text:
            return path, r.text
    except Exception:
        return path, None
    return path, None

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
    print(f"[+] Сформировано {len(unique_paths)} уникальных направлений для проверки.")
    print("="*60)
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        future_to_path = {executor.submit(fetch_playlist, path): path for path in unique_paths}
        for future in concurrent.futures.as_completed(future_to_path):
            path, text = future.result()
            features = parse_hls_features(text)
            is_alive = bool(text)
            results[path] = {"features": features, "alive": is_alive}
            status = "[LIVE]" if is_alive else "[DEAD]"
            details = f" ({', '.join(features)})" if features else ""
            print(f"{status} {path}{details}")
    print("="*60)
    return results

def write_skala_report(results, filename="NgenixScan_report.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("СКАЛА-ТЕЛЕТАЙП ОТЧЁТ CDN NGENIX\n")
        f.write(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*60 + "\n")
        for ch, meta in sorted(results.items()):
            if meta["alive"]:
                info = ", ".join(meta["features"]) if meta["features"] else "Доступен"
                f.write(f"[LIVE] {ch} :: {info}\n")
            else:
                f.write(f"[DEAD] {ch}\n")
        f.write("="*60 + "\n")
        f.write("КОНЕЦ ОТЧЁТА\n")

def write_m3u(results, filename="NgenixScan.m3u"):
    lines = ["#EXTM3U"]
    for ch, meta in sorted(results.items()):
        if meta["alive"]:
            display_name = ch.upper()
            features_str = f" [{', '.join(meta['features'])}]" if meta['features'] else ""
            lines.append(f'#EXTINF:-1 http-user-agent="{USER_AGENT}",{display_name}{features_str}')
            lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
            lines.append(f"{BASE}/{quote(ch)}/2/index.m3u8")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    print("[*] Запуск сканера ВАЛЕНТИНА...")
    epg_file = download_epg()
    channels = load_channels_from_epg(epg_file)
    if not channels:
        print("[-] Нет данных для анализа. Завершение.")
        exit(1)
    data = scan_all(channels)
    write_skala_report(data)
    write_m3u(data)
    print("[+] Отчёт NgenixScan_report.txt и плейлист NgenixScan.m3u успешно сгенерированы.")