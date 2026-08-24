from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import sqlite3
import ssl
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ============================================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ И КОНФИГУРАЦИЯ
# ============================================================

CDN_BASE_URL = "https://s70378.cdn.ngenix.net"
DEFAULT_STREAM_FILE = "index.m3u8"
DEFAULT_MAX_VARIANT_NUMBER = 10
DEFAULT_REQUEST_TIMEOUT = 5
MAX_WORKER_THREADS = 10
DEFAULT_USER_AGENT = "AliasVerificationModule/3.0 (Production Self-Learning Engine)"
DB_FILE_PATH = "knowledge.db"

# Базовые имена выходных файлов
BASE_HUMAN_REPORT_NAME = "Ai_Alias.txt"
BASE_MACHINE_REPORT_NAME = "Ai_Alias_ngnorm.txt"
BASE_JSON_EXPORT_NAME = "Ai_Alias_export.json"

MACHINE_SOURCE = "ALIAS_MODULE_V3"
M3U_SECTION_TITLE = "M3U PLAYLIST EDITION"

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger("AliasEngine")

# ============================================================
# СЛУЖЕБНЫЕ ТАБЛИЦЫ, ПСЕВДОНИМЫ И СТОП-СЛОВА
# ============================================================

RUSSIAN_TRANSLITERATION_TABLE = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)

QUALITY_TOKENS = {"hd", "sd", "fhd", "uhd", "4k", "8k", "hevc", "50fps"}
TECHNICAL_SUFFIXES = {"channel", "tv", "television", "online", "live", "stream", "hd"}
STUB_TOKENS = {"заглушка", "stub", "test", "temp", "placeholder", "тест", "проверка", "резерв"}

# Карта промежуточного маппинга названий каналов
CHANNEL_NAME_ALIASES: Dict[str, str] = {
    "голливуд hd": "Hollywood HD",
    "голливуд": "Hollywood HD",
    "рен тв hd": "РЕН ТВ",
    "первый": "Первый канал",
    "россия 1 hd": "Россия 1",
    "матч тв hd": "Матч ТВ",
}

# Базовый словарь явной привязки каналов к CDN алиасам
KNOWN_ALIAS_DICTIONARY: Dict[str, Set[str]] = {
    "Hollywood HD": {"amc"},
    "AMC": {"amc"},
    "Карусель": {"karousel"},
    "РЕН ТВ": {"ren_tv"},
    "ТВ-3": {"tv_3"},
    "Мир": {"mir"},
    "НТВ Сериал": {"ntv_serial"},
    "Мир сериала": {"mir_seriala"},
    "Дом Кино": {"dom_kino"},
    "FilmBox": {"filmbox"},
    "Sony Turbo": {"sony_turbo"},
    "NickToons": {"nicktoons"},
    "Nickelodeon": {"nickelodeon"},
    "Gulli": {"gulli"},
    "TiJi": {"tiji"},
    "Ocean TV": {"ocean_tv"},
    "RTVi": {"rtvi"},
    "Ностальгия": {"nostalgia"},
    "Mezzo": {"mezzo"},
    "ТНТ Music": {"tnt_music"},
    "Galaxy": {"galaxy"},
}

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class ChannelInput:
    display_name: str
    tvg_id: str = ""
    tvg_name: str = ""
    group_title: str = ""
    original_url: str = ""
    source_line: int = 0

@dataclass
class CDNStream:
    alias: str
    url: str
    variant: Optional[int]
    source: str = ""
    http_status: Optional[int] = None
    reachable: Optional[bool] = None
    response_time_ms: float = 0.0

@dataclass
class AliasCandidate:
    value: str
    reason: str
    score: float = 0.0
    confirmed: bool = False

@dataclass
class AliasMatch:
    channel_name: str
    normalized_name: str
    cdn_alias: Optional[str]
    match_type: str
    confidence: float
    reason: str
    candidates: List[AliasCandidate] = field(default_factory=list)
    streams: List[CDNStream] = field(default_factory=list)

