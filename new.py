import subprocess
import sys

BRANCH = "gh-pages-3"
FILENAME = "urls.txt"
RAW_URL = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/gh-pages-3/urls.txt"

def run(cmd):
    print(">>", cmd)
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"Ошибка выполнения: {cmd}")
        sys.exit(1)

# 1. Создаём orphan-ветку
run(f"git checkout --orphan {BRANCH}")

# 2. Удаляем все файлы из индекса
run("git rm -rf .")

# 3. Создаём urls.txt с сырой ссылкой
with open(FILENAME, "w", encoding="utf-8") as f:
    f.write(RAW_URL)

# 4. Добавляем файл
run(f"git add {FILENAME}")

# 5. Коммитим
run('git commit -m "Initial empty branch with raw self-link"')

# 6. Пушим ветку
run(f"git push origin {BRANCH}")

print(f"Готово: ветка {BRANCH} создана, файл {FILENAME} добавлен.")
print(f"Содержимое файла: {RAW_URL}")