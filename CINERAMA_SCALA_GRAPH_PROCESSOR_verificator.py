import logging
import sys
from collections import Counter
from datetime import datetime


# ============================================================================
# CINERAMA SCALA GRAPH PROCESSOR
# Расширенная обработка M3U с логированием
# ============================================================================

LOG_FILE = "playlist_log.txt"
OUTPUT_FILE = "Extra_channels2026_verified.m3u"


# ============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# Двойной вывод: консоль + playlist_log.txt
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_FILE,
            encoding="utf-8"
        )
    ]
)

logger = logging.getLogger("CineramaScalaGraph")


# ============================================================================
# ПРАВИЛА ЗАМЕНЫ HOST
# ============================================================================

CINERAMA_HOST_REPLACEMENTS = {
    "https://stream8.cinerama.uz": "https://stream1.cinerama.uz",
}


# ============================================================================
# СТАТИСТИКА
# ============================================================================

stats = {
    "total_lines": 0,
    "empty_lines": 0,
    "comment_lines": 0,
    "url_lines": 0,
    "changed_lines": 0,
    "unchanged_url_lines": 0,
    "matched_rules": 0,
    "errors": 0,
}


replacement_counter = Counter()


# ============================================================================
# ОБРАБОТКА ОДНОГО URL
# ============================================================================

def process_node_scala_style(url: str) -> str:
    """
    Обрабатывает URL по принципу направленного графа.

    Проверяет URL на соответствие правилам замены
    и при совпадении меняет только HOST.

    Пример:

    https://stream8.cinerama.uz/1009/tracks-v1a1/playlist.m3u8

    -->

    https://stream1.cinerama.uz/1009/tracks-v1a1/playlist.m3u8
    """

    original_url = url.strip()
    target_url = original_url

    logger.info(
        "[NODE START / УЗЕЛ START]\n"
        f"  • Получен URL / Empfangene URL:\n"
        f"    {original_url}"
    )

    # ------------------------------------------------------------------------
    # Проверяем все правила графа
    # ------------------------------------------------------------------------

    for old_host, new_host in CINERAMA_HOST_REPLACEMENTS.items():

        logger.debug(
            "[GRAPH EDGE CHECK / ПРОВЕРКА РЕБРА]\n"
            f"  • Старый узел / Alter Knoten: {old_host}\n"
            f"  • Новый узел / Neuer Knoten: {new_host}"
        )

        if original_url.startswith(old_host):

            applied_rule = f"{old_host} -> {new_host}"

            # Меняем только первый HOST
            target_url = original_url.replace(
                old_host,
                new_host,
                1
            )

            stats["matched_rules"] += 1
            replacement_counter[applied_rule] += 1

            logger.info(
                "\n"
                "[GRAPH TRANSFORMATION / ТРАНСФОРМАЦИЯ ГРАФА]\n"
                f"  • Было / Ursprung:\n"
                f"    {original_url}\n"
                f"  • Старый узел / Alter Knoten:\n"
                f"    {old_host}\n"
                f"  • Правило / Regel:\n"
                f"    {applied_rule}\n"
                f"  • Новый узел / Neuer Knoten:\n"
                f"    {new_host}\n"
                f"  • Стало / Ziel:\n"
                f"    {target_url}\n"
                f"  • Статус / Status:\n"
                f"    УСПЕШНО ЗАМЕНЕНО / ERFOLGREICH ERSETZT"
            )

            return target_url

    # ------------------------------------------------------------------------
    # Правило не найдено
    # ------------------------------------------------------------------------

    stats["unchanged_url_lines"] += 1

    logger.info(
        "[NODE UNCHANGED / УЗЕЛ БЕЗ ИЗМЕНЕНИЙ]\n"
        f"  • URL:\n"
        f"    {original_url}\n"
        f"  • Причина / Grund:\n"
        f"    Подходящее правило не найдено / "
        f"Keine passende Regel gefunden"
    )

    return target_url


# ============================================================================
# ОБРАБОТКА ПЛЕЙЛИСТА
# ============================================================================

