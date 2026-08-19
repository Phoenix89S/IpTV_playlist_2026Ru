import os
import re
import asyncio
import aiohttp

from aiohttp import (
    ClientTimeout,
    TCPConnector,
)

from datetime import datetime
from urllib.parse import (
    urljoin,
    urlparse,
    unquote,
)


# ============================================================
# 1. TURBO-ПАРАМЕТРЫ
# ============================================================

CPU = os.cpu_count() or 1

TURBO = os.getenv("NGNORM_TURBO") == "1"

MAX_THREADS = CPU * (40 if TURBO else 10)

TIMEOUT = 1 if TURBO else 2

CACHE = {}

MANIFEST_CACHE = {}

ALIAS_CACHE = {}

NODE_CACHE = {}

CDN_DISCOVERY_CACHE = {}

SESSION = None

SEMAPHORE = None

REPORT_FILE = "ngnorm_report.txt"


# ============================================================
# 2. ТЕЛЕТАЙП СКАЛА ДРЕГ
#
# ВАЖНО:
# Полный телетайп НЕ печатается в консоль.
# Все сообщения пишутся в REPORT_FILE.
# В консоль выводится только краткая статистика.
# ============================================================

REPORT_HANDLE = None


def current_time():
    return datetime.now().strftime("%H:%M:%S")


def current_datetime():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def teletype(text):
    global REPORT_HANDLE

    line = (
        f"[{current_time()}] "
        f"СКАЛА ДРЕГ :: "
        f"{text}"
    )

    if REPORT_HANDLE:
        REPORT_HANDLE.write(line + "\n")
        REPORT_HANDLE.flush()


def teletype_ok(name, url):
    teletype(
        f"[ OK ] "
        f"{name} -> {url}"
    )


def teletype_dead(name, url):
    teletype(
        f"[DEAD] "
        f"{name} -> {url}"
    )


def teletype_error(text):
    teletype(
        f"[ERROR] {text}"
    )


# ============================================================
# 3. КОНСОЛЬНЫЙ ТЕЛЕТАЙП
#
# Только краткие сообщения.
# ============================================================

def console(text):
    now = current_time()

    print(
        f"[{now}] "
        f"СКАЛА ДРЕГ :: "
        f"{text}",
        flush=True
    )


# ============================================================
# 4. НОРМАЛИЗАЦИЯ URL NGENIX
# ============================================================

def normalize_ngenix(url):
    if not isinstance(url, str):
        return "INVALID:"

    url = url.strip()

    if not url:
        return "INVALID:"

    if "..." in url:
        return "INVALID:" + url

    if url.startswith("https://"):
        url = url[8:]

    elif url.startswith("http://"):
        url = url[7:]

    while "//" in url:
        url = url.replace("//", "/")

    if ".cdn.ngenix.net" not in url:
        return "INVALID:" + url

    parts = url.split("/", 1)

    host = parts[0]

    path = ""

    if len(parts) > 1:
        path = parts[1]

    if not host.endswith(".cdn.ngenix.net"):
        return "INVALID:" + url

    if not path:
        return (
            "https://"
            + host
        )

    return (
        "https://"
        + host
        + "/"
        + path
    )


# ============================================================
# 5. ПРОВЕРКА URL
# ============================================================

def is_valid_url(url):
    if not isinstance(url, str):
        return False

    if not url:
        return False

    if url.startswith("INVALID:"):
        return False

    if "..." in url:
        return False

    parsed = urlparse(url)

    if parsed.scheme not in (
        "http",
        "https",
    ):
        return False

    if not parsed.netloc:
        return False

    return True


# ============================================================
# 6. ИЗВЛЕЧЕНИЕ CDN NODE
# ============================================================

def extract_node(url):
    if not is_valid_url(url):
        return None

    try:
        parsed = urlparse(url)

        host = parsed.netloc

        if host.endswith(
            ".cdn.ngenix.net"
        ):
            return host

    except Exception:
        return None

    return None


# ============================================================
# 7. ИЗВЛЕЧЕНИЕ ПУТИ
# ============================================================

def extract_path(url):
    if not is_valid_url(url):
        return None

    try:
        parsed = urlparse(url)

        return parsed.path.lstrip("/")

    except Exception:
        return None


# ============================================================
# 8. ИЗВЛЕЧЕНИЕ ИДЕНТИФИКАТОРА КАНАЛА
# ============================================================

def extract_channel(url):
    path = extract_path(url)

    if not path:
        return None

    parts = [
        item
        for item in path.split("/")
        if item
    ]

    if not parts:
        return None

    first = parts[0]

    first = unquote(first)

    return first


# ============================================================
# 9. ПОЛУЧЕНИЕ BASE URL
# ============================================================

def get_base_url(url):
    if not is_valid_url(url):
        return None

    parsed = urlparse(url)

    path = parsed.path

    if not path:
        return (
            parsed.scheme
            + "://"
            + parsed.netloc
        )

    directory = path.rsplit(
        "/",
        1
    )[0]

    return (
        parsed.scheme
        + "://"
        + parsed.netloc
        + directory
    )


# ============================================================
# 10. ПОЛУЧЕНИЕ NODE BASE URL
# ============================================================

def get_node_base(url):
    node = extract_node(url)

    if not node:
        return None

    return (
        "https://"
        + node
    )


# ============================================================
# 11. TVG-ID
# ============================================================

def make_tvg_id(name, url):
    channel = extract_channel(url)

    if channel:
        value = channel

    else:
        value = name

    value = unquote(value)

    value = value.lower()

    value = value.replace(
        "+",
        "_plus_"
    )

    value = value.replace(
        "&",
        "_and_"
    )

    value = re.sub(
        r"[^a-z0-9а-яё_]+",
        "_",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"_+",
        "_",
        value,
    )

    value = value.strip("_")

    if not value:
        value = "unknown"

    return value


# ============================================================
# 12. M3U NAME
# ============================================================

