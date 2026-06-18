import subprocess
import requests

PLAYLIST = "test_channels.m3u"
SOURCE_URL = f"https://raw.githubusercontent.com/Phoenix89S/iptv_Ru2026/main/{PLAYLIST}"

print("Скачиваю test_channels.m3u из старого репозитория...")
data = requests.get(SOURCE_URL).text

# сохраняем файл в main (он нужен для коммита)
with open(PLAYLIST, "w", encoding="utf-8") as f:
    f.write(data)

print("Плейлист скачан. Переключаюсь на gh-pages...")

# переключаемся на gh-pages
subprocess.run(["git", "fetch"], check=True)
subprocess.run(["git", "checkout", "gh-pages"], check=True)

print("Записываю файл в ветку gh-pages...")
# просто перезаписываем файл в gh-pages
with open(PLAYLIST, "w", encoding="utf-8") as f:
    f.write(data)

print("Коммичу изменения...")
subprocess.run(["git", "add", PLAYLIST], check=True)
subprocess.run(["git", "commit", "-m", "Обновление test_channels.m3u"], check=False)

print("Пушу в gh-pages...")
subprocess.run(["git", "push", "origin", "gh-pages"], check=True)

print("Готово. test_channels.m3u перенесён в gh-pages.")