import requests

# Конфигурация подключения
SERVER = "xpxmesh.megahdtv.xyz"
PORT = "80"
USERNAME = "1847334999"
PASSWORD = "8261235853"

# Выходные файлы
OUTPUT_M3U = "xplash.m3u"
OUTPUT_M3U8 = "xplash.m3u8"

# Категории/слова для исключения
EXCLUDE_KEYWORDS = ["18+", "XXX", "ADULT", "FOR ADULTS"]

BASE_URL = f"http://{SERVER}:{PORT}"
API_URL = f"{BASE_URL}/player_api.php?username={USERNAME}&password={PASSWORD}"


def build_m3u():
    print("1. Получение категорий...")
    try:
        categories_resp = requests.get(f"{API_URL}&action=get_live_categories", timeout=15)
        categories_data = categories_resp.json()
    except Exception as e:
        print(f"Ошибка при запросе категорий: {e}")
        return

    # Карта: ID категории -> Название категории
    categories = {str(cat['category_id']): cat['category_name'] for cat in categories_data}

    print("2. Загрузка списка каналов...")
    try:
        streams_resp = requests.get(f"{API_URL}&action=get_live_streams", timeout=20)
        streams = streams_resp.json()
    except Exception as e:
        print(f"Ошибка при запросе списка каналов: {e}")
        return

    print("3. Формирование плейлистов xplash.m3u и xplash.m3u8...")
    written_count = 0

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f_m3u, open(OUTPUT_M3U8, "w", encoding="utf-8") as f_m3u8:
        f_m3u.write("#EXTM3U\n")
        f_m3u8.write("#EXTM3U\n")

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

            # Ссылка M3U8 (HLS) для обоих плейлистов
            stream_url_m3u8 = f"{BASE_URL}/live/{USERNAME}/{PASSWORD}/{stream_id}.m3u8"

            # Запись в xplash.m3u
            f_m3u.write(f'#EXTINF:-1 tvg-id="{epg_id}" tvg-logo="{logo}" group-title="{cat_name}",{ch_name}\n')
            f_m3u.write(f"{stream_url_m3u8}\n")

            # Запись в xplash.m3u8
            f_m3u8.write(f'#EXTINF:-1 tvg-id="{epg_id}" tvg-logo="{logo}" group-title="{cat_name}",{ch_name}\n')
            f_m3u8.write(f"{stream_url_m3u8}\n")

            written_count += 1

    print(f"Готово! Сохранено каналов: {written_count}")
    print(f"Файлы: '{OUTPUT_M3U}' и '{OUTPUT_M3U8}'")


if __name__ == "__main__":
    build_m3u()
