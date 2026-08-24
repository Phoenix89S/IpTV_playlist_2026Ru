
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import os
import gzip
import sqlite3
import argparse
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
#  1. КОНФИГУРАЦИЯ
# ============================================================

EPG_URL = "http://epg.one/epg2.xml.gz"
DB_FILE = "knowledge.db"
OUTPUT_REPORT = "ngSKALA_learned_report.txt"
OUTPUT_PLAYLIST = "playlist.m3u"

# Узлы Ngenix CDN для активного опроса
NODES = [f"s703{i}" for i in range(78, 91)]

TIMEOUT = 3
MAX_THREADS = 25

DEFAULT_PATTERNS = [
    "{v}/index.m3u8",
    "{v}/mono.m3u8",
    "{v}/live.m3u8",
    "hls/{v}/variant.m3u8",
    "{v}/tracks-v1a1/mono.m3u8",
    "{v}/1/index.m3u8",
    "hls/CH_{v}/variant.m3u8"
]

# ============================================================
#  2. БАЗА ДАННЫХ (SQLITE)
# ============================================================

class Database:
    def __init__(self, db_path=DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    attempts INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    weight REAL DEFAULT 0.5
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT UNIQUE,
                    attempts INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    weight REAL DEFAULT 0.5
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT,
                    rule_name TEXT,
                    pattern TEXT,
                    node TEXT,
                    success INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def register_rule(self, rule_name: str):
        with self._get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO rules (name) VALUES (?)", (rule_name,))

    def register_pattern(self, pattern: str):
        with self._get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO patterns (pattern) VALUES (?)", (pattern,))

    def get_ranked_rules(self) -> list:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT name, weight FROM rules ORDER BY weight DESC").fetchall()
            return [dict(r) for r in rows]

    def get_ranked_patterns(self) -> list:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT pattern, weight FROM patterns ORDER BY weight DESC").fetchall()
            return [dict(r) for r in rows]

    def log_attempt(self, channel_id: str, rule_name: str, pattern: str, node: str, success: bool):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO history (channel_id, rule_name, pattern, node, success)
                VALUES (?, ?, ?, ?, ?)
            """, (channel_id, rule_name, pattern, node, 1 if success else 0))

    def update_weights(self):
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE rules
                SET attempts = (SELECT COUNT(*) FROM history WHERE history.rule_name = rules.name),
                    success  = (SELECT COUNT(*) FROM history WHERE history.rule_name = rules.name AND success = 1)
            """)
            conn.execute("""
                UPDATE rules
                SET weight = (CAST(success AS REAL) + 1.0) / (CAST(attempts AS REAL) + 2.0)
            """)
            conn.execute("""
                UPDATE patterns
                SET attempts = (SELECT COUNT(*) FROM history WHERE history.pattern = patterns.pattern),
                    success  = (SELECT COUNT(*) FROM history WHERE history.pattern = patterns.pattern AND success = 1)
            """)
            conn.execute("""
                UPDATE patterns
                SET weight = (CAST(success AS REAL) + 1.0) / (CAST(attempts AS REAL) + 2.0)
            """)
            conn.commit()

# ============================================================
#  3. ГЕНЕРАТОР ПРАВИЛ И ЛОГИКА
# ============================================================

class RuleEngine:
    TRANSLIT_MAP = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "abvgdeezzijklmnoprstufhcchshschyyeu"
    )

    @classmethod
    def generate_variants(cls, name: str, epg_id: str) -> list:
        name_lower = name.lower().strip()
        clean_id = epg_id.lower().replace(" ", "").replace("-", "").replace("_", "")
        
        variants = [
            (epg_id, "exact_id"),
            (clean_id, "clean_id"),
            (name_lower.replace(" ", "_"), "underscore"),
            (name_lower.replace(" ", ""), "no_spaces"),
            (name_lower.translate(cls.TRANSLIT_MAP).replace(" ", "_"), "translit_underscore")
        ]

        if "hd" in name_lower:
            variants.append((name_lower.replace("hd", "").replace(" ", ""), "strip_hd"))

        if "viju" in name_lower:
            core = name_lower.replace("viju", "").replace("+", "").strip().translate(cls.TRANSLIT_MAP)
            variants.append((f"vip_{core}", "viju_prefix"))

        return variants

