import re
import urllib.request


def convert_playlist(url: str, output_file: str = "wink_playlist.m3u8"):
    # Настройки catchup и user-agent
    CATCHUP_ATTRS = (
        'catchup="append" catchup-days="3" '
        'catchup-source="?offset=-${offset}&utcstart=${timestamp}"'
    )
    USER_AGENT_LINE = "#EXTVLCOPT:http-user-agent=HlsWinkPlayer"
    DEFAULT_HEADER = '#EXTM3U url-tvg="https://iptvx.one/EPG"'

    print(f"Загрузка плейлиста из {url}...")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    try:
        with urllib.request.urlopen(req) as response:
            content = response.read().decode("utf-8")
    except Exception as e:
        print(f"Ошибка при загрузке: {e}")
        return

    lines = [line.strip() for line in content.splitlines() if line.strip()]

    output_lines = [DEFAULT_HEADER]
    current_extinf = None

    # Регулярное выражение для разбора строки #EXTINF
    extinf_pattern = re.compile(
        r"^#EXTINF:(?P<duration>-?\d+)\s*(?P<attrs>.*?),(?P<name>.*)$"
    )

    for line in lines:
        if line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXTINF:"):
            match = extinf_pattern.match(line)
            if match:
                duration = match.group("duration")
                attrs_raw = match.group("attrs")
                name = match.group("name").strip()

                # Извлекаем существующие теги (group-title, tvg-id, tvg-logo и т.д.)
                attr_dict = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs_raw))

                # Пересобираем атрибуты
                formatted_attrs = []

                # Добавляем catchup атрибуты
                formatted_attrs.append(CATCHUP_ATTRS)

                # Сохраняем остальные ключевые атрибуты, если они есть
                for key in ["group-title", "tvg-id", "tvg-logo"]:
                    if key in attr_dict:
                        formatted_attrs.append(f'{key}="{attr_dict[key]}"')

                # Если есть другие атрибуты из оригинала, сохраняем их
                for key, val in attr_dict.items():
                    if key not in ["group-title", "tvg-id", "tvg-logo", "catchup", "catchup-days", "catchup-source"]:
                        formatted_attrs.append(f'{key}="{val}"')

                attrs_str = " ".join(formatted_attrs)
                current_extinf = f"#EXTINF:{duration} {attrs_str},{name}"
            else:
                current_extinf = line

        elif not line.startswith("#"):
            # Строка со ссылкой на поток
            if current_extinf:
                output_lines.append(current_extinf)
                output_lines.append(USER_AGENT_LINE)
                output_lines.append(line)
                current_extinf = None

    # Сохранение в файл
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"Готово! Сохранено в файл: {output_file}")


if __name__ == "__main__":
    PLAYLIST_URL = "https://gist.githubusercontent.com/icehack3/55a5c448261ccb20b1471c297713af7f/raw/ebbaa9b362273e5ef7d7053cc14e258f98eb0665/zmp.m3u8"
    convert_playlist(PLAYLIST_URL)
