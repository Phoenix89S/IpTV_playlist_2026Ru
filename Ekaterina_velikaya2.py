# ================================================================
#   CANON + SCALA NGENIX FINDER
#   Версия 3.9.1 — Высокопроизводительный валидатор HLS-потоков
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
# Единый словарь сопоставления (CHANNEL_META)
# -------------------------------

CHANNEL_META = {
    #  ФЕДЕРАЛЬНЫЕ
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

    #  ПРИРОДА / WILDLIFE / TRAVEL / SCIENCE
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

    #  TV1000 — ВСЯ ЛИНЕЙКА
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
    "tv1000_megahit": ("TV1000 Megahit", "Кино"),
    "tv1000_premiere": ("TV1000 Premiere", "Кино"),
    "tv1000_serial": ("TV1000 Serial", "Кино"),
    "tv1000_hd": ("TV1000 HD", "Кино"),
    "tv1000_plus": ("TV1000+", "Кино"),

    #  ДЕТСКИЕ КАНАЛЫ
    "ani": ("Ani", "Детские"),
    "tlum_hd": ("Tlum HD", "Детские"),
    "mult": ("Мульт", "Детские"),
    "mult_music": ("Мульт Музыка", "Детские"),
    "sts_kids": ("СТС Kids", "Детские"),
    "tiji": ("TiJi", "Детские"),
    "gulli": ("Gulli", "Детские"),
    "cartoon_network": ("Cartoon Network", "Детские"),
    "boomerang": ("Boomerang", "Детские"),
    "nickelodeon": ("Nickelodeon", "Детские"),
    "nick_jr": ("Nick Jr", "Детские"),
    "nickelodeon_hd": ("Nickelodeon HD", "Детские"),
    "nicktoons": ("NickToons", "Детские"),
    "disney": ("Disney Channel", "Детские"),
    "baby_tv": ("Baby TV", "Детские"),
    "jimjam": ("JimJam", "Детские"),
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
    "animax": ("Animax", "Детские"),
    "pogo": ("Pogo", "Детские"),
    "hungama": ("Hungama", "Детские"),

    #  СПОРТИВНЫЕ КАНАЛЫ
    "match_arena": ("Матч! Арена", "Спорт"),
    "match_igra": ("Матч! Игра", "Спорт"),
    "match_boec": ("Матч! Боец", "Спорт"),
    "match_strana": ("Матч! Страна", "Спорт"),
    "match_planeta": ("Матч! Планета", "Спорт"),
    "match_premier": ("Матч! Премьер", "Спорт"),
    "match_futbol_1": ("Матч! Футбол 1", "Спорт"),
    "match_futbol_2": ("Матч! Футбол 2", "Спорт"),
    "match_futbol_3": ("Матч! Футбол 3", "Спорт"),
    "khl": ("КХЛ", "Спорт"),
    "khl_prime": ("КХЛ Prime", "Спорт"),
    "khl_world": ("КХЛ World", "Спорт"),
    "start": ("Старт", "Спорт"),
    "start_basket": ("Старт Баскет", "Спорт"),
    "start_triumf": ("Старт Триумф", "Спорт"),
    "start_hockey": ("Старт Хоккей", "Спорт"),
    "udar": ("Удар", "Спорт"),
    "boxtv": ("Бокс ТВ", "Спорт"),
    "ufc_channel": ("UFC Channel", "Спорт"),
    "fightbox": ("FightBox", "Спорт"),
    "mma_tv": ("MMA TV", "Спорт"),
    "wbc_fight": ("WBC Fight", "Спорт"),
    "eurosport_1": ("Eurosport 1", "Спорт"),
    "eurosport_2": ("Eurosport 2", "Спорт"),
    "fastnfunbox": ("Fast&FunBox", "Спорт"),
    "extreme_sports": ("Extreme Sports", "Спорт"),
    "xsport": ("XSport", "Спорт"),
    "fuel_tv": ("Fuel TV", "Спорт"),
    "trace_sport_stars": ("Trace Sport Stars", "Спорт"),
    "premier_league_tv": ("Premier League TV", "Спорт"),
    "laliga_tv": ("LaLiga TV", "Спорт"),
    "bundesliga_tv": ("Bundesliga TV", "Спорт"),
    "serie_a_tv": ("Serie A TV", "Спорт"),
    "liga_tv": ("Liga TV", "Спорт"),
    "motorsport_tv": ("Motorsport TV", "Спорт"),
    "nascar_tv": ("NASCAR TV", "Спорт"),
    "f1_tv": ("Formula 1 TV", "Спорт"),
    "fifa_tv": ("FIFA TV", "Спорт"),
    "nhl_tv": ("NHL TV", "Спорт"),
    "nba_tv": ("NBA TV", "Спорт"),
    "nfl_tv": ("NFL TV", "Спорт"),
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

    #  КИНОКАНАЛЫ
    "amedia_1": ("Amedia 1", "Кино"),
    "amedia_2": ("Amedia 2", "Кино"),
    "amedia_premium_hd": ("Amedia Premium HD", "Кино"),
    "amedia_hit": ("Amedia Hit", "Кино"),
    "filmbox": ("FilmBox", "Кино"),
    "filmbox_arthouse": ("FilmBox Arthouse", "Кино"),
    "filmbox_action": ("FilmBox Action", "Кино"),
    "filmbox_family": ("FilmBox Family", "Кино"),
    "filmbox_plus": ("FilmBox Plus", "Кино"),
    "viju_comedy": ("Viju Comedy", "Кино"),
    "viju_megahit": ("Viju Megahit", "Кино"),
    "viju_premiere": ("Viju Premiere", "Кино"),
    "viju_serial": ("Viju Serial", "Кино"),
    "viju_thriller": ("Viju Thriller", "Кино"),
    "viju_dark": ("Viju Dark", "Кино"),
    "viju_horror": ("Viju Horror", "Кино"),
    "amc": ("AMC", "Кино"),
    "amc_thriller": ("AMC Thriller", "Кино"),
    "amc_fear": ("AMC Fear", "Кино"),
    "hollywood_hd": ("Hollywood HD", "Кино"),
    "hollywood_classic": ("Hollywood Classic", "Кино"),
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
    "start_megahit": ("Start Megahit", "Кино"),
    "start_premiere": ("Start Premiere", "Кино"),
    "start_serial": ("Start Serial", "Кино"),
    "paramount_channel": ("Paramount Channel", "Кино"),
    "paramount_comedy": ("Paramount Comedy", "Кино"),
    "sony_channel": ("Sony Channel", "Кино"),
    "sony_turbo": ("Sony Turbo", "Кино"),
    "universal_channel": ("Universal Channel", "Кино"),
    "axn": ("AXN", "Кино"),
    "syfy": ("SyFy", "Кино"),
    "epic_drama": ("Epic Drama", "Кино"),
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
    "axn_black": ("AXN Black", "Кино"),
    "axn_white": ("AXN White", "Кино"),
    "tcm": ("TCM", "Кино"),
    "amc_movies": ("AMC Movies", "Кино"),
    "mgm": ("MGM Channel", "Кино"),
    "warner_tv": ("Warner TV", "Кино"),
    "hbo": ("HBO", "Кино"),
    "hbo_2": ("HBO 2", "Кино"),
    "hbo_3": ("HBO 3", "Кино"),

    #  УЖАСЫ / ТРИЛЛЕРЫ / МИСТИКА
    "nst": ("НСТ — Настоящее Страшное Телевидение", "Ужасы"),
    "kinoujas": ("Киноужас", "Ужасы"),
    "kinomystic": ("Киномистика", "Ужасы"),
    "kinoterrory": ("Кино Террор", "Ужасы"),
    "dark_tv": ("Dark TV", "Ужасы"),
    "horror_tv": ("Horror TV", "Ужасы"),
    "trash_horror": ("Trash Horror", "Ужасы"),
    "scream_horror": ("Scream Horror", "Ужасы"),
    "cult_horror": ("Cult Horror", "Ужасы"),
    "indie_horror": ("Indie Horror", "Ужасы"),
    "dark_cinema": ("Dark Cinema", "Ужасы"),
    "mystic_tv": ("Mystic TV", "Ужасы"),
    "thriller_tv": ("Thriller TV", "Ужасы"),
    "thriller_box": ("Thriller Box", "Ужасы"),
    "crime_thriller": ("Crime Thriller", "Ужасы"),
    "amc_dark": ("AMC Dark", "Ужасы"),
    "fox_crime": ("Fox Crime", "Ужасы"),
    "fox_thriller": ("Fox Thriller", "Ужасы"),
    "fear_channel": ("Fear Channel", "Ужасы"),
    "terror_tv": ("Terror TV", "Ужасы"),
    "horror_box": ("Horror Box", "Ужасы"),
    "nightmare_tv": ("Nightmare TV", "Ужасы"),
    "blood_tv": ("Blood TV", "Ужасы"),
    "shadow_tv": ("Shadow TV", "Ужасы"),
    "phantom_tv": ("Phantom TV", "Ужасы"),
    "paranormal_tv": ("Paranormal TV", "Ужасы"),
    "mystery_tv": ("Mystery TV", "Ужасы"),
    "occult_tv": ("Occult TV", "Ужасы"),
    "ghost_tv": ("Ghost TV", "Ужасы"),
    "supernatural_tv": ("Supernatural TV", "Ужасы"),
    "horror_plus": ("Horror+", "Ужасы"),
    "thriller_plus": ("Thriller+", "Ужасы"),
    "dark_plus": ("Dark+", "Ужасы"),
    "mystic_plus": ("Mystic+", "Ужасы"),
    "fear_plus": ("Fear+", "Ужасы"),

    #  ПОЗНАВАТЕЛЬНЫЕ / ДОКУМЕНТАЛЬНЫЕ
    "discovery_id": ("Investigation Discovery", "Познавательные"),
    "discovery_civilization": ("Discovery Civilization", "Познавательные"),
    "history_2": ("History 2", "Познавательные"),
    "docubox": ("DocuBox", "Познавательные"),
    "fashionbox": ("FashionBox", "Познавательные"),
    "nostalgia": ("Ностальгия", "Познавательные"),
    "hunterfisher": ("Охотник и рыболов", "Познавательные"),
    "rybolov": ("Рыболов", "Познавательные"),
    "oruzhie": ("Оружие", "Познавательные"),
    "hunt_world": ("Hunt World", "Познавательные"),
    "fish_world": ("Fish World", "Познавательные"),
    "da_vinci": ("Da Vinci", "Познавательные"),
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

    #  КУЛИНАРИЯ / ЕДА
    "eda": ("Еда", "Кулинария"),
    "eda_hd": ("Еда HD", "Кулинария"),
    "kitchen_tv": ("Kitchen TV", "Кулинария"),
    "chef_tv": ("Chef TV", "Кулинария"),
    "my_cuisine": ("Моя Кухня", "Кулинария"),
    "kulinarnyi": ("Кулинарный", "Кулинария"),
    "kulinaria_hd": ("Кулинария HD", "Кулинария"),
    "food_network": ("Food Network", "Кулинария"),
    "gusto_tv": ("Gusto TV", "Кулинария"),
    "bon_appetit": ("Bon Appétit TV", "Кулинария"),
    "hgtv_food": ("HGTV Food", "Кулинария"),
    "viju_cooking": ("Viju Cooking", "Кулинария"),
    "viju_food": ("Viju Food", "Кулинария"),
    "cook_tv": ("Cook TV", "Кулинария"),
    "cook_hd": ("Cook HD", "Кулинария"),
    "chef_plus": ("Chef+", "Кулинария"),
    "kitchen_plus": ("Kitchen+", "Кулинария"),
    "taste_tv": ("Taste TV", "Кулинария"),
    "world_cuisine": ("World Cuisine", "Кулинария"),
    "gourmet_tv": ("Gourmet TV", "Кулинария"),
    "culinary_world": ("Culinary World", "Кулинария"),
    "sweet_tv": ("Sweet TV", "Кулинария"),
    "dessert_tv": ("Dessert TV", "Кулинария"),
    "baking_tv": ("Baking TV", "Кулинария"),
    "grill_tv": ("Grill TV", "Кулинария"),
    "bbq_tv": ("BBQ TV", "Кулинария"),
    "vegan_tv": ("Vegan TV", "Кулинария"),
    "healthy_food_tv": ("Healthy Food TV", "Кулинария"),

    #  МУЗЫКАЛЬНЫЕ КАНАЛЫ
    "bridge_tv": ("Bridge TV", "Музыка"),
    "bridge_hits": ("Bridge Hits", "Музыка"),
    "bridge_deluxe": ("Bridge Deluxe", "Музыка"),
    "bridge_classic": ("Bridge Classic", "Музыка"),
    "bridge_fresh": ("Bridge Fresh", "Музыка"),
    "mtv": ("MTV", "Музыка"),
    "mtv_live_hd": ("MTV Live HD", "Музыка"),
    "mtv_hits": ("MTV Hits", "Музыка"),
    "mtv_rocks": ("MTV Rocks", "Музыка"),
    "mtv_dance": ("MTV Dance", "Музыка"),
    "vh1": ("VH1", "Музыка"),
    "vh1_classic": ("VH1 Classic", "Музыка"),
    "mezzo": ("Mezzo", "Музыка"),
    "mezzo_live_hd": ("Mezzo Live HD", "Музыка"),
    "mezzo_classic": ("Mezzo Classic", "Музыка"),
    "russian_music_box": ("Russian Music Box", "Музыка"),
    "music_box_ru": ("Music Box Russia", "Музыка"),
    "music_box_hd": ("Music Box HD", "Музыка"),
    "music_box_ua": ("Music Box UA", "Музыка"),
    "europa_plus_tv": ("Europa Plus TV", "Музыка"),
    "music_first": ("Музыка Первого", "Музыка"),
    "soyuz": ("Союз", "Музыка"),
    "rutv": ("RU.TV", "Музыка"),
    "tnt_music": ("ТНТ Music", "Музыка"),
    "sts_love_music": ("СТС Love Music", "Музыка"),
    "zhara_tv": ("Жара ТВ", "Музыка"),
    "trace_urban": ("Trace Urban", "Музыка"),
    "trace_latin": ("Trace Latin", "Музыка"),
    "trace_africa": ("Trace Africa", "Музыка"),
    "trace_tropical": ("Trace Tropical", "Музыка"),
    "deluxe_music": ("Deluxe Music", "Музыка"),
    "unitv": ("UniTV", "Музыка"),
    "4fun_tv": ("4Fun TV", "Музыка"),
    "clubland_tv": ("Clubland TV", "Музыка"),
    "dance_tv": ("Dance TV", "Музыка"),
    "party_tv": ("Party TV", "Музыка"),
    "hit_tv": ("Hit TV", "Музыка"),
    "top_music": ("Top Music", "Музыка"),
    "global_music": ("Global Music", "Музыка"),
    "music_plus": ("Music+", "Музыка"),
    "music_360": ("Music 360", "Музыка"),
    "retro_music": ("Retro Music", "Музыка"),
    "classic_music": ("Classic Music", "Музыка"),
    "jazz_tv": ("Jazz TV", "Музыка"),
    "rock_tv": ("Rock TV", "Музыка"),
    "metal_tv": ("Metal TV", "Музыка"),
    "hiphop_tv": ("HipHop TV", "Музыка"),
    "rap_tv": ("Rap TV", "Музыка"),
    "pop_tv": ("Pop TV", "Музыка"),
    "kpop_tv": ("K‑Pop TV", "Музыка"),
    "edm_tv": ("EDM TV", "Музыка"),

    #  РЕГИОНАЛЬНЫЕ КАНАЛЫ РОССИИ
    "moskva_24": ("Москва 24", "Региональные"),
    "moskva_doverie": ("Москва Доверие", "Региональные"),
    "moskva_1": ("Москва 1", "Региональные"),
    "tvcentr_moscow": ("ТВ Центр Москва", "Региональные"),
    "peterburg_5": ("Санкт-Петербург 5", "Региональные"),
    "peterburg_78": ("78 канал", "Региональные"),
    "peterburg_otv": ("ОТВ Петербург", "Региональные"),
    "peterburg_len_tv": ("Лен ТВ", "Региональные"),
    "360": ("360 Подмосковье", "Региональные"),
    "chekhov_tv": ("Чехов ТВ", "Региональные"),
    "balashikha_tv": ("Балашиха ТВ", "Региональные"),
    "korolev_tv": ("Королёв ТВ", "Региональные"),
    "len_obl_tv": ("ЛенОбл ТВ", "Региональные"),
    "vsevolozhsk_tv": ("Всеволожск ТВ", "Региональные"),
    "tatarstan_tv": ("Татарстан ТВ", "Региональные"),
    "tnv": ("ТНВ", "Региональные"),
    "tnv_planeta": ("ТНВ Планета", "Региональные"),
    "kazan_first": ("Первый Казанский", "Региональные"),
    "bashkortostan_tv": ("Башкортостан ТВ", "Региональные"),
    "bst": ("БСТ", "Региональные"),
    "ufa_tv": ("Уфа ТВ", "Региональные"),
    "krasnodar_tv": ("Краснодар ТВ", "Региональные"),
    "kuban_24": ("Кубань 24", "Региональные"),
    "sochi_tv": ("Сочи ТВ", "Региональные"),
    "ekb_tv": ("Екатеринбург ТВ", "Региональные"),
    "ekb_4": ("4 канал Екатеринбург", "Региональные"),
    "ekb_otv": ("ОТВ Екатеринбург", "Региональные"),
    "novosibirsk_tv": ("Новосибирск ТВ", "Региональные"),
    "nsk_otv": ("ОТВ Новосибирск", "Региональные"),
    "samara_tv": ("Самара ТВ", "Региональные"),
    "samara_guberniya": ("Губерния Самара", "Региональные"),
    "perm_tv": ("Пермь ТВ", "Региональные"),
    "rifei_perm": ("Рифей Пермь", "Региональные"),
    "omsk_tv": ("Омск ТВ", "Региональные"),
    "omsk_12": ("12 канал Омск", "Региональные"),
    "tomsk_tv": ("Томск ТВ", "Региональные"),
    "tomsk_vest": ("Вести Томск", "Региональные"),
    "volgograd_tv": ("Волгоград ТВ", "Региональные"),
    "volgograd_1": ("Первый Волгоградский", "Региональные"),
    "vladivostok_tv": ("Владивосток ТВ", "Региональные"),
    "primorye_tv": ("Приморье ТВ", "Региональные"),
    "kaliningrad_tv": ("Калининград ТВ", "Региональные"),
    "balt_tv": ("Балтик ТВ", "Региональные"),
    "west_tv": ("Запад ТВ", "Региональные"),
    "kaliningrad_1": ("Первый Калининградский", "Региональные"),
    "chelyabinsk_tv": ("Челябинск ТВ", "Региональные"),
    "cheltv_31": ("31 канал Челябинск", "Региональные"),
    "irkutsk_tv": ("Иркутск ТВ", "Региональные"),
    "irkutsk_as_baikal": ("АС Байкал ТВ", "Региональные"),
    "khabarovsk_tv": ("Хабаровск ТВ", "Региональные"),
    "dv_tv": ("ДВ ТВ", "Региональные"),
    "yakutia_tv": ("Якутия 24", "Региональные"),
    "sakha_tv": ("Саха ТВ", "Региональные"),
    "karelia_tv": ("Карелия ТВ", "Региональные"),
    "ptz_tv": ("Петрозаводск ТВ", "Региональные"),
    "komi_tv": ("Коми ТВ", "Региональные"),
    "siktivkar_tv": ("Сыктывкар ТВ", "Региональные"),
    "mordovia_tv": ("Мордовия ТВ", "Региональные"),
    "mariel_tv": ("Марий Эл ТВ", "Региональные"),
    "udmurtia_tv": ("Удмуртия ТВ", "Региональные"),
    "chuvashia_tv": ("Чувашия ТВ", "Региональные"),
    "altai_tv": ("Алтай ТВ", "Региональные"),
    "barnaul_tv": ("Барнаул ТВ", "Региональные"),
    "kemerovo_tv": ("Кемерово ТВ", "Региональные"),
    "kuzbass_tv": ("Кузбасс ТВ", "Региональные"),
    "tyumen_tv": ("Тюмень ТВ", "Региональные"),
    "region_tyumen": ("Регион-Тюмень", "Региональные"),
    "otr_region": ("ОТР Регион", "Региональные"),
    "otr_local": ("ОТР Локальный", "Региональные"),
    "tvc_region": ("ТВЦ Регион", "Региональные"),
    "tvc_local": ("ТВЦ Локальный", "Региональные"),
    "city_tv": ("City TV", "Региональные"),
    "gorod_tv": ("Город ТВ", "Региональные"),
    "local_tv": ("Local TV", "Региональные"),
    "tnt_region": ("ТНТ Регион", "Региональные"),
    "sts_region": ("СТС Регион", "Региональные"),
    "che_region": ("Че Регион", "Региональные"),
    "yu_region": ("Ю Регион", "Региональные"),
    "tv3_region": ("ТВ-3 Регион", "Региональные"),

    #  РУССКОЯЗЫЧНЫЕ ЗАРУБЕЖНЫЕ
    "rtvi": ("RTVi", "Русскоязычные зарубежные"),
    "rtvi_europe": ("RTVi Europe", "Русскоязычные зарубежные"),
    "rtvi_germany": ("RTVi Germany", "Русскоязычные зарубежные"),
    "rtvi_baltic": ("RTVi Baltic", "Русскоязычные зарубежные"),
    "ostankino_international": ("Останкино International", "Русскоязычные зарубежные"),
    "ostankino_europe": ("Останкино Europe", "Русскоязычные зарубежные"),
    "russia_today_eu": ("Russia Today EU", "Русскоязычные зарубежные"),
    "russia_today_de": ("Russia Today DE", "Русскоязычные зарубежные"),
    "baltic_plus": ("Baltic+", "Русскоязычные зарубежные"),
    "baltic_tv": ("Baltic TV", "Русскоязычные зарубежные"),
    "rtvi_usa": ("RTVi USA", "Русскоязычные зарубежные"),
    "russian_america_tv": ("Russian America TV", "Русскоязычные зарубежные"),
    "russian_television_network": ("Russian Television Network", "Русскоязычные зарубежные"),
    "russian_channel_one_usa": ("Первый канал USA", "Русскоязычные зарубежные"),
    "russian_tv_usa": ("Russian TV USA", "Русскоязычные зарубежные"),
    "russian_canada_tv": ("Russian Canada TV", "Русскоязычные зарубежные"),
    "israel_plus": ("Israel+", "Русскоязычные зарубежные"),
    "israel_9": ("9 канал Израиль", "Русскоязычные зарубежные"),
    "israel_rus_tv": ("Israel Russian TV", "Русскоязычные зарубежные"),
    "inter_plus": ("Интер+", "Русскоязычные зарубежные"),
    "1plus1_international": ("1+1 International", "Русскоязычные зарубежные"),
    "ictv_international": ("ICTV International", "Русскоязычные зарубежные"),
    "novy_channel_int": ("Новый канал International", "Русскоязычные зарубежные"),
    "stb_int": ("СТБ International", "Русскоязычные зарубежные"),
    "ukraine_24_int": ("Украина 24 International", "Русскоязычные зарубежные"),
    "belarus_1": ("Беларусь 1", "Русскоязычные зарубежные"),
    "belarus_2": ("Беларусь 2", "Русскоязычные зарубежные"),
    "belarus_3": ("Беларусь 3", "Русскоязычные зарубежные"),
    "belarus_5": ("Беларусь 5", "Русскоязычные зарубежные"),
    "belsat": ("Белсат", "Русскоязычные зарубежные"),
    "kazakhstan_tv": ("Казахстан ТВ", "Русскоязычные зарубежные"),
    "khabar": ("Хабар", "Русскоязычные зарубежные"),
    "khabar_24": ("Хабар 24", "Русскоязычные зарубежные"),
    "ktk": ("КТК", "Русскоязычные зарубежные"),
    "ntk_kz": ("НТК Казахстан", "Русскоязычные зарубежные"),
    "rustavi_2_int": ("Rustavi 2 International", "Русскоязычные зарубежные"),
    "imedi_int": ("Imedi International", "Русскоязычные зарубежные"),
    "armenia_tv": ("Армения ТВ", "Русскоязычные зарубежные"),
    "armenia_1": ("Армения 1", "Русскоязычные зарубежные"),
    "armenia_24": ("Армения 24", "Русскоязычные зарубежные"),
    "kyrgyzstan_tv": ("Кыргызстан ТВ", "Русскоязычные зарубежные"),
    "osh_tv": ("Ош ТВ", "Русскоязычные зарубежные"),
    "moldova_1": ("Молдова 1", "Русскоязычные зарубежные"),
    "moldova_2": ("Молдова 2", "Русскоязычные зарубежные"),
    "moldova_int": ("Moldova International", "Русскоязычные зарубежные"),
    "latvia_rus_tv": ("Latvia Russian TV", "Русскоязычные зарубежные"),
    "lithuania_rus_tv": ("Lithuania Russian TV", "Русскоязычные зарубежные"),
    "estonia_rus_tv": ("Estonia Russian TV", "Русскоязычные зарубежные"),
    "euronews_russian": ("Euronews Russian", "Русскоязычные зарубежные"),
    "dw_russian": ("DW Russian", "Русскоязычные зарубежные"),
    "bbc_russian": ("BBC Russian", "Русскоязычные зарубежные"),
    "france24_russian": ("France24 Russian", "Русскоязычные зарубежные"),
    "cgtn_russian": ("CGTN Russian", "Русскоязычные зарубежные"),
    "current_time": ("Настоящее Время", "Русскоязычные зарубежные"),
    "current_time_eu": ("Настоящее Время Европа", "Русскоязычные зарубежные"),
    "current_time_usa": ("Настоящее Время США", "Русскоязычные зарубежные"),
    "diaspora_tv": ("Diaspora TV", "Русскоязычные зарубежные"),
    "russian_world_tv": ("Russian World TV", "Русскоязычные зарубежные"),
    "russian_family_tv": ("Russian Family TV", "Русскоязычные зарубежные"),

    #  МЕЖДУНАРОДНЫЕ КАНАЛЫ
    "discovery_history": ("Discovery History", "Международные"),
    "viasat_film": ("Viasat Film", "Международные"),
    "viasat_film_family": ("Viasat Film Family", "Международные"),
    "viasat_film_action": ("Viasat Film Action", "Международные"),
    "tlc": ("TLC", "Международные"),
    "funbox": ("FunBox", "Международные"),
    "cgtn": ("CGTN", "Международные"),
    "france24": ("France 24", "Международные"),
    "dw": ("Deutsche Welle", "Международные"),
    "bbc_world": ("BBC World News", "Международные"),
    "cnn_international": ("CNN International", "Международные"),
    "sky_news": ("Sky News", "Международные"),
    "al_jazeera": ("Al Jazeera", "Международные"),
    "nhk_world": ("NHK World", "Международные"),
    "rai_italia": ("RAI Italia", "Международные"),
    "tv5_monde": ("TV5 Monde", "Международные"),
    "rt_doc": ("RT Documentary", "Международные"),

    #  АВТО / ТЕХНО / МОДА / ЛАЙФСТАЙЛ
    "avto_plus": ("Авто Плюс", "Авто/Техно"),
    "avto_24": ("Авто 24", "Авто/Техно"),
    "auto_world": ("Auto World", "Авто/Техно"),
    "car_channel": ("Car Channel", "Авто/Техно"),
    "drive_tv": ("Drive TV", "Авто/Техно"),
    "garage_tv": ("Garage TV", "Авто/Техно"),
    "tuning_tv": ("Tuning TV", "Авто/Техно"),
    "retro_auto_tv": ("Retro Auto TV", "Авто/Техно"),
    "hitech_tv": ("HiTech TV", "Авто/Техно"),
    "gadget_tv": ("Gadget TV", "Авто/Техно"),
    "digital_world": ("Digital World", "Авто/Техно"),
    "future_tv": ("Future TV", "Авто/Техно"),
    "innovation_tv": ("Innovation TV", "Авто/Техно"),
    "tech_tv": ("Tech TV", "Авто/Техно"),
    "robotics_tv": ("Robotics TV", "Авто/Техно"),
    "ai_tv": ("AI TV", "Авто/Техно"),
    "fashion_tv": ("Fashion TV", "Мода/Лайфстайл"),
    "fashion_one": ("Fashion One", "Мода/Лайфстайл"),
    "fashion_world": ("Fashion World", "Мода/Лайфстайл"),
    "style_tv": ("Style TV", "Мода/Лайфстайл"),
    "glamour_tv": ("Glamour TV", "Мода/Лайфстайл"),
    "luxury_tv": ("Luxury TV", "Мода/Лайфстайл"),
    "lifestyle_tv": ("Lifestyle TV", "Мода/Лайфстайл"),
    "life_tv": ("Life TV", "Мода/Лайфстайл"),
    "living_tv": ("Living TV", "Мода/Лайфстайл"),
    "home_tv": ("Home TV", "Мода/Лайфстайл"),
    "family_tv": ("Family TV", "Мода/Лайфстайл"),
    "happy_tv": ("Happy TV", "Мода/Лайфстайл"),
    "relax_tv": ("Relax TV", "Мода/Лайфстайл"),
    "zen_tv": ("Zen TV", "Мода/Лайфстайл"),
    "home_design_tv": ("Home Design TV", "Мода/Лайфстайл"),
    "interior_tv": ("Interior TV", "Мода/Лайфстайл"),
    "renovation_tv": ("Renovation TV", "Мода/Лайфстайл"),
    "diy_tv": ("DIY TV", "Мода/Лайфстайл"),
    "garden_tv": ("Garden TV", "Мода/Лайфстайл"),
    "home_plus": ("Home+", "Мода/Лайфстайл"),
    "fitness_tv": ("Fitness TV", "Мода/Лайфстайл"),
    "health_tv": ("Health TV", "Мода/Лайфстайл"),
    "yoga_tv": ("Yoga TV", "Мода/Лайфстайл"),
    "wellness_tv": ("Wellness TV", "Мода/Лайфстайл"),
    "sport_life_tv": ("Sport Life TV", "Мода/Лайфстайл"),
    "body_tv": ("Body TV", "Мода/Лайфстайл"),
    "beauty_tv": ("Beauty TV", "Мода/Лайфстайл"),
    "makeup_tv": ("Makeup TV", "Мода/Лайфстайл"),
    "spa_tv": ("SPA TV", "Мода/Лайфстайл"),
    "cosmetics_tv": ("Cosmetics TV", "Мода/Лайфстайл"),
    "auto_mania": ("Auto Mania", "Авто/Техно"),
    "tech_plus": ("Tech+", "Авто/Техно"),
    "fashion_plus": ("Fashion+", "Мода/Лайфстайл"),
    "lifestyle_plus": ("Lifestyle+", "Мода/Лайфстайл"),
    "home_style": ("Home Style", "Мода/Лайфстайл"),
    "drive_plus": ("Drive+", "Авто/Техно"),
    "garage_plus": ("Garage+", "Авто/Техно"),

#  ЦЕНТРАЛЬНОЕ ТЕЛЕВИДЕНИЕ / ЦЕНТР
    "che": ("Че", "Центральные"),
    "yu": ("Ю", "Центральные"),
    "sts_love": ("СТС Love", "Центральные"),
    "tnt4": ("ТНТ4", "Центральные"),
    "super": ("Супер", "Центральные"),
    "subbota": ("Суббота!", "Центральные"),
    "2x2": ("2x2", "Центральные"),
    "tnt_central": ("ТНТ Центральный", "Центральные"),
    "sts_central": ("СТС Центральный", "Центральные"),
    "che_central": ("Че Центральный", "Центральные"),
    "yu_central": ("Ю Центральный", "Центральные"),
    "tv3_central": ("ТВ-3 Центральный", "Центральные"),
    "rentv_central": ("РЕН ТВ Центральный", "Центральные"),
    "ntv_central": ("НТВ Центральный", "Центральные"),
    "tvc_central": ("ТВЦ Центральный", "Центральные"),
    "rbk": ("РБК", "Центральные"),
    "dozhd": ("Дождь", "Центральные"),
    "sts_love_hd": ("СТС Love HD", "Центральные"),
    "muz_tv_hd": ("Муз-ТВ HD", "Центральные"),
    "otr_central": ("ОТР Центральный", "Центральные"),
    "mir_central": ("Мир Центральный", "Центральные"),
    "zvezda_central": ("Zvezda Central", "Центральные"),

    # ==========================
    # VIJU / VIASAT
    # ==========================

    "viju_tv1000": ("viju TV1000", "Viasat"),
    "viju_tv1000_russian": ("viju TV1000 Русское", "Viasat"),
    "viju_action": ("viju Action", "Viasat"),
    "viju_comedy": ("viju Comedy", "Viasat"),
    "viju_megahit": ("viju Megahit", "Viasat"),
    "viju_premiere": ("viju Premiere", "Viasat"),
    "viju_serial": ("viju Serial", "Viasat"),
    "viju_thriller": ("viju Thriller", "Viasat"),
    "viju_dark": ("viju Dark", "Viasat"),
    "viju_horror": ("viju Horror", "Viasat"),

    "viju_history": ("viju History", "Viasat"),
    "viju_explore": ("viju Explore", "Viasat"),
    "viju_nature": ("viju Nature", "Viasat"),

    "viju_plus_history": ("viju+ History", "Viasat"),
    "viju_plus_explore": ("viju+ Explore", "Viasat"),
    "viju_plus_nature": ("viju+ Nature", "Viasat"),

    "viju_plus_megahit": ("viju+ Megahit", "Viasat"),
    "viju_plus_premiere": ("viju+ Premiere", "Viasat"),
    "viju_plus_comedy": ("viju+ Comedy", "Viasat"),
    "viju_plus_serial": ("viju+ Serial", "Viasat"),
    "viju_plus_thriller": ("viju+ Thriller", "Viasat"),
    "viju_plus_dark": ("viju+ Dark", "Viasat"),
    "viju_plus_horror": ("viju+ Horror", "Viasat"),

    "viju_sport": ("viju Sport", "Viasat"),
    "viju_plus_sport": ("viju+ Sport", "Viasat"),

    "viasat_nature": ("Viasat Nature", "Viasat"),
    "viasat_explore": ("Viasat Explore", "Viasat"),
    "viasat_history": ("Viasat History", "Viasat"),
    "viasat_sport": ("Viasat Sport", "Viasat"),
    "viasat_film": ("Viasat Film", "Viasat"),
    "viasat_film_action": ("Viasat Film Action", "Viasat"),
    "viasat_film_family": ("Viasat Film Family", "Viasat"),
    "viasat_tv1000": ("Viasat TV1000", "Viasat"),

    # ==========================
    # VIJU / VIASAT
    # ==========================

    "viju_tv1000": ("viju TV1000", "Viasat"),
    "viju_tv1000_russian": ("viju TV1000 Русское", "Viasat"),
    "viju_tv1000_action": ("viju TV1000 Action", "Viasat"),
    "viju_tv1000_novella": ("viju TV1000 Novella", "Viasat"),
    "viju_tv1000_romantica": ("viju TV1000 Romantica", "Viasat"),

    "viju_plus_premiere": ("viju+ Premiere", "Viasat"),
    "viju_plus_megahit": ("viju+ Megahit", "Viasat"),
    "viju_plus_comedy": ("viju+ Comedy", "Viasat"),
    "viju_plus_serial": ("viju+ Serial", "Viasat"),
    "viju_plus_planet": ("viju+ Planet", "Viasat"),
    "viju_plus_sport": ("viju+ Sport", "Viasat"),

    "viju_explore": ("viju Explore", "Viasat"),
    "viju_nature": ("viju Nature", "Viasat"),
    "viju_history": ("viju History", "Viasat"),

    # ==========================================================
    # VIJU TV1000
    # ==========================================================
    "tv1000": "viju_tv1000",
    "viju_tv1000": "viju_tv1000",
    "viju tv1000": "viju_tv1000",
    "viasat_tv1000": "viju_tv1000",
    "viasat tv1000": "viju_tv1000",

    "tv1000_russian": "viju_tv1000_russian",
    "tv1000 russian": "viju_tv1000_russian",
    "tv1000_rus": "viju_tv1000_russian",
    "tv1000_russkoe": "viju_tv1000_russian",
    "tv1000_russian_movie": "viju_tv1000_russian",
    "tv1000_русское": "viju_tv1000_russian",
    "tv1000_русское_кино": "viju_tv1000_russian",
    "tv1000 русское": "viju_tv1000_russian",
    "tv1000 русское кино": "viju_tv1000_russian",
    "viju_tv1000_russian": "viju_tv1000_russian",
    "viju tv1000 russian": "viju_tv1000_russian",
    "viju tv1000 русское": "viju_tv1000_russian",

    "tv1000_action": "viju_tv1000_action",
    "tv1000 action": "viju_tv1000_action",
    "tv1000action": "viju_tv1000_action",
    "viju_action": "viju_tv1000_action",
    "viju action": "viju_tv1000_action",
    "viju_tv1000_action": "viju_tv1000_action",
    "viju tv1000 action": "viju_tv1000_action",

    "tv1000_novella": "viju_tv1000_novella",
    "tv1000 novella": "viju_tv1000_novella",
    "viju_tv1000_novella": "viju_tv1000_novella",
    "viju tv1000 novella": "viju_tv1000_novella",

    "tv1000_romantica": "viju_tv1000_romantica",
    "tv1000 romantica": "viju_tv1000_romantica",
    "viju_tv1000_romantica": "viju_tv1000_romantica",
    "viju tv1000 romantica": "viju_tv1000_romantica",

    # ==========================================================
    # VIJU+
    # ==========================================================
    "viju_plus_premiere": "viju_plus_premiere",
    "viju+_premiere": "viju_plus_premiere",
    "viju+ premiere": "viju_plus_premiere",
    "viju plus premiere": "viju_plus_premiere",
    "vip_premiere": "viju_plus_premiere",
    "vip premiere": "viju_plus_premiere",
    "vippremiere": "viju_plus_premiere",

    "viju_plus_megahit": "viju_plus_megahit",
    "viju+_megahit": "viju_plus_megahit",
    "viju+ megahit": "viju_plus_megahit",
    "viju plus megahit": "viju_plus_megahit",
    "vip_megahit": "viju_plus_megahit",
    "vip megahit": "viju_plus_megahit",
    "vipmegahit": "viju_plus_megahit",

    "viju_plus_comedy": "viju_plus_comedy",
    "viju+_comedy": "viju_plus_comedy",
    "viju+ comedy": "viju_plus_comedy",
    "viju plus comedy": "viju_plus_comedy",
    "vip_comedy": "viju_plus_comedy",
    "vip comedy": "viju_plus_comedy",
    "vipcomedy": "viju_plus_comedy",

    "viju_plus_serial": "viju_plus_serial",
    "viju+_serial": "viju_plus_serial",
    "viju+ serial": "viju_plus_serial",
    "viju plus serial": "viju_plus_serial",
    "vip_serial": "viju_plus_serial",
    "vip serial": "viju_plus_serial",
    "vipserial": "viju_plus_serial",

    "viju_plus_planet": "viju_plus_planet",
    "viju+_planet": "viju_plus_planet",
    "viju+ planet": "viju_plus_planet",
    "viju plus planet": "viju_plus_planet",
    "vip_planet": "viju_plus_planet",
    "vip planet": "viju_plus_planet",
    "viasat_planet": "viju_plus_planet",
    "viasat planet": "viju_plus_planet",
    "planet_hd": "viju_plus_planet",

    "viju_plus_sport": "viju_plus_sport",
    "viju+_sport": "viju_plus_sport",
    "viju+ sport": "viju_plus_sport",
    "viju plus sport": "viju_plus_sport",
    "vip_sport": "viju_plus_sport",
    "vip sport": "viju_plus_sport",
    "viasat_sport": "viju_plus_sport",
    "viasat sport": "viju_plus_sport",

    # ==========================================================
    # VIJU EXPLORE
    # ==========================================================
    "viju_explore": "viju_explore",
    "viju explore": "viju_explore",
    "viasat_explore": "viju_explore",
    "viasat explore": "viju_explore",
    "explore_hd": "viju_explore",
    "explore hd": "viju_explore",

    # ==========================================================
    # VIJU NATURE
    # ==========================================================
    "viju_nature": "viju_nature",
    "viju nature": "viju_nature",
    "viasat_nature": "viju_nature",
    "viasat nature": "viju_nature",
    "nature_hd": "viju_nature",
    "nature hd": "viju_nature",

    # ==========================================================
    # VIJU HISTORY
    # ==========================================================
    "viju_history": "viju_history",
    "viju history": "viju_history",
    "viasat_history": "viju_history",
    "viasat history": "viju_history",
    "history_hd": "viju_history",
    "history hd": "viju_history",
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
# Валидатор HLS-потока
# -------------------------------

def probe_url(session: Any, url: str, timeout: float = 2.5, user_agent: str = "HlsWinkPlayer") -> Tuple[bool, int, float, str]:
    if session is None:
        return False, 0, 0.0, "requests_not_installed"

    headers = {"User-Agent": user_agent}
    start = time.time()

    try:
        with session.get(url, headers=headers, stream=True, timeout=timeout) as resp:
            latency = (time.time() - start) * 1000.0

            if resp.status_code in (200, 206):
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
# Движок сканирования
# -------------------------------

def scan_ngenix_node(
    cdn_host: str = "s70378.cdn.ngenix.net", 
    meta_dict: Dict[str, Tuple[str, str]] = CHANNEL_META,
    start_index: int = 1,
    group_override: str = "Эфирные ТВ Плюс",
    timeout: float = 2.5,
    max_workers: int = 20
):
    print(f"=== [СКАЛА] Запуск валидатора: {cdn_host} ===")

    tasks = []
    path_templates = [
        "/{slug}/2/index.m3u8",
        "/{slug}/1/index.m3u8",
        "/hls/{slug}/variant.m3u8"
    ]

        for key, meta_data in meta_dict.items():
        # Защищённая распаковка: извлекаем title и group независимо от длины кортежа/списка
        if isinstance(meta_data, (list, tuple)):
            title = meta_data[0]
            group = meta_data[1] if len(meta_data) > 1 else "Разное"
        else:
            title = str(meta_data)
            group = "Разное"

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
    found_keys = set()
    scanned_logs = []

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

    meta_keys = list(meta_dict.keys())
    found_channels.sort(key=lambda x: meta_keys.index(x["key"]))

    with open("ngenix_report.txt", "w", encoding="utf-8") as f:
        f.write("СКАЛА Вер 3.9.1 — NGENIX FINDER REPORT\n")
        f.write("=========================================\n")
        f.write(f"Проверено комбинаций URL: {len(tasks)}\n")
        f.write(f"Успешно найдено каналов: {len(found_channels)}\n")
        f.write("=========================================\n\n")

        for log in scanned_logs:
            tag = "OK" if log["ok"] else "FAIL"
            f.write(f"[СКАЛА] [{tag}] Канал: {log['title']} | Key: {log['key']}\n")
            f.write(f"        URL: {log['url']}\n")
            f.write(f"        Статус: {log['status']} | Latency: {int(log['latency'])} ms | Error: {log['error']}\n\n")

    with open("ngenix_found.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, ch in enumerate(found_channels, start=start_index):
            f.write(f'#EXTINF:-1 tvg-id="{ch["key"]}" group-title="{ch["group"]}",{i}. {ch["title"]}\n')
            f.write(f'{ch["url"]}\n')

    print("\n[СКАЛА] Поиск завершён!")
    print(" — Отчёт: ngenix_report.txt")
    print(" — Сгенерирован M3U: ngenix_found.m3u")


if __name__ == "__main__":
    scan_ngenix_node(
        cdn_host="s70378.cdn.ngenix.net", 
        meta_dict=CHANNEL_META,
        start_index=1,
        group_override="Эфирные ТВ Плюс",
        timeout=2.5,
        max_workers=20
    )












