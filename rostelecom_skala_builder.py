import os
import re
import json
import shutil
import urllib.request
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime

# ==========================================
# ВХОДНОЙ ИСТОЧНИК И НАСТРОЙКИ
# ==========================================
SOURCE_URL = "https://gist.githubusercontent.com/ageresz/a1b1790b4febbf219df31ba32094e3bf/raw/76a3d1b67274410099fd7b665ba82380c22b4aec/4_List.m3u"
WINK_API_URL = "https://backend.v2.wink.ru/api/v2/channels"

NODE_NAME = "Ростелеком"
BASE_NAME = "rostel_SKALA_Dreg"
EXTENSIONS = [".m3u", ".m3u8", ".yml", ".txt"]
PLAYLIST_GROUP = "Rostelecom"

# Комплексный User-Agent для сетевых запросов к API
WINK_USER_AGENT = "Mozilla/5.0 (Linux; Android 12; WinkTV 1.88.1; ru-RU) Gecko/20100101 Firefox/117.0"

TARGET_FOLDERS = [
    ".",         # Корень
    "./main",    # Папка main
    "./output"   # Папка output
]

ADULT_KEYWORDS = [
    "18+", "adult", "erotika", "эротика", "ночные", "brazzers", 
    "hustler", "playboy", "русская ночь", "vivid", "penthouse", "xx", "эгоист"
]

# ==========================================
# ФУНКЦИИ СЕТИ И ОПРОСА API
# ==========================================

def get_web_data(url: str) -> str:
    """Загрузка данных по URL с эмуляцией мобильного клиента Wink."""
    headers = {
        'User-Agent': WINK_USER_AGENT,
        'Referer': 'https://wink.ru/',
        'X-Requested-With': 'Wink',
        'X-Forwarded-For': '95.24.0.1',
        'Accept': '*/*'
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read().decode('utf-8', errors='ignore')

def load_wink_channels_map(sys_logs: list) -> dict:
    """Опрашивает API Wink с полными заголовками."""
    sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Запрос к API Wink (Android WinkTV)...")
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
        sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] API Wink загружен. Записей: {len(channels_map)}")
    except Exception as e:
        sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] [ОШИБКА API] Не удалось загрузить API Wink: {e}")
    
    return channels_map

# ==========================================
# ОБРАБОТКА И ФОРМИРОВАНИЕ YML / M3U
# ==========================================

def parse_and_build(m3u_raw: str, wink_map: dict) -> tuple[str, str, list]:
    lines = m3u_raw.splitlines()
    m3u_lines = ["#EXTM3U url-tvg=\"http://epg.itv.uz/teleguide.xml.gz\""]
    detail_logs = []

    # Создание структуры YML XML
    yml_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml_catalog = ET.Element("yml_catalog", date=yml_date)
    shop = ET.SubElement(yml_catalog, "shop")
    
    ET.SubElement(shop, "name").text = "Rostelecom IPTV Node"
    ET.SubElement(shop, "company").text = "СКАЛА / ДРЭГ"
    ET.SubElement(shop, "url").text = "http://rostelekom.xyz"

    categories = ET.SubElement(shop, "categories")
    cat = ET.SubElement(categories, "category", id="1")
    cat.text = PLAYLIST_GROUP

    offers = ET.SubElement(shop, "offers")

    channel_number = 1
    blocked_count = 0
    unknown_count = 0

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#EXTM3U") or line.startswith("#EXTINF"):
            continue
        
        if line.startswith("http"):
            stream_url = line
            
            id_match = re.search(r'/iptv/[^/]+/(\d+)/', stream_url)
            channel_id = id_match.group(1) if id_match else None

            ch_info = wink_map.get(channel_id, {}) if channel_id else {}
            
            raw_name = ch_info.get("name")
            if not raw_name:
                raw_name = f"Канал {channel_id}" if channel_id else "Неизвестный поток"
                unknown_count += 1

            is_adult = ch_info.get("is_adult", False)
            
            # 1. ФИЛЬТР 18+
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

            formatted_name = f"{channel_number}. {raw_name}"

            # 3. ФОРМИРОВАНИЕ СТРОК M3U С ПОЛНЫМ НАБОРОМ WINK-ЗАГОЛОВКОВ
            extinf = f'#EXTINF:-1 tvg-id="{tvg_id}"{shift_attr} tvg-logo="{tvg_logo}" group-title="{PLAYLIST_GROUP}",{formatted_name}'
            m3u_lines.append(extinf)
            m3u_lines.append('#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Linux; Android 12; WinkTV 1.88.1; ru-RU) Gecko/20100101 Firefox/117.0')
            m3u_lines.append('#EXTVLCOPT:http-referer=https://wink.ru/')
            m3u_lines.append('#EXTVLCOPT:http-header=X-Requested-With=Wink')
            m3u_lines.append('#EXTVLCOPT:http-header=X-Forwarded-For: 95.24.0.1')
            m3u_lines.append(stream_url)

            # 4. ФОРМИРОВАНИЕ ВЕТКИ YML (<offer>)
            offer = ET.SubElement(offers, "offer", id=str(channel_number), available="true")
            ET.SubElement(offer, "url").text = stream_url
            ET.SubElement(offer, "name").text = formatted_name
            ET.SubElement(offer, "categoryId").text = "1"
            if tvg_logo:
                ET.SubElement(offer, "picture").text = tvg_logo
            if tvg_id:
                ET.SubElement(offer, "param", name="epg_id").text = str(tvg_id)
            if shift_val:
                ET.SubElement(offer, "param", name="shift").text = str(shift_val)
            
            # Параметры авторизации Wink в YML
            ET.SubElement(offer, "param", name="user-agent").text = WINK_USER_AGENT
            ET.SubElement(offer, "param", name="referer").text = "https://wink.ru/"
            ET.SubElement(offer, "param", name="x-requested-with").text = "Wink"
            ET.SubElement(offer, "param", name="x-forwarded-for").text = "95.24.0.1"

            detail_logs.append(f"[ОК] #{channel_number} | ID: {channel_id} -> {formatted_name}")
            channel_number += 1

    total_added = channel_number - 1
    summary_logs = [
        "--------------------------------------------------",
        f"[ИТОГ ОПРОСА СКАЛА] Добавлено каналов (M3U + YML): {total_added} (№ 1..{total_added}) | Отсечено 18+: {blocked_count} | Без имени: {unknown_count}",
        "--------------------------------------------------"
    ]

    raw_xml = ET.tostring(yml_catalog, encoding="utf-8")
    reparsed = minidom.parseString(raw_xml)
    yml_content = reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

    return "\n".join(m3u_lines) + "\n", yml_content, summary_logs + detail_logs

