import os
import shutil
from datetime import datetime

# ==========================================
# КОНФИГУРАЦИЯ СИСТЕМЫ И УЗЛА
# ==========================================
NODE_NAME = "Ростелеком"
BASE_NAME = "rostel_SKALA_Dreg"
EXTENSIONS = [".m3u", ".m3u8", ".txt"]

# Настройки группы и фильтрации
PLAYLIST_GROUP = "Rostelecom"
ENABLE_ADULT_FILTER = True  # ЖЁСТКАЯ БЛОКИРОВКА: каналы 18+ отсекаются наглухо!

# Целевые директории для вывода
TARGET_FOLDERS = [
    "./output_dir_1",
    "./output_dir_2",
    "./logs_dir"
]

# ==========================================
# БАЗА ДАННЫХ КАНАЛОВ УЗЛА РОСТЕЛЕКОМ (ПРИМЕР)
# ==========================================
CHANNELS_DATA = [
    {
        "name": "Первый канал",
        "tvg_id": "1tv",
        "logo": "https://iptvx.one/picons/1tv.png",
        "url": "https://zabava-htlive.cdn.ngenix.net/hls/CH_1TV/variant.m3u8",
        "shift": 0,
        "is_adult": False
    },
    {
        "name": "Первый канал (+2)",
        "tvg_id": "1tv-pl2",
        "logo": "https://iptvx.one/picons/1tv.png",
        "url": "https://zabava-htlive.cdn.ngenix.net/hls/CH_1TV_2/variant.m3u8",
        "shift": 2,
        "is_adult": False
    },
    {
        "name": "Домашний",
        "tvg_id": "domashny",
        "logo": "https://iptvx.one/picons/domashniy.png",
        "url": "https://zabava-htlive.cdn.ngenix.net/hls/CH_DOMASHNIY/variant.m3u8",
        "shift": 0,
        "is_adult": False
    },
    {
        "name": "Домашний (+2)",
        "tvg_id": "domashny-pl2",
        "logo": "https://iptvx.one/picons/domashny_2.png",
        "url": "https://zabava-htlive.cdn.ngenix.net/hls/CH_DOMASHNY_2/variant.m3u8",
        "shift": 2,
        "is_adult": False
    },
    {
        "name": "Русская Ночь",
        "tvg_id": "rus-night",
        "logo": "https://iptvx.one/picons/rus_night.png",
        "url": "https://zabava-htlive.cdn.ngenix.net/hls/CH_RUSNIGHT/variant.m3u8",
        "shift": 0,
        "is_adult": True  # Будет отсечен наглухо
    },
    {
        "name": "Brazzers TV",
        "tvg_id": "brazzers-tv",
        "logo": "https://iptvx.one/picons/brazzers.png",
        "url": "https://zabava-htlive.cdn.ngenix.net/hls/CH_BRAZZERS/variant.m3u8",
        "shift": 0,
        "is_adult": True  # Будет отсечен наглухо
    }
]

# ==========================================
# ФУНКЦИИ ГЕНЕРАЦИИ И СБОРКИ
# ==========================================

def generate_playlist(channels: list) -> tuple[str, list]:
    """
    Генерирует M3U плейлист:
    - Жесткая отфильтровка 18+
    - Привязка EPG и смещения (shift)
    - Группировка в Rostelecom
    """
    lines = ["#EXTM3U url-tvg=\"http://epg.itv.uz/teleguide.xml.gz\""]
    logs = []
    
    added_count = 0
    blocked_count = 0

    for ch in channels:
        # 1. Жесткий фильтр 18+
        if ch.get("is_adult") or ENABLE_ADULT_FILTER and ch.get("is_adult"):
            logs.append(f"[ФИЛЬТР 18+ | БЛОКИРОВКА] Отсечен контент для взрослых: {ch['name']}")
            blocked_count += 1
            continue

        # 2. Обработка EPG и смещения времени
        shift_val = ch.get("shift", 0)
        shift_attr = f' tvg-shift="{shift_val}"' if shift_val != 0 else ""
        tvg_id = ch.get("tvg_id", "")
        logo = ch.get("logo", "")
        channel_name = ch["name"]

        # 3. Формирование строки плейлиста в группу Rostelecom
        extinf = f'#EXTINF:-1 tvg-id="{tvg_id}"{shift_attr} tvg-logo="{logo}" group-title="{PLAYLIST_GROUP}",{channel_name}'
        
        lines.append(extinf)
        lines.append("#EXTVLCOPT:http-user-agent=HlsWinkPlayer")
        lines.append(ch["url"])
        
        added_count += 1

    logs.append("--------------------------------------------------")
    logs.append(f"[ИТОГ СБОРКИ] Добавлено каналов: {added_count} | Заблокировано (18+): {blocked_count}")
    return "\n".join(lines) + "\n", logs


def process_pass(pass_number: int, channels: list):
    """
    Выполняет один проход (_n) сборки и логирования (СКАЛА / ДРЭГ).
    """
    suffix = f"_{pass_number}"
    time_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Генерация плейлиста
    m3u_content, process_logs = generate_playlist(channels)

    # Формирование текстового лога телетайпа
    log_lines = [
        "==================================================",
        f" УЗЕЛ: {NODE_NAME}",
        f" СИСТЕМА: СКАЛА / ДРЭГ (ver 10.10.6.1)",
        f" МЕТКА ВРЕМЕНИ: {time_stamp}",
        f" ПРОХОД: {suffix}",
        f" ФИЛЬТР 18+: ЖЁСТКАЯ БЛОКИРОВКА (АКТИВЕН)",
        f" ГРУППА ПЛЕЙЛИСТА: {PLAYLIST_GROUP}",
        "==================================================",
        ""
    ] + process_logs

    full_log_content = "\n".join(log_lines) + "\n"

    # Сохранение и продублирование во все директории
    for folder in TARGET_FOLDERS:
        os.makedirs(folder, exist_ok=True)

        # 1. Запись файлов прохода (_n)
        for ext in EXTENSIONS:
            filename = f"{BASE_NAME}{suffix}{ext}"
            file_path = os.path.join(folder, filename)
            
            content = full_log_content if ext == ".txt" else m3u_content
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        # 2. Обновление рабочей копии без суффикса (текущая версия узла)
        for ext in EXTENSIONS:
            src = os.path.join(folder, f"{BASE_NAME}{suffix}{ext}")
            dst = os.path.join(folder, f"{BASE_NAME}{ext}")
            shutil.copyfile(src, dst)

    print(f"[{NODE_NAME} | СКАЛА] Проход {suffix} завершен. Все каналы 18+ отсечены. Файлы записаны.")


# ==========================================
# ТОЧКА ВХОДА
# ==========================================
if __name__ == "__main__":
    process_pass(1, CHANNELS_DATA)
