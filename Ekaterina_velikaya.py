# ================================================================
#   CANON + SCALA NGENIX FINDER
#   Версия 3.9.0 — Высокопроизводительный валидатор HLS-потоков
# ================================================================

import time
import re
from typing import Dict, Tuple, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    requests = None


# -------------------------------
# Словари сопоставления (CHANNEL_META)
# -------------------------------

CHANNEL_META = {
    # ------------------------------------------------------------
    #  ФЕДЕРАЛЬНЫЕ
    # ------------------------------------------------------------
    "perviy": ("Первый канал", "Федеральные"),
    "rossiya_1": ("Россия 1", "Федеральные"),
    "match_tv": ("Матч ТВ", "Федеральные"),
    "ntv": ("НТВ", "Федеральные"),
    "pyatyi": ("Пятый канал", "Федеральные"),
    "rossiya_k": ("Россия К", "Федеральные"),
    "rossiya_24": ("Россия 24", "Федеральные"),
    "karusel": ("Карусель", "Федеральные"),
    "otr": ("ОТР", "Федеральные"),
    "tvc": ("ТВ Центр", "Федеральные"),
    "rentv": ("РЕН ТВ", "Федеральные"),
    "spas": ("Спас", "Федеральные"),
    "sts": ("СТС", "Федеральные"),
    "domashniy": ("Домашний", "Федеральные"),
    "tv3": ("ТВ-3", "Федеральные"),
    "pyatnica": ("Пятница!", "Федеральные"),
    "zvezda": ("Звезда", "Федеральные"),
    "mir": ("Мир", "Федеральные"),
    "tnt": ("ТНТ", "Федеральные"),
    "muz_tv": ("Муз-ТВ", "Федеральные"),
}

# ------------------------------------------------------------
    #  ПРИРОДА / WILDLIFE / TRAVEL / SCIENCE
    # ------------------------------------------------------------
    "wild_nature": ("Wild Nature", "Природа"),
    "nature_hd": ("Nature HD", "Природа"),
    "planet_earth_hd": ("Planet Earth HD", "Природа"),
    "wildlife_tv": ("Wildlife TV", "Природа"),
    "ocean_tv": ("Ocean TV", "Природа"),
    "zhivaya_planeta": ("Живая Планета", "Природа"),
    "moya_planeta": ("Моя Планета", "Природа"),
    "nauka": ("Наука", "Природа"),
    "techno_24": ("Techno 24", "Природа"),
    "travelxp": ("TravelXP", "Природа"),
    "travel_plus": ("Travel+", "Природа"),
    "natgeo": ("National Geographic", "Природа"),
    "natgeo_wild": ("NatGeo Wild", "Природа"),
    "discovery_channel": ("Discovery Channel", "Природа"),
    "discovery_science": ("Discovery Science", "Природа"),
    "discovery_turbo": ("Discovery Turbo", "Природа"),
    "discovery_world": ("Discovery World", "Природа"),
    "animal_planet": ("Animal Planet", "Природа"),
    "history_channel": ("History Channel", "Природа"),
    "viasat_nature": ("Viasat Nature", "Природа"),
    "viasat_explore": ("Viasat Explore", "Природа"),
    "viasat_history": ("Viasat History", "Природа"),
    "travel_channel": ("Travel Channel", "Природа"),
    "hgtv": ("HGTV", "Природа"),
    "fine_living": ("Fine Living", "Природа"),
    "myzen": ("MyZen TV", "Природа"),
}

# ------------------------------------------------------------
    #  TV1000 — ВСЯ ЛИНЕЙКА
    # ------------------------------------------------------------
    "tv1000": ("TV1000", "Кино"),
    "tv1000_action": ("TV1000 Action", "Кино"),
    "tv1000_russian": ("TV1000 Русское кино", "Кино"),
    "tv1000_world": ("TV1000 World", "Кино"),
    "tv1000_premium": ("TV1000 Premium", "Кино"),
    "tv1000_thriller": ("TV1000 Thriller", "Кино"),
    "tv1000_dark": ("TV1000 Dark", "Кино"),
    "tv1000_horror": ("TV1000 Horror", "Кино"),
    "tv1000_comedy": ("TV1000 Comedy", "Кино"),
    "tv1000_family": ("TV1000 Family", "Кино"),
    "tv1000_romance": ("TV1000 Romance", "Кино"),
    "tv1000_adventure": ("TV1000 Adventure", "Кино"),
    "tv1000_classic": ("TV1000 Classic", "Кино"),

    # Дополнительные редкие варианты, встречающиеся в IPTV‑плейлистах
    "tv1000_megahit": ("TV1000 Megahit", "Кино"),
    "tv1000_premiere": ("TV1000 Premiere", "Кино"),
    "tv1000_serial": ("TV1000 Serial", "Кино"),
    "tv1000_hd": ("TV1000 HD", "Кино"),
    "tv1000_plus": ("TV1000+", "Кино"),
}

