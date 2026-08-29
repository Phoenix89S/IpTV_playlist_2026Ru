import json
import requests
from pathlib import Path

# === Имя базового файла (меняется только здесь) ===
BASE_NAME = "zabava_tiles"

JSON_INPUT = "_scanner_zaba_1788021849270.txt"
BASE_CDN = "https://zabava-htlive.cdn.ngenix.net"

OUT_JSON = f"{BASE_NAME}.json"
OUT_TXT = f"{BASE_NAME}.txt"
OUT_M3U = f"{BASE_NAME}.m3u"


def load_tails():
    with open(JSON_INPUT, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["tails"]


def check_variant(channel_id: str, tails_set: set):
    url = f"{BASE_CDN}/hls/{channel_id}/variant.m3u8"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return {"channel": channel_id, "status": "variant_fail", "http": r.status_code, "matches": []}

    matches = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if not line.startswith("/"):
            line = "/hls/" + line

        if line in tails_set:
            matches.append(line)

    return {
        "channel": channel_id,
        "status": "ok" if matches else "no_matches",
        "http": 200,
        "matches": matches,
    }


def check_generated_urls(tails):
    results = []
    for tail in tails:
        full_url = BASE_CDN + tail
        try:
            r = requests.head(full_url, timeout=5)
            status = r.status_code
        except Exception:
            status = None

        results.append({
            "tail": tail,
            "url": full_url,
            "http": status,
            "ok": status == 200,
        })
    return results


def build_playlist_json(http_results):
    channels = []
    for item in http_results:
        if item["ok"]:
            channels.append({
                "name": item["tail"].split("/")[-1],
                "url": item["url"],
            })
    return {"channels": channels}


def build_m3u(http_results):
    lines = ["#EXTM3U"]
    for item in http_results:
        if item["ok"]:
            name = item["tail"].split("/")[-1]
            lines.append(f"#EXTINF:-1,{name}")
            lines.append(item["url"])
    return "\n".join(lines)


def write_skala_report(variant_checks, http_results):
    lines = []
    lines.append("SKALA REPORT: ZABAVA CDN\n")
    lines.append("=== VARIANT CHECKS ===\n")
    for vc in variant_checks:
        lines.append(f"{vc['channel']}: status={vc['status']} http={vc['http']} matches={len(vc['matches'])}")
        for m in vc["matches"]:
            lines.append(f"  MATCH: {m}")

    lines.append("\n=== HTTP CHECKS ===\n")
    ok_count = sum(1 for r in http_results if r["ok"])
    total = len(http_results)
    lines.append(f"OK: {ok_count}/{total}")

    for r in http_results:
        lines.append(f"{r['tail']} -> {r['http']} {'OK' if r['ok'] else 'FAIL'}")

    return "\n".join(lines)


def main():
    tails = load_tails()
    tails_set = set(tails)

    channels_to_check = ["CH_TVC", "CH_1TV", "CH_RUSSIA1"]
    variant_checks = [check_variant(ch, tails_set) for ch in channels_to_check]

    http_results = check_generated_urls(tails)

    # JSON
    playlist_json = build_playlist_json(http_results)
    Path(OUT_JSON).write_text(json.dumps(playlist_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # TXT (SKALA)
    skala_text = write_skala_report(variant_checks, http_results)
    Path(OUT_TXT).write_text(skala_text, encoding="utf-8")

    # M3U
    m3u_text = build_m3u(http_results)
    Path(OUT_M3U).write_text(m3u_text, encoding="utf-8")

    print(f"Готово: {OUT_JSON}, {OUT_TXT}, {OUT_M3U} сформированы.")


if __name__ == "__main__":
    main()