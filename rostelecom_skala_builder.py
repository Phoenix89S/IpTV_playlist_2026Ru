import os
import re
import json
import shutil
import urllib.request
from datetime import datetime

# ==========================================
# ВХОДНОЙ ИСТОЧНИК И НАСТРОЙКИ
# ==========================================
SOURCE_URL = "https://gist.githubusercontent.com/ageresz/a1b1790b4febbf219df31ba32094e3bf/raw/76a3d1b67274410099fd7b665ba82380c22b4aec/4_List.m3u"
WINK_API_URL = "https://backend.v2.wink.ru/api/v2/channels"

NODE_NAME = "Ростелеком"
BASE_NAME = "rostel_SKALA_Dreg"
EXTENSIONS = [".m3u", ".m3u8", ".txt"]
PLAYLIST_GROUP = "Rostelecom"

TARGET_FOLDERS = [
    ".",         # Корень
    "./main",    # Папка main
    "./output"   # Папка output
]

# Ключевые слова для НАГЛУХОГО отсечения 18+
ADULT_KEYWORDS = [
    "18+", "adult", "erotika", "эротика", "ночные", "brazzers", 
    "hustler", "playboy", "русская ночь", "vivid", "penthouse", "xx", "эгоист"
]

# ==========================================
# ФУНКЦИИ СЕТИ И ОПРОСА API
# ==========================================

def get_web_data(url: str) -> str:
    """Загрузка данных по URL без вывода в консоль."""
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read().decode('utf-8', errors='ignore')

def load_wink_channels_map(sys_logs: list) -> dict:
    """Опрашивает API Wink / Ростелеком. Логирует всё в массив sys_logs."""
    sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Инициализация запроса к API Wink (Ростелеком)...")
    channels_map = {}
    try:
        data_raw = get_web_data(WINK_API_URL)
        json_data = json.loads(data_raw)
        
        items = json_data.get("items", [])
        for item in items:
            ch_id = str(item.get("id"))
            channels_map[ch_id] = {
                "name": item.get("name", "").strip(),
                "logo": item.get("logo", {}).get("url", ""),
                "is_adult": item.get("is_adult", False) or item.get("age_rating", 0) >= 18,
                "epg_id": item.get("epg_id", "")
            }
        sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] API Wink успешно загружен. Найдено каналов в реестре: {len(channels_map)}")
    except Exception as e:
        sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] [ОШИБКА API] Не удалось загрузить API Wink: {e}")
    
    return channels_map

# ==========================================
# ОБРАБОТКА, НУМЕРАЦИЯ И ЛОГИРОВАНИЕ
# ==========================================

