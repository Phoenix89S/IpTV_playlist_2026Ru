import asyncio
import aiohttp
import time

# ============================================================
#   KOLYA SCANNER — PHOENIX EDITION
#
#   Абсолютный тотальный сканер CDN-кластера UMA Media.
#   Полный перебор всей матрицы вещания:
#
#       3 домена  ×  5 профилей  ×  3 ноды  ×  2 типа плейлистов
#
#   Сканирует реальные ID-пулы UMA:
#       • 314xxx — старые тестовые и архивные каналы
#       • 317xxx — основной ГПМ-кластер (ТНТ, ТВ3, Пятница, 2x2)
#       • 619xxx — музыкальный/цифровой кластер (ТНТ Music)
#
#   Итоговый файл:
#       Kolya.m3u — финальный плейлист, который будет
#       автоматически отправляться в ветки main и gh-pages.
# ============================================================

DOMAINS = [
    "https://bl.uma.media/live",
    "https://edge.uma.media/live",
    "https://strm.uma.media/live"
]

NODES = ["1/1", "2/1", "3/1"]

PLAYLIST_FILES = ["playlist.m3u8", "master.m3u8"]

ID_RANGES = [
    range(314000, 315000),
    range(317000, 318000),
    range(619000, 619400)
]

CONCURRENCY_LIMIT = 150


async def check_combination(session, semaphore, stream_id, domain, profile, node, file, results_list):
    url = f"{domain}/{stream_id}/HLS/{profile}/{node}/{file}"

    async with semaphore:
        try:
            async with session.head(url, timeout=2.5, allow_redirects=True) as response:
                if response.status == 200:
                    res_data = {
                        "id": stream_id,
                        "url": url,
                        "domain": domain.split('//')[1].split('/')[0],
                        "profile": profile,
                        "node": node,
                        "file": file
                    }
                    results_list.append(res_data)

                    print(f"[FOUND] ID {stream_id} | {res_data['domain']} | {profile} | {node} | {file}")
                    return True
        except Exception:
            pass

    return False


async def worker(session, semaphore, stream_id, results_list):
    profiles = [
        "4614144_2",
        "4614144_3",
        "4614144_4",
        f"{stream_id}_3",
        "4614144_1"
    ]

    tasks = []
    for domain in DOMAINS:
        for profile in profiles:
            for node in NODES:
                for file in PLAYLIST_FILES:
                    tasks.append(
                        check_combination(
                            session, semaphore, stream_id,
                            domain, profile, node, file,
                            results_list
                        )
                    )

    await asyncio.gather(*tasks)


async def main():
    start_time = time.time()

    all_ids = []
    for r in ID_RANGES:
        all_ids.extend(r)

    print("=== KOLYA SCANNER — PHOENIX MODE ===")
    print(f"ID к проверке: {len(all_ids)}")
    print("Матрица: 3 домена × 5 профилей × 3 ноды × 2 файла\n")

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT, ttl_dns_cache=300)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Origin": "https://tnt-online.ru"
    }

    found_streams = []

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        batch_size = 50
        for i in range(0, len(all_ids), batch_size):
            batch = all_ids[i:i+batch_size]
            batch_tasks = [
                worker(session, semaphore, s_id, found_streams)
                for s_id in batch
            ]
            await asyncio.gather(*batch_tasks)

    # ---------------------- M3U ВЫГРУЗКА ----------------------
    m3u_filename = "Kolya.m3u"
    with open(m3u_filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for stream in sorted(found_streams, key=lambda x: x['id']):
            channel_name = (
                f"UMA ID {stream['id']} "
                f"({stream['profile']} | {stream['node']} | "
                f"{stream['domain'].split('.')[0].upper()})"
            )
            f.write(
                f'#EXTINF:-1 tvg-id="uma-{stream["id"]}" '
                f'group-title="Kolya Scan", {channel_name}\n'
            )
            f.write(f"{stream['url']}\n")

    end_time = time.time()

    print("\n==================================================")
    print(f"Сканирование завершено за {round(end_time - start_time, 2)} сек.")
    print(f"Найдено живых потоков: {len(found_streams)}")
    print(f"Результат сохранён в: Kolya.m3u")
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(main())
