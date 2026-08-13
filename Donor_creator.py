import datetime
import os
import re
import subprocess
import requests


def get_msk_time_skala():
    """Время по МСК для режима СКАЛА (ЧЧ:ММ.СС)."""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    return now.strftime("%H:%M.%S")


def get_msk_date_skala():
    """Дата по МСК (ДД.ММ.ГГГГ)."""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    return now.strftime("%d.%m.%Y")


def get_msk_time_dreg():
    """Точное время по МСК для режима ДРЭГ (ЧЧ:ММ:СС:МСМСМС)."""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    ms = now.microsecond // 1000
    return f"{now.strftime('%H:%M:%S')}:{ms:03d}"


class TeletypeLogger:
    """Класс безаварийного логирования с дублированием в файлы."""

    def __init__(self):
        self.logs = []

    def log(self, text=""):
        print(text)
        self.logs.append(text)

    def save_to_file(self, filepath):
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(self.logs) + "\n")
        except Exception as e:
            print(f"[ОШИБКА ЗАПИСИ ЛОГА {filepath}]: {e}")


def safe_commit_and_push(logger, target_dir, commit_message):
    """Безопасный коммит с проверкой наличия .git."""
    if not os.path.isdir(".git"):
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Git-репозиторий не найден. Пропуск коммита для '{target_dir}'."
        )
        return False

    try:
        # Для корневой директории передаем "." в git add
        add_path = "." if target_dir == "root" else target_dir
        subprocess.run(
            ["git", "add", add_path], check=True, capture_output=True
        )
        res = subprocess.run(
            ["git", "commit", "-m", commit_message],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            logger.log(
                f"{get_msk_time_skala()} [СКАЛА] [GIT] Изменения зафиксированы для '{target_dir}'."
            )
        else:
            logger.log(
                f"{get_msk_time_skala()} [СКАЛА] [GIT] Изменений для '{target_dir}' не обнаружено."
            )
        return True
    except Exception as e:
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] [GIT ОШИБКА] Не удалось выполнить коммит для '{target_dir}': {e}"
        )
        return False


# База эталонных сопоставлений системных ID Ngenix -> Официальное название Wink
WINK_SERVER_NAME_MAP = {
    "CH_1TV": "Первый канал",
    "CH_1TVHD": "Первый канал HD",
    "CH_RUSSIA1": "Россия 1",
    "CH_RUSSIA1HD": "Россия 1 HD",
    "CH_MATCHTV": "Матч!",
    "CH_MATCHTVHD": "Матч! HD",
    "CH_NTV": "НТВ",
    "CH_NTVHD": "НТВ HD",
    "CH_5TV": "Пятый Канал",
    "CH_RUSSIAK": "Россия К",
    "CH_RUSSIA24": "Россия 24",
    "CH_KARUSEL": "Карусель",
    "CH_OTR": "ОТР",
    "CH_TVC": "ТВ Центр",
    "CH_RENTV": "РЕН ТВ",
    "CH_SPAS": "Спас",
    "CH_STS": "СТС",
    "CH_DOMASHNIY": "Домашний",
    "CH_DOMASHNY_2": "Домашний (+2)",
    "CH_TV3": "ТВ-3",
    "CH_FRIDAY": "Пятница!",
    "CH_ZVEZDA": "Звезда",
    "CH_MIR": "МИР",
    "CH_TNT": "ТНТ",
    "CH_MUZTV": "Муз ТВ",
    "CH_VIASATHISTORY": "Viasat History",
    "CH_DISCOVERY": "Discovery Channel",
}


def validate_and_resolve_stream_name(stream_url, current_name, session):
    """Опрашивает сервер Ngenix/Wink, вытаскивает идентификатор потока и сверяет имя."""
    server_status = "UNKNOWN"
    server_stream_id = None

    # Извлекаем системный ID канала из URL (например, CH_MATCHTV)
    match = re.search(r"/hls/([^/]+)/", stream_url)
    if match:
        server_stream_id = match.group(1).upper()

    # Опрос сервера Ngenix методом HEAD для проверки отклика
    try:
        headers = {"User-Agent": "HlsWinkPlayer"}
        resp = session.head(stream_url, headers=headers, timeout=5)
        server_status = f"HTTP {resp.status_code}"
    except Exception:
        server_status = "TIMEOUT/OFFLINE"

    # Определение эталонного имени по ответу/ID сервера
    expected_name = None
    if server_stream_id and server_stream_id in WINK_SERVER_NAME_MAP:
        expected_name = WINK_SERVER_NAME_MAP[server_stream_id]

    # Сравнение имеющегося имени и сервера
    if expected_name:
        if current_name.strip().lower() == expected_name.strip().lower():
            return {
                "status": "VALID",
                "server_status": server_status,
                "server_id": server_stream_id,
                "server_name": expected_name,
                "final_name": current_name,
                "modified": False,
            }
        else:
            return {
                "status": "CORRECTED",
                "server_status": server_status,
                "server_id": server_stream_id,
                "server_name": expected_name,
                "final_name": expected_name,
                "modified": True,
            }

    return {
        "status": "PASSED",
        "server_status": server_status,
        "server_id": server_stream_id or "N/A",
        "server_name": current_name,
        "final_name": current_name,
        "modified": False,
    }


