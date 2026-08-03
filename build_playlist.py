import re
import urllib.request
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

# Устанавливаем тайм-аут на сетевые соединения
socket.setdefaulttimeout(15)

# ----------------------------------------------------------------------
# ИСТОЧНИК: Настройки GitVerse
# ----------------------------------------------------------------------
GITVERSE_USER = "RUVIPIEN"
REPO_NAME = "IPTVMIR"
BRANCH = "main"

# Рабочий формат прямых ссылок GitVerse
BASE_RAW_URL = f"https://gitverse.ru/{GITVERSE_USER}/{REPO_NAME}/content/{BRANCH}/"

# ----------------------------------------------------------------------
# Фильтры контента 18+
# ----------------------------------------------------------------------
ADULT_GROUPS = [
    r'18\+', r'adult', r'xxx', r'porn', r'эротика', 
    r'для взрослых', r'hot', r'sex', r'erotic', r'ночные'
]

ADULT_TITLE_KEYWORDS = [
    r'\b18\+\b', r'\badult\b', r'\bxxx\b', r'porn', r'эротик', 
    r'erotic', r'playboy', r'hustler', r'brazzers', r'penthouse', 
    r'redlight', r'dorcel', r'candyman', r'sct', r'privat', r'vixen',
    r'exxx', r'barely legal', r'sex'
]

MANUAL_BLACK_LIST = []


def fetch_content(url):
    """Однократная загрузка содержимого файла из GitVerse."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            content_type = response.headers.get('Content-Type', '')
            if 'html' in content_type.lower():
                return None
                
            content = response.read().decode('utf-8', errors='ignore')
            
            if "<html" in content.lower() or "<!doctype" in content.lower():
                return None
            if "#EXTINF" not in content and "#EXTM3U" not in content:
                return None
                
            return content
    except Exception as e:
        return None


def fetch_single_task(item):
    """Вспомогательная функция для многопоточной загрузки."""
    part_idx, base_filename = item
    
    # Проверяем оба варианта расширения (.m3u и .M3U)
    for ext in [".m3u", ".M3U"]:
        url = f"{BASE_RAW_URL}{base_filename}{ext}"
        content = fetch_content(url)
        if content:
            channels = parse_m3u(content)
            return part_idx, channels
            
    return part_idx, None


def fetch_all_channels_single_pass():
    """
    Загружает плейлисты с GitVerse за ОДИН проход (в 16 параллельных потоков):
    1. Основной плейлист (IPTV_MEGA_PLAYLIST.m3u / .M3U)
    2. Части по порядку (IPTV_MEGA_PLAYLIST_part_01.m3u, part_02.m3u...),
       пока файлы отдаются успешно.
    """
    all_channels = []

    # Генерируем список имен файлов без расширения (проверка .m3u / .M3U внутри task)
    tasks = [(0, "IPTV_MEGA_PLAYLIST")]
    
    # Запас по количеству частей (до 500)
    MAX_PARTS = 500
    for i in range(1, MAX_PARTS + 1):
        tasks.append((i, f"IPTV_MEGA_PLAYLIST_part_{i:02d}"))

    print(f"Запуск параллельной загрузки {len(tasks)} источников в 16 потоков...")

    results = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(fetch_single_task, task) for task in tasks]
        for future in as_completed(futures):
            part_idx, channels = future.result()
            if channels:
                results[part_idx] = channels
                if part_idx == 0:
                    print(f"Успешно загружен основной плейлист (каналов: {len(channels)})")
                else:
                    print(f"Успешно загружена часть part_{part_idx:02d} (каналов: {len(channels)})")

    if 0 not in results:
        print("Внимание: Основной плейлист не найден или недоступен!")

    # Склеиваем каналы в строгом порядке частей (0, 1, 2, 3...)
    for idx in sorted(results.keys()):
        all_channels.extend(results[idx])

    return all_channels


def parse_m3u(content):
    """Разбор M3U файла с полным сохранением тегов, EXTVLCOPT и ссылок."""
    channels = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            header = line
            opt_lines = []
            i += 1
            while i < len(lines) and lines[i].strip().startswith("#EXT"):
                opt_lines.append(lines[i].strip())
                i += 1
            if i < len(lines):
                stream_url = lines[i].strip()
                if stream_url and not stream_url.startswith("#"):
                    channels.append({
                        'header': header,
                        'opts': opt_lines,
                        'url': stream_url,
                        'title': extract_channel_title(header),
                        'group': extract_group_title(header)
                    })
        i += 1
    return channels


def extract_channel_title(header):
    """Извлечение названия канала из строки #EXTINF."""
    parts = header.split(',', 1)
    if len(parts) > 1:
        return parts[1].strip()
    return header.strip()


def extract_group_title(header):
    """Извлечение group-title из строки #EXTINF."""
    match = re.search(r'group-title="([^"]+)"', header, re.IGNORECASE)
    return match.group(1) if match else ""


def is_adult(channel):
    """Проверка на контент 18+."""
    header = channel['header']
    title = channel['title']
    group = channel['group']

    for pattern in ADULT_GROUPS:
        if re.search(pattern, group, re.IGNORECASE):
            return True

    for pattern in ADULT_TITLE_KEYWORDS:
        if re.search(pattern, title, re.IGNORECASE):
            return True

    for banned in MANUAL_BLACK_LIST:
        if banned.lower() in header.lower() or banned.lower() in title.lower():
            return True

    return False


def sort_key(channel):
    """
    Двухуровневая сортировка:
    1. Кириллица (А-Я)
    2. Латиница (A-Z)
    3. Остальное (цифры, символы)
    """
    title = channel['title']
    first_char = title[0] if title else ''

    if re.match(r'[\u0400-\u04FF]', first_char):
        group_priority = 0
    elif re.match(r'[a-zA-Z]', first_char):
        group_priority = 1
    else:
        group_priority = 2

    return (group_priority, title.lower())


def main():
    # 1. Загрузка всех каналов с GitVerse за 1 проход
    all_channels = fetch_all_channels_single_pass()
    print(f"Всего загружено каналов: {len(all_channels)}")

    # 2. Фильтрация 18+ (дубликаты обычных каналов НЕ удаляются)
    clean_channels = []
    adult_count = 0

    for ch in all_channels:
        if is_adult(ch):
            adult_count += 1
        else:
            clean_channels.append(ch)

    print(f"Исключено каналов категории 18+: {adult_count}")
    print(f"Осталось каналов для сборки: {len(clean_channels)}")

    # 3. Алфавитная сортировка (А-Я -> A-Z)
    sorted_channels = sorted(clean_channels, key=sort_key)

    # 4. Сохранение итогового файла для GitHub
    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in sorted_channels:
            f.write(f"{ch['header']}\n")
            for opt in ch['opts']:
                f.write(f"{opt}\n")
            f.write(f"{ch['url']}\n")

    print("Итоговый playlist.m3u успешно сформирован и готов к коммиту в GitHub!")


if __name__ == "__main__":
    main()
