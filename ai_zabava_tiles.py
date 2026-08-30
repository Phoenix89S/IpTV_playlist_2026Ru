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
import json
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

try:
    import orjson
except ImportError:
    orjson = None


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
SCANNER_VERSION = "12.0.6"

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
# V12.0.6: performance release; throughput and local processing are optimized without changing scan logic.y removing validation.
# Override with NGENIX_CONCURRENCY in CI if the CDN/network needs tuning.
CONCURRENCY_LIMIT = max(1, int(os.getenv("NGENIX_CONCURRENCY", "180")))
CONNECTOR_LIMIT = max(CONCURRENCY_LIMIT, int(os.getenv("NGENIX_CONNECTOR_LIMIT", str(CONCURRENCY_LIMIT))))
DNS_CACHE_TTL = max(0, int(os.getenv("NGENIX_DNS_CACHE_TTL", "300")))

# V12.0.6 Performance Engine:
# execution optimizations only; discovery/ranking/validation/learning semantics stay intact.
JSON_INDENT = 2
JSON_CACHE_ENABLED = True
JSON_CACHE = {}
JSON_CACHE_MTIME = {}
LOG_BUFFER_SIZE = max(1, int(os.getenv("NGENIX_LOG_BUFFER_SIZE", "512")))
LOG_BUFFER = []
LOG_BUFFER_FLUSH_EVERY = max(1, int(os.getenv("NGENIX_LOG_FLUSH_EVERY", "512")))

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


def _json_dumps(data: Any, *, pretty: bool = True) -> str:
    if orjson is not None:
        option = orjson.OPT_INDENT_2 if pretty else 0
        return orjson.dumps(
            data,
            option=option,
        ).decode("utf-8")

    return json.dumps(
        data,
        ensure_ascii=False,
        indent=JSON_INDENT if pretty else None,
        separators=None if pretty else (",", ":"),
    )


def write_json(path: str, data: Any) -> None:
    payload = _json_dumps(data, pretty=True)
    with open(path, "w", encoding="utf-8", buffering=1024 * 1024) as f:
        f.write(payload)


def load_json_safe(path: str) -> Optional[Dict[str, Any]]:
    try:
        stat = os.stat(path)
        mtime = stat.st_mtime_ns

        if JSON_CACHE_ENABLED:
            cached_mtime = JSON_CACHE_MTIME.get(path)
            if cached_mtime == mtime and path in JSON_CACHE:
                return JSON_CACHE[path]

        with open(path, "rb", buffering=1024 * 1024) as f:
            raw = f.read()

        if orjson is not None:
            data = orjson.loads(raw)
        else:
            data = json.loads(raw)

        if JSON_CACHE_ENABLED:
            JSON_CACHE_MTIME[path] = mtime
            JSON_CACHE[path] = data

        return data

    except Exception as exc:
        skala(f"Cannot read {path}: {exc}", "WARN")
        return None

def _flush_log_buffer() -> None:
    global LOG_BUFFER
    if LOG_BUFFER:
        print("\n".join(LOG_BUFFER))
        LOG_BUFFER = []


def skala(message: str, level: str = "INFO") -> None:
    line = f"{now_local()} [{level:<7}] {message}"
    LOG_BUFFER.append(line)
    if len(LOG_BUFFER) >= LOG_BUFFER_FLUSH_EVERY:
        _flush_log_buffer()


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
                async with session.get(
                    child_url,
                    timeout=aiohttp.ClientTimeout(
                        total=CHILD_TIMEOUT,
                        connect=min(3, CHILD_TIMEOUT),
                        sock_connect=min(3, CHILD_TIMEOUT),
                        sock_read=CHILD_TIMEOUT,
                    ),
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

            except Exception as exc:
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
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(
                    total=HTTP_TIMEOUT,
                    connect=min(3, HTTP_TIMEOUT),
                    sock_connect=min(3, HTTP_TIMEOUT),
                    sock_read=HTTP_TIMEOUT,
                ),
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

    connector = aiohttp.TCPConnector(
        limit=CONNECTOR_LIMIT,
        limit_per_host=CONNECTOR_LIMIT,
        ttl_dns_cache=DNS_CACHE_TTL,
        enable_cleanup_closed=True,
    )

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
    resolve_output_paths()

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


import atexit
atexit.register(_flush_log_buffer)

if __name__ == "__main__":
    main()
