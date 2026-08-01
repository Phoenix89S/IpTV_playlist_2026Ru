#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
Скрипт: Peter_I_rus.py
Версия: 13.3 AI Edition

Сохранение выходных файлов одновременно в main и output/:
1. Peter_I_Full_report.txt   — Основной лог (СКАЛА / ДРЭГ)
2. Peter_I_rating_report.txt — Рейтинговый отчет
3. Peter_I_full.m3u          — Итоговый плейлист
===============================================================================
"""

import os
import re
import time
import gzip
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET
import requests

OUTPUT_DIR = "output"

def save_to_main_and_output(filename, content):
    """Сохранение файла в корень проекта (main) и в папку output/"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 1. Запись в корень (main)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
        
    # 2. Запись в папку output/
    output_path = os.path.join(OUTPUT_DIR, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

# -----------------------------------------------------------------------------
# БЛОК СКАЛА / ДРЭГ (ОСНОВНОЙ ЛОГ)
# -----------------------------------------------------------------------------
class SKALA_DREG_Logger:
    def __init__(self, system_name="ПЕТР_I_ОКНО_В_ЕВПРОПУ_v13.3_AI", main_log="Peter_I_Full_report.txt"):
        self.system_name = system_name
        self.main_log = main_log
        self.t_start = None
        self.log_buffer = []

    def _write(self, text):
        print(text)
        self.log_buffer.append(text)

    def skala_start(self):
        self.t_start = time.perf_counter()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        border = "=" * 95
        self._write(border)
        self._write(f" СКАЛА [ПУСК] :: СИСТЕМА [{self.system_name}]")
        self._write(f" СКАЛА [ВРЕМЯ НАЧАЛА]: {now_str}")
        self._write(border)

    def skala_phase(self, phase_name, detail=""):
        if self.t_start is None:
            self.t_start = time.perf_counter()
        elapsed = time.perf_counter() - self.t_start
        self._write(f"СКАЛА | +{elapsed:07.3f}s | >>> ФАЗА: [{phase_name:<18}] | {detail}")

    def dreg(self, channel_or_slug, action, detail=""):
        if self.t_start is None:
            self.t_start = time.perf_counter()
        elapsed = time.perf_counter() - self.t_start
        self._write(f"ДРЭГ  | +{elapsed:07.3f}s | [{channel_or_slug:<18}] | {action:<28} | {detail}")

    def skala_stop(self, status="НОРМА (200 OK)"):
        t_end = time.perf_counter()
        total_time = t_end - self.t_start
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        border = "=" * 95
        self._write(border)
        self._write(f" СКАЛА [ОСТАНОВ] :: СИСТЕМА [{self.system_name}]")
        self._write(f" СКАЛА [ВРЕМЯ ОКОНЧАНИЯ]: {now_str}")
        self._write(f" СКАЛА [ОБЩЕЕ ВРЕМЯ НАРАБОТКИ]: {total_time:.3f} сек")
        self._write(f" СКАЛА [СТАТУС ИСПОЛНЕНИЯ]: {status}")
        self._write(border)

        # Сохранение основного лога в main и output/
        save_to_main_and_output(self.main_log, "\n".join(self.log_buffer) + "\n")

logger = SKALA_DREG_Logger()

# -----------------------------------------------------------------------------
# НАСТРОЙКИ И АНАЛИЗ РЕЙТИНГОВ
# -----------------------------------------------------------------------------
BASE_HOSTS = [
    "http://a3285272783-s80718.cdn.ngenix.net/hls/",
    "http://a787201481-s80718.cdn.ngenix.net/hls/",
    "http://s80718.cdn.ngenix.net/hls/"
]

ENDPOINTS = [
    "variant.m3u8",
    "video_1920x1080_avc1/playlist.m3u8",
    "video_1280x720_avc1/playlist.m3u8",
    "index.m3u8"
]

EPG_URLS = [
    "http://epg.one/epg.xml.gz",
    "http://epg.one/epg2.xml.gz"
]

HEADERS = {"User-Agent": "HlsWinkPlayer"}
TIMEOUT = 3.5

def calculate_stars_and_age(title, url, latency_ms, has_epg):
    """Расчет звезд (1-5) по Q-Score + Определение возрастного рейтинга."""
    if "1920x1080" in url:
        q_res, res_label = 1.0, "1080p FHD"
    elif "1280x720" in url:
        q_res, res_label = 0.75, "720p HD"
    else:
        q_res, res_label = 0.50, "SD/Auto"

    s_lat = 1.0 if latency_ms < 150 else (0.8 if latency_ms < 350 else 0.5)
    u_epg = 1.0 if has_epg else 0.2
    q_score = (q_res * 0.4) + (s_lat * 0.4) + (u_epg * 0.2)

    stars_count = max(1, min(5, round(q_score * 5)))
    stars_str = "★" * stars_count + "☆" * (5 - stars_count)

    age_match = re.search(r'(\d+\+)', title)
    if age_match:
        age_limit = age_match.group(1)
    else:
        t_low = title.lower()
        if any(w in t_low for w in ["удар", "ночной", "erotika", "18+"]):
            age_limit = "18+"
        elif any(w in t_low for w in ["кино", "fox", "боевик", "хит", "ужас", "hollywood"]):
            age_limit = "16+"
        elif any(w in t_low for w in ["мульт", "детский", "карусель", "0+"]):
            age_limit = "0+"
        else:
            age_limit = "12+"

    return stars_str, age_limit, round(q_score, 2), res_label, f"{latency_ms:.1f}ms"

# -----------------------------------------------------------------------------
# ОСНОВНАЯ ЛОГИКА И СКАНИРОВАНИЕ
# -----------------------------------------------------------------------------
def inspect_and_resolve_node(raw_line, title=""):
    slug_match = re.search(r'CH_[A-Z0-9_]+', raw_line)
    if not slug_match:
        return None, 0.0
    slug = slug_match.group(0)

    target_hosts = list(BASE_HOSTS)
    for host in BASE_HOSTS:
        domain = urllib.parse.urlparse(host).netloc
        if domain in raw_line:
            target_hosts.remove(host)
            target_hosts.insert(0, host)
            break

    for host in target_hosts:
        master_url = f"{host}{slug}/variant.m3u8"
        t0 = time.perf_counter()
        try:
            r = requests.get(master_url, headers=HEADERS, timeout=TIMEOUT)
            latency = (time.perf_counter() - t0) * 1000
            if r.status_code == 200 and "#EXTM3U" in r.text:
                tracks = re.findall(r'([\w_.-]+(?:/playlist\.m3u8|\.m3u8))', r.text)
                if tracks:
                    best_track = next((t for t in tracks if "1920x1080" in t), tracks[0])
                    resolved_url = f"{host}{slug}/{best_track}" if not best_track.startswith("http") else best_track
                    logger.dreg(slug, "ОПРОС_МАНИФЕСТА_200_OK", f"Задержка: {latency:.1f}ms -> {best_track}")
                    return resolved_url, latency
                logger.dreg(slug, "ОПРОС_МАНИФЕСТА_200_OK", f"Задержка: {latency:.1f}ms -> variant.m3u8")
                return master_url, latency
        except requests.RequestException:
            pass

        for ep in ENDPOINTS:
            direct_url = f"{host}{slug}/{ep}"
            t0 = time.perf_counter()
            try:
                r = requests.head(direct_url, headers=HEADERS, timeout=TIMEOUT)
                latency = (time.perf_counter() - t0) * 1000
                if r.status_code == 200:
                    logger.dreg(slug, "ПРЯМОЙ_ЗАПРОС_200_OK", f"Задержка: {latency:.1f}ms -> {ep}")
                    return direct_url, latency
            except requests.RequestException:
                pass

    logger.dreg(slug, "ОТКАЗ_ПОТОКА", "Все узлы недоступны")
    return None, 0.0

def step_0_repair_raw_list(raw_input_text):
    logger.skala_phase("ЭТАП_0_СТАРТ", "Разбор и восстановление оборванных ссылок")
    results = {}
    lines = [l.strip() for l in raw_input_text.strip().split('\n') if l.strip()]
    
    current_title = "Неизвестный канал"
    for line in lines:
        if line.startswith("#EXTINF"):
            # Очистка названия канала от лишних пробелов после запятой
            current_title = line.split(",")[-1].strip()
        elif "CH_" in line:
            resolved_url, latency = inspect_and_resolve_node(line, current_title)
            if resolved_url:
                results[current_title] = {"url": resolved_url, "latency": latency}
                
    return results

def step_1_dictionary_scan(existing_results, channels_to_scan):
    logger.skala_phase("ЭТАП_1_СТАРТ", f"Проверка через словарь ({len(channels_to_scan)} шт.)")
    results = dict(existing_results)

    for title, slug in channels_to_scan.items():
        if title in results:
            logger.dreg(title, "ПРОПУСК_ДУБЛИКАТА", "Уже восстановлен на Этапе 0")
            continue

        dummy_line = f"http://a3285272783-s80718.cdn.ngenix.net/hls/{slug}/"
        resolved_url, latency = inspect_and_resolve_node(dummy_line, title)
        if resolved_url:
            results[title] = {"url": resolved_url, "latency": latency}

    return results

def step_2_epg_and_ai_metrics(channels_map):
    logger.skala_phase("ЭТАП_2_СТАРТ", "Загрузка EPG и расчет рейтингов")
    epg_database = {}

    for epg_url in EPG_URLS:
        t0 = time.perf_counter()
        try:
            r = requests.get(epg_url, timeout=10)
            latency = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                xml_data = gzip.decompress(r.content)
                root = ET.fromstring(xml_data)
                count = 0
                for channel in root.findall("channel"):
                    cid = channel.get("id")
                    for name_elem in channel.findall("display-name"):
                        cname = name_elem.text.strip() if name_elem.text else ""
                        if cname:
                            epg_database[cname.lower()] = cid
                            count += 1
                logger.skala_phase("EPG_УСПЕХ", f"База загружена ({latency:.1f}ms): {count} каналов")
                break
        except Exception as e:
            logger.skala_phase("EPG_ОШИБКА", f"{epg_url} -> {e}")

    final_playlist_items = []
    rating_report_lines = []

    for title, data in channels_map.items():
        url = data["url"]
        latency = data["latency"]
        tvg_id = epg_database.get(title.lower(), "")
        has_epg = bool(tvg_id)

        stars, age_limit, q_score, res_label, lat_str = calculate_stars_and_age(title, url, latency, has_epg)

        tvg_attr = f' tvg-id="{tvg_id}"' if tvg_id else ''
        group_title = "Премиум качество" if q_score >= 0.8 else "Стандарт"
        
        extinf = f'#EXTINF:-1{tvg_attr} rating="{stars}" age="{age_limit}" q-score="{q_score}" group-title="{group_title}" tvg-name="{title}",{title} [{stars} | {age_limit} | {res_label}]'
        final_playlist_items.append((extinf, url))
        
        logger.dreg(title, "РЕЙТИНГ_ФИКСАЦИЯ", f"Звезды: [{stars}] | Ценз: [{age_limit:<3}] | Пинг: {lat_str} | Режим: {res_label}")
        rating_report_lines.append(f"{title:<25} | {stars} | Возраст: {age_limit:<4} | Q-Score: {q_score:<4} | Пинг: {lat_str:<7} | {res_label}")

    return final_playlist_items, rating_report_lines

# -----------------------------------------------------------------------------
# СОХРАНЕНИЕ ВЫХОДНЫХ ФАЙЛОВ
# -----------------------------------------------------------------------------
def save_m3u_playlist(playlist_items, m3u_file="Peter_I_full.m3u"):
    logger.skala_phase("СОХРАНЕНИЕ_M3U", f"Запись выходного плейлиста [{m3u_file}] в main и {OUTPUT_DIR}/")
    epg_header_str = ' url-tvg="' + ', '.join(EPG_URLS) + '"'
    m3u_lines = [f"#EXTM3U{epg_header_str}\n"]

    for extinf, url in playlist_items:
        m3u_lines.append(extinf)
        m3u_lines.append(url)
        m3u_lines.append("")

    save_to_main_and_output(m3u_file, "\n".join(m3u_lines))

def save_rating_report(rating_report_lines, report_file="Peter_I_rating_report.txt"):
    logger.skala_phase("СОХРАНЕНИЕ_РЕЙТИНГА", f"Запись рейтингового отчета [{report_file}] в main и {OUTPUT_DIR}/")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines = [
        "=" * 95,
        f" СИСТЕМА ПЕТР I :: СВОДНЫЙ РЕЙТИНГОВЫЙ ОТЧЕТ КАНАЛОВ ({now_str})",
        "=" * 95,
        f"{'КАНАЛ':<25} | {'РЕЙТИНГ':<5} | {'ВОЗРАСТ':<9} | {'Q-SCORE':<8} | {'ПИНГ':<9} | РЕЖИМ",
        "-" * 95
    ]
    lines.extend(rating_report_lines)
    lines.append("=" * 95)

    save_to_main_and_output(report_file, "\n".join(lines) + "\n")

# -----------------------------------------------------------------------------
# ТОЧКА ВХОДА
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    logger.skala_start()

    # Входной массив ссылок с вашей структуры
    RAW_INPUT = """
    #EXTINF:-1 ,.Red
    a3285272783-s80718.cdn.ngenix.net/hls/CH_REDHD/video_..
    #EXTINF:-1,КИНОКОМЕДИЯ
    a3285272783-s80718.cdn.ngenix.net/hls//CH_KINOKOMEDIY..
    #EXTINF:-1,КИНОСВИДАНИЕ
    a3285272783-s80718.cdn.ngenix.net/hls//CH_KINOSVIDANI..
    #EXTINF:-1,КИНОСЕРИЯ
    a3285272783-s80718.cdn.ngenix.net/hls//CH_KINOSERIYA/..
    #EXTINF:-1,КИНОУЖАС
    a3285272783-s80718.cdn.ngenix.net/hls//CH_KINOUZHAS//..
    #EXTINF:-1 ,Hollywood HD
    a3285272783-s80718.cdn.ngenix.net/hls/CH_HOLLYWOODHD/..
    #EXTINF:-1,FlixSnip
    a3285272783-s80718.cdn.ngenix.net/hls/CH_FLIXSNIPHD/v..
    #EXTINF:-1 ,FOX
    a3285272783-s80718.cdn.ngenix.net//hls//CH_FOX//video..
    #EXTINF:-1 ,Еврокино
    a3285272783-s80718.cdn.ngenix.net//hls//CH_EVROKINO//..
    #EXTINF:-1 ,Filmbox
    a3285272783-s80718.cdn.ngenix.net//hls//CH_FILMBOX//v..
    #EXTINF:-1 ,ВРЕМЯ
    a3285272783-s80718.cdn.ngenix.net//hls//CH_VREMIA//vi..
    #EXTINF:-1 ,ДИКИЙ
    a3285272783-s80718.cdn.ngenix.net//hls//CH_DIKIY//vid..
    #EXTINF:-1 ,ЗООПАРК
    a3285272783-s80718.cdn.ngenix.net//hls//CH_ZOOPARK//v..
    #EXTINF:-1 ,RTVI
    a3285272783-s80718.cdn.ngenix.net//hls//CH_RTVI//vide..
    #EXTINF:-1 ,МИР
    a3285272783-s80718.cdn.ngenix.net//hls//CH_MIR//video..
    #EXTINF:-1 ,КАРУСЕЛЬ
    a3285272783-s80718.cdn.ngenix.net//hls//CH_KARUSEL//v..
    #EXTINF:-1 , KHL HD
    a3285272783-s80718.cdn.ngenix.net//hls//CH_KHL//video..
    #EXTINF:-1 , Удар HD
    a787201481-s80718.cdn.ngenix.net/hls/CH_UDARHD/video_..
    #EXTINF:-1 , Бокс ТВ
    a3285272783-s80718.cdn.ngenix.net/hls/CH_BOKS_TVHD/vi..
    #EXTINF:-1, Fast Sports
    a3285272783-s80718.cdn.ngenix.net//hls//CH_FAST_SPORT..
    """

    # Дополнительные каналы для проверки по словарю (Этап 1)
    CHANNELS_TO_SCAN = {
        "Матч Премьер": "CH_MATCHPREMIER"
    }

    try:
        recovered_map = step_0_repair_raw_list(RAW_INPUT)
        full_map = step_1_dictionary_scan(recovered_map, CHANNELS_TO_SCAN)
        final_items, report_lines = step_2_epg_and_ai_metrics(full_map)
        
        # Сохранение плейлиста и рейтингов
        save_m3u_playlist(final_items, "Peter_I_full.m3u")
        save_rating_report(report_lines, "Peter_I_rating_report.txt")
        
        # Финализация лога СКАЛА / ДРЭГ
        logger.skala_stop(status="НОРМА (200 OK)")
    except Exception as fatal_error:
        logger.skala_phase("АВАРИЯ", f"Критический сбой: {fatal_error}")
        logger.skala_stop(status="АВАРИЙНЫЙ ОСТАНОВ")
