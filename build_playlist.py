import re
import urllib.request

# Имя пользователя и репозитория на GitHub
GITHUB_USER = "YOUR_USERNAME"
REPO_NAME = "YOUR_REPO"
BRANCH = "main"

BASE_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{REPO_NAME}/{BRANCH}/"

# 1. Черный список по group-title (регистр не имеет значения)
ADULT_GROUPS = [
    r'18\+', r'adult', r'xxx', r'porn', r'эротика', 
    r'для взрослых', r'hot', r'sex', r'erotic', r'ночные'
]

# 2. Черный список слов в названии канала (#EXTINF)
ADULT_TITLE_KEYWORDS = [
    r'\b18\+\b', r'\badult\b', r'\bxxx\b', r'porn', r'эротик', 
    r'erotic', r'playboy', r'hustler', r'brazzers', r'penthouse', 
    r'redlight', r'dorcel', r'candyman', r'sct', r'privat', r'vixen',
    r'exxx', r'barely legal', r'sex'
]

# 3. Точечный черный список (конкретные tvg-id или названия для ручного бана)
MANUAL_BLACK_LIST = [
    # "some-adult-tvg-id",
    # "Конкретное Название Канала"
]

def fetch_content(url):
    """Скачивание содержимого файла по URL."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception:
        return None

def get_all_playlist_urls():
    """Формирует список URL: основной плейлист + динамический поиск всех part_XX.m3u."""
    urls = [f"{BASE_RAW_URL}IPTV_MEGA_PLAYLIST.m3u"]
    
    part_idx = 1
    while True:
        part_name = f"IPTV_MEGA_PLAYLIST_part_{part_idx:02d}.m3u"
        url = f"{BASE_RAW_URL}{part_name}"
        
        content = fetch_content(url)
        if content is not None:
            urls.append(url)
            part_idx += 1
        else:
            break
            
    return urls

def parse_m3u(content):
    """Разбор M3U файла с сохранением метаданных, тегов EXTVLCOPT и URL."""
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
    """Проверка канала на принадлежность к категории 18+."""
    header = channel['header']
    title = channel['title']
    group = channel['group']
    
    # Проверка group-title
    for pattern in ADULT_GROUPS:
        if re.search(pattern, group, re.IGNORECASE):
            return True
            
    # Проверка названия канала
    for pattern in ADULT_TITLE_KEYWORDS:
        if re.search(pattern, title, re.IGNORECASE):
            return True
            
    # Проверка ручного черного списка
    for banned in MANUAL_BLACK_LIST:
        if banned.lower() in header.lower() or banned.lower() in title.lower():
            return True
            
    return False

def sort_key(channel):
    """
    Ключ сортировки:
    Сначала Кириллица (А-Я), затем Латиница (A-Z), затем все остальные символы.
    """
    title = channel['title']
    first_char = title[0] if title else ''
    
    if re.match(r'[\u0400-\u04FF]', first_char):
        group_priority = 0  # Кириллица
    elif re.match(r'[a-zA-Z]', first_char):
        group_priority = 1  # Латиница
    else:
        group_priority = 2  # Цифры и спецсимволы
        
    return (group_priority, title.lower())

def main():
    all_channels = []
    playlist_urls = get_all_playlist_urls()
    
    print(f"Найдено источников для скачивания: {len(playlist_urls)}")
    
    # 1. Скачивание всех частей
    for url in playlist_urls:
        print(f"Загрузка: {url}")
        content = fetch_content(url)
        if content:
            parsed = parse_m3u(content)
            all_channels.extend(parsed)

    print(f"Всего загружено каналов: {len(all_channels)}")

    # 2. Исключение каналов 18+ (дубликаты обычных каналов НЕ удаляются)
    clean_channels = []
    adult_count = 0
    
    for ch in all_channels:
        if is_adult(ch):
            adult_count += 1
        else:
            clean_channels.append(ch)

    print(f"Исключено каналов категории 18+: {adult_count}")
    print(f"Осталось чистых каналов: {len(clean_channels)}")

    # 3. Сортировка (А-Я -> A-Z)
    sorted_channels = sorted(clean_channels, key=sort_key)

    # 4. Сохранение итогового playlist.m3u
    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in sorted_channels:
            f.write(f"{ch['header']}\n")
            for opt in ch['opts']:
                f.write(f"{opt}\n")
            f.write(f"{ch['url']}\n")

    print("Итоговый playlist.m3u успешно сформирован!")

if __name__ == "__main__":
    main()
