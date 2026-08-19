# ============================================================
# МОДУЛЬ ПРОВЕРКИ АЛИАСОВ
# ============================================================
#
# Назначение:
#
#   1. Получить названия каналов из пользовательского списка.
#   2. Нормализовать названия.
#   3. Построить набор возможных alias-кандидатов.
#   4. НЕ считать сгенерированный alias доказанным.
#   5. Получить доступный реальный список CDN-каналов,
#      если источник такого списка указан.
#   6. Извлечь из CDN реальные alias.
#   7. Найти реальные варианты потоков:
#
#          /index.m3u8
#          /1/index.m3u8
#          /2/index.m3u8
#          /3/index.m3u8
#          ...
#
#   8. Сопоставить пользовательское название с CDN alias.
#   9. Разделить:
#
#          EXACT
#          NORMALIZED
#          ALIAS
#          DICTIONARY
#          FUZZY
#          UNKNOWN
#
#  10. Хранить предположения отдельно от подтвержденных данных.
#
#  11. Создать человекочитаемый:
#
#          n_Alias.txt
#
#  12. Создать машинный файл:
#
#          n_Alias_ngnorm.txt
#
#      для последующей обработки ngnorm.py.
#
# ВАЖНО:
#
#   "Карусель" -> "karousel"
#
# является подтвержденным CDN alias только тогда,
# когда "karousel" реально обнаружен в источнике CDN
# или реально отвечает при разрешенном CDN probe.
#
# "karusel" является только кандидатом.
#
# Аналогично:
#
#   "Голливуд HD" -> "amc"
#
# не может быть выведено обычной транслитерацией.
#
# Это может быть только внешнее/словарное/обнаруженное
# соответствие.
#
# ============================================================


from __future__ import annotations


import json
import logging
import re
import ssl
import time
import unicodedata


from dataclasses import dataclass, field, asdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# ============================================================
# НАСТРОЙКИ
# ============================================================


CDN_BASE_URL = "https://s70378.cdn.ngenix.net"

DEFAULT_STREAM_FILE = "index.m3u8"

DEFAULT_MAX_VARIANT_NUMBER = 10

DEFAULT_REQUEST_TIMEOUT = 10

DEFAULT_FUZZY_THRESHOLD = 0.78

DEFAULT_HIGH_CONFIDENCE_THRESHOLD = 0.92

DEFAULT_USER_AGENT = (
    "AliasVerificationModule/1.0 "
    "(CDN channel alias verification)"
)

LOG_LEVEL = logging.INFO


# ============================================================
# НАСТРОЙКИ ФАЙЛОВ
# ============================================================


# ------------------------------------------------------------
# Человекочитаемый полный отчет.
#
# Предназначен для просмотра пользователем.
# ------------------------------------------------------------

HUMAN_REPORT_FILENAME = "n_Alias.txt"


# ------------------------------------------------------------
# Машинный отчет для ngnorm.py.
#
# Этот файл не предназначен для красивого просмотра.
# Он содержит строгие поля NAME / ALIAS / URL / STATUS /
# SOURCE / FOUND.
# ------------------------------------------------------------

MACHINE_REPORT_FILENAME = "n_Alias_ngnorm.txt"


# ------------------------------------------------------------
# Источник машинного результата.
# ------------------------------------------------------------

MACHINE_SOURCE = "ALIAS_MODULE"


# ------------------------------------------------------------
# Заголовок секции M3U в человекочитаемом отчете.
# ------------------------------------------------------------

M3U_SECTION_TITLE = "M3U PLAYLIST EDITION"


# ------------------------------------------------------------
# 0.0 означает, что файл записывается построчно
# без искусственной задержки.
#
# При необходимости можно поставить, например:
#
#     0.01
#
# чтобы запись происходила с эффектом телетайпа.
#
# По умолчанию задержки НЕТ, чтобы не замедлять работу.
# ------------------------------------------------------------

TEXT_REPORT_TELETYPE_DELAY = 0.0


# ------------------------------------------------------------
# Если True, после каждой записанной строки выполняется flush().
#
# Это делает запись действительно последовательной:
#
#     строка -> запись -> flush -> следующая строка
#
# Для обычного запуска оставляем True.
# ------------------------------------------------------------

TEXT_REPORT_FLUSH_EACH_LINE = True


# ------------------------------------------------------------
# Машинный файл также записывается построчно.
# ------------------------------------------------------------

MACHINE_REPORT_FLUSH_EACH_LINE = True


# ============================================================
# SSL
# ============================================================


SSL_CONTEXT = ssl.create_default_context()


# ============================================================
# LOGGING
# ============================================================


logging.basicConfig(
    level=LOG_LEVEL,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)


LOGGER = logging.getLogger(
    "alias_verification_module"
)


# ============================================================
# СЛУЖЕБНЫЕ ТАБЛИЦЫ
# ============================================================


RUSSIAN_TRANSLITERATION_TABLE = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "yo",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


# ============================================================
# СЛОВАРЬ ЯВНЫХ СООТВЕТСТВИЙ
# ============================================================
#
# Здесь хранятся только известные соответствия.
#
# Это НЕ список "догадок".
#
# Например:
#
#   Голливуд HD -> amc
#
# если это соответствие действительно подтверждено
# пользователем или надежным источником.
#
# ============================================================


KNOWN_ALIAS_DICTIONARY: Dict[str, Set[str]] = {
    "Карусель": {
        "karousel",
    },

    "Hollywood HD": {
        "amc",
    },

    "Голливуд": {
        "amc",
    },

    "РЕН ТВ": {
        "ren_tv",
    },

    "ТВ-3": {
        "tv_3",
    },

    "Мир": {
        "mir",
    },

    "НТВ Сериал": {
        "ntv_serial",
    },

    "Мир сериала": {
        "mir_seriala",
    },

    "Дом Кино": {
        "dom_kino",
    },

    "FilmBox": {
        "filmbox",
    },

    "AMC": {
        "amc",
    },

    "Sony Turbo": {
        "sony_turbo",
    },

    "NickToons": {
        "nicktoons",
    },

    "Nickelodeon": {
        "nickelodeon",
    },

    "Gulli": {
        "gulli",
    },

    "TiJi": {
        "tiji",
    },

    "Ocean TV": {
        "ocean_tv",
    },

    "RTVi": {
        "rtvi",
    },

    "Ностальгия": {
        "nostalgia",
    },

    "Mezzo": {
        "mezzo",
    },

    "ТНТ Music": {
        "tnt_music",
    },

    "Galaxy": {
        "galaxy",
    },
}


# ============================================================
# СТОП-СЛОВА / ТЕХНИЧЕСКИЕ СУФФИКСЫ
# ============================================================


QUALITY_TOKENS = {
    "hd",
    "sd",
    "fhd",
    "uhd",
    "4k",
    "8k",
}


TECHNICAL_SUFFIXES = {
    "channel",
    "tv",
    "television",
    "online",
    "live",
}


# ============================================================
# DATA CLASS
# ============================================================


