import concurrent.futures
import os
import re
import ssl
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
    return now.strftime("%H:%M:%S.%f")[:-3] # Точность до миллисекунд (0006 c)

def get_current_date():
    now = datetime.now(MSK)
    return now.strftime("%Y-%m-%d_%H-%M-%S")

def get_next_filename(base_name, extension):
    """Генерирует имя файла с возрастающим индексом (_1, _2 и т.д.), если файл уже существует"""
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

def fetch_stream_metadata(url, timeout=2):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HlsWinkPlayer",
        "Connection": "keep-alive"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            icy_name = response.headers.get("icy-name")
            if icy_name:
                return icy_name.strip()
            chunk = response.read(4096)
            text_chunk = chunk.decode("utf-8", errors="ignore")
            match = re.search(r'tvg-name="([^"]+)"', text_chunk)
            if match:
                return match.group(1)
    except Exception:
        pass
    return None

def process_channel_metadata(i, logger):
    url_http = f"http://cdn.kubteltv.workers.dev/?ID=357&channel={i}"
    url_https = f"https://cdn.kubteltv.workers.dev/?ID=357&channel={i}"
    
    logger.log(3, f"Поток канала {i}: запуск запроса метаданных (HTTP/HTTPS)")
    detected_name = fetch_stream_metadata(url_http)
    if not detected_name:
        detected_name = fetch_stream_metadata(url_https)
    
    channel_name = detected_name if detected_name else f"Канал {i}"
    logger.log(4, f"Поток канала {i}: метаданные получены -> {channel_name}")
    return i, channel_name

def generate_m3u():
    # Используем базовое имя Kub_kirill с автоинкрементом (_1, _2 и т.д. при повторных запусках)
    log_file_name = get_next_filename("Kub_kirill", "txt")
    m3u_file_name = get_next_filename("Kub_kirill", "m3u")
    m3u8_file_name = get_next_filename("Kub_kirill", "m3u8")

    logger = DregLogger(log_file_name)
    total_channels = 400

    logger.log_system("================================================================================")
    logger.log_system("СКАЛА / ДРЕГ v12.9.11.4.124.ai.Dyatlov A.S. ver — СТАРТ")
    logger.log_system("================================================================================")
    logger.log_system(f"Выходные файлы: Лог -> {log_file_name} | Плейлисты -> {m3u_file_name}, {m3u8_file_name}")

    # ЭТАП 1: Начало выполнения генерации потоков
    logger.log_system("ЭТАП 1: Начало выполнения генерации потоков...")
    channels_data = ["" for _ in range(total_channels)]
    logger.log(1, f"Инициализация массива на {total_channels} каналов. Пул потоков: 20 воркеров.")
    
    # ЭТАП 3: Начало поиска метаданных
    logger.log_system("ЭТАП 3: Начало поиска метаданных и опроса потоков...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(process_channel_metadata, i, logger): i for i in range(1, total_channels + 1)}
        
        channel_names = {}
        for future in concurrent.futures.as_completed(futures):
            i, channel_name = future.result()
            channel_names[i] = channel_name

    # ЭТАП 4: Конец поиска метаданных
    logger.log_system("ЭТАП 4: Конец поиска метаданных. Все потоки опрошены.")
    logger.log(4, "Словарь имён успешно сформирован. Переход к этапу формирования сетки EPG.")

    # ЭТАП 5: Формирование сетки EPG и смещений
    logger.log_system("ЭТАП 5: Формирование сетки EPG и смещений привязки...")
    
    for i in range(1, total_channels + 1):
        channel_name = channel_names.get(i, f"Канал {i}")
        url_http = f"http://cdn.kubteltv.workers.dev/?ID=357&channel={i}"
        url_https = f"https://cdn.kubteltv.workers.dev/?ID=357&channel={i}"

        logger.log(5, f"Канал {i} ({channel_name}): привязка EPG xmltv-id и смещений...")

        extinf_http = (
            f'#EXTINF:-1 tvg-id="ch_{i}" '
            f'tvg-name="{channel_name}" '
            f'tvg-logo="https://iptvx.one/picons/channel{i}.png" '
            f'tvg-chno="{i}" '
            f'group-title="КубТел ТВ (HTTP 1.1)",{i}. {channel_name} (HTTP 1.1)'
        )

        extinf_https = (
            f'#EXTINF:-1 tvg-id="ch_{i}" '
            f'tvg-name="{channel_name}" '
            f'tvg-logo="https://iptvx.one/picons/channel{i}.png" '
            f'tvg-chno="{i}" '
            f'group-title="КубТел ТВ (HTTPS 1.2)",{i}. {channel_name} (HTTPS 1.2)'
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

    # ЭТАП 2: Завершение генерации и запись файлов
    logger.log_system("ЭТАП 2: Завершение выполнения генерации потоков. Запись в файлы плейлистов...")
    
    playlist_content = "#EXTM3U\n\n" + "".join(channels_data) + "# --------------------------------------------------------------------\n"

    # Сохраняем в M3U
    with open(m3u_file_name, "w", encoding="utf-8") as f:
        f.write(playlist_content)
    logger.log(2, f"Плейлист успешно сохранен: {m3u_file_name}")

    # Сохраняем в M3U8
    with open(m3u8_file_name, "w", encoding="utf-8") as f:
        f.write(playlist_content)
    logger.log(2, f"Плейлист успешно сохранен: {m3u8_file_name}")

    logger.log_system("================================================================================")
    logger.log_system(f"[СКАЛА] Работа завершена успешно. Логи записаны в: {log_file_name}")
    logger.log_system("================================================================================")

if __name__ == "__main__":
    generate_m3u()