def parse_and_identify_playlist(m3u_raw: str, wink_map: dict) -> tuple[str, list]:
    lines = m3u_raw.splitlines()
    output_lines = ["#EXTM3U url-tvg=\"http://epg.itv.uz/teleguide.xml.gz\""]
    detail_logs = []

    channel_number = 1
    blocked_count = 0
    unknown_count = 0

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#EXTM3U") or line.startswith("#EXTINF"):
            continue
        
        if line.startswith("http"):
            stream_url = line
            
            # Вытягиваем ID из URL (.../2402/index.m3u8 -> 2402)
            id_match = re.search(r'/iptv/[^/]+/(\d+)/', stream_url)
            channel_id = id_match.group(1) if id_match else None

            ch_info = wink_map.get(channel_id, {}) if channel_id else {}
            
            raw_name = ch_info.get("name")
            if not raw_name:
                raw_name = f"Канал {channel_id}" if channel_id else "Неизвестный поток"
                unknown_count += 1

            is_adult = ch_info.get("is_adult", False)
            
            # 1. ЖЁСТКИЙ ФИЛЬТР 18+ (НАГЛУХО)
            full_check = f"{raw_name} {stream_url}".lower()
            if is_adult or any(kw in full_check for kw in ADULT_KEYWORDS):
                detail_logs.append(f"[ОТСЕЧЕНО 18+] ID: {channel_id} | {raw_name}")
                blocked_count += 1
                continue

            # 2. ОПРЕДЕЛЕНИЕ СМЕЩЕНИЯ (Shift) И EPG
            shift_val = 0
            shift_match = re.search(r'\(\+(\d+)\)', raw_name)
            if shift_match:
                shift_val = shift_match.group(1)

            shift_attr = f' tvg-shift="{shift_val}"' if str(shift_val) != "0" else ""
            tvg_logo = ch_info.get("logo", "")
            tvg_id = ch_info.get("epg_id", "")

            # 3. ПОДГОТОВКА ИМЕНИ И НУМЕРАЦИЯ (Сквозной номер: 1, 2, 3... N)
            formatted_name = f"{channel_number}. {raw_name}"

            # 4. ФОРМИРОВАНИЕ СТРОКИ В ГРУППУ Rostelecom
            extinf = f'#EXTINF:-1 tvg-id="{tvg_id}"{shift_attr} tvg-logo="{tvg_logo}" group-title="{PLAYLIST_GROUP}",{formatted_name}'
            
            output_lines.append(extinf)
            output_lines.append("#EXTVLCOPT:http-user-agent=HlsWinkPlayer")
            output_lines.append(stream_url)

            # Полная запись о добавлении канала со сквозным номером в .txt лог
            detail_logs.append(f"[ОК] #{channel_number} | ID: {channel_id} -> {formatted_name}")
            
            channel_number += 1

    total_added = channel_number - 1
    summary_logs = [
        "--------------------------------------------------",
        f"[ИТОГ ОПРОСА СКАЛА] Добавлено в {PLAYLIST_GROUP}: {total_added} (№ 1..{total_added}) | Отсечено 18+: {blocked_count} | Без имени: {unknown_count}",
        "--------------------------------------------------"
    ]
    return "\n".join(output_lines) + "\n", summary_logs + detail_logs

# ==========================================
# ОСНОВНОЙ ПРОЦЕСС (СКАЛА / ДРЭГ)
# ==========================================

def process_pass(pass_number: int):
    suffix = f"_{pass_number}"
    time_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sys_logs = []

    try:
        raw_m3u = get_web_data(SOURCE_URL)
        sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Исходный M3U список с Gist успешно загружен.")
    except Exception as e:
        sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] [КРИТИЧЕСКАЯ ОШИБКА] Загрузка Gist сорвалась: {e}")
        return

    # Опрашиваем API Wink
    wink_map = load_wink_channels_map(sys_logs)

    # Собираем, нумеруем и фильтруем
    m3u_content, process_logs = parse_and_identify_playlist(raw_m3u, wink_map)

    # Формируем полный текст лога для файла .txt
    log_lines = [
        "==================================================",
        f" УЗЕЛ: {NODE_NAME}",
        f" СИСТЕМА: СКАЛА / ДРЭГ (ver 10.10.6.1)",
        f" МЕТКА ВРЕМЕНИ: {time_stamp}",
        f" ПРОХОД: {suffix}",
        f" ФИЛЬТР 18+: ЖЁСТКАЯ БЛОКИРОВКА (АКТИВЕН)",
        f" ГРУППА ПЛЕЙЛИСТА: {PLAYLIST_GROUP}",
        "==================================================",
        "--- СИСТЕМНЫЙ ЖУРНАЛ ---"
    ] + sys_logs + [
        "",
        "--- ДЕТАЛИЗАЦИЯ И ОБРАБОТКА ПОТОКОВ ---"
    ] + process_logs

    full_log_content = "\n".join(log_lines) + "\n"

    # Раскладываем файлы по всем директориям без вывода в консоль
    for folder in TARGET_FOLDERS:
        os.makedirs(folder, exist_ok=True)

        for ext in EXTENSIONS:
            filename = f"{BASE_NAME}{suffix}{ext}"
            file_path = os.path.join(folder, filename)
            
            content = full_log_content if ext == ".txt" else m3u_content
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        for ext in EXTENSIONS:
            src = os.path.join(folder, f"{BASE_NAME}{suffix}{ext}")
            dst = os.path.join(folder, f"{BASE_NAME}{ext}")
            shutil.copyfile(src, dst)

if __name__ == "__main__":
    process_pass(1)