# ============================================================
# ДИНАМИЧЕСКИЙ ДВИЖОК САМООБУЧЕНИЯ (ALIAS LEARNER ENGINE)
# ============================================================

class AliasLearnerEngine:
    def __init__(self, db_path: str = DB_FILE_PATH):
        self.db_path = db_path
        self._init_db()
        self._load_learned_dictionary()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_aliases (
                    channel_name TEXT PRIMARY KEY,
                    cdn_alias TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    hit_count INTEGER DEFAULT 1,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_stats (
                    reason TEXT PRIMARY KEY,
                    attempts INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    weight REAL DEFAULT 0.5
                )
            """)
            conn.commit()

    def _load_learned_dictionary(self) -> None:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT channel_name, cdn_alias FROM learned_aliases").fetchall()
            for r in rows:
                ch_name = r["channel_name"]
                alias = r["cdn_alias"]
                if ch_name not in KNOWN_ALIAS_DICTIONARY:
                    KNOWN_ALIAS_DICTIONARY[ch_name] = set()
                KNOWN_ALIAS_DICTIONARY[ch_name].add(alias)

    def get_reason_weight(self, reason: str, default_score: float) -> float:
        with self._get_connection() as conn:
            row = conn.execute("SELECT weight FROM candidate_stats WHERE reason = ?", (reason,)).fetchone()
            if row and row["weight"] is not None:
                return float(row["weight"])
        return default_score

    def record_success(self, channel_name: str, confirmed_alias: str, reason: str) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO learned_aliases (channel_name, cdn_alias, confidence, hit_count)
                VALUES (?, ?, 1.0, 1)
                ON CONFLICT(channel_name) DO UPDATE SET
                    cdn_alias = excluded.cdn_alias,
                    hit_count = hit_count + 1,
                    last_updated = CURRENT_TIMESTAMP
            """, (channel_name, confirmed_alias))

            conn.execute("""
                INSERT INTO candidate_stats (reason, attempts, success, weight)
                VALUES (?, 1, 1, 0.6)
                ON CONFLICT(reason) DO UPDATE SET
                    attempts = attempts + 1,
                    success = success + 1,
                    weight = (CAST(success + 1 AS REAL) + 1.0) / (CAST(attempts + 1 AS REAL) + 2.0)
            """, (reason,))
            conn.commit()

        if channel_name not in KNOWN_ALIAS_DICTIONARY:
            KNOWN_ALIAS_DICTIONARY[channel_name] = set()
        KNOWN_ALIAS_DICTIONARY[channel_name].add(confirmed_alias)

    def record_failure(self, reason: str) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO candidate_stats (reason, attempts, success, weight)
                VALUES (?, 1, 0, 0.3)
                ON CONFLICT(reason) DO UPDATE SET
                    attempts = attempts + 1,
                    weight = (CAST(success AS REAL) + 1.0) / (CAST(attempts + 1 AS REAL) + 2.0)
            """, (reason,))
            conn.commit()

# ============================================================
# НОРМАЛИЗАЦИЯ И ТРАНСФОРМАЦИЯ СТРОК
# ============================================================

def normalize_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip() if value else ""

def normalize_case(value: str) -> str:
    return value.casefold()

def remove_quality_tokens(value: str) -> str:
    parts = value.split()
    return " ".join([p for p in parts if p.casefold() not in QUALITY_TOKENS])

def normalize_separators(value: str) -> str:
    for sep in ["-", "_", ".", "/", "\\", "|", ":"]:
        value = value.replace(sep, " ")
    return re.sub(r"\s+", " ", value).strip()

def normalize_channel_name(value: str, remove_quality: bool = False) -> str:
    value = normalize_unicode(value)
    value = normalize_case(value)
    if remove_quality:
        value = remove_quality_tokens(value)
    return normalize_separators(value)

def normalize_alias(value: str) -> str:
    return normalize_unicode(value).casefold().strip("/")

def transliterate_russian(value: str) -> str:
    return normalize_unicode(value).casefold().translate(RUSSIAN_TRANSLITERATION_TABLE)

def cleanup_alias_candidate(value: str) -> str:
    value = normalize_unicode(value).casefold()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")

def is_stub(value: str) -> bool:
    val_lower = value.lower()
    return any(stub in val_lower for stub in STUB_TOKENS)

# ============================================================
# ГЕНЕРАТОР КАНДИДАТОВ С ПОДДЕРЖКОЙ МАППИНГА ИМЕН
# ============================================================

def generate_alias_candidates(
    channel: ChannelInput, 
    learner: Optional[AliasLearnerEngine] = None
) -> List[AliasCandidate]:
    if is_stub(channel.display_name):
        return []

    result: Dict[str, AliasCandidate] = {}

    def add_candidate(val: str, reason: str, default_score: float) -> None:
        val = normalize_alias(val)
        if not val or is_stub(val):
            return
        
        score = learner.get_reason_weight(reason, default_score) if learner else default_score

        if val not in result:
            result[val] = AliasCandidate(value=val, reason=reason, score=score)
        elif score > result[val].score:
            result[val].score = score
            result[val].reason = reason

    display_name = channel.display_name
    raw_norm = display_name.strip().casefold()
    
    # 1. Проверка маппинга псевдонимов имён ("Голливуд HD" -> "Hollywood HD")
    mapped_name = CHANNEL_NAME_ALIASES.get(raw_norm)

    # 2. Поиск по словарю прямого соответствия
    dictionary_aliases = set(KNOWN_ALIAS_DICTIONARY.get(display_name, set()))
    if mapped_name:
        dictionary_aliases.update(KNOWN_ALIAS_DICTIONARY.get(mapped_name, set()))

    for alias in dictionary_aliases:
        add_candidate(alias, "known_dictionary", 0.99)

    # 3. Генерация кандидатов из оригинального имени
    normalized = normalize_channel_name(display_name, remove_quality=False)
    normalized_no_quality = normalize_channel_name(display_name, remove_quality=True)
    transliterated = transliterate_russian(display_name)
    transliterated_no_quality = transliterate_russian(normalized_no_quality)

    add_candidate(cleanup_alias_candidate(normalized), "normalized_name", 0.60)
    add_candidate(cleanup_alias_candidate(normalized_no_quality), "normalized_without_quality", 0.55)
    add_candidate(cleanup_alias_candidate(transliterated), "transliteration", 0.50)
    add_candidate(cleanup_alias_candidate(transliterated_no_quality), "transliteration_without_quality", 0.48)

    # 4. Если сработал маппинг, генерируем варианты для переведенного имени
    if mapped_name:
        mapped_norm = normalize_channel_name(mapped_name, remove_quality=True)
        add_candidate(cleanup_alias_candidate(mapped_norm), "mapped_name_transliteration", 0.85)

    # 5. tvg_id и tvg_name
    if channel.tvg_id:
        add_candidate(cleanup_alias_candidate(channel.tvg_id), "tvg_id", 0.90)
    if channel.tvg_name:
        add_candidate(cleanup_alias_candidate(channel.tvg_name), "tvg_name", 0.70)

    # 6. Варианты с техническими суффиксами
    base_values = list(result.keys())
    for base in base_values:
        for suffix in TECHNICAL_SUFFIXES:
            add_candidate(f"{base}_{suffix}", f"technical_suffix:{suffix}", 0.25)

    return sorted(result.values(), key=lambda item: (-item.score, item.value))

# ============================================================
# ПАРАЛЛЕЛЬНЫЙ СЕТЕВОЙ СКАНЕР ПОТОКОВ
# ============================================================

def build_stream_url(base_url: str, alias: str, variant: Optional[int] = None) -> str:
    base_url = base_url.rstrip("/")
    alias = alias.strip("/")
    if variant is None:
        return f"{base_url}/{alias}/{DEFAULT_STREAM_FILE}"
    return f"{base_url}/{alias}/{variant}/{DEFAULT_STREAM_FILE}"

def check_single_url(url: str, alias: str, variant: Optional[int], source: str, timeout: int) -> CDNStream:
    start_time = time.time()
    request = Request(url, method="HEAD", headers={"User-Agent": DEFAULT_USER_AGENT})
    try:
        with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
            elapsed_ms = (time.time() - start_time) * 1000.0
            status = getattr(response, "status", 200)
            return CDNStream(
                alias=alias,
                url=url,
                variant=variant,
                source=source,
                http_status=status,
                reachable=(status == 200),
                response_time_ms=elapsed_ms,
            )
    except HTTPError as error:
        elapsed_ms = (time.time() - start_time) * 1000.0
        return CDNStream(
            alias=alias, url=url, variant=variant, source=source,
            http_status=error.code, reachable=False, response_time_ms=elapsed_ms
        )
    except Exception:
        elapsed_ms = (time.time() - start_time) * 1000.0
        return CDNStream(
            alias=alias, url=url, variant=variant, source=source,
            http_status=None, reachable=False, response_time_ms=elapsed_ms
        )

def discover_stream_variants_parallel(
    alias: str,
    base_url: str = CDN_BASE_URL,
    max_variant_number: int = DEFAULT_MAX_VARIANT_NUMBER,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> List[CDNStream]:
    urls_to_check: List[Tuple[str, Optional[int]]] = [(build_stream_url(base_url, alias, None), None)]
    for v in range(1, max_variant_number + 1):
        urls_to_check.append((build_stream_url(base_url, alias, v), v))

    results: List[CDNStream] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS) as executor:
        futures = [
            executor.submit(check_single_url, url, alias, variant, "parallel_probe", timeout)
            for url, variant in urls_to_check
        ]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)
            if res.reachable and res.http_status == 200 and res.variant is None:
                break

    return sorted(results, key=lambda x: (x.variant is not None, x.variant or 0))

# ============================================================
# ОСНОВНОЙ МОДУЛЬ ПРОВЕРКИ
# ============================================================

def probe_channel_candidates(
    channel: ChannelInput,
    learner: AliasLearnerEngine,
    base_url: str = CDN_BASE_URL,
    max_variant_number: int = DEFAULT_MAX_VARIANT_NUMBER,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> AliasMatch:
    candidates = generate_alias_candidates(channel, learner=learner)
    all_streams: List[CDNStream] = []
    confirmed_candidate: Optional[AliasCandidate] = None

    for candidate in candidates:
        streams = discover_stream_variants_parallel(
            alias=candidate.value,
            base_url=base_url,
            max_variant_number=max_variant_number,
            timeout=timeout,
        )

        reachable_streams = [s for s in streams if s.reachable and s.http_status == 200]

        if reachable_streams:
            candidate.confirmed = True
            all_streams.extend(reachable_streams)
            confirmed_candidate = candidate
            
            # АВТООБУЧЕНИЕ: Запоминаем успешное имя канала напрямую
            learner.record_success(
                channel_name=channel.display_name,
                confirmed_alias=candidate.value,
                reason=candidate.reason
            )
            break
        else:
            learner.record_failure(reason=candidate.reason)

    if confirmed_candidate is not None:
        match_type = "DICTIONARY_CONFIRMED" if "dictionary" in confirmed_candidate.reason else "CANDIDATE_CONFIRMED"
        return AliasMatch(
            channel_name=channel.display_name,
            normalized_name=normalize_channel_name(channel.display_name),
            cdn_alias=confirmed_candidate.value,
            match_type=match_type,
            confidence=confirmed_candidate.score,
            reason="Alias подтвержден ответом CDN (HTTP 200).",
            candidates=candidates,
            streams=all_streams,
        )

    return AliasMatch(
        channel_name=channel.display_name,
        normalized_name=normalize_channel_name(channel.display_name),
        cdn_alias=None,
        match_type="UNKNOWN",
        confidence=0.0,
        reason="Ни один кандидат не прошел сетевую проверку.",
        candidates=candidates,
        streams=[],
    )

def verify_channels(
    channels: Iterable[ChannelInput],
    learner: AliasLearnerEngine,
    base_url: str = CDN_BASE_URL,
    max_variant_number: int = DEFAULT_MAX_VARIANT_NUMBER,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> List[AliasMatch]:
    results: List[AliasMatch] = []
    for channel in channels:
        LOGGER.info("Обработка канала: %s", channel.display_name)
        if is_stub(channel.display_name):
            LOGGER.warning("Пропущен (заглушка): %s", channel.display_name)
            continue

        result = probe_channel_candidates(
            channel=channel,
            learner=learner,
            base_url=base_url,
            max_variant_number=max_variant_number,
            timeout=timeout,
        )
        results.append(result)
    return results

# ============================================================
# МЕХАНИЗМ АВТОИHomeНУМЕРАЦИИ ВЫХОДНЫХ ФАЙЛОВ
# ============================================================

def generate_numbered_filename(base_filename: str) -> str:
    """
    Автоматически генерирует имя с инкрементом.
    Пример: Ai_Alias.txt -> Ai_Alias_1.txt -> Ai_Alias_2.txt ...
    """
    if not os.path.exists(base_filename):
        return base_filename

    name, ext = os.path.splitext(base_filename)
    counter = 1
    
    while True:
        new_filename = f"{name}_{counter}{ext}"
        if not os.path.exists(new_filename):
            return new_filename
        counter += 1

# ============================================================
# ГЕНЕРАЦИЯ И СОХРАНЕНИЕ ОТЧЕТОВ
# ============================================================

def is_confirmed_playlist_match(result: AliasMatch) -> bool:
    return bool(result.cdn_alias and any(s.reachable for s in result.streams))

def build_m3u_playlist(channels: Iterable[ChannelInput], results: List[AliasMatch]) -> str:
    channel_list = list(channels)
    lines = ["#EXTM3U", ""]

    for index, result in enumerate(results):
        if not is_confirmed_playlist_match(result):
            continue

        channel = channel_list[index] if index < len(channel_list) else ChannelInput(display_name=result.channel_name)
        for stream in result.streams:
            if stream.reachable and stream.url:
                tvg_id = channel.tvg_id or ""
                group = channel.group_title or "General"
                lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" group-title="{group}",{channel.display_name}')
                lines.append(stream.url)
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"

def save_text_report(report: Dict, channels: Iterable[ChannelInput], results: List[AliasMatch]) -> str:
    filename = generate_numbered_filename(BASE_HUMAN_REPORT_NAME)
    
    lines = [
        "=" * 70, "МОДУЛЬ ПРОВЕРКИ АЛИАСОВ (АВТОМАТИЧЕСКАЯ СБОРКА)", "ИТОГОВЫЙ ТЕКСТОВЫЙ ОТЧЁТ", "=" * 70, "",
        f"Модуль: {report.get('module', '')}",
        f"Всего обработано: {report.get('total_channels', 0)}",
        f"Успешно найдено: {report.get('matched_channels', 0)}",
        f"Не найдено: {report.get('unknown_channels', 0)}", "",
        "=" * 70, "ПОДРОБНАЯ ДЕТАЛИЗАЦИЯ ПО КАНАЛАМ", "=" * 70, ""
    ]

    for number, result in enumerate(results, start=1):
        lines.append(f"[{number}] КАНАЛ: {result.channel_name}")
        lines.append(f"    Нормализованное имя: {result.normalized_name}")
        lines.append(f"    Найденный CDN alias: {result.cdn_alias}")
        lines.append(f"    Статус: {result.match_type}")
        lines.append(f"    Уровень доверия: {result.confidence:.3f}")

        lines.append("    Кандидаты:")
        for cand in result.candidates:
            marker = "[CONFIRMED]" if cand.confirmed else "[candidate]"
            lines.append(f"      - {cand.value:<25} (score={cand.score:.3f}) {marker:<12} reason={cand.reason}")

        lines.append("    Потоки:")
        for stream in result.streams:
            status = "OK" if stream.reachable else "FAIL"
            lines.append(f"      - [{status}] [HTTP {stream.http_status}] (ping: {stream.response_time_ms:.1f}ms) -> {stream.url}")
        lines.append("-" * 70 + "\n")

    lines.extend(["=" * 70, M3U_SECTION_TITLE, "=" * 70, ""])
    lines.append(build_m3u_playlist(channels, results))
    lines.extend(["=" * 70, "КОНЕЦ МАРШРУТА", "=" * 70, ""])

    with open(filename, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    LOGGER.info("Текстовый отчет сохранен в: %s", filename)
    return filename

def save_machine_report(channels: Iterable[ChannelInput], results: List[AliasMatch]) -> str:
    filename = generate_numbered_filename(BASE_MACHINE_REPORT_NAME)
    channel_list = list(channels)
    found_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(filename, "w", encoding="utf-8", newline="\n") as f:
        for index, result in enumerate(results):
            channel = channel_list[index] if index < len(channel_list) else ChannelInput(display_name=result.channel_name)
            stream = result.streams[0] if result.streams else None
            
            f.write(f"NAME={channel.display_name}\n")
            f.write(f"ALIAS={result.cdn_alias or ''}\n")
            f.write(f"URL={stream.url if stream else ''}\n")
            f.write(f"STATUS={stream.http_status if stream else 'UNKNOWN'}\n")
            f.write(f"SOURCE={MACHINE_SOURCE}\n")
            f.write(f"FOUND={found_time}\n\n")

    LOGGER.info("Машинный отчет сохранен в: %s", filename)
    return filename

def export_to_json(results: List[AliasMatch]) -> str:
    filename = generate_numbered_filename(BASE_JSON_EXPORT_NAME)
    data = [asdict(r) for r in results]
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    LOGGER.info("JSON отчет сохранен в: %s", filename)
    return filename

# ============================================================
# ТОЧКА ВХОДА И ТЕСТОВЫЙ ПРОГОН
# ============================================================

def main() -> None:
    LOGGER.info("=== Запуск финального Alias Engine ===")

    learner = AliasLearnerEngine(db_path=DB_FILE_PATH)

    INPUT_CHANNELS = [
        ChannelInput(display_name="Голливуд HD", group_title="Кино"), # Маппинг -> Hollywood HD -> amc
        ChannelInput(display_name="Карусель", tvg_id="karusel", group_title="Детские"),
        ChannelInput(display_name="РЕН ТВ", tvg_id="rentv", group_title="Общие"),
        ChannelInput(display_name="ТВ-3", tvg_id="tv3", group_title="Развлекательные"),
        ChannelInput(display_name="Мир", tvg_id="mir"),
        ChannelInput(display_name="Заглушка_1080p"),                 # Авто-пропуск
    ]

    results = verify_channels(
        channels=INPUT_CHANNELS,
        learner=learner,
        max_variant_number=5,
        timeout=4,
    )

    confirmed_count = sum(1 for r in results if is_confirmed_playlist_match(r))
    report_meta = {
        "module": "AliasVerificationModule (Ai Output Edition)",
        "total_channels": len(results),
        "matched_channels": confirmed_count,
        "unknown_channels": len(results) - confirmed_count,
    }

    txt_file = save_text_report(report=report_meta, channels=INPUT_CHANNELS, results=results)
    ngnorm_file = save_machine_report(channels=INPUT_CHANNELS, results=results)
    json_file = export_to_json(results=results)

    LOGGER.info("Сканирование завершено. Файлы созданы:")
    LOGGER.info(" - %s", txt_file)
    LOGGER.info(" - %s", ngnorm_file)
    LOGGER.info(" - %s", json_file)

if __name__ == "__main__":
    main()
