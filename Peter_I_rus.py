#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
Скрипт: Peter_I_rus.py
Назначение: Автоматическое восстановление, полное сканирование и EPG-разметка
            IPTV-плейлистов Wink / Ngenix.
===============================================================================
"""

import os
import re
import gzip
import urllib.parse
import xml.etree.ElementTree as ET
import requests

# -----------------------------------------------------------------------------
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ И БАЗОВЫЕ УЗЛЫ
# -----------------------------------------------------------------------------
BASE_HOSTS = [
    "http://a3285272783-s80718.cdn.ngenix.net/hls/",  # Основной кластер 1
    "http://a787201481-s80718.cdn.ngenix.net/hls/",   # Кластер 2 (Спорт/Удар)
    "http://s80718.cdn.ngenix.net/hls/"               # Прямой узел Ngenix
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

# -----------------------------------------------------------------------------
# ЭТАП 0: Восстановление оборванных ссылок + Опрос манифеста узла
# -----------------------------------------------------------------------------
def inspect_and_resolve_node(raw_line):
    """
    Нормализует обрывок URL, находит слюг (CH_...), запрашивает узел,
    вычитывает Master Playlist (если есть) и возвращает наилучший рабочий URL.
    """
    slug_match = re.search(r'CH_[A-Z0-9_]+', raw_line)
    if not slug_match:
        return None
    slug = slug_match.group(0)

    # Приоритезируем хост, если он частично сохранился в строке
    target_hosts = list(BASE_HOSTS)
    for host in BASE_HOSTS:
        domain = urllib.parse.urlparse(host).netloc
        if domain in raw_line:
            target_hosts.remove(host)
            target_hosts.insert(0, host)
            break

    for host in target_hosts:
        # 1. Запрашиваем Master Playlist (variant.m3u8) для вычитывания внутренних дорожек
        master_url = f"{host}{slug}/variant.m3u8"
        try:
            r = requests.get(master_url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and "#EXTM3U" in r.text:
                # Извлекаем все доступные суб-потоки из ответа сервера
                tracks = re.findall(r'([\w_.-]+(?:/playlist\.m3u8|\.m3u8))', r.text)
                if tracks:
                    # Выбираем Full HD (1080p), если есть, иначе первую доступную
                    best_track = next((t for t in tracks if "1920x1080" in t), tracks[0])
                    resolved_url = f"{host}{slug}/{best_track}" if not best_track.startswith("http") else best_track
                    print(f"  [Этап 0 - Опрос] {slug} -> Манифест отдан! Поток: {best_track}")
                    return resolved_url
                return master_url
        except requests.RequestException:
            pass

        # 2. Если манифест не ответил, делаем перебор явных эндпоинтов (HEAD)
        for ep in ENDPOINTS:
            direct_url = f"{host}{slug}/{ep}"
            try:
                r = requests.head(direct_url, headers=HEADERS, timeout=TIMEOUT)
                if r.status_code == 200:
                    print(f"  [Этап 0 - Прямой] {slug} -> {ep} (HTTP 200)")
                    return direct_url
            except requests.RequestException:
                pass

    return None

def step_0_repair_raw_list(raw_input_text):
    print("\n=== [ ЭТАП 0: Восстановление оборванных URL и опрос узлов ] ===")
    results = {}
    lines = [l.strip() for l in raw_input_text.strip().split('\n') if l.strip()]
    
    current_title = "Неизвестный канал"
    for line in lines:
        if line.startswith("#EXTINF"):
            current_title = line.split(",")[-1].strip()
        elif "CH_" in line:
            resolved_url = inspect_and_resolve_node(line)
            if resolved_url:
                results[current_title] = resolved_url
            else:
                print(f"  [Этап 0 - FAIL] Не удалось восстановить: {current_title}")
                
    return results

# -----------------------------------------------------------------------------
# ЭТАП 1: Сканирование словаря недостающих каналов (CHANNELS_TO_SCAN)
# -----------------------------------------------------------------------------
def step_1_dictionary_scan(existing_results, channels_to_scan):
    print("\n=== [ ЭТАП 1: Полное сканирование словаря CHANNELS_TO_SCAN ] ===")
    results = dict(existing_results)

    for title, slug in channels_to_scan.items():
        if title in results:
            continue  # Ужe восстановлен на Этапе 0

        dummy_line = f"http://a3285272783-s80718.cdn.ngenix.net/hls/{slug}/"
        resolved_url = inspect_and_resolve_node(dummy_line)
        if resolved_url:
            results[title] = resolved_url
            print(f"  [Этап 1 - OK] {title} ({slug}) -> Найдено!")
        else:
            print(f"  [Этап 1 - FAIL] {title} ({slug}) -> Нет ответа")

    return results

# -----------------------------------------------------------------------------
# ЭТАП 2: Загрузка EPG, обогащение tvg-id и дельта-сканирование
# -----------------------------------------------------------------------------
def step_2_epg_enrichment_and_delta(channels_map):
    print("\n=== [ ЭТАП 2: Парсинг EPG (xml.gz), сопоставление и досканирование ] ===")
    epg_database = {}

    for epg_url in EPG_URLS:
        print(f"  Скачивание EPG: {epg_url} ...")
        try:
            r = requests.get(epg_url, timeout=10)
            if r.status_code == 200:
                xml_data = gzip.decompress(r.content)
                root = ET.fromstring(xml_data)
                
                for channel in root.findall("channel"):
                    cid = channel.get("id")
                    for name_elem in channel.findall("display-name"):
                        cname = name_elem.text.strip() if name_elem.text else ""
                        if cname:
                            epg_database[cname.lower()] = cid
                            
                print(f"  Успешно загружено EPG: {len(epg_database)} каналов в базе.")
                break
        except Exception as e:
            print(f"  Ошибка загрузки EPG ({epg_url}): {e}")

    final_playlist_items = []
    
    # Привязка tvg-id к найденным каналам
    for title, url in channels_map.items():
        tvg_id = epg_database.get(title.lower(), "")
        tvg_attr = f' tvg-id="{tvg_id}"' if tvg_id else ''
        
        extinf = f'#EXTINF:-1{tvg_attr} tvg-name="{title}",{title}'
        final_playlist_items.append((extinf, url))

    return final_playlist_items

# -----------------------------------------------------------------------------
# ФОРМИРОВАНИЕ ИТОГОВОГО M3U
# -----------------------------------------------------------------------------
def generate_m3u(playlist_items, output_filename="Peter_I_output.m3u"):
    epg_header_str = ' url-tvg="' + ', '.join(EPG_URLS) + '"'
    lines = [f"#EXTM3U{epg_header_str}\n"]

    for extinf, url in playlist_items:
        lines.append(extinf)
        lines.append(url)
        lines.append("")

    content = "\n".join(lines)
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(content)
        
    print(f"\n[ГОТОВО] Итоговый плейлист успешно сохранен в файл: {output_filename}")

# -----------------------------------------------------------------------------
# ТОЧКА ВХОДА
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Пример сырого обрывочного ввода для Этапа 0
    RAW_INPUT = """
    #EXTINF:-1,.Red
    a3285272783-s80718.cdn.ngenix.net/hls/CH_REDHD/video_..
    #EXTINF:-1,КИНОКОМЕДИЯ
    a3285272783-s80718.cdn.ngenix.net/hls//CH_KINOKOMEDIY..
    #EXTINF:-1,FOX
    a3285272783-s80718.cdn.ngenix.net//hls//CH_FOX//video..
    #EXTINF:-1,Удар HD
    a787201481-s80718.cdn.ngenix.net/hls/CH_UDARHD/video_..
    """

    # Пример дополнительного словаря для Этапа 1
    CHANNELS_TO_SCAN = {
        "Матч Премьер": "CH_MATCHPREMIER",
        "Кинохит": "CH_KINOHIT",
        "Еврокино": "CH_EVROKINO"
    }

    # Запуск конвейера
    recovered_map = step_0_repair_raw_list(RAW_INPUT)
    full_map = step_1_dictionary_scan(recovered_map, CHANNELS_TO_SCAN)
    final_items = step_2_epg_enrichment_and_delta(full_map)
    
    generate_m3u(final_items)
