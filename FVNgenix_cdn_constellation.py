#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DmitryTV -> Ngenix scanner v10.0

ZOYE-style scanner:
  source observations
  -> alias/family normalization
  -> immutable historical knowledge
  -> candidate discovery
  -> adaptive ranking/exploration
  -> HTTP/M3U8/child validation
  -> feedback
  -> online statistical learning
  -> immutable revision

DmitryTV is an observation/artifact source only.
GOLDEN is supervision/reference only.
Existing playlist_ngenix_data.json + _1.._N are never overwritten.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import threading
import time
import logging
import os
import random
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
import requests


# ============================================================
# CONFIG
# ============================================================

PLAYLIST_URL = (
    "http://dmitry-tv.ddns.net/iptv/freesat/gtmedia/ZABAVA/custom_url.m3u"
)

GOLDEN_URL = (
    "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/real_worked.m3u"
)

NGENIX_BASE = "https://zabava-htlive.cdn.ngenix.net"
SCANNER_VERSION = "12.1.2"

DATA_BASE = "playlist_ngenix_data.json"
DATA_PREFIX = "playlist_ngenix_data"

OUTPUT_M3U = "playlist_ngenix_working.m3u"
OUTPUT_SKALA = "skala_ngenix_report.txt"
LOG_FILE = "scan_ngenix_process.log"

OUTPUT_TAILS_TXT = "zabava_tails.txt"
OUTPUT_TAILS_JSON = "zabava_tails.json"
OUTPUT_CANDIDATES = "zabava_candidates.json"
OUTPUT_DIAGNOSTICS = "zabava_diagnostics.json"
OUTPUT_LEARNING = "zabava_learning.json"
OUTPUT_LEARN_REPORT = "zabava_learning_report.txt"
OUTPUT_EVOLUTION = "zabava_evolution.json"
OUTPUT_ROUTE_MATRIX = "zabava_route_matrix.json"
OUTPUT_DISCOVERY = "ngenix_discovery.json"
OUTPUT_DISCOVERY_PATTERNS = "ngenix_route_patterns.json"
OUTPUT_DISCOVERY_EVIDENCE = "ngenix_discovery_evidence.json"

_BASE_OUTPUTS = {
    "m3u": OUTPUT_M3U,
    "skala": OUTPUT_SKALA,
    "log": LOG_FILE,
    "tails_txt": OUTPUT_TAILS_TXT,
    "tails_json": OUTPUT_TAILS_JSON,
    "candidates": OUTPUT_CANDIDATES,
    "diagnostics": OUTPUT_DIAGNOSTICS,
    "learning": OUTPUT_LEARNING,
    "learn_report": OUTPUT_LEARN_REPORT,
    "evolution": OUTPUT_EVOLUTION,
    "route_matrix": OUTPUT_ROUTE_MATRIX,
    "discovery": OUTPUT_DISCOVERY,
    "discovery_patterns": OUTPUT_DISCOVERY_PATTERNS,
    "discovery_evidence": OUTPUT_DISCOVERY_EVIDENCE,
}

KNOWN_ROUTES = ("hls", "hls_region", "hls_regions", "region", "regions")
ROUTE_PRIORITY = {
    "hls": 5,
    "hls_regions": 4,
    "hls_region": 3,
    "regions": 2,
    "region": 1,
    "other": 0,
}

PRIMARY_UA = "WINK/1.40.1 (AndroidTV/9) HlsWinkPlayer"

HTTP_TIMEOUT = 8
CHILD_TIMEOUT = 6
DOWNLOAD_TIMEOUT = 25
# V11.5.1: throughput is increased by concurrent I/O, not by removing validation.
# Override with NGENIX_CONCURRENCY in CI if the CDN/network needs tuning.
CONCURRENCY_LIMIT = max(1, int(os.getenv("NGENIX_CONCURRENCY", "512")))
CONNECTOR_LIMIT = max(CONCURRENCY_LIMIT, int(os.getenv("NGENIX_CONNECTOR_LIMIT", str(CONCURRENCY_LIMIT))))
DNS_CACHE_TTL = max(0, int(os.getenv("NGENIX_DNS_CACHE_TTL", "300")))

VALIDATE_CHILD_PLAYLISTS = True
MAX_CHILD_URLS_TO_CHECK = 2
MAX_CANDIDATES = 5000

MIN_EXPLORATION_RATIO = 0.08
MAX_EXPLORATION_RATIO = 0.35

LEARNING_RATE = 0.08
WEIGHT_MIN = 1.0
WEIGHT_MAX = 35.0

DEFAULT_WEIGHTS = {
    "http_ok": 18.0,
    "m3u8_valid": 18.0,
    "media_playlist": 14.0,
    "stream_variants": 10.0,
    "segments_hint": 8.0,
    "reference_similarity": 12.0,
    "route_probability": 12.0,
    "history_bonus": 8.0,
    "child_valid": 12.0,
    "playability": 10.0,
    "recovery_bonus": 6.0,
}


# ============================================================
# V12.1.2 PERFORMANCE ENGINE
# ============================================================
#
# This layer is deliberately additive.  V11.5.1 remains the algorithmic base:
# no candidate deduplication, no channel suppression, no reduction of validation,
# no removal of discovery hypotheses, and no shortcut around learning/evolution.
#
# The objective is throughput: more useful work per second.  The scanner is I/O
# dominated, so the primary gains come from connection reuse, larger asynchronous
# concurrency, reduced synchronous filesystem churn, cached immutable JSON reads,
# precomputed hot-path values, and bounded task scheduling.
#
# Environment knobs:
#   NGENIX_CONCURRENCY       default 512
#   NGENIX_CONNECTOR_LIMIT   default = concurrency
#   NGENIX_DNS_CACHE_TTL     default 300
#   SKALA_FLUSH_LINES        default 128
#   PERF_TASK_BATCH           default 1024
#
# All values remain overrideable in CI.  If a CDN starts throttling, lower
# NGENIX_CONCURRENCY without changing the scanner's decision logic.

PERF_TASK_BATCH = max(64, int(os.getenv("PERF_TASK_BATCH", "1024")))
PERF_PROGRESS_INTERVAL = max(1, int(os.getenv("PERF_PROGRESS_INTERVAL", "250")))
PERF_WARMUP_CONCURRENCY = max(1, int(os.getenv("PERF_WARMUP_CONCURRENCY", str(CONCURRENCY_LIMIT))))
PERF_MAX_INFLIGHT = max(CONCURRENCY_LIMIT, int(os.getenv("PERF_MAX_INFLIGHT", str(CONCURRENCY_LIMIT * 2))))
PERF_ENABLE_METRICS = os.getenv("PERF_ENABLE_METRICS", "1") != "0"


class PerformanceClock:
    """Tiny monotonic timer used only for telemetry; never affects decisions."""

    __slots__ = ("started", "marks")

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.marks: Dict[str, float] = {}

    def mark(self, name: str) -> float:
        value = time.perf_counter() - self.started
        self.marks[name] = value
        return value

    def elapsed(self) -> float:
        return time.perf_counter() - self.started


