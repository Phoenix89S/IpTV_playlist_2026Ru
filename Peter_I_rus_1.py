#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
===============================================================================
ПРОЕКТ: ПЁТР I — ОКНО В ЕВРОПУ (v18.9.3.6.002 AI Dual-Engine)
ОПИСАНИЕ: Асинхронный брутфорсер CDN-узлов, двойное сканирование (с EPG и 
          прямой брутфорс БЕЗ EPG), логгер СКАЛА / ДРЭГ с полным учётом 
          AI-рейтингов, возрастных цензов и авто-подгрузкой зависимостей.
===============================================================================
"""

import subprocess
import sys

# =============================================================================
# 0. АВТО-УСТАНОВКА МОДУЛЕЙ ДЛЯ GITHUB ACTIONS / RUNNER
# =============================================================================
REQUIRED_PACKAGES = ["requests", "aiohttp"]
for pkg in REQUIRED_PACKAGES:
    try:
        __import__(pkg)
    except ImportError:
        print(f"[СКАЛА] Внимание: Модуль '{pkg}' не обнаружен. Авто-установка...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

import os
import re
import time
import gzip
import signal
import asyncio
import aiohttp
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# =============================================================================
# 1. АВТО-ИНКРЕМЕНТ ИМЁН ФАЙЛОВ (_1 ... _N)
# =============================================================================
def get_next_run_index(base_name="Peter_I_full", output_dir="output"):
    max_idx = 0
    pattern = re.compile(rf"^{re.escape(base_name)}_(\d+)\.(m3u|txt)$", re.IGNORECASE)

    paths_to_check = [".", output_dir]
    for path in paths_to_check:
        if os.path.exists(path):
            for fname in os.listdir(path):
                match = pattern.match(fname)
                if match:
                    idx = int(match.group(1))
                    if idx > max_idx:
                        max_idx = idx

    return max_idx + 1

RUN_INDEX = get_next_run_index()

FILE_M3U = f"Peter_I_full_{RUN_INDEX}.m3u"
FILE_EXTRA_M3U = f"PeterIextra_scan_{RUN_INDEX}.m3u"
FILE_MAIN_LOG = f"Peter_I_Full_report_{RUN_INDEX}.txt"
FILE_RATING_LOG = f"Peter_I_rating_report_{RUN_INDEX}.txt"

def save_to_main_and_output(filename, content):
    out_dir = "output"
    os.makedirs(out_dir, exist_ok=True)
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
        
    out_path = os.path.join(out_dir, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)


# =============================================================================
# 2. БЛОК СКАЛА / ДРЭГ (ЛОГГЕР С ПОДДЕРЖКОЙ АЗ-5)
# =============================================================================
class SKALA_DREG_Logger:
    def __init__(self, system_name="ПЕТР_I_ОКНО_В_ЕВРОПУ_v18.9.3.6.002", main_log=FILE_MAIN_LOG):
        self.system_name = system_name
        self.main_log = main_log
        self.run_index = RUN_INDEX
        self.t_start = None
        self.log_buffer = []

        self.ROD_TRAVEL_DISTANCE_M = 7.0
        self.ROD_SPEED_MPS = 0.40

    def _write(self, text):
        print(text)
        self.log_buffer.append(text)

    def skala_start(self):
        self.t_start = time.perf_counter()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        border = "=" * 95
        self._write(border)
        self._write(f" СКАЛА [ПУСК] :: СИСТЕМА [{self.system_name}] :: СЕАНС #{self.run_index}")
        self._write(f" СКАЛА [ВРЕМЯ НАЧАЛА]: {now_str}")
        self._write(f" СКАЛА [ОСНОВНОЙ ПЛЕЙЛИСТ]:   {FILE_M3U}")
        self._write(f" СКАЛА [ЭКСТРА-СКАНЕР M3U]:  {FILE_EXTRA_M3U}")
        self._write(f" СКАЛА [ЛОГ-ОТЧЕТЫ]:         {FILE_MAIN_LOG} | {FILE_RATING_LOG}")
        self._write(border)

    def skala_phase(self, phase_name, detail=""):
        if self.t_start is None:
            self.t_start = time.perf_counter()
        elapsed = time.perf_counter() - self.t_start
        self._write(f"СКАЛА | +{elapsed:07.3f}s | >>> ФАЗА: [{phase_name:<22}] | {detail}")

    def dreg(self, channel_or_slug, action, detail=""):
        if self.t_start is None:
            self.t_start = time.perf_counter()
        elapsed = time.perf_counter() - self.t_start
        self._write(f"ДРЭГ  | +{elapsed:07.3f}s | [{channel_or_slug:<22}] | {action:<28} | {detail}")

    def trigger_az5(self, reason="РУЧНОЙ ОСТАНОВ WORKFLOW / INTERRUPT"):
        t_end = time.perf_counter() if self.t_start else 0.0
        total_time_before_az5 = t_end - self.t_start if self.t_start else 0.0
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        az5_insertion_time = self.ROD_TRAVEL_DISTANCE_M / self.ROD_SPEED_MPS
        total_shutdown_time = total_time_before_az5 + az5_insertion_time

        border = "!" * 95
        self._write(border)
        self._write(f" !!! [НАЖАТИЕ КНОПКИ АЗ-5] !!! [СЕАНС #{self.run_index}]")
        self._write(" !!! СИГНАЛ АВАРИЙНОЙ ЗАЩИТЫ :: ГЛУШЕНИЕ РЕАКТОРНОЙ УСТАНОВКИ !!!")
        self._write(f" СКАЛА [ВРЕМЯ СБРОСА СТЕРЖНЕЙ СУЗ]: {now_str}")
        self._write(f" СКАЛА [ПРИЧИНА ОСТАНОВА]:            {reason}")
        self._write(f" СКАЛА [НАРАБОТКА ДО НАЖАТИЯ АЗ-5]:   {total_time_before_az5:.3f} сек")
        self._write(f" СКАЛА [ПОЛНОЕ ВРЕМЯ ОСТАНОВА (ИТОГО)]:{total_shutdown_time:.3f} сек")
        self._write(border)

        save_to_main_and_output(self.main_log, "\n".join(self.log_buffer) + "\n")

    def skala_stop(self, status="НОРМА (200 OK)"):
        t_end = time.perf_counter()
        total_time = t_end - self.t_start
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        border = "=" * 95
        self._write(border)
        self._write(f" СКАЛА [ОСТАНОВ] :: СИСТЕМА [{self.system_name}] :: СЕАНС #{self.run_index}")
        self._write(f" СКАЛА [ВРЕМЯ ОКОНЧАНИЯ]: {now_str}")
        self._write(f" СКАЛА [ОБЩЕЕ ВРЕМЯ НАРАБОТКИ]: {total_time:.3f} сек")
        self._write(f" СКАЛА [СТАТУС ИСПОЛНЕНИЯ]: {status}")
        self._write(border)

        save_to_main_and_output(self.main_log, "\n".join(self.log_buffer) + "\n")

logger = SKALA_DREG_Logger()

def handle_abort_signal(sig, frame):
    logger.trigger_az5(reason=f"Перехвачен системный сигнал отмены ({sig})")
    sys.exit(130)

signal.signal(signal.SIGINT, handle_abort_signal)
signal.signal(signal.SIGTERM, handle_abort_signal)


# =============================================================================
# 3. БРУТФОРС CDN УЗЛОВ
# =============================================================================
def bruteforce_cdn_nodes(test_channel="CH_MATCHTV", node_start=80700, node_end=80725):
    logger.skala_phase("БРУТФОРС_CDN", f"Перебор узлов s{node_start} .. s{node_end}")
    headers = {"User-Agent": "HlsWinkPlayer"}
    active_nodes = []

    for node_num in range(node_start, node_end + 1):
        node_host = f"s{node_num}.cdn.ngenix.net"
        test_url = f"http://{node_host}/hls/{test_channel}/variant.m3u8"
        
        t0 = time.perf_counter()
        try:
            resp = requests.head(test_url, headers=headers, timeout=1.2)
            ping_ms = (time.perf_counter() - t0) * 1000
            if resp.status_code in (200, 302):
                logger.dreg(f"s{node_num}", "УЗЕЛ_ОТКЛИКНУЛСЯ", f"Ping: {ping_ms:.1f}ms | HTTP {resp.status_code}")
                active_nodes.append((node_host, ping_ms))
        except Exception:
            pass

    if active_nodes:
        active_nodes.sort(key=lambda x: x[1])
        best_node, best_ping = active_nodes[0]
        logger.skala_phase("БРУТФОРС_УСПЕХ", f"Выбран наилучший узел: {best_node} ({best_ping:.1f}ms)")
        return best_node
    else:
        fallback = "s80718.cdn.ngenix.net"
        logger.skala_phase("БРУТФОРС_ДЕФОЛТ", f"Откат на базовый узел: {fallback}")
        return fallback


# =============================================================================
# 4. АСИНХРОННЫЙ МОДУЛЬ (DEEP SCAN)
# =============================================================================
async def fetch_stream(session, url, slug, semaphore):
    async with semaphore:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                if resp.status == 200:
                    logger.dreg(slug, "ПОТОК_НАЙДЕН_DEEP", f"URL: {url}")
                    return (slug, url)
        except Exception:
            return None

async def scannode_async(nodes, slug_range, concurrency_limit=50):
    headers = {"User-Agent": "HlsWinkPlayer"}
    semaphore = asyncio.Semaphore(concurrency_limit)
    conn = aiohttp.TCPConnector(limit=concurrency_limit, ssl=False)
    
    async with aiohttp.ClientSession(headers=headers, connector=conn) as session:
        tasks = []
        for node_host in nodes:
            for slug in slug_range:
                url = f"http://{node_host}/hls/{slug}/variant.m3u8"
                tasks.append(fetch_stream(session, url, slug, semaphore))
        
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

def run_extra_deep_scan():
    logger.skala_phase("ЭКСТРА_СКАН_СТАРТ", "Запуск асинхронного сканирования узлов")
    
    slugrange = [f"a3285272783{i}" for i in range(30)]
    base_channels = ["MATCHTV", "1TV", "RUSSIA1", "NTV", "CTC", "RENTV", "DOMASHNIY", "MUZTV", "CHAZ"]
    for ch in base_channels:
        slugrange.append(f"CH_{ch}")

    nodes = ["a3285272783-s80718.cdn.ngenix.net", "s80718.cdn.ngenix.net"]
    all_found = asyncio.run(scannode_async(nodes, slugrange))
    
    if all_found:
        m3u_lines = [
            "#EXTM3U",
            f"# EXTRASCAN PLAYLIST :: СЕАНС #{RUN_INDEX}",
            "# --------------------------------------------------------------------"
        ]
        for slug, url in all_found:
            m3u_lines.append(f'#EXTINF:-1 group-title="Найденные потоки" censorship="16+",{slug} [16+]')
            m3u_lines.append("#EXTVLCOPT:http-user-agent=HlsWinkPlayer")
            m3u_lines.append(url)
            m3u_lines.append("")
            
        save_to_main_and_output(FILE_EXTRA_M3U, "\n".join(m3u_lines))
        logger.skala_phase("ЭКСТРА_СКАН_УСПЕХ", f"Сохранено {len(all_found)} потоков в {FILE_EXTRA_M3U}")


# =============================================================================
# 5. СЛОВАРИ И ВОЗРАСТНЫЕ ОГРАНИЧЕНИЯ (CENSORSHIP)
# =============================================================================
# 5.1 Основной словарь каналов (С EPG)
CHANNEL_DICTIONARY = {
    "CH_MATCHTV":  {"name": "Матч!", "tvg_id": "match-tv", "logo": "https://naggdd.github.io/iptv/logos/match.png", "censorship": "16+"},
    "CH_DOMASHNIY":{"name": "Домашний", "tvg_id": "domashny", "logo": "https://iptvx.one/picons/domashniy.png", "censorship": "16+"},
    "CH_DOMASHNY_2":{"name": "Домашний (+2)", "tvg_id": "domashny-pl2", "logo": "https://iptvx.one/picons/domashniy.png", "censorship": "16+"},
    "CH_CTC":      {"name": "СТС", "tvg_id": "sts", "logo": "https://iptvx.one/picons/ctc.png", "censorship": "16+"},
    "CH_RENTV":    {"name": "РЕН ТВ", "tvg_id": "ren-tv", "logo": "https://iptvx.one/picons/rentv.png", "censorship": "16+"},
    "CH_NTV":      {"name": "НТВ", "tvg_id": "ntv", "logo": "https://iptvx.one/picons/ntv.png", "censorship": "16+"},
    "CH_1TV":      {"name": "Первый канал", "tvg_id": "1tv", "logo": "https://iptvx.one/picons/1tv.png", "censorship": "16+"},
    "CH_RUSSIA1":  {"name": "Россия 1", "tvg_id": "russia1", "logo": "https://iptvx.one/picons/russia1.png", "censorship": "16+"},
    "CH_MUZTV":    {"name": "МУЗ-ТВ", "tvg_id": "muz-tv", "logo": "https://iptvx.one/picons/muztv.png", "censorship": "16+"},
    "CH_CHAZ":     {"name": "Че", "tvg_id": "chetv", "logo": "https://iptvx.one/picons/che.png", "censorship": "16+"}
}

# 5.2 Пул брутфорс-потоков БЕЗ EPG (прямые хэши и технические слоты)
NO_EPG_BRUTEFORCE_SLUGS = [
    {"slug": "a32852727830", "name": "Брутфорс-Поток #0", "logo": "", "censorship": "16+"},
    {"slug": "a32852727831", "name": "Брутфорс-Поток #1", "logo": "", "censorship": "16+"},
    {"slug": "a32852727832", "name": "Брутфорс-Поток #2", "logo": "", "censorship": "16+"},
    {"slug": "a32852727833", "name": "Брутфорс-Поток #3", "logo": "", "censorship": "16+"},
    {"slug": "a32852727834", "name": "Брутфорс-Поток #4", "logo": "", "censorship": "16+"},
    {"slug": "CH_TEST_LIVE", "name": "Тест Слот Live",  "logo": "", "censorship": "16+"},
]

def fetch_and_parse_epg():
    logger.skala_phase("ПАРСИНГ_EPG", "Загрузка и парсинг баз epg.one")
    epg_urls = ["https://epg.one/epg.xml.gz", "https://epg.one/epg2.xml.gz"]
    epg_map = {}

    for url in epg_urls:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                xml_data = gzip.decompress(resp.content)
                root = ET.fromstring(xml_data)
                for channel in root.findall("channel"):
                    cid = channel.get("id")
                    display_name = channel.find("display-name")
                    if cid and display_name is not None and display_name.text:
                        epg_map[display_name.text.strip().lower()] = cid
        except Exception:
            pass

    return epg_map


# =============================================================================
# 6. ПРОВЕРКА КАНАЛОВ, РЕЙТИНГ И ЦЕНЗУРА
# =============================================================================
def check_channel_and_rate(slug, info, target_cdn):
    clean_slug = re.sub(r"^(a\d+|video_)", "", slug).split("/")[0]
    stream_url = f"http://{target_cdn}/hls/{clean_slug}/variant.m3u8"
    
    headers = {"User-Agent": "HlsWinkPlayer"}
    t0 = time.perf_counter()
    
    try:
        resp = requests.get(stream_url, headers=headers, timeout=2.0)
        ping_ms = (time.perf_counter() - t0) * 1000
        
        if resp.status_code == 200:
            if ping_ms < 50:
                stars = "★★★★★"
                q_score = 98.5
            elif ping_ms < 120:
                stars = "★★★★☆"
                q_score = 88.0
            else:
                stars = "★★★☆☆"
                q_score = 75.0
                
            censorship = info.get("censorship", "16+")
            
            logger.dreg(
                clean_slug, 
                "200_OK_ПОТОК_АКТИВЕН", 
                f"Ping: {ping_ms:.1f}ms | Q-Score: {q_score}% | {stars} | Age: {censorship}"
            )
            
            return {
                "slug": clean_slug,
                "name": info["name"],
                "tvg_id": info.get("tvg_id", "no-epg"),
                "logo": info.get("logo", ""),
                "censorship": censorship,
                "url": stream_url,
                "ping": ping_ms,
                "q_score": q_score,
                "stars": stars,
                "status": "200 OK"
            }
        else:
            logger.dreg(clean_slug, "ОШИБКА_ПОТОКА", f"HTTP {resp.status_code}")
            return None
    except Exception:
        logger.dreg(clean_slug, "ТАЙМАУТ_ПОТОКА", "Не отвечает")
        return None


# =============================================================================
# 7. ГЛАВНЫЙ ЦИКЛ ИСПОЛНЕНИЯ
# =============================================================================
def main():
    logger.skala_start()
    
    best_cdn = bruteforce_cdn_nodes()
    epg_db = fetch_and_parse_epg()
    
    # 7.1 Проверка основного словаря (С EPG)
    logger.skala_phase("СКАНИРОВАНИЕ_СЛОВАРА", "Проверка доступности каналов (Блок с EPG)")
    valid_channels = []
    
    for slug, info in CHANNEL_DICTIONARY.items():
        lower_name = info["name"].lower()
        if lower_name in epg_db:
            info["tvg_id"] = epg_db[lower_name]
            
        res = check_channel_and_rate(slug, info, best_cdn)
        if res:
            valid_channels.append(res)
            
    # 7.2 Прямой брутфорс каналов БЕЗ EPG
    logger.skala_phase("БРУТФОРС_БЕЗ_EPG", "Сканирование технических слотов без EPG")
    for b_item in NO_EPG_BRUTEFORCE_SLUGS:
        slug = b_item["slug"]
        info = {
            "name": b_item["name"],
            "tvg_id": "no-epg",
            "logo": b_item["logo"],
            "censorship": b_item["censorship"]
        }
        res = check_channel_and_rate(slug, info, best_cdn)
        if res:
            valid_channels.append(res)
            
    # 7.3 Формирование M3U с тегами q-score, censorship и возрастом в названии
    logger.skala_phase("СБОРКА_M3U", f"Формирование файла {FILE_M3U}")
    
    m3u_lines = [
        '#EXTM3U url-tvg="https://epg.one/epg.xml.gz, https://epg.one/epg2.xml.gz" refresh="3600"',
        '# --------------------------------------------------------------------',
        '# ПЛЕЙЛИСТ ПЁТР I: ЭФИРНЫЕ ТВ ПЛЮС (DUAL ENGINE: EPG + NO-EPG BRUTE)',
        '# --------------------------------------------------------------------'
    ]
    
    for ch in valid_channels:
        extinf = (
            f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-logo="{ch["logo"]}" '
            f'group-title="Эфирные ТВ плюс" q-score="{ch["q_score"]}%" censorship="{ch["censorship"]}",'
            f'{ch["name"]} ({ch["stars"]}) [{ch["censorship"]}]'
        )
        m3u_lines.append(extinf)
        m3u_lines.append('#EXTVLCOPT:http-user-agent=HlsWinkPlayer')
        m3u_lines.append(ch["url"])
        m3u_lines.append('')
        
    save_to_main_and_output(FILE_M3U, "\n".join(m3u_lines))
    
    # 7.4 Дополнительный Deep Scan
    run_extra_deep_scan()
    
    # 7.5 Расширенный отчёт по рейтингам и ограничениям
    logger.skala_phase("ОТЧЕТ_РЕЙТИНГА", f"Формирование сводки {FILE_RATING_LOG}")
    
    rating_lines = [
        f"====================================================================================================",
        f"               СВОДНЫЙ AI-РЕЙТИНГ И ВОЗРАСТНЫЕ ОГРАНИЧЕНИЯ (СЕАНС #{RUN_INDEX})",
        f"====================================================================================================",
        f"{'КАНАЛ':<20} | {'СТАТУС':<8} | {'ПИНГ':<10} | {'Q-SCORE':<8} | {'ЦЕНЗ':<6} | {'РЕЙТИНГ'}",
        f"----------------------------------------------------------------------------------------------------"
    ]
    
    for ch in valid_channels:
        rating_lines.append(
            f"{ch['name']:<20} | {ch['status']:<8} | {ch['ping']:05.1f} ms   | {ch['q_score']:<5.1f}%   | {ch['censorship']:<6} | {ch['stars']}"
        )
        
    rating_lines.append(f"====================================================================================================")
    save_to_main_and_output(FILE_RATING_LOG, "\n".join(rating_lines))
    
    logger.skala_stop(status="НОРМА (200 OK)")

if __name__ == "__main__":
    main()
