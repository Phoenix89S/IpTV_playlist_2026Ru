import os
import requests
import subprocess

# === 1. Скачиваем плейлист из старого репо ===
SOURCE_URL = "https://raw.githubusercontent.com/Phoenix89S/Iptv_Ru2026/main/FullProverka.m3u"
TARGET_FILE = "FullProverka.m3u"

print("Скачиваю плейлист из старого репозитория...")
data = requests.get(SOURCE_URL).text

with open(TARGET_FILE, "w", encoding="utf-8") as f:
    f.write(data)

print("Плейлист скачан и сохранён в main нового репо.")

# === 2. Переключаемся на gh-pages ===
print("Переключаюсь на ветку gh-pages...")
subprocess.run(["git", "checkout", "gh-pages"], check=True)

# === 3. Копируем файл в gh-pages ===
print("Копирую файл в gh-pages...")
subprocess.run(["cp", f"../{TARGET_FILE}", TARGET_FILE], check=False)

# === 4. Коммитим ===
subprocess.run(["git", "add", TARGET_FILE], check=True)
subprocess.run(["git", "commit", "-m", "Обновление плейлиста из main"], check=False)

# === 5. Пушим ===
subprocess.run(["git", "push", "origin", "gh-pages"], check=True)

print("Готово. Плейлист перенесён в gh-pages.")