# ------------------------------------------------------------
    #  ДЕТСКИЕ КАНАЛЫ (Россия + международные)
    # ------------------------------------------------------------

    # Российские детские
    "ani": ("Ani", "Детские"),
    "tlum_hd": ("Tlum HD", "Детские"),
    "mult": ("Мульт", "Детские"),
    "mult_music": ("Мульт Музыка", "Детские"),
    "karusel": ("Карусель", "Детские"),
    "sts_kids": ("СТС Kids", "Детские"),

    # Французские / европейские детские
    "tiji": ("TiJi", "Детские"),
    "gulli": ("Gulli", "Детские"),

    # Международные детские
    "cartoon_network": ("Cartoon Network", "Детские"),
    "boomerang": ("Boomerang", "Детские"),
    "nickelodeon": ("Nickelodeon", "Детские"),
    "nick_jr": ("Nick Jr", "Детские"),
    "nickelodeon_hd": ("Nickelodeon HD", "Детские"),
    "nicktoons": ("NickToons", "Детские"),

    # Disney / Baby / JimJam
    "disney": ("Disney Channel", "Детские"),
    "baby_tv": ("Baby TV", "Детские"),
    "jimjam": ("JimJam", "Детские"),

    # Дополнительные детские, встречающиеся в IPTV‑плейлистах
    "duck_tv": ("Duck TV", "Детские"),
    "fixiki": ("Фиксики", "Детские"),
    "kids_time": ("Kids Time", "Детские"),
    "kids_co": ("KidsCo", "Детские"),
    "minimax": ("Minimax", "Детские"),
    "panda_tv": ("Panda TV", "Детские"),
    "happy_kids": ("Happy Kids", "Детские"),
    "junior_music": ("Junior Music", "Детские"),
    "cartoonito": ("Cartoonito", "Детские"),
    "super_wings": ("Super Wings TV", "Детские"),
    "lego_tv": ("LEGO TV", "Детские"),

    # Азиатские детские (редко встречаются, но есть в русских плейлистах)
    "animax": ("Animax", "Детские"),
    "pogo": ("Pogo", "Детские"),
    "hungama": ("Hungama", "Детские"),
}

