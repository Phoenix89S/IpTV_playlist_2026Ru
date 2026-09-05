#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests


# ============================================================
# КОНКРЕТНЫЙ GITHUB ACTIONS RUN / JOB
# ============================================================

OWNER = "Phoenix89S"
REPO = "IpTV_playlist_2026Ru"

RUN_ID = 33913972891
JOB_ID = 101156745303

WORKFLOW_NAME = "NGENIX CDN Constellation #7"

RUN_URL = (
    f"https://github.com/{OWNER}/{REPO}"
    f"/actions/runs/{RUN_ID}"
)

JOB_URL = (
    f"{RUN_URL}/job/{JOB_ID}"
)


# ============================================================
# SYSTEM
# ============================================================

SYSTEM_NAME = (
    "СИСТЕМА ЭВС (ЭВМ) версия — последняя сборка_Iptv_AI_edition."
)

SYSTEM_INFO = (
    "SKALA IPTV 8.2026 / IPTV _zoye / Version 6.3 (Build 9600)"
)

DEFAULT_OUTPUT = (
    f"SKALA_RUN_{RUN_ID}_JOB_{JOB_ID}.txt"
)

TIMEOUT = 60


# ============================================================
# SEPARATORS
# ============================================================

SEPARATOR_TOP = (
    "#************************************************************"
)

SEPARATOR_MAIN = (
    "#=============================================="
)

SEPARATOR_BLOCK = (
    "#================================================="
)

SEPARATOR_END = (
    "#*************************************************"
)


# ============================================================
# DATA
# ============================================================

@dataclass
class StepLog:
    number: int
    name: str
    job_name: str
    text: str
    source_file: str


# ============================================================
# GITHUB SESSION
# ============================================================

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


# ============================================================
# API GET
# ============================================================

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
            "Проверь OWNER/REPO/RUN_ID/JOB_ID "
            "и права GITHUB_TOKEN."
        )

    response.raise_for_status()

    return response


# ============================================================
# RUN INFO
# ============================================================

def get_run_info(
    session: requests.Session,
) -> dict:

    url = (
        "https://api.github.com/repos/"
        f"{OWNER}/{REPO}/actions/runs/{RUN_ID}"
    )

    return api_get(
        session,
        url,
    ).json()


# ============================================================
# JOB INFO
# ============================================================

def get_job_info(
    session: requests.Session,
) -> dict:

    url = (
        "https://api.github.com/repos/"
        f"{OWNER}/{REPO}/actions/jobs/{JOB_ID}"
    )

    return api_get(
        session,
        url,
    ).json()


# ============================================================
# ПРОВЕРКА JOB
# ============================================================

def verify_job(
    job_info: dict,
) -> None:

    actual_run_id = job_info.get(
        "run_id"
    )

    if actual_run_id is not None:

        try:
            actual_run_id = int(
                actual_run_id
            )
        except (
            TypeError,
            ValueError,
        ):
            pass

    if actual_run_id != RUN_ID:

        raise RuntimeError(
            "JOB_ID не принадлежит указанному RUN_ID.\n"
            f"Ожидался RUN_ID: {RUN_ID}\n"
            f"Получен RUN_ID: {actual_run_id}\n"
            f"JOB_ID: {JOB_ID}"
        )


# ============================================================
# DOWNLOAD FULL RUN LOG ARCHIVE
# ============================================================

