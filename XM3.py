import requests

# Список всех серверов Xtream
SOURCES = [
    {
        "server": "xpxmesh.megahdtv.xyz",
        "port": "80",
        "username": "1847334999",
        "password": "8261235853"
    },
    {
        "server": "maxtv.123tv.to",
        "port": "8080",
        "username": "GregWoi",
        "password": "6zb7jrUQNK"
    },
    {
        "server": "nocable.cc",
        "port": "8080",
        "username": "s5pPNV",
        "password": "514479"
    }
]

# Выходные файлы
OUTPUT_M3U = "xplash.m3u"
OUTPUT_M3U8 = "xplash.m3u8"

# Категории/слова для исключения
EXCLUDE_KEYWORDS = ["18+", "XXX", "ADULT", "FOR ADULTS"]


def build_m3u():
    total_written = 0

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f_m3u, open(OUTPUT_M3U8, "w", encoding="utf-8") as f_m3u8:
        f_m3u.write("#EXTM3U\n")
        f_m3u8.write("#EXTM3U\n")

        for idx, src in enumerate(SOURCES, start=1):
            # Очистка хоста от лишних протоколов и слэшей
            raw_server = src["server"].replace("http://", "").replace("https://", "").split(":")[0].strip("/")
            port = src["port"]
            username = src["username"]
            password = src["password"]

            base_url = f"http://{raw_server}:{port}"
            api_url = f"{base_url}/player_api.php?username={username}&password={password}"

            print(f"\n--- Обработка источника #{idx} ({raw_server}) ---")

            # 1. Получение категорий
            try:
                cat_resp = requests.get(f"{api_url}&action=get_live_categories", timeout=15)
                categories_data = cat_resp.json()
                categories = {str(cat['category_id']): cat['category_name'] for cat in categories_data}
            except Exception as e:
                print(f" Ошибка получения категорий с {raw_server}: {e}")
                continue

            # 2. Загрузка каналов
            try:
                streams_resp = requests.get(f"{api_url}&action=get_live_streams", timeout=20)
                streams = streams_resp.json()
            except Exception as e:
                print(f" Ошибка получения каналов с {raw_server}: {e}")
                continue

            # 3. Запись каналов источника
            source_count = 0
            for stream in streams:
                cat_id = str(stream.get('category_id'))
                cat_name = categories.get(cat_id, "Без категории")
                ch_name = stream.get('name', 'Без названия')

                # Пропуск ненужных категорий/каналов
                if any(bad in cat_name.upper() or bad in ch_name.upper() for bad in EXCLUDE_KEYWORDS):
                    continue

                stream_id = stream.get('stream_id')
                logo = stream.get('stream_icon', '')
                epg_id = stream.get('epg_channel_id', '')

                # Поток .m3u8
                stream_url_m3u8 = f"{base_url}/live/{username}/{password}/{stream_id}.m3u8"

                # Форматирование записи
                entry = f'#EXTINF:-1 tvg-id="{epg_id}" tvg-logo="{logo}" group-title="{cat_name}",{ch_name}\n{stream_url_m3u8}\n'

                f_m3u.write(entry)
                f_m3u8.write(entry)

                source_count += 1
                total_written += 1

            print(f" Успешно добавлено каналов: {source_count}")

    print(f"\nИтог: Сохранено всего {total_written} каналов в '{OUTPUT_M3U}' и '{OUTPUT_M3U8}'")


if __name__ == "__main__":
    build_m3u()
