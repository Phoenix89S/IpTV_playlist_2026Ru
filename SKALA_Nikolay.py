#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from urllib.parse import urljoin
import random

SRC_URL = "https://iptv-org.github.io/iptv/regions/cis.m3u"

OUT_PLAYLIST = "SNG_Ru.m3u"
OUT_PLAYLIST_OLD = "SNG_Ru_Old.m3u"
REPORT_DIR = "reports"

HEADERS = {
    "User-Agent": "Mozilla/5.0 Phoenix89S-SNG-Validator"
}

#===========================================================
# СКАЛА/ДРЭГ — ТЫ ВСТАВИШЬ СЮДА ПОЛНЫЕ СЛОВАРИ
#===========================================================

NORMAL_LINES = [
    
00:00:00  СМЕНА 5  НАЧ_СМЕНЫ АКИМОВ_А_Ф  СИУР ТОПТУНОВ_Л_Ф
00:00:10  N_ТЕПЛ      1600 МВТ
00:00:30  N_ЭЛЕКТР     480 МВТ
00:01:15  ОЗР_ПРИЗМА  24.2 СТЕРЖ
00:05:00  ПАР_РЕАКТ_СТАБИЛЕН
00:26:00  N_ТЕПЛ      1500 МВТ -- НАЧАЛО СНИЖЕНИЯ МОЩНОСТИ ПО ПРОГРАММЕ ИСПЫТАНИЙ
00:26:15  N_ЭЛЕКТР     450 МВТ
00:28:00  ОЗР_ПРИЗМА  26.1 СТЕРЖ

00:28:10  ПЕРЕХОД НА ЛОКАЛЬНЫЙ АВТОМАТИЧЕСКИЙ РЕГУЛЯТОР (ЛАР)
00:28:13  СИГНАЛ: ОТКЛЮЧЕНИЕ АР (АВТОМАТИЧЕСКИЙ РЕГУЛЯТОР)
00:28:15  N_ТЕПЛ      МЕНЕЕ 30 МВТ -- КРАТКОВРЕМЕННЫЙ ПРОВАЛ МОЩНОСТИ (ОТРАВЛЕНИЕ РЕАКТОРА)
00:28:40  ПОДЪЕМ СТЕРЖНЕЙ СУЗ ВРУЧНУЮ (РЕЖИМ РР)

01:00:00  N_ТЕПЛ      200 МВТ -- МОЩНОСТЬ СТАБИЛИЗИРОВАНА НА НОВОМ УРОВНЕ
01:03:00  ОЗР_ПРИЗМА  18.4 СТЕРЖ
01:03:15  ВКЛЮЧЕН ГЦН-12 (ПЕТЛЯ ЛЕВАЯ) -- ВСЕГО В РАБОТЕ 7 НАГНЕТАТЕЛЬНЫХ НАСОСОВ
01:07:00  ВКЛЮЧЕН ГЦН-24 (ПЕТЛЯ ПРАВАЯ) -- ВСЕГО В РАБОТЕ 8 НАГНЕТАТЕЛЬНЫХ НАСОСОВ
01:07:15  ВНИМАНИЕ: РАСХОД ВОДЫ ЧЕРЕЗ РЕАКТОР ВЫШЕ ПРЕДЕЛЬНО ДОПУСТИМОГО (8200 М3/Ч)
01:09:00  Р_БС_ЛЕВ    6.1 МПА  -- СНИЖЕНИЕ ДАВЛЕНИЯ В БАРАБАН-СЕПАТОРАХ
01:09:10  Р_БС_ПРАВ   6.1 МПА
01:12:00  СИГНАЛ: ТЕПЛОВОЙ ПОТОК -- ОТКЛОНЕНИЕ ОТ ЗАДАННОГО
01:15:30  СИГНАЛ: ТЕМПЕРАТУРА КМПЦ -- РОСТ ДО ПРЕДЕЛА НАШЫЩЕНИЯ

01:19:00  УРОВЕНЬ_БС  СНИЖЕНИЕ НИЖЕ АВАРИЙНОЙ ОТМЕТКИ (-600 ММ)
01:19:15  СИГНАЛ: ЗАПЛАТКА АЗС (БЛОКИРОВКА АЗ ПО СНИЖЕНИЮ УРОВНЯ В БАРАБАН-СЕПАТОРАХ)
01:19:20  ПОДЪЕМ ДОПОЛНИТЕЛЬНЫХ СТЕРЖНЕЙ СУЗ ИЗ АКТИВНОЙ ЗОНЫ ДЛЯ ПОДДЕРЖАНИЯ МОЩНОСТИ
01:20:00  N_ТЕПЛ      200 МВТ
01:21:40  Р_БС_ЛЕВ    6.2 МПА  -- РОСТ ДАВЛЕНИЯ ЗА СЧЕТ ЗАКРЫТИЯ СЛИВНОЙ АРМАТУРЫ
01:22:10  РАСХОД_ПИТ_ВОДЫ  УВЕЛИЧЕН ДО 1200 Т/Ч

01:22:30  ЗАПУСК ПРОГРАММЫ РАСЧЕТА ПРИЗМА-ОЗР (ЦИКЛ 7 МИНУТ)
01:22:45  РЕЗУЛЬТАТ ПРИЗМА:
================================================================================
          ВРЕМЯ РАСЧЕТА: 26.04.86  01:22:30
          ОПЕРАТИВНЫЙ ЗАПАС РЕАКТИВНОСТИ (ОЗР) = 13.2 СТЕРЖНЯ СУЗ
          ВНИМАНИЕ: ЗНАЧЕНИЕ НИЖЕ РЕГЛАМЕНТНОГО МИНИМУМА (ОЗР < 15 СТЕРЖНЕЙ)
================================================================================

01:23:00  N_ТЕПЛ      200 МВТ  -- ПАРАМЕТРЫ СТАБИЛЬНЫ ДЛЯ НАЧАЛА ИСПЫТАНИЙ
01:23:04  СИГНАЛ: ЗАКРЫТИЕ БЫСТРОДЕЙСТВУЮЩИХ СТОПОРНЫХ КЛАПАНОВ (БСК) ТГ-8
01:23:05  НАЧАЛО ВЫБЕГА ТУРБОГЕНЕРАТОРА №8
01:23:06  ОТКЛЮЧЕНИЕ АВТОМАТИЧЕСКОЙ ЗАЩИТЫ ПО СИГНАЛУ "ОТКЛЮЧЕНИЕ ДВУХ ТГ" (БЛОКИРОВКА АЗ)
01:23:10  ГЦН-13  УМЕНЬШЕНИЕ ОБОРОТОВ (ПИТАНИЕ ОТ ВЫБЕГА ТУРБИНЫ)
01:23:12  ГЦН-23  УМЕНЬШЕНИЕ ОБОРОТОВ
01:23:15  ГЦН-14  УМЕНЬШЕНИЕ ОБОРОТОВ
01:23:17  ГЦН-24  УМЕНЬШЕНИЕ ОБОРОТОВ
01:23:20  ОБЩИЙ РАСХОД ТЕПЛОНОСИТЕЛЯ ЧЕРЕЗ АКТИВНУЮ ЗОНУ СНИЖАЕТСЯ
01:23:22  Т_ВХОД_АЗ   РОСТ (ТЕМПЕРАТУРА НА ВХОДЕ В КАНАЛЫ СБЛИЖАЕТСЯ С ТЕМПЕРАТУРОЙ КИПЕНИЯ)

01:23:30  ОБЪЕМНОЕ ПАРОСОДЕРЖАНИЕ В АКТИВНОЙ ЗОНЕ РАСТЕТ
01:23:35  МЕДЛЕННЫЙ РОСТ НЕЙТРОННОЙ МОЩНОСТИ (ПОЛОЖИТЕЛЬНЫЙ ПАРОВОЙ ЭФФЕКТ)
01:23:37  АР (АВТОМАТИЧЕСКИЕ РЕГУЛЯТОРЫ) ИДУТ ВНИЗ НА ЗАПОЛНЕНИЕ ЗОНЫ ДЛЯ КОМПЕНСАЦИИ РОСТА

01:23:40  СИГНАЛ: НАЖАТИЕ КНОПКИ АЗ-5 (АВАРИЙНАЯ ЗАЩИТА 5-Й КАТЕГОРИИ)
01:23:40  НАЧАЛО ДВИЖЕНИЯ ВСЕХ РЕГУЛИРУЮЩИХ И ПОГЛОЩАЮЩИХ СТЕРЖНЕЙ СУЗ В АКТИВНУЮ ЗОНУ
01:23:40  СИГНАЛ: СУЗ_ДВИЖ_АКТИВЕН (СКОРОСТЬ СПУСКА 0.4 М/С)

]