def process_playlist_graph(input_text: str) -> str:

    lines = input_text.splitlines()
    processed_lines = []

    logger.info("")
    logger.info("=" * 100)
    logger.info(
        "НАЧАЛО ОБРАБОТКИ ПЛЕЙЛИСТА / "
        "START DER PLAYLIST-VERARBEITUNG"
    )
    logger.info(
        f"Время запуска / Startzeit: "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    logger.info(
        f"Всего строк / Gesamtzeilen: {len(lines)}"
    )
    logger.info(
        f"Выходной файл / Ausgabedatei: {OUTPUT_FILE}"
    )
    logger.info("=" * 100)

    for line_number, line in enumerate(lines, start=1):

        stats["total_lines"] += 1

        stripped = line.strip()

        # ====================================================================
        # ПУСТАЯ СТРОКА
        # ====================================================================

        if not stripped:

            stats["empty_lines"] += 1

            logger.debug(
                f"[LINE {line_number}] "
                "Пустая строка / Leere Zeile"
            )

            processed_lines.append(line)
            continue

        # ====================================================================
        # M3U-КОММЕНТАРИЙ
        # ====================================================================

        if stripped.startswith("#"):

            stats["comment_lines"] += 1

            # Не выводим в INFO каждый комментарий,
            # чтобы лог не разрастался слишком сильно.
            logger.debug(
                f"[LINE {line_number}] "
                f"Комментарий / Kommentar: {stripped}"
            )

            processed_lines.append(line)
            continue

        # ====================================================================
        # URL ПОТОКА
        # ====================================================================

        stats["url_lines"] += 1

        logger.info("")
        logger.info(
            f"[LINE {line_number}] "
            "ОБНАРУЖЕН URL ПОТОКА / STREAM-URL GEFUNDEN"
        )
        logger.info(
            f"  ORIGINAL:\n"
            f"    {stripped}"
        )

        try:

            new_line = process_node_scala_style(stripped)

            # ================================================================
            # URL ИЗМЕНЁН
            # ================================================================

            if new_line != stripped:

                stats["changed_lines"] += 1

                logger.info(
                    f"[LINE {line_number}] "
                    "СТРОКА ИЗМЕНЕНА / ZEILE GEÄNDERT"
                )

                logger.info(
                    f"  OLD / БЫЛО:\n"
                    f"    {stripped}"
                )

                logger.info(
                    f"  NEW / СТАЛО:\n"
                    f"    {new_line}"
                )

            # ================================================================
            # URL НЕ ИЗМЕНЁН
            # ================================================================

            else:

                logger.info(
                    f"[LINE {line_number}] "
                    "БЕЗ ИЗМЕНЕНИЙ / UNVERÄNDERT"
                )

            processed_lines.append(new_line)

        except Exception as exc:

            stats["errors"] += 1

            logger.exception(
                f"[LINE {line_number}] "
                "ОШИБКА ОБРАБОТКИ / VERARBEITUNGSFEHLER:\n"
                f"  {exc}"
            )

            # При ошибке сохраняем исходную строку.
            processed_lines.append(line)

    # =========================================================================
    # ФОРМИРОВАНИЕ VERIFIED PLAYLIST
    # =========================================================================

    result = "\n".join(processed_lines)

    # -------------------------------------------------------------------------
    # Добавляем Verified в заголовок M3U.
    #
    # Если #PLAYLIST уже существует, заменяем его.
    # Если его нет — добавляем после #EXTM3U.
    # -------------------------------------------------------------------------

    result_lines = result.splitlines()

    playlist_header_found = False
    final_lines = []

    for index, line in enumerate(result_lines):

        stripped = line.strip()

        if stripped.startswith("#PLAYLIST:"):

            final_lines.append(
                "#PLAYLIST:Extra Channels 2026 - Verified"
            )

            playlist_header_found = True

        else:

            final_lines.append(line)

        # Если это #EXTM3U и #PLAYLIST ещё не существует,
        # добавляем Verified сразу после #EXTM3U.
        if (
            stripped == "#EXTM3U"
            and not playlist_header_found
        ):

            final_lines.append(
                "#PLAYLIST:Extra Channels 2026 - Verified"
            )

            playlist_header_found = True

    result = "\n".join(final_lines)

    # =========================================================================
    # ФИНАЛЬНАЯ СТАТИСТИКА
    # =========================================================================

    logger.info("")
    logger.info("=" * 100)
    logger.info(
        "ИТОГ ОБРАБОТКИ / VERARBEITUNGSERGEBNIS"
    )
    logger.info("=" * 100)

    logger.info(
        f"Всего строк / Gesamtzeilen: "
        f"{stats['total_lines']}"
    )

    logger.info(
        f"Пустых строк / Leere Zeilen: "
        f"{stats['empty_lines']}"
    )

    logger.info(
        f"Комментариев M3U / M3U-Kommentare: "
        f"{stats['comment_lines']}"
    )

    logger.info(
        f"URL потоков / Stream-URLs: "
        f"{stats['url_lines']}"
    )

    logger.info(
        f"Изменено / Geändert: "
        f"{stats['changed_lines']}"
    )

    logger.info(
        f"Без изменений / Unverändert: "
        f"{stats['unchanged_url_lines']}"
    )

    logger.info(
        f"Срабатываний правил / Regel-Treffer: "
        f"{stats['matched_rules']}"
    )

    logger.info(
        f"Ошибок / Fehler: "
        f"{stats['errors']}"
    )

    # =========================================================================
    # СТАТИСТИКА ЗАМЕН
    # =========================================================================

    logger.info("")
    logger.info(
        "СТАТИСТИКА ПРАВИЛ / REGELSTATISTIK"
    )

    if replacement_counter:

        for rule, count in replacement_counter.items():

            logger.info(
                f"  • {rule}\n"
                f"    Срабатываний / Treffer: {count}"
            )

    else:

        logger.info(
            "  • Правила замены не срабатывали / "
            "Keine Ersetzungsregeln ausgelöst"
        )

    logger.info("=" * 100)

    return result


# ============================================================================
# СОХРАНЕНИЕ VERIFIED ПЛЕЙЛИСТА
# ============================================================================

def save_verified_playlist(content: str) -> bool:

    try:

        with open(
            OUTPUT_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(content)

        logger.info("")
        logger.info("=" * 100)
        logger.info(
            "VERIFIED PLAYLIST CREATED"
        )
        logger.info(
            f"Файл / Datei:\n"
            f"  {OUTPUT_FILE}"
        )
        logger.info(
            "Статус / Status: VERIFIED"
        )
        logger.info("=" * 100)

        return True

    except Exception as exc:

        logger.exception(
            "ОШИБКА СОХРАНЕНИЯ / "
            "FEHLER BEIM SPEICHERN:\n"
            f"  {exc}"
        )

        stats["errors"] += 1

        return False


# ============================================================================
# ЗАПУСК
# ============================================================================

if __name__ == "__main__":

    logger.info("")
    logger.info("#" * 100)
    logger.info(
        "CINERAMA SCALA GRAPH PROCESSOR"
    )
    logger.info(
        "Расширенное логирование / Erweitertes Logging"
    )
    logger.info(
        "Режим: HOST REPLACEMENT + VERIFIED OUTPUT"
    )
    logger.info(
        f"Log: {LOG_FILE}"
    )
    logger.info(
        f"Output: {OUTPUT_FILE}"
    )
    logger.info("#" * 100)

    # =========================================================================
    # ТЕСТОВЫЙ M3U
    # =========================================================================

    sample_m3u = """#EXTM3U
#EXTINF:-1,Channel 1
https://stream8.cinerama.uz/1009/tracks-v1a1/playlist.m3u8
#EXTINF:-1,Channel 2
https://someotherstream.com/live/mono.m3u8
#EXTINF:-1,Channel 3
https://stream8.cinerama.uz/2001/tracks-v1a1/playlist.m3u8"""

    # =========================================================================
    # ОБРАБОТКА
    # =========================================================================

    try:

        result = process_playlist_graph(
            sample_m3u
        )

        # =====================================================================
        # СОХРАНЕНИЕ
        # =====================================================================

        save_verified_playlist(result)

    except Exception as exc:

        logger.exception(
            ""
            "КРИТИЧЕСКАЯ ОШИБКА / "
            "KRITISCHER FEHLER:\n"
            f"{exc}"
        )

    logger.info("")
    logger.info(
        "ОБРАБОТКА ПОЛНОСТЬЮ ЗАВЕРШЕНА / "
        "VERARBEITUNG VOLLSTÄNDIG ABGESCHLOSSEN"
    )
    logger.info(
        f"Лог / Log: {LOG_FILE}"
    )
    logger.info(
        f"Плейлист / Playlist: {OUTPUT_FILE}"
    )