@dataclass
class ChannelInput:
    """
    Канал из пользовательского списка.
    """

    display_name: str

    tvg_id: str = ""

    tvg_name: str = ""

    group_title: str = ""

    original_url: str = ""

    source_line: int = 0


@dataclass
class CDNStream:
    """
    Реально обнаруженный поток CDN.
    """

    alias: str

    url: str

    variant: Optional[int]

    source: str = ""

    http_status: Optional[int] = None

    reachable: Optional[bool] = None


@dataclass
class AliasCandidate:
    """
    Предполагаемый alias.

    ВАЖНО:

    candidate != confirmed alias.
    """

    value: str

    reason: str

    score: float = 0.0

    confirmed: bool = False


@dataclass
class AliasMatch:
    """
    Результат сопоставления пользовательского канала
    с CDN alias.
    """

    channel_name: str

    normalized_name: str

    cdn_alias: Optional[str]

    match_type: str

    confidence: float

    reason: str

    candidates: List[AliasCandidate] = field(
        default_factory=list
    )

    streams: List[CDNStream] = field(
        default_factory=list
    )


@dataclass
class CDNInventory:
    """
    Инвентаризация CDN.

    Это то, что реально удалось обнаружить.
    """

    aliases: Set[str] = field(
        default_factory=set
    )

    streams: List[CDNStream] = field(
        default_factory=list
    )

    source_urls: List[str] = field(
        default_factory=list
    )


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================


def normalize_unicode(value: str) -> str:
    """
    Нормализует Unicode.
    """

    if value is None:
        return ""

    value = unicodedata.normalize(
        "NFKC",
        value,
    )

    return value.strip()


def normalize_case(value: str) -> str:
    """
    Приводит строку к нижнему регистру.
    """

    return value.casefold()


def remove_quality_tokens(value: str) -> str:
    """
    Удаляет технические обозначения качества.
    """

    parts = value.split()

    filtered = [
        part
        for part in parts
        if part.casefold() not in QUALITY_TOKENS
    ]

    return " ".join(filtered)


def normalize_separators(value: str) -> str:
    """
    Унифицирует разделители.
    """

    value = value.replace("-", " ")

    value = value.replace("_", " ")

    value = value.replace(".", " ")

    value = value.replace("/", " ")

    value = value.replace("\\", " ")

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def normalize_channel_name(
    value: str,
    remove_quality: bool = False,
) -> str:
    """
    Полная нормализация названия.
    """

    value = normalize_unicode(value)

    value = normalize_case(value)

    if remove_quality:
        value = remove_quality_tokens(value)

    value = normalize_separators(value)

    return value


def normalize_alias(value: str) -> str:
    """
    Нормализует alias CDN.

    Пример:

        KAROUSEL
        Karousel
        karousel

    превращаются в:

        karousel
    """

    value = normalize_unicode(value)

    value = normalize_case(value)

    value = value.strip("/")

    return value


# ============================================================
# ТРАНСЛИТЕРАЦИЯ
# ============================================================


def transliterate_russian(
    value: str,
) -> str:
    """
    Выполняет русскую транслитерацию.

    ВАЖНО:

    результат этой функции является только
    кандидатом alias.

    Он никогда автоматически не становится
    подтвержденным CDN alias.
    """

    value = normalize_unicode(value)

    value = value.casefold()

    return value.translate(
        RUSSIAN_TRANSLITERATION_TABLE
    )


# ============================================================
# ПРЕОБРАЗОВАНИЕ В CDN-STYLE ALIAS
# ============================================================


def cleanup_alias_candidate(
    value: str,
) -> str:
    """
    Преобразует строку в формат, характерный
    для CDN-путей.
    """

    value = normalize_unicode(value)

    value = value.casefold()

    value = re.sub(
        r"[^a-z0-9]+",
        "_",
        value,
    )

    value = re.sub(
        r"_+",
        "_",
        value,
    )

    value = value.strip("_")

    return value


# ============================================================
# ГЕНЕРАТОР ALIAS
# ============================================================


def generate_alias_candidates(
    channel: ChannelInput,
) -> List[AliasCandidate]:
    """
    Генерирует кандидаты alias.

    Ни один результат здесь не считается фактом.
    """

    result: Dict[str, AliasCandidate] = {}

    def add_candidate(
        value: str,
        reason: str,
        score: float,
    ) -> None:

        value = normalize_alias(value)

        if not value:
            return

        if value not in result:
            result[value] = AliasCandidate(
                value=value,
                reason=reason,
                score=score,
            )

            return

        if score > result[value].score:
            result[value].score = score

            result[value].reason = reason

    display_name = channel.display_name

    normalized = normalize_channel_name(
        display_name,
        remove_quality=False,
    )

    normalized_without_quality = normalize_channel_name(
        display_name,
        remove_quality=True,
    )

    transliterated = transliterate_russian(
        display_name,
    )

    transliterated_without_quality = transliterate_russian(
        normalized_without_quality,
    )

    add_candidate(
        cleanup_alias_candidate(normalized),
        "normalized_name",
        0.60,
    )

    add_candidate(
        cleanup_alias_candidate(
            normalized_without_quality
        ),
        "normalized_without_quality",
        0.55,
    )

    add_candidate(
        cleanup_alias_candidate(transliterated),
        "transliteration",
        0.50,
    )

    add_candidate(
        cleanup_alias_candidate(
            transliterated_without_quality
        ),
        "transliteration_without_quality",
        0.48,
    )

    words = normalized_without_quality.split()

    if words:

        add_candidate(
            cleanup_alias_candidate(
                "_".join(words)
            ),
            "word_join",
            0.45,
        )

        add_candidate(
            cleanup_alias_candidate(
                "".join(words)
            ),
            "word_concatenation",
            0.40,
        )

    # --------------------------------------------------------
    # tvg-id
    # --------------------------------------------------------

    if channel.tvg_id:

        add_candidate(
            cleanup_alias_candidate(
                channel.tvg_id
            ),
            "tvg_id",
            0.90,
        )

    # --------------------------------------------------------
    # tvg-name
    # --------------------------------------------------------

    if channel.tvg_name:

        add_candidate(
            cleanup_alias_candidate(
                channel.tvg_name
            ),
            "tvg_name",
            0.70,
        )

    # --------------------------------------------------------
    # Явный словарь
    # --------------------------------------------------------

    dictionary_aliases = KNOWN_ALIAS_DICTIONARY.get(
        display_name,
        set(),
    )

    for alias in dictionary_aliases:

        add_candidate(
            alias,
            "known_dictionary",
            0.99,
        )

    # --------------------------------------------------------
    # Добавляем варианты с техническими суффиксами.
    # --------------------------------------------------------

    base_values = list(result.keys())

    for base in base_values:

        for suffix in TECHNICAL_SUFFIXES:

            add_candidate(
                f"{base}_{suffix}",
                f"technical_suffix:{suffix}",
                0.25,
            )

    return sorted(
        result.values(),
        key=lambda item: (
            -item.score,
            item.value,
        ),
    )