# ==========================================
# ОСНОВНОЙ ПРОЦЕСС
# ==========================================

def process_pass(pass_number: int):
    suffix = f"_{pass_number}"
    time_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sys_logs = []

    try:
        raw_m3u = get_web_data(SOURCE_URL)
        sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Исходный список Gist загружен.")
    except Exception as e:
        sys_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] [КРИТИЧЕСКАЯ ОШИБКА] Загрузка сорвалась: {e}")
        return

    wink_map = load_wink_channels_map(sys_logs)
    m3u_content, yml_content, process_logs = parse_and_build(raw_m3u, wink_map)

    log_lines = [
        "==================================================",
        f" УЗЕЛ: {NODE_NAME}",
        f" СИСТЕМА: СКАЛА / ДРЭГ (ver 10.10.6.1)",
        f" МЕТКА ВРЕМЕНИ: {time_stamp}",
        f" ПРОФИЛЬ АВТОРИЗАЦИИ: WINK FULL HEADERS (Android 12)",
        f" ПРОХОД: {suffix}",
        f" СГЕНЕРИРОВАНЫ ФОРМАТЫ: M3U, M3U8, YML, TXT",
        f" ФИЛЬТР 18+: ЖЁСТКАЯ БЛОКИРОВКА (АКТИВЕН)",
        f" ГРУППА ПЛЕЙЛИСТА: {PLAYLIST_GROUP}",
        "==================================================",
        "--- СИСТЕМНЫЙ ЖУРНАЛ ---"
    ] + sys_logs + [
        "",
        "--- ДЕТАЛИЗАЦИЯ И ОБРАБОТКА ПОТОКОВ ---"
    ] + process_logs

    full_log_content = "\n".join(log_lines) + "\n"

    for folder in TARGET_FOLDERS:
        os.makedirs(folder, exist_ok=True)

        for ext in EXTENSIONS:
            filename = f"{BASE_NAME}{suffix}{ext}"
            file_path = os.path.join(folder, filename)
            
            if ext == ".txt":
                content = full_log_content
            elif ext == ".yml":
                content = yml_content
            else:
                content = m3u_content

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        for ext in EXTENSIONS:
            src = os.path.join(folder, f"{BASE_NAME}{suffix}{ext}")
            dst = os.path.join(folder, f"{BASE_NAME}{ext}")
            shutil.copyfile(src, dst)

if __name__ == "__main__":
    process_pass(1)
