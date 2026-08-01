import os
import re
import sys
import time
import signal
import requests
from datetime import datetime

# -----------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ АВТО-ИНКРЕМЕНТА ИМЕН (_1 ... _N)
# -----------------------------------------------------------------------------
def get_next_run_index(base_name="Peter_I_full", output_dir="output"):
    """
    Сканирует текущую директорию и папки вывода, ищет файлы вида base_name_N.m3u
    и возвращает следующий номер N = n + 1.
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

# Определяем текущий порядковый номер запуска _N
RUN_INDEX = get_next_run_index()

# Формируем динамические имена выходных файлов
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


# -----------------------------------------------------------------------------
# БЛОК СКАЛА / ДРЭГ (ОСНОВНОЙ ЛОГ) С АЗ-5 И АВТО-ИНКРЕМЕНТОМ ИМЕН
# -----------------------------------------------------------------------------
class SKALA_DREG_Logger:
    def __init__(self, system_name="ПЕТР_I_ОКНО_В_ЕВПРОПУ_v13.4_AI", main_log=FILE_MAIN_LOG):
        self.system_name = system_name
        self.main_log = main_log
        self.run_index = RUN_INDEX
        self.t_start = None
        self.log_buffer = []

        # Константы кинетики СУЗ
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
        """Симуляция нажатия кнопки АЗ-5 при остановке процесса"""
        t_end = time.perf_counter() if self.t_start else 0.0
        total_time_before_az5 = t_end - self.t_start if self.t_start else 0.0
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        # Расчет кинетики погружения поглотителей
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

# Перехват аварийных сигналов
def handle_abort_signal(sig, frame):
    logger.trigger_az5(reason=f"Перехвачен системный сигнал отмены ({sig})")
    sys.exit(130)

signal.signal(signal.SIGINT, handle_abort_signal)
signal.signal(signal.SIGTERM, handle_abort_signal)


# -----------------------------------------------------------------------------
# МОДУЛЬ БРУТФОРСА CDN УЗЛОВ
# -----------------------------------------------------------------------------
def bruteforce_cdn_nodes(test_channel="CH_MATCHTV", node_start=80700, node_end=80725):
    """
    Брутфорс диапазона узлов CDN sXXXXX.cdn.ngenix.net 
    для поиска наиболее отзывчивого зеркала.
    """
    logger.skala_phase("БРУТФОРС_CDN", f"Перебор узлов s{node_start} .. s{node_end}")
    
    headers = {"User-Agent": "HlsWinkPlayer"}
    active_nodes = []

    for node_num in range(node_start, node_end + 1):
        node_host = f"s{node_num}.cdn.ngenix.net"
        test_url = f"http://{node_host}/hls/{test_channel}/variant.m3u8"
        
        t0 = time.perf_counter()
        try:
            resp = requests.head(test_url, headers=headers, timeout=1.5)
            ping_ms = (time.perf_counter() - t0) * 1000
            
            if resp.status_code in (200, 302):
                logger.dreg(f"s{node_num}", "УЗЕЛ_ОТКЛИКНУЛСЯ", f"Ping: {ping_ms:.1f}ms | HTTP {resp.status_code}")
                active_nodes.append((node_host, ping_ms))
            else:
                logger.dreg(f"s{node_num}", "ОТКЛОНЕНО", f"HTTP {resp.status_code}")
        except Exception as e:
            logger.dreg(f"s{node_num}", "ТАЙМАУТ/ОШИБКА", "Нет ответа от узла")

    if active_nodes:
        # Сортируем по минимальному пингу
        active_nodes.sort(key=lambda x: x[1])
        best_node, best_ping = active_nodes[0]
        logger.skala_phase("БРУТФОРС_УСПЕХ", f"Выбран наилучший узел: {best_node} ({best_ping:.1f}ms)")
        return best_node
    else:
        fallback = "s80718.cdn.ngenix.net"
        logger.skala_phase("БРУТФОРС_ДЕФОЛТ", f"Активные узлы не найдены. Откат на базовый: {fallback}")
        return fallback
