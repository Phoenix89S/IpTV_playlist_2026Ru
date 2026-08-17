import requests

# Конфигурация подключения[span_0](start_span)[span_0](end_span)
SERVER = "xpxmesh.megahdtv.xyz[span_1](start_span)"[span_1](end_span)
PORT = "80[span_2](start_span)"[span_2](end_span)
USERNAME = "1847334999[span_3](start_span)"[span_3](end_span)
PASSWORD = "8261235853[span_4](start_span)"[span_4](end_span)

# Выходные файлы[span_5](start_span)[span_5](end_span)
OUTPUT_M3U = "xplash.m3u[span_6](start_span)"[span_6](end_span)
OUTPUT_M3U8 = "xplash.m3u8[span_7](start_span)"[span_7](end_span)

# Категории/слова для исключения[span_8](start_span)[span_8](end_span)
EXCLUDE_KEYWORDS = ["18+", "XXX", "ADULT", "FOR ADULTS"][span_9](start_span)[span_9](end_span)

BASE_URL = f"http://{SERVER}:{PORT}[span_10](start_span)"[span_10](end_span)
API_URL = f"{BASE_URL}/player_api.php?username={USERNAME}&password={PASSWORD}[span_11](start_span)"[span_11](end_span)


def build_m3u():
    print("1. Получение категорий...")[span_12](start_span)[span_12](end_span)
    try:
        categories_resp = requests.get(f"{API_URL}&action=get_live_categories", timeout=15)[span_13](start_span)[span_13](end_span)
        categories_data = categories_resp.json()[span_14](start_span)[span_14](end_span)
    except Exception as e:
        print(f"Ошибка при запросе категорий: {e}")[span_15](start_span)[span_15](end_span)
        return

    # Карта: ID категории -> Название категории[span_16](start_span)[span_16](end_span)
    categories = {str(cat['category_id']): cat['category_name'] for cat in categories_data}[span_17](start_span)[span_17](end_span)

    print("2. Загрузка списка каналов...")[span_18](start_span)[span_18](end_span)
    try:
        streams_resp = requests.get(f"{API_URL}&action=get_live_streams", timeout=20)[span_19](start_span)[span_19](end_span)
        streams = streams_resp.json()[span_20](start_span)[span_20](end_span)
    except Exception as e:
        print(f"Ошибка при запросе списка каналов: {e}")[span_21](start_span)[span_21](end_span)
        return

    print("3. Формирование плейлистов с .m3u8 потоками...")
    written_count = 0

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f_m3u, open(OUTPUT_M3U8, "w", encoding="utf-8") as f_m3u8:
        f_m3u.write("#EXTM3U\n")[span_22](start_span)[span_22](end_span)
        f_m3u8.write("#EXTM3U\n")[span_23](start_span)[span_23](end_span)

        for stream in streams:
            cat_id = str(stream.get('category_id'))[span_24](start_span)[span_24](end_span)
            cat_name = categories.get(cat_id, "Без категории")[span_25](start_span)[span_25](end_span)
            ch_name = stream.get('name', 'Без названия')[span_26](start_span)[span_26](end_span)

            # Пропуск ненужных категорий/каналов[span_27](start_span)[span_27](end_span)
            if any(bad in cat_name.upper() or bad in ch_name.upper() for bad in EXCLUDE_KEYWORDS):[span_28](start_span)[span_28](end_span)
                continue

            stream_id = stream.get('stream_id')[span_29](start_span)[span_29](end_span)
            logo = stream.get('stream_icon', '')[span_30](start_span)[span_30](end_span)
            epg_id = stream.get('epg_channel_id', '')[span_31](start_span)[span_31](end_span)

            # Теперь ОБА файла содержат .m3u8 ссылки
            stream_url_m3u8 = f"{BASE_URL}/live/{USERNAME}/{PASSWORD}/{stream_id}.m3u8"

            # Запись в xplash.m3u
            f_m3u.write(f'#EXTINF:-1 tvg-id="{epg_id}" tvg-logo="{logo}" group-title="{cat_name}",{ch_name}\n')[span_32](start_span)[span_32](end_span)
            f_m3u.write(f"{stream_url_m3u8}\n")

            # Запись в xplash.m3u8
            f_m3u8.write(f'#EXTINF:-1 tvg-id="{epg_id}" tvg-logo="{logo}" group-title="{cat_name}",{ch_name}\n')[span_33](start_span)[span_33](end_span)
            f_m3u8.write(f"{stream_url_m3u8}\n")

            written_count += 1

    print(f"Готово! Сохранено каналов: {written_count}")[span_34](start_span)[span_34](end_span)
    print(f"Файлы: '{OUTPUT_M3U}' и '{OUTPUT_M3U8}'")[span_35](start_span)[span_35](end_span)


if __name__ == "__main__":
    build_m3u()[span_36](start_span)[span_36](end_span)
