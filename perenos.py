import subprocess
import requests
import base64

OWNER = "Phoenix89S"
REPO = "Iptv_Ru2026"  # ВАЖНО: с большой I
FILE_PATH = "test_channels.m3u"
BRANCH = "main"

API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}?ref={BRANCH}"

# GitHub token, который мы сохранили в YML
TOKEN_PATH = "/home/runner/work/_temp/_github_token"
with open(TOKEN_PATH, "r") as f:
    TOKEN = f.read().strip()

headers = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

print("Скачиваю test_channels.m3u из приватного репозитория...")

response = requests.get(API_URL, headers=headers)

if response.status_code != 200:
    print("Ошибка скачивания:", response.text)
    raise SystemExit("Не удалось скачать файл из приватного репо")

data_json = response.json()
file_content = base64.b64decode(data_json["content"]).decode("utf-8")

with open(FILE_PATH, "w", encoding="utf-8") as f:
    f.write(file_content)

print("Файл скачан. Переключаюсь на gh-pages...")

subprocess.run(["git", "fetch"], check=True)
subprocess.run(["git", "checkout", "gh-pages"], check=True)

with open(FILE_PATH, "w", encoding="utf-8") as f:
    f.write(file_content)

subprocess.run(["git", "add", FILE_PATH], check=True)
subprocess.run(["git", "commit", "-m", "Обновление test_channels.m3u"], check=False)
subprocess.run(["git", "push", "origin", "gh-pages"], check=True)

print("Готово. test_channels.m3u перенесён из PRIVATE → gh-pages.")