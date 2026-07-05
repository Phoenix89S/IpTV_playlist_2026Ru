# Valentina.py
# Универсальный сканер CDN NGENIX
# Автор: Phoenix + Copilot
# Стиль отчёта: СКАЛА (телетайп)

import requests
import re
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures
from datetime import datetime

USER_AGENT = "HlsWinkPlayer"
BASE = "https://s70378.cdn.ngenix.net"
EPG_URL = "http://epg.one/epg2.xml.gz"

def load_channels_from_epg(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = gzip.decompress(r.content)
    root = ET.fromstring(data)
    channels = []
    for ch in root.findall("channel"):
        cid = ch.get("id")
        names = [dn.text for dn in ch.findall("display-name") if dn.text]
        if cid and names:
            channels.append({"id": cid, "names": names})
    return channels

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
        return []
    matches = re.findall(r'#EXTINF:-1\s+(.*)', playlist_text)
    return [m.strip() for m in matches]

def scan_all(channels):
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = []
        for ch in channels:
            for name in ch["names"]:
                futures.append(executor.submit(fetch_playlist, name.lower()))
        for f in concurrent.futures.as_completed(futures):
            path, text = f.result()
            infos = parse_extinf(text)
            results[path] = {"extinf": infos, "alive": bool(text)}
    return results

def write_skala_report(results, filename="NgenixScan_report.txt"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("СКАЛА-ТЕЛЕТАЙП ОТЧЁТ CDN NGENIX\n")
        f.write(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*60 + "\n")
        for ch, meta in results.items():
            if meta["alive"]:
                if meta["extinf"]:
                    for info in meta["extinf"]:
                        f.write(f"[LIVE] {ch} :: {info}\n")
                else:
                    f.write(f"[LIVE] {ch} :: (без EXTINF)\n")
            else:
                f.write(f"[DEAD] {ch}\n")
        f.write("="*60 + "\n")
        f.write("КОНЕЦ ОТЧЁТА\n")

def write_m3u(results, filename="NgenixScan.m3u"):
    lines = ["#EXTM3U"]
    for ch, meta in results.items():
        if meta["alive"]:
            if meta["extinf"]:
                for info in meta["extinf"]:
                    lines.append(f"#EXTINF:-1 {info}")
                    lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
                    lines.append(f"{BASE}/{ch}/2/index.m3u8")
            else:
                lines.append(f"#EXTINF:-1,{ch}")
                lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
                lines.append(f"{BASE}/{ch}/2/index.m3u8")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    channels = load_channels_from_epg(EPG_URL)
    data = scan_all(channels)
    for ch, meta in data.items():
        print(f"{ch}: {meta}")
    write_skala_report(data)
    write_m3u(data)
    print("Отчёт NgenixScan_report.txt и плейлист NgenixScan.m3u созданы.")