def run_pipeline_dreg_skala():
    logger = TeletypeLogger()
    session = requests.Session()

    wink_url = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/wink_playlist.m3u8"
    donor_url = "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/donor89s.m3u"

    dir_main = "main"
    dir_output = "output"

    os.makedirs(dir_main, exist_ok=True)
    os.makedirs(dir_output, exist_ok=True)

    # Пути отчетов: main/, output/ и корневой каталог
    report_files = [
        os.path.join(dir_main, "report_dreg_skala.txt"),
        os.path.join(dir_output, "report_dreg_skala.txt"),
        "report_dreg_skala.txt",
    ]

    try:
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
        logger.log(
            "РЕЖИМ ИНИЦИАЛИЗАЦИИ: СКАЛА (КРУПНОБЛОЧНЫЙ СКАН + ВАЛИДАЦИЯ ИМЕН NGENIX)"
        )
        logger.log("СТАТУС: ИСПОЛНЕНИЕ / ТЕЛЕТАЙПНЫЙ ВЫВОД (ГАРАНТИРОВАННЫЙ)")
        logger.log(
            "================================================================================"
        )
        logger.log()

        # --- СКАЛА: ЗАГРУЗКА И ДИАГНОСТИКА СЕТИ ---
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Инициализация ядра обработки плейлистов ver 10.10.6.1..."
        )
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Запрошена загрузка плейлистов из GitHub..."
        )

        wink_res = None
        donor_res = None

        try:
            wink_res = session.get(wink_url, timeout=15)
            donor_res = session.get(donor_url, timeout=15)
        except Exception as net_err:
            logger.log(
                f"{get_msk_time_skala()} [СКАЛА] ОШИБКА СЕТЕВОГО СОЕДИНЕНИЯ: {net_err}"
            )
            return

        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Ответ Wink URL: Status HTTP {wink_res.status_code}"
        )
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Ответ Donor URL: Status HTTP {donor_res.status_code}"
        )

        if wink_res.status_code != 200 or donor_res.status_code != 200:
            logger.log(
                f"{get_msk_time_skala()} [СКАЛА] ОШИБКА ДОСТУПА К РЕПОЗИТОРИЮ (Код не равен 200). ОПЕРАЦИЯ ПРЕКРАЩЕНА."
            )
            return

        wink_lines = wink_res.text.splitlines()
        donor_lines = donor_res.text.splitlines()

        original_header = "#EXTM3U"
        for line in wink_lines:
            if line.startswith("#EXTM3U"):
                original_header = line.strip()
                break

        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Фиксация глобальной шапки: {original_header[:60]}..."
        )
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Включение микропроцессинга ДРЭГ с опросом серверов Ngenix."
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

        # --- ДРЭГ: ЗАЩИЩЕННЫЙ ПАРСИНГ КАНАЛОВ И ОПРОС СЕРВЕРОВ ---
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
                logger.log(
                    f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Инициализация захвата..."
                )

                if not stream_url:
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] ОШИБКА ПАРСИНГА: Не найден URL потока!"
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] ПОТОК ПРОПУЩЕН.\n"
                    )
                    i += 1
                    continue

                parts = extinf.rsplit(",", 1)
                raw_source_name = (
                    parts[1].strip() if len(parts) > 1 else "Канал"
                )
                clean_source_name = re.sub(
                    r"^\d+[\.\s\-]+", "", raw_source_name
                )

                # Фильтрация 18+
                is_adult = any(
                    kw in extinf.lower() for kw in adult_keywords
                ) or any(kw in stream_url.lower() for kw in adult_keywords)

                if is_adult:
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Поток: {clean_source_name} | Выявление 18+..."
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] СРАБОТКА ФИЛЬТРА 18+ (Adult Content Detected)."
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] ПОТОК ИСКЛЮЧЕН ИЗ ФИНАЛЬНОЙ СБОРКИ.\n"
                    )
                else:
                    cdn_note = ""
                    if "ott.service.ip-tv.ru" in stream_url:
                        cdn_note = " | CDN заменен -> zabava-htlive.cdn.ngenix.net"
                        stream_url = stream_url.replace(
                            "ott.service.ip-tv.ru",
                            "zabava-htlive.cdn.ngenix.net",
                        )

                    # Валидация именования через опрос Ngenix
                    val_result = validate_and_resolve_stream_name(
                        stream_url, clean_source_name, session
                    )
                    final_channel_name = val_result["final_name"]

                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Опрос Ngenix... Статус: [{val_result['server_status']}] | Stream ID: [{val_result['server_id']}]"
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Имя в источнике: '{clean_source_name}' | Ответ сервера: '{val_result['server_name']}'"
                    )

                    if val_result["modified"]:
                        logger.log(
                            f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] ДЕЙСТВИЕ: [ИСПРАВЛЕНО] -> Скорректировано на '{final_channel_name}'"
                        )
                    else:
                        logger.log(
                            f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] ДЕЙСТВИЕ: [НЕ ТРОГАЛИ] -> Имя верное"
                        )

                    if not extvlc:
                        extvlc = "#EXTVLCOPT:http-user-agent=HlsWinkPlayer"

                    channels.append(
                        {
                            "extinf": extinf,
                            "extvlc": extvlc,
                            "url": stream_url,
                            "clean_name": final_channel_name,
                        }
                    )

                    current_idx = len(channels)
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Фильтр 18+: Чисто | Группа: 'Винк /забава'{cdn_note}"
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Архив: catchup-days=\"3\" (flussonic)"
                    )
                    logger.log(
                        f"{get_msk_time_dreg()} [ДРЭГ] [{ch_code}] Назначен номер: {current_idx}. {final_channel_name}\n"
                    )

                i = j if j < len(wink_lines) else i + 1
            else:
                i += 1

        # --- СКАЛА: ФИНАЛЬНАЯ СБОРКА И НУМЕРАЦИЯ ---
        logger.log(
            "--------------------------------------------------------------------------------"
        )
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Завершение микропроцессинга ДРЭГ и валидации."
        )
        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] Успешно обработано каналов: {len(channels)}. Сквозная нумерация от 1 до {len(channels)}."
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
                extinf = re.sub(
                    r'catchup-days="[^"]*"', 'catchup-days="3"', extinf
                )
            else:
                extinf = re.sub(
                    r'group-title="Винк /забава"',
                    'group-title="Винк /забава" catchup-days="3" catchup-type="flussonic"',
                    extinf,
                )

            parts = extinf.rsplit(",", 1)
            new_extinf = f"{parts[0]},{index}. {ch['clean_name']}"

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

        # Сохранение плейлистов в main/, output/ и корень
        playlist_files = [
            os.path.join(dir_main, "donor89s_updated.m3u"),
            os.path.join(dir_output, "donor89s_updated.m3u"),
            "donor89s_updated.m3u",
        ]

        for p_file in playlist_files:
            with open(p_file, "w", encoding="utf-8") as f:
                f.write(final_playlist)
            logger.log(
                f"{get_msk_time_skala()} [СКАЛА] Записан плейлист: {p_file}"
            )

        logger.log(
            f"{get_msk_time_skala()} [СКАЛА] СБОРКА И ИНТЕГРАЦИЯ УСПЕШНО СОВЕРШЕНЫ."
        )
        logger.log(
            "================================================================================"
        )

        # Вызовы Git-коммитов для main/, output/ и КОРНЕВОЙ директории (root)
        commit_msg = f"Авто-обновление ДРЭГ/СКАЛА - {start_date} {start_time_skala}"
        safe_commit_and_push(logger, dir_main, f"[MAIN] {commit_msg}")
        safe_commit_and_push(logger, dir_output, f"[OUTPUT] {commit_msg}")
        safe_commit_and_push(logger, "root", f"[ROOT] {commit_msg}")

    except Exception as fatal_err:
        logger.log(
            f"\n{get_msk_time_skala()} [КРИТИЧЕСКАЯ ОШИБКА ВЫПОЛНЕНИЯ]: {fatal_err}"
        )

    finally:
        # БЛОК FINALLY: Запись отчета в TXT во все 3 локации в любом случае
        for r_file in report_files:
            logger.save_to_file(r_file)
        print(
            f"\n[СИСТЕМА] Телетайпный отчёт сгенерирован и сохранён в: {', '.join(report_files)}"
        )


if __name__ == "__main__":
    run_pipeline_dreg_skala()