# ============================================================
#  4. МОДУЛЬ ОБУЧЕНИЯ
# ============================================================

class Learner:
    def __init__(self, db: Database):
        self.db = db
        self._bootstrap()

    def _bootstrap(self):
        for _, rule in RuleEngine.generate_variants("test", "test"):
            self.db.register_rule(rule)
        for p in DEFAULT_PATTERNS:
            self.db.register_pattern(p)

    def train(self):
        self.db.update_weights()

    def get_prioritized_pipeline(self, name: str, epg_id: str) -> tuple:
        variants = RuleEngine.generate_variants(name, epg_id)
        ranked_rules = {r["name"]: r["weight"] for r in self.db.get_ranked_rules()}
        ranked_patterns = [p["pattern"] for p in self.db.get_ranked_patterns()]

        sorted_variants = sorted(
            variants,
            key=lambda x: ranked_rules.get(x[1], 0.5),
            reverse=True
        )

        return sorted_variants, ranked_patterns

# ============================================================
#  5. ИСПОЛНИТЕЛЬНЫЙ СКАНИРОВЩИК И ОПРОС УЗЛОВ
# ============================================================

class Scanner:
    def __init__(self, db: Database, learner: Learner):
        self.db = db
        self.learner = learner
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
        })
        self.active_nodes = []

    def ping_nodes(self) -> list:
        """Активный опрос узлов Ngenix для проверки доступности хостов"""
        print("[*] Опрос и проверка доступности узлов Ngenix...")
        valid_nodes = []

        def check_node(node):
            url = f"https://{node}.cdn.ngenix.net/"
            try:
                # Отправляем легкий HEAD запрос к узлу
                r = self.session.head(url, timeout=TIMEOUT, allow_redirects=True)
                if r.status_code < 500: # Узел ответил (даже если 404/403, сервер жив)
                    return node
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check_node, node) for node in NODES]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    valid_nodes.append(res)

        self.active_nodes = sorted(valid_nodes)
        print(f"[+] Откликнулись узлы ({len(self.active_nodes)}/{len(NODES)}): {', '.join(self.active_nodes)}")
        return self.active_nodes

    def fetch_epg(self) -> list:
        r = self.session.get(EPG_URL, timeout=20)
        gz = gzip.GzipFile(fileobj=io.BytesIO(r.content))
        root = ET.fromstring(gz.read())
        
        channels = []
        for ch in root.findall("channel"):
            cid = ch.get("id", "").strip()
            disp = ch.find("display-name")
            icon = ch.find("icon")
            
            logo = icon.get("src", "").strip() if icon is not None else ""
            
            if cid and disp is not None and disp.text:
                channels.append({
                    "id": cid,
                    "name": disp.text.strip(),
                    "logo": logo
                })
        return channels

    def _verify_stream(self, url: str) -> bool:
        """Опрос и глубокая валидация конкретного потока на узле"""
        try:
            r = self.session.get(url, timeout=TIMEOUT, stream=True)
            if r.status_code == 200:
                # Читаем только первое содержимое, чтобы проверить заголовок HLS
                chunk = next(r.iter_content(chunk_size=256), b"").decode("utf-8", errors="ignore")
                return "#EXTM3U" in chunk
        except Exception:
            pass
        return False

    def scan_channel(self, channel: dict) -> list:
        cid = channel["id"]
        name = channel["name"]
        
        variants, patterns = self.learner.get_prioritized_pipeline(name, cid)
        nodes_to_check = self.active_nodes if self.active_nodes else NODES

        for variant, rule_name in variants:
            for node in nodes_to_check:
                for pattern in patterns:
                    url = f"https://{node}.cdn.ngenix.net/" + pattern.format(v=variant)
                    
                    # Прямой опрос узла Ngenix
                    is_valid = self._verify_stream(url)
                    
                    self.db.log_attempt(cid, rule_name, pattern, node, is_valid)

                    if is_valid:
                        return [{
                            "url": url,
                            "node": f"{node}.cdn.ngenix.net",
                            "rule": rule_name,
                            "pattern": pattern,
                            "variant": variant,
                            "name": name,
                            "logo": channel.get("logo", "")
                        }]
        return []