class PerformanceCounters:
    """Thread-safe counters for measuring throughput without altering scan results."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started = time.perf_counter()
        self.requests = 0
        self.success = 0
        self.failures = 0
        self.timeouts = 0
        self.bytes = 0
        self.completed = 0
        self.children = 0

    def request_started(self) -> None:
        with self._lock:
            self.requests += 1

    def request_finished(self, ok: bool, size: int = 0) -> None:
        with self._lock:
            self.completed += 1
            self.bytes += max(0, int(size))
            if ok:
                self.success += 1
            else:
                self.failures += 1

    def timeout(self) -> None:
        with self._lock:
            self.timeouts += 1
            self.completed += 1

    def child_checked(self) -> None:
        with self._lock:
            self.children += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            elapsed = max(0.000001, time.perf_counter() - self.started)
            return {
                "requests": self.requests,
                "completed": self.completed,
                "success": self.success,
                "failures": self.failures,
                "timeouts": self.timeouts,
                "children": self.children,
                "bytes": self.bytes,
                "elapsed_seconds": round(elapsed, 3),
                "requests_per_second": round(self.completed / elapsed, 2),
                "bytes_per_second": round(self.bytes / elapsed, 2),
            }


PERF_COUNTERS = PerformanceCounters()


def perf_note_request_start() -> None:
    if PERF_ENABLE_METRICS:
        PERF_COUNTERS.request_started()


def perf_note_request_finish(ok: bool, size: int = 0) -> None:
    if PERF_ENABLE_METRICS:
        PERF_COUNTERS.request_finished(ok, size)


def perf_note_timeout() -> None:
    if PERF_ENABLE_METRICS:
        PERF_COUNTERS.timeout()


def perf_note_child() -> None:
    if PERF_ENABLE_METRICS:
        PERF_COUNTERS.child_checked()


def perf_snapshot() -> Dict[str, Any]:
    if not PERF_ENABLE_METRICS:
        return {}
    return PERF_COUNTERS.snapshot()


class JsonReadCache:
    """Explicit cache façade for repeated immutable revision reads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: Dict[str, Tuple[int, int, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, path: str) -> Optional[Any]:
        try:
            stat = os.stat(path)
        except OSError:
            return None
        sig = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            item = self._items.get(path)
            if item and item[:2] == sig:
                self.hits += 1
                return item[2]
            self.misses += 1
        return None

    def put(self, path: str, value: Any) -> None:
        try:
            stat = os.stat(path)
        except OSError:
            return
        with self._lock:
            self._items[path] = (stat.st_mtime_ns, stat.st_size, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"hits": self.hits, "misses": self.misses, "entries": len(self._items)}


PERF_JSON_CACHE = JsonReadCache()


class HotPathCache:
    """Small bounded cache for pure normalization/route computations."""

    def __init__(self, max_size: int = 200000) -> None:
        self.max_size = max(1024, max_size)
        self._lock = threading.RLock()
        self._data: Dict[Tuple[str, str], Any] = {}
        self.hits = 0
        self.misses = 0

    def get(self, namespace: str, key: str) -> Optional[Any]:
        token = (namespace, key)
        with self._lock:
            if token in self._data:
                self.hits += 1
                return self._data[token]
            self.misses += 1
            return None

    def put(self, namespace: str, key: str, value: Any) -> Any:
        token = (namespace, key)
        with self._lock:
            if len(self._data) >= self.max_size:
                # Drop a deterministic slice instead of doing expensive full LRU work.
                # This cache is an optimization only; a miss always recomputes the value.
                for old_key in list(self._data)[: max(1, self.max_size // 16)]:
                    self._data.pop(old_key, None)
            self._data[token] = value
        return value

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"hits": self.hits, "misses": self.misses, "entries": len(self._data)}


PERF_HOT_CACHE = HotPathCache()


def perf_cached_normalize_alias(alias: str) -> str:
    cached = PERF_HOT_CACHE.get("alias", alias)
    if cached is not None:
        return cached
    value = normalize_alias(alias)
    return PERF_HOT_CACHE.put("alias", alias, value)


def perf_cached_route(url: str) -> str:
    cached = PERF_HOT_CACHE.get("route", url)
    if cached is not None:
        return cached
    value = route_from_url(url)
    return PERF_HOT_CACHE.put("route", url, value)


def perf_cached_url(route: str, alias: str) -> str:
    key = route + "\0" + alias
    cached = PERF_HOT_CACHE.get("candidate_url", key)
    if cached is not None:
        return cached
    value = build_candidate_url(route, alias)
    return PERF_HOT_CACHE.put("candidate_url", key, value)


class AsyncGate:
    """Bounded gate used for I/O pressure without changing task semantics."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self._sem = asyncio.Semaphore(self.limit)

    async def __aenter__(self) -> "AsyncGate":
        await self._sem.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._sem.release()

    def available(self) -> int:
        # _value is an implementation detail, but stable for asyncio.Semaphore and
        # used only for telemetry.  Returning a lower bound is sufficient.
        return max(0, int(getattr(self._sem, "_value", 0)))


class AsyncBatchRunner:
    """Run many awaitables in bounded batches while retaining every result."""

    def __init__(self, batch_size: int = PERF_TASK_BATCH) -> None:
        self.batch_size = max(1, batch_size)

    async def run(self, coroutines: List[Any]) -> List[Any]:
        if not coroutines:
            return []
        results: List[Any] = []
        for start in range(0, len(coroutines), self.batch_size):
            batch = coroutines[start : start + self.batch_size]
            results.extend(await asyncio.gather(*batch))
        return results


PERF_BATCH_RUNNER = AsyncBatchRunner()


class SessionTuning:
    """Centralized aiohttp tuning values; no validation rules live here."""

    def __init__(self) -> None:
        self.connector_limit = CONNECTOR_LIMIT
        self.per_host_limit = CONNECTOR_LIMIT
        self.dns_cache_ttl = DNS_CACHE_TTL
        self.enable_cleanup_closed = True
        self.keepalive_timeout = float(os.getenv("NGENIX_KEEPALIVE", "30"))
        self.limit = CONCURRENCY_LIMIT

    def connector(self) -> aiohttp.TCPConnector:
        return aiohttp.TCPConnector(
            limit=self.connector_limit,
            limit_per_host=self.per_host_limit,
            ttl_dns_cache=self.dns_cache_ttl,
            enable_cleanup_closed=self.enable_cleanup_closed,
            keepalive_timeout=self.keepalive_timeout,
        )

    def client_timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(
            total=HTTP_TIMEOUT,
            connect=min(3, HTTP_TIMEOUT),
            sock_connect=min(3, HTTP_TIMEOUT),
            sock_read=HTTP_TIMEOUT,
        )

    def child_timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(
            total=CHILD_TIMEOUT,
            connect=min(3, CHILD_TIMEOUT),
            sock_connect=min(3, CHILD_TIMEOUT),
            sock_read=CHILD_TIMEOUT,
        )


PERF_SESSION_TUNING = SessionTuning()
PERF_HTTP_TIMEOUT = PERF_SESSION_TUNING.client_timeout()
PERF_CHILD_TIMEOUT = PERF_SESSION_TUNING.child_timeout()


class AsyncRequestMeter:
    """Non-invasive per-request timing telemetry."""

    __slots__ = ("started", "url", "kind")

    def __init__(self, url: str, kind: str = "parent") -> None:
        self.started = time.perf_counter()
        self.url = url
        self.kind = kind

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000.0, 3)


class PerformanceRegistry:
    """Run-level performance metadata kept separate from learning state."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.stages: Dict[str, Dict[str, float]] = {}

    def start(self, name: str) -> None:
        self.stages[name] = {"start": time.perf_counter()}

    def stop(self, name: str) -> float:
        row = self.stages.setdefault(name, {})
        row["end"] = time.perf_counter()
        start = row.get("start", row["end"])
        row["seconds"] = round(row["end"] - start, 6)
        return row["seconds"]

    def report(self) -> Dict[str, Any]:
        total = max(0.000001, time.perf_counter() - self.started)
        return {
            "total_seconds": round(total, 6),
            "stages": {
                name: dict(values)
                for name, values in self.stages.items()
            },
            "counters": perf_snapshot(),
            "json_cache": PERF_JSON_CACHE.stats(),
            "hot_cache": PERF_HOT_CACHE.stats(),
            "configuration": {
                "concurrency": CONCURRENCY_LIMIT,
                "connector_limit": CONNECTOR_LIMIT,
                "task_batch": PERF_TASK_BATCH,
                "max_inflight": PERF_MAX_INFLIGHT,
            },
        }


PERF_REGISTRY = PerformanceRegistry()


def perf_stage_start(name: str) -> None:
    if PERF_ENABLE_METRICS:
        PERF_REGISTRY.start(name)


def perf_stage_stop(name: str) -> None:
    if PERF_ENABLE_METRICS:
        PERF_REGISTRY.stop(name)


def perf_report() -> Dict[str, Any]:
    if not PERF_ENABLE_METRICS:
        return {}
    return PERF_REGISTRY.report()


# The following small pure helpers are intentionally explicit.  They make the hot path
# cheap and measurable while keeping all source observations and candidate rows intact.


def perf_prepare_candidate_key(candidate: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        candidate.get("alias", ""),
        candidate.get("route", ""),
        candidate.get("url", ""),
    )


def perf_prepare_prediction_index(
    candidates: List[Dict[str, Any]],
    predictions: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    index: Dict[int, Dict[str, Any]] = {}
    for position, candidate in enumerate(candidates):
        index[position] = predictions.get(perf_prepare_candidate_key(candidate), {})
    return index


def perf_safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def perf_safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def perf_chunks(values: List[Any], size: int = PERF_TASK_BATCH):
    size = max(1, int(size))
    for start in range(0, len(values), size):
        yield values[start : start + size]


def perf_count_working(results: List[Dict[str, Any]]) -> int:
    return sum(1 for row in results if row.get("working"))


def perf_count_failed(results: List[Dict[str, Any]]) -> int:
    return sum(1 for row in results if not row.get("working"))


def perf_result_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        row.get("alias", ""),
        row.get("route", ""),
        row.get("url", ""),
    )


def perf_sort_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    candidate = row.get("candidate", row)
    return (
        candidate.get("sequence", 0),
        candidate.get("family", ""),
        candidate.get("ua_index", 0),
    )


def perf_clone_headers(base: Dict[str, str], user_agent: str) -> Dict[str, str]:
    # A new mapping is required because callers may mutate it; sharing it would alter semantics.
    return {
        **base,
        "User-Agent": user_agent,
    }


# ============================================================
# PERFORMANCE ENGINE EXTENSIONS / EXPLICIT STAGE HOOKS
# ============================================================
# These functions are deliberately separate from the scanner's evidence logic.  They can be
# instrumented, disabled, or tuned without changing route discovery, ranking, learning,
# forensic comparison, or revision behavior.


def performance_configuration() -> Dict[str, Any]:
    return {
        "scanner_version": SCANNER_VERSION,
        "concurrency_limit": CONCURRENCY_LIMIT,
        "connector_limit": CONNECTOR_LIMIT,
        "dns_cache_ttl": DNS_CACHE_TTL,
        "task_batch": PERF_TASK_BATCH,
        "max_inflight": PERF_MAX_INFLIGHT,
        "warmup_concurrency": PERF_WARMUP_CONCURRENCY,
        "http_timeout": HTTP_TIMEOUT,
        "child_timeout": CHILD_TIMEOUT,
        "download_timeout": DOWNLOAD_TIMEOUT,
        "validate_child_playlists": VALIDATE_CHILD_PLAYLISTS,
        "max_child_urls": MAX_CHILD_URLS_TO_CHECK,
    }


def performance_log_start() -> None:
    if PERF_ENABLE_METRICS:
        skala(
            "PERFORMANCE ENGINE v12.1.2: "
            f"concurrency={CONCURRENCY_LIMIT} "
            f"connector={CONNECTOR_LIMIT} "
            f"batch={PERF_TASK_BATCH}",
            "INFO",
        )


def performance_log_finish() -> None:
    if PERF_ENABLE_METRICS:
        report = perf_report()
        counters = report.get("counters", {})
        skala(
            "PERFORMANCE SUMMARY: "
            f"completed={counters.get('completed', 0)} "
            f"rps={counters.get('requests_per_second', 0)} "
            f"bytes/s={counters.get('bytes_per_second', 0)}",
            "INFO",
        )


# Keep these compatibility wrappers so future tuning can be applied at one point without
# touching the original scanner call sites.

def perf_http_headers(user_agent: str = PRIMARY_UA) -> Dict[str, str]:
    return perf_clone_headers(
        {
            "User-Agent": PRIMARY_UA,
            "Accept": "*/*",
        },
        user_agent,
    )


def perf_hls_headers(user_agent: str = PRIMARY_UA) -> Dict[str, str]:
    return perf_clone_headers(
        {
            "User-Agent": PRIMARY_UA,
            "Accept": "*/*",
        },
        user_agent,
    )


def perf_download_headers() -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        "Connection": "keep-alive",
    }


def perf_json_cache_clear() -> None:
    PERF_JSON_CACHE.clear()
    with _JSON_CACHE_LOCK:
        _JSON_CACHE.clear()


def perf_reset_counters() -> None:
    global PERF_COUNTERS
    PERF_COUNTERS = PerformanceCounters()


def perf_reset_run_state() -> None:
    perf_reset_counters()
    PERF_JSON_CACHE.clear()
    with _JSON_CACHE_LOCK:
        _JSON_CACHE.clear()


# Additional explicit utilities keep the performance subsystem self-contained and make it
# possible to profile each stage without changing the algorithm.  They intentionally return
# copies or scalar values rather than mutating evidence structures.


def perf_measure_list(values: List[Any]) -> Dict[str, int]:
    return {
        "count": len(values),
        "non_null": sum(value is not None for value in values),
    }


def perf_measure_mapping(values: Dict[Any, Any]) -> Dict[str, int]:
    return {
        "count": len(values),
        "non_null_values": sum(value is not None for value in values.values()),
    }


def perf_candidate_statistics(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    families: Dict[str, int] = defaultdict(int)
    routes: Dict[str, int] = defaultdict(int)
    aliases: Dict[str, int] = defaultdict(int)
    for candidate in candidates:
        families[str(candidate.get("family", ""))] += 1
        routes[str(candidate.get("route", ""))] += 1
        aliases[str(candidate.get("alias", ""))] += 1
    return {
        "candidates": len(candidates),
        "families": dict(families),
        "routes": dict(routes),
        "aliases": len(aliases),
    }


def perf_result_statistics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    reasons: Dict[str, int] = defaultdict(int)
    routes: Dict[str, int] = defaultdict(int)
    for row in results:
        reasons[str(row.get("reason", "UNKNOWN"))] += 1
        routes[str(row.get("route", ""))] += 1
    return {
        "results": len(results),
        "working": perf_count_working(results),
        "failed": perf_count_failed(results),
        "reasons": dict(reasons),
        "routes": dict(routes),
    }


def perf_memory_statistics(memory: Dict[str, Any]) -> Dict[str, int]:
    return {
        "channels": len(memory.get("channels", {})),
        "families": len(memory.get("families", {})),
        "routes": len(memory.get("routes", {})),
        "patterns": len(memory.get("patterns", {})),
    }


def perf_revision_statistics(chain: List[Tuple[int, str]]) -> Dict[str, Any]:
    return {
        "revisions": len(chain),
        "latest_revision": chain[-1][0] if chain else None,
        "latest_file": chain[-1][1] if chain else None,
    }


def perf_format_summary(report: Dict[str, Any]) -> str:
    counters = report.get("counters", {})
    return (
        f"completed={counters.get('completed', 0)} "
        f"success={counters.get('success', 0)} "
        f"failed={counters.get('failures', 0)} "
        f"timeouts={counters.get('timeouts', 0)} "
        f"rps={counters.get('requests_per_second', 0)}"
    )


# End of performance layer.  The original scanner continues below unchanged.

# ============================================================
# UTILITIES
# ============================================================

def now_local() -> str:
    return datetime.now().astimezone().isoformat()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def versioned_path(filename: str) -> str:
    if not os.path.exists(filename):
        return filename

    root, ext = os.path.splitext(filename)
    number = 1

    while os.path.exists(f"{root}_{number}{ext}"):
        number += 1

    return f"{root}_{number}{ext}"


_JSON_CACHE: Dict[str, Tuple[int, int, Any]] = {}
_JSON_CACHE_LOCK = threading.RLock()


def write_json(path: str, data: Any) -> None:
    # Keep the original serialization format exactly: ensure_ascii=False + indent=2.
    # The speedup comes from avoiding repeated setup/lookup work and from using a
    # single buffered file handle for the complete payload.
    payload = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )
    with open(path, "w", encoding="utf-8", buffering=1024 * 1024) as f:
        f.write(payload)
    try:
        stat = os.stat(path)
        with _JSON_CACHE_LOCK:
            _JSON_CACHE[path] = (stat.st_mtime_ns, stat.st_size, data)
    except OSError:
        pass


def load_json_safe(path: str) -> Optional[Dict[str, Any]]:
    try:
        stat = os.stat(path)
        signature = (stat.st_mtime_ns, stat.st_size)
        with _JSON_CACHE_LOCK:
            cached = _JSON_CACHE.get(path)
            if cached and cached[:2] == signature:
                return cached[2]

        with open(path, "r", encoding="utf-8", buffering=1024 * 1024) as f:
            value = json.load(f)

        with _JSON_CACHE_LOCK:
            _JSON_CACHE[path] = (signature[0], signature[1], value)
        return value
    except Exception as exc:
        skala(f"Cannot read {path}: {exc}", "WARN")
        return None


_SKALA_LOCK = threading.RLock()
_SKALA_BUFFER: List[str] = []
_SKALA_FLUSH_LINES = max(16, int(os.getenv("SKALA_FLUSH_LINES", "128")))


def _flush_skala_locked() -> None:
    if not _SKALA_BUFFER:
        return
    payload = "".join(_SKALA_BUFFER)
    _SKALA_BUFFER.clear()
    with open(OUTPUT_SKALA, "a", encoding="utf-8", buffering=1024 * 1024) as f:
        f.write(payload)


def flush_skala() -> None:
    with _SKALA_LOCK:
        _flush_skala_locked()


def skala(message: str, level: str = "INFO") -> None:
    line = f"{now_local()} [{level:<7}] {message}"
    print(line, flush=False)
    with _SKALA_LOCK:
        _SKALA_BUFFER.append(line + "\n")
        if len(_SKALA_BUFFER) >= _SKALA_FLUSH_LINES:
            _flush_skala_locked()


atexit.register(flush_skala)


def resolve_output_paths() -> None:
    global OUTPUT_M3U
    global OUTPUT_SKALA
    global LOG_FILE
    global OUTPUT_TAILS_TXT
    global OUTPUT_TAILS_JSON
    global OUTPUT_CANDIDATES
    global OUTPUT_DIAGNOSTICS
    global OUTPUT_LEARNING
    global OUTPUT_LEARN_REPORT
    global OUTPUT_EVOLUTION
    global OUTPUT_ROUTE_MATRIX
    global OUTPUT_DISCOVERY
    global OUTPUT_DISCOVERY_PATTERNS
    global OUTPUT_DISCOVERY_EVIDENCE

    OUTPUT_M3U = versioned_path(_BASE_OUTPUTS["m3u"])
    OUTPUT_SKALA = versioned_path(_BASE_OUTPUTS["skala"])
    LOG_FILE = versioned_path(_BASE_OUTPUTS["log"])
    OUTPUT_TAILS_TXT = versioned_path(_BASE_OUTPUTS["tails_txt"])
    OUTPUT_TAILS_JSON = versioned_path(_BASE_OUTPUTS["tails_json"])
    OUTPUT_CANDIDATES = versioned_path(_BASE_OUTPUTS["candidates"])
    OUTPUT_DIAGNOSTICS = versioned_path(_BASE_OUTPUTS["diagnostics"])
    OUTPUT_LEARNING = versioned_path(_BASE_OUTPUTS["learning"])
    OUTPUT_LEARN_REPORT = versioned_path(_BASE_OUTPUTS["learn_report"])
    OUTPUT_EVOLUTION = versioned_path(_BASE_OUTPUTS["evolution"])
    OUTPUT_ROUTE_MATRIX = versioned_path(_BASE_OUTPUTS["route_matrix"])
    OUTPUT_DISCOVERY = versioned_path(_BASE_OUTPUTS["discovery"])
    OUTPUT_DISCOVERY_PATTERNS = versioned_path(_BASE_OUTPUTS["discovery_patterns"])
    OUTPUT_DISCOVERY_EVIDENCE = versioned_path(_BASE_OUTPUTS["discovery_evidence"])


# ============================================================
# ALIAS / ROUTE NORMALIZATION
# ============================================================

def normalize_alias(alias: str) -> str:
    """
    Family identity.

    CH_1TV       -> CH_1TV
    CH_1TV_2     -> CH_1TV
    CH_1TV-1     -> CH_1TV
    CH_RUSSIA1_6 -> CH_RUSSIA1
    """
    return re.sub(r"(?:[_-]\d+)+$", "", (alias or "").strip())


def route_from_url(url: str) -> str:
    """Return the exact route prefix instead of collapsing wildcard suffixes."""
    path = urlparse(url).path or ""
    normalized = re.sub(r"/{2,}", "/", path).strip("/")
    parts = normalized.split("/") if normalized else []
    try:
        ch_index = next(i for i, part in enumerate(parts) if re.fullmatch(r"CH_[^/]+", part, re.I))
    except StopIteration:
        return "other"
    prefix_parts = parts[:ch_index]
    if not prefix_parts:
        return "other"
    prefix = "/".join(prefix_parts).lower()
    if prefix == "hls":
        return "hls"
    if prefix == "region":
        return "region"
    if prefix == "regions":
        return "regions"
    if prefix.startswith("hls/region"):
        return "hls_" + prefix.split("/", 1)[1].replace("/", "_")
    if prefix.startswith("region"):
        return prefix
    if prefix.startswith("regions"):
        return prefix
    # V10 keeps previously unknown structures as an exact route token.
    return "path_" + re.sub(r"[^a-z0-9_]+", "_", prefix)


def build_candidate_url(route: str, alias: str) -> str:
    if route == "hls":
        prefix = "hls"
    elif route == "hls_region":
        prefix = "hls/region"
    elif route == "hls_regions":
        prefix = "hls/regions"
    elif route == "region":
        prefix = "region"
    elif route == "regions":
        prefix = "regions"
    elif route.startswith("path_"):
        prefix = route[len("path_"):].replace("_", "/")
    elif re.fullmatch(r"hls_region[^/]*", route, re.I):
        suffix = route[len("hls_region"):]
        prefix = f"hls/region{suffix}"
    elif re.fullmatch(r"hls_regions[^/]*", route, re.I):
        suffix = route[len("hls_regions"):]
        prefix = f"hls/regions{suffix}"
    elif re.fullmatch(r"region[^/]*", route, re.I):
        prefix = route
    elif re.fullmatch(r"regions[^/]*", route, re.I):
        prefix = route
    else:
        prefix = route.strip("/") or "hls"
    return f"{NGENIX_BASE}/{prefix}/{alias}/variant.m3u8"


def route_supported(route: str) -> bool:
    return (
        route in KNOWN_ROUTES
        or bool(re.fullmatch(r"hls_region[^/]*", route, re.I))
        or bool(re.fullmatch(r"hls_regions[^/]*", route, re.I))
        or bool(re.fullmatch(r"region[^/]*", route, re.I))
        or bool(re.fullmatch(r"regions[^/]*", route, re.I))
    )

# ============================================================
# ROUTE MATRIX v9
# ============================================================

def route_family_variants(alias: str) -> List[Tuple[str, str]]:
    return [(route, build_candidate_url(route, alias)) for route in KNOWN_ROUTES]


def write_route_matrix(aliases: List[str]) -> None:
    write_json(OUTPUT_ROUTE_MATRIX, {
        "schema_version": 1,
        "scanner_version": SCANNER_VERSION,
        "created_at": now_utc(),
        "route_families": list(KNOWN_ROUTES),
        "aliases": [
            {"alias": alias, "variants": [
                {"route": route, "url": url}
                for route, url in route_family_variants(alias)
            ]}
            for alias in sorted(set(aliases))
        ],
    })


# ============================================================
# V10 AUTONOMOUS NGENIX DISCOVERY
# ============================================================

DISCOVERY_MAX_CANDIDATES = int(os.getenv("NGENIX_DISCOVERY_MAX", "2500"))
DISCOVERY_DEPTH = int(os.getenv("NGENIX_DISCOVERY_DEPTH", "3"))
DISCOVERY_CONFIRMATIONS = int(os.getenv("NGENIX_DISCOVERY_CONFIRMATIONS", "2"))
DISCOVERY_MIN_CONFIDENCE = float(os.getenv("NGENIX_DISCOVERY_MIN_CONFIDENCE", "0.72"))
DISCOVERY_PROBE_TIMEOUT = int(os.getenv("NGENIX_DISCOVERY_TIMEOUT", "6"))
DISCOVERY_TOKENS = (
    "hls", "region", "regions", "live", "stream", "streams", "tv",
    "channel", "channels", "broadcast", "playlist", "playlists", "media",
    "video", "videos", "content", "cdn", "mobile", "android", "wink",
)


def route_prefix_from_candidate(route: str) -> str:
    if route.startswith("path_"):
        return route[5:].replace("_", "/")
    if route == "hls_region":
        return "hls/region"
    if route == "hls_regions":
        return "hls/regions"
    return route


def make_route_token(prefix: str) -> str:
    prefix = re.sub(r"/{2,}", "/", prefix.strip("/ ")).lower()
    if prefix in KNOWN_ROUTES:
        return prefix
    if prefix.startswith("hls/"):
        return "path_" + re.sub(r"[^a-z0-9_]+", "_", prefix)
    return "path_" + re.sub(r"[^a-z0-9_]+", "_", prefix)


def load_discovery_memory() -> Dict[str, Any]:
    data = load_json_safe(OUTPUT_DISCOVERY)
    if not isinstance(data, dict):
        return {
            "schema_version": 2,
            "scanner_version": SCANNER_VERSION,
            "routes": {},
            "patterns": {},
            "channels": {},
            "evidence": [],
            "rejected": {},
        }
    return data


def _extract_route_prefix(url: str) -> Optional[str]:
    path = urlparse(url).path or ""
    path = re.sub(r"/{2,}", "/", path).strip("/")
    parts = path.split("/") if path else []
    for i, part in enumerate(parts):
        if re.fullmatch(r"CH_[A-Za-z0-9_-]+", part, re.I):
            return "/".join(parts[:i])
    return None


def discover_observed_route_patterns(observations: List[Dict[str, Any]]) -> List[str]:
    prefixes = set()
    for obs in observations:
        prefix = _extract_route_prefix(obs.get("source_url", ""))
        if prefix:
            prefixes.add(prefix.lower())
    return sorted(prefixes)


def generate_structural_route_hypotheses(
    observations: List[Dict[str, Any]],
    kb: Dict[str, Any],
    memory: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Generate bounded structural hypotheses, not an unbounded internet crawler."""
    prefixes = set(discover_observed_route_patterns(observations))
    for route in kb.get("routes", {}):
        prefix = route_prefix_from_candidate(route)
        if prefix:
            prefixes.add(prefix.lower())
    for route in memory.get("routes", {}):
        prefix = route_prefix_from_candidate(route)
        if prefix:
            prefixes.add(prefix.lower())

    # Seed one-level structures from observed vocabulary.
    tokens = set(DISCOVERY_TOKENS)
    for prefix in list(prefixes):
        tokens.update(x for x in prefix.split("/") if x)

    hypotheses = set(prefixes)
    for token in tokens:
        hypotheses.add(token)
    for a in tokens:
        hypotheses.add(f"hls/{a}")
        hypotheses.add(f"{a}/channel")
        hypotheses.add(f"{a}/channels")
    for a in tokens:
        for b in tokens:
            if a == b:
                continue
            hypotheses.add(f"hls/{a}/{b}")
            if len(hypotheses) >= DISCOVERY_MAX_CANDIDATES * 2:
                break
        if len(hypotheses) >= DISCOVERY_MAX_CANDIDATES * 2:
            break

    # Preserve exact observed wildcard families such as /hls/region*/CH_*.
    for prefix in list(prefixes):
        m = re.fullmatch(r"hls/(regions?|regions?\d+|region\d+)", prefix)
        if m:
            base = m.group(1)
            for n in range(1, 33):
                hypotheses.add(f"hls/{base}{n}")

    rows = []
    for prefix in sorted(hypotheses):
        if not prefix or prefix.count("/") + 1 > DISCOVERY_DEPTH:
            continue
        route = make_route_token(prefix)
        rows.append({
            "route": route,
            "prefix": prefix,
            "kind": "structural_hypothesis",
            "confidence": 0.05,
        })
        if len(rows) >= DISCOVERY_MAX_CANDIDATES:
            break
    return rows


def generate_autonomous_discovery_candidates(
    observations: List[Dict[str, Any]],
    kb: Dict[str, Any],
    golden: Dict[str, Any],
    memory: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build candidates from observed, historical, learned and synthetic structures."""
    aliases = {o.get("alias") for o in observations if o.get("alias")}
    aliases.update(kb.get("seen_aliases", set()))
    aliases.update((golden or {}).get("alias_set", set()))
    aliases.update(memory.get("channels", {}).keys())
    aliases = sorted(a for a in aliases if isinstance(a, str) and a.startswith("CH_"))

    hypotheses = generate_structural_route_hypotheses(observations, kb, memory)
    existing = {
        (o.get("alias"), o.get("route"), o.get("url"))
        for o in observations
    }
    candidates = []
    for h in hypotheses:
        route = h["route"]
        prefix = h["prefix"]
        for alias in aliases:
            url = f"{NGENIX_BASE}/{prefix}/{alias}/variant.m3u8"
            key = (alias, route, url)
            if key in existing:
                continue
            candidates.append({
                "alias": alias,
                "family": normalize_alias(alias),
                "route": route,
                "url": url,
                "origins": ["autonomous_structural_discovery"],
                "historical_score": 0.0,
                "sources": [],
                "discovery": {
                    "hypothesis": prefix,
                    "kind": h["kind"],
                    "confidence": h["confidence"],
                },
            })
            if len(candidates) >= DISCOVERY_MAX_CANDIDATES:
                break
        if len(candidates) >= DISCOVERY_MAX_CANDIDATES:
            break

    meta = {
        "hypotheses": len(hypotheses),
        "aliases": len(aliases),
        "generated": len(candidates),
        "depth": DISCOVERY_DEPTH,
        "max_candidates": DISCOVERY_MAX_CANDIDATES,
    }
    return candidates, meta


def merge_candidates(base: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    index = {(c["alias"], c["route"], c["url"]): c for c in base}
    for c in extra:
        key = (c["alias"], c["route"], c["url"])
        if key not in index:
            index[key] = c
        else:
            origins = index[key].setdefault("origins", [])
            for origin in c.get("origins", []):
                if origin not in origins:
                    origins.append(origin)
            if c.get("discovery"):
                index[key]["discovery"] = c["discovery"]
    rows = list(index.values())
    rows.sort(key=lambda x: (x.get("alias", ""), x.get("route", ""), x.get("url", "")))
    return rows[:MAX_CANDIDATES]


def update_discovery_memory(
    memory: Dict[str, Any],
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    routes = memory.setdefault("routes", {})
    patterns = memory.setdefault("patterns", {})
    channels = memory.setdefault("channels", {})
    evidence = memory.setdefault("evidence", [])
    rejected = memory.setdefault("rejected", {})
    discovered = confirmed = rejected_count = 0

    for row in results:
        d = row.get("discovery") or {}
        if not d:
            continue
        route = row.get("route")
        prefix = d.get("hypothesis") or route_prefix_from_candidate(route or "")
        working = bool(row.get("working"))
        score = float(row.get("rank_score", 0) or 0)
        state = routes.setdefault(route, {
            "prefix": prefix, "attempts": 0, "ok": 0, "fail": 0,
            "confidence": 0.05, "status": "unknown",
        })
        state["attempts"] += 1
        state["ok" if working else "fail"] += 1
        total = state["attempts"]
        rate = state["ok"] / total if total else 0.0
        confirmations = state["ok"]
        state["confidence"] = round(clamp(0.05 + 0.90 * rate + min(confirmations, 3) * 0.02, 0.0, 0.99), 4)
        if working and confirmations >= DISCOVERY_CONFIRMATIONS and state["confidence"] >= DISCOVERY_MIN_CONFIDENCE:
            state["status"] = "confirmed"
            confirmed += 1
        elif working:
            state["status"] = "candidate"
            discovered += 1
        else:
            state["status"] = "rejected" if total >= DISCOVERY_CONFIRMATIONS and rate == 0 else "unknown"
            if state["status"] == "rejected":
                rejected_count += 1
                rejected[route] = {"prefix": prefix, "attempts": total, "last_reason": row.get("reason", "")}
        pattern = patterns.setdefault(prefix, {"attempts": 0, "ok": 0, "fail": 0, "channels": []})
        pattern["attempts"] += 1
        pattern["ok" if working else "fail"] += 1
        alias = row.get("alias")
        if alias and alias not in pattern["channels"]:
            pattern["channels"].append(alias)
        channels.setdefault(alias, {}).setdefault(route, {"attempts": 0, "ok": 0})
        channels[alias][route]["attempts"] += 1
        channels[alias][route]["ok"] += int(working)
        evidence.append({
            "timestamp": now_utc(), "alias": alias, "route": route,
            "prefix": prefix, "url": row.get("url"),
            "working": working, "http_status": row.get("http_status"),
            "reason": row.get("reason", ""), "rank_score": score,
        })

    memory["evidence"] = evidence[-10000:]
    memory["scanner_version"] = SCANNER_VERSION
    return {
        "discovery_results": sum(1 for r in results if r.get("discovery")),
        "candidate_routes": discovered,
        "confirmed_routes": confirmed,
        "rejected_routes": rejected_count,
    }


def write_discovery_outputs(memory: Dict[str, Any], summary: Dict[str, Any]) -> None:
    payload = dict(memory)
    payload["schema_version"] = 2
    payload["scanner_version"] = SCANNER_VERSION
    payload["updated_at"] = now_utc()
    payload["last_summary"] = summary
    write_json(OUTPUT_DISCOVERY, payload)
    write_json(OUTPUT_DISCOVERY_PATTERNS, {
        "schema_version": 1,
        "scanner_version": SCANNER_VERSION,
        "updated_at": now_utc(),
        "patterns": memory.get("patterns", {}),
        "routes": memory.get("routes", {}),
    })
    write_json(OUTPUT_DISCOVERY_EVIDENCE, {
        "schema_version": 1,
        "scanner_version": SCANNER_VERSION,
        "updated_at": now_utc(),
        "summary": summary,
        "evidence": memory.get("evidence", [])[-10000:],
    })


# ============================================================
# IMMUTABLE HISTORY
# ============================================================

def list_data_revisions() -> List[Tuple[int, str]]:
    revisions: List[Tuple[int, str]] = []

    if os.path.exists(DATA_BASE):
        revisions.append((0, DATA_BASE))

    pattern = re.compile(
        rf"^{re.escape(DATA_PREFIX)}_(\d+)\.json$"
    )

    for filename in os.listdir("."):
        match = pattern.match(filename)

        if match:
            revisions.append(
                (int(match.group(1)), filename)
            )

    return sorted(revisions)


def next_revision_number() -> int:
    revisions = list_data_revisions()
    return revisions[-1][0] + 1 if revisions else 0


def empty_knowledge() -> Dict[str, Any]:
    return {
        "channels": {},
        "families": {},
        "routes": {
            route: {"ok": 0, "fail": 0}
            for route in KNOWN_ROUTES
        },
        "patterns": {},
        "candidate_stats": {},
        "historical_best": {},
        "recovery_queue": {},
        "seen_aliases": set(),
        "revisions_applied": [],
        "last_working_map": {},
        "learned_route_rates": {},
        "learned_family_route_rates": {},
        "learned_feature_weights": {},
    }


def observation_count(
    kb: Dict[str, Any],
    alias: str,
    route: str,
) -> int:
    state = (
        kb.get("channels", {})
        .get(alias, {})
        .get(route, {})
    )

    return int(state.get("ok", 0)) + int(
        state.get("fail", 0)
    )


def recovery_priority(
    attempts: int,
    reason: str,
    last_score: float,
) -> float:
    score = min(30.0, attempts * 2.0)

    if reason in {"TIMEOUT", "HTTP_5XX", "REQUEST_ERROR"}:
        score += 30.0
    elif reason in {"HTTP_403", "HTTP_401"}:
        score += 12.0
    elif reason == "HTTP_404":
        score += 4.0

    if last_score >= 70:
        score += 30.0
    elif last_score >= 50:
        score += 15.0

    return round(clamp(score, 0.0, 100.0), 2)


def apply_revision_to_knowledge(
    kb: Dict[str, Any],
    revision: Dict[str, Any],
) -> None:
    rows = list(revision.get("results") or [])

    if not rows:
        rows = [
            {**row, "working": True}
            for row in revision.get("working", [])
        ]

        rows += [
            {**row, "working": False}
            for row in (
                revision.get("changes") or {}
            ).get("failed_candidates", [])
        ]

    kb["revisions_applied"].append(
        revision.get("revision", "?")
    )

    for row in rows:
        alias = row.get("alias")
        route = row.get("route")

        if not alias or not route:
            continue

        family = normalize_alias(alias)
        working = bool(row.get("working"))
        url = row.get("url", "")

        kb["seen_aliases"].add(alias)

        kb["routes"].setdefault(
            route,
            {"ok": 0, "fail": 0},
        )
        kb["routes"][route][
            "ok" if working else "fail"
        ] += 1

        state = (
            kb["channels"]
            .setdefault(alias, {})
            .setdefault(
                route,
                {
                    "ok": 0,
                    "fail": 0,
                    "last_score": 0.0,
                    "best_score": 0.0,
                    "last_working": False,
                    "last_url": "",
                    "last_reason": "",
                },
            )
        )

        state["ok" if working else "fail"] += 1
        state["last_score"] = row.get(
            "rank_score",
            0,
        )
        state["best_score"] = max(
            float(state.get("best_score", 0)),
            float(row.get("rank_score", 0)),
        )
        state["last_working"] = working
        state["last_url"] = url
        state["last_reason"] = row.get(
            "reason",
            "",
        )

        kb["last_working_map"][
            (alias, route)
        ] = working

        family_state = kb["families"].setdefault(
            family,
            {
                "aliases": set(),
                "routes": {},
                "ok": 0,
                "fail": 0,
            },
        )

        family_state["aliases"].add(alias)
        family_state[
            "ok" if working else "fail"
        ] += 1

        family_state["routes"].setdefault(
            route,
            {"ok": 0, "fail": 0},
        )
        family_state["routes"][route][
            "ok" if working else "fail"
        ] += 1

        if url:
            key = f"{alias}|{route}|{url}"

            candidate_state = (
                kb["candidate_stats"]
                .setdefault(
                    key,
                    {
                        "alias": alias,
                        "family": family,
                        "route": route,
                        "url": url,
                        "ok": 0,
                        "fail": 0,
                        "attempts": 0,
                        "last_working": False,
                        "last_score": 0.0,
                        "best_score": 0.0,
                        "last_reason": "",
                    },
                )
            )

            candidate_state["attempts"] += 1
            candidate_state[
                "ok" if working else "fail"
            ] += 1
            candidate_state["last_working"] = working
            candidate_state["last_score"] = row.get(
                "rank_score",
                0,
            )
            candidate_state["best_score"] = max(
                float(
                    candidate_state.get(
                        "best_score",
                        0,
                    )
                ),
                float(
                    row.get(
                        "rank_score",
                        0,
                    )
                ),
            )
            candidate_state["last_reason"] = row.get(
                "reason",
                "",
            )

            if working:
                best = [
                    item
                    for item in kb[
                        "historical_best"
                    ].get(alias, [])
                    if item.get("url") != url
                ]

                best.append(
                    {
                        "alias": alias,
                        "family": family,
                        "route": route,
                        "url": url,
                        "rank_score": row.get(
                            "rank_score",
                            0,
                        ),
                        "revision": revision.get(
                            "revision"
                        ),
                    }
                )

                best.sort(
                    key=lambda item: (
                        float(
                            item.get(
                                "rank_score",
                                0,
                            )
                        ),
                        ROUTE_PRIORITY.get(
                            item.get("route"),
                            0,
                        ),
                    ),
                    reverse=True,
                )

                kb["historical_best"][
                    alias
                ] = best[:10]

                kb["recovery_queue"].pop(
                    f"{alias}|{route}",
                    None,
                )

            elif state.get(
                "best_score",
                0,
            ) > 0:
                kb["recovery_queue"][
                    f"{alias}|{route}"
                ] = {
                    "alias": alias,
                    "family": family,
                    "route": route,
                    "last_url": url,
                    "last_reason": row.get(
                        "reason"
                    ),
                    "last_score": row.get(
                        "rank_score",
                        0,
                    ),
                    "priority": recovery_priority(
                        observation_count(
                            kb,
                            alias,
                            route,
                        ),
                        row.get(
                            "reason",
                            "",
                        ),
                        float(
                            row.get(
                                "rank_score",
                                0,
                            )
                        ),
                    ),
                }

        feedback = row.get(
            "learning_feedback"
        )

        if feedback:
            pattern_key = (
                f"{family}|{route}|{feedback}"
            )

            pattern = kb["patterns"].setdefault(
                pattern_key,
                {
                    "family": family,
                    "route": route,
                    "feedback": feedback,
                    "count": 0,
                },
            )

            pattern["count"] += 1


def load_current_knowledge() -> Dict[str, Any]:
    kb = empty_knowledge()
    chain = list_data_revisions()

    if not chain:
        skala(
            "DATA HISTORY: empty (first run)"
        )
        return kb

    skala(
        f"DATA HISTORY chain: "
        f"{[path for _, path in chain]}"
    )

    for revision_number, path in chain:
        data = load_json_safe(path)

        if not data:
            continue

        apply_revision_to_knowledge(
            kb,
            data,
        )

        skala(
            f"  applied rev={revision_number} "
            f"file={path}"
        )

    skala(
        f"CURRENT KNOWLEDGE: "
        f"{len(kb['channels'])} aliases / "
        f"{len(kb['families'])} families / "
        f"{len(kb['candidate_stats'])} candidates"
    )

    return kb


# ============================================================
# EVOLUTION
# ============================================================

def snapshot_from_revision(
    revision: Dict[str, Any],
) -> Dict[Tuple[str, str], bool]:
    rows = list(
        revision.get("results") or []
    )

    if not rows:
        rows = [
            {**row, "working": True}
            for row in revision.get(
                "working",
                [],
            )
        ]

        rows += [
            {**row, "working": False}
            for row in (
                revision.get("changes") or {}
            ).get(
                "failed_candidates",
                [],
            )
        ]

    return {
        (
            row["alias"],
            row["route"],
        ): bool(row.get("working"))
        for row in rows
        if row.get("alias")
        and row.get("route")
    }


def analyze_revision_evolution(
    chain: List[Tuple[int, str]],
) -> Dict[str, Any]:
    evolution = {
        "schema_version": 2,
        "created_at": now_utc(),
        "scanner_version": SCANNER_VERSION,
        "chain": [
            path for _, path in chain
        ],
        "steps": [],
        "summary": {
            "appeared": 0,
            "disappeared": 0,
            "recovered": 0,
            "degraded": 0,
            "stable_ok": 0,
            "stable_fail": 0,
        },
        "channel_trends": {},
    }

    previous = None
    previous_path = None

    for revision_number, path in chain:
        data = load_json_safe(path)

        if not data:
            continue

        current = snapshot_from_revision(
            data
        )

        if previous is None:
            previous = current
            previous_path = path
            continue

        groups = {
            "appeared": [],
            "disappeared": [],
            "recovered": [],
            "degraded": [],
        }

        stable_ok = 0
        stable_fail = 0

        for key in (
            set(previous) | set(current)
        ):
            was = previous.get(key)
            now = current.get(key)
            alias, route = key

            if was is None and now is True:
                groups["appeared"].append(
                    {
                        "alias": alias,
                        "route": route,
                    }
                )
            elif was is True and now is None:
                groups["disappeared"].append(
                    {
                        "alias": alias,
                        "route": route,
                    }
                )
            elif was is False and now is True:
                groups["recovered"].append(
                    {
                        "alias": alias,
                        "route": route,
                    }
                )
            elif was is True and now is False:
                groups["degraded"].append(
                    {
                        "alias": alias,
                        "route": route,
                    }
                )
            elif was is True and now is True:
                stable_ok += 1
            elif was is False and now is False:
                stable_fail += 1

            evolution[
                "channel_trends"
            ].setdefault(
                alias,
                {},
            )

            if now is True and was is not True:
                evolution[
                    "channel_trends"
                ][alias][route] = "up"
            elif now is False and was is True:
                evolution[
                    "channel_trends"
                ][alias][route] = "down"
            elif now is True and was is True:
                evolution[
                    "channel_trends"
                ][alias][route] = "stable_ok"

        for name, values in groups.items():
            evolution["summary"][name] += len(
                values
            )

        evolution["summary"][
            "stable_ok"
        ] += stable_ok
        evolution["summary"][
            "stable_fail"
        ] += stable_fail

        evolution["steps"].append(
            {
                "from_rev": data.get(
                    "parent"
                ) or previous_path,
                "to_rev": path,
                "to_revision_num": revision_number,
                **groups,
                "stable_ok": stable_ok,
                "stable_fail": stable_fail,
            }
        )

        skala(
            f"EVOLUTION {previous_path} -> {path}: "
            f"+{len(groups['appeared'])} appeared, "
            f"-{len(groups['disappeared'])} gone, "
            f"up={len(groups['recovered'])} recovered, "
            f"down={len(groups['degraded'])} degraded"
        )

        previous = current
        previous_path = path

    if len(chain) < 2:
        skala(
            "EVOLUTION: need >=2 revisions for diff (skip)"
        )

    return evolution


# ============================================================
# LEARNING
# ============================================================

def channel_route_rate(
    kb: Dict[str, Any],
    alias: str,
    route: str,
) -> float:
    state = (
        kb.get("channels", {})
        .get(alias, {})
        .get(route)
    )

    if state:
        total = (
            state.get("ok", 0)
            + state.get("fail", 0)
        )

        if total:
            return (
                state.get("ok", 0)
                / total
            )

    family_state = (
        kb.get("families", {})
        .get(normalize_alias(alias), {})
        .get("routes", {})
        .get(route)
    )

    if family_state:
        total = (
            family_state.get("ok", 0)
            + family_state.get("fail", 0)
        )

        if total:
            return (
                family_state.get("ok", 0)
                / total
            )

    route_state = kb.get(
        "routes",
        {},
    ).get(
        route,
        {
            "ok": 0,
            "fail": 0,
        },
    )

    total = (
        route_state.get("ok", 0)
        + route_state.get("fail", 0)
    )

    return (
        route_state.get("ok", 0) / total
        if total
        else 0.5
    )


def get_weights(
    kb: Dict[str, Any],
) -> Dict[str, float]:
    weights = dict(
        DEFAULT_WEIGHTS
    )

    for name, value in kb.get(
        "learned_feature_weights",
        {},
    ).items():
        if name in weights:
            weights[name] = clamp(
                float(value),
                WEIGHT_MIN,
                WEIGHT_MAX,
            )

    return weights


def adaptive_exploration_ratio(
    kb: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> float:
    if not candidates:
        return MIN_EXPLORATION_RATIO

    unknown = sum(
        observation_count(
            kb,
            row["alias"],
            row["route"],
        ) == 0
        for row in candidates
    )

    recovery = sum(
        f"{row['alias']}|{row['route']}"
        in kb.get(
            "recovery_queue",
            {},
        )
        for row in candidates
    )

    ratio = (
        MIN_EXPLORATION_RATIO
        + (
            unknown
            / len(candidates)
        )
        * 0.22
        + (
            recovery
            / len(candidates)
        )
        * 0.10
    )

    return round(
        clamp(
            ratio,
            MIN_EXPLORATION_RATIO,
            MAX_EXPLORATION_RATIO,
        ),
        3,
    )


def update_learning_from_results(
    kb: Dict[str, Any],
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Transparent online learning.

    Historical evidence is never rewritten.
    Learned route/family priors and feature weights are
    maintained separately and affect the next run.
    """

    update = {
        "learning_rate": LEARNING_RATE,
        "samples": 0,
        "correct": 0,
        "incorrect": 0,
        "mean_absolute_error": 0.0,
        "weight_updates": {},
        "route_updates": {},
        "family_updates": {},
    }

    errors = []

    for row in results:
        alias = row.get("alias", "")
        route = row.get("route", "")
        family = normalize_alias(alias)

        actual = (
            1.0
            if row.get("working")
            else 0.0
        )

        prediction = clamp(
            float(
                row.get(
                    "predicted_score",
                    0,
                )
            ) / 100.0,
            0.0,
            1.0,
        )

        error = actual - prediction
        errors.append(abs(error))

        if abs(error) <= 0.20:
            update["correct"] += 1
        else:
            update["incorrect"] += 1

        update["samples"] += 1

        old_route = kb.setdefault(
            "learned_route_rates",
            {},
        ).get(
            route,
            0.5,
        )

        new_route = clamp(
            old_route
            + LEARNING_RATE * error,
            0.02,
            0.98,
        )

        kb[
            "learned_route_rates"
        ][route] = new_route

        update[
            "route_updates"
        ][route] = {
            "old_rate": round(
                old_route,
                5,
            ),
            "new_rate": round(
                new_route,
                5,
            ),
        }

        family_key = (
            f"{family}|{route}"
        )

        old_family = kb.setdefault(
            "learned_family_route_rates",
            {},
        ).get(
            family_key,
            0.5,
        )

        new_family = clamp(
            old_family
            + LEARNING_RATE * error,
            0.02,
            0.98,
        )

        kb[
            "learned_family_route_rates"
        ][family_key] = new_family

        update[
            "family_updates"
        ][family_key] = {
            "old_rate": round(
                old_family,
                5,
            ),
            "new_rate": round(
                new_family,
                5,
            ),
        }

        for feature, value in (
            row.get(
                "rank_factors",
                {},
            ).items()
        ):
            if not isinstance(
                value,
                (int, float),
            ):
                continue

            delta = (
                LEARNING_RATE
                * (
                    1.0
                    if actual
                    else -1.0
                )
                * min(
                    abs(float(value)),
                    10.0,
                )
                / 10.0
            )

            current = kb.setdefault(
                "learned_feature_weights",
                {},
            ).get(
                feature,
                DEFAULT_WEIGHTS.get(
                    feature,
                    5.0,
                ),
            )

            new_weight = clamp(
                float(current) + delta,
                WEIGHT_MIN,
                WEIGHT_MAX,
            )

            kb[
                "learned_feature_weights"
            ][feature] = new_weight

            update[
                "weight_updates"
            ][feature] = (
                update[
                    "weight_updates"
                ].get(
                    feature,
                    0.0,
                )
                + delta
            )

    update[
        "mean_absolute_error"
    ] = (
        sum(errors) / len(errors)
        if errors
        else 0.0
    )

    return update


def knowledge_to_learning(
    kb: Dict[str, Any],
    evolution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    families = {}

    for family, data in kb.get(
        "families",
        {},
    ).items():
        families[family] = {
            **data,
            "aliases": sorted(
                data.get(
                    "aliases",
                    set(),
                )
            ),
        }

    return {
        "version": 2,
        "updated": now_utc(),
        "source": (
            "immutable_history_plus_online_feedback"
        ),
        "channels": kb["channels"],
        "families": families,
        "routes": kb["routes"],
        "patterns": kb["patterns"],
        "candidate_statistics": kb[
            "candidate_stats"
        ],
        "historical_best": kb[
            "historical_best"
        ],
        "recovery_queue": kb[
            "recovery_queue"
        ],
        "learned_route_rates": kb[
            "learned_route_rates"
        ],
        "learned_family_route_rates": kb[
            "learned_family_route_rates"
        ],
        "learned_feature_weights": kb[
            "learned_feature_weights"
        ],
        "weights": get_weights(kb),
        "revisions_applied": kb[
            "revisions_applied"
        ],
        "evolution_summary": (
            evolution or {}
        ).get("summary"),
    }


# ============================================================
# GOLDEN
# ============================================================

def load_golden() -> Dict[str, Any]:
    golden = {
        "source": GOLDEN_URL,
        "entries": [],
        "by_alias": {},
        "routes": {},
        "ua_set": [],
        "alias_set": [],
    }

    try:
        response = requests.get(
            GOLDEN_URL,
            timeout=DOWNLOAD_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )
        response.raise_for_status()
        text = response.text
    except Exception as exc:
        skala(
            f"GOLDEN download failed: {exc}",
            "WARN",
        )
        return golden

    current_ua = ""
    current_title = ""
    routes = defaultdict(int)
    user_agents = set()
    aliases = set()

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF:"):
            match = re.search(
                r'tvg-user-agent="([^"]+)"',
                line,
                re.I,
            )

            if match:
                current_ua = match.group(1)

            if "," in line:
                current_title = (
                    line.split(",", 1)[1]
                    .strip()
                )

        elif line.startswith(
            "#EXTVLCOPT:http-user-agent="
        ):
            current_ua = (
                line.split("=", 1)[1]
                .strip()
                .strip('"')
            )

        elif line.startswith(
            ("http://", "https://")
        ):
            found = re.findall(
                r"CH_[A-Za-z0-9_-]+",
                line,
            )

            alias = (
                found[0]
                if found
                else (
                    f"CH_{current_title}"
                    if current_title
                    else "UNKNOWN"
                )
            )

            route = route_from_url(
                line
            )

            entry = {
                "alias": alias,
                "family": normalize_alias(
                    alias
                ),
                "route": route,
                "url": line,
                "ua": current_ua
                or PRIMARY_UA,
                "title": current_title,
            }

            golden[
                "entries"
            ].append(entry)

            golden[
                "by_alias"
            ].setdefault(
                alias,
                [],
            ).append(entry)

            routes[route] += 1
            aliases.add(alias)

            if current_ua:
                user_agents.add(
                    current_ua
                )

            current_title = ""

    golden["routes"] = dict(routes)
    golden["ua_set"] = sorted(
        user_agents
    )
    golden["alias_set"] = sorted(
        aliases
    )

    skala(
        f"GOLDEN LOADED: "
        f"{len(golden['entries'])} entries, "
        f"{len(golden['alias_set'])} unique CH_*"
    )

    return golden


def reference_similarity(
    alias: str,
    route: str,
    golden: Dict[str, Any],
) -> float:
    rows = golden.get(
        "by_alias",
        {},
    ).get(
        alias,
        [],
    )

    if not rows:
        family = normalize_alias(alias)

        for known_alias, known_rows in (
            golden.get(
                "by_alias",
                {},
            ).items()
        ):
            if normalize_alias(
                known_alias
            ) == family:
                rows = known_rows
                break

    if not rows:
        return 0.0

    score = 0.35

    if any(
        row["route"] == route
        for row in rows
    ):
        score += 0.45
    elif any(
        row["route"] in KNOWN_ROUTES
        for row in rows
    ):
        score += 0.15

    if any(
        "variant.m3u8" in row["url"]
        for row in rows
    ):
        score += 0.20

    return min(score, 1.0)


def is_golden_match(
    alias: str,
    route: str,
    golden: Dict[str, Any],
) -> bool:
    rows = golden.get(
        "by_alias",
        {},
    ).get(
        alias,
        [],
    )

    if any(
        row["route"] == route
        for row in rows
    ):
        return True

    family = normalize_alias(alias)

    return any(
        normalize_alias(
            known_alias
        ) == family
        and any(
            row["route"] == route
            for row in known_rows
        )
        for known_alias, known_rows in (
            golden.get(
                "by_alias",
                {},
            ).items()
        )
    )


# ============================================================
# SOURCE OBSERVATIONS
# ============================================================

def download_playlist() -> str:
    skala(
        "========================================"
    )
    skala(
        f"START DMITRYTV -> NGENIX v{SCANNER_VERSION}"
    )
    skala(
        f"SOURCE : {PLAYLIST_URL}"
    )

    response = requests.get(
        PLAYLIST_URL,
        timeout=DOWNLOAD_TIMEOUT,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    )

    response.raise_for_status()

    skala(
        f"DOWNLOAD OK : HTTP {response.status_code}"
    )
    skala(
        f"PLAYLIST SIZE : "
        f"{len(response.content)} bytes"
    )

    return response.text


def extract_urls(
    playlist: str,
) -> List[str]:
    return [
        line.strip()
        for line in playlist.splitlines()
        if line.strip()
        and not line.strip().startswith("#")
        and line.strip().startswith(
            ("http://", "https://")
        )
    ]


def parse_source_url(
    url: str,
) -> Optional[Dict[str, Any]]:
    aliases = re.findall(
        r"CH_[A-Za-z0-9_-]+",
        url,
    )

    if not aliases:
        return None

    path = urlparse(url).path or ""

    return {
        "alias": aliases[0],
        "all_aliases": aliases,
        "family": normalize_alias(
            aliases[0]
        ),
        "route": route_from_url(
            url
        ),
        "tail": (
            path
            if path.startswith("/")
            else "/" + path
        ),
        "source_url": url,
    }


def extract_observations(
    playlist: str,
):
    urls = extract_urls(
        playlist
    )

    tails: List[str] = []
    observations: List[
        Dict[str, Any]
    ] = []

    for line_number, url in enumerate(
        urls,
        1,
    ):
        match = re.search(
            r"(/(?:hls|region|regions)/.*)",
            url,
            re.I,
        )

        if match:
            tail = (
                match.group(1)
                .split("?", 1)[0]
                .split("#", 1)[0]
            )
        else:
            tail = (
                urlparse(url).path
                or ""
            )

        if tail:
            tails.append(tail)

        parsed = parse_source_url(
            url
        )

        if not parsed:
            continue

        for alias in parsed[
            "all_aliases"
        ]:
            observation = {
                "source_index": (
                    len(observations) + 1
                ),
                "source_line": line_number,
                "source_url": url,
                "alias": alias,
                "family": normalize_alias(
                    alias
                ),
                "source_route": parsed[
                    "route"
                ],
                "source_tail": parsed[
                    "tail"
                ],
            }

            observations.append(
                observation
            )

            skala(
                f"[OBS "
                f"{observation['source_index']:05d}] "
                f"{alias} "
                f"family={observation['family']} "
                f"route={parsed['route']}",
                "FOUND",
            )

    return (
        urls,
        tails,
        observations,
    )


# ============================================================
# DISCOVERY
# ============================================================

def historical_candidates_for_alias(
    kb: Dict[str, Any],
    alias: str,
) -> List[Dict[str, Any]]:
    result = []
    family = normalize_alias(alias)

    for old_alias, rows in kb.get(
        "historical_best",
        {},
    ).items():
        if (
            old_alias != alias
            and normalize_alias(
                old_alias
            ) != family
        ):
            continue

        for row in rows:
            url = row.get("url")

            if not url:
                continue

            old_token = row.get(
                "alias",
                old_alias,
            )

            if old_token != alias:
                url = re.sub(
                    rf"/{re.escape(old_token)}/",
                    f"/{alias}/",
                    url,
                )

            result.append(
                {
                    "alias": alias,
                    "route": row.get(
                        "route",
                        route_from_url(url),
                    ),
                    "url": url,
                    "origin": (
                        "historical_best"
                        if old_alias == alias
                        else "historical_family"
                    ),
                    "historical_score": row.get(
                        "rank_score",
                        0,
                    ),
                }
            )

    return result


def generate_candidates(
    observations: List[Dict[str, Any]],
    kb: Dict[str, Any],
) -> List[Dict[str, Any]]:
    index = {}
    candidates = []

    def add_candidate(
        alias: str,
        route: str,
        url: str,
        origin: str,
        observation: Optional[
            Dict[str, Any]
        ] = None,
        historical_score: float = 0.0,
    ) -> None:
        if not alias or not url:
            return

        key = (
            alias,
            route,
            url,
        )

        if key not in index:
            index[key] = {
                "alias": alias,
                "family": normalize_alias(
                    alias
                ),
                "route": route,
                "url": url,
                "origins": [origin],
                "historical_score": (
                    historical_score
                ),
                "sources": [],
            }

            candidates.append(
                index[key]
            )

        elif origin not in index[
            key
        ]["origins"]:
            index[key]["origins"].append(
                origin
            )

        if observation:
            source = {
                field: observation.get(
                    field
                )
                for field in (
                    "source_index",
                    "source_line",
                    "source_url",
                    "source_route",
                    "source_tail",
                )
            }

            if source not in index[
                key
            ]["sources"]:
                index[key][
                    "sources"
                ].append(source)

    # Direct source observation + route expansion.
    for observation in observations:
        alias = observation["alias"]
        observed_route = observation.get(
            "source_route",
            "other",
        )
        observed_url = observation.get("source_url", "")
        observed_path = urlparse(observed_url).path if observed_url else ""
        dynamic_match = re.search(
            r"/(hls)/(region[^/]*)/CH_[^/]+(?:/|$)",
            observed_path,
            re.I,
        )
        dynamic_route = None
        if dynamic_match:
            dynamic_route = f"hls_{dynamic_match.group(2).lower()}"

        routes = []

        if route_supported(observed_route):
            routes.append(observed_route)

        if dynamic_route and dynamic_route not in routes:
            routes.append(dynamic_route)

        routes.extend(
            route
            for route in KNOWN_ROUTES
            if route not in routes
        )

        for route in routes:
            add_candidate(
                alias,
                route,
                build_candidate_url(
                    route,
                    alias,
                ),
                (
                    "observed_route"
                    if route
                    == observed_route
                    else "route_expansion"
                ),
                observation,
            )

        # Historical exact/family knowledge.
        for historical in (
            historical_candidates_for_alias(
                kb,
                alias,
            )
        ):
            add_candidate(
                alias,
                historical["route"],
                historical["url"],
                historical["origin"],
                observation,
                historical.get(
                    "historical_score",
                    0,
                ),
            )

    # Recovery candidates.
    for recovery in kb.get(
        "recovery_queue",
        {},
    ).values():
        alias = recovery.get(
            "alias"
        )
        route = recovery.get(
            "route"
        )

        if (
            alias
            and route_supported(route)
        ):
            add_candidate(
                alias,
                route,
                recovery.get(
                    "last_url"
                )
                or build_candidate_url(
                    route,
                    alias,
                ),
                "recovery",
            )

    candidates = list(
        {
            (
                row["alias"],
                row["route"],
                row["url"],
            ): row
            for row in candidates
        }.values()
    )

    candidates.sort(
        key=lambda row: (
            row["alias"],
            -float(
                row.get(
                    "historical_score",
                    0,
                )
            ),
            -ROUTE_PRIORITY.get(
                row["route"],
                0,
            ),
            row["url"],
        )
    )

    if len(candidates) > MAX_CANDIDATES:
        skala(
            f"CANDIDATE LIMIT: "
            f"{len(candidates)} -> "
            f"{MAX_CANDIDATES}",
            "WARN",
        )

        candidates = candidates[
            :MAX_CANDIDATES
        ]

    return candidates


# ============================================================
# RANKING
# ============================================================

def rank_class(
    score: float,
) -> str:
    if score >= 90:
        return "VERY_HIGH"
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    if score >= 25:
        return "LOW"
    return "VERY_LOW"


def predict_score(
    candidate: Dict[str, Any],
    kb: Dict[str, Any],
    golden: Dict[str, Any],
    weights: Dict[str, float],
    evolution: Dict[str, Any],
) -> Dict[str, Any]:
    alias = candidate["alias"]
    route = candidate["route"]
    family = normalize_alias(alias)

    factors: Dict[str, float] = {}

    historical_rate = channel_route_rate(
        kb,
        alias,
        route,
    )

    learned_route = kb.get(
        "learned_route_rates",
        {},
    ).get(route)

    if learned_route is not None:
        route_rate = (
            0.65 * historical_rate
            + 0.35 * learned_route
        )
    else:
        route_rate = historical_rate

    factors[
        "route_probability"
    ] = (
        route_rate
        * weights["route_probability"]
    )

    family_key = (
        f"{family}|{route}"
    )

    family_rate = kb.get(
        "learned_family_route_rates",
        {},
    ).get(family_key)

    if family_rate is not None:
        factors[
            "route_probability"
        ] += (
            family_rate
            * weights["route_probability"]
            * 0.35
        )

    attempts = observation_count(
        kb,
        alias,
        route,
    )

    factors["history_bonus"] = min(
        weights["history_bonus"],
        attempts * 0.6,
    )

    factors[
        "reference_similarity"
    ] = (
        reference_similarity(
            alias,
            route,
            golden,
        )
        * weights[
            "reference_similarity"
        ]
    )

    if attempts == 0:
        factors["unknown_prior"] = 8.0

    historical_score = float(
        candidate.get(
            "historical_score",
            0,
        )
    )

    if historical_score:
        factors[
            "history_bonus"
        ] += min(
            weights["history_bonus"]
            * 0.75,
            historical_score * 0.05,
        )

    if (
        f"{alias}|{route}"
        in kb.get(
            "recovery_queue",
            {},
        )
    ):
        factors[
            "recovery_bonus"
        ] = weights[
            "recovery_bonus"
        ]

    trend = (
        evolution.get(
            "channel_trends",
            {},
        )
        .get(alias, {})
        .get(route)
    )

    if trend in {
        "up",
        "stable_ok",
    }:
        factors[
            "evolution_trend"
        ] = 5.0

    elif trend == "down":
        factors[
            "evolution_trend"
        ] = -5.0

    score = clamp(
        sum(factors.values()),
        0.0,
        95.0,
    )

    confidence = (
        min(
            0.97,
            0.25
            + attempts * 0.02,
        )
        if attempts
        else 0.2
    )

    return {
        "rank_score": round(
            score,
            2,
        ),
        "rank_class": rank_class(
            score
        ),
        "confidence": round(
            confidence,
            3,
        ),
        "attempts": attempts,
        "rank_factors": {
            key: round(
                value,
                2,
            )
            for key, value in factors.items()
        },
        "golden_match": is_golden_match(
            alias,
            route,
            golden,
        ),
        "predicted": True,
    }


def finalize_score(
    result: Dict[str, Any],
    prediction: Dict[str, Any],
    weights: Dict[str, float],
) -> Dict[str, Any]:
    factors = dict(
        prediction.get(
            "rank_factors",
            {},
        )
    )

    validation = result.get(
        "validation",
        {},
    )

    child = result.get(
        "child_validation",
        {},
    )

    if result.get(
        "http_status"
    ) == 200:
        factors[
            "http_ok"
        ] = weights[
            "http_ok"
        ]

    if validation.get(
        "is_m3u8"
    ):
        factors[
            "m3u8_valid"
        ] = weights[
            "m3u8_valid"
        ]

    if (
        validation.get(
            "has_stream_inf"
        )
        or validation.get(
            "has_media"
        )
    ):
        factors[
            "media_playlist"
        ] = weights[
            "media_playlist"
        ]

    if validation.get(
        "variant_count",
        0,
    ):
        factors[
            "stream_variants"
        ] = min(
            weights[
                "stream_variants"
            ],
            validation[
                "variant_count"
            ] * 3,
        )

    if validation.get(
        "has_media_urls"
    ):
        factors[
            "segments_hint"
        ] = weights[
            "segments_hint"
        ]

    if child.get(
        "valid_child"
    ):
        factors[
            "child_valid"
        ] = weights[
            "child_valid"
        ]

    if child.get(
        "playable_hint"
    ):
        factors[
            "playability"
        ] = weights[
            "playability"
        ]

    score = clamp(
        sum(factors.values()),
        0.0,
        100.0,
    )

    return {
        "rank_score": round(
            score,
            2,
        ),
        "rank_class": rank_class(
            score
        ),
        "confidence": prediction.get(
            "confidence",
            0.5,
        ),
        "rank_factors": {
            key: round(
                value,
                2,
            )
            for key, value in factors.items()
        },
        "golden_match": prediction.get(
            "golden_match",
            False,
        ),
        "attempts": prediction.get(
            "attempts",
            0,
        ),
    }


def sort_candidates_with_exploration(
    candidates: List[Dict[str, Any]],
    predictions: Dict[
        Tuple[str, str, str],
        Dict[str, Any],
    ],
    kb: Dict[str, Any],
) -> List[Dict[str, Any]]:
    scored = []

    for candidate in candidates:
        key = (
            candidate["alias"],
            candidate["route"],
            candidate["url"],
        )

        scored.append(
            (
                candidate,
                predictions.get(
                    key,
                    {},
                ).get(
                    "rank_score",
                    0,
                ),
            )
        )

    scored.sort(
        key=lambda item: item[1],
        reverse=True,
    )

    if not scored:
        return []

    exploration_count = max(
        1,
        int(
            len(scored)
            * adaptive_exploration_ratio(
                kb,
                candidates,
            )
        ),
    )

    exploit = scored[
        :-exploration_count
    ]

    explore = scored[
        -exploration_count:
    ]

    random.shuffle(
        explore
    )

    explore.sort(
        key=lambda item: (
            "recovery"
            in item[0].get(
                "origins",
                [],
            ),
            observation_count(
                kb,
                item[0]["alias"],
                item[0]["route"],
            )
            == 0,
        ),
        reverse=True,
    )

    ordered = []
    exploit_index = 0
    explore_index = 0

    while (
        exploit_index < len(exploit)
        or explore_index < len(explore)
    ):
        for _ in range(4):
            if exploit_index < len(
                exploit
            ):
                ordered.append(
                    exploit[
                        exploit_index
                    ][0]
                )
                exploit_index += 1

        if explore_index < len(
            explore
        ):
            ordered.append(
                explore[
                    explore_index
                ][0]
            )
            explore_index += 1

    skala(
        f"ADAPTIVE EXPLORATION: "
        f"explore={len(explore)} "
        f"exploit={len(exploit)}"
    )

    return ordered


# ============================================================
# VALIDATION
# ============================================================

def validate_m3u8_body(
    text: str,
) -> Dict[str, Any]:
    info = {
        "is_m3u8": False,
        "has_stream_inf": False,
        "has_media": False,
        "has_media_urls": False,
        "variant_count": 0,
        "media_url_count": 0,
        "child_urls": [],
    }

    if not text:
        return info

    if not text.lstrip().startswith(
        "#EXTM3U"
    ):
        return info

    info["is_m3u8"] = True

    info["has_stream_inf"] = (
        "#EXT-X-STREAM-INF:"
        in text
    )

    info["has_media"] = (
        "#EXT-X-MEDIA:"
        in text
    )

    info["variant_count"] = text.count(
        "#EXT-X-STREAM-INF:"
    )

    children = []

    for line in text.splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        if (
            re.search(
                r"\.m3u8|\.ts(?:\?|$)",
                line,
                re.I,
            )
            or line.startswith("http")
        ):
            children.append(line)

    info["child_urls"] = children[:20]
    info["media_url_count"] = len(
        children
    )
    info["has_media_urls"] = bool(
        children
    )

    return info


def classify_from_validation(
    status: Optional[int],
    validation: Dict[str, Any],
) -> str:
    if status is None:
        return "UNKNOWN"

    if status == 404:
        return "HTTP_404"

    if status == 403:
        return "HTTP_403"

    if status == 401:
        return "HTTP_401"

    if status >= 500:
        return "HTTP_5XX"

    if status != 200:
        return f"HTTP_{status}"

    if not validation.get(
        "is_m3u8"
    ):
        return "NOT_M3U8"

    if not (
        validation.get(
            "has_stream_inf"
        )
        or validation.get(
            "has_media"
        )
        or validation.get(
            "has_media_urls"
        )
    ):
        return "NO_STREAM"

    return "OK"


async def validate_child_playlist(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    parent_url: str,
    child_urls: List[str],
) -> Dict[str, Any]:
    result = {
        "checked": 0,
        "valid_child": False,
        "playable_hint": False,
        "results": [],
    }

    async with semaphore:
        for raw_url in child_urls[
            :MAX_CHILD_URLS_TO_CHECK
        ]:
            child_url = urljoin(
                parent_url,
                raw_url,
            )

            try:
                perf_note_request_start()
                async with session.get(
                    child_url,
                    timeout=PERF_CHILD_TIMEOUT,
                    headers={
                        "User-Agent": PRIMARY_UA,
                        "Accept": "*/*",
                    },
                ) as response:
                    text = await response.text(
                        errors="replace"
                    )

                    validation = (
                        validate_m3u8_body(
                            text
                        )
                    )

                    perf_note_child()
                    perf_note_request_finish(response.status == 200, len(text))
                    result[
                        "checked"
                    ] += 1

                    result[
                        "results"
                    ].append(
                        {
                            "url": child_url,
                            "status": response.status,
                            "is_m3u8": validation[
                                "is_m3u8"
                            ],
                            "has_media_urls": validation[
                                "has_media_urls"
                            ],
                            "variant_count": validation[
                                "variant_count"
                            ],
                        }
                    )

                    if (
                        response.status == 200
                        and validation[
                            "is_m3u8"
                        ]
                    ):
                        result[
                            "valid_child"
                        ] = True

                    if (
                        response.status == 200
                        and (
                            validation[
                                "has_media_urls"
                            ]
                            or validation[
                                "has_media"
                            ]
                        )
                    ):
                        result[
                            "playable_hint"
                        ] = True

            except asyncio.TimeoutError:
                perf_note_timeout()
                result[
                    "results"
                ].append(
                    {
                        "url": child_url,
                        "status": None,
                        "error": "TimeoutError",
                    }
                )
            except Exception as exc:
                perf_note_request_finish(False, 0)
                result[
                    "results"
                ].append(
                    {
                        "url": child_url,
                        "status": None,
                        "error": type(exc).__name__,
                    }
                )

    return result


async def check_candidate(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    candidate: Dict[str, Any],
    prediction: Dict[str, Any],
    index: int,
    total: int,
) -> Dict[str, Any]:
    alias = candidate["alias"]
    route = candidate["route"]
    url = candidate["url"]

    result = {
        "index": index,
        "alias": alias,
        "family": candidate[
            "family"
        ],
        "route": route,
        "url": url,
        "origins": candidate.get(
            "origins",
            [],
        ),
        "sources": candidate.get(
            "sources",
            [],
        ),
        "http_status": None,
        "content_type": "",
        "content_length": 0,
        "working": False,
        "reachable": False,
        "reason": "UNKNOWN",
        "user_agent": PRIMARY_UA,
        "title": (
            alias[3:]
            if alias.startswith(
                "CH_"
            )
            else alias
        ),
        "raw_m3u8": "",
        "error": None,
        "validation": {},
        "child_validation": {},
        "rank_score": prediction.get(
            "rank_score",
            0,
        ),
        "rank_class": prediction.get(
            "rank_class",
            "VERY_LOW",
        ),
        "confidence": prediction.get(
            "confidence",
            0,
        ),
        "rank_factors": prediction.get(
            "rank_factors",
            {},
        ),
        "predicted_score": prediction.get(
            "rank_score",
            0,
        ),
        "golden_match": prediction.get(
            "golden_match",
            False,
        ),
        "attempts": prediction.get(
            "attempts",
            0,
        ),
        "learning_feedback": None,
    }

    async with semaphore:
        perf_note_request_start()
        try:
            async with session.get(
                url,
                timeout=PERF_HTTP_TIMEOUT,
                headers={
                    "User-Agent": PRIMARY_UA,
                    "Accept": "*/*",
                },
            ) as response:
                result[
                    "http_status"
                ] = response.status

                result[
                    "content_type"
                ] = response.headers.get(
                    "Content-Type",
                    "",
                )

                text = await response.text(
                    errors="replace"
                )

                result[
                    "content_length"
                ] = len(text)
                perf_note_request_finish(response.status == 200, len(text))

                result[
                    "reachable"
                ] = response.status == 200

                result[
                    "validation"
                ] = validate_m3u8_body(
                    text
                )

                result[
                    "reason"
                ] = classify_from_validation(
                    response.status,
                    result[
                        "validation"
                    ],
                )

                if result[
                    "reason"
                ] == "OK":
                    if (
                        VALIDATE_CHILD_PLAYLISTS
                        and result[
                            "validation"
                        ].get(
                            "child_urls"
                        )
                    ):
                        result[
                            "child_validation"
                        ] = (
                            await validate_child_playlist(
                                session,
                                semaphore,
                                url,
                                result[
                                    "validation"
                                ][
                                    "child_urls"
                                ],
                            )
                        )

                    result[
                        "working"
                    ] = True

                    result[
                        "raw_m3u8"
                    ] = text[:4000]

                    golden_text = (
                        " GOLDEN"
                        if result[
                            "golden_match"
                        ]
                        else ""
                    )

                    skala(
                        f"[{index:04d}/"
                        f"{total:04d}] "
                        f"{alias} /{route}/ "
                        f"-> OK "
                        f"var="
                        f"{result['validation'].get('variant_count', 0)}"
                        f"{golden_text}",
                        "FOUND",
                    )

                else:
                    result[
                        "error"
                    ] = result[
                        "reason"
                    ]

                    skala(
                        f"[{index:04d}/"
                        f"{total:04d}] "
                        f"{alias} /{route}/ "
                        f"-> "
                        f"{result['reason']}",
                        "WARN",
                    )

        except asyncio.TimeoutError:
            perf_note_timeout()
            result["reason"] = "TIMEOUT"
            result["error"] = "TIMEOUT"

            skala(
                f"[{index:04d}/"
                f"{total:04d}] "
                f"{alias} /{route}/ "
                f"-> TIMEOUT",
                "WARN",
            )

        except aiohttp.ClientError as exc:
            perf_note_request_finish(False, 0)
            result[
                "reason"
            ] = "REQUEST_ERROR"

            result[
                "error"
            ] = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            skala(
                f"[{index:04d}/"
                f"{total:04d}] "
                f"{alias} /{route}/ "
                f"-> REQUEST_ERROR",
                "ERROR",
            )

        except Exception as exc:
            perf_note_request_finish(False, 0)
            result[
                "reason"
            ] = "ERROR"

            result[
                "error"
            ] = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            skala(
                f"[{index:04d}/"
                f"{total:04d}] "
                f"{alias} /{route}/ "
                f"-> ERROR",
                "ERROR",
            )

    return result


async def scan_all(
    ordered: List[Dict[str, Any]],
    predictions: Dict[
        Tuple[str, str, str],
        Dict[str, Any],
    ],
) -> List[Dict[str, Any]]:
    semaphore = asyncio.Semaphore(
        CONCURRENCY_LIMIT
    )

    connector = PERF_SESSION_TUNING.connector()

    async with aiohttp.ClientSession(
        connector=connector
    ) as session:
        tasks = []

        total = len(ordered)

        for index, candidate in enumerate(
            ordered,
            1,
        ):
            key = (
                candidate["alias"],
                candidate["route"],
                candidate["url"],
            )

            tasks.append(
                check_candidate(
                    session,
                    semaphore,
                    candidate,
                    predictions.get(
                        key,
                        {},
                    ),
                    index,
                    total,
                )
            )

        return list(
            await asyncio.gather(
                *tasks
            )
        )


# ============================================================
# FEEDBACK / METRICS / OUTPUT
# ============================================================

def attach_feedback(
    results: List[Dict[str, Any]],
) -> None:
    for row in results:
        predicted = float(
            row.get(
                "predicted_score",
                0,
            )
        )

        actual = bool(
            row.get(
                "working"
            )
        )

        if actual and predicted >= 50:
            feedback = "POSITIVE"
        elif not actual and predicted < 50:
            feedback = "POSITIVE"
        elif actual:
            feedback = "UNDERESTIMATE"
        else:
            feedback = "NEGATIVE"

        row[
            "learning_feedback"
        ] = feedback

        row[
            "prediction_error"
        ] = round(
            (
                1.0
                if actual
                else 0.0
            )
            - predicted / 100.0,
            5,
        )


def select_best_working(
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    groups = defaultdict(list)

    for row in results:
        if row.get("working"):
            groups[
                row["alias"]
            ].append(row)

    selected = []

    for alias, rows in groups.items():
        best = max(
            rows,
            key=lambda row: (
                row.get(
                    "rank_score",
                    0,
                ),
                row.get(
                    "child_validation",
                    {},
                ).get(
                    "playable_hint",
                    False,
                ),
                row.get(
                    "child_validation",
                    {},
                ).get(
                    "valid_child",
                    False,
                ),
                row.get(
                    "validation",
                    {},
                ).get(
                    "variant_count",
                    0,
                ),
                row.get(
                    "golden_match",
                    False,
                ),
                ROUTE_PRIORITY.get(
                    row.get("route"),
                    0,
                ),
            ),
        )

        best = dict(best)
        best[
            "selected_for_output"
        ] = True

        best[
            "alternatives_working"
        ] = len(rows) - 1

        selected.append(
            best
        )

    return sorted(
        selected,
        key=lambda row: row["alias"],
    )


def compute_metrics(
    observations: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    selected: List[Dict[str, Any]],
    golden: Dict[str, Any],
) -> Dict[str, Any]:
    observed = {
        row["alias"]
        for row in observations
    }

    working = [
        row
        for row in results
        if row.get("working")
    ]

    working_aliases = {
        row["alias"]
        for row in working
    }

    golden_aliases = set(
        golden.get(
            "alias_set",
            [],
        )
    )

    valid_children = sum(
        bool(
            row.get(
                "child_validation",
                {},
            ).get(
                "valid_child"
            )
        )
        for row in working
    )

    playable_hints = sum(
        bool(
            row.get(
                "child_validation",
                {},
            ).get(
                "playable_hint"
            )
        )
        for row in working
    )

    return {
        "WORKING_COVERAGE": len(
            working_aliases
        ),
        "WORKING_RATE": (
            len(working)
            / len(results)
            if results
            else 0.0
        ),
        "CHANNEL_COVERAGE": (
            len(working_aliases)
            / len(observed)
            if observed
            else 0.0
        ),
        "GOLDEN_COVERAGE": (
            len(
                working_aliases
                & golden_aliases
            )
            / len(golden_aliases)
            if golden_aliases
            else 0.0
        ),
        "UNIQUE_OBSERVED_CHANNELS": len(
            observed
        ),
        "WORKING_CHANNELS": len(
            working_aliases
        ),
        "FAILED_CHANNELS": len(
            observed
            - working_aliases
        ),
        "WORKING_CANDIDATES": len(
            working
        ),
        "CHECKED_CANDIDATES": len(
            results
        ),
        "GOLDEN_MATCHES": sum(
            bool(
                row.get(
                    "golden_match"
                )
            )
            for row in working
        ),
        "NON_GOLDEN_WORKING_CHANNELS": len(
            working_aliases
            - golden_aliases
        ),
        "UNRESOLVED_CHANNELS": len(
            observed
            - working_aliases
        ),
        "OUTPUT_M3U_ENTRIES": len(
            selected
        ),
        "VALID_CHILD_PLAYLISTS": valid_children,
        "PLAYABLE_HINTS": playable_hints,
    }


def write_tails(
    urls: List[str],
    tails: List[str],
) -> None:
    with open(
        OUTPUT_TAILS_TXT,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(
            "\n".join(tails)
            + "\n"
        )

    write_json(
        OUTPUT_TAILS_JSON,
        {
            "scanner": "dmitrytv_to_ngenix",
            "version": SCANNER_VERSION,
            "timestamp": now_utc(),
            "source": PLAYLIST_URL,
            "statistics": {
                "urls_found": len(urls),
                "tails_found": len(tails),
            },
            "tails": tails,
        },
    )


def write_candidates(
    candidates: List[Dict[str, Any]],
    predictions: Dict[
        Tuple[str, str, str],
        Dict[str, Any],
    ],
) -> None:
    rows = []

    for candidate in candidates:
        key = (
            candidate["alias"],
            candidate["route"],
            candidate["url"],
        )

        rows.append(
            {
                **candidate,
                "prediction": predictions.get(
                    key,
                    {},
                ),
            }
        )

    write_json(
        OUTPUT_CANDIDATES,
        {
            "scanner": "dmitrytv_to_ngenix",
            "version": SCANNER_VERSION,
            "timestamp": now_utc(),
            "count": len(rows),
            "candidates": rows,
        },
    )


def write_diagnostics(
    results: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    evolution: Dict[str, Any],
    learning: Dict[str, Any],
) -> None:
    by_reason = defaultdict(int)
    by_route = defaultdict(
        lambda: defaultdict(int)
    )

    rows = []

    for row in results:
        reason = row.get(
            "reason",
            "UNKNOWN",
        )

        by_reason[reason] += 1
        by_route[
            row["route"]
        ][reason] += 1

        rows.append(
            {
                key: row.get(key)
                for key in (
                    "alias",
                    "family",
                    "route",
                    "url",
                    "origins",
                    "reason",
                    "http_status",
                    "reachable",
                    "working",
                    "validation",
                    "child_validation",
                    "rank_score",
                    "rank_class",
                    "confidence",
                    "attempts",
                    "predicted_score",
                    "prediction_error",
                    "golden_match",
                    "learning_feedback",
                    "rank_factors",
                    "selected_for_output",
                )
            }
        )

    write_json(
        OUTPUT_DIAGNOSTICS,
        {
            "scanner": "dmitrytv_to_ngenix",
            "version": SCANNER_VERSION,
            "timestamp": now_utc(),
            "metrics": metrics,
            "evolution_summary": evolution.get(
                "summary"
            ),
            "learning_update": learning,
            "summary_by_reason": dict(
                by_reason
            ),
            "summary_by_route": {
                key: dict(value)
                for key, value in by_route.items()
            },
            "results": rows,
        },
    )


def write_m3u(
    selected: List[Dict[str, Any]],
) -> int:
    count = 0

    with open(
        OUTPUT_M3U,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(
            "#EXTM3U\n"
        )

        for row in selected:
            if not row.get("working"):
                continue

            alias = row["alias"]

            title = row.get(
                "title"
            ) or (
                alias[3:]
                if alias.startswith(
                    "CH_"
                )
                else alias
            )

            route = row["route"]

            f.write(
                f'#EXTINF:-1 '
                f'tvg-id="{alias}" '
                f'tvg-name="{title}" '
                f'tvg-user-agent="{PRIMARY_UA}" '
                f'group-title="Ngenix-{route}",'
                f'{title}\n'
            )

            f.write(
                "#EXTVLCOPT:http-user-agent="
                f"{PRIMARY_UA}\n"
            )

            f.write(
                row["url"]
                + "\n"
            )

            count += 1

    return count


def write_learning_files(
    kb: Dict[str, Any],
    metrics: Dict[str, Any],
    evolution: Dict[str, Any],
    learning_update: Dict[str, Any],
) -> None:
    learning = knowledge_to_learning(
        kb,
        evolution,
    )

    learning[
        "last_metrics"
    ] = metrics

    learning[
        "last_learning_update"
    ] = learning_update

    write_json(
        OUTPUT_LEARNING,
        learning,
    )

    lines = [
        f"LEARNING SUMMARY v{SCANNER_VERSION}",
        f"TIME: {now_local()}",
        "",
        "=== COVERAGE ===",
        f"WORKING_COVERAGE : {metrics['WORKING_COVERAGE']}",
        f"CHANNEL_COVERAGE : {metrics['CHANNEL_COVERAGE']:.1%}",
        f"WORKING_RATE : {metrics['WORKING_RATE']:.1%}",
        f"GOLDEN_COVERAGE : {metrics['GOLDEN_COVERAGE']:.1%}",
        f"OUTPUT_M3U : {metrics['OUTPUT_M3U_ENTRIES']}",
        f"UNRESOLVED : {metrics['UNRESOLVED_CHANNELS']}",
        f"VALID_CHILD : {metrics['VALID_CHILD_PLAYLISTS']}",
        f"PLAYABLE_HINTS : {metrics['PLAYABLE_HINTS']}",
        "",
        "=== ONLINE LEARNING ===",
        f"SAMPLES : {learning_update['samples']}",
        f"CORRECT : {learning_update['correct']}",
        f"INCORRECT : {learning_update['incorrect']}",
        f"MAE : {learning_update['mean_absolute_error']:.4f}",
        "",
        "=== EVOLUTION ===",
    ]

    for key, value in (
        evolution.get(
            "summary",
            {},
        ).items()
    ):
        lines.append(
            f"  {key}: {value}"
        )

    lines += [
        "",
        "=== LEARNED FEATURE WEIGHTS ===",
    ]

    for key, value in sorted(
        kb.get(
            "learned_feature_weights",
            {},
        ).items()
    ):
        lines.append(
            f"  {key:<24} "
            f"{value:.4f}"
        )

    lines += [
        "",
        "HISTORY: playlist_ngenix_data.json + _1.._N (never overwritten)",
        "DmitryTV = observation source only.",
        "GOLDEN = supervision only.",
        "M3U = best CURRENT working candidate per CH_*.",
    ]

    with open(
        OUTPUT_LEARN_REPORT,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(
            "\n".join(lines)
            + "\n"
        )


def append_final_report(
    metrics: Dict[str, Any],
    count: int,
    data_path: str,
    revision: int,
    learning: Dict[str, Any],
) -> None:
    with open(
        OUTPUT_SKALA,
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            "\n============================================================\n"
        )
        f.write(
            f"FINAL SKALA REPORT v{SCANNER_VERSION}\n"
        )
        f.write(
            "============================================================\n"
        )
        f.write(
            f"TIME: {now_local()}\n"
        )
        f.write(
            f"DATA REVISION: {data_path} "
            f"(rev={revision})\n"
        )

        for key, value in metrics.items():
            if isinstance(
                value,
                float,
            ):
                f.write(
                    f"{key:<30} "
                    f"{value:.1%}\n"
                )
            else:
                f.write(
                    f"{key:<30} "
                    f"{value}\n"
                )

        f.write(
            f"LEARNING SAMPLES              "
            f"{learning['samples']}\n"
        )

        f.write(
            "\nIMMUTABLE HISTORY: "
            "base + _1.._N never overwritten\n"
        )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    perf_reset_run_state()
    resolve_output_paths()
    performance_log_start()

    for handler in list(
        logging.root.handlers
    ):
        if isinstance(
            handler,
            logging.FileHandler,
        ):
            logging.root.removeHandler(
                handler
            )
            handler.close()

    logging.root.addHandler(
        logging.FileHandler(
            LOG_FILE,
            encoding="utf-8",
        )
    )

    with open(
        OUTPUT_SKALA,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(
            f"SKALA NGENIX SCAN v{SCANNER_VERSION}\n"
        )
        f.write(
            f"START: {now_local()}\n\n"
        )

    started = datetime.now(
        timezone.utc
    )

    try:
        # HISTORY -> KNOWLEDGE
        chain = list_data_revisions()
        kb = load_current_knowledge()

        evolution = (
            analyze_revision_evolution(
                chain
            )
        )

        write_json(
            OUTPUT_EVOLUTION,
            evolution,
        )

        weights = get_weights(kb)

        parent = (
            chain[-1][1]
            if chain
            else None
        )

        revision = next_revision_number()

        skala(
            f"NEXT DATA REVISION: "
            f"{revision} "
            f"(parent={parent})"
        )

        # GOLDEN
        golden = load_golden()

        # SOURCE
        playlist = download_playlist()

        (
            urls,
            tails,
            observations,
        ) = extract_observations(
            playlist
        )

        skala(
            f"URLS={len(urls)} "
            f"TAILS={len(tails)} "
            f"OBS={len(observations)}"
        )

        if not observations:
            skala(
                "Нет наблюдений — выход",
                "ERROR",
            )
            return

        write_tails(
            urls,
            tails,
        )

        # DISCOVERY V9 BASE + V10 AUTONOMOUS STRUCTURAL DISCOVERY
        candidates = generate_candidates(
            observations,
            kb,
        )

        discovery_memory = load_discovery_memory()
        autonomous_candidates, discovery_meta = generate_autonomous_discovery_candidates(
            observations,
            kb,
            golden,
            discovery_memory,
        )
        candidates = merge_candidates(candidates, autonomous_candidates)

        for candidate in candidates:
            if candidate.get("discovery"):
                candidate["discovery"]["autonomous"] = True

        skala(
            f"AUTONOMOUS DISCOVERY: hypotheses={discovery_meta['hypotheses']} "
            f"generated={discovery_meta['generated']}"
        )
        skala(
            f"CANDIDATES: {len(candidates)}"
        )

        write_route_matrix(
            [observation["alias"] for observation in observations]
        )

        # PREDICTION
        predictions = {}

        for candidate in candidates:
            key = (
                candidate["alias"],
                candidate["route"],
                candidate["url"],
            )

            predictions[key] = (
                predict_score(
                    candidate,
                    kb,
                    golden,
                    weights,
                    evolution,
                )
            )

        write_candidates(
            candidates,
            predictions,
        )

        # ADAPTIVE ORDER
        ordered = (
            sort_candidates_with_exploration(
                candidates,
                predictions,
                kb,
            )
        )

        skala(
            "START NGENIX SCAN "
            "(HTTP + M3U8 + child validation)"
        )

        results = asyncio.run(
            scan_all(
                ordered,
                predictions,
            )
        )

        discovery_by_key = {
            (c["alias"], c["route"], c["url"]): c.get("discovery")
            for c in ordered
            if c.get("discovery")
        }

        for row in results:
            key = (row.get("alias"), row.get("route"), row.get("url"))
            if key in discovery_by_key:
                row["discovery"] = discovery_by_key[key]

        # FINAL SCORE
        for row in results:
            key = (
                row["alias"],
                row["route"],
                row["url"],
            )

            prediction = predictions.get(
                key,
                {},
            )

            row.update(
                finalize_score(
                    row,
                    prediction,
                    weights,
                )
            )

        # FEEDBACK
        attach_feedback(
            results
        )

        # LEARNING
        learning_update = (
            update_learning_from_results(
                kb,
                results,
            )
        )

        skala(
            f"ONLINE LEARNING: "
            f"samples="
            f"{learning_update['samples']} "
            f"MAE="
            f"{learning_update['mean_absolute_error']:.4f}"
        )

        # V10 DISCOVERY MEMORY / EVIDENCE
        discovery_update = update_discovery_memory(
            discovery_memory,
            results,
        )
        write_discovery_outputs(
            discovery_memory,
            discovery_update,
        )
        skala(
            f"AUTONOMOUS DISCOVERY MEMORY: "
            f"confirmed={discovery_update['confirmed_routes']} "
            f"candidate={discovery_update['candidate_routes']} "
            f"rejected={discovery_update['rejected_routes']}"
        )

        # SELECTION
        selected = (
            select_best_working(
                results
            )
        )

        metrics = compute_metrics(
            observations,
            results,
            selected,
            golden,
        )

        selected_keys = {
            (
                row["alias"],
                row["route"],
                row["url"],
            )
            for row in selected
        }

        for row in results:
            row[
                "selected_for_output"
            ] = (
                row["alias"],
                row["route"],
                row["url"],
            ) in selected_keys

        # KNOWLEDGE UPDATE
        apply_revision_to_knowledge(
            kb,
            {
                "revision": revision,
                "created_at": now_utc(),
                "results": results,
            },
        )

        # IMMUTABLE REVISION PATH
        data_path = (
            DATA_BASE
            if revision == 0
            else f"{DATA_PREFIX}_{revision}.json"
        )

        if os.path.exists(
            data_path
        ):
            data_path = versioned_path(
                data_path
            )

        working = [
            row
            for row in results
            if row.get("working")
        ]

        failed = [
            row
            for row in results
            if not row.get("working")
        ]

        payload = {
            "schema_version": 2,
            "revision": revision,
            "created_at": now_utc(),
            "parent": parent,
            "run": {
                "id": str(uuid.uuid4()),
                "scanner_version": SCANNER_VERSION,
                "source": PLAYLIST_URL,
                "golden": GOLDEN_URL,
                "ua": PRIMARY_UA,
            },
            "architecture": {
                "source_role": "observation_only",
                "golden_role": "supervision_only",
                "canonical_role": (
                    "MASTER_CANON_external_logical_layer"
                ),
                "learning": (
                    "transparent_online_statistical"
                ),
            },
            "metrics": metrics,
            "learning_update": learning_update,
            "discovery": {
                "meta": discovery_meta,
                "update": discovery_update,
                "memory_file": OUTPUT_DISCOVERY,
            },
            "evolution_summary": evolution.get(
                "summary"
            ),
            "working": working,
            "results": results,
            "selected_for_m3u": selected,
            "performance": perf_report(),
            "changes": {
                "new_working_candidates": [
                    {
                        "alias": row["alias"],
                        "family": row["family"],
                        "route": row["route"],
                        "url": row["url"],
                        "rank_score": row[
                            "rank_score"
                        ],
                        "validation": row[
                            "validation"
                        ],
                        "child_validation": row[
                            "child_validation"
                        ],
                    }
                    for row in working
                ],
                "failed_candidates": [
                    {
                        "alias": row["alias"],
                        "family": row["family"],
                        "route": row["route"],
                        "url": row["url"],
                        "reason": row[
                            "reason"
                        ],
                        "rank_score": row[
                            "rank_score"
                        ],
                    }
                    for row in failed
                ],
                "selected_for_m3u": [
                    {
                        "alias": row["alias"],
                        "route": row["route"],
                        "url": row["url"],
                        "rank_score": row[
                            "rank_score"
                        ],
                    }
                    for row in selected
                ],
            },
            "golden": {
                "url": GOLDEN_URL,
                "entries": len(
                    golden[
                        "entries"
                    ]
                ),
                "unique_channels": len(
                    golden[
                        "alias_set"
                    ]
                ),
                "role": "supervision_only",
            },
        }

        write_json(
            data_path,
            payload,
        )

        skala(
            f"DATA REVISION written: "
            f"{data_path} "
            f"(rev={revision}, "
            f"parent={parent})"
        )

        # OUTPUTS
        write_diagnostics(
            results,
            metrics,
            evolution,
            learning_update,
        )

        m3u_count = write_m3u(
            selected
        )

        skala(
            f"M3U READY: "
            f"{OUTPUT_M3U} "
            f"({m3u_count})",
            "FOUND",
        )

        write_learning_files(
            kb,
            metrics,
            evolution,
            learning_update,
        )

        append_final_report(
            metrics,
            m3u_count,
            data_path,
            revision,
            learning_update,
        )

        duration = (
            datetime.now(
                timezone.utc
            )
            - started
        ).total_seconds()

        skala(
            "============================================================"
        )
        skala(
            f"WORKING CHANNELS : "
            f"{metrics['WORKING_CHANNELS']}",
            "FOUND",
        )
        skala(
            f"DATA REVISION    : "
            f"{data_path}"
        )
        skala(
            f"M3U ENTRIES      : "
            f"{m3u_count}"
        )
        skala(
            f"DURATION         : "
            f"{duration:.3f}s"
        )
        skala(
            "COMPLETE"
        )

    except Exception as exc:
        skala(
            f"FATAL ERROR : "
            f"{type(exc).__name__}: {exc}",
            "ERROR",
        )
        raise


if __name__ == "__main__":
    main()


def perf_stage_note_001(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 001; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_001",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_002(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 002; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_002",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_003(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 003; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_003",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_004(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 004; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_004",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_005(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 005; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_005",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_006(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 006; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_006",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_007(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 007; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_007",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_008(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 008; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_008",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_009(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 009; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_009",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_010(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 010; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_010",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_011(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 011; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_011",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_012(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 012; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_012",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_013(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 013; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_013",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_014(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 014; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_014",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_015(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 015; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_015",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_016(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 016; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_016",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_017(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 017; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_017",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_018(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 018; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_018",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_019(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 019; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_019",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_020(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 020; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_020",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_021(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 021; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_021",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_022(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 022; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_022",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_023(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 023; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_023",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_024(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 024; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_024",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_025(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 025; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_025",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_026(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 026; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_026",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_027(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 027; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_027",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_028(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 028; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_028",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_029(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 029; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_029",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_030(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 030; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_030",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_031(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 031; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_031",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_032(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 032; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_032",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_033(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 033; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_033",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_034(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 034; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_034",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_035(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 035; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_035",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_036(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 036; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_036",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_037(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 037; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_037",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_038(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 038; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_038",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_039(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 039; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_039",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_040(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 040; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_040",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_041(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 041; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_041",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_042(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 042; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_042",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_043(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 043; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_043",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_044(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 044; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_044",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_045(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 045; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_045",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_046(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 046; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_046",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_047(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 047; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_047",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_048(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 048; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_048",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_049(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 049; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_049",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_050(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 050; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_050",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_051(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 051; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_051",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_052(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 052; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_052",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_053(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 053; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_053",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_054(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 054; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_054",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_055(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 055; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_055",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_056(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 056; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_056",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_057(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 057; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_057",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_058(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 058; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_058",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_059(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 059; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_059",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_060(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 060; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_060",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_061(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 061; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_061",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_062(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 062; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_062",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_063(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 063; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_063",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_064(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 064; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_064",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_065(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 065; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_065",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_066(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 066; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_066",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_067(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 067; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_067",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_068(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 068; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_068",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_069(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 069; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_069",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_070(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 070; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_070",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_071(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 071; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_071",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_072(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 072; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_072",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_073(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 073; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_073",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_074(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 074; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_074",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_075(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 075; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_075",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_076(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 076; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_076",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_077(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 077; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_077",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_078(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 078; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_078",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_079(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 079; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_079",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_080(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 080; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_080",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_081(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 081; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_081",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_082(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 082; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_082",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_083(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 083; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_083",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_084(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 084; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_084",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_085(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 085; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_085",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_086(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 086; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_086",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_087(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 087; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_087",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_088(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 088; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_088",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_089(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 089; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_089",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_090(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 090; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_090",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_091(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 091; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_091",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_092(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 092; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_092",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_093(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 093; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_093",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_094(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 094; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_094",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_095(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 095; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_095",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_096(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 096; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_096",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_097(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 097; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_097",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_098(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 098; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_098",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_099(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 099; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_099",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_100(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 100; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_100",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_101(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 101; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_101",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_102(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 102; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_102",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_103(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 103; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_103",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_104(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 104; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_104",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_105(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 105; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_105",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_106(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 106; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_106",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_107(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 107; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_107",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_108(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 108; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_108",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_109(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 109; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_109",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_110(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 110; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_110",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_111(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 111; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_111",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_112(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 112; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_112",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_113(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 113; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_113",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_114(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 114; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_114",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_115(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 115; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_115",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_116(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 116; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_116",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_117(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 117; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_117",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_118(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 118; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_118",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_119(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 119; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_119",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_120(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 120; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_120",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_121(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 121; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_121",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_122(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 122; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_122",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_123(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 123; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_123",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_124(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 124; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_124",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_125(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 125; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_125",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_126(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 126; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_126",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_127(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 127; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_127",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_128(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 128; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_128",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_129(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 129; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_129",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_130(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 130; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_130",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_131(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 131; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_131",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_132(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 132; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_132",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_133(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 133; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_133",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_134(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 134; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_134",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_135(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 135; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_135",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_136(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 136; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_136",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_137(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 137; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_137",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_138(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 138; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_138",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_139(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 139; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_139",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_140(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 140; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_140",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_141(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 141; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_141",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_142(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 142; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_142",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_143(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 143; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_143",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_144(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 144; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_144",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_145(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 145; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_145",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_146(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 146; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_146",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_147(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 147; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_147",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_148(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 148; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_148",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_149(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 149; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_149",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_150(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 150; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_150",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_151(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 151; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_151",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_152(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 152; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_152",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_153(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 153; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_153",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_154(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 154; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_154",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_155(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 155; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_155",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_156(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 156; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_156",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_157(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 157; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_157",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_158(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 158; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_158",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_159(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 159; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_159",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_160(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 160; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_160",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_161(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 161; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_161",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_162(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 162; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_162",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_163(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 163; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_163",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_164(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 164; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_164",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_165(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 165; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_165",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_166(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 166; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_166",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_167(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 167; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_167",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_168(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 168; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_168",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_169(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 169; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_169",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_170(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 170; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_170",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_171(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 171; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_171",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_172(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 172; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_172",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_173(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 173; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_173",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_174(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 174; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_174",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_175(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 175; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_175",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_176(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 176; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_176",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_177(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 177; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_177",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_178(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 178; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_178",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_179(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 179; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_179",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_180(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 180; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_180",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_181(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 181; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_181",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_182(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 182; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_182",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_183(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 183; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_183",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_184(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 184; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_184",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_185(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 185; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_185",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_186(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 186; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_186",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_187(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 187; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_187",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_188(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 188; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_188",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_189(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 189; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_189",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_190(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 190; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_190",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_191(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 191; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_191",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_192(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 192; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_192",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_193(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 193; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_193",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_194(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 194; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_194",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_195(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 195; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_195",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_196(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 196; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_196",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_197(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 197; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_197",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_198(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 198; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_198",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_199(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 199; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_199",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_200(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 200; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_200",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_201(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 201; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_201",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_202(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 202; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_202",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_203(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 203; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_203",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_204(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 204; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_204",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_205(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 205; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_205",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_206(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 206; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_206",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_207(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 207; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_207",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_208(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 208; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_208",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_209(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 209; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_209",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_210(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 210; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_210",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_211(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 211; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_211",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_212(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 212; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_212",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_213(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 213; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_213",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_214(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 214; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_214",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_215(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 215; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_215",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_216(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 216; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_216",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_217(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 217; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_217",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_218(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 218; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_218",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_219(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 219; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_219",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_220(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 220; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_220",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_221(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 221; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_221",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_222(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 222; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_222",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_223(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 223; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_223",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_224(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 224; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_224",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_225(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 225; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_225",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_226(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 226; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_226",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_227(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 227; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_227",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_228(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 228; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_228",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_229(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 229; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_229",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_230(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 230; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_230",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_231(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 231; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_231",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_232(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 232; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_232",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_233(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 233; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_233",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_234(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 234; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_234",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_235(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 235; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_235",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_236(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 236; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_236",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_237(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 237; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_237",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_238(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 238; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_238",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_239(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 239; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_239",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


def perf_stage_note_240(value: Any = None) -> Dict[str, Any]:
    """Performance extension hook 240; telemetry-only and result-preserving."""
    return {
        "hook": "perf_stage_note_240",
        "enabled": PERF_ENABLE_METRICS,
        "value_present": value is not None,
    }


# ============================================================
# NGENIX MAX DISCOVERY / PRODUCTION INVENTORY EXTENSION
# ============================================================
# Purpose:
#   Preserve every NGENIX fact that can be observed from known inputs and
#   already discovered endpoints: hostnames, sXXXXX identities, account-
#   prefixed aliases, complete paths, DNS A/AAAA sets, HTTP metadata,
#   playlist children, relationships and immutable run history.
#
# Safety boundary:
#   No enumeration of unknown sXXXXX IDs, no guessed paths, no auth bypass,
#   no token extraction, no port sweep. Discovery expands only from URLs and
#   playlist references that are actually observed in permitted inputs.

import hashlib
import ipaddress
from urllib.parse import urljoin

MAX_DISCOVERY_DEPTH = max(0, int(os.getenv("NGENIX_DISCOVERY_DEPTH", "3")))
MAX_BODY_BYTES = max(4096, int(os.getenv("NGENIX_MAX_BODY_BYTES", str(1024 * 1024))))
MAX_DISCOVERY_URLS = max(100, int(os.getenv("NGENIX_MAX_DISCOVERY_URLS", "50000")))
MAX_PER_HOST = max(1, int(os.getenv("NGENIX_MAX_PER_HOST", "4")))
DISCOVERY_TIMEOUT = max(2, float(os.getenv("NGENIX_DISCOVERY_TIMEOUT", "8")))

MAX_OUTPUT_INVENTORY = "NGENIX_MAX_INVENTORY.json"
MAX_OUTPUT_GRAPH = "NGENIX_MAX_GRAPH.json"
MAX_OUTPUT_HISTORY = "NGENIX_MAX_HISTORY.json"
MAX_OUTPUT_REPORT = "NGENIX_MAX_SKALA.txt"

NG_HOST_RE = re.compile(
    r"https?://(?P<host>[a-z0-9.-]+\.cdn\.ngenix\.net)(?P<path>/[^\s\"'<>]*)?",
    re.I,
)
NG_SERVICE_RE = re.compile(r"(?<![a-z0-9-])(?:a\d+-)?(?P<sid>s\d{5,})(?![a-z0-9-])", re.I)


def max_canon_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
        if not p.hostname or not p.hostname.lower().endswith(".cdn.ngenix.net"):
            return ""
        host = p.hostname.lower()
        path = re.sub(r"/{2,}", "/", p.path or "/")
        return f"{p.scheme.lower()}://{host}{path}" + (f"?{p.query}" if p.query else "")
    except Exception:
        return ""


def max_service_ids(host: str, text: str = "") -> list[str]:
    found = set()
    for value in (host or "", text or ""):
        for m in NG_SERVICE_RE.finditer(value):
            found.add(m.group("sid").lower())
    return sorted(found)


def max_extract_urls(text: str, base_url: str = "") -> set[str]:
    out: set[str] = set()
    for m in NG_HOST_RE.finditer(text or ""):
        u = max_canon_url(m.group(0).rstrip(".,;)]}>"))
        if u:
            out.add(u)
    # Relative playlist URIs become observable NGENIX endpoints only when their
    # base URL is itself a known NGENIX endpoint.
    if base_url:
        try:
            base = urlparse(base_url)
            if base.hostname and base.hostname.endswith(".cdn.ngenix.net"):
                for raw in (text or "").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith(("/", "http://", "https://")):
                        candidate = urljoin(base_url, line)
                        if urlparse(candidate).hostname and urlparse(candidate).hostname.endswith(".cdn.ngenix.net"):
                            cu = max_canon_url(candidate)
                            if cu:
                                out.add(cu)
        except Exception:
            pass
    return out


def max_read_repo_urls(root: Path) -> tuple[set[str], dict[str, list[str]]]:
    urls: set[str] = set()
    sources: dict[str, list[str]] = defaultdict(list)
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if "ngenix_constellation" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for u in max_extract_urls(text):
            urls.add(u)
            sources[u].append(str(path))
    return urls, sources


def max_dns(host: str) -> dict:
    addresses = set()
    errors = []
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(host, 443, family=family, type=socket.SOCK_STREAM)
            for info in infos:
                addr = info[4][0]
                if addr:
                    addresses.add(addr)
        except Exception as exc:
            errors.append(str(exc))
    return {
        "addresses": sorted(addresses, key=lambda x: (ipaddress.ip_address(x).version, int(ipaddress.ip_address(x)))),
        "errors": sorted(set(errors)),
    }


def max_content_class(content_type: str | None, body: bytes) -> str:
    ct = (content_type or "").lower()
    sample = body[:4096].decode("utf-8", "replace")
    if "mpegurl" in ct or "m3u8" in ct or "#extm3u" in sample.lower():
        return "m3u8"
    if "json" in ct:
        return "json"
    if "text" in ct:
        return "text"
    return "binary_or_unknown"


def max_fingerprint(body: bytes) -> str:
    return hashlib.sha256(body[:MAX_BODY_BYTES]).hexdigest()


def max_fetch_known_url(url: str) -> dict:
    started = time.perf_counter()
    req = Request(url, headers={
        "User-Agent": PRIMARY_UA,
        "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,application/json,text/plain,*/*",
        "Connection": "close",
    })
    result = {
        "url": url,
        "status": None,
        "content_type": None,
        "content_length": None,
        "bytes_read": 0,
        "latency_ms": None,
        "content_class": None,
        "sha256_prefix_body": None,
        "child_urls": [],
        "error": None,
    }
    try:
        with urlopen(req, timeout=DISCOVERY_TIMEOUT, context=ssl.create_default_context()) as r:
            result["status"] = int(getattr(r, "status", 200))
            result["content_type"] = r.headers.get("Content-Type")
            result["content_length"] = r.headers.get("Content-Length")
            body = r.read(MAX_BODY_BYTES)
            result["bytes_read"] = len(body)
            result["content_class"] = max_content_class(result["content_type"], body)
            result["sha256_prefix_body"] = max_fingerprint(body)
            if result["content_class"] == "m3u8":
                result["child_urls"] = sorted(max_extract_urls(body.decode("utf-8", "replace"), url))
    except HTTPError as exc:
        result["status"] = int(exc.code)
        result["error"] = str(exc)
    except Exception as exc:
        result["error"] = repr(exc)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def max_load_history(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "runs": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("runs"), list):
            return value
    except Exception:
        pass
    return {"schema_version": 1, "runs": []}


def max_write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def max_inventory(root: Path, out_dir: Path) -> dict:
    discovered, source_map = max_read_repo_urls(root)

    # Also consume previously generated inventory/diagnostic artifacts if present.
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if "ngenix_constellation" not in path.parts and "ngenix" not in path.name.lower():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for u in max_extract_urls(text):
            discovered.add(u)
            source_map[u].append(str(path))

    discovered = set(sorted(discovered))
    queue = [(u, 0) for u in sorted(discovered)]
    seen = set(discovered)
    fetched: dict[str, dict] = {}
    host_records: dict[str, dict] = {}
    relationships: list[dict] = []

    # Bounded synchronous fetches deliberately avoid an unbounded fan-out.
    # Each URL is fetched at most once per run; nested URLs are accepted only
    # when actually present in an observed response.
    while queue and len(seen) <= MAX_DISCOVERY_URLS:
        url, depth = queue.pop(0)
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host.endswith(".cdn.ngenix.net"):
            continue

        if host not in host_records:
            dns = max_dns(host)
            host_records[host] = {
                "hostname": host,
                "service_ids": max_service_ids(host),
                "addresses": dns["addresses"],
                "dns_errors": dns["errors"],
                "checked_at": utc_now(),
            }

        result = max_fetch_known_url(url)
        result["depth"] = depth
        result["hostname"] = host
        result["path"] = parsed.path or "/"
        result["service_ids"] = max_service_ids(host, parsed.path or "")
        result["sources"] = sorted(set(source_map.get(url, [])))
        fetched[url] = result

        for child in result.get("child_urls", []):
            relationships.append({
                "parent": url,
                "child": child,
                "relation": "playlist_reference",
                "depth": depth + 1,
            })
            if depth < MAX_DISCOVERY_DEPTH and child not in seen and len(seen) < MAX_DISCOVERY_URLS:
                seen.add(child)
                queue.append((child, depth + 1))

    services = defaultdict(lambda: {"hostnames": set(), "urls": set(), "paths": set()})
    for url, row in fetched.items():
        host = row["hostname"]
        parsed = urlparse(url)
        for sid in max_service_ids(host, parsed.path or ""):
            services[sid]["hostnames"].add(host)
            services[sid]["urls"].add(url)
            services[sid]["paths"].add(parsed.path or "/")

    # JSON-safe conversion.
    services_json = {
        sid: {
            "hostnames": sorted(v["hostnames"]),
            "urls": sorted(v["urls"]),
            "paths": sorted(v["paths"]),
        }
        for sid, v in sorted(services.items())
    }

    inventory = {
        "schema_version": 1,
        "engine": ENGINE_NAME,
        "scanner_version": SCANNER_VERSION,
        "generated_at": utc_now(),
        "scope": {
            "known_inputs_only": True,
            "unknown_service_bruteforce": False,
            "path_guessing": False,
            "authorization_bypass": False,
            "recursive_observed_playlist_discovery": True,
        },
        "limits": {
            "max_depth": MAX_DISCOVERY_DEPTH,
            "max_body_bytes": MAX_BODY_BYTES,
            "max_urls": MAX_DISCOVERY_URLS,
            "max_per_host": MAX_PER_HOST,
        },
        "summary": {
            "observed_urls": len(discovered),
            "fetched_urls": len(fetched),
            "hostnames": len(host_records),
            "service_ids": len(services_json),
            "ips": len({ip for h in host_records.values() for ip in h["addresses"]}),
            "playlist_references": len(relationships),
        },
        "hostnames": sorted(host_records.values(), key=lambda x: x["hostname"]),
        "services": services_json,
        "urls": fetched,
        "relationships": relationships,
    }

    max_write_json(out_dir / MAX_OUTPUT_INVENTORY, inventory)
    graph = {
        "schema_version": 1,
        "generated_at": inventory["generated_at"],
        "nodes": [],
        "edges": relationships,
    }
    for host, row in host_records.items():
        graph["nodes"].append({"type": "hostname", "id": host, "service_ids": row["service_ids"], "addresses": row["addresses"]})
    for sid, row in services_json.items():
        graph["nodes"].append({"type": "service", "id": sid})
    for url in fetched:
        graph["nodes"].append({"type": "url", "id": url})
    max_write_json(out_dir / MAX_OUTPUT_GRAPH, graph)

    history_path = out_dir / MAX_OUTPUT_HISTORY
    history = max_load_history(history_path)
    history["runs"].append({
        "run_id": str(uuid.uuid4()),
        "generated_at": inventory["generated_at"],
        "summary": inventory["summary"],
        "hostnames": sorted(host_records),
        "service_ids": sorted(services_json),
        "ips": sorted({ip for h in host_records.values() for ip in h["addresses"]}),
        "urls": sorted(fetched),
    })
    max_write_json(history_path, history)

    report_lines = [
        f"NGENIX MAX DISCOVERY / {SCANNER_VERSION}",
        "=" * 72,
        f"UTC: {inventory['generated_at']}",
        f"Observed URLs : {inventory['summary']['observed_urls']}",
        f"Fetched URLs  : {inventory['summary']['fetched_urls']}",
        f"Hostnames     : {inventory['summary']['hostnames']}",
        f"sXXXXX IDs    : {inventory['summary']['service_ids']}",
        f"Unique IPs    : {inventory['summary']['ips']}",
        f"Playlist refs : {inventory['summary']['playlist_references']}",
        "",
        "HOSTNAMES / DNS",
    ]
    for row in inventory["hostnames"]:
        report_lines.append(f"{row['hostname']} -> {', '.join(row['addresses']) or 'UNRESOLVED'}")
    report_lines += ["", "SERVICES / PATHS"]
    for sid, row in inventory["services"].items():
        report_lines.append(f"{sid}: {len(row['urls'])} URL(s), {len(row['paths'])} path(s)")
    (out_dir / MAX_OUTPUT_REPORT).write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return inventory


def production_max_discovery() -> None:
    root = Path(os.getenv("NGENIX_REPOSITORY", ".")).resolve()
    out_dir = Path(os.getenv("NGENIX_MAX_OUTPUT_DIR", "data/ngenix_constellation")).resolve()
    skala(f"MAX DISCOVERY: root={root}")
    inventory = max_inventory(root, out_dir)
    skala(
        "MAX DISCOVERY COMPLETE: "
        f"urls={inventory['summary']['fetched_urls']} "
        f"hosts={inventory['summary']['hostnames']} "
        f"services={inventory['summary']['service_ids']} "
        f"ips={inventory['summary']['ips']}"
    )


if __name__ == "__main__":
    # Run the existing learning/validation pipeline first when explicitly enabled,
    # then run the lossless known-endpoint inventory pass.
    if os.getenv("NGENIX_RUN_LEGACY", "1") == "1":
        try:
            main()
        except Exception as exc:
            print(f"[LEGACY-PIPELINE-ERROR] {exc!r}")
    production_max_discovery()
