import json
import requests
from pathlib import Path

JSON_INPUT = "_scanner_zaba_1788021849270.txt"
BASE_CDN = "https://zabava-htlive.cdn.ngenix.net"

OUT_PLAYLIST_JSON = "zabava_playlist.json"
OUT_SKALA_TXT = "zabava_skala_report.txt"


def load_tails():
    with open(JSON_INPUT, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["tails"]


def check_variant(channel_id: str, tails_set: set):
    """
    channel_id: например 'CH_TVC'
    """
    url = f"{BASE_CDN}/hls/{channel_id}/variant.m3u8"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return {"channel": channel_id, "status": "variant_fail", "http": r.status_code, "matches": []}

    matches = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Приводим к формату хвоста
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
    """
    Проверка всех хвостов на HTTP 200.
    """
    results = []
    for tail in tails:
        full_url = BASE_CDN + tail
        try:
            r = requests.head(full_url, timeout=5)
            status = r.status_code
        except Exception as e:
            status = None

        results.append({
            "tail": tail,
            "url": full_url,
            "http": status,
            "ok": status == 200,
        })
    return results


def build_playlist_json(http_results):
    """
    Собираем JSON-плейлист только из хвостов с HTTP 200.
    """
    channels = []
    for item in http_results:
        if item["ok"]:
            channels.append({
                "name": item["tail"].split("/")[-1],  # грубое имя по хвосту
                "url": item["url"],
            })
    return {"channels": channels}


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

    Path(OUT_SKALA_TXT).write_text("\n".join(lines), encoding="utf-8")


def main():
    tails = load_tails()
    tails_set = set(tails)

    # 1. Проверка variant.m3u8 для выбранных каналов (можно список расширить)
    channels_to_check = ["CH_TVC", "CH_1TV", "CH_RUSSIA1"]
    variant_checks = [check_variant(ch, tails_set) for ch in channels_to_check]

    # 2. Проверка всех сгенерированных хвостов на HTTP 200
    http_results = check_generated_urls(tails)

    # 3. Генерация JSON-плейлиста
    playlist = build_playlist_json(http_results)
    Path(OUT_PLAYLIST_JSON).write_text(json.dumps(playlist, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. SKALA-отчёт
    write_skala_report(variant_checks, http_results)

    print("Готово: JSON плейлист и SKALA отчёт сформированы.")


if __name__ == "__main__":
    main()