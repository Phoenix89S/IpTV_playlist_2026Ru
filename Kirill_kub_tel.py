import concurrent.futures
import os
import re
import ssl
import time
import urllib.request
from datetime import datetime, timezone, timedelta

# Создаем контекст для игнорирования SSL-ошибок
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Московское время (UTC+3)
MSK = timezone(timedelta(hours=3))

def get_msk_time():
    now = datetime.now(MSK)
    return now.strftime("%H:%M:%S.%f")[:-3]

def get_current_date():
    now = datetime.now(MSK)
    return now.strftime("%Y-%m-%d_%H-%M-%S")

def get_next_filename(base_name, extension):
    filename = f"{base_name}.{extension}"
    if not os.path.exists(filename):
        return filename

    counter = 1
    while True:
        filename = f"{base_name}_{counter}.{extension}"
        if not os.path.exists(filename):
            return filename
        counter += 1

class DregLogger:
    def __init__(self, log_filename):
        self.log_filename = log_filename
        with open(self.log_filename, "w", encoding="utf-8") as f:
            f.write(f"=== ЛОГ-ОТЧЕТ СКАЛА / ДРЕГ [{get_current_date()}] ===\n\n")

    def log(self, stage_num, sub_msg):
        timestamp = get_msk_time()
        log_line = f"[{timestamp}] [ДРЕГ-ЭТАП {stage_num}] {sub_msg}"
        print(log_line)
        with open(self.log_filename, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")

    def log_system(self, message):
        timestamp = get_msk_time()
        log_line = f"[{timestamp}] [СИСТЕМА] {message}"
        print(log_line)
        with open(self.log_filename, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")

def fetch_stream_metadata_and_metrics(url, timeout=3):
    """
    Глубокий анализ потока (Ока-анализ):
    - Выхватывает имя, tvg-id, сдвиг прямо из потока/заголовков.
    - Измеряет скорость отклика/загрузки и оценивает качество потока (байт/мс).
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HlsWinkPlayer",
        "Connection": "keep-alive"
    }
    meta = {
        "name": None,
        "tvg_id": None,
        "shift": None,
        "speed_kbps": 0.0,
        "quality_score": "Низкое"
    }
    
    start_time = time.time()
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            # Анализ ICY заголовков
            icy_name = response.headers.get("icy-name")
            if icy_name:
                meta["name"] = icy_name.strip()

            # Чтение порции данных для метрик качества и поиска метаданных внутри потока
            chunk = response.read(16384)
            elapsed = time.time() - start_time
            
            if elapsed > 0:
                # Расчет скорости (Ока-метрик): кБ/с
                meta["speed_kbps"] = round((len(chunk) / 1024) / elapsed, 2)
                if meta["speed_kbps'] > 50:
                    meta["quality_score"] = "Высокое (HD/FHD)"
                elif meta["speed_kbps"] > 15:
                    meta["quality_score"] = "Стабильное (SD)"
                else:
                    meta["quality_score"] = "Низкое / Зажатый битрейт"

            text_chunk = chunk.decode("utf-8", errors="ignore")

            if not meta["name"]:
                match_name = re.search(r'tvg-name="([^"]+)"', text_chunk)
                if match_name:
                    meta["name"] = match_name.group(1)

            match_id = re.search(r'tvg-id="([^"]+)"', text_chunk)
            if match_id:
                meta["tvg_id"] = match_id.group(1)

            match_shift = re.search(r'(?:shift|utc|plus)[=_\s]*([+-]?\d+)', text_chunk, re.IGNORECASE)
            if match_shift:
                meta["shift"] = match_shift.group(1)
    except Exception:
        pass
        
    return meta

def process_channel_metadata(i, logger):
    url_http = f"http://cdn.kubteltv.workers.dev/?ID={i}"
    url_https = f"https://cdn.kubteltv.workers.dev/?ID={i}"

    logger.log(3, f"ID канала {i}: сканирование потока, замеры скорости (Ока) и качества...")
    
    data = fetch_stream_metadata_and_metrics(url_http)
    if not data["name"]:
        data = fetch_stream_metadata_and_metrics(url_https)

    channel_name = data["name"] if data["name"] else f"Канал {i}"
    tvg_id = data["tvg_id"] if data["tvg_id"] else f"ch_{i}"
    shift = data["shift"]
    speed = data["speed_kbps"]
    quality = data["quality_score"]

    logger.log(4, f"ID {i} выхвачено -> Имя: '{channel_name}', ID: '{tvg_id}', Сдвиг: '{shift}', Скорость: {speed} кБ/с, Качество: {quality}")
    return i, channel_name, tvg_id, shift, speed, quality

def generate_m3u():
    log_file_name = get_next_filename("Kub_kirill", "txt")
    m3u_file_name = get_next_filename("Kub_kirill", "m3u")
    m3u8_file_name = get_next_filename("Kub_kirill", "m3u8")

    logger = DregLogger(log_file_name)
    total_channels = 450

    logger.log_system("================================================================================")
    logger.log_system("СКАЛА / ДРЕГ v12.9.11.4.124.ai.Dyatlov A.S. ver — СТАРТ СКАНИРОВАНИЯ ПОТОКОВ")
    logger.log_system("================================================================================")
    logger.log_system(f"Диапазон: 1 - {total_channels} | Лог: {log_file_name} | Плейлисты: {m3u_file_name}, {m3u8_file_name}")

    logger.log_system("ЭТАП 1: Инициализация массива каналов. Поток воркеров: 20.")
    channels_data = ["" for _ in range(total_channels)]

    logger.log_system("ЭТАП 3: Запуск глубокого анализа потоков, выхватывания имен, EPG, Ока-скорости и качества...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(process_channel_metadata, i, logger): i for i in range(1, total_channels + 1)}

        scanned_results = {}
        for future in concurrent.futures.as_completed(futures):
            i, name, tvg_id, shift, speed, quality = future.result()
            scanned_results[i] = {
                "name": name, 
                "tvg_id": tvg_id, 
                "shift": shift, 
                "speed": speed, 
                "quality": quality
            }

    logger.log_system("ЭТАП 4: Анализ завершен. Все параметры и метрики качества зафиксированы.")
    logger.log_system("ЭТАП 5: Генерация расширенных тегов плейлиста с учетом Ока-скорости...")

    for i in range(1, total_channels + 1):
        d = scanned_results.get(i, {})
        channel_name = d.get("name", f"Канал {i}")
        tvg_id = d.get("tvg_id", f"ch_{i}")
        shift = d.get("shift")
        speed = d.get("speed", 0.0)
        quality = d.get("quality", "Низкое")

        shift_suffix = f" (+{shift})" if shift and int(shift) > 0 else (f" ({shift})" if shift else "")
        full_channel_name = f"{channel_name}{shift_suffix}"

        url_http = f"http://cdn.kubteltv.workers.dev/?ID={i}"
        url_https = f"https://cdn.kubteltv.workers.dev/?ID={i}"

        logger.log(5, f"Запись ID {i}: [{full_channel_name}] | Скорость: {speed} кБ/с | Качество: {quality}")

        extinf_http = (
            f'#EXTINF:-1 tvg-id="{tvg_id}" '
            f'tvg-name="{channel_name}" '
            f'tvg-logo="https://iptvx.one/picons/channel{i}.png" '
            f'tvg-chno="{i}" '
            f'group-title="КубТел ТВ (Ока: {speed} кБ/с | {quality})",{i}. {full_channel_name}'
        )

        extinf_https = (
            f'#EXTINF:-1 tvg-id="{tvg_id}" '
            f'tvg-name="{channel_name}" '
            f'tvg-logo="https://iptvx.one/picons/channel{i}.png" '
            f'tvg-chno="{i}" '
            f'group-title="КубТел ТВ (Ока: {speed} кБ/с | {quality})",{i}. {full_channel_name}'
        )

        block = (
            f"{extinf_http}\n"
            f"#EXTVLCOPT:http-protocol=1.1\n"
            f"{url_http}\n\n"
            f"{extinf_https}\n"
            f"#EXTVLCOPT:http-protocol=1.2\n"
            f"{url_https}\n\n"
        )
        channels_data[i - 1] = block

    logger.log_system("ЭТАП 2: Запись и сохранение файлов...")

    playlist_content = "#EXTM3U\n\n" + "".join(channels_data) + "# --------------------------------------------------------------------\n"

    with open(m3u_file_name, "w", encoding="utf-8") as f:
        f.write(playlist_content)
    logger.log(2, f"Сохранен плейлист: {m3u_file_name}")

    with open(m3u8_file_name, "w", encoding="utf-8") as f:
        f.write(playlist_content)
    logger.log(2, f"Сохранен плейлист: {m3u8_file_name}")

    logger.log_system("================================================================================")
    logger.log_system(f"[СКАЛА] Скрипт успешно отработал. Лог-файл: {log_file_name}")
    logger.log_system("================================================================================")

if __name__ == "__main__":
    generate_m3u()
