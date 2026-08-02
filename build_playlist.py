import re
import requests
from github import Github

# 1. Список ссылок для скачивания
URLS = [
    "https://gitverse.ru/RUVIPIEN/IPTVMIR/content/main/IPTV_MEGA_PLAYLIST.m3u",
    "https://gitverse.ru/RUVIPIEN/IPTVMIR/content/main/IPTV_MEGA_PLAYLIST_part_01.m3u",
    "https://gitverse.ru/RUVIPIEN/IPTVMIR/content/main/IPTV_MEGA_PLAYLIST_part_02.m3u",
]

# 2. Настройки GitHub для коммита
GITHUB_TOKEN = "ВАШ_GITHUB_PERSONAL_ACCESS_TOKEN"
REPO_NAME = "ваш_username/ваш_репозиторий"
TARGET_FILE_PATH = "playlists/russian_and_hit_hd.m3u"  # Путь к файлу в репозитории
COMMIT_MESSAGE = "Update Russian IPTV channels and Hit HD (filtered 18+)"

# Ключевые слова для поиска русскоязычных групп/каналов
RU_KEYWORDS = ["ru", "rus", "russia", "рус", "россия", "первый", "россия 1", "нтв", "стс", "тнт"]

# Черный список для блокировки 18+ контента
ADULT_KEYWORDS = [
    "18+", "adult", "xxx", "erotic", "эротика", "для взрослых", 
    "brazzers", "hustler", "playboy", "penthouse", "redlight", 
    "candy", "exxxotica", "dorcel", "nuart", "vivid", "blue hustler"
]


def is_adult_channel(text):
    """Проверка, относится ли канал к категории 18+."""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in ADULT_KEYWORDS)


def parse_and_filter(urls):
    unique_channels = {}  # Ключ: URL потока, Значение: кортеж (extinf, extvlcopt, url)

    for url in urls:
        print(f"Скачивание: {url}...")
        try:
            resp = requests.get(url, timeout=30)
            resp.encoding = 'utf-8'
            lines = resp.text.splitlines()
        except Exception as e:
            print(f"Ошибка загрузки {url}: {e}")
            continue

        current_extinf = ""
        current_opts = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("#EXTINF:"):
                current_extinf = line
                current_opts = []
            elif line.startswith("#EXTVLCOPT:") or line.startswith("#EXTGRP:"):
                current_opts.append(line)
            elif not line.startswith("#"):
                stream_url = line

                # Проверка на 18+ (если взрослая категория/тег — сразу пропускаем)
                full_metadata = f"{current_extinf} {' '.join(current_opts)}"
                if is_adult_channel(full_metadata):
                    current_extinf = ""
                    current_opts = []
                    continue

                # Проверка условий: русскоязычный канал ИЛИ Hit HD
                extinf_lower = current_extinf.lower()
                is_hit_hd = "hit hd" in extinf_lower
                is_russian = any(kw in extinf_lower for kw in RU_KEYWORDS) or "group-title=\"ru" in extinf_lower or "group-title=\"рус" in extinf_lower

                if (is_russian or is_hit_hd) and current_extinf:
                    # Сохраняем без дубликатов по ссылке
                    unique_channels[stream_url] = (current_extinf, current_opts, stream_url)

                current_extinf = ""
                current_opts = []

    # Сборка итогового плейлиста
    output_lines = ["#EXTM3U\n"]
    for stream_url, (extinf, opts, url_path) in unique_channels.items():
        output_lines.append(extinf)
        for opt in opts:
            output_lines.append(opt)
        output_lines.append(url_path)

    return "\n".join(output_lines)


def commit_to_github(content):
    print("Подключение к GitHub...")
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)

    try:
        # Проверяем, существует ли уже файл в репозитории
        contents = repo.get_contents(TARGET_FILE_PATH)
        repo.update_file(
            path=TARGET_FILE_PATH,
            message=COMMIT_MESSAGE,
            content=content,
            sha=contents.sha
        )
        print(f"Файл {TARGET_FILE_PATH} успешно обновлен!")
    except Exception:
        # Если файла нет — создаем новый
        repo.create_file(
            path=TARGET_FILE_PATH,
            message=COMMIT_MESSAGE,
            content=content
        )
        print(f"Файл {TARGET_FILE_PATH} успешно создан!")


if __name__ == "__main__":
    playlist_data = parse_and_filter(URLS)
    if playlist_data:
        commit_to_github(playlist_data)
    else:
        print("Не найдено подходящих каналов.")