def download_run_logs(
    session: requests.Session,
) -> bytes:

    url = (
        "https://api.github.com/repos/"
        f"{OWNER}/{REPO}/actions/runs/{RUN_ID}/logs"
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


# ============================================================
# DECODE
# ============================================================

def decode_log(
    data: bytes,
) -> str:

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


# ============================================================
# EXTRACT ZIP
# ============================================================

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

            raw = archive.read(
                member
            )

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


# ============================================================
# NORMALIZATION
# ============================================================

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


def normalize_log(
    text: str,
) -> str:

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

        if GROUP_END_RE.search(
            line
        ):

            output.append(
                "[КОНЕЦ БЛОКА]"
            )

            continue

        output.append(
            line
        )

    return "\n".join(
        output
    ).strip()


# ============================================================
# JOB NAME FROM LOG PATH
# ============================================================

def job_name_from_path(
    path: str,
) -> str:

    parts = Path(path).parts

    if len(parts) >= 2:
        return parts[0]

    return "Неизвестный job"


# ============================================================
# BUILD STEP LOGS
# ============================================================

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


# ============================================================
# SCORE MAIN LOG
# ============================================================

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


# ============================================================
# FIND MAIN LOGS
# ============================================================

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


# ============================================================
# UTC
# ============================================================

def utc_now() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# HEADER
# ============================================================

def make_header(
    run_info: dict,
    job_info: dict,
    export_number: int,
) -> str:

    return "\n".join([

        SEPARATOR_TOP,

        f"# {SYSTEM_NAME}",

        f"# Выгрузка конкретного запуска "
        f"{export_number}",

        f"# {SYSTEM_INFO}",

        SEPARATOR_TOP,

        "",

        "#=================================================",
        "# КОНКРЕТНАЯ ЦЕЛЬ ЭКСПОРТА",
        "#=================================================",

        f"# Repository: {OWNER}/{REPO}",

        f"# Workflow: {WORKFLOW_NAME}",

        f"# RUN ID: {RUN_ID}",

        f"# JOB ID: {JOB_ID}",

        f"# Run URL: {RUN_URL}",

        f"# Job URL: {JOB_URL}",

        "",

        f"# Название запуска: "
        f"{run_info.get('name', 'unknown')}",

        f"# Статус Run: "
        f"{run_info.get('status', 'unknown')}",

        f"# Результат Run: "
        f"{run_info.get('conclusion', 'unknown')}",

        f"# Ветка: "
        f"{run_info.get('head_branch', 'unknown')}",

        f"# SHA: "
        f"{run_info.get('head_sha', 'unknown')}",

        f"# Создан: "
        f"{run_info.get('created_at', 'unknown')}",

        f"# Начат: "
        f"{run_info.get('run_started_at', 'unknown')}",

        "",

        f"# Название Job: "
        f"{job_info.get('name', 'unknown')}",

        f"# Статус Job: "
        f"{job_info.get('status', 'unknown')}",

        f"# Результат Job: "
        f"{job_info.get('conclusion', 'unknown')}",

        f"# Job started: "
        f"{job_info.get('started_at', 'unknown')}",

        f"# Job completed: "
        f"{job_info.get('completed_at', 'unknown')}",

        "",

        f"# Экспортирован UTC: "
        f"{utc_now()}",

        "",
    ])


# ============================================================
# MAIN LOG
# ============================================================

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
            "# главный лог проверки узла — "
            "не удалось автоматически определить",
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

                f"# JOB: {step.job_name}",

                f"# SOURCE: {step.source_file}",

                "#",

                "#**********************************************************",

                SEPARATOR_MAIN,

                "",

                step.text,

                "",
            ])
        )

    return "\n".join(
        blocks
    )


# ============================================================
# STEP
# ============================================================

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

        f"# SOURCE: {step.source_file}",

        SEPARATOR_BLOCK,

        "",

        step.text,

        "",
    ])


# ============================================================
# DOCUMENT
# ============================================================

def render_document(
    run_info: dict,
    job_info: dict,
    steps: list[StepLog],
    export_number: int,
) -> str:

    parts = []

    parts.append(
        make_header(
            run_info,
            job_info,
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
            "# ПОЛНЫЙ СОСТАВ ОСТАЛЬНЫХ ЛОГОВ RUN",
            "#==============================================",
            "#**********************************************************",

            "",
        ])
    )

    for step in steps:

        parts.append(
            render_step(
                step
            )
        )

    parts.append(
        "\n".join([

            "",

            "#**********************************************",

            "# конец телетайпа",

            f"# RUN ID: {RUN_ID}",

            f"# JOB ID: {JOB_ID}",

            SEPARATOR_END,

            "",
        ])
    )

    return "\n".join(
        parts
    )


