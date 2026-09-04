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


SYSTEM_NAME = (
    "СИСТЕМА ЭВС (ЭВМ) версия — последняя сборка_Iptv_AI_edition."
)

SYSTEM_INFO = (
    "SKALA IPTV 8.2026 / IPTV _zoye / Version 6.3 (Build 9600)"
)

DEFAULT_OUTPUT = "SKALA_RUN_33892073670.txt"

TIMEOUT = 60

SEPARATOR_TOP = "#************************************************************"
SEPARATOR_MAIN = "#=============================================="
SEPARATOR_BLOCK = "#================================================="
SEPARATOR_END = "#*************************************************"


@dataclass
class StepLog:
    number: int
    name: str
    job_name: str
    text: str
    source_file: str


def parse_target_url(url: str) -> tuple[str, str, int]:
    """
    Поддерживаются оба варианта:

    GitHub Run:
    https://github.com/OWNER/REPO/actions/runs/RUN_ID

    GitHub API:
    https://api.github.com/repos/OWNER/REPO/actions/runs/RUN_ID
    """

    url = url.strip()

    parsed = urlparse(url)

    host = parsed.netloc.lower()

    parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    # ---------------------------------------------------------
    # Обычная ссылка GitHub Actions Run
    # ---------------------------------------------------------

    if host in {"github.com", "www.github.com"}:

        if (
            len(parts) >= 5
            and parts[2] == "actions"
            and parts[3] == "runs"
        ):
            owner = parts[0]
            repo = parts[1]

            try:
                run_id = int(parts[4])
            except ValueError:
                raise ValueError(
                    f"Некорректный RUN_ID: {parts[4]}"
                )

            return owner, repo, run_id

        raise ValueError(
            "Некорректная ссылка GitHub Actions Run.\n"
            "Ожидается:\n"
            "https://github.com/OWNER/REPO/actions/runs/RUN_ID"
        )

    # ---------------------------------------------------------
    # GitHub API URL
    # ---------------------------------------------------------

    if host == "api.github.com":

        if (
            len(parts) >= 6
            and parts[0] == "repos"
            and parts[3] == "actions"
            and parts[4] == "runs"
        ):
            owner = parts[1]
            repo = parts[2]

            try:
                run_id = int(parts[5])
            except ValueError:
                raise ValueError(
                    f"Некорректный RUN_ID: {parts[5]}"
                )

            return owner, repo, run_id

        raise ValueError(
            "Некорректная ссылка GitHub API Run.\n"
            "Ожидается:\n"
            "https://api.github.com/repos/OWNER/REPO/actions/runs/RUN_ID"
        )

    raise ValueError(
        "Неизвестный GitHub URL."
    )


def github_session() -> requests.Session:

    session = requests.Session()

    session.headers.update({
        "Accept": "application/vnd.github+json",
        "User-Agent": (
            "SKALA-IPTV-ZOYE-LogExporter/6.3"
        ),
        "X-GitHub-Api-Version": "2022-11-28",
    })

    token = (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
    )

    if token:
        session.headers[
            "Authorization"
        ] = f"Bearer {token}"

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
            "GITHUB_TOKEN недействителен или отсутствует."
        )

    if response.status_code == 403:
        raise RuntimeError(
            "GitHub вернул 403 Forbidden. "
            "Недостаточно прав для чтения Actions."
        )

    if response.status_code == 404:
        raise RuntimeError(
            "GitHub вернул 404 Not Found.\n"
            f"URL API: {url}\n"
            "Проверь OWNER/REPO/RUN_ID и права GITHUB_TOKEN."
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
        "https://api.github.com/repos/"
        f"{owner}/{repo}/actions/runs/{run_id}"
    )

    return api_get(
        session,
        url,
    ).json()


def download_run_logs(
    session: requests.Session,
    owner: str,
    repo: str,
    run_id: int,
) -> bytes:

    url = (
        "https://api.github.com/repos/"
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

    if not data:
        raise RuntimeError(
            "GitHub вернул пустой архив логов."
        )

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
                encoding
            )
        except UnicodeDecodeError:
            continue

    return data.decode(
        "utf-8",
        errors="replace",
    )


