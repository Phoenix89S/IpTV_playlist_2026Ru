#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import io
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests


SYSTEM_NAME = "СИСТЕМА ЭВС (ЭВМ) версия — последняя сборка_Iptv_AI_edition."
SYSTEM_INFO = "SKALA IPTV 8.2026 / IPTV _zoye / Version 6.3 (Build 9600)"
DEFAULT_OUTPUT = "ngenix_run_full_telegraph.txt"
TIMEOUT = 60

SEPARATOR_TOP = "#************************************************************"
SEPARATOR_MAIN = "#=============================================="
SEPARATOR_BLOCK = "#================================================="


@dataclass
class StepLog:
    number: int
    name: str
    job_name: str
    text: str
    source_file: str


def parse_run_url(url: str) -> tuple[str, str, int]:
    url = url.strip()
    parsed = urlparse(url)

    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError(
            "Ожидается ссылка вида "
            "https://github.com/OWNER/REPO/actions/runs/RUN_ID"
        )

    parts = [p for p in parsed.path.split("/") if p]

    try:
        idx = parts.index("runs")
    except ValueError:
        raise ValueError("В ссылке не найден /actions/runs/<RUN_ID>")

    if idx < 2 or idx + 1 >= len(parts):
        raise ValueError("Не удалось определить OWNER, REPO и RUN_ID.")

    owner = parts[idx - 2]
    repo = parts[idx - 1]

    try:
        run_id = int(parts[idx + 1])
    except ValueError:
        raise ValueError(f"Некорректный RUN_ID: {parts[idx + 1]}")

    return owner, repo, run_id


def github_session() -> requests.Session:
    session = requests.Session()

    session.headers.update({
        "Accept": "application/vnd.github+json",
        "User-Agent": "SKALA-IPTV-ZOYE-LogExporter/6.3",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    token = os.environ.get("GITHUB_TOKEN")

    if token:
        session.headers["Authorization"] = f"Bearer {token}"

    return session


def api_get(
    session: requests.Session,
    url: str,
    *,
    stream: bool = False,
) -> requests.Response:

    response = session.get(
        url,
        timeout=TIMEOUT,
        stream=stream,
        allow_redirects=True,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "GitHub вернул 401 Unauthorized. "
            "Для этого репозитория нужен GITHUB_TOKEN."
        )

    if response.status_code == 403:
        raise RuntimeError(
            "GitHub вернул 403 Forbidden. "
            "Проверь GITHUB_TOKEN и права доступа к Actions."
        )

    if response.status_code == 404:
        raise RuntimeError(
            "GitHub вернул 404 Not Found. "
            "Проверь ссылку на Run и доступ к репозиторию."
        )

    response.raise_for_status()
    return response


def get_run_info(
    session: requests.Session,
    owner: str,
    repo: str,
    run_id: int,
) -> dict:

    url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/actions/runs/{run_id}"
    )

    return api_get(session, url).json()


def download_run_logs(
    session: requests.Session,
    owner: str,
    repo: str,
    run_id: int,
) -> bytes:

    url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/actions/runs/{run_id}/logs"
    )

    response = api_get(
        session,
        url,
        stream=True,
    )

    data = bytearray()

    for chunk in response.iter_content(
        chunk_size=1024 * 1024
    ):
        if chunk:
            data.extend(chunk)

    return bytes(data)


def decode_log(data: bytes) -> str:

    for encoding in (
        "utf-8",
        "utf-8-sig",
        "cp1251",
        "latin-1",
    ):
        try:
            return data.decode(
                encoding,
                errors="replace",
            )
        except Exception:
            continue

    return data.decode(
        "utf-8",
        errors="replace",
    )


def extract_logs(
    zip_bytes: bytes,
) -> list[tuple[str, str]]:

    result = []

    with zipfile.ZipFile(
        io.BytesIO(zip_bytes),
        "r",
    ) as archive:

        for member in archive.infolist():

            if member.is_dir():
                continue

            raw = archive.read(member)

            result.append(
                (
                    member.filename,
                    decode_log(raw),
                )
            )

    result.sort(
        key=lambda x: x[0].lower()
    )

    return result


TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?Z\s?"
)

GROUP_START_RE = re.compile(
    r"##\[group\]\s*(.*)"
)

GROUP_END_RE = re.compile(
    r"##\[endgroup\]"
)


def normalize_log(text: str) -> str:

    output = []

    for raw_line in text.splitlines():

        line = raw_line.rstrip()

        line = TIMESTAMP_RE.sub(
            "",
            line,
        )

        match = GROUP_START_RE.search(line)

        if match:
            output.append(
                f"[НАЧАЛО БЛОКА] {match.group(1).strip()}"
            )
            continue

        if GROUP_END_RE.search(line):
            output.append(
                "[КОНЕЦ БЛОКА]"
            )
            continue

        output.append(line)

    return "\n".join(output).strip()


def job_name_from_path(path: str) -> str:

    parts = Path(path).parts

    if len(parts) >= 2:
        return parts[0]

    return "Неизвестный job"


def build_step_logs(
    extracted: Iterable[tuple[str, str]]
) -> list[StepLog]:

    result = []
    number = 1

    for filename, text in extracted:

        normalized = normalize_log(text)

        if not normalized:
            continue

        result.append(
            StepLog(
                number=number,
                name=Path(filename).name,
                job_name=job_name_from_path(filename),
                text=normalized,
                source_file=filename,
            )
        )

        number += 1

    return result


def score_main_log(step: StepLog) -> int:

    text = (
        step.name
        + "\n"
        + step.source_file
        + "\n"
        + step.text[:30000]
    ).lower()

    keywords = {
        "главный": 100,
        "main": 90,
        "constellation": 90,
        "ngenix": 80,
        "cdn": 70,
        "skala": 70,
        "node": 60,
        "узел": 60,
        "online streams": 50,
        "online nodes": 50,
        "alias map": 40,
        "service map": 40,
        "summary": 30,
    }

    return sum(
        value
        for keyword, value in keywords.items()
        if keyword in text
    )


def find_main_logs(
    steps: list[StepLog],
) -> list[StepLog]:

    if not steps:
        return []

    scored = [
        (score_main_log(step), step)
        for step in steps
    ]

    scored.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    best_score = scored[0][0]

    if best_score <= 0:
        return []

    threshold = max(
        50,
        int(best_score * 0.70),
    )

    return [
        step
        for score, step in scored
        if score >= threshold
    ]


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def make_header(
    run_info: dict,
    export_number: int,
) -> str:

    return "\n".join([
        SEPARATOR_TOP,
        f"# {SYSTEM_NAME}",
        f"# Выгрузка логов запуска {export_number}",
        f"# {SYSTEM_INFO}",
        SEPARATOR_TOP,
        "",
        f"# GitHub Run ID: {run_info.get('id', 'unknown')}",
        f"# Название запуска: {run_info.get('name', 'unknown')}",
        f"# Статус: {run_info.get('status', 'unknown')}",
        f"# Результат: {run_info.get('conclusion', 'unknown')}",
        f"# Ветка: {run_info.get('head_branch', 'unknown')}",
        f"# SHA: {run_info.get('head_sha', 'unknown')}",
        f"# Создан: {run_info.get('created_at', 'unknown')}",
        f"# Начат: {run_info.get('run_started_at', 'unknown')}",
        f"# Экспортирован UTC: {utc_now()}",
        "",
    ])


def render_main_log(
    steps: list[StepLog],
) -> str:

    main_logs = find_main_logs(steps)

    if not main_logs:
        return "\n".join([
            SEPARATOR_MAIN,
            "#**********************************************************",
            "#",
            "# главный лог проверки узла — не удалось автоматически определить",
            "#",
            "#**********************************************************",
            SEPARATOR_MAIN,
            "",
        ])

    blocks = []

    for step in main_logs:

        blocks.append(
            "\n".join([
                SEPARATOR_MAIN,
                "#**********************************************************",
                "#",
                f"# главный лог проверки узла — {step.name}",
                "#",
                "#**********************************************************",
                SEPARATOR_MAIN,
                "",
                step.text,
                "",
            ])
        )

    return "\n".join(blocks)


