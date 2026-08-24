import gzip
import io
import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Архивный URL слепка 2016 года из Wayback Machine
ARCHIVE_2016_EPG_URL = "http://web.archive.org/web/20160801000000id_/http://iptvx.one/epg/epg.xml.gz"

class EPGXmlExtractor2016:
    def __init__(self, url: str = ARCHIVE_2016_EPG_URL):
        self.url = url
        self.extracted_data = []

    def fetch_and_parse(self) -> list[dict]:
        """Качает архивный epg.xml.gz за 2016 год и разбирает XMLTV структуры."""
        logging.info(f"Загрузка архивного XMLTV (2016 год) с {self.url}...")
        req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                gz_buffer = io.BytesIO(response.read())
            with gzip.GzipFile(fileobj=gz_buffer) as gz:
                xml_content = gz.read()
        except Exception as e:
            logging.error(f"Ошибка загрузки или распаковки архивного XML.GZ 2016: {e}")
            return []

        logging.info("Парсинг XML структуры за 2016 год...")
        try:
            root = ET.fromstring(xml_content)
        except Exception as e:
            logging.error(f"Ошибка парсинга XML: {e}")
            return []

        parsed_items = []

        for channel in root.findall("channel"):
            channel_id = channel.get("id", "").strip()
            ru_names, en_names = [], []

            for dn in channel.findall("display-name"):
                text = dn.text.strip() if dn.text else ""
                lang = dn.get("lang", "").lower()
                if not text:
                    continue
                if lang == "en" or re.match(r'^[a-zA-Z0-9\s\+\-_]+$', text):
                    en_names.append(text)
                else:
                    ru_names.append(text)

            icon_alias = ""
            icon_tag = channel.find("icon")
            if icon_tag is not None:
                src = icon_tag.get("src", "")
                if src:
                    icon_filename = Path(src).stem
                    if re.match(r'^[a-zA-Z0-9\-_]+$', icon_filename):
                        icon_alias = icon_filename.lower()

            cdn_candidates = set()
            if channel_id and re.match(r'^[a-zA-Z0-9\-_]+$', channel_id):
                cdn_candidates.add(channel_id.lower())
            if icon_alias:
                cdn_candidates.add(icon_alias)

            for en_name in en_names:
                clean_en = re.sub(r'[^a-zA-Z0-9_]', '', en_name.lower().replace(' ', '_'))
                clean_en_dash = re.sub(r'[^a-zA-Z0-9-]', '', en_name.lower().replace(' ', '-'))
                clean_en_nospace = re.sub(r'[^a-zA-Z0-9]', '', en_name.lower())

                if clean_en: cdn_candidates.add(clean_en)
                if clean_en_dash: cdn_candidates.add(clean_en_dash)
                if clean_en_nospace: cdn_candidates.add(clean_en_nospace)

            if cdn_candidates:
                parsed_items.append({
                    "channel_id": channel_id,
                    "ru_names": ru_names,
                    "en_names": en_names,
                    "icon_alias": icon_alias,
                    "cdn_candidates": list(cdn_candidates),
                    "year": 2016,
                    "confidence": 0.99
                })

        self.extracted_data = parsed_items
        logging.info(f"Извлечено {len(parsed_items)} архивных каналов (2016).")
        return parsed_items

    def save_to_txt(self, output_file: str = "xml_2016_aliases.txt") -> None:
        lines = []
        for item in self.extracted_data:
            primary_ru = item["ru_names"][0] if item["ru_names"] else item["channel_id"]
            for candidate in item["cdn_candidates"]:
                lines.append(f"{candidate}={primary_ru}")
        
        Path(output_file).write_text("\n".join(lines), encoding="utf-8")
        logging.info(f"Архивные алиасы 2016 сохранены в TXT: {output_file}")

    def save_to_json(self, output_file: str = "xml_2016_knowledge.json") -> None:
        payload = {
            "source": "iptvx_archive_2016",
            "year": 2016,
            "total": len(self.extracted_data),
            "items": self.extracted_data
        }
        Path(output_file).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info(f"Архивная база 2016 сохранена в JSON: {output_file}")


if __name__ == "__main__":
    extractor = EPGXmlExtractor2016()
    extractor.fetch_and_parse()
    extractor.save_to_txt()
    extractor.save_to_json()