# ------------------------------------------------------------
    #  СПОРТИВНЫЕ КАНАЛЫ (Россия + международные)
    # ------------------------------------------------------------

    # Матч! (вся линейка)
    "match_tv": ("Матч ТВ", "Спорт"),
    "match_arena": ("Матч! Арена", "Спорт"),
    "match_igra": ("Матч! Игра", "Спорт"),
    "match_boec": ("Матч! Боец", "Спорт"),
    "match_strana": ("Матч! Страна", "Спорт"),
    "match_planeta": ("Матч! Планета", "Спорт"),
    "match_premier": ("Матч! Премьер", "Спорт"),
    "match_futbol_1": ("Матч! Футбол 1", "Спорт"),
    "match_futbol_2": ("Матч! Футбол 2", "Спорт"),
    "match_futbol_3": ("Матч! Футбол 3", "Спорт"),

    # КХЛ
    "khl": ("КХЛ", "Спорт"),
    "khl_prime": ("КХЛ Prime", "Спорт"),
    "khl_world": ("КХЛ World", "Спорт"),

    # Старт (вся линейка)
    "start": ("Старт", "Спорт"),
    "start_basket": ("Старт Баскет", "Спорт"),
    "start_triumf": ("Старт Триумф", "Спорт"),
    "start_hockey": ("Старт Хоккей", "Спорт"),

    # Единоборства
    "udar": ("Удар", "Спорт"),
    "boxtv": ("Бокс ТВ", "Спорт"),
    "ufc_channel": ("UFC Channel", "Спорт"),
    "fightbox": ("FightBox", "Спорт"),
    "mma_tv": ("MMA TV", "Спорт"),
    "wbc_fight": ("WBC Fight", "Спорт"),

    # Международные спортивные
    "eurosport_1": ("Eurosport 1", "Спорт"),
    "eurosport_2": ("Eurosport 2", "Спорт"),
    "fastnfunbox": ("Fast&FunBox", "Спорт"),
    "extreme_sports": ("Extreme Sports", "Спорт"),
    "xsport": ("XSport", "Спорт"),
    "fuel_tv": ("Fuel TV", "Спорт"),
    "trace_sport_stars": ("Trace Sport Stars", "Спорт"),

    # Футбол / Лиги / Спорт‑пакеты
    "premier_league_tv": ("Premier League TV", "Спорт"),
    "laliga_tv": ("LaLiga TV", "Спорт"),
    "bundesliga_tv": ("Bundesliga TV", "Спорт"),
    "serie_a_tv": ("Serie A TV", "Спорт"),
    "liga_tv": ("Liga TV", "Спорт"),

    # Экстрим / гонки / авто‑спорт
    "motorsport_tv": ("Motorsport TV", "Спорт"),
    "nascar_tv": ("NASCAR TV", "Спорт"),
    "f1_tv": ("Formula 1 TV", "Спорт"),
    "fifa_tv": ("FIFA TV", "Спорт"),
    "nhl_tv": ("NHL TV", "Спорт"),
    "nba_tv": ("NBA TV", "Спорт"),
    "nfl_tv": ("NFL TV", "Спорт"),

    # Дополнительные спортивные каналы, встречающиеся в российских IPTV‑плейлистах
    "sport_plus": ("Спорт Плюс", "Спорт"),
    "sport_hd": ("Спорт HD", "Спорт"),
    "sport_mania": ("Sport Mania", "Спорт"),
    "sport_1": ("Sport 1", "Спорт"),
    "sport_2": ("Sport 2", "Спорт"),
    "sport_3": ("Sport 3", "Спорт"),
    "fight_sports": ("Fight Sports", "Спорт"),
    "boxing_world": ("Boxing World", "Спорт"),
    "hockey_world": ("Hockey World", "Спорт"),
    "football_world": ("Football World", "Спорт"),

}

