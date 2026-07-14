#!/usr/bin/env python3

BASE_URL = "http://cdn.kubteltv.workers.dev/?ID="
OUTPUT_FILE = "playlist.m3u"

def main():
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i in range(1, 1001):
            f.write(f"#EXTINF:-1,Заглушка {i}\n")
            f.write(f"{BASE_URL}{i}\n")

if __name__ == "__main__":
    main()
    print("[OK] playlist.m3u создан. 1000 ссылок записано.")