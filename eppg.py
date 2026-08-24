import io
import json
import logging
import urllib.request
import zipfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

REMOTE_RESOURCE_URL = "http://www.teleguide.info:80/download/new3/jtv.zip"

class EPPGProcessor:
    def __init__(self, url: str = REMOTE_RESOURCE_URL):
        self.url = url
        self.payload_data = []

    def fetch_and_extract(self) -> list[dict]:
        """Загружает архив и извлекает системные алиасы."""
        logging.info("Сбор метаданных из удаленного источника...")
        req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                zip_buffer = io.BytesIO(response.read())
        except Exception as e:
            logging.error(f"Ошибка получения данных: {e}")
            return []

        parsed_items = []
        with zipfile.ZipFile(zip_buffer, "r") as z:
            for filename in z.namelist():
                if filename.lower().endswith(".pdt"):
                    raw_name = Path(filename).stem.strip()
                    clean_alias = raw_name.lower().replace(" ", "_")

                    parsed_items.append({
                        "raw_name": raw_name,
                        "alias": clean_alias,
                        "confidence": 0.95,
                        "source": "eppg_legacy"
                    })

        self.payload_data = parsed_items
        logging.info(f"Обработано записей: {len(parsed_items)}")
        return parsed_items

    def save_to_txt(self, output_file: str = "jtv_aliases.txt") -> None:
        lines = [f"{item['alias']}={item['raw_name']}" for item in self.payload_data]
        Path(output_file).write_text("\n".join(lines), encoding="utf-8")
        logging.info(f"Данные экпортированы в TXT: {output_file}")

    def save_to_json(self, output_file: str = "jtv_knowledge.json") -> None:
        payload = {
            "source": "eppg_provider",
            "total": len(self.payload_data),
            "items": self.payload_data
        }
        Path(output_file).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info(f"Данные экспортированы в JSON: {output_file}")


def inject_eppg_knowledge(db_instance):
    """Шпионский импорт исторической базы прямо в knowledge.db"""
    processor = EPPGProcessor()
    items = processor.fetch_and_extract()
    
    if not items:
        return

    for item in items:
        db_instance.record_learned_alias(
            channel_name=item["raw_name"],
            learned_alias=item["alias"]
        )
    logging.info("База данных успешно обогащена через eppg!")


if __name__ == "__main__":
    processor = EPPGProcessor()
    processor.fetch_and_extract()
    processor.save_to_txt()
    processor.save_to_json()
