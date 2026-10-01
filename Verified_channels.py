import requests
import re

# ============================================================
#              CINERAMA STREAM8 → STREAM1
# ============================================================

SOURCE_URL = (
    "https://raw.githubusercontent.com/IPTVRU2026/IPTVMIR/"
    "refs/heads/main/IPTV_MEGA_PLAYLIST.m3u"
)

OUTPUT_FILE = "CINERAMA_VERIFIED.m3u"

OLD_HOST = "stream8.cinerama.uz"
NEW_HOST = "stream1.cinerama.uz"
NEW_GROUP = "Verified channels"


def replace_group_title(extinf):
    """
    Меняет ТОЛЬКО значение group-title.
    Остальная строка #EXTINF остаётся без изменений.
    """

    pattern = r'group-title="[^"]*"'

    if re.search(pattern, extinf, flags=re.IGNORECASE):
        return re.sub(
            pattern,
            f'group-title="{NEW_GROUP}"',
            extinf,
            count=1,
            flags=re.IGNORECASE
        )

    # Если group-title отсутствует — добавляем его
    comma_pos = extinf.find(",")

    if comma_pos != -1:
        return (
            extinf[:comma_pos]
            + f' group-title="{NEW_GROUP}"'
            + extinf[comma_pos:]
        )

    return extinf


def main():

    print("==============================================")
    print("     CINERAMA STREAM8 → STREAM1")
    print("==============================================")
    print()
    print("[INFO] Загружаем исходный M3U...")

    response = requests.get(
        SOURCE_URL,
        timeout=60,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    lines = response.text.splitlines()

    result = ["#EXTM3U"]

    found = 0

    i = 0

    while i < len(lines):

        line = lines[i].strip()

        # Ищем URL stream8.cinerama.uz
        if OLD_HOST in line:

            # Для корректной пары EXTINF + URL
            if i > 0 and lines[i - 1].strip().startswith("#EXTINF:"):

                extinf = lines[i - 1].strip()

                # Меняем только группу
                extinf = replace_group_title(extinf)

                # Меняем только hostname
                new_url = line.replace(
                    OLD_HOST,
                    NEW_HOST
                )

                # Сохраняем пару EXTINF + URL
                result.append(extinf)
                result.append(new_url)

                found += 1

        i += 1

    # Записываем результат
    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="\n"
    ) as f:
        f.write("\n".join(result))
        f.write("\n")

    print()
    print("==============================================")
    print("[ OK ] ОБРАБОТКА ЗАВЕРШЕНА")
    print("==============================================")
    print()
    print(f"Найдено Cinerama потоков : {found}")
    print(f"Старая адресация         : {OLD_HOST}")
    print(f"Новая адресация          : {NEW_HOST}")
    print(f"Новая группа             : {NEW_GROUP}")
    print()
    print(f"Файл создан: {OUTPUT_FILE}")
    print()


if __name__ == "__main__":
    main()