#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DmitryTV → Ngenix scanner (v7.3)

IMMUTABLE HISTORY:
  playlist_ngenix_data.json          — base (создаётся один раз, не перезаписывается)
  playlist_ngenix_data_1.json        — revision 1
  playlist_ngenix_data_2.json        — revision 2
  ...
  playlist_ngenix_data_N.json

При старте: base + _1.._N → CURRENT KNOWLEDGE → ranking → scan → NEW REVISION _N+1

GOLDEN = supervision only.
M3U = best REALLY WORKING per CH_*.
Прочие артефакты (m3u, skala, log, …) — versioned_path (_1, _2, …) без затирания.
zabava_learning.json — агрегат из всей истории (можно пересобирать).
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
from urllib.parse import urlparse

import aiohttp
import requests


# ============================================================
#                         НАСТРОЙКИ
# ============================================================

PLAYLIST_URL = (
    "http://dmitry-tv.ddns.net/iptv/freesat/gtmedia/ZABAVA/custom_url.m3u"
)

GOLDEN_URL = (
    "https://raw.githubusercontent.com/Phoenix89S/IpTV_playlist_2026Ru/main/real_worked.m3u"
)

NGENIX_BASE = "https://zabava-htlive.cdn.ngenix.net"

SCANNER_VERSION = "7.3"

# Immutable data history
DATA_BASE = "playlist_ngenix_data.json"
DATA_PREFIX = "playlist_ngenix_data"

OUTPUT_M3U   = "playlist_ngenix_working.m3u"
OUTPUT_SKALA = "skala_ngenix_report.txt"
LOG_FILE     = "scan_ngenix_process.log"

OUTPUT_TAILS_TXT    = "zabava_tails.txt"
OUTPUT_TAILS_JSON   = "zabava_tails.json"
OUTPUT_CANDIDATES   = "zabava_candidates.json"
OUTPUT_DIAGNOSTICS  = "zabava_diagnostics.json"
OUTPUT_LEARNING     = "zabava_learning.json"
OUTPUT_LEARN_REPORT = "zabava_learning_report.txt"

_BASE_OUTPUTS = {
    "m3u": OUTPUT_M3U,
    "skala": OUTPUT_SKALA,
    "log": LOG_FILE,
    "tails_txt": OUTPUT_TAILS_TXT,
    "tails_json": OUTPUT_TAILS_JSON,
    "candidates": OUTPUT_CANDIDATES,
    "diagnostics": OUTPUT_DIAGNOSTICS,
    "learn_report": OUTPUT_LEARN_REPORT,
}

KNOWN_ROUTES = ("hls", "region", "regions")

USER_AGENTS = [
    "HlsWinkPlayer",
    "WINK/1.40.1 (AndroidTV/9) HlsWinkPlayer",
]
PRIMARY_UA = USER_AGENTS[1]

HTTP_TIMEOUT = 8
DOWNLOAD_TIMEOUT = 25
CONCURRENCY_LIMIT = 30
EXPLORATION_RATIO = 0.20


# ============================================================
#                       ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