# ============================================================
#  6. ГЕНЕРАЦИЯ ОТЧЕТОВ И ПЛЕЙЛИСТА
# ============================================================

def export_results(results: dict):
    # 1. Текстовый отчёт
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write("=== NGENIX CDN СКАЛА — ОПТИМИЗИРОВАННЫЙ ОТЧЕТ ===\n\n")
        for cid, data in results.items():
            item = data[0]
            f.write(f"[КАНАЛ] {item['name']}\n")
            f.write(f"[EPG-ID] {cid}\n")
            f.write(f"  [УЗЕЛ NGENIX] {item['node']}\n")
            f.write(f"  [ВАРИАНТ] {item['variant']}\n")
            f.write(f"  [ПРАВИЛО] {item['rule']}\n")
            f.write(f"  [ПОТОК] {item['url']}\n")
            f.write("-" * 50 + "\n\n")

    # 2. IPTV M3U Плейлист
    with open(OUTPUT_PLAYLIST, "w", encoding="utf-8") as f:
        f.write("#EXTM3U url-tvg=\"http://epg.one/epg2.xml.gz\"\n")
        for cid, data in results.items():
            item = data[0]
            logo_attr = f' tvg-logo="{item["logo"]}"' if item["logo"] else ""
            f.write(f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{item["name"]}"{logo_attr},{item["name"]}\n')
            f.write(f"{item['url']}\n")

# ============================================================
#  7. CLI И ТОЧКА ВХОДА
# ============================================================

def run_scan():
    db = Database()
    learner = Learner(db)
    scanner = Scanner(db, learner)

    # 1. Активный опрос узлов Ngenix перед началом
    scanner.ping_nodes()

    print("[*] Загрузка каналов из EPG...")
    channels = scanner.fetch_epg()
    
    # Наши доп. каналы
    extra_channels = [
        {"id": "scream", "name": "Scream", "logo": ""},
        {"id": "shokiruyuschee", "name": "Шокирующее", "logo": ""},
        {"id": "viju_planet", "name": "viju+ planet", "logo": ""},
        {"id": "viju_tv1000_romantica", "name": "viju TV1000 romantica", "logo": ""},
        {"id": "viju_tv1000_novella", "name": "viju TV1000 новелла", "logo": ""},
        {"id": "viju_tv1000_action", "name": "viju TV1000 action", "logo": ""},
        {"id": "viju_tv1000_russkoe", "name": "viju TV1000 русское", "logo": ""},
        {"id": "hit", "name": "ХИТ", "logo": ""},
        {"id": "kinokomediya", "name": "Кинокомедия", "logo": ""},
        {"id": "cinema", "name": "CINEMA", "logo": ""},
        {"id": "mosfilm_gold", "name": "Мосфильм. Золотая коллекция", "logo": ""},
        {"id": "fantastic_channel", "name": "Fantastic Channel", "logo": ""},
        {"id": "boevik", "name": "Боевик", "logo": ""},
        {"id": "kinomix", "name": "Киномикс", "logo": ""},
        {"id": "detektiv", "name": "Детектив", "logo": ""},
        {"id": "rodnoe_kino", "name": "Родное кино", "logo": ""},
        {"id": "patriot", "name": "Патриот", "logo": ""},
        {"id": "rtg_hd", "name": "RTG HD", "logo": ""},
        {"id": "rtg_int", "name": "RTG Int", "logo": ""},
        {"id": "nat_geo_ru", "name": "National Geographic RU", "logo": ""},
        {"id": "nat_geo_wild", "name": "NAT GEO WILD", "logo": ""},
        {"id": "kinoujas", "name": "КИНОУЖАС", "logo": ""},
        {"id": "kinosemya", "name": "КИНОСЕМЬЯ", "logo": ""},
        {"id": "russkiy_roman", "name": "Русский роман", "logo": ""},
        {"id": "russkiy_detektiv", "name": "Русский детектив", "logo": ""},
        {"id": "komediya", "name": "Комедия", "logo": ""},
        {"id": "klyuch", "name": "Ключ", "logo": ""},
        {"id": "ntv_plus", "name": "НТВ-ПЛЮС", "logo": ""},
        {"id": "rutube", "name": "RUTUBE", "logo": ""},
        {"id": "premier", "name": "PREMIER", "logo": ""},
        {"id": "ntv", "name": "НТВ", "logo": ""},
        {"id": "tnt", "name": "ТНТ", "logo": ""},
        {"id": "pyatnica", "name": "Пятница!", "logo": ""},
        {"id": "tv3", "name": "ТВ-3", "logo": ""},
        {"id": "tnt4", "name": "ТНТ4", "logo": ""},
        {"id": "match_tv", "name": "Матч ТВ", "logo": ""},
        {"id": "trash", "name": "Trash", "logo": ""},
        {"id": "match_strana", "name": "Матч! Страна", "logo": ""},
        {"id": "2x2", "name": "2x2", "logo": ""},
        {"id": "subbota", "name": "Суббота!", "logo": ""},
        {"id": "ntv_style", "name": "НТВ Стиль", "logo": ""},
        {"id": "ntv_pravo", "name": "НТВ Право", "logo": ""},
        {"id": "ntv_serial", "name": "НТВ Сериал", "logo": ""},
        {"id": "ntv_hit", "name": "НТВ Хит", "logo": ""},
        {"id": "unknown_russia", "name": "Неизвестная Россия", "logo": ""},
        {"id": "boec", "name": "Боец", "logo": ""},
    ]

    channels.extend(extra_channels)
    print(f"[*] Всего каналов к проверке: {len(channels)}")

    results = {}
    print("[*] Запуск сканирования CDN...")

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(scanner.scan_channel, ch): ch for ch in channels}
        for future in as_completed(futures):
            ch = futures[future]
            res = future.result()
            if res:
                results[ch["id"]] = res
                print(f"[+] Найден: {ch['name']} -> {res[0]['node']}")

    print("[*] Перерасчет рейтинга обучающей модели...")
    learner.train()

    print("[*] Экспорт отчета и плейлиста...")
    export_results(results)

    print(f"[+] Готово! Отчёт: {OUTPUT_REPORT}")
    print(f"[+] Готово! Плейлист: {OUTPUT_PLAYLIST}")

def show_stats():
    db = Database()
    print("\n=== РЕЙТИНГ ПРАВИЛ ===")
    for r in db.get_ranked_rules():
        print(f"Правило: {r['name']:<20} Вес: {r['weight']:.4f}")

    print("\n=== РЕЙТИНГ ШАБЛОНОВ ===")
    for p in db.get_ranked_patterns():
        print(f"Шаблон: {p['pattern']:<30} Вес: {p['weight']:.4f}")

def main():
    parser = argparse.ArgumentParser(description="Адаптивный сканер Ngenix CDN с опросником узлов")
    parser.add_argument("--scan", action="store_true", help="Запустить опрос и сформировать плейлист")
    parser.add_argument("--train", action="store_true", help="Переобучить модель")
    parser.add_argument("--stats", action="store_true", help="Показать статистику")

    args = parser.parse_args()

    if args.scan:
        run_scan()
    elif args.train:
        db = Database()
        Learner(db).train()
        print("[+] Модель переобучена.")
    elif args.stats:
        show_stats()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()