def extract_logs(
    zip_bytes: bytes,
) -> list[tuple[str, str]]:

    result = []

    try:
        archive = zipfile.ZipFile(
            io.BytesIO(zip_bytes),
            "r",
        )
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            "GitHub не вернул корректный ZIP-архив логов."
        ) from exc

    with archive:

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
        key=lambda item: item[0].lower()
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

        match = GROUP_START_RE.search(
            line
        )

        if match:
            output.append(
                f"[НАЧАЛО БЛОКА] "
                f"{match.group(1).strip()}"
            )
            continue

        if GROUP_END_RE.search(line):
            output.append(
                "[КОНЕЦ БЛОКА]"
            )
            continue

        output.append(line)

    return "\n".join(output).strip()


def job_name_from_path(
    path: str,
) -> str:

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

        normalized = normalize_log(
            text
        )

        if not normalized:
            continue

        result.append(
            StepLog(
                number=number,
                name=Path(
                    filename
                ).name,
                job_name=job_name_from_path(
                    filename
                ),
                text=normalized,
                source_file=filename,
            )
        )

        number += 1

    return result


def score_main_log(
    step: StepLog,
) -> int:

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
        (
            score_main_log(step),
            step,
        )
        for step in steps
    ]

    scored.sort(
        key=lambda item: item[0],
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
        f"# GitHub Run ID: "
        f"{run_info.get('id', 'unknown')}",
        f"# Название запуска: "
        f"{run_info.get('name', 'unknown')}",
        f"# Статус: "
        f"{run_info.get('status', 'unknown')}",
        f"# Результат: "
        f"{run_info.get('conclusion', 'unknown')}",
        f"# Ветка: "
        f"{run_info.get('head_branch', 'unknown')}",
        f"# SHA: "
        f"{run_info.get('head_sha', 'unknown')}",
        f"# Создан: "
        f"{run_info.get('created_at', 'unknown')}",
        f"# Начат: "
        f"{run_info.get('run_started_at', 'unknown')}",
        f"# Экспортирован UTC: "
        f"{utc_now()}",
        "",
    ])


def render_main_log(
    steps: list[StepLog],
) -> str:

    main_logs = find_main_logs(
        steps
    )

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
                (
                    "# главный лог проверки узла — "
                    f"{step.name}"
                ),
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
        (
            f"# ШАГ {step.number} — "
            f"{step.name}"
        ),
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
        render_main_log(
            steps
        )
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
            SEPARATOR_END,
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
            "GitHub Actions Run URL или "
            "GitHub API Run URL."
        ),
    )

    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT,
        help="Итоговый TXT.",
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

        owner, repo, run_id = parse_target_url(
            args.run_url
        )

        print(
            f"[INFO] Репозиторий: "
            f"{owner}/{repo}"
        )

        print(
            f"[INFO] Run ID: {run_id}"
        )

        session = github_session()

        run_api_url = (
            "https://api.github.com/repos/"
            f"{owner}/{repo}/actions/runs/"
            f"{run_id}"
        )

        logs_api_url = (
            "https://api.github.com/repos/"
            f"{owner}/{repo}/actions/runs/"
            f"{run_id}/logs"
        )

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
            "[INFO] Информация о Run получена."
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
            "[INFO] Архив логов получен."
        )

        extracted = extract_logs(
            zip_bytes
        )

        steps = build_step_logs(
            extracted
        )

        print(
            "[INFO] Формирование SKALA-документа..."
        )

        document = render_document(
            run_info,
            steps,
            args.export_number,
        )

        output = Path(
            args.output
        )

        save_text(
            output,
            document,
        )

        if not output.is_file():
            raise RuntimeError(
                "Итоговый TXT не создан."
            )

        if output.stat().st_size <= 0:
            raise RuntimeError(
                "Итоговый TXT пуст."
            )

        print(
            "[OK] SKALA TXT создан."
        )

        return 0

    except KeyboardInterrupt:
        return 130

    except Exception as exc:

        print(
            f"[ERROR] "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )