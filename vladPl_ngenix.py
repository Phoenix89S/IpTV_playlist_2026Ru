import re
import urllib.request


def convert_playlist(url: str, output_file: str = "wink_playlist.m3u8"):
    # 1. Архив на 14 дней (336 часов)
    CATCHUP_ATTRS = (
        'catchup="append" catchup-days="14" '
        'catchup-source="?offset=-${offset}&utcstart=${timestamp}"'
    )

    # 2. Заголовки Wink
    VLCOPTS = [
        "#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Linux; Android 12; WinkTV 1.88.1; ru-RU) Gecko/20100101 Firefox/117.0",
        "#EXTVLCOPT:http-referer=https://wink.ru/",
        "#EXTVLCOPT:http-header=X-Requested-With=Wink",
        "#EXTVLCOPT:http-header=X-Forwarded-For: 95.24.0.1",
    ]

    # 3. Источники EPG + Глубина архива/анонса EPG (14 дней в обе стороны)
    EPG_URLS = "https://iptvx.one/EPG,https://epg.itv.uz/teleguide.xml.gz"
    DEFAULT_HEADER = (
        f'#EXTM3U url-tvg="{EPG_URLS}" '
        'tvg-days-past="14" tvg-days-future="14"'
    )

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

                attr_dict = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs_raw))

                formatted_attrs = [CATCHUP_ATTRS]

                # Приоритетные атрибуты
                for key in ["group-title", "tvg-id", "tvg-logo", "tvg-name"]:
                    if key in attr_dict:
                        formatted_attrs.append(f'{key}="{attr_dict[key]}"')

                # Остальные атрибуты
                for key, val in attr_dict.items():
                    if key not in [
                        "group-title",
                        "tvg-id",
                        "tvg-logo",
                        "tvg-name",
                        "catchup",
                        "catchup-days",
                        "catchup-source",
                    ]:
                        formatted_attrs.append(f'{key}="{val}"')

                attrs_str = " ".join(formatted_attrs)
                current_extinf = f"#EXTINF:{duration} {attrs_str},{name}"
            else:
                current_extinf = line

        elif not line.startswith("#"):
            if current_extinf:
                output_lines.append(current_extinf)
                output_lines.extend(VLCOPTS)
                output_lines.append(line)
                current_extinf = None

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"Готово! Сохранено в файл: {output_file}")


if __name__ == "__main__":
    PLAYLIST_URL = "https://gist.githubusercontent.com/icehack3/55a5c448261ccb20b1471c297713af7f/raw/ebbaa9b362273e5ef7d7053cc14e258f98eb0665/zmp.m3u8"
    convert_playlist(PLAYLIST_URL)