PREFAIL_LINES = [
    # вставишь сам
]

FAIL_LINES = [
    # вставишь сам
]

#===========================================================
# ФУНКЦИИ ВЫБОРА СТРОКИ
#===========================================================

def get_normal_line():
    return random.choice(NORMAL_LINES) if NORMAL_LINES else "00:00:10  N_ТЕПЛ      1600 МВТ"

def get_prefail_line():
    return random.choice(PREFAIL_LINES) if PREFAIL_LINES else "01:23:30.0  СИГНАЛ    ПАР_ОБЪЕМНЫЙ_АКТИВНАЯ_ЗОНА_РОСТ"

def get_fail_line():
    return random.choice(FAIL_LINES) if FAIL_LINES else "01:23:40.0  СИГНАЛ    НАЖАТИЕ_КНОПКИ_АЗ_5    =  АКТИВЕН"

#===========================================================
# СКАЛА/ДРЭГ ЛОГГЕР
#===========================================================

def log(msg):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] :: {msg}"
    print(line)
    LOG_LINES.append(line)

def log_ok(channel_name, url):
    extra = get_normal_line()
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] :: {channel_name} :: сигнал стабилен (!). {extra} :: {url}"
    print(line)
    LOG_LINES.append(line)

def log_prefail(channel_name, url):
    extra = get_prefail_line()
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] :: {channel_name} :: сигнал потерян (!). {extra} :: {url}"
    print(line)
    LOG_LINES.append(line)