# ============================================================
# SAVE
# ============================================================

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


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    print("=" * 60)

    print(
        "SKALA IPTV 8.2026"
    )

    print(
        "IPTV _zoye"
    )

    print(
        "Version 6.3 (Build 9600)"
    )

    print(
        "ЭКСПОРТ КОНКРЕТНОГО GITHUB ACTIONS RUN"
    )

    print("=" * 60)

    print(
        f"[TARGET] Repository: "
        f"{OWNER}/{REPO}"
    )

    print(
        f"[TARGET] Workflow: "
        f"{WORKFLOW_NAME}"
    )

    print(
        f"[TARGET] RUN_ID: "
        f"{RUN_ID}"
    )

    print(
        f"[TARGET] JOB_ID: "
        f"{JOB_ID}"
    )

    print(
        f"[TARGET] Job URL: "
        f"{JOB_URL}"
    )

    try:

        session = github_session()

        # ----------------------------------------------------
        # RUN
        # ----------------------------------------------------

        print(
            "[INFO] Получение информации о Run..."
        )

        run_info = get_run_info(
            session
        )

        actual_run_id = run_info.get(
            "id"
        )

        if actual_run_id != RUN_ID:

            raise RuntimeError(
                "GitHub вернул другой RUN_ID.\n"
                f"Ожидался: {RUN_ID}\n"
                f"Получен: {actual_run_id}"
            )

        print(
            "[OK] Run подтверждён."
        )

        # ----------------------------------------------------
        # JOB
        # ----------------------------------------------------

        print(
            "[INFO] Получение информации о Job..."
        )

        job_info = get_job_info(
            session
        )

        verify_job(
            job_info
        )

        print(
            "[OK] Job подтверждён."
        )

        print(
            f"[INFO] Job name: "
            f"{job_info.get('name', 'unknown')}"
        )

        # ----------------------------------------------------
        # LOGS
        # ----------------------------------------------------

        print(
            "[INFO] Скачивание полного архива логов Run..."
        )

        zip_bytes = download_run_logs(
            session
        )

        print(
            "[OK] Архив логов получен."
        )

        print(
            f"[INFO] Размер архива: "
            f"{len(zip_bytes)} байт"
        )

        # ----------------------------------------------------
        # EXTRACT
        # ----------------------------------------------------

        print(
            "[INFO] Извлечение логов..."
        )

        extracted = extract_logs(
            zip_bytes
        )

        print(
            f"[OK] Файлов в архиве: "
            f"{len(extracted)}"
        )

        # ----------------------------------------------------
        # BUILD
        # ----------------------------------------------------

        steps = build_step_logs(
            extracted
        )

        print(
            f"[OK] Непустых логов: "
            f"{len(steps)}"
        )

        # ----------------------------------------------------
        # DOCUMENT
        # ----------------------------------------------------

        print(
            "[INFO] Формирование SKALA-документа..."
        )

        document = render_document(
            run_info,
            job_info,
            steps,
            1,
        )

        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        output = Path(
            DEFAULT_OUTPUT
        )

        save_text(
            output,
            document,
        )

        if not output.is_file():

            raise RuntimeError(
                "Итоговый TXT не создан."
            )

        size = output.stat().st_size

        if size <= 0:

            raise RuntimeError(
                "Итоговый TXT пуст."
            )

        print(
            "[OK] SKALA TXT создан."
        )

        print(
            f"[OK] Файл: {output}"
        )

        print(
            f"[OK] Размер: {size} байт"
        )

        print(
            f"[OK] RUN_ID: {RUN_ID}"
        )

        print(
            f"[OK] JOB_ID: {JOB_ID}"
        )

        return 0

    except KeyboardInterrupt:

        print(
            "[WARN] Остановлено пользователем.",
            file=sys.stderr,
        )

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