# ============================================================
# СОЗДАНИЕ URL ПО ALIAS
# ============================================================


def build_stream_url(
    base_url: str,
    alias: str,
    variant: Optional[int] = None,
) -> str:
    """
    Строит URL потока.
    """

    base_url = base_url.rstrip("/")

    alias = alias.strip("/")

    if variant is None:

        return (
            f"{base_url}/"
            f"{alias}/"
            f"{DEFAULT_STREAM_FILE}"
        )

    return (
        f"{base_url}/"
        f"{alias}/"
        f"{variant}/"
        f"{DEFAULT_STREAM_FILE}"
    )


# ============================================================
# ПРОВЕРКА URL
# ============================================================


def check_url(
    url: str,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> Tuple[
    Optional[int],
    bool,
    Optional[str],
]:
    """
    Проверяет доступность URL.

    Возвращает:

        HTTP status
        reachable
        error text
    """

    request = Request(
        url,
        method="HEAD",
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
        },
    )

    try:

        with urlopen(
            request,
            timeout=timeout,
            context=SSL_CONTEXT,
        ) as response:

            return (
                getattr(
                    response,
                    "status",
                    None,
                ),
                True,
                None,
            )

    except HTTPError as error:

        # Некоторые CDN плохо относятся к HEAD.
        # Сохраняем статус, но считаем HEAD неуспешным.

        return (
            error.code,
            False,
            str(error),
        )

    except URLError as error:

        return (
            None,
            False,
            str(error),
        )

    except Exception as error:

        return (
            None,
            False,
            str(error),
        )


# ============================================================
# ПРОВЕРКА ALIAS И ЕГО ВАРИАНТОВ
# ============================================================


def discover_stream_variants(
    alias: str,
    base_url: str = CDN_BASE_URL,
    max_variant_number: int = DEFAULT_MAX_VARIANT_NUMBER,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> List[CDNStream]:
    """
    Проверяет:

        alias/index.m3u8

    и:

        alias/1/index.m3u8
        alias/2/index.m3u8
        alias/3/index.m3u8
        ...
        alias/N/index.m3u8
    """

    streams: List[CDNStream] = []

    # --------------------------------------------------------
    # Основной поток.
    # --------------------------------------------------------

    primary_url = build_stream_url(
        base_url=base_url,
        alias=alias,
        variant=None,
    )

    status, reachable, _ = check_url(
        primary_url,
        timeout=timeout,
    )

    streams.append(
        CDNStream(
            alias=alias,
            url=primary_url,
            variant=None,
            source="generated_probe",
            http_status=status,
            reachable=reachable,
        )
    )

    # --------------------------------------------------------
    # Варианты.
    # --------------------------------------------------------

    for variant in range(
        1,
        max_variant_number + 1,
    ):

        url = build_stream_url(
            base_url=base_url,
            alias=alias,
            variant=variant,
        )

        status, reachable, _ = check_url(
            url,
            timeout=timeout,
        )

        streams.append(
            CDNStream(
                alias=alias,
                url=url,
                variant=variant,
                source="generated_probe",
                http_status=status,
                reachable=reachable,
            )
        )

    return streams


# ============================================================
# РАЗБОР M3U
# ============================================================


M3U_EXTINF_PATTERN = re.compile(
    r"#EXTINF:[^,]*(?:,)(.*)$",
    re.IGNORECASE,
)


M3U_ATTRIBUTE_PATTERN = re.compile(
    r'([A-Za-z0-9_-]+)="([^"]*)"'
)


def parse_m3u(
    content: str,
    source_url: str = "",
) -> CDNInventory:
    """
    Разбирает M3U и пытается извлечь alias из URL.

    Важный принцип:

    URL является источником факта.

    Название EXTINF является дополнительной
    информацией.
    """

    inventory = CDNInventory()

    inventory.source_urls.append(
        source_url
    )

    lines = content.splitlines()

    pending_extinf: Optional[str] = None

    for raw_line in lines:

        line = raw_line.strip()

        if not line:
            continue

        if line.upper().startswith(
            "#EXTINF:"
        ):

            pending_extinf = line

            continue

        if line.startswith("#"):
            continue

        if not (
            line.startswith("http://")
            or line.startswith("https://")
        ):
            continue

        parsed = urlparse(line)

        path_parts = [
            part
            for part in parsed.path.split("/")
            if part
        ]

        if len(path_parts) < 2:
            continue

        if path_parts[-1].casefold() != (
            DEFAULT_STREAM_FILE.casefold()
        ):
            continue

        alias = path_parts[-2]

        variant: Optional[int] = None

        if len(path_parts) >= 3:

            possible_variant = path_parts[-2]

            if possible_variant.isdigit():

                variant = int(
                    possible_variant
                )

                if len(path_parts) >= 3:

                    alias = path_parts[-3]

        alias = normalize_alias(
            alias
        )

        if not alias:
            continue

        inventory.aliases.add(
            alias
        )

        inventory.streams.append(
            CDNStream(
                alias=alias,
                url=line,
                variant=variant,
                source=source_url,
                reachable=True,
            )
        )

        pending_extinf = None

    return inventory


# ============================================================
# ЗАГРУЗКА M3U
# ============================================================


def fetch_text(
    url: str,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> str:
    """
    Загружает текстовый источник.

    Используется только если пользователь указал
    доступный источник каталога/плейлиста.
    """

    request = Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": (
                "application/vnd.apple.mpegurl,"
                "application/x-mpegURL,"
                "audio/mpegurl,"
                "text/plain,"
                "*/*"
            ),
        },
    )

    with urlopen(
        request,
        timeout=timeout,
        context=SSL_CONTEXT,
    ) as response:

        data = response.read()

    return data.decode(
        "utf-8",
        errors="replace",
    )


