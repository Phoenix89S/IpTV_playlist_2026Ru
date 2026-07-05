# Valentina.py
# Универсальный сканер CDN NGENIX
# Автор: Phoenix + Copilot
# Стиль отчёта: СКАЛА (телетайп)

import requests
import re
import concurrent.futures
from datetime import datetime

USER_AGENT = "HlsWinkPlayer"
BASE = "https://s70378.cdn.ngenix.net"

# диапазон каталогов для перебора (можно расширять)
CANDIDATES = [f"ch{i}" for i in range(1, 500)] + [
    "domashniy", "pyatnica", "karusel", "perets", "perets_int",
    "sts", "tnt", "muztv", "rentv", "tv3", "matchtv",
    "rossiya1", "rossiya24", "otr", "ntv"
]

def fetch_playlist(path):
    url = f"{BASE}/{path}/2/index.m3u8"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200 and "#EXTM3U" in r.text:
            return path, r.text
    except Exception:
        return path, None
    return path, None

def parse_extinf(playlist_text):
    if not playlist_text:
        return None
    match = re.search(r'#EXTINF:-1\s+(.*)', playlist_text)
    return match.group(1).strip() if match else None

def scan_all():
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(fetch_playlist, p) for p in CANDIDATES]
        for f in concurrent.futures.as_completed(futures):
            path, text = f.result()
            info = parse_extinf(text)
            results[path] = {"extinf": info, "alive": bool(text)}
    return results

def write_skala_report(results, filename="NgenixScan_report.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("СКАЛА-ТЕЛЕТАЙП ОТЧЁТ CDN NGENIX\n")
        f.write(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*60 + "\n")
        for ch, meta in results.items():
            if meta["alive"]:
                f.write(f"[LIVE] {ch} :: {meta['extinf']}\n")
            else:
                f.write(f"[DEAD] {ch}\n")
        f.write("="*60 + "\n")
        f.write("КОНЕЦ ОТЧЁТА\n")

def write_m3u(results, filename="NgenixScan.m3u"):
    lines = ["#EXTM3U"]
    for ch, meta in results.items():
        if meta["alive"]:
            extinf = meta["extinf"] or f",-1,{ch}"
            lines.append(f"#EXTINF:-1 {extinf}")
            lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
            lines.append(f"{BASE}/{ch}/2/index.m3u8")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    data = scan_all()
    for ch, meta in data.items():
        print(f"{ch}: {meta}")
    write_skala_report(data)
    write_m3u(data)
    print("Отчёт NgenixScan_report.txt и плейлист NgenixScan.m3u созданы.")