# ------------------------------------------------------------
    #  КИНОКАНАЛЫ (Россия + международные)
    # ------------------------------------------------------------

    # Amedia
    "amedia_1": ("Amedia 1", "Кино"),
    "amedia_2": ("Amedia 2", "Кино"),
    "amedia_premium_hd": ("Amedia Premium HD", "Кино"),
    "amedia_hit": ("Amedia Hit", "Кино"),

    # FilmBox
    "filmbox": ("FilmBox", "Кино"),
    "filmbox_arthouse": ("FilmBox Arthouse", "Кино"),
    "filmbox_action": ("FilmBox Action", "Кино"),
    "filmbox_family": ("FilmBox Family", "Кино"),
    "filmbox_plus": ("FilmBox Plus", "Кино"),

    # Viju (вся линейка)
    "viju_comedy": ("Viju Comedy", "Кино"),
    "viju_megahit": ("Viju Megahit", "Кино"),
    "viju_premiere": ("Viju Premiere", "Кино"),
    "viju_serial": ("Viju Serial", "Кино"),
    "viju_thriller": ("Viju Thriller", "Кино"),
    "viju_dark": ("Viju Dark", "Кино"),
    "viju_horror": ("Viju Horror", "Кино"),

    # AMC / Hollywood / Fear / Thriller
    "amc": ("AMC", "Кино"),
    "amc_thriller": ("AMC Thriller", "Кино"),
    "amc_fear": ("AMC Fear", "Кино"),
    "hollywood_hd": ("Hollywood HD", "Кино"),
    "hollywood_classic": ("Hollywood Classic", "Кино"),

    # Российские кино‑каналы
    "dom_kino": ("Дом Кино", "Кино"),
    "dom_kino_premium_hd": ("Дом Кино Премиум HD", "Кино"),
    "evrokino": ("Еврокино", "Кино"),
    "illusion_plus": ("Иллюзион+", "Кино"),
    "mir_seriala": ("Мир сериала", "Кино"),
    "tv_xxi": ("ТВ XXI", "Кино"),
    "365_dney_tv": ("365 дней ТВ", "Кино"),
    "galaxy": ("Galaxy", "Кино"),
    "kino_tv": ("Кино ТВ", "Кино"),
    "kinomix": ("Киномикс", "Кино"),
    "kinohit": ("Кинохит", "Кино"),
    "kinopokaz": ("Кинопоказ", "Кино"),

    # Start (вся линейка)
    "start": ("Start", "Кино"),
    "start_megahit": ("Start Megahit", "Кино"),
    "start_premiere": ("Start Premiere", "Кино"),
    "start_serial": ("Start Serial", "Кино"),

    # Paramount / Sony / Universal / AXN / SyFy
    "paramount_channel": ("Paramount Channel", "Кино"),
    "paramount_comedy": ("Paramount Comedy", "Кино"),
    "sony_channel": ("Sony Channel", "Кино"),
    "sony_turbo": ("Sony Turbo", "Кино"),
    "universal_channel": ("Universal Channel", "Кино"),
    "axn": ("AXN", "Кино"),
    "syfy": ("SyFy", "Кино"),

    # Epic Drama
    "epic_drama": ("Epic Drama", "Кино"),

    # Дополнительные кино‑каналы, встречающиеся в российских IPTV‑плейлистах
    "kino_1": ("Кино 1", "Кино"),
    "kino_2": ("Кино 2", "Кино"),
    "kino_3": ("Кино 3", "Кино"),
    "kino_premium": ("Кино Премиум", "Кино"),
    "kino_platinum": ("Кино Платинум", "Кино"),
    "kino_family": ("Кино Семейный", "Кино"),
    "kino_comedy": ("Кино Комедия", "Кино"),
    "kino_action": ("Кино Экшен", "Кино"),
    "kino_thriller": ("Кино Триллер", "Кино"),
    "kino_romance": ("Кино Роман", "Кино"),
    "kino_adventure": ("Кино Приключения", "Кино"),
    "kino_classic": ("Кино Классика", "Кино"),

    # Редкие международные кино‑каналы
    "axn_black": ("AXN Black", "Кино"),
    "axn_white": ("AXN White", "Кино"),
    "tcm": ("TCM", "Кино"),
    "amc_movies": ("AMC Movies", "Кино"),
    "mgm": ("MGM Channel", "Кино"),
    "warner_tv": ("Warner TV", "Кино"),
    "hbo": ("HBO", "Кино"),
    "hbo_2": ("HBO 2", "Кино"),
    "hbo_3": ("HBO 3", "Кино"),
}

# ------------------------------------------------------------
    #  УЖАСЫ / ТРИЛЛЕРЫ / МИСТИКА
    # ------------------------------------------------------------

    # Российские и СНГ‑каналы ужасов
    "nst": ("НСТ — Настоящее Страшное Телевидение", "Ужасы"),
    "kinoujas": ("Киноужас", "Ужасы"),
    "kinomystic": ("Киномистика", "Ужасы"),
    "kinoterrory": ("Кино Террор", "Ужасы"),
    "dark_tv": ("Dark TV", "Ужасы"),
    "horror_tv": ("Horror TV", "Ужасы"),

    # Trash / Scream / Cult / Indie
    "trash_horror": ("Trash Horror", "Ужасы"),
    "scream_horror": ("Scream Horror", "Ужасы"),
    "cult_horror": ("Cult Horror", "Ужасы"),
    "indie_horror": ("Indie Horror", "Ужасы"),
    "dark_cinema": ("Dark Cinema", "Ужасы"),
    "mystic_tv": ("Mystic TV", "Ужасы"),

    # Thriller‑каналы
    "thriller_tv": ("Thriller TV", "Ужасы"),
    "thriller_box": ("Thriller Box", "Ужасы"),
    "crime_thriller": ("Crime Thriller", "Ужасы"),

    # Viju Horror / Dark / Thriller
    "viju_thriller": ("Viju Thriller", "Ужасы"),
    "viju_dark": ("Viju Dark", "Ужасы"),
    "viju_horror": ("Viju Horror", "Ужасы"),

    # AMC Horror / Fear / Thriller
    "amc_thriller": ("AMC Thriller", "Ужасы"),
    "amc_fear": ("AMC Fear", "Ужасы"),
    "amc_dark": ("AMC Dark", "Ужасы"),

    # TV1000 Horror / Thriller / Dark (часть уже была, но повторяем для развёрнутого блока)
    "tv1000_horror": ("TV1000 Horror", "Ужасы"),
    "tv1000_thriller": ("TV1000 Thriller", "Ужасы"),
    "tv1000_dark": ("TV1000 Dark", "Ужасы"),

    # Fox Crime / Fox Thriller
    "fox_crime": ("Fox Crime", "Ужасы"),
    "fox_thriller": ("Fox Thriller", "Ужасы"),

    # Дополнительные международные каналы ужасов
    "fear_channel": ("Fear Channel", "Ужасы"),
    "terror_tv": ("Terror TV", "Ужасы"),
    "horror_box": ("Horror Box", "Ужасы"),
    "nightmare_tv": ("Nightmare TV", "Ужасы"),
    "blood_tv": ("Blood TV", "Ужасы"),
    "shadow_tv": ("Shadow TV", "Ужасы"),
    "phantom_tv": ("Phantom TV", "Ужасы"),

    # Мистика / паранормальное / эзотерика
    "paranormal_tv": ("Paranormal TV", "Ужасы"),
    "mystery_tv": ("Mystery TV", "Ужасы"),
    "occult_tv": ("Occult TV", "Ужасы"),
    "ghost_tv": ("Ghost TV", "Ужасы"),
    "supernatural_tv": ("Supernatural TV", "Ужасы"),

    # Редкие каналы, встречающиеся в русских IPTV‑плейлистах
    "horror_plus": ("Horror+", "Ужасы"),
    "thriller_plus": ("Thriller+", "Ужасы"),
    "dark_plus": ("Dark+", "Ужасы"),
    "mystic_plus": ("Mystic+", "Ужасы"),
    "fear_plus": ("Fear+", "Ужасы"),
}

# ------------------------------------------------------------
    #  ПОЗНАВАТЕЛЬНЫЕ / ДОКУМЕНТАЛЬНЫЕ
    # ------------------------------------------------------------

    # Discovery (вся линейка)
    "discovery_channel": ("Discovery Channel", "Познавательные"),
    "discovery_science": ("Discovery Science", "Познавательные"),
    "discovery_turbo": ("Discovery Turbo", "Познавательные"),
    "discovery_world": ("Discovery World", "Познавательные"),
    "discovery_id": ("Investigation Discovery", "Познавательные"),
    "discovery_civilization": ("Discovery Civilization", "Познавательные"),

    # National Geographic
    "natgeo": ("National Geographic", "Познавательные"),
    "natgeo_wild": ("NatGeo Wild", "Познавательные"),

    # History / H2
    "history_channel": ("History Channel", "Познавательные"),
    "history_2": ("History 2", "Познавательные"),

    # Viasat Documentary
    "viasat_nature": ("Viasat Nature", "Познавательные"),
    "viasat_explore": ("Viasat Explore", "Познавательные"),
    "viasat_history": ("Viasat History", "Познавательные"),

    # DocuBox / Fast&FunBox / FightBox / FashionBox
    "docubox": ("DocuBox", "Познавательные"),
    "fastnfunbox": ("Fast&FunBox", "Познавательные"),
    "fightbox": ("FightBox", "Познавательные"),
    "fashionbox": ("FashionBox", "Познавательные"),

    # TravelXP / Travel Channel / Fine Living / HGTV
    "travelxp": ("TravelXP", "Познавательные"),
    "travel_channel": ("Travel Channel", "Познавательные"),
    "fine_living": ("Fine Living", "Познавательные"),
    "hgtv": ("HGTV", "Познавательные"),

    # Российские познавательные
    "moya_planeta": ("Моя Планета", "Познавательные"),
    "zhivaya_planeta": ("Живая Планета", "Познавательные"),
    "ocean_tv": ("Ocean TV", "Познавательные"),
    "nauka": ("Наука", "Познавательные"),
    "techno_24": ("Техно 24", "Познавательные"),
    "nostalgia": ("Ностальгия", "Познавательные"),

    # Охота / рыбалка / оружие
    "hunterfisher": ("Охотник и рыболов", "Познавательные"),
    "rybolov": ("Рыболов", "Познавательные"),
    "oruzhie": ("Оружие", "Познавательные"),
    "hunt_world": ("Hunt World", "Познавательные"),
    "fish_world": ("Fish World", "Познавательные"),

    # Научные / образовательные
    "da_vinci": ("Da Vinci", "Познавательные"),
    "myzen": ("MyZen TV", "Познавательные"),
    "planet_earth_hd": ("Planet Earth HD", "Познавательные"),
    "wildlife_tv": ("Wildlife TV", "Познавательные"),
    "nature_hd": ("Nature HD", "Познавательные"),

    # Дополнительные документальные каналы
    "science_360": ("Science 360", "Познавательные"),
    "world_documentary": ("World Documentary", "Познавательные"),
    "geo_tv": ("GEO TV", "Познавательные"),
    "explorer_tv": ("Explorer TV", "Познавательные"),
    "adventure_tv": ("Adventure TV", "Познавательные"),
    "planet_hd": ("Planet HD", "Познавательные"),
    "earth_hd": ("Earth HD", "Познавательные"),
    "wild_hd": ("Wild HD", "Познавательные"),
    "space_tv": ("Space TV", "Познавательные"),
    "universe_tv": ("Universe TV", "Познавательные"),
}

