def now_local() -> str:
    return datetime.now().astimezone().isoformat()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def skala(message: str, level: str = "INFO") -> None:
    line = f"{now_local()} [{level:<7}] {message}"
    print(line)
    with open(OUTPUT_SKALA, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def versioned_path(filename: str) -> str:
    if not os.path.exists(filename):
        return filename
    root, ext = os.path.splitext(filename)
    n = 1
    while True:
        candidate = f"{root}_{n}{ext}"
        if not os.path.exists(candidate):
            return candidate
        n += 1


def resolve_output_paths() -> None:
    global OUTPUT_M3U, OUTPUT_SKALA, LOG_FILE
    global OUTPUT_TAILS_TXT, OUTPUT_TAILS_JSON
    global OUTPUT_CANDIDATES, OUTPUT_DIAGNOSTICS, OUTPUT_LEARN_REPORT

    OUTPUT_M3U = versioned_path(_BASE_OUTPUTS["m3u"])
    OUTPUT_SKALA = versioned_path(_BASE_OUTPUTS["skala"])
    LOG_FILE = versioned_path(_BASE_OUTPUTS["log"])
    OUTPUT_TAILS_TXT = versioned_path(_BASE_OUTPUTS["tails_txt"])
    OUTPUT_TAILS_JSON = versioned_path(_BASE_OUTPUTS["tails_json"])
    OUTPUT_CANDIDATES = versioned_path(_BASE_OUTPUTS["candidates"])
    OUTPUT_DIAGNOSTICS = versioned_path(_BASE_OUTPUTS["diagnostics"])
    OUTPUT_LEARN_REPORT = versioned_path(_BASE_OUTPUTS["learn_report"])


# ============================================================
#         IMMUTABLE DATA HISTORY (playlist_ngenix_data*)
# ============================================================

def list_data_revisions() -> List[Tuple[int, str]]:
    """
    Возвращает [(0, base), (1, _1), (2, _2), ...] только существующие файлы.
    revision 0 = DATA_BASE
    """
    items: List[Tuple[int, str]] = []
    if os.path.exists(DATA_BASE):
        items.append((0, DATA_BASE))
    pat = re.compile(rf"^{re.escape(DATA_PREFIX)}_(\d+)\.json$")
    for name in os.listdir("."):
        m = pat.match(name)
        if m:
            items.append((int(m.group(1)), name))
    items.sort(key=lambda x: x[0])
    return items


def next_revision_number() -> int:
    revs = list_data_revisions()
    if not revs:
        return 0
    return revs[-1][0] + 1


def load_json_safe(path: str) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        skala(f"Cannot read {path}: {e}", "WARN")
        return None


def empty_knowledge() -> Dict[str, Any]:
    return {
        "channels": {},
        "routes": {r: {"ok": 0, "fail": 0} for r in KNOWN_ROUTES},
        "seen_aliases": set(),
        "revisions_applied": [],
    }


def apply_revision_to_knowledge(kb: Dict, rev: Dict) -> None:
    """Агрегирует одну revision в CURRENT KNOWLEDGE."""
    rev_id = rev.get("revision", "?")
    kb["revisions_applied"].append(rev_id)

    results = rev.get("results") or []
    if not results and "working" in rev:
        for w in rev.get("working") or []:
            results.append({**w, "working": True})
        for f in (rev.get("changes") or {}).get("failed_candidates") or []:
            results.append({**f, "working": False})

    for r in results:
        alias = r.get("alias")
        route = r.get("route")
        if not alias or not route:
            continue
        ok = bool(r.get("working"))
        kb["seen_aliases"].add(alias)
        kb["routes"].setdefault(route, {"ok": 0, "fail": 0})
        if ok:
            kb["routes"][route]["ok"] += 1
        else:
            kb["routes"][route]["fail"] += 1

        kb["channels"].setdefault(alias, {})
        st = kb["channels"][alias].setdefault(
            route, {"ok": 0, "fail": 0, "last_score": 0, "last_working": False}
        )
        if ok:
            st["ok"] += 1
        else:
            st["fail"] += 1
        st["last_score"] = r.get("rank_score", st.get("last_score", 0))
        st["last_working"] = ok


def load_current_knowledge() -> Dict[str, Any]:
    """
    base + _1 + _2 + ... → CURRENT KNOWLEDGE
    История не стирается.
    """
    kb = empty_knowledge()
    chain = list_data_revisions()
    if not chain:
        skala("DATA HISTORY: empty (first run)")
        return kb

    skala(f"DATA HISTORY chain: {[p for _, p in chain]}")
    for rev_num, path in chain:
        data = load_json_safe(path)
        if not data:
            continue
        apply_revision_to_knowledge(kb, data)
        skala(f"  applied rev={rev_num} file={path}")

    kb["seen_aliases"] = set(kb["seen_aliases"])
    n_ch = len(kb["channels"])
    skala(f"CURRENT KNOWLEDGE: {n_ch} channels, revs={kb['revisions_applied']}")
    return kb


def knowledge_to_learning(kb: Dict) -> Dict[str, Any]:
    """Собрать zabava_learning-совместимый объект из агрегата."""
    channels = {}
    for alias, routes in kb.get("channels", {}).items():
        channels[alias] = {}
        for route, st in routes.items():
            channels[alias][route] = {
                "ok": st.get("ok", 0),
                "fail": st.get("fail", 0),
                "last_score": st.get("last_score", 0),
            }
    return {
        "version": 1,
        "updated": now_utc(),
        "source": "aggregated_from_immutable_history",
        "channels": channels,
        "routes": dict(kb.get("routes", {})),
        "patterns": {},
        "history": [],
        "weights": {
            "http_ok": 20.0,
            "m3u8_valid": 20.0,
            "media_playlist": 15.0,
            "segments_hint": 10.0,
            "reference_similarity": 12.0,
            "route_probability": 15.0,
            "history_bonus": 8.0,
        },
        "revisions_applied": kb.get("revisions_applied", []),
    }


def channel_route_rate(kb: Dict, alias: str, route: str) -> float:
    st = kb.get("channels", {}).get(alias, {}).get(route)
    if st:
        t = st.get("ok", 0) + st.get("fail", 0)
        if t:
            return st["ok"] / t
    rs = kb.get("routes", {}).get(route, {"ok": 0, "fail": 0})
    rt = rs["ok"] + rs["fail"]
    return (rs["ok"] / rt) if rt else 0.5


def observation_count(kb: Dict, alias: str, route: str) -> int:
    st = kb.get("channels", {}).get(alias, {}).get(route, {})
    return st.get("ok", 0) + st.get("fail", 0)


def write_data_revision(
    rev_num: int,
    parent_name: Optional[str],
    results: List[Dict],
    selected: List[Dict],
    metrics: Dict,
    observations: List[Dict],
    candidates: List[Dict],
    golden: Dict,
) -> str:
    """
    rev 0 → playlist_ngenix_data.json (base)
    rev N → playlist_ngenix_data_N.json
    НИКОГДА не перезаписывает существующий файл.
    """
    if rev_num == 0:
        path = DATA_BASE
    else:
        path = f"{DATA_PREFIX}_{rev_num}.json"

    if os.path.exists(path):
        path = versioned_path(path)

    working = [r for r in results if r.get("working")]
    failed = [r for r in results if not r.get("working")]

    payload = {
        "schema_version": 1,
        "revision": rev_num,
        "created_at": now_utc(),
        "parent": parent_name,
        "run": {
            "id": str(uuid.uuid4()),
            "scanner_version": SCANNER_VERSION,
            "source": PLAYLIST_URL,
            "golden": GOLDEN_URL,
            "ua": PRIMARY_UA,
        },
        "changes": {
            "new_working_candidates": [
                {
                    "alias": r["alias"],
                    "route": r["route"],
                    "url": r["url"],
                    "rank_score": r.get("rank_score"),
                    "golden_match": r.get("golden_match", False),
                }
                for r in working
            ],
            "failed_candidates": [
                {
                    "alias": r["alias"],
                    "route": r["route"],
                    "url": r["url"],
                    "reason": r.get("reason"),
                    "rank_score": r.get("rank_score"),
                }
                for r in failed
            ],
            "selected_for_m3u": [
                {
                    "alias": s["alias"],
                    "route": s["route"],
                    "url": s["url"],
                    "rank_score": s.get("rank_score"),
                }
                for s in selected
            ],
        },
        "statistics": {
            "checked": len(results),
            "working": len(working),
            "failed": len(failed),
            "output_channels": len(selected),
            "observations": len(observations),
            "candidates": len(candidates),
        },
        "metrics": metrics,
        "working": working,
        "results": results,
        "selected_for_m3u": selected,
        "golden": {
            "url": GOLDEN_URL,
            "entries": len(golden.get("entries", [])),
            "unique_channels": len(golden.get("alias_set", [])),
            "role": "supervision_only",
        },
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    skala(f"DATA REVISION written: {path} (rev={rev_num}, parent={parent_name})")
    return path


# ============================================================
#                    GOLDEN
# ============================================================

def load_golden() -> Dict[str, Any]:
    golden: Dict[str, Any] = {
        "source": GOLDEN_URL,
        "entries": [],
        "by_alias": {},
        "routes": defaultdict(int),
        "ua_set": set(),
        "alias_set": set(),
    }
    try:
        resp = requests.get(
            GOLDEN_URL, timeout=DOWNLOAD_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        skala(f"GOLDEN download failed: {e}", "WARN")
        return golden

    current_ua = ""
    current_title = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            m_ua = re.search(r'tvg-user-agent="([^"]+)"', line, re.I)
            if m_ua:
                current_ua = m_ua.group(1)
            if "," in line:
                current_title = line.split(",", 1)[1].strip()
            continue
        if line.startswith("#EXTVLCOPT:http-user-agent="):
            current_ua = line.split("=", 1)[1].strip().strip('"')
            continue
        if line.startswith(("http://", "https://")):
            aliases = re.findall(r"CH_[A-Za-z0-9_-]+", line)
            route = "other"
            m = re.search(r"/(hls|region|regions)/", line, re.I)
            if m:
                route = m.group(1).lower()
            alias = aliases[0] if aliases else (
                f"CH_{current_title}" if current_title else "UNKNOWN"
            )
            entry = {
                "alias": alias,
                "route": route,
                "url": line,
                "ua": current_ua or PRIMARY_UA,
                "title": current_title,
            }
            golden["entries"].append(entry)
            golden["by_alias"].setdefault(alias, []).append(entry)
            golden["routes"][route] += 1
            golden["alias_set"].add(alias)
            if current_ua:
                golden["ua_set"].add(current_ua)
            current_title = ""

    golden["routes"] = dict(golden["routes"])
    golden["ua_set"] = list(golden["ua_set"])
    golden["alias_set"] = list(golden["alias_set"])
    skala(
        f"GOLDEN LOADED: {len(golden['entries'])} entries, "
        f"{len(golden['alias_set'])} unique CH_*"
    )
    return golden


def reference_similarity(alias: str, route: str, golden: Dict) -> float:
    entries = golden.get("by_alias", {}).get(alias, [])
    if not entries:
        base = re.sub(r"_\d+$", "", alias)
        for a, lst in golden.get("by_alias", {}).items():
            if re.sub(r"_\d+$", "", a) == base:
                entries = lst
                break
    if not entries:
        return 0.0
    score = 0.35
    if any(e["route"] == route for e in entries):
        score += 0.45
    elif any(e["route"] in KNOWN_ROUTES for e in entries):
        score += 0.15
    if any("variant.m3u8" in e.get("url", "") for e in entries):
        score += 0.2
    return min(1.0, score)


def is_golden_match(alias: str, route: str, golden: Dict) -> bool:
    for e in golden.get("by_alias", {}).get(alias, []):
        if e.get("route") == route:
            return True
    return False


# ============================================================
#                    DmitryTV
# ============================================================

def download_playlist() -> str:
    skala("========================================")
    skala(f"       START DMITRYTV → NGENIX v{SCANNER_VERSION}")
    skala("========================================")
    skala(f"SOURCE : {PLAYLIST_URL}")
    response = requests.get(
        PLAYLIST_URL,
        timeout=DOWNLOAD_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    skala(f"DOWNLOAD OK : HTTP {response.status_code}")
    skala(f"PLAYLIST SIZE : {len(response.content)} bytes")
    return response.text


def extract_urls(playlist: str) -> List[str]:
    urls: List[str] = []
    for line in playlist.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("http://", "https://")):
            urls.append(line)
    return urls


def parse_source_url(url: str) -> Optional[Dict[str, Any]]:
    aliases = re.findall(r"CH_[A-Za-z0-9_-]+", url)
    if not aliases:
        return None
    path = urlparse(url).path or ""
    tail = path if path.startswith("/") else "/" + path
    route = "other"
    m = re.search(r"/(hls|region|regions)(?:/|$)", path, re.I)
    if m:
        route = m.group(1).lower()
    elif "/hls/" in path.lower():
        route = "hls"
    return {
        "alias": aliases[0],
        "all_aliases": aliases,
        "route": route,
        "tail": tail,
        "source_url": url,
    }


def extract_observations(playlist: str) -> Tuple[List[str], List[str], List[Dict]]:
    urls = extract_urls(playlist)
    tails: List[str] = []
    observations: List[Dict] = []

    for line_number, url in enumerate(urls, start=1):
        m_tail = re.search(r"(/hls/.*|/region/.*|/regions/.*)", url, re.I)
        if m_tail:
            tails.append(m_tail.group(1).split("?", 1)[0].split("#", 1)[0])
        else:
            p = urlparse(url).path
            if p:
                tails.append(p.split("?", 1)[0])

        parsed = parse_source_url(url)
        if not parsed:
            continue
        for alias in parsed["all_aliases"]:
            obs = {
                "source_index": len(observations) + 1,
                "source_line": line_number,
                "source_url": url,
                "alias": alias,
                "source_route": parsed["route"],
                "source_tail": parsed["tail"],
            }
            observations.append(obs)
            skala(
                f"[OBS {obs['source_index']:05d}] {alias} route={parsed['route']}",
                "FOUND",
            )
    return urls, tails, observations


# ============================================================
#                 CANDIDATES + RANKING
# ============================================================

def build_candidate_url(route: str, alias: str) -> str:
    route = route if route in KNOWN_ROUTES else "hls"
    return f"{NGENIX_BASE}/{route}/{alias}/variant.m3u8"


def generate_candidates(observations: List[Dict]) -> List[Dict]:
    candidates: List[Dict] = []
    seen = set()

    for obs in observations:
        alias = obs["alias"]
        observed = obs["source_route"]
        routes_order: List[str] = []
        if observed in KNOWN_ROUTES:
            routes_order.append(observed)
        for r in KNOWN_ROUTES:
            if r not in routes_order:
                routes_order.append(r)

        for route in routes_order:
            key = (alias, route)
            if key in seen:
                for c in candidates:
                    if c["alias"] == alias and c["route"] == route:
                        c["sources"].append({
                            "source_index": obs["source_index"],
                            "source_line": obs["source_line"],
                            "source_url": obs["source_url"],
                            "source_route": obs["source_route"],
                            "source_tail": obs["source_tail"],
                        })
                        break
                continue
            seen.add(key)
            candidates.append({
                "alias": alias,
                "route": route,
                "url": build_candidate_url(route, alias),
                "observed_route": observed,
                "sources": [{
                    "source_index": obs["source_index"],
                    "source_line": obs["source_line"],
                    "source_url": obs["source_url"],
                    "source_route": obs["source_route"],
                    "source_tail": obs["source_tail"],
                }],
            })
    return candidates


def rank_class(score: float) -> str:
    if score >= 90:
        return "VERY_HIGH"
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    if score >= 25:
        return "LOW"
    return "VERY_LOW"


def predict_score(cand: Dict, kb: Dict, golden: Dict, weights: Dict) -> Dict[str, Any]:
    alias, route = cand["alias"], cand["route"]
    factors: Dict[str, float] = {}

    rate = channel_route_rate(kb, alias, route)
    factors["route_probability"] = rate * weights["route_probability"]

    n = observation_count(kb, alias, route)
    factors["history_bonus"] = min(weights["history_bonus"], n * 0.6)

    sim = reference_similarity(alias, route, golden)
    factors["reference_similarity"] = sim * weights["reference_similarity"]

    if n == 0:
        factors["unknown_prior"] = 8.0

    conf = min(0.97, 0.25 + n * 0.02) if n else 0.2
    score = min(95.0, sum(factors.values()))
    return {
        "rank_score": round(score, 2),
        "rank_class": rank_class(score),
        "confidence": round(conf, 3),
        "attempts": n,
        "rank_factors": {k: round(v, 2) for k, v in factors.items()},
        "golden_match": is_golden_match(alias, route, golden),
        "predicted": True,
    }


def finalize_score(result: Dict, pred: Dict, weights: Dict) -> Dict[str, Any]:
    factors = dict(pred.get("rank_factors", {}))

    if result.get("http_status") == 200:
        factors["http_ok"] = weights["http_ok"]
    else:
        factors["http_ok"] = 0.0

    reason = result.get("reason") or ""
    if reason == "OK":
        factors["m3u8_valid"] = weights["m3u8_valid"]
        factors["media_playlist"] = weights["media_playlist"]
        factors["segments_hint"] = weights["segments_hint"]
    elif reason == "NOT_M3U8":
        factors["m3u8_valid"] = 0.0
    elif reason == "NO_STREAM":
        factors["m3u8_valid"] = weights["m3u8_valid"] * 0.4
        factors["media_playlist"] = 0.0

    score = min(100.0, sum(factors.values()))
    return {
        "rank_score": round(score, 2),
        "rank_class": rank_class(score),
        "confidence": pred.get("confidence", 0.5),
        "rank_factors": {k: round(v, 2) for k, v in factors.items()},
        "golden_match": pred.get("golden_match", False),
        "attempts": pred.get("attempts", 0),
    }


def sort_candidates_with_exploration(
    candidates: List[Dict],
    predictions: Dict[Tuple[str, str], Dict],
) -> List[Dict]:
    scored = []
    for c in candidates:
        key = (c["alias"], c["route"])
        pred = predictions.get(key, {"rank_score": 0})
        scored.append((c, pred.get("rank_score", 0)))
    scored.sort(key=lambda x: x[1], reverse=True)

    n = len(scored)
    if n == 0:
        return []
    exploit_n = max(1, int(n * (1 - EXPLORATION_RATIO)))
    exploit = [x[0] for x in scored[:exploit_n]]
    rest = [x[0] for x in scored[exploit_n:]]
    random.shuffle(rest)

    out: List[Dict] = []
    ei, ri = 0, 0
    while ei < len(exploit) or ri < len(rest):
        for _ in range(4):
            if ei < len(exploit):
                out.append(exploit[ei])
                ei += 1
        if ri < len(rest):
            out.append(rest[ri])
            ri += 1
    return out


# ============================================================
#                 CHECK
# ============================================================

def classify_error(http_status: Optional[int], text: str) -> str:
    if http_status is None:
        return "UNKNOWN"
    if http_status == 404:
        return "HTTP_404"
    if http_status == 403:
        return "HTTP_403"
    if http_status == 401:
        return "HTTP_401"
    if http_status >= 500:
        return "HTTP_5XX"
    if http_status != 200:
        return f"HTTP_{http_status}"
    if not text or not text.strip():
        return "EMPTY_BODY"
    if not text.lstrip().startswith("#EXTM3U"):
        return "NOT_M3U8"
    has_stream = "#EXT-X-STREAM-INF:" in text
    has_media = "#EXT-X-MEDIA:" in text
    has_pl = bool(re.search(r"\.m3u8|\.ts", text, re.I))
    if not (has_stream or has_media or has_pl):
        return "NO_STREAM"
    return "OK"


async def check_candidate(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    cand: Dict,
    pred: Dict,
    index: int,
    total: int,
) -> Dict:
    alias, route, url = cand["alias"], cand["route"], cand["url"]
    result = {
        "index": index,
        "alias": alias,
        "route": route,
        "url": url,
        "observed_route": cand.get("observed_route"),
        "sources": cand.get("sources", []),
        "http_status": None,
        "content_type": "",
        "content_length": 0,
        "working": False,
        "reason": "UNKNOWN",
        "user_agent": PRIMARY_UA,
        "title": alias[3:] if alias.startswith("CH_") else alias,
        "raw_m3u8": "",
        "error": None,
        "rank_score": pred.get("rank_score", 0),
        "rank_class": pred.get("rank_class", "VERY_LOW"),
        "confidence": pred.get("confidence", 0),
        "rank_factors": pred.get("rank_factors", {}),
        "predicted_score": pred.get("rank_score", 0),
        "golden_match": pred.get("golden_match", False),
        "attempts": pred.get("attempts", 0),
        "learning_feedback": None,
    }

    async with semaphore:
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
                headers={"User-Agent": PRIMARY_UA, "Accept": "*/*"},
            ) as response:
                result["http_status"] = response.status
                result["content_type"] = response.headers.get("Content-Type", "")
                text = await response.text(errors="replace")
                result["content_length"] = len(text)
                reason = classify_error(response.status, text)
                result["reason"] = reason
                if reason == "OK":
                    result["working"] = True
                    result["raw_m3u8"] = text
                    gm = " GOLDEN" if result["golden_match"] else ""
                    skala(
                        f"[{index:04d}/{total:04d}] {alias} /{route}/ → OK{gm}",
                        "FOUND",
                    )
                else:
                    result["error"] = reason
                    skala(
                        f"[{index:04d}/{total:04d}] {alias} /{route}/ → {reason}",
                        "WARN",
                    )
                return result
        except asyncio.TimeoutError:
            result["reason"] = "TIMEOUT"
            result["error"] = "TIMEOUT"
            skala(f"[{index:04d}/{total:04d}] {alias} /{route}/ → TIMEOUT", "WARN")
            return result
        except aiohttp.ClientError as e:
            result["reason"] = "REQUEST_ERROR"
            result["error"] = f"{type(e).__name__}: {e}"
            skala(f"[{index:04d}/{total:04d}] {alias} /{route}/ → REQUEST_ERROR", "ERROR")
            return result
        except Exception as e:
            result["reason"] = "ERROR"
            result["error"] = f"{type(e).__name__}: {e}"
            skala(f"[{index:04d}/{total:04d}] {alias} /{route}/ → ERROR", "ERROR")
            return result


async def scan_all(
    ordered: List[Dict],
    predictions: Dict[Tuple[str, str], Dict],
) -> List[Dict]:
    total = len(ordered)
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT)
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = []
        for i, cand in enumerate(ordered):
            key = (cand["alias"], cand["route"])
            pred = predictions.get(
                key,
                {
                    "rank_score": 0,
                    "rank_class": "VERY_LOW",
                    "confidence": 0,
                    "rank_factors": {},
                    "golden_match": False,
                    "attempts": 0,
                },
            )
            tasks.append(check_candidate(session, semaphore, cand, pred, i + 1, total))
        return list(await asyncio.gather(*tasks))


# ============================================================
#                 SELECT + METRICS
# ============================================================

def select_best_working(results: List[Dict]) -> List[Dict]:
    by_alias: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        if r.get("working"):
            by_alias[r["alias"]].append(r)

    selected: List[Dict] = []
    for alias, group in by_alias.items():
        group_sorted = sorted(
            group,
            key=lambda x: (
                x.get("rank_score", 0),
                1 if x.get("golden_match") else 0,
                {"hls": 3, "regions": 2, "region": 1}.get(x.get("route"), 0),
            ),
            reverse=True,
        )
        best = dict(group_sorted[0])
        best["selected_for_output"] = True
        best["alternatives_working"] = len(group) - 1
        selected.append(best)

    selected.sort(key=lambda x: x["alias"])
    return selected


def compute_metrics(
    observations: List[Dict],
    results: List[Dict],
    selected: List[Dict],
    golden: Dict,
) -> Dict[str, Any]:
    unique_obs = {o["alias"] for o in observations}
    working_candidates = [r for r in results if r.get("working")]
    unique_working = {r["alias"] for r in working_candidates}
    golden_aliases = set(golden.get("alias_set", []))

    golden_found_working = unique_working & golden_aliases
    non_golden_working = unique_working - golden_aliases
    checked = len(results)

    return {
        "WORKING_COVERAGE": len(unique_working),
        "WORKING_RATE": (len(working_candidates) / checked) if checked else 0.0,
        "CHANNEL_COVERAGE": (
            len(unique_working) / len(unique_obs)
        ) if unique_obs else 0.0,
        "GOLDEN_COVERAGE": (
            len(golden_found_working) / len(golden_aliases)
        ) if golden_aliases else 0.0,
        "UNIQUE_OBSERVED_CHANNELS": len(unique_obs),
        "WORKING_CHANNELS": len(unique_working),
        "FAILED_CHANNELS": len(unique_obs - unique_working),
        "WORKING_CANDIDATES": len(working_candidates),
        "CHECKED_CANDIDATES": checked,
        "GOLDEN_MATCHES": sum(1 for r in working_candidates if r.get("golden_match")),
        "NON_GOLDEN_WORKING_CHANNELS": len(non_golden_working),
        "UNRESOLVED_CHANNELS": len(unique_obs - unique_working),
        "OUTPUT_M3U_ENTRIES": len(selected),
    }


def attach_feedback(results: List[Dict]) -> None:
    for r in results:
        pred = r.get("predicted_score", 0)
        ok = bool(r.get("working"))
        if ok and pred >= 50:
            r["learning_feedback"] = "POSITIVE"
        elif not ok and pred < 50:
            r["learning_feedback"] = "POSITIVE"
        elif ok and pred < 50:
            r["learning_feedback"] = "UNDERESTIMATE"
        else:
            r["learning_feedback"] = "NEGATIVE"


# ============================================================
#                    WRITERS
# ============================================================

def save_tails(urls: List[str], tails: List[str]) -> None:
    with open(OUTPUT_TAILS_TXT, "w", encoding="utf-8") as f:
        for t in tails:
            f.write(t + "\n")
    data = {
        "scanner": "dmitrytv_to_ngenix",
        "version": SCANNER_VERSION,
        "timestamp": now_utc(),
        "source": PLAYLIST_URL,
        "statistics": {"urls_found": len(urls), "tails_found": len(tails)},
        "tails": tails,
    }
    with open(OUTPUT_TAILS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    skala(f"TAILS: {OUTPUT_TAILS_TXT} / {OUTPUT_TAILS_JSON}")


def write_candidates(candidates: List[Dict], predictions: Dict) -> None:
    enriched = []
    for c in candidates:
        key = (c["alias"], c["route"])
        enriched.append({**c, "prediction": predictions.get(key, {})})
    data = {
        "scanner": "dmitrytv_to_ngenix",
        "version": SCANNER_VERSION,
        "timestamp": now_utc(),
        "count": len(enriched),
        "candidates": enriched,
    }
    with open(OUTPUT_CANDIDATES, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    skala(f"CANDIDATES: {OUTPUT_CANDIDATES}")


def write_diagnostics(results: List[Dict], metrics: Dict) -> None:
    by_reason: Dict[str, int] = defaultdict(int)
    by_route: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rows = []
    for r in results:
        reason = r.get("reason") or "UNKNOWN"
        by_reason[reason] += 1
        by_route[r["route"]][reason] += 1
        rows.append({
            "alias": r["alias"],
            "route": r["route"],
            "url": r["url"],
            "reason": reason,
            "http_status": r.get("http_status"),
            "working": r.get("working"),
            "rank_score": r.get("rank_score"),
            "rank_class": r.get("rank_class"),
            "confidence": r.get("confidence"),
            "attempts": r.get("attempts"),
            "predicted_score": r.get("predicted_score"),
            "golden_match": r.get("golden_match", False),
            "actual": "SUCCESS" if r.get("working") else "FAIL",
            "learning_feedback": r.get("learning_feedback"),
            "rank_factors": r.get("rank_factors"),
            "selected_for_output": r.get("selected_for_output", False),
        })
    data = {
        "scanner": "dmitrytv_to_ngenix",
        "version": SCANNER_VERSION,
        "timestamp": now_utc(),
        "metrics": metrics,
        "summary_by_reason": dict(by_reason),
        "summary_by_route": {k: dict(v) for k, v in by_route.items()},
        "results": rows,
    }
    with open(OUTPUT_DIAGNOSTICS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    skala(f"DIAGNOSTICS: {OUTPUT_DIAGNOSTICS}")


def write_m3u(selected: List[Dict]) -> int:
    with open(OUTPUT_M3U, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for r in selected:
            if not r.get("working"):
                continue
            alias = r["alias"]
            title = r.get("title") or (alias[3:] if alias.startswith("CH_") else alias)
            route = r["route"]
            f.write(
                f'#EXTINF:-1 tvg-id="{alias}" tvg-name="{title}" '
                f'tvg-user-agent="{PRIMARY_UA}" '
                f'group-title="Ngenix-{route}",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-user-agent={PRIMARY_UA}\n")
            f.write(r["url"] + "\n")
    return len(selected)


def write_learning_files(kb: Dict, metrics: Dict) -> None:
    learning = knowledge_to_learning(kb)
    learning["last_metrics"] = metrics
    with open(OUTPUT_LEARNING, "w", encoding="utf-8") as f:
        json.dump(learning, f, ensure_ascii=False, indent=2)
    skala(f"LEARNING (aggregate): {OUTPUT_LEARNING}")

    lines = [
        f"LEARNING SUMMARY v{SCANNER_VERSION}",
        f"TIME: {now_local()}",
        f"REVISIONS APPLIED: {kb.get('revisions_applied')}",
        "",
        "=== MAX WORKING COVERAGE ===",
        f"WORKING_COVERAGE : {metrics['WORKING_COVERAGE']}",
        f"CHANNEL_COVERAGE : {metrics['CHANNEL_COVERAGE']:.1%}",
        f"WORKING_RATE     : {metrics['WORKING_RATE']:.1%}",
        f"GOLDEN_COVERAGE  : {metrics['GOLDEN_COVERAGE']:.1%}",
        f"OUTPUT_M3U       : {metrics['OUTPUT_M3U_ENTRIES']}",
        f"NON_GOLDEN_WORKING : {metrics['NON_GOLDEN_WORKING_CHANNELS']}",
        f"UNRESOLVED       : {metrics['UNRESOLVED_CHANNELS']}",
        "",
        "ROUTE STATS (from full immutable history):",
    ]
    for route, st in kb.get("routes", {}).items():
        total = st["ok"] + st["fail"]
        rate = (st["ok"] / total) if total else 0.0
        lines.append(f"  /{route}/  ok={st['ok']} fail={st['fail']} rate={rate:.1%}")
    lines.append("")
    lines.append("HISTORY: playlist_ngenix_data.json + _1.._N (never deleted)")
    lines.append("NOTE: GOLDEN = supervision only. M3U = best working per CH_*.")

    with open(OUTPUT_LEARN_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    skala(f"LEARN REPORT: {OUTPUT_LEARN_REPORT}")


def append_final_report(metrics: Dict, selected_count: int, data_path: str, rev: int) -> None:
    with open(OUTPUT_SKALA, "a", encoding="utf-8") as f:
        f.write("\n============================================================\n")
        f.write(f"                 FINAL SKALA REPORT v{SCANNER_VERSION}\n")
        f.write("============================================================\n")
        f.write(f"TIME: {now_local()}\n")
        f.write(f"DATA REVISION: {data_path} (rev={rev})\n")
        f.write(f"SOURCE: {PLAYLIST_URL}\n")
        f.write(f"GOLDEN: {GOLDEN_URL} (supervision only)\n\n")
        f.write("MAX WORKING COVERAGE\n")
        for k in (
            "UNIQUE_OBSERVED_CHANNELS",
            "WORKING_CHANNELS",
            "FAILED_CHANNELS",
            "WORKING_CANDIDATES",
            "CHECKED_CANDIDATES",
            "GOLDEN_MATCHES",
            "NON_GOLDEN_WORKING_CHANNELS",
            "UNRESOLVED_CHANNELS",
        ):
            f.write(f"  {k:<28} {metrics[k]}\n")
        f.write(f"  OUTPUT_M3U_ENTRIES           {selected_count}\n")
        f.write(f"  CHANNEL_COVERAGE             {metrics['CHANNEL_COVERAGE']:.1%}\n")
        f.write(f"  GOLDEN_COVERAGE              {metrics['GOLDEN_COVERAGE']:.1%}\n")
        f.write("\nIMMUTABLE HISTORY: base + _1.._N never overwritten\n")
        f.write("RULE: M3U = best WORKING per CH_* (not GOLDEN copy)\n")
        f.write("============================================================\n")


# ============================================================
#                           MAIN
# ============================================================

def main() -> None:
    resolve_output_paths()

    for h in list(logging.root.handlers):
        if isinstance(h, logging.FileHandler):
            logging.root.removeHandler(h)
            h.close()
    logging.root.addHandler(logging.FileHandler(LOG_FILE, encoding="utf-8"))

    with open(OUTPUT_SKALA, "w", encoding="utf-8") as f:
        f.write(f"SKALA NGENIX SCAN v{SCANNER_VERSION}\n")
        f.write(f"START: {now_local()}\n\n")

    started = datetime.now(timezone.utc)

    try:
        skala(f"OUTPUT M3U   : {OUTPUT_M3U}")
        skala(f"OUTPUT SKALA : {OUTPUT_SKALA}")
        skala(f"OUTPUT LOG   : {LOG_FILE}")

        kb = load_current_knowledge()
        learning = knowledge_to_learning(kb)
        weights = learning["weights"]

        chain = list_data_revisions()
        parent_name = chain[-1][1] if chain else None
        rev_num = next_revision_number()
        skala(f"NEXT DATA REVISION: {rev_num} (parent={parent_name})")

        golden = load_golden()

        playlist = download_playlist()
        urls, tails, observations = extract_observations(playlist)
        skala(f"URLS={len(urls)} TAILS={len(tails)} OBS={len(observations)}")
        if not observations:
            skala("Нет наблюдений — выход", "ERROR")
            return

        save_tails(urls, tails)

        candidates = generate_candidates(observations)
        skala(f"CANDIDATES: {len(candidates)}")

        predictions: Dict[Tuple[str, str], Dict] = {}
        for c in candidates:
            predictions[(c["alias"], c["route"])] = predict_score(
                c, kb, golden, weights
            )

        write_candidates(candidates, predictions)

        ordered = sort_candidates_with_exploration(candidates, predictions)
        skala("START NGENIX SCAN (ranked + exploration)")
        results = asyncio.run(scan_all(ordered, predictions))

        for r in results:
            key = (r["alias"], r["route"])
            pred = predictions.get(key, {})
            final = finalize_score(r, pred, weights)
            r.update(final)

        attach_feedback(results)

        selected = select_best_working(results)
        metrics = compute_metrics(observations, results, selected, golden)

        selected_keys = {(s["alias"], s["route"]) for s in selected}
        for r in results:
            r["selected_for_output"] = (r["alias"], r["route"]) in selected_keys

        apply_revision_to_knowledge(
            kb,
            {
                "revision": rev_num,
                "results": results,
            },
        )

        data_path = write_data_revision(
            rev_num=rev_num,
            parent_name=parent_name,
            results=results,
            selected=selected,
            metrics=metrics,
            observations=observations,
            candidates=candidates,
            golden=golden,
        )

        skala("============================================================")
        skala(
            f"WORKING COVERAGE: {metrics['WORKING_COVERAGE']} unique CH_* "
            f"({metrics['CHANNEL_COVERAGE']:.0%} of observed)",
            "FOUND",
        )
        skala(f"NON-GOLDEN working: {metrics['NON_GOLDEN_WORKING_CHANNELS']}")

        write_diagnostics(results, metrics)
        m3u_count = write_m3u(selected)
        skala(f"M3U READY: {OUTPUT_M3U} ({m3u_count})", "FOUND")

        write_learning_files(kb, metrics)
        append_final_report(metrics, m3u_count, data_path, rev_num)

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        skala("============================================================")
        skala("FINAL RESULT")
        skala(f"WORKING CHANNELS : {metrics['WORKING_CHANNELS']}", "FOUND")
        skala(f"DATA REVISION    : {data_path}")
        skala(f"M3U ENTRIES      : {m3u_count}")
        skala(f"DURATION         : {duration:.3f}s")
        skala("COMPLETE")

    except Exception as e:
        skala(f"FATAL ERROR : {type(e).__name__}: {e}", "ERROR")
        raise


if __name__ == "__main__":
    main()