def log_fail(channel_name, url):
    extra = get_fail_line()
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{t}] :: {channel_name} :: сигнал потерян (!). {extra} :: {url}"
    print(line)
    LOG_LINES.append(line)

#===========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
#===========================================================

def extract_name(extinf_line):
    if "," in extinf_line:
        return extinf_line.split(",", 1)[1].strip()
    return "НЕИЗВЕСТНЫЙ_КАНАЛ"

def load_m3u(url):
    log(f"LOAD_M3U :: {url}")
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text.splitlines()

def parse_m3u(lines):
    log("PARSE_M3U :: start")
    channels = []
    current = {}

    for line in lines:
        if line.startswith("#EXTINF"):
            current = {"extinf": line, "url": None}
        elif line.startswith("http"):
            current["url"] = line.strip()
            channels.append(current)
            current = {}

    log(f"OK :: PARSE_M3U :: {len(channels)} каналов разобрано")
    return channels

#===========================================================
# ПРОВЕРКА ЖИВОСТИ ПОТОКА
#===========================================================

def is_alive(extinf, url):
    channel_name = extract_name(extinf)

    try:
        r = requests.get(url, headers=HEADERS, timeout=10, stream=True)

        # Сервер не ответил → авария
        if r.status_code != 200:
            log_fail(channel_name, url)
            return False

        text = r.text
        segs = re.findall(r"(.*\.ts)", text)

        # Если сегмент найден
        if segs:
            seg = segs[0]
            seg_url = urljoin(url, seg)
            log(f"SEG_CHECK :: {channel_name} :: {seg_url}")

            r2 = requests.get(seg_url, headers=HEADERS, timeout=10, stream=True)

            # Сегмент отвечает → нормальная строка
            if r2.status_code == 200:
                log_ok(channel_name, url)
                return True

            # Сегмент не отвечает → предаварийная строка
            else:
                log_prefail(channel_name, seg_url)
                return False

        # Поток активен, но без сегментов
        log_ok(channel_name, url)
        return True

    except Exception:
        # Любая ошибка сети → авария
        log_fail(channel_name, url)
        return False

#===========================================================
# СОХРАНЕНИЕ ПЛЕЙЛИСТОВ И ОТЧЁТОВ
#===========================================================

def save_playlist(channels):
    with open(OUT_PLAYLIST, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            f.write(ch["extinf"] + "\n")
            f.write(ch["url"] + "\n")

    log(f"OK :: Плейлист сохранён :: {OUT_PLAYLIST} :: {len(channels)} каналов")

def save_old_playlist():
    if os.path.exists(OUT_PLAYLIST):
        with open(OUT_PLAYLIST, "r", encoding="utf-8") as src:
            data = src.read()
        with open(OUT_PLAYLIST_OLD, "w", encoding="utf-8") as dst:
            dst.write(data)
        log(f"OK :: OLD-плейлист создан :: {OUT_PLAYLIST_OLD}")

def save_report():
    if not os.path.exists(REPORT_DIR):
        os.makedirs(REPORT_DIR)

    existing = [f for f in os.listdir(REPORT_DIR) if f.startswith("SNG_Ru_Report_")]
    next_num = len(existing) + 1
    filename = f"SNG_Ru_Report_{next_num}.txt"
    path = os.path.join(REPORT_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        f.write("#==============================================\n")
        f.write("# Система ЭВМ/ЭВС СКАЛА 3 ver 9 ДРЕГ Phoenix89S edition.\n")
        f.write("#==============================================\n\n")
        for line in LOG_LINES:
            f.write(line + "\n")

    log(f"OK :: Отчёт сохранён :: {path}")

#===========================================================
# MAIN
#===========================================================

def main():
    global LOG_LINES
    LOG_LINES = []

    print("#==============================================")
    print("# Система ЭВМ/ЭВС СКАЛА 3 ver 9 ДРЕГ Phoenix89S edition.")
    print("#==============================================")

    log("START :: Phoenix SNG validator")

    save_old_playlist()

    lines = load_m3u(SRC_URL)
    channels = parse_m3u(lines)

    live = []
    for ch in channels:
        extinf = ch["extinf"]
        url = ch["url"]
        name = extract_name(extinf)

        log(f"ПРОВЕРКА :: {name} :: {url}")

        if is_alive(extinf, url):
            live.append(ch)

    save_playlist(live)
    save_report()

    log("REPORT :: DONE")
    log(f"TOTAL :: {len(channels)} каналов")
    log(f"LIVE  :: {len(live)} каналов")
    log("END :: SCALA/DRÆG REPORT COMPLETE")

if __name__ == "__main__":
    main()