def render_step(
    step: StepLog,
) -> str:

    return "\n".join([
        SEPARATOR_BLOCK,
        f"# ШАГ {step.number} — {step.name}",
        f"# JOB: {step.job_name}",
        SEPARATOR_BLOCK,
        "",
        step.text,
        "",
    ])


def render_document(
    run_info: dict,
    steps: list[StepLog],
    export_number: int,
) -> str:

    parts = []

    parts.append(
        make_header(
            run_info,
            export_number,
        )
    )

    parts.append(
        render_main_log(steps)
    )

    parts.append(
        "\n".join([
            "#**********************************************************",
            "#==============================================",
            "# ПОЛНЫЙ СОСТАВ ОСТАЛЬНЫХ ЛОГОВ ЗАПУСКА",
            "#==============================================",
            "#**********************************************************",
            "",
        ])
    )

    for step in steps:
        parts.append(
            render_step(step)
        )

    parts.append(
        "\n".join([
            "",
            "#**********************************************",
            "# конец телетайпа",
            "#*************************************************",
            "",
        ])
    )

    return "\n".join(parts)


def save_text(
    path: Path,
    text: str,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        text,
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Полная выгрузка всех логов "
            "GitHub Actions Run в SKALA TXT."
        )
    )

    parser.add_argument(
        "run_url",
        help=(
            "URL GitHub Actions Run, например: "
            "https://github.com/OWNER/REPO/actions/runs/123"
        ),
    )

    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT,
        help="Имя/путь итогового TXT.",
    )

    parser.add_argument(
        "--export-number",
        type=int,
        default=1,
        help="Номер выгрузки.",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("SKALA IPTV 8.2026")
    print("IPTV _zoye")
    print("Version 6.3 (Build 9600)")
    print("Полная выгрузка GitHub Actions Run")
    print("=" * 60)

    try:

        owner, repo, run_id = parse_run_url(
            args.run_url
        )

        print(
            f"[INFO] Репозиторий: {owner}/{repo}"
        )
        print(
            f"[INFO] Run ID: {run_id}"
        )

        session = github_session()

        print(
            "[INFO] Получение информации о запуске..."
        )

        run_info = get_run_info(
            session,
            owner,
            repo,
            run_id,
        )

        print(
            f"[INFO] Название: "
            f"{run_info.get('name')}"
        )

        print(
            f"[INFO] Статус: "
            f"{run_info.get('status')} / "
            f"{run_info.get('conclusion')}"
        )

        print(
            "[INFO] Скачивание полного архива логов..."
        )

        zip_bytes = download_run_logs(
            session,
            owner,
            repo,
            run_id,
        )

        print(
            f"[INFO] Получено байт: "
            f"{len(zip_bytes):,}"
        )

        print(
            "[INFO] Распаковка ВСЕХ логов..."
        )

        extracted = extract_logs(
            zip_bytes
        )

        print(
            f"[INFO] Файлов в архиве: "
            f"{len(extracted)}"
        )

        steps = build_step_logs(
            extracted
        )

        print(
            f"[INFO] Логов включено в TXT: "
            f"{len(steps)}"
        )

        main_logs = find_main_logs(steps)

        if main_logs:
            print(
                "[INFO] Главный лог:"
            )

            for step in main_logs:
                print(
                    f"       {step.name}"
                )

        else:
            print(
                "[WARN] Главный лог автоматически "
                "не определён."
            )

        print(
            "[INFO] Формирование телетайпа SKALA..."
        )

        document = render_document(
            run_info,
            steps,
            args.export_number,
        )

        output = Path(args.output)

        save_text(
            output,
            document,
        )

        print("=" * 60)
        print("[OK] ВЫГРУЗКА ЗАВЕРШЕНА")
        print(
            f"[OK] Файл: {output.resolve()}"
        )
        print(
            f"[OK] Размер: "
            f"{output.stat().st_size:,} байт"
        )
        print("=" * 60)

        return 0

    except KeyboardInterrupt:
        print(
            "\n[STOP] Операция прервана."
        )
        return 130

    except Exception as exc:
        print(
            f"\n[ERROR] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())