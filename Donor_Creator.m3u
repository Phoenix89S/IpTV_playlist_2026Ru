import datetime
import os
import re
import subprocess
import time
import requests


def get_msk_time_skala():
    # Формат времени СКАЛА: ЧЧ:ММ.СС
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    return now.strftime("%H:%M.%S")


def get_msk_date_skala():
    # Дата по МСК
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    return now.strftime("%d.%m.%Y")


def get_msk_time_dreg():
    # Формат времени ДРЭГ: ЧЧ:ММ:СС:МСМСМС
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    ms = now.microsecond // 1000
    return f"{now.strftime('%H:%M:%S')}:{ms:03d}"


class TeletypeLogger:
    """Класс для одновременного вывода в консоль и записи отчёта в TXT-файл."""

    def __init__(self):
        self.logs = []

    def log(self, text=""):
        print(text)
        self.logs.append(text)

    def save_to_file(self, filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(self.logs) + "\n")


def commit_and_push(target_dir, commit_message):
    """Функция автоматического коммита и отправки изменений в Git (если используется git-репозиторий)."""
    try:
        if os.path.exists(os.path.join(target_dir, ".git")) or os.path.exists(
            ".git"
        ):
            subprocess.run(
                ["git", "add", target_dir], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", commit_message],
                check=False,
                capture_output=True,
            )
            # subprocess.run(["git", "push"], check=False, capture_output=True)
            return True
    except Exception as e:
        return False
    return False


