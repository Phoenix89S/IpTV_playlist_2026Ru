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

# ----------------------------------------------------------------------
# Фильтр русскоязычных каналов
# ----------------------------------------------------------------------

RU_KEYWORDS = [
    "россия", "россия 1", "россия 24", "ртр", "ртр-планета",
    "первый", "1 канал", "мир", "мир 24",
    "нтв", "рен", "рен тв", "тнт", "стс", "дом кино",
    "пятница", "твц", "карусель", "матч", "матч!", "матч тв",
    "кино", "музыка", "спорт", "беларусь", "минск",
    "казахстан", "viju", "euronews"
]

RU_DOMAINS = [".ru/", ".su/", ".by/", ".kz/"]

def is_russian_title(title: str) -> bool:
    return bool(re.search(r'[\u0400-\u04FF]', title))

def is_russian_keyword(title: str) -> bool:
    low = title.lower()
    return any(k in low for k in RU_KEYWORDS)

def is_russian_domain(url: str) -> bool:
    low = url.lower()
    return any(dom in low for dom in RU_DOMAINS)

def is_russian_channel(channel):
    title = channel['title']
    group = channel['group']
    url = channel['url']

    if is_russian_title(title):
        return True
    if is_russian_keyword(title):
        return True
    if is_russian_domain(url):
        return True
    
    group_low = group.lower()
    if any(tag in group_low for tag in ["ru", "rus", "россия", "беларусь", "казахстан", "снг", "cis"]):
        return True

    return False

# ----------------------------------------------------------------------
# Загрузка GitVerse
# ----------------------------------------------------------------------

def fetch_content(url):
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
    except Exception:
        return None


def fetch_single_task(item):
    part_idx, base_filename = item

    for ext in [".m3u", ".M3U"]:
        url = f"{BASE_RAW_URL}{base_filename}{ext}"
        content = fetch_content(url)
        if content:
            channels = parse_m3u(content)
            return part_idx, channels

    return part_idx, None


def fetch_all_channels_single_pass():
    all_channels = []

    tasks = [(0, "IPTV_MEGA_PLAYLIST")]

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

    for idx in sorted(results.keys()):
        all_channels.extend(results[idx])

    return all_channels

# ----------------------------------------------------------------------
# Парсер M3U
# ----------------------------------------------------------------------

def parse_m3u(content):
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
    parts = header.split(',', 1)
    if len(parts) > 1:
        return parts[1].strip()
    return header.strip()


def extract_group_title(header):
    match = re.search(r'group-title="([^"]+)"', header, re.IGNORECASE)
    return match.group(1) if match else ""

# ----------------------------------------------------------------------
# Фильтр 18+
# ----------------------------------------------------------------------

def is_adult(channel):
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

# ----------------------------------------------------------------------
# Сортировка
# ----------------------------------------------------------------------

def sort_key(channel):
    title = channel['title']
    first_char = title[0] if title else ''

    if re.match(r'[\u0400-\u04FF]', first_char):
        group_priority = 0
    elif re.match(r'[a-zA-Z]', first_char):
        group_priority = 1
    else:
        group_priority = 2

    return (group_priority, title.lower())

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    all_channels = fetch_all_channels_single_pass()
    print(f"Всего загружено каналов: {len(all_channels)}")

    # 1. Удаляем 18+
    clean_channels = []
    adult_count = 0

    for ch in all_channels:
        if is_adult(ch):
            adult_count += 1
        else:
            clean_channels.append(ch)

    print(f"Исключено каналов категории 18+: {adult_count}")
    print(f"Осталось каналов после фильтра 18+: {len(clean_channels)}")

    # 2. Оставляем только русскоязычные
    russian_channels = []
    non_ru_count = 0

    for ch in clean_channels:
        if is_russian_channel(ch):
            russian_channels.append(ch)
        else:
            non_ru_count += 1

    print(f"Исключено НЕ русскоязычных каналов: {non_ru_count}")
    print(f"Русскоязычных каналов осталось: {len(russian_channels)}")

    # 3. Сортировка (А-Я -> A-Z)
    sorted_channels = sorted(russian_channels, key=sort_key)

    # 4. Сохранение итогового файла Gitver.m3u
    with open("Gitver.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in sorted_channels:
            f.write(f"{ch['header']}\n")
            for opt in ch['opts']:
                f.write(f"{opt}\n")
            f.write(f"{ch['url']}\n")

    print("Файл Gitver.m3u успешно сформирован!")


if __name__ == "__main__":
    main()
