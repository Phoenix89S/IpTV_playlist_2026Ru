#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
===============================================================================
ПРОЕКТ: ПЁТР I — ОКНО В ЕВРОПУ (v13.4 AI Edition)
ОПИСАНИЕ: Автоматический агрегатор, брутфорсер CDN-узлов, парсер EPG 
          и анализатор каналов с интеграцией логгера СКАЛА / ДРЭГ и АЗ-5.
===============================================================================
"""

import os
import re
import sys
import time
import gzip
import signal
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# =============================================================================
# 1. АВТО-ИНКРЕМЕНТ ИМЁН ФАЙЛОВ (_1 ... _N)
# =============================================================================
def get_next_run_index(base_name="Peter_I_full", output_dir="output"):
    """
    Сканирует корень и директорию вывода.
    Если файлов с номерами _N нет — начинает с 1.
    Если есть — возвращает max(N) + 1.
    """
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
FILE_MAIN_LOG = f"Peter_I_Full_report_{RUN_INDEX}.txt"
FILE_RATING_LOG = f"Peter_I_rating_report_{RUN_INDEX}.txt"

def save_to_main_and_output(filename, content):
    """Сохраняет файл в корень и дублирует в директорию /output"""
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
    def __init__(self, system_name="ПЕТР_I_ОКНО_В_ЕВПРОПУ_v13.4_AI", main_log=FILE_MAIN_LOG):
        self.system_name = system_name
        self.main_log = main_log
        self.run_index = RUN_INDEX
        self.t_start = None
        self.log_buffer = []

        # Константы кинетики СУЗ (Системы Управления и Защиты)
        self.ROD_TRAVEL_DISTANCE_M = 7.0  # Длина хода стержня (м)
        self.ROD_SPEED_MPS = 0.40         # Скорость спуска стержней (м/с)

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
        self._write(f" СКАЛА [ВЫХОДНЫЕ ФАЙЛЫ]: {FILE_M3U} | {FILE_MAIN_LOG} | {FILE_RATING_LOG}")
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

    def trigger_az5(self, reason="РУЧНОЙ ОСТАНОВ WORKFLOW / INTERRUPT"):
        """Симуляция нажатия кнопки АЗ-5 при аварийном останове"""
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
        self._write(f" СКАЛА [СКОРОСТЬ СПУСКА СТЕРЖНЕЙ]:    {self.ROD_SPEED_MPS:.2f} м/с (Ход: {self.ROD_TRAVEL_DISTANCE_M:.1f} м)")
        self._write(f" СКАЛА [ВРЕМЯ ПОГРУЖЕНИЯ СУЗ (АЗ-5)]: {az5_insertion_time:.2f} сек")
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

# Перехват системных сигналов отмены (SIGINT / SIGTERM)
def handle_abort_signal(sig, frame):
    logger.trigger_az5(reason=f"Перехвачен системный сигнал отмены ({sig})")
    sys.exit(130)

signal.signal(signal.SIGINT, handle_abort_signal)
signal.signal(signal.SIGTERM, handle_abort_signal)


# =============================================================================
# 3. БРУТФОРС CDN УЗЛОВ
# =============================================================================
def bruteforce_cdn_nodes(test_channel="CH_MATCHTV", node_start=80700, node_end=80725):
    """Сканирует пул зеркал CDN Ngenix для выбора самого устойчивого узла."""
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
            else:
                logger.dreg(f"s{node_num}", "ОТКЛОНЕНО", f"HTTP {resp.status_code}")
        except Exception:
            logger.dreg(f"s{node_num}", "ТАЙМАУТ/ОШИБКА", "Узел не отвечает")

    if active_nodes:
        active_nodes.sort(key=lambda x: x[1])
        best_node, best_ping = active_nodes[0]
        logger.skala_phase("БРУТФОРС_УСПЕХ", f"Выбран наилучший узел: {best_node} ({best_ping:.1f}ms)")
        return best_node
    else:
        fallback = "s80718.cdn.ngenix.net"
        logger.skala_phase("БРУТФОРС_ДЕФОЛТ", f"Активные узлы не найдены. Откат на базовый: {fallback}")
        return fallback


# =============================================================================
# 4. БАЗОВЫЕ СЛОВАРИ И ПАРСИНГ EPG
# =============================================================================
CHANNEL_DICTIONARY = {
    "CH_MATCHTV": {"name": "Матч!", "tvg_id": "match-tv", "logo": "https://naggdd.github.io/iptv/logos/match.png"},
    "CH_DOMASHNIY": {"name": "Домашний", "tvg_id": "domashny", "logo": "https://iptvx.one/picons/domashniy.png"},
    "CH_DOMASHNY_2": {"name": "Домашний (+2)", "tvg_id": "domashny-pl2", "logo": "https://iptvx.one/picons/domashniy.png"},
    "CH_CTC": {"name": "СТС", "tvg_id": "sts", "logo": "https://iptvx.one/picons/ctc.png"},
    "CH_RENTV": {"name": "РЕН ТВ", "tvg_id": "ren-tv", "logo": "https://iptvx.one/picons/rentv.png"},
    "CH_NTV": {"name": "НТВ", "tvg_id": "ntv", "logo": "https://iptvx.one/picons/ntv.png"},
    "CH_1TV": {"name": "Первый канал", "tvg_id": "1tv", "logo": "https://iptvx.one/picons/1tv.png"},
    "CH_RUSSIA1": {"name": "Россия 1", "tvg_id": "russia1", "logo": "https://iptvx.one/picons/russia1.png"},
    "CH_MUZTV": {"name": "МУЗ-ТВ", "tvg_id": "muz-tv", "logo": "https://iptvx.one/picons/muztv.png"},
    "CH_CHAZ": {"name": "Че", "tvg_id": "chetv", "logo": "https://iptvx.one/picons/che.png"}
}

def fetch_and_parse_epg():
    """Скачивает и прочесывает базы epg.one для авто-привязки tvg-id."""
    logger.skala_phase("ПАРСИНГ_EPG", "Загрузка и 'шуршание' по базам epg.one")
    epg_urls = ["https://epg.one/epg.xml.gz", "https://epg.one/epg2.xml.gz"]
    epg_map = {}

    for url in epg_urls:
        try:
            logger.dreg("EPG_DOWNLOAD", "ЗАПРОС_АРХИВА", url)
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                xml_data = gzip.decompress(resp.content)
                root = ET.fromstring(xml_data)
                for channel in root.findall("channel"):
                    cid = channel.get("id")
                    display_name = channel.find("display-name")
                    if cid and display_name is not None and display_name.text:
                        epg_map[display_name.text.strip().lower()] = cid
                logger.dreg("EPG_SUCCESS", "БАЗА_ОБРАБОТАНА", f"Загружено ID: {len(epg_map)}")
        except Exception as e:
            logger.dreg("EPG_ERROR", "СБОЙ_ЗАГРУЗКИ", str(e))

    return epg_map


# =============================================================================
# 5. ПРОВЕРКА КАНАЛОВ И РАСЧЕТ AI-РЕЙТИНГА
# =============================================================================
def check_channel_and_rate(slug, info, target_cdn):
    """Проверяет поток на CDN, считает пинг, выставляет Q-Score и звездочки."""
    clean_slug = re.sub(r"^(a\d+|video_)", "", slug).split("/")[0]
    stream_url = f"http://{target_cdn}/hls/{clean_slug}/variant.m3u8"
    
    headers = {"User-Agent": "HlsWinkPlayer"}
    t0 = time.perf_counter()
    
    try:
        resp = requests.get(stream_url, headers=headers, timeout=2.0)
        ping_ms = (time.perf_counter() - t0) * 1000
        
        if resp.status_code == 200:
            # Алгоритм оценки качества Q-Score
            if ping_ms < 50:
                stars = "★★★★★"
                q_score = 98.5
            elif ping_ms < 120:
                stars = "★★★★☆"
                q_score = 88.0
            else:
                stars = "★★★☆☆"
                q_score = 75.0
                
            logger.dreg(clean_slug, "200_OK_ПОТОК_АКТИВЕН", f"Ping: {ping_ms:.1f}ms | Q-Score: {q_score}% | {stars}")
            
            return {
                "slug": clean_slug,
                "name": info["name"],
                "tvg_id": info["tvg_id"],
                "logo": info["logo"],
                "url": stream_url,
                "ping": ping_ms,
                "q_score": q_score,
                "stars": stars,
                "status": "200 OK"
            }
        else:
            logger.dreg(clean_slug, "ОШИБКА_ПОТОКА", f"HTTP {resp.status_code}")
            return None
    except Exception as e:
        logger.dreg(clean_slug, "ТАЙМАУТ_ПОТОКА", "Не отвечает")
        return None


# =============================================================================
# 6. ГЛАВНЫЙ ЦИКЛ ИСПОЛНЕНИЯ
# =============================================================================
def main():
    logger.skala_start()
    
    # 1. Брутфорс CDN
    best_cdn = bruteforce_cdn_nodes()
    
    # 2. Шуршим по EPG
    epg_db = fetch_and_parse_epg()
    
    # 3. Сканирование и сборка каналов
    logger.skala_phase("СКАНИРОВАНИЕ_СЛОВАРА", "Проверка доступности каналов")
    valid_channels = []
    
    for slug, info in CHANNEL_DICTIONARY.items():
        # Доп. привязка EPG, если совпали названия
        lower_name = info["name"].lower()
        if lower_name in epg_db:
            info["tvg_id"] = epg_db[lower_name]
            
        res = check_channel_and_rate(slug, info, best_cdn)
        if res:
            valid_channels.append(res)
            
    # 4. Генерация итогового M3U плейлиста
    logger.skala_phase("СБОРКА_M3U", f"Формирование файла {FILE_M3U}")
    
    m3u_lines = [
        '#EXTM3U url-tvg="https://epg.one/epg.xml.gz, https://epg.one/epg2.xml.gz" refresh="3600"',
        '# --------------------------------------------------------------------',
        '# ПЛЕЙЛИСТ ПЁТР I: ЭФИРНЫЕ ТВ ПЛЮС',
        '# --------------------------------------------------------------------'
    ]
    
    for ch in valid_channels:
        m3u_lines.append(f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-logo="{ch["logo"]}" group-title="Эфирные ТВ плюс" q-score="{ch["q_score"]}%",{ch["name"]} ({ch["stars"]})')
        m3u_lines.append('#EXTVLCOPT:http-user-agent=HlsWinkPlayer')
        m3u_lines.append(ch["url"])
        m3u_lines.append('')
        
    save_to_main_and_output(FILE_M3U, "\n".join(m3u_lines))
    
    # 5. Генерация рейтингового отчета
    logger.skala_phase("ОТЧЕТ_РЕЙТИНГА", f"Формирование сводки {FILE_RATING_LOG}")
    
    rating_lines = [
        f"=========================================================================================",
        f"                   СВОДНЫЙ AI-РЕЙТИНГ КАНАЛОВ (СЕАНС #{RUN_INDEX})",
        f"=========================================================================================",
        f"{'КАНАЛ':<20} | {'СТАТУС':<10} | {'ПИНГ':<10} | {'Q-SCORE':<10} | {'РЕЙТИНГ'}",
        f"-----------------------------------------------------------------------------------------"
    ]
    
    for ch in valid_channels:
        rating_lines.append(f"{ch['name']:<20} | {ch['status']:<10} | {ch['ping']:05.1f} ms   | {ch['q_score']:<5.1f} %   | {ch['stars']}")
        
    rating_lines.append(f"=========================================================================================")
    save_to_main_and_output(FILE_RATING_LOG, "\n".join(rating_lines))
    
    # 6. Успешное завершение работы
    logger.skala_stop(status="НОРМА (200 OK)")

if __name__ == "__main__":
    main()