def load_m3u_inventory(
    playlist_url: str,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> CDNInventory:
    """
    Загружает доступный M3U и строит inventory.
    """

    LOGGER.info(
        "Загрузка CDN inventory: %s",
        playlist_url,
    )

    content = fetch_text(
        playlist_url,
        timeout=timeout,
    )

    return parse_m3u(
        content=content,
        source_url=playlist_url,
    )


# ============================================================
# СРАВНЕНИЕ СТРОК
# ============================================================


def similarity(
    left: str,
    right: str,
) -> float:
    """
    Сравнивает две строки.
    """

    left = normalize_alias(left)

    right = normalize_alias(right)

    if not left or not right:
        return 0.0

    if left == right:
        return 1.0

    return SequenceMatcher(
        None,
        left,
        right,
    ).ratio()


# ============================================================
# ПОЛУЧЕНИЕ СТРОКОВЫХ ФОРМ
# ============================================================


def build_comparison_forms(
    value: str,
) -> Set[str]:
    """
    Создаёт несколько форм для сопоставления.
    """

    forms: Set[str] = set()

    normalized = normalize_channel_name(
        value,
        remove_quality=False,
    )

    normalized_no_quality = normalize_channel_name(
        value,
        remove_quality=True,
    )

    transliterated = transliterate_russian(
        value
    )

    transliterated_no_quality = transliterate_russian(
        normalized_no_quality
    )

    forms.add(
        cleanup_alias_candidate(
            normalized
        )
    )

    forms.add(
        cleanup_alias_candidate(
            normalized_no_quality
        )
    )

    forms.add(
        cleanup_alias_candidate(
            transliterated
        )
    )

    forms.add(
        cleanup_alias_candidate(
            transliterated_no_quality
        )
    )

    return {
        item
        for item in forms
        if item
    }


# ============================================================
# СОПОСТАВЛЕНИЕ ОДНОГО КАНАЛА
# ============================================================


def match_channel_against_inventory(
    channel: ChannelInput,
    inventory: CDNInventory,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> AliasMatch:

    candidates = generate_alias_candidates(
        channel
    )

    confirmed_aliases = inventory.aliases

    # --------------------------------------------------------
    # 1. Сначала проверяем словарь.
    # --------------------------------------------------------

    dictionary_aliases = {
        normalize_alias(alias)
        for alias
        in KNOWN_ALIAS_DICTIONARY.get(
            channel.display_name,
            set(),
        )
    }

    for alias in dictionary_aliases:

        if alias in confirmed_aliases:

            for candidate in candidates:

                if candidate.value == alias:
                    candidate.confirmed = True

            streams = [
                stream
                for stream in inventory.streams
                if stream.alias == alias
            ]

            return AliasMatch(
                channel_name=channel.display_name,
                normalized_name=normalize_channel_name(
                    channel.display_name
                ),
                cdn_alias=alias,
                match_type="DICTIONARY_CONFIRMED",
                confidence=1.0,
                reason=(
                    "Alias найден в явном словаре "
                    "и реально присутствует "
                    "в CDN inventory."
                ),
                candidates=candidates,
                streams=streams,
            )

    # --------------------------------------------------------
    # 2. Точное совпадение кандидата.
    # --------------------------------------------------------

    for candidate in candidates:

        if candidate.value in confirmed_aliases:

            candidate.confirmed = True

            streams = [
                stream
                for stream in inventory.streams
                if stream.alias == candidate.value
            ]

            if candidate.reason == "known_dictionary":

                match_type = (
                    "DICTIONARY_CONFIRMED"
                )

            elif candidate.reason == "tvg_id":

                match_type = (
                    "TVG_ID_CONFIRMED"
                )

            elif candidate.reason == "normalized_name":

                match_type = (
                    "NORMALIZED_CONFIRMED"
                )

            else:

                match_type = (
                    "ALIAS_CANDIDATE_CONFIRMED"
                )

            return AliasMatch(
                channel_name=channel.display_name,
                normalized_name=normalize_channel_name(
                    channel.display_name
                ),
                cdn_alias=candidate.value,
                match_type=match_type,
                confidence=max(
                    candidate.score,
                    0.90,
                ),
                reason=(
                    "Сгенерированный кандидат "
                    "совпал с реально обнаруженным "
                    "CDN alias."
                ),
                candidates=candidates,
                streams=streams,
            )

    # --------------------------------------------------------
    # 3. Проверяем формы названия непосредственно
    #    против реально обнаруженных alias.
    # --------------------------------------------------------

    comparison_forms = build_comparison_forms(
        channel.display_name
    )

    for alias in sorted(
        confirmed_aliases
    ):

        alias_forms = build_comparison_forms(
            alias
        )

        if comparison_forms.intersection(
            alias_forms
        ):

            streams = [
                stream
                for stream in inventory.streams
                if stream.alias == alias
            ]

            return AliasMatch(
                channel_name=channel.display_name,
                normalized_name=normalize_channel_name(
                    channel.display_name
                ),
                cdn_alias=alias,
                match_type="NORMALIZED_ALIAS_CONFIRMED",
                confidence=0.94,
                reason=(
                    "Нормализованная форма "
                    "названия совпала с "
                    "реальным CDN alias."
                ),
                candidates=candidates,
                streams=streams,
            )

    # --------------------------------------------------------
    # 4. Fuzzy.
    #
    # Здесь особенно важно:
    #
    # fuzzy = только вероятное совпадение.
    #
    # Нельзя выдавать его за факт.
    # --------------------------------------------------------

    best_alias: Optional[str] = None

    best_score = 0.0

    for alias in confirmed_aliases:

        for form in comparison_forms:

            score = similarity(
                form,
                alias,
            )

            if score > best_score:

                best_score = score

                best_alias = alias

    if (
        best_alias is not None
        and best_score >= fuzzy_threshold
    ):

        streams = [
            stream
            for stream in inventory.streams
            if stream.alias == best_alias
        ]

        return AliasMatch(
            channel_name=channel.display_name,
            normalized_name=normalize_channel_name(
                channel.display_name
            ),
            cdn_alias=best_alias,
            match_type="FUZZY_CANDIDATE",
            confidence=best_score,
            reason=(
                "Найден похожий CDN alias. "
                "Это вероятное соответствие, "
                "а не доказанное соответствие."
            ),
            candidates=candidates,
            streams=streams,
        )

    # --------------------------------------------------------
    # 5. Ничего не найдено.
    # --------------------------------------------------------

    return AliasMatch(
        channel_name=channel.display_name,
        normalized_name=normalize_channel_name(
            channel.display_name
        ),
        cdn_alias=None,
        match_type="UNKNOWN",
        confidence=0.0,
        reason=(
            "Реальный CDN alias "
            "для этого канала "
            "в доступном inventory "
            "не найден."
        ),
        candidates=candidates,
        streams=[],
    )


# ============================================================
# ПРОВЕРКА НЕПОСРЕДСТВЕННО ПО BASE URL
# ============================================================


def probe_channel_candidates(
    channel: ChannelInput,
    base_url: str = CDN_BASE_URL,
    max_variant_number: int = DEFAULT_MAX_VARIANT_NUMBER,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> AliasMatch:
    """
    Используется, когда у нас НЕТ каталога CDN,
    но пользователь разрешил проверять известные
    кандидаты непосредственно по URL.

    ВАЖНО:

    эта функция НЕ утверждает, что случайно найденный
    URL соответствует каналу по смыслу.

    Она только говорит:

        "этот alias технически существует".
    """

    candidates = generate_alias_candidates(
        channel
    )

    all_streams: List[CDNStream] = []

    confirmed_candidate: Optional[
        AliasCandidate
    ] = None

    for candidate in candidates:

        streams = discover_stream_variants(
            alias=candidate.value,
            base_url=base_url,
            max_variant_number=max_variant_number,
            timeout=timeout,
        )

        reachable_streams = [
            stream
            for stream in streams
            if stream.reachable
        ]

        if reachable_streams:

            candidate.confirmed = True

            all_streams.extend(
                reachable_streams
            )

            if confirmed_candidate is None:

                confirmed_candidate = candidate

    if confirmed_candidate is not None:

        if confirmed_candidate.reason == (
            "known_dictionary"
        ):

            match_type = (
                "DICTIONARY_CONFIRMED_BY_PROBE"
            )

            confidence = 1.0

        elif confirmed_candidate.reason == (
            "tvg_id"
        ):

            match_type = (
                "TVG_ID_CONFIRMED_BY_PROBE"
            )

            confidence = 0.95

        else:

            match_type = (
                "CANDIDATE_CONFIRMED_BY_PROBE"
            )

            confidence = confirmed_candidate.score

        return AliasMatch(
            channel_name=channel.display_name,
            normalized_name=normalize_channel_name(
                channel.display_name
            ),
            cdn_alias=confirmed_candidate.value,
            match_type=match_type,
            confidence=confidence,
            reason=(
                "Alias реально отвечает на CDN URL probe."
            ),
            candidates=candidates,
            streams=all_streams,
        )

    return AliasMatch(
        channel_name=channel.display_name,
        normalized_name=normalize_channel_name(
            channel.display_name
        ),
        cdn_alias=None,
        match_type="UNKNOWN",
        confidence=0.0,
        reason=(
            "Ни один из сгенерированных "
            "кандидатов не подтвердился "
            "при непосредственной проверке."
        ),
        candidates=candidates,
        streams=[],
    )


# ============================================================
# ПРОВЕРКА ВСЕГО СПИСКА
# ============================================================


def verify_channels(
    channels: Iterable[ChannelInput],
    inventory: Optional[CDNInventory] = None,
    base_url: str = CDN_BASE_URL,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
    probe_candidates_if_inventory_empty: bool = False,
    max_variant_number: int = DEFAULT_MAX_VARIANT_NUMBER,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> List[AliasMatch]:

    results: List[AliasMatch] = []

    for channel in channels:

        LOGGER.info(
            "Проверка канала: %s",
            channel.display_name,
        )

        if (
            inventory is not None
            and inventory.aliases
        ):

            result = match_channel_against_inventory(
                channel=channel,
                inventory=inventory,
                fuzzy_threshold=fuzzy_threshold,
            )

        elif probe_candidates_if_inventory_empty:

            result = probe_channel_candidates(
                channel=channel,
                base_url=base_url,
                max_variant_number=max_variant_number,
                timeout=timeout,
            )

        else:

            result = AliasMatch(
                channel_name=channel.display_name,
                normalized_name=normalize_channel_name(
                    channel.display_name
                ),
                cdn_alias=None,
                match_type="NO_CDN_INVENTORY",
                confidence=0.0,
                reason=(
                    "CDN inventory не предоставлен. "
                    "Генерация кандидатов выполнена, "
                    "но подтверждение невозможно."
                ),
                candidates=generate_alias_candidates(
                    channel
                ),
                streams=[],
            )

        results.append(
            result
        )

    return results


# ============================================================
# ПОСТРОЕНИЕ ОТЧЕТА
# ============================================================


def build_report(
    results: List[AliasMatch],
) -> Dict:

    confirmed = [
        result
        for result in results
        if result.cdn_alias is not None
        and result.match_type != "FUZZY_CANDIDATE"
    ]

    unknown = [
        result
        for result in results
        if (
            result.cdn_alias is None
            or result.match_type == "FUZZY_CANDIDATE"
        )
    ]

    return {
        "module": (
            "Модуль проверки алиасов"
        ),

        "total_channels": len(results),

        "matched_channels": len(
            confirmed
        ),

        "unknown_channels": len(
            unknown
        ),

        "results": [
            asdict(result)
            for result in results
        ],
    }


# ============================================================
# СОХРАНЕНИЕ JSON
# ============================================================


def save_json_report(
    report: Dict,
    filename: str,
) -> None:

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=4,
        )


# ============================================================
# ПЕЧАТЬ РЕЗУЛЬТАТОВ
# ============================================================


def print_results(
    results: List[AliasMatch],
) -> None:

    print()

    print(
        "=" * 100
    )

    print(
        "МОДУЛЬ ПРОВЕРКИ АЛИАСОВ"
    )

    print(
        "=" * 100
    )

    print()

    for result in results:

        print(
            f"КАНАЛ: {result.channel_name}"
        )

        print(
            f"Нормализованный: "
            f"{result.normalized_name}"
        )

        print(
            f"CDN alias: "
            f"{result.cdn_alias}"
        )

        print(
            f"Тип: "
            f"{result.match_type}"
        )

        print(
            f"Уверенность: "
            f"{result.confidence:.3f}"
        )

        print(
            f"Причина: "
            f"{result.reason}"
        )

        print(
            "Кандидаты:"
        )

        for candidate in result.candidates:

            marker = (
                "CONFIRMED"
                if candidate.confirmed
                else "candidate"
            )

            print(
                "  "
                f"{candidate.value:<35} "
                f"{candidate.score:.3f} "
                f"{marker:<12} "
                f"{candidate.reason}"
            )

        if result.streams:

            print(
                "Потоки:"
            )

            for stream in result.streams:

                print(
                    "  "
                    f"{stream.url}"
                )

        print()

        print(
            "-" * 100
        )

        print()


# ============================================================
# ПРИМЕР ВХОДНЫХ КАНАЛОВ
# ============================================================


TEST_CHANNELS = [
    ChannelInput(
        display_name="Карусель",
        tvg_id="karusel",
    ),

    ChannelInput(
        display_name="РЕН ТВ",
        tvg_id="rentv",
    ),

    ChannelInput(
        display_name="ТВ-3",
        tvg_id="tv3",
    ),

    ChannelInput(
        display_name="Мир",
        tvg_id="mir",
    ),

    ChannelInput(
        display_name="Голливуд HD",
    ),

    ChannelInput(
        display_name="Дом Кино",
        tvg_id="dom_kino",
    ),

    ChannelInput(
        display_name="FilmBox",
        tvg_id="filmbox",
    ),

    ChannelInput(
        display_name="NickToons",
        tvg_id="nicktoons",
    ),

    ChannelInput(
        display_name="Ocean TV",
        tvg_id="ocean_tv",
    ),

    ChannelInput(
        display_name="Ностальгия",
        tvg_id="nostalgia",
    ),
]


# ============================================================
# ТЕСТОВЫЙ CDN INVENTORY
# ============================================================
#
# Это имитация результата, который в дальнейшем должен
# прийти из реального доступного каталога.
#
# Здесь специально оставлены:
#
#     karousel
#
# вместо:
#
#     karusel
#
# чтобы проверить главный принцип.
#
# ============================================================


def create_test_inventory() -> CDNInventory:

    inventory = CDNInventory()

    test_aliases = [
        "karousel",
        "ren_tv",
        "tv_3",
        "mir",
        "amc",
        "dom_kino",
        "filmbox",
        "nicktoons",
        "ocean_tv",
        "nostalgia",
    ]

    for alias in test_aliases:

        inventory.aliases.add(
            alias
        )

        inventory.streams.append(
            CDNStream(
                alias=alias,
                url=build_stream_url(
                    CDN_BASE_URL,
                    alias,
                ),
                variant=None,
                source="test_inventory",
                http_status=200,
                reachable=True,
            )
        )

        inventory.streams.append(
            CDNStream(
                alias=alias,
                url=build_stream_url(
                    CDN_BASE_URL,
                    alias,
                    variant=1,
                ),
                variant=1,
                source="test_inventory",
                http_status=200,
                reachable=True,
            )
        )

        inventory.streams.append(
            CDNStream(
                alias=alias,
                url=build_stream_url(
                    CDN_BASE_URL,
                    alias,
                    variant=2,
                ),
                variant=2,
                source="test_inventory",
                http_status=200,
                reachable=True,
            )
        )

    return inventory


# ============================================================
# НОВЫЙ БЛОК:
# ФОРМИРОВАНИЕ ГОТОВОГО M3U PLAYLIST EDITION
# ============================================================
#
# Этот блок дополнительно использует уже полученные результаты.
#
# Основной принцип:
#
#   подтвержденный alias -> может попасть в M3U
#   FUZZY_CANDIDATE -> НЕ попадает в M3U
#   UNKNOWN -> НЕ попадает в M3U
#   NO_CDN_INVENTORY -> НЕ попадает в M3U
#
# Таким образом, случайная fuzzy-догадка не превращается
# в рабочую ссылку плейлиста.
#
# ============================================================


def is_confirmed_playlist_match(
    result: AliasMatch,
) -> bool:
    """
    Определяет, можно ли использовать результат
    для формирования рабочего M3U.

    ВАЖНО:

    FUZZY_CANDIDATE намеренно исключается.

    Наличие cdn_alias само по себе недостаточно:
    alias должен быть подтвержден логикой модуля.
    """

    if not result.cdn_alias:
        return False

    if result.match_type in {
        "FUZZY_CANDIDATE",
        "UNKNOWN",
        "NO_CDN_INVENTORY",
    }:
        return False

    reachable_streams = [
        stream
        for stream in result.streams
        if stream.reachable is True
    ]

    if not reachable_streams:
        return False

    return True


# ============================================================
# ЭКРАНИРОВАНИЕ M3U EXTINF
# ============================================================


def sanitize_m3u_attribute(
    value: str,
) -> str:
    """
    Очищает значение атрибута EXTINF.

    Кавычки заменяются, чтобы не сломать M3U.
    Переводы строк удаляются.
    """

    if value is None:
        return ""

    value = str(value)

    value = value.replace(
        '"',
        "'",
    )

    value = value.replace(
        "\r",
        " ",
    )

    value = value.replace(
        "\n",
        " ",
    )

    return value.strip()


# ============================================================
# СОЗДАНИЕ EXTINF
# ============================================================


def build_m3u_extinf(
    channel: ChannelInput,
) -> str:
    """
    Создаёт строку #EXTINF для канала.

    Если дополнительные поля отсутствуют,
    они просто не заполняются.
    """

    tvg_id = sanitize_m3u_attribute(
        channel.tvg_id
    )

    tvg_name = sanitize_m3u_attribute(
        channel.tvg_name
        or channel.display_name
    )

    group_title = sanitize_m3u_attribute(
        channel.group_title
    )

    display_name = (
        channel.display_name
        .replace(
            "\r",
            " ",
        )
        .replace(
            "\n",
            " ",
        )
        .strip()
    )

    attributes: List[str] = []

    if tvg_id:

        attributes.append(
            f'tvg-id="{tvg_id}"'
        )

    if tvg_name:

        attributes.append(
            f'tvg-name="{tvg_name}"'
        )

    if group_title:

        attributes.append(
            f'group-title="{group_title}"'
        )

    if attributes:

        return (
            "#EXTINF:-1 "
            + " ".join(attributes)
            + ","
            + display_name
        )

    return (
        "#EXTINF:-1,"
        + display_name
    )


# ============================================================
# СОЗДАНИЕ M3U PLAYLIST
# ============================================================


def build_m3u_playlist(
    channels: Iterable[ChannelInput],
    results: List[AliasMatch],
) -> str:
    """
    Создаёт готовый M3U playlist.

    Каждый подтвержденный поток получает свою
    пару:

        #EXTINF
        URL

    В M3U попадают только подтвержденные результаты.

    FUZZY_CANDIDATE намеренно исключается.
    """

    channel_list = list(
        channels
    )

    lines: List[str] = []

    lines.append(
        "#EXTM3U"
    )

    lines.append("")

    # --------------------------------------------------------
    # Сопоставляем результат с исходным ChannelInput.
    #
    # Порядок результатов соответствует порядку входных
    # каналов, но здесь дополнительно используем имя,
    # чтобы сохранить корректные данные EXTINF.
    # --------------------------------------------------------

    for index, result in enumerate(results):

        if not is_confirmed_playlist_match(
            result
        ):
            continue

        if index < len(channel_list):

            channel = channel_list[index]

        else:

            channel = ChannelInput(
                display_name=result.channel_name,
                tvg_name=result.channel_name,
            )

        reachable_streams = [
            stream
            for stream in result.streams
            if stream.reachable is True
        ]

        # ----------------------------------------------------
        # Убираем дубликаты URL,
        # сохраняя порядок обнаружения.
        # ----------------------------------------------------

        unique_urls: List[str] = []

        seen_urls: Set[str] = set()

        for stream in reachable_streams:

            if not stream.url:
                continue

            if stream.url in seen_urls:
                continue

            seen_urls.add(
                stream.url
            )

            unique_urls.append(
                stream.url
            )

        for url in unique_urls:

            lines.append(
                build_m3u_extinf(
                    channel
                )
            )

            lines.append(
                url
            )

            lines.append("")

    return "\n".join(
        lines
    ).rstrip() + "\n"


# ============================================================
# ПОДРОБНЫЙ ЧЕЛОВЕКОЧИТАЕМЫЙ ТЕКСТОВЫЙ ОТЧЁТ
# ============================================================
#
# Файл:
#
#     n_Alias.txt
#
# В отличие от машинного:
#
#     n_Alias_ngnorm.txt
#
# здесь сохраняется вся диагностическая информация:
#
#   - сводка;
#   - каждый канал;
#   - найденный alias;
#   - тип;
#   - confidence;
#   - причина;
#   - кандидаты;
#   - подтвержденные кандидаты;
#   - потоки;
#   - итоговая секция M3U PLAYLIST EDITION.
#
# ============================================================


def build_text_report_lines(
    report: Dict,
    channels: Iterable[ChannelInput],
    results: List[AliasMatch],
) -> List[str]:
    """
    Формирует весь человекочитаемый отчет построчно.

    В конце добавляется готовый M3U.
    """

    lines: List[str] = []

    lines.append(
        "============================================================"
    )

    lines.append(
        "МОДУЛЬ ПРОВЕРКИ АЛИАСОВ"
    )

    lines.append(
        "ИТОГОВЫЙ ТЕКСТОВЫЙ ОТЧЁТ"
    )

    lines.append(
        "============================================================"
    )

    lines.append("")

    lines.append(
        f"Модуль: {report.get('module', '')}"
    )

    lines.append(
        f"Всего каналов: "
        f"{report.get('total_channels', 0)}"
    )

    lines.append(
        f"Найдено подтвержденных соответствий: "
        f"{report.get('matched_channels', 0)}"
    )

    lines.append(
        f"Не найдено / вероятные: "
        f"{report.get('unknown_channels', 0)}"
    )

    lines.append("")

    lines.append(
        "ВАЖНО:"
    )

    lines.append(
        "Сгенерированный alias не считается "
        "подтвержденным только из-за генерации."
    )

    lines.append(
        "Подтверждение происходит только при наличии "
        "реального CDN alias / подтвержденного probe."
    )

    lines.append(
        "FUZZY_CANDIDATE не считается доказанным "
        "соответствием и не добавляется в рабочий M3U."
    )

    lines.append("")

    lines.append(
        "============================================================"
    )

    lines.append(
        "ПОДРОБНЫЕ РЕЗУЛЬТАТЫ"
    )

    lines.append(
        "============================================================"
    )

    lines.append("")

    for number, result in enumerate(
        results,
        start=1,
    ):

        lines.append(
            f"[{number}] КАНАЛ: "
            f"{result.channel_name}"
        )

        lines.append(
            f"Нормализованный: "
            f"{result.normalized_name}"
        )

        lines.append(
            f"CDN alias: "
            f"{result.cdn_alias}"
        )

        lines.append(
            f"Тип: "
            f"{result.match_type}"
        )

        lines.append(
            f"Уверенность: "
            f"{result.confidence:.3f}"
        )

        lines.append(
            f"Причина: "
            f"{result.reason}"
        )

        lines.append("")

        lines.append(
            "КАНДИДАТЫ:"
        )

        if result.candidates:

            for candidate in result.candidates:

                marker = (
                    "CONFIRMED"
                    if candidate.confirmed
                    else "candidate"
                )

                lines.append(
                    "  "
                    f"{candidate.value:<35} "
                    f"score={candidate.score:.3f} "
                    f"{marker:<12} "
                    f"reason={candidate.reason}"
                )

        else:

            lines.append(
                "  Нет кандидатов."
            )

        lines.append("")

        lines.append(
            "ПОТОКИ:"
        )

        if result.streams:

            for stream in result.streams:

                status = (
                    "REACHABLE"
                    if stream.reachable
                    else "UNREACHABLE"
                )

                variant = (
                    "primary"
                    if stream.variant is None
                    else f"variant={stream.variant}"
                )

                lines.append(
                    "  "
                    f"[{status}] "
                    f"[{variant}] "
                    f"[HTTP={stream.http_status}] "
                    f"{stream.url}"
                )

        else:

            lines.append(
                "  Реальных потоков не обнаружено."
            )

        lines.append("")

        playlist_allowed = (
            is_confirmed_playlist_match(
                result
            )
        )

        lines.append(
            "В M3U PLAYLIST EDITION: "
            + (
                "ДА"
                if playlist_allowed
                else "НЕТ"
            )
        )

        if (
            result.match_type
            == "FUZZY_CANDIDATE"
        ):

            lines.append(
                "Причина исключения из M3U: "
                "FUZZY является только вероятным "
                "соответствием."
            )

        elif result.cdn_alias is None:

            lines.append(
                "Причина исключения из M3U: "
                "подтвержденный CDN alias отсутствует."
            )

        elif not result.streams:

            lines.append(
                "Причина исключения из M3U: "
                "подтвержденные потоки отсутствуют."
            )

        lines.append("")

        lines.append(
            "-" * 100
        )

        lines.append("")

    # ========================================================
    # Готовый M3U
    # ========================================================

    lines.append(
        "============================================================"
    )

    lines.append(
        M3U_SECTION_TITLE
    )

    lines.append(
        "============================================================"
    )

    lines.append("")

    lines.append(
        "Скопируйте содержимое следующего блока "
        "и вставьте его как M3U playlist."
    )

    lines.append(
        "В этот блок попадают только подтвержденные "
        "CDN alias и реально обнаруженные потоки."
    )

    lines.append("")

    m3u_playlist = build_m3u_playlist(
        channels=channels,
        results=results,
    )

    m3u_lines = m3u_playlist.splitlines()

    for m3u_line in m3u_lines:

        lines.append(
            m3u_line
        )

    lines.append("")

    lines.append(
        "============================================================"
    )

    lines.append(
        "КОНЕЦ M3U PLAYLIST EDITION"
    )

    lines.append(
        "============================================================"
    )

    lines.append("")

    return lines


# ============================================================
# ПОСТРОЧНАЯ ЗАПИСЬ ЧЕЛОВЕКОЧИТАЕМОГО ОТЧЁТА
# ============================================================


def save_text_report(
    report: Dict,
    channels: Iterable[ChannelInput],
    results: List[AliasMatch],
    filename: str = HUMAN_REPORT_FILENAME,
    teletype_delay: float = TEXT_REPORT_TELETYPE_DELAY,
    flush_each_line: bool = TEXT_REPORT_FLUSH_EACH_LINE,
) -> None:
    """
    Сохраняет человекочитаемый отчет в текстовый файл.

    Файл создается построчно.

    Это позволяет получить именно текстовый
    телетайп-формат, а не JSON.

    ВАЖНО:

    Никакого вывода подробного отчета в консоль
    здесь нет.
    """

    channels = list(
        channels
    )

    lines = build_text_report_lines(
        report=report,
        channels=channels,
        results=results,
    )

    with open(
        filename,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as file:

        for line in lines:

            file.write(
                line
            )

            file.write(
                "\n"
            )

            if flush_each_line:

                file.flush()

            if (
                teletype_delay
                and teletype_delay > 0
            ):

                time.sleep(
                    teletype_delay
                )


# ============================================================
# НОВАЯ ЛОГИКА:
# МАШИННЫЙ ФОРМАТ ДЛЯ ngnorm.py
# ============================================================
#
# Формат одной записи:
#
# NAME=НТВ Право
# ALIAS=ntv_pravo
# URL=https://...
# STATUS=200
# SOURCE=ALIAS_MODULE
# FOUND=2026-08-19 18:42:11
#
# Между записями:
#
# пустая строка.
#
# ============================================================
#
# ПРИНЦИП:
#
# n_Alias.txt
#     содержит ВСЮ информацию, включая кандидатов.
#
# n_Alias_ngnorm.txt
#     содержит только результат, пригодный для машинной
#     обработки.
#
# Особенно важно:
#
# FUZZY_CANDIDATE НЕ превращается в ALIAS.
#
# ============================================================


def machine_safe(
    value: str,
) -> str:
    """
    Подготавливает значение для машинного файла.

    Запрещаем переносы строк, чтобы одна запись
    всегда оставалась однозначно разбираемой.
    """

    if value is None:
        return ""

    value = str(value)

    value = value.replace(
        "\r",
        " ",
    )

    value = value.replace(
        "\n",
        " ",
    )

    return value.strip()


def get_result_status(
    result: AliasMatch,
) -> str:
    """
    Возвращает машинный логический статус результата.

    Этот статус используется только тогда,
    когда невозможно вернуть HTTP-код конкретного
    найденного потока.
    """

    if result.match_type == "UNKNOWN":

        return "UNKNOWN"

    if result.match_type == "NO_CDN_INVENTORY":

        return "NO_CDN_INVENTORY"

    if result.match_type == "FUZZY_CANDIDATE":

        return "FUZZY"

    if result.cdn_alias:

        reachable = any(
            stream.reachable is True
            for stream in result.streams
        )

        if reachable:

            return "CONFIRMED"

        return "FOUND"

    return "UNKNOWN"


def get_primary_stream(
    result: AliasMatch,
) -> Optional[CDNStream]:
    """
    Возвращает основной найденный поток.

    Приоритет:

        1. variant=None и reachable=True
        2. первый reachable поток
    """

    for stream in result.streams:

        if (
            stream.variant is None
            and stream.reachable is True
        ):

            return stream

    for stream in result.streams:

        if stream.reachable is True:

            return stream

    return None


def get_machine_status(
    result: AliasMatch,
    stream: Optional[CDNStream],
) -> str:
    """
    Формирует STATUS для n_Alias_ngnorm.txt.

    Если есть HTTP-код найденного потока,
    используем именно его.

    Иначе используем логический статус.
    """

    if stream is not None:

        if stream.http_status is not None:

            return str(
                stream.http_status
            )

        if stream.reachable is True:

            return "200"

    return get_result_status(
        result
    )


def build_machine_record(
    channel: ChannelInput,
    result: AliasMatch,
    found_time: Optional[str] = None,
) -> List[str]:
    """
    Формирует одну машинную запись.

    Формат:

        NAME=...
        ALIAS=...
        URL=...
        STATUS=...
        SOURCE=...
        FOUND=...

    ngnorm.py сможет читать его без JSON,
    regex-хака или зависимости от структуры Python-классов.
    """

    if found_time is None:

        found_time = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    stream = get_primary_stream(
        result
    )

    alias = ""

    # --------------------------------------------------------
    # Только подтвержденный / реально найденный alias.
    #
    # FUZZY здесь намеренно НЕ превращается в ALIAS.
    # --------------------------------------------------------

    if result.cdn_alias:

        if result.match_type != "FUZZY_CANDIDATE":

            alias = result.cdn_alias

    url = ""

    if stream is not None:

        url = stream.url

    status = get_machine_status(
        result=result,
        stream=stream,
    )

    return [
        f"NAME={machine_safe(channel.display_name)}",
        f"ALIAS={machine_safe(alias)}",
        f"URL={machine_safe(url)}",
        f"STATUS={machine_safe(status)}",
        f"SOURCE={MACHINE_SOURCE}",
        f"FOUND={machine_safe(found_time)}",
    ]


def build_machine_report_lines(
    channels: Iterable[ChannelInput],
    results: List[AliasMatch],
) -> List[str]:
    """
    Формирует весь n_Alias_ngnorm.txt.

    Одна запись отделяется пустой строкой.

    Порядок каналов сохраняется.
    """

    channel_list = list(
        channels
    )

    lines: List[str] = []

    found_time = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    for index, result in enumerate(
        results
    ):

        if index < len(channel_list):

            channel = channel_list[index]

        else:

            channel = ChannelInput(
                display_name=result.channel_name,
                tvg_name=result.channel_name,
            )

        record = build_machine_record(
            channel=channel,
            result=result,
            found_time=found_time,
        )

        lines.extend(
            record
        )

        lines.append("")

    return lines


def save_machine_report(
    channels: Iterable[ChannelInput],
    results: List[AliasMatch],
    filename: str = MACHINE_REPORT_FILENAME,
    flush_each_line: bool = MACHINE_REPORT_FLUSH_EACH_LINE,
) -> None:
    """
    Сохраняет машинный файл для ngnorm.py.

    Запись выполняется последовательно.
    """

    channels = list(
        channels
    )

    lines = build_machine_report_lines(
        channels=channels,
        results=results,
    )

    with open(
        filename,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as file:

        for line in lines:

            file.write(
                line
            )

            file.write(
                "\n"
            )

            if flush_each_line:

                file.flush()


# ============================================================
# MAIN
# ============================================================


def main() -> None:

    LOGGER.info(
        "Запуск модуля проверки алиасов"
    )

    # --------------------------------------------------------
    # Пока используем тестовый inventory.
    #
    # В рабочем варианте сюда будет передаваться
    # реально полученный CDN inventory.
    # --------------------------------------------------------

    inventory = create_test_inventory()

    results = verify_channels(
        channels=TEST_CHANNELS,
        inventory=inventory,
        base_url=CDN_BASE_URL,
        fuzzy_threshold=DEFAULT_FUZZY_THRESHOLD,
        probe_candidates_if_inventory_empty=False,
        max_variant_number=DEFAULT_MAX_VARIANT_NUMBER,
        timeout=DEFAULT_REQUEST_TIMEOUT,
    )

    # --------------------------------------------------------
    # Формируем итоговый внутренний отчет.
    # --------------------------------------------------------

    report = build_report(
        results
    )

    # --------------------------------------------------------
    # ВАЖНО:
    #
    # print_results(results)
    #
    # здесь НЕ вызывается.
    #
    # Подробный результат не засоряет консоль.
    # --------------------------------------------------------

    # ========================================================
    # 1. ЧЕЛОВЕКОЧИТАЕМЫЙ ФАЙЛ
    # ========================================================

    save_text_report(
        report=report,
        channels=TEST_CHANNELS,
        results=results,
        filename=HUMAN_REPORT_FILENAME,
        teletype_delay=TEXT_REPORT_TELETYPE_DELAY,
        flush_each_line=TEXT_REPORT_FLUSH_EACH_LINE,
    )

    LOGGER.info(
        "Человекочитаемый отчет сохранен: %s",
        HUMAN_REPORT_FILENAME,
    )

    # ========================================================
    # 2. МАШИННЫЙ ФАЙЛ ДЛЯ ngnorm.py
    # ========================================================

    save_machine_report(
        channels=TEST_CHANNELS,
        results=results,
        filename=MACHINE_REPORT_FILENAME,
        flush_each_line=MACHINE_REPORT_FLUSH_EACH_LINE,
    )

    LOGGER.info(
        "Машинный отчет сохранен: %s",
        MACHINE_REPORT_FILENAME,
    )

    # ========================================================
    # 3. ФИНАЛЬНЫЙ СТАТУС
    # ========================================================

    LOGGER.info(
        "Модуль проверки алиасов завершил работу."
    )

    LOGGER.info(
        "Созданы файлы: %s и %s",
        HUMAN_REPORT_FILENAME,
        MACHINE_REPORT_FILENAME,
    )


if __name__ == "__main__":

    main()