def make_m3u_name(name):
    value = str(name).strip()

    value = value.replace(
        "\n",
        " "
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value


# ============================================================
# 13. ГЕНЕРАЦИЯ ALIAS
# ============================================================

def generate_aliases(name, url):
    aliases = []

    def add(value):
        if not value:
            return

        value = str(value).strip()

        if not value:
            return

        value = value.lower()

        value = value.strip(
            "/"
        )

        if value not in aliases:
            aliases.append(value)

    clean_name = str(
        name
    ).strip()

    clean_name = clean_name.lower()

    add(clean_name)

    add(
        clean_name.replace(
            "+",
            " plus "
        )
    )

    add(
        clean_name.replace(
            "+",
            "_plus_"
        )
    )

    add(
        clean_name.replace(
            "+",
            "_plus"
        )
    )

    add(
        clean_name.replace(
            " ",
            "_"
        )
    )

    add(
        clean_name.replace(
            " ",
            "-"
        )
    )

    add(
        clean_name.replace(
            " ",
            ""
        )
    )

    normalized = re.sub(
        r"[^a-z0-9а-яё]+",
        "_",
        clean_name,
        flags=re.IGNORECASE,
    )

    normalized = re.sub(
        r"_+",
        "_",
        normalized,
    )

    normalized = normalized.strip(
        "_"
    )

    add(normalized)

    if normalized:
        add(
            normalized
            .replace(
                "_plus_",
                "_plus"
            )
        )

        add(
            normalized
            .replace(
                "_plus_",
                "plus_"
            )
        )

        add(
            normalized.replace(
                "_",
                ""
            )
        )

    channel = extract_channel(
        url
    )

    if channel:
        add(channel)

        add(
            channel.replace(
                "_",
                "-"
            )
        )

        add(
            channel.replace(
                "_",
                ""
            )
        )

        add(
            channel + "_1"
        )

        add(
            channel + "_hd"
        )

    path = extract_path(
        url
    )

    if path:
        directory = path.rsplit(
            "/",
            1
        )[0]

        if directory:
            add(directory)

    return aliases


# ============================================================
# 14. СРАВНЕНИЕ ALIAS
# ============================================================

def alias_matches(
    alias,
    channel_name,
    original_url,
):
    if not alias:
        return False

    alias = alias.lower()

    aliases = generate_aliases(
        channel_name,
        original_url,
    )

    if alias in aliases:
        return True

    return False


# ============================================================
# 15. HTTP FETCH
# ============================================================

async def fetch(
    url,
    read_body=False,
    max_bytes=65536,
):
    global SESSION
    global SEMAPHORE

    if not is_valid_url(url):
        return None

    cache_key = (
        url,
        read_body,
    )

    if cache_key in CACHE:
        return CACHE[
            cache_key
        ]

    start = datetime.now()

    async with SEMAPHORE:

        try:

            async with SESSION.get(
                url,
                allow_redirects=True,
                headers={
                    "User-Agent":
                        "SKALA-DREG/2.0",
                    "Accept":
                        "*/*",
                    "Connection":
                        "keep-alive",
                },
            ) as response:

                body = b""

                if read_body:

                    body = await response.content.read(
                        max_bytes
                    )

                elapsed = (
                    datetime.now()
                    - start
                ).total_seconds()

                result = {
                    "url": str(
                        response.url
                    ),
                    "status":
                        response.status,
                    "content_type":
                        response.headers.get(
                            "Content-Type",
                            "",
                        ),
                    "body":
                        body,
                    "elapsed":
                        elapsed,
                    "headers":
                        dict(
                            response.headers
                        ),
                }

                CACHE[
                    cache_key
                ] = result

                return result

        except (
            asyncio.TimeoutError,
            aiohttp.ClientError,
            ConnectionError,
            OSError,
        ) as error:

            elapsed = (
                datetime.now()
                - start
            ).total_seconds()

            result = {
                "url": url,
                "status": None,
                "content_type": "",
                "body": b"",
                "elapsed": elapsed,
                "headers": {},
                "error": str(error),
            }

            CACHE[
                cache_key
            ] = result

            return result

        except Exception as error:

            elapsed = (
                datetime.now()
                - start
            ).total_seconds()

            result = {
                "url": url,
                "status": None,
                "content_type": "",
                "body": b"",
                "elapsed": elapsed,
                "headers": {},
                "error": str(error),
            }

            CACHE[
                cache_key
            ] = result

            return result


# ============================================================
# 16. ПРОВЕРКА HLS
# ============================================================

def is_hls_manifest(
    body,
    content_type="",
):
    if not body:
        return False

    try:
        text = body.decode(
            "utf-8",
            errors="ignore",
        )
    except Exception:
        return False

    if "#EXTM3U" in text:
        return True

    if "#EXT-X-" in text:
        return True

    content_type = (
        content_type
        or ""
    ).lower()

    if (
        "mpegurl"
        in content_type
    ):
        return True

    if (
        "vnd.apple.mpegurl"
        in content_type
    ):
        return True

    return False


# ============================================================
# 17. ПРОВЕРКА ПОТОКА
# ============================================================

async def check_stream(
    base,
):
    if not base:
        return None

    tests = [
        f"{base}/index.m3u8",
        f"{base}/playlist.m3u8",
        f"{base}/master.m3u8",
        f"{base}/1/index.m3u8",
        f"{base}/2/index.m3u8",
    ]

    for url in tests:

        result = await fetch(
            url,
            read_body=True,
            max_bytes=65536,
        )

        if not result:
            continue

        if result.get(
            "status"
        ) != 200:
            continue

        body = result.get(
            "body",
            b"",
        )

        content_type = result.get(
            "content_type",
            "",
        )

        if is_hls_manifest(
            body,
            content_type,
        ):
            return {
                "url":
                    result.get(
                        "url",
                        url,
                    ),
                "status":
                    result.get(
                        "status"
                    ),
                "elapsed":
                    result.get(
                        "elapsed",
                        0,
                    ),
                "body":
                    body,
                "content_type":
                    content_type,
            }

        if (
            url.lower().endswith(
                ".m3u8"
            )
            and result.get(
                "status"
            ) == 200
        ):
            return {
                "url":
                    result.get(
                        "url",
                        url,
                    ),
                "status":
                    result.get(
                        "status"
                    ),
                "elapsed":
                    result.get(
                        "elapsed",
                        0,
                    ),
                "body":
                    body,
                "content_type":
                    content_type,
            }

    return None


# ============================================================
# 18. ПАРСИНГ M3U8
# ============================================================

def parse_m3u8(
    text,
    manifest_url,
):
    discovered = []

    if not text:
        return discovered

    lines = text.splitlines()

    current_stream_info = None

    for line in lines:

        line = line.strip()

        if not line:
            continue

        if line.startswith(
            "#EXT-X-STREAM-INF:"
        ):
            current_stream_info = (
                line
                .split(
                    ":",
                    1
                )[1]
            )

            continue

        if line.startswith(
            "#EXTINF:"
        ):
            current_stream_info = (
                line
                .split(
                    ":",
                    1
                )[1]
            )

            continue

        if line.startswith(
            "#"
        ):
            continue

        absolute_url = urljoin(
            manifest_url,
            line,
        )

        discovered.append(
            {
                "url":
                    absolute_url,
                "info":
                    current_stream_info,
                "source_manifest":
                    manifest_url,
            }
        )

        current_stream_info = None

    return discovered


# ============================================================
# 19. ПОЛНЫЙ АНАЛИЗ MANIFEST
# ============================================================

async def discover_manifest(
    manifest_url,
    depth=0,
    max_depth=2,
):
    if depth > max_depth:
        return []

    cache_key = (
        manifest_url,
        depth,
    )

    if cache_key in MANIFEST_CACHE:
        return MANIFEST_CACHE[
            cache_key
        ]

    result = await fetch(
        manifest_url,
        read_body=True,
        max_bytes=262144,
    )

    if not result:
        MANIFEST_CACHE[
            cache_key
        ] = []

        return []

    if result.get(
        "status"
    ) != 200:
        MANIFEST_CACHE[
            cache_key
        ] = []

        return []

    body = result.get(
        "body",
        b"",
    )

    try:
        text = body.decode(
            "utf-8",
            errors="ignore",
        )
    except Exception:
        text = ""

    if not is_hls_manifest(
        body,
        result.get(
            "content_type",
            "",
        ),
    ):
        MANIFEST_CACHE[
            cache_key
        ] = []

        return []

    entries = parse_m3u8(
        text,
        result.get(
            "url",
            manifest_url,
        ),
    )

    discovered = []

    for entry in entries:

        item_url = entry.get(
            "url"
        )

        if not item_url:
            continue

        discovered.append(
            {
                "url":
                    item_url,
                "info":
                    entry.get(
                        "info"
                    ),
                "source_manifest":
                    entry.get(
                        "source_manifest"
                    ),
                "depth":
                    depth,
            }
        )

    nested_tasks = []

    for entry in entries:

        item_url = entry.get(
            "url"
        )

        if not item_url:
            continue

        if (
            item_url.lower()
            .endswith(".m3u8")
        ):
            nested_tasks.append(
                discover_manifest(
                    item_url,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
            )

    if nested_tasks:

        nested_results = (
            await asyncio.gather(
                *nested_tasks,
                return_exceptions=True,
            )
        )

        for nested in nested_results:

            if isinstance(
                nested,
                Exception,
            ):
                continue

            discovered.extend(
                nested
            )

    unique = {}

    for item in discovered:

        item_url = item.get(
            "url"
        )

        if item_url:
            unique[
                item_url
            ] = item

    final_result = list(
        unique.values()
    )

    MANIFEST_CACHE[
        cache_key
    ] = final_result

    return final_result


# ============================================================
# 20. ПОЛУЧЕНИЕ CDN NODE ИЗ URL
# ============================================================

def same_cdn_node(
    first_url,
    second_url,
):
    first_node = extract_node(
        first_url
    )

    second_node = extract_node(
        second_url
    )

    if not first_node:
        return False

    if not second_node:
        return False

    return (
        first_node.lower()
        ==
        second_node.lower()
    )


# ============================================================
# 21. ALIAS TARGETS
#
# ВАЖНО:
# Здесь используются только варианты,
# полученные из реально существующего URL
# конкретного канала.
# ============================================================

def build_alias_targets(
    name,
    original_url,
):
    node = extract_node(
        original_url
    )

    if not node:
        return []

    aliases = generate_aliases(
        name,
        original_url,
    )

    targets = []

    for alias in aliases:

        alias = alias.strip(
            "/"
        )

        if not alias:
            continue

        targets.append(
            f"https://{node}/{alias}/index.m3u8"
        )

        targets.append(
            f"https://{node}/{alias}/playlist.m3u8"
        )

        targets.append(
            f"https://{node}/{alias}/master.m3u8"
        )

    unique = []

    seen = set()

    for target in targets:

        if target in seen:
            continue

        seen.add(target)

        unique.append(
            target
        )

    return unique


# ============================================================
# 22. ALIAS DISCOVERY
# ============================================================

async def alias_discovery(
    name,
    original_url,
):
    cache_key = (
        name,
        original_url,
    )

    if cache_key in ALIAS_CACHE:
        return ALIAS_CACHE[
            cache_key
        ]

    start = datetime.now()

    aliases = generate_aliases(
        name,
        original_url,
    )

    targets = build_alias_targets(
        name,
        original_url,
    )

    teletype(
        "ALIAS DISCOVERY :: "
        f"{name} :: "
        f"ALIASES={len(aliases)} :: "
        f"TARGETS={len(targets)}"
    )

    found = []

    for target in targets:

        result = await fetch(
            target,
            read_body=True,
            max_bytes=65536,
        )

        if not result:
            continue

        if result.get(
            "status"
        ) != 200:
            continue

        body = result.get(
            "body",
            b"",
        )

        if not is_hls_manifest(
            body,
            result.get(
                "content_type",
                "",
            ),
        ):
            continue

        path = extract_path(
            target
        )

        alias = None

        if path:
            alias = path.split(
                "/",
                1
            )[0]

        found.append(
            {
                "name":
                    name,
                "alias":
                    alias,
                "url":
                    result.get(
                        "url",
                        target,
                    ),
                "status":
                    result.get(
                        "status"
                    ),
                "elapsed":
                    result.get(
                        "elapsed",
                        0,
                    ),
                "found_at":
                    current_datetime(),
                "aliases":
                    aliases,
            }
        )

        teletype(
            "ALIAS MATCH :: "
            f"{name} :: "
            f"{alias} :: "
            f"{target}"
        )

    elapsed = (
        datetime.now()
        - start
    ).total_seconds()

    result = {
        "name":
            name,
        "original_url":
            original_url,
        "aliases":
            aliases,
        "found":
            found,
        "elapsed":
            elapsed,
    }

    ALIAS_CACHE[
        cache_key
    ] = result

    return result


# ============================================================
# 23. CDN DISCOVERY ПО НАЙДЕННОМУ MANIFEST
# ============================================================

async def cdn_manifest_discovery(
    name,
    live_url,
):
    cache_key = live_url

    if cache_key in CDN_DISCOVERY_CACHE:
        return CDN_DISCOVERY_CACHE[
            cache_key
        ]

    start = datetime.now()

    teletype(
        "CDN MANIFEST DISCOVERY :: "
        f"{name} :: "
        f"{live_url}"
    )

    discovered = await discover_manifest(
        live_url,
        depth=0,
        max_depth=2,
    )

    final = []

    seen = set()

    for item in discovered:

        item_url = item.get(
            "url"
        )

        if not item_url:
            continue

        if not same_cdn_node(
            live_url,
            item_url,
        ):
            continue

        if item_url in seen:
            continue

        seen.add(
            item_url
        )

        final.append(
            {
                "name":
                    name,
                "url":
                    item_url,
                "source":
                    "MANIFEST_DISCOVERY",
                "source_manifest":
                    item.get(
                        "source_manifest"
                    ),
                "info":
                    item.get(
                        "info"
                    ),
                "found_at":
                    current_datetime(),
            }
        )

    elapsed = (
        datetime.now()
        - start
    ).total_seconds()

    result = {
        "name":
            name,
        "live_url":
            live_url,
        "found":
            final,
        "elapsed":
            elapsed,
    }

    CDN_DISCOVERY_CACHE[
        cache_key
    ] = result

    teletype(
        "CDN MANIFEST DISCOVERY END :: "
        f"{name} :: "
        f"FOUND={len(final)} :: "
        f"TIME={elapsed:.3f}s"
    )

    return result


# ============================================================
# 24. WORKER
# ============================================================

async def worker(
    name,
    raw,
):
    started = datetime.now()

    url = normalize_ngenix(
        raw
    )

    if not is_valid_url(
        url
    ):

        teletype_dead(
            name,
            raw,
        )

        return {
            "name":
                name,
            "original":
                raw,
            "url":
                raw,
            "live":
                False,
            "source":
                "INVALID",
            "found_at":
                None,
            "elapsed":
                (
                    datetime.now()
                    - started
                ).total_seconds(),
            "node":
                None,
            "channel":
                None,
            "alias_result":
                None,
            "cdn_result":
                None,
        }

    node = extract_node(
        url
    )

    channel = extract_channel(
        url
    )

    base = get_base_url(
        url
    )

    teletype(
        "ПРОВЕРКА :: "
        f"{name} :: "
        f"NODE={node} :: "
        f"CHANNEL={channel}"
    )

    live_result = await check_stream(
        base
    )

    if live_result:

        live_url = live_result.get(
            "url",
            url,
        )

        elapsed = (
            datetime.now()
            - started
        ).total_seconds()

        teletype_ok(
            name,
            live_url,
        )

        alias_result = (
            await alias_discovery(
                name,
                url,
            )
        )

        cdn_result = (
            await cdn_manifest_discovery(
                name,
                live_url,
            )
        )

        return {
            "name":
                name,
            "original":
                raw,
            "url":
                live_url,
            "live":
                True,
            "source":
                "DIRECT",
            "found_at":
                current_datetime(),
            "elapsed":
                elapsed,
            "node":
                node,
            "channel":
                channel,
            "alias_result":
                alias_result,
            "cdn_result":
                cdn_result,
        }

    teletype(
        "ПРЯМОЙ URL НЕ ОТВЕТИЛ :: "
        f"{name}"
    )

    alias_result = (
        await alias_discovery(
            name,
            url,
        )
    )

    alias_found = (
        alias_result.get(
            "found",
            [],
        )
    )

    if alias_found:

        selected = (
            alias_found[0]
        )

        live_url = selected.get(
            "url",
            url,
        )

        elapsed = (
            datetime.now()
            - started
        ).total_seconds()

        teletype_ok(
            name,
            live_url,
        )

        cdn_result = (
            await cdn_manifest_discovery(
                name,
                live_url,
            )
        )

        return {
            "name":
                name,
            "original":
                raw,
            "url":
                live_url,
            "live":
                True,
            "source":
                "ALIAS_MATCH",
            "found_at":
                current_datetime(),
            "elapsed":
                elapsed,
            "node":
                extract_node(
                    live_url
                ),
            "channel":
                extract_channel(
                    live_url
                ),
            "alias_result":
                alias_result,
            "cdn_result":
                cdn_result,
        }

    elapsed = (
        datetime.now()
        - started
    ).total_seconds()

    teletype_dead(
        name,
        url,
    )

    return {
        "name":
            name,
        "original":
            raw,
        "url":
            url,
        "live":
            False,
        "source":
            "DEAD",
        "found_at":
            None,
        "elapsed":
            elapsed,
        "node":
            node,
        "channel":
            channel,
        "alias_result":
            alias_result,
        "cdn_result":
            None,
    }


# ============================================================
# 25. ПОЛНЫЙ СПИСОК КАНАЛОВ
#
# Здесь оставлены все предоставленные тобой записи.
# Записи с "..." намеренно не считаются рабочими URL.
# ============================================================

CHANNELS = {

    # ========================================================
    # VIJU+
    # ========================================================

    "viju+ Premiere":
        "s70378.cdn.ngenix.net/vip_premiere/index.m3u8",

    "viju+ Megahit":
        "s70378.cdn.ngenix.net/vip_megahit/index.m3u8",

    "viju+ Comedy":
        "s70378.cdn.ngenix.net/vip_comedy/index.m3u8",

    "viju+ Serial":
        "s70378.cdn.ngenix.net/vip_serial/index.m3u8",

    "viju+ Planet":
        "s70378.cdn.ngenix.net/vip_planet/index.m3u8",

    "viju+ Sport":
        "s70378.cdn.ngenix.net/vip_sport/index.m3u8",

    "viju+ Novella":
        "s70378.cdn.ngenix.net/vip_novella/index.m3u8",

    "viju+ Romance":
        "s70378.cdn.ngenix.net/vip_romance/index.m3u8",


    # ========================================================
    # HORROR
    # ========================================================

    "Страшное HD":
        "s70378.cdn.ngenix.net/horror/strashnoe_hd/index.m3u8",

    "Страх HD":
        "s70378.cdn.ngenix.net/horror/strakh_hd/index.m3u8",

    "TRASH HD":
        "s70378.cdn.ngenix.net/trash/trash_hd/index.m3u8",

    "Scream":
        "s70378.cdn.ngenix.net/horror/scream/index.m3u8",


    # ========================================================
    # ЕДА
    # ========================================================

    "Еда":
        "s70378.cdn.ngenix.net/eda/index.m3u8",


    # ========================================================
    # КЛЮЧ
    # ========================================================

    "Ключ":
        "s70378.cdn.ngenix.net/misc/kluch/index.m3u8",

    "Ключ HD":
        "s70378.cdn.ngenix.net/misc/kluch_hd/index.m3u8",

    "Ключ ТВ":
        "s70378.cdn.ngenix.net/misc/kluch_tv/index.m3u8",


    # ========================================================
    # ОСТАЛЬНЫЕ КАНАЛЫ
    #
    # Исходные строки с "..." сохранены как данные,
    # но программа автоматически пометит их INVALID.
    # ========================================================

    ".sci-fi":
        "a3569457567-s70378.cdn.ngenix.net/sony_sci-f...",

    "РЕН ТВ International":
        "a3569457567-s70378.cdn.ngenix.net/ren_tv/1/i...",

    "НТВ Право":
        "a3569457567-s70378.cdn.ngenix.net/ntv_pravo/...",

    "НТВ Сериал":
        "a3569457567-s70378.cdn.ngenix.net/ntv_serial...",

    "National geographic":
        "a3569457567-s70378.cdn.ngenix.net/national_g...",

    "Terra":
        "a3569457567-s70378.cdn.ngenix.net/terra/2/in...",

    "Ocean TV":
        "a3569457567-s70378.cdn.ngenix.net/ocean_tv/1...",

    "Точка РФ":
        "a3569457567-s70378.cdn.ngenix.net/hd_life/1/...",

    "History":
        "a3569457567-s70378.cdn.ngenix.net/history/1/...",

    "H2":
        "a3569457567-s70378.cdn.ngenix.net/history_2/...",

    "Дикий":
        "a3569457567-s70378.cdn.ngenix.net/dikiy/1/in...",

    "RTG HD":
        "a3569457567-s70378.cdn.ngenix.net/rtg_hd/1/i...",

    "DocuBox":
        "a3569457567-s70378.cdn.ngenix.net/docubox/1/...",

    "Galaxy TV":
        "a3569457567-s70378.cdn.ngenix.net/galaxy/1/i...",

    "Глазами туриста":
        "a3569457567-s70378.cdn.ngenix.net/glazami_tu...",

    "Travel+Adventure":
        "a3569457567-s70378.cdn.ngenix.net/travel_and...",

    "The explorers":
        "a3569457567-s70378.cdn.ngenix.net/the_explor...",

    "Viasat Explore":
        "a3569457567-s70378.cdn.ngenix.net/viasat_exp...",

    "Viasat History":
        "a3569457567-s70378.cdn.ngenix.net/viasat_his...",

    "Viasat Nature":
        "a3569457567-s70378.cdn.ngenix.net/viasat_nat...",

    "365 дней":
        "a3569457567-s70378.cdn.ngenix.net/365_dney_t...",

    "Hollywood HD":
        "a3569457567-s70378.cdn.ngenix.net/amc/2/inde...",

    "Amedia 1":
        "a3569457567-s70378.cdn.ngenix.net/amedia_1/2...",

    "Amedia 2":
        "a3569457567-s70378.cdn.ngenix.net/amedia_2/2...",

    "Amedia Hit":
        "a3569457567-s70378.cdn.ngenix.net/amedia_hit...",

    "Amedia Premium HD":
        "a3569457567-s70378.cdn.ngenix.net/amedia_pre...",

    "Bloomberg":
        "a3569457567-s70378.cdn.ngenix.net/bloomberg/...",

    "Shoghakat":
        "a3569457567-s70378.cdn.ngenix.net/shoghakat/...",

    ".Black":
        "a3569457567-s70378.cdn.ngenix.net/sony_turbo...",

    "Телекафе":
        "a3569457567-s70378.cdn.ngenix.net/telecafe/2...",

    "Индийское кино":
        "a3569457567-s70378.cdn.ngenix.net/india_tv/1...",

    "Индия":
        "a3569457567-s70378.cdn.ngenix.net/zee_tv/2/i...",

    "Наше новое кино":
        "a3569457567-s70378.cdn.ngenix.net/nashe_novo...",

    "Киноужас":
        "a3569457567-s70378.cdn.ngenix.net/kinouzhas/...",

    "Киносерия":
        "a3569457567-s70378.cdn.ngenix.net/mnogo_tv/1...",

    "Киносвидание":
        "a3569457567-s70378.cdn.ngenix.net/kinoklub/1...",

    "Дом Кино Премиум":
        "a3569457567-s70378.cdn.ngenix.net/dom_kino_p...",

    "ТВ3":
        "a3569457567-s70378.cdn.ngenix.net/tv_3/2/ind...",

    "TV XXI":
        "a3569457567-s70378.cdn.ngenix.net/tv_xxi/2/i...",

    "VIP Comedy":
        "a3569457567-s70378.cdn.ngenix.net/vip_comedy...",

    "VIP Megahit":
        "a3569457567-s70378.cdn.ngenix.net/vip_megahi...",

    "VIP Premiere":
        "a3569457567-s70378.cdn.ngenix.net/vip_premie...",

    "VIP Serial":
        "a3569457567-s70378.cdn.ngenix.net/vip_serial...",

    "Время":
        "a3569457567-s70378.cdn.ngenix.net/vremia/2/i...",

    "Дом Кино":
        "a3569457567-s70378.cdn.ngenix.net/dom_kino/1...",

    "Euronews":
        "a3569457567-s70378.cdn.ngenix.net/euronews/1...",

    "Еврокино":
        "a3569457567-s70378.cdn.ngenix.net/evrokино/1...",

    "Мир сериала":
        "a3569457567-s70378.cdn.ngenix.net/mir_serial...",

    "FashionBox":
        "a3569457567-s70378.cdn.ngenix.net/fashion_bo...",

    "Filmbox":
        "a3569457567-s70378.cdn.ngenix.net/filmbox/1/...",

    "Filmbox Arthouse":
        "a3569457567-s70378.cdn.ngenix.net/filmbox_ar...",

    "Flixsnip":
        "a3569457567-s70378.cdn.ngenix.net/flixsnip/1...",

    "Fox life":
        "a3569457567-s70378.cdn.ngenix.net/fox_life/1...",

    "Иллюзион+":
        "a3569457567-s70378.cdn.ngenix.net/illusion_p...",

    "Зоопарк":
        "a3569457567-s70378.cdn.ngenix.net/zoopark/2/...",

    "Armenia 1":
        "a3569457567-s70378.cdn.ngenix.net/h1/1/index...",

    "Armenia 2":
        "a3569457567-s70378.cdn.ngenix.net/h2/1/index...",

    "Известия":
        "a3569457567-s70378.cdn.ngenix.net/izvestiya/...",

    "Живи":
        "a3569457567-s70378.cdn.ngenix.net/jivi/1/ind...",

    "ATV Kinoman HD AM":
        "a3569457567-s70378.cdn.ngenix.net/kinoman/1/...",

    "КВН ТВ":
        "a3569457567-s70378.cdn.ngenix.net/kvn_tv/1/i...",

    "Мир 24":
        "a3569457567-s70378.cdn.ngenix.net/mir_24/1/i...",

    "Мир":
        "a3569457567-s70378.cdn.ngenix.net/mir/1/inde...",

    "Ностальгия":
        "a3569457567-s70378.cdn.ngenix.net/nostalgia/...",

    "РБК":
        "a3569457567-s70378.cdn.ngenix.net/rbc/1/inde...",

    "RTVI":
        "a3569457567-s70378.cdn.ngenix.net/rtvi/1/ind...",

    "shant serial":
        "a3569457567-s70378.cdn.ngenix.net/shant_seri...",

    "shant premium":
        "a3569457567-s70378.cdn.ngenix.net/shant_prem...",

    "21TV AM":
        "a3569457567-s70378.cdn.ngenix.net/dar21/1/in...",

    "Mezzo":
        "a3569457567-s70378.cdn.ngenix.net/mezzo/1/in...",

    "Muzzone":
        "a3569457567-s70378.cdn.ngenix.net/muzzone/1/...",

    "Shant music":
        "a3569457567-s70378.cdn.ngenix.net/shant_musi...",

    "Baby TV":
        "a3569457567-s70378.cdn.ngenix.net/baby_tv/2/...",

    "Tiji":
        "a3569457567-s70378.cdn.ngenix.net/tiji/2/ind...",

    "СТС Kids":
        "a3569457567-s70378.cdn.ngenix.net/ctc_kids/1...",

    "Nickelodeon":
        "a3569457567-s70378.cdn.ngenix.net/nickelodeo...",

    "Nicktoons":
        "a3569457567-s70378.cdn.ngenix.net/nicktoons/...",

    "Малыш":
        "a3569457567-s70378.cdn.ngenix.net/malish/1/i...",

    "Gulli Girl":
        "a3569457567-s70378.cdn.ngenix.net/gulli/1/in...",

    "Карусель":
        "a3569457567-s70378.cdn.ngenix.net/karusel/1/...",

    "Da Vinci":
        "a3569457567-s70378.cdn.ngenix.net/da_vinci/1...",

    "Детский мир":
        "a3569457567-s70378.cdn.ngenix.net/detskij_mi...",

    "UFC":
        "a3569457567-s70378.cdn.ngenix.net/ufc/2/inde...",

    "Viasat sport":
        "a3569457567-s70378.cdn.ngenix.net/viasat_spo...",

    "Бокс ТВ":
        "a3569457567-s70378.cdn.ngenix.net/boks_tv/1/...",

    "Матч! Планета":
        "a3569457567-s70378.cdn.ngenix.net/match_plan...",

    "KHL":
        "a3569457567-s70378.cdn.ngenix.net/kxl/1/inde...",

    "MMA-TV.com":
        "a3569457567-s70378.cdn.ngenix.net/m1_global/...",
}


# ============================================================
# 26. СБОР УНИКАЛЬНЫХ РЕЗУЛЬТАТОВ CDN
# ============================================================

def collect_cdn_results(
    results
):
    discovered = {}

    for result in results:

        cdn_result = result.get(
            "cdn_result"
        )

        if not cdn_result:
            continue

        entries = cdn_result.get(
            "found",
            [],
        )

        for entry in entries:

            url = entry.get(
                "url"
            )

            if not url:
                continue

            if url not in discovered:

                discovered[
                    url
                ] = {
                    "name":
                        entry.get(
                            "name",
                            "UNKNOWN",
                        ),
                    "url":
                        url,
                    "source":
                        entry.get(
                            "source",
                            "CDN_DISCOVERY",
                        ),
                    "source_manifest":
                        entry.get(
                            "source_manifest"
                        ),
                    "info":
                        entry.get(
                            "info"
                        ),
                    "found_at":
                        entry.get(
                            "found_at"
                        ),
                }

    return list(
        discovered.values()
    )


# ============================================================
# 27. СОБИРАЕМ ALIAS MATCHES
# ============================================================

def collect_alias_matches(
    results
):
    matches = []

    for result in results:

        alias_result = result.get(
            "alias_result"
        )

        if not alias_result:
            continue

        found = alias_result.get(
            "found",
            [],
        )

        for item in found:

            matches.append(
                {
                    "name":
                        result.get(
                            "name"
                        ),
                    "alias":
                        item.get(
                            "alias"
                        ),
                    "url":
                        item.get(
                            "url"
                        ),
                    "status":
                        item.get(
                            "status"
                        ),
                    "elapsed":
                        item.get(
                            "elapsed",
                            0,
                        ),
                    "found_at":
                        item.get(
                            "found_at"
                        ),
                }
            )

    return matches


# ============================================================
# 28. M3U ENTRY
# ============================================================

def make_m3u_entry(
    number,
    name,
    url,
    group_title,
):
    tvg_id = make_tvg_id(
        name,
        url,
    )

    display_name = make_m3u_name(
        name
    )

    return (
        f'#EXTINF:-1 '
        f'tvg-id="{tvg_id}" '
        f'group-title="{group_title}",'
        f'{number}. '
        f'{display_name}\n'
        f'{url}\n'
    )


# ============================================================
# 29. РАБОЧИЙ M3U БЛОК
# ============================================================

def make_working_m3u(
    results
):
    lines = []

    lines.append(
        "#EXTM3U"
    )

    number = 1

    for result in results:

        if not result.get(
            "live",
            False,
        ):
            continue

        name = result.get(
            "name",
            "UNKNOWN",
        )

        url = result.get(
            "url"
        )

        if not url:
            continue

        lines.append(
            make_m3u_entry(
                number,
                name,
                url,
                "Эфирные ТВ Плюс",
            ).rstrip()
        )

        number += 1

    return "\n".join(
        lines
    )


# ============================================================
# 30. CDN DISCOVERY M3U
# ============================================================

def make_cdn_m3u(
    discovered
):
    lines = []

    lines.append(
        "#EXTM3U"
    )

    number = 1

    for item in discovered:

        url = item.get(
            "url"
        )

        if not url:
            continue

        name = item.get(
            "name",
            "CDN UNKNOWN",
        )

        lines.append(
            make_m3u_entry(
                number,
                name,
                url,
                "CDN DISCOVERY",
            ).rstrip()
        )

        number += 1

    return "\n".join(
        lines
    )


# ============================================================
# 31. ALIAS M3U
# ============================================================

def make_alias_m3u(
    matches
):
    lines = []

    lines.append(
        "#EXTM3U"
    )

    number = 1

    seen = set()

    for item in matches:

        url = item.get(
            "url"
        )

        if not url:
            continue

        if url in seen:
            continue

        seen.add(
            url
        )

        name = item.get(
            "name",
            "UNKNOWN",
        )

        lines.append(
            make_m3u_entry(
                number,
                name,
                url,
                "CDN ALIAS MATCH",
            ).rstrip()
        )

        number += 1

    return "\n".join(
        lines
    )


# ============================================================
# 32. ЗАПИСЬ ПОЛНОГО ОТЧЁТА
# ============================================================

def write_report(
    results,
    discovered,
    alias_matches,
    started_at,
    finished_at,
):
    global REPORT_HANDLE

    alive = [
        item
        for item in results
        if item.get(
            "live",
            False,
        )
    ]

    dead = [
        item
        for item in results
        if not item.get(
            "live",
            False,
        )
    ]

    working_m3u = make_working_m3u(
        results
    )

    alias_m3u = make_alias_m3u(
        alias_matches
    )

    cdn_m3u = make_cdn_m3u(
        discovered
    )

    total_time = (
        finished_at
        - started_at
    ).total_seconds()

    REPORT_HANDLE.write(
        "\n\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "СКАЛА ДРЕГ :: ПОЛНЫЙ ОТЧЁТ\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        f"START: "
        f"{started_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    REPORT_HANDLE.write(
        f"FINISH: "
        f"{finished_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    REPORT_HANDLE.write(
        f"TOTAL TIME: "
        f"{total_time:.3f} sec\n"
    )

    REPORT_HANDLE.write(
        f"CHANNELS: "
        f"{len(results)}\n"
    )

    REPORT_HANDLE.write(
        f"LIVE: "
        f"{len(alive)}\n"
    )

    REPORT_HANDLE.write(
        f"DEAD: "
        f"{len(dead)}\n"
    )

    REPORT_HANDLE.write(
        f"ALIAS MATCHES: "
        f"{len(alias_matches)}\n"
    )

    REPORT_HANDLE.write(
        f"CDN DISCOVERED: "
        f"{len(discovered)}\n"
    )

    REPORT_HANDLE.write(
        "\n"
    )


    # ========================================================
    # РАБОЧИЕ КАНАЛЫ
    # ========================================================

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "РАБОЧИЕ КАНАЛЫ\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    if not alive:

        REPORT_HANDLE.write(
            "Нет рабочих каналов.\n"
        )

    else:

        for number, item in enumerate(
            alive,
            1,
        ):

            REPORT_HANDLE.write(
                f"\n"
                f"[{number}]\n"
            )

            REPORT_HANDLE.write(
                f"CHANNEL: "
                f"{item.get('name')}\n"
            )

            REPORT_HANDLE.write(
                f"URL: "
                f"{item.get('url')}\n"
            )

            REPORT_HANDLE.write(
                f"NODE: "
                f"{item.get('node')}\n"
            )

            REPORT_HANDLE.write(
                f"CHANNEL ID: "
                f"{item.get('channel')}\n"
            )

            REPORT_HANDLE.write(
                f"SOURCE: "
                f"{item.get('source')}\n"
            )

            REPORT_HANDLE.write(
                f"FOUND: "
                f"{item.get('found_at')}\n"
            )

            REPORT_HANDLE.write(
                f"SEARCH TIME: "
                f"{item.get('elapsed', 0):.3f}s\n"
            )


    # ========================================================
    # ГОТОВЫЙ M3U
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "M3U BLOCK :: РАБОЧИЕ КАНАЛЫ\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n\n"
    )

    REPORT_HANDLE.write(
        working_m3u
    )

    REPORT_HANDLE.write(
        "\n"
    )


    # ========================================================
    # ALIAS MATCHES
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "СОПОСТАВЛЕНИЕ КАНАЛОВ С CDN ПО ALIAS\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    if not alias_matches:

        REPORT_HANDLE.write(
            "Совпадений по alias не обнаружено.\n"
        )

    else:

        for number, item in enumerate(
            alias_matches,
            1,
        ):

            REPORT_HANDLE.write(
                f"\n"
                f"[{number}]\n"
            )

            REPORT_HANDLE.write(
                f"CHANNEL: "
                f"{item.get('name')}\n"
            )

            REPORT_HANDLE.write(
                f"ALIAS: "
                f"{item.get('alias')}\n"
            )

            REPORT_HANDLE.write(
                f"URL: "
                f"{item.get('url')}\n"
            )

            REPORT_HANDLE.write(
                f"HTTP: "
                f"{item.get('status')}\n"
            )

            REPORT_HANDLE.write(
                f"FOUND: "
                f"{item.get('found_at')}\n"
            )

            REPORT_HANDLE.write(
                f"REQUEST TIME: "
                f"{item.get('elapsed', 0):.3f}s\n"
            )


    # ========================================================
    # ALIAS M3U
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "M3U BLOCK :: CDN ALIAS MATCH\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n\n"
    )

    REPORT_HANDLE.write(
        alias_m3u
    )

    REPORT_HANDLE.write(
        "\n"
    )


    # ========================================================
    # CDN DISCOVERY
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "КАНАЛЫ / ПОТОКИ, КОТОРЫЕ РЕАЛЬНО ОТДАЛ CDN\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    if not discovered:

        REPORT_HANDLE.write(
            "Дополнительных потоков не обнаружено.\n"
        )

    else:

        for number, item in enumerate(
            discovered,
            1,
        ):

            REPORT_HANDLE.write(
                f"\n"
                f"[{number}]\n"
            )

            REPORT_HANDLE.write(
                f"NAME: "
                f"{item.get('name')}\n"
            )

            REPORT_HANDLE.write(
                f"URL: "
                f"{item.get('url')}\n"
            )

            REPORT_HANDLE.write(
                f"SOURCE: "
                f"{item.get('source')}\n"
            )

            REPORT_HANDLE.write(
                f"SOURCE MANIFEST: "
                f"{item.get('source_manifest')}\n"
            )

            REPORT_HANDLE.write(
                f"STREAM INFO: "
                f"{item.get('info')}\n"
            )

            REPORT_HANDLE.write(
                f"FOUND: "
                f"{item.get('found_at')}\n"
            )


    # ========================================================
    # CDN M3U
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "M3U BLOCK :: CDN DISCOVERY\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n\n"
    )

    REPORT_HANDLE.write(
        cdn_m3u
    )

    REPORT_HANDLE.write(
        "\n"
    )


    # ========================================================
    # НЕРАБОЧИЕ
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "НЕРАБОЧИЕ КАНАЛЫ\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    if not dead:

        REPORT_HANDLE.write(
            "Нерабочих каналов нет.\n"
        )

    else:

        for number, item in enumerate(
            dead,
            1,
        ):

            REPORT_HANDLE.write(
                f"\n"
                f"[{number}]\n"
            )

            REPORT_HANDLE.write(
                f"CHANNEL: "
                f"{item.get('name')}\n"
            )

            REPORT_HANDLE.write(
                f"URL: "
                f"{item.get('url')}\n"
            )

            REPORT_HANDLE.write(
                f"NODE: "
                f"{item.get('node')}\n"
            )

            REPORT_HANDLE.write(
                f"CHANNEL ID: "
                f"{item.get('channel')}\n"
            )

            REPORT_HANDLE.write(
                f"SOURCE: "
                f"{item.get('source')}\n"
            )

            REPORT_HANDLE.write(
                f"SEARCH TIME: "
                f"{item.get('elapsed', 0):.3f}s\n"
            )


    # ========================================================
    # ПОДРОБНАЯ ИНФОРМАЦИЯ ПО ALIAS
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "ПОЛНЫЙ СПИСОК СОЗДАННЫХ ALIAS\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    for number, result in enumerate(
        results,
        1,
    ):

        alias_result = result.get(
            "alias_result"
        )

        if not alias_result:
            continue

        REPORT_HANDLE.write(
            f"\n[{number}] "
            f"{result.get('name')}\n"
        )

        REPORT_HANDLE.write(
            "ALIASES:\n"
        )

        for alias in alias_result.get(
            "aliases",
            [],
        ):

            REPORT_HANDLE.write(
                f"  - {alias}\n"
            )


    # ========================================================
    # CDN NODES
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "CDN NODE SUMMARY\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    nodes = {}

    for result in results:

        node = result.get(
            "node"
        )

        if not node:
            continue

        if node not in nodes:

            nodes[node] = {
                "total":
                    0,
                "live":
                    0,
                "dead":
                    0,
            }

        nodes[node][
            "total"
        ] += 1

        if result.get(
            "live",
            False,
        ):

            nodes[node][
                "live"
            ] += 1

        else:

            nodes[node][
                "dead"
            ] += 1

    for node, data in sorted(
        nodes.items()
    ):

        REPORT_HANDLE.write(
            f"\n"
            f"NODE: {node}\n"
        )

        REPORT_HANDLE.write(
            f"TOTAL: "
            f"{data['total']}\n"
        )

        REPORT_HANDLE.write(
            f"LIVE: "
            f"{data['live']}\n"
        )

        REPORT_HANDLE.write(
            f"DEAD: "
            f"{data['dead']}\n"
        )


    # ========================================================
    # ЗАВЕРШЕНИЕ
    # ========================================================

    REPORT_HANDLE.write(
        "\n"
        + "=" * 72
        + "\n"
    )

    REPORT_HANDLE.write(
        "СКАЛА ДРЕГ :: ОТЧЁТ ЗАВЕРШЁН\n"
    )

    REPORT_HANDLE.write(
        "=" * 72
        + "\n"
    )

    REPORT_HANDLE.flush()