def generate_slug_candidates(key: str) -> List[str]:
    candidates = [key]
    custom_map = {
        "perviy": ["1tv", "ch_1tv", "pervy", "perviy_kanal"],
        "rossiya_1": ["ch_russia1", "rossiya1", "russia1", "russia_1"],
        "match_tv": ["ch_matchtv", "match", "matchtv"],
        "ntv": ["ch_ntv", "ntv_hd"],
        "pyatyi": ["ch_5tv", "5tv", "5kanal"],
        "rossiya_k": ["ch_russiak", "rossiya_kultura", "kultura"],
        "rossiya_24": ["ch_russia24", "rossiya24", "russia24"],
        "karusel": ["ch_karusel", "karusel_tv"],
        "otr": ["ch_otr", "otr_tv"],
        "tvc": ["ch_tvc", "tvcentr", "tv_center"],
        "rentv": ["ch_rentv", "ren", "ren_tv"],
        "spas": ["ch_spas", "spas_tv"],
        "sts": ["ch_sts", "ctc"],
        "domashniy": ["ch_domashniy", "domashny", "domashniy_2"],
        "tv3": ["ch_tv3", "tv_3"],
        "pyatnica": ["ch_friday", "friday", "pyatnitsa"],
        "zvezda": ["ch_zvezda", "zvezda_tv"],
        "mir": ["ch_mir", "mirtv"],
        "tnt": ["ch_tnt", "tnt_hd"],
        "muz_tv": ["ch_muztv", "muz", "muztv"]
    }
    
    if key in custom_map:
        candidates.extend(custom_map[key])
        
    return list(dict.fromkeys(candidates))


# -------------------------------
# Улучшенный валидатор HLS-потока
# -------------------------------

def probe_url(session: Any, url: str, timeout: float = 2.5, user_agent: str = "HlsWinkPlayer") -> Tuple[bool, int, float, str]:
    if session is None:
        return False, 0, 0.0, "requests_not_installed"
        
    headers = {"User-Agent": user_agent}
    start = time.time()
    
    try:
        # Использование контекстного менеджера с сессией
        with session.get(url, headers=headers, stream=True, timeout=timeout) as resp:
            latency = (time.time() - start) * 1000.0
            
            # Принимаем статус-коды 200 и 206
            if resp.status_code in (200, 206):
                # Читаем первые 512 байт для валидации заголовков HLS
                chunk = resp.raw.read(512).decode('utf-8', errors='ignore')
                if "#EXTM3U" in chunk or "#EXT-X-" in chunk:
                    return True, resp.status_code, latency, ""
                else:
                    return False, resp.status_code, latency, "not_a_valid_m3u8_payload"
            
            return False, resp.status_code, latency, f"HTTP {resp.status_code}"
            
    except Exception as e:
        latency = (time.time() - start) * 1000.0
        return False, 0, latency, str(e)