def run_pipeline_dreg_skala():
    logger = TeletypeLogger()

    wink_url = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/wink_playlist.m3u8"
    donor_url = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/donor89s.m3u"

    # Директории назначения для дублирования результатов и коммитов
    dir_main = "main"
    dir_output = "output"

    os.makedirs(dir_main, exist_ok=True)
    os.makedirs(dir_output, exist_ok=True)

    start_date = get_msk_date_skala()
    start_time_skala = get_msk_time_skala()

    # --- ТЕЛЕТАЙПНАЯ ШАПКА ---
    logger.log(
        "================================================================================"
    )
    logger.log(
        "                    ПРОТОКОЛ / АКТ ТЕХНИЧЕСКОЙ МОДИФИКАЦИИ"
    )
    logger.log(
        "    СИСТЕМНО-СТРУКТУРНАЯ СБОРКА: ДРЭГ / СКАЛА ver 10.10.6.1_IPTV_edition"
    )
    logger.log(
        "================================================================================"
    )
    logger.log(f"ДАТА И ВРЕМЯ (МСК): {start_date} | {start_time_skala}")
    logger.log(
        "ОБЪЕКТ ОБРАБОТКИ: Плейлист Wink / Zabava -> Модуль Интеграции Donor89s"
    )
    logger.log("РЕЖИМ ИНИЦИАЛИЗАЦИИ: СКАЛА (КРУПНОБЛОЧНЫЙ СКАН)")
    logger.log("СТАТУС: ИСПОЛНЕНИЕ / ТЕЛЕТАЙПНЫЙ ВЫВОД")
    logger.log(
        "================================================================================"
    )
    logger.log()

    # --- СКАЛА: СТАРТ ---
    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Инициализация ядра обработки плейлистов ver 10.10.6.1..."
    )
    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Загрузка исходных массивов из GitHub репозиториев..."
    )

    wink_res = requests.get(wink_url)
    donor_res = requests.get(donor_url)

    if wink_res.status_code != 200 or donor_res.status_code != 200:
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] ОШИБКА ДОСТУПА К РЕПОЗИТОРИЮ. ПРЕКРАЩЕНИЕ ОПЕРАЦИИ."
        )
        return

    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Массив wink_playlist.m3u8 получен (HTTP 200 OK)."
    )
    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Массив donor89s.m3u получен (HTTP 200 OK)."
    )

    wink_lines = wink_res.text.splitlines()
    donor_lines = donor_res.text.splitlines()

    # Фиксация строго исходной шапки
    original_header = "#EXTM3U"
    for line in wink_lines:
        if line.startswith("#EXTM3U"):
            original_header = line.strip()
            break

    header_preview = (
        original_header[:60] + "..."
        if len(original_header) > 60
        else original_header
    )
    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Фиксация исходной глобальной шапки: {header_preview}"
    )
    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Переход в поканальный микропроцессинг. Включение режима ДРЭГ."
    )
    logger.log()
    logger.log(
        "--------------------------------------------------------------------------------"
    )
    logger.log(
        "                     РЕЖИМ ДРЭГ: ПОКАНАЛЬНЫЙ ТЕЛЕТАЙПНЫЙ ПРОТОКОЛ"
    )
    logger.log(
        "--------------------------------------------------------------------------------"
    )
    logger.log()

    # Ключевые слова 18+
    adult_keywords = [
        "18+",
        "adult",
        "эротика",
        "erotica",
        "playboy",
        "hustler",
        "brazzers",
        "redlight",
        "penthous",
        "ночной",
        "русская ночь",
        "candy",
        "exxxotica",
        "nuart",
        "эгоист",
        "vixen",
        "blue hustler",
    ]

    channels = []
    i = 0
    raw_count = 0

    # --- ДРЭГ: ПОКАНАЛЬНАЯ ОБРАБОТКА ---
    while i < len(wink_lines):
        line = wink_lines[i].strip()

        if line.startswith("#EXTINF:"):
            raw_count += 1
            extinf = line
            extvlc = ""
            stream_url = ""

            j = i + 1
            while j < len(wink_lines):
                next_line = wink_lines[j].strip()
                if next_line.startswith("#EXTVLCOPT:"):
                    extvlc = next_line
                elif next_line and not next_line.startswith("#"):
                    stream_url = next_line
                    break
                j += 1

            ch_code = f"CH_{raw_count:03d}"

            parts = extinf.rsplit(",", 1)
            ch_name = parts[1].strip() if len(parts) > 1 else "Канал"
            clean_name = re.sub(r"^\d+[\.\s\-]+", "", ch_name)

            logger.log(
                f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Инициализация захвата..."
            )

            if stream_url:
                is_adult = any(
                    kw in extinf.lower() for kw in adult_keywords
                ) or any(kw in stream_url.lower() for kw in adult_keywords)

                if is_adult:
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Поток: {clean_name} | Анализ контента..."
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] СРАБОТКА ФИЛЬТРА 18+ (Adult Keyword Detected)."
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] ПОТОК ИСКЛЮЧЕН ИЗ ФИНАЛЬНОЙ СБОРКИ."
                    )
                    logger.log()
                else:
                    cdn_note = ""
                    if "ott.service.ip-tv.ru" in stream_url:
                        cdn_note = " | CDN устарел: ott.service.ip-tv.ru -> Коррекция -> zabava-htlive.cdn.ngenix.net"
                        stream_url = stream_url.replace(
                            "ott.service.ip-tv.ru",
                            "zabava-htlive.cdn.ngenix.net",
                        )

                    if not extvlc:
                        extvlc = "#EXTVLCOPT:http-user-agent=HlsWinkPlayer"

                    channels.append(
                        {
                            "extinf": extinf,
                            "extvlc": extvlc,
                            "url": stream_url,
                        }
                    )

                    current_idx = len(channels)
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Поток: {clean_name}{cdn_note}"
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Фильтр 18+: Чисто | Назначена группа: \"Винк /забава\""
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Установка индивидуального архива: catchup-days=\"3\" (flussonic)"
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Зафиксирован индекс: {current_idx}. {clean_name}"
                    )
                    logger.log()

            i = j
        i += 1

    # --- СКАЛА: ФИНАЛИЗАЦИЯ И СБОРКА ---
    logger.log(
        "--------------------------------------------------------------------------------"
    )
    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Завершение микропроцессинга ДРЭГ. Выход в режим СКАЛА."
    )
    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Формирование итогового блока \"Винк /забава\" (Всего каналов: {len(channels)})."
    )

    formatted_block = [
        "#---------------- ГРУППА: Винк /забава ----------------"
    ]

    for index, ch in enumerate(channels, 1):
        extinf = ch["extinf"]

        if 'group-title="' in extinf:
            extinf = re.sub(
                r'group-title="[^"]*"', 'group-title="Винк /забава"', extinf
            )
        else:
            extinf = re.sub(
                r"(#EXTINF:-1)", r'\1 group-title="Винк /забава"', extinf
            )

        if 'catchup-days="' in extinf:
            extinf = re.sub(r'catchup-days="[^"]*"', 'catchup-days="3"', extinf)
        else:
            extinf = re.sub(
                r'group-title="Винк /забава"',
                'group-title="Винк /забава" catchup-days="3" catchup-type="flussonic"',
                extinf,
            )

        parts = extinf.rsplit(",", 1)
        channel_name = parts[1].strip() if len(parts) > 1 else "Канал"
        clean_name = re.sub(r"^\d+[\.\s\-]+", "", channel_name)

        new_extinf = f"{parts[0]},{index}. {clean_name}"

        formatted_block.append(new_extinf)
        if ch["extvlc"]:
            formatted_block.append(ch["extvlc"])
        formatted_block.append(ch["url"])
        formatted_block.append("")

    new_wink_section = "\n".join(formatted_block)

    donor_content = "\n".join(donor_lines)
    donor_body = re.sub(r"^#EXTM3U[^\n]*\n?", "", donor_content).strip()

    if "#---------------- ГРУППА: Винк /забава ----------------" in donor_body:
        pattern = r"#---------------- ГРУППА: Винк /забава ----------------.*?(?=#----------------|$)"
        updated_body = re.sub(
            pattern, new_wink_section + "\n\n", donor_body, flags=re.DOTALL
        )
    else:
        updated_body = donor_body + "\n\n" + new_wink_section

    final_playlist = f"{original_header}\n\n{updated_body.strip()}\n"

    # --- ЗАПИСЬ В ОБЕ ДИРЕКТОРИИ (main И output) ---
    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] Интеграция блока в массив donor89s.m3u..."
    )

    playlist_files = [
        os.path.join(dir_main, "donor89s_updated.m3u"),
        os.path.join(dir_output, "donor89s_updated.m3u"),
    ]

    for p_file in playlist_files:
        with open(p_file, "w", encoding="utf-8") as f:
            f.write(final_playlist)
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Сохранен плейлист: {p_file}"
        )

    # Запись текстового отчёта
    report_files = [
        os.path.join(dir_main, "report_dreg_skala.txt"),
        os.path.join(dir_output, "report_dreg_skala.txt"),
    ]

    logger.log(
        f"{get_msk_time_skala()} [СКАЛА] СБОРКА УСПЕШНО ЗАВЕРШЕНА. ИНИЦИАЛИЗАЦИЯ КОММИТА..."
    )
    logger.log(
        "================================================================================"
    )

    for r_file in report_files:
        logger.save_to_file(r_file)

    # Инициализация процедур коммита
    commit_msg = f"Auto-update Wink/Zabava group (v10.10.6.1_IPTV) - {start_date} {start_time_skala}"
    commit_and_push(dir_main, f"[MAIN] {commit_msg}")
    commit_and_push(dir_output, f"[OUTPUT] {commit_msg}")


if __name__ == "__main__":
    run_pipeline_dreg_skala()