# ============================================================
# 33. ГЛАВНЫЙ ЗАПУСК
# ============================================================

async def main():
    global SESSION
    global SEMAPHORE
    global REPORT_HANDLE

    started_at = datetime.now()

    REPORT_HANDLE = open(
        REPORT_FILE,
        "w",
        encoding="utf-8",
        buffering=1,
    )

    teletype(
        "=" * 72
    )

    teletype(
        "СКАЛА ДРЕГ :: "
        "NGENIX NORMALIZER"
    )

    teletype(
        "РАСШИРЕННЫЙ РЕЖИМ :: "
        "DIRECT + ALIAS + MANIFEST DISCOVERY"
    )

    teletype(
        "=" * 72
    )

    teletype(
        f"START :: "
        f"{started_at.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    teletype(
        f"CPU :: "
        f"{CPU}"
    )

    teletype(
        f"TURBO :: "
        f"{TURBO}"
    )

    teletype(
        f"MAX THREADS :: "
        f"{MAX_THREADS}"
    )

    teletype(
        f"TIMEOUT :: "
        f"{TIMEOUT}s"
    )

    teletype(
        f"CHANNELS :: "
        f"{len(CHANNELS)}"
    )

    teletype(
        "=" * 72
    )

    SEMAPHORE = asyncio.Semaphore(
        max(
            1,
            MAX_THREADS,
        )
    )

    connector = TCPConnector(
        limit=max(
            1,
            MAX_THREADS,
        ),
        limit_per_host=max(
            1,
            MAX_THREADS // 4,
        ),
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )

    SESSION = aiohttp.ClientSession(
        connector=connector,
        timeout=ClientTimeout(
            total=TIMEOUT,
            connect=TIMEOUT,
            sock_connect=TIMEOUT,
            sock_read=TIMEOUT,
        ),
    )

    results = []

    try:

        tasks = [
            asyncio.create_task(
                worker(
                    name,
                    raw,
                )
            )
            for name, raw
            in CHANNELS.items()
        ]

        total = len(tasks)

        console(
            f"СТАРТ :: "
            f"каналов={total}"
        )

        completed = 0

        for task in asyncio.as_completed(
            tasks
        ):

            try:

                result = await task

                results.append(
                    result
                )

                completed += 1

                if result.get(
                    "live",
                    False,
                ):

                    console(
                        f"PROGRESS "
                        f"{completed}/{total} "
                        f":: LIVE "
                        f":: "
                        f"{result.get('name')}"
                    )

                else:

                    console(
                        f"PROGRESS "
                        f"{completed}/{total} "
                        f":: DEAD "
                        f":: "
                        f"{result.get('name')}"
                    )

            except Exception as error:

                completed += 1

                teletype_error(
                    f"WORKER :: "
                    f"{error}"
                )

        order = {
            name:
                position
            for position, name
            in enumerate(
                CHANNELS.keys()
            )
        }

        results.sort(
            key=lambda item:
                order.get(
                    item.get(
                        "name"
                    ),
                    999999,
                )
        )

        alias_matches = (
            collect_alias_matches(
                results
            )
        )

        discovered = (
            collect_cdn_results(
                results
            )
        )

        finished_at = datetime.now()

        write_report(
            results,
            discovered,
            alias_matches,
            started_at,
            finished_at,
        )

        alive = sum(
            1
            for item in results
            if item.get(
                "live",
                False,
            )
        )

        dead = (
            len(results)
            - alive
        )

        console(
            "=========================================="
        )

        console(
            "СКАНИРОВАНИЕ ЗАВЕРШЕНО"
        )

        console(
            f"ВСЕГО :: "
            f"{len(results)}"
        )

        console(
            f"LIVE :: "
            f"{alive}"
        )

        console(
            f"DEAD :: "
            f"{dead}"
        )

        console(
            f"ALIAS MATCH :: "
            f"{len(alias_matches)}"
        )

        console(
            f"CDN DISCOVERY :: "
            f"{len(discovered)}"
        )

        console(
            f"ОТЧЁТ :: "
            f"{REPORT_FILE}"
        )

        console(
            "СКАЛА ДРЕГ :: ГОТОВО"
        )

        console(
            "=========================================="
        )

    finally:

        if SESSION:

            await SESSION.close()

            SESSION = None

        if REPORT_HANDLE:

            teletype(
                "SESSION CLOSED"
            )

            REPORT_HANDLE.close()

            REPORT_HANDLE = None


# ============================================================
# 34. ТОЧКА ВХОДА
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print()

        console(
            "ОСТАНОВКА "
            "ПОЛЬЗОВАТЕЛЕМ"
        )