# -------------------------------
# Оптимизированный движок
# -------------------------------

def scan_ngenix_node(
    cdn_host: str = "s70378.cdn.ngenix.net", 
    meta_dict: Dict[str, Tuple[str, str]] = CHANNEL_META,
    start_index: int = 1,
    group_override: str = "Дополнительные Эфирные ТВ Плюс",
    timeout: float = 2.0,
    max_workers: int = 20
):
    print(f"=== [СКАЛА] Запуск профессионального валидатора: {cdn_host} ===")
    
    tasks = []
    path_templates = [
        "/{slug}/2/index.m3u8",
        "/{slug}/1/index.m3u8",
        "/hls/{slug}/variant.m3u8"
    ]

    for key, (title, group) in meta_dict.items():
        slugs = generate_slug_candidates(key)
        for slug in slugs:
            for path_tmpl in path_templates:
                url = f"https://{cdn_host}" + path_tmpl.format(slug=slug)
                tasks.append({
                    "key": key,
                    "title": title,
                    "group": group_override if group_override else group,
                    "slug": slug,
                    "url": url
                })

    found_channels = []
    found_keys = set()  # Множество O(1) для быстрого поиска дубликатов
    scanned_logs = []

    # Используемrequests.Session для повторного использования TCP-соединений
    session = requests.Session() if requests else None

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(probe_url, session, item["url"], timeout): item 
                for item in tasks
            }

            for future in as_completed(future_map):
                item = future_map[future]
                ok, status, latency, err = future.result()
                
                log_entry = {
                    "title": item["title"],
                    "key": item["key"],
                    "url": item["url"],
                    "ok": ok,
                    "status": status,
                    "latency": latency,
                    "error": err
                }
                scanned_logs.append(log_entry)

                if ok and item["key"] not in found_keys:
                    found_keys.add(item["key"])
                    found_channels.append(item)
                    print(f"[НАЙДЕН HLS] {item['title']} -> {item['url']} ({int(latency)} ms)")

    finally:
        if session:
            session.close()

    # Сортировка по порядку CHANNEL_META
    meta_keys = list(meta_dict.keys())
    found_channels.sort(key=lambda x: meta_keys.index(x["key"]))

    # 1. Запись отчета СКАЛА
    with open("ngenix_report.txt", "w", encoding="utf-8") as f:
        f.write("СКАЛА кант Вер 3.9.0 — NGENIX FINDER REPORT\n")
        f.write("=========================================\n")
        f.write(f"Проверено комбинаций URL: {len(tasks)}\n")
        f.write(f"Успешно найдено каналов: {len(found_channels)}\n")
        f.write("=========================================\n\n")

        for log in scanned_logs:
            tag = "OK" if log["ok"] else "FAIL"
            f.write(f"[СКАЛА] [{tag}] Канал: {log['title']} | Key: {log['key']}\n")
            f.write(f"        URL: {log['url']}\n")
            f.write(f"        Статус: {log['status']} | Latency: {int(log['latency'])} ms | Error: {log['error']}\n\n")

    # 2. Выгрузка M3U8
    with open("ngenix_found.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, ch in enumerate(found_channels, start=start_index):
            f.write(f'#EXTINF:-1 tvg-id="{ch["key"]}" group-title="{ch["group"]}",{i}. {ch["title"]}\n')
            f.write(f'{ch["url"]}\n')

    print("\n[СКАЛА] Поиск завершён успешно!")
    print(" — Отчёт: ngenix_report.txt")
    print(" — Сгенерирован рабочий M3U: ngenix_found.m3u")


if __name__ == "__main__":
    scan_ngenix_node(
        cdn_host="s70378.cdn.ngenix.net", 
        meta_dict=CHANNEL_META,
        start_index=1,
        group_override="Эфирные ТВ Плюс",
        timeout=2.5,
        max_workers=20
    )
