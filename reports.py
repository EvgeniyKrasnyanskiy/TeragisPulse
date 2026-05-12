"""
reports.py — модуль автоматической генерации ежедневных CSV-отчётов
по новым пациентам для TeragisPulse.
"""

import csv
import logging
from datetime import date
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from difflib import SequenceMatcher

_PROJECT_ROOT = Path(__file__).parent.resolve()

REPORTS_DIR     = _PROJECT_ROOT / "Reports"       # папка отчётов
DOCTORS_FILE    = _PROJECT_ROOT / "doctors.txt"   # список врачей

CSV_DELIMITER = ","
CSV_ENCODING  = "utf-8-sig"
CSV_HEADER = ["ФИО_Пациента", "Фамилия_Доктора"]

# REPEAT_PLAN_SUFFIX = " (второй план)"
REPEAT_PLAN_SUFFIX = " ②" 

logger = logging.getLogger(__name__)

def load_doctors(filepath: Path = DOCTORS_FILE) -> List[str]:
    """Загружает список фамилий врачей, игнорируя пустые строки."""
    if not filepath.exists():
        logger.debug(f"[Reports] {filepath.name} не найден — все врачи будут '-'")
        return []
    try:
        with filepath.open(encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        logger.error(f"[Reports] Ошибка чтения {filepath.name}: {e}")
        return []

def extract_doctor(comment: Optional[str], doctors_list: List[str]) -> str:
    """
    Ищет фамилию врача в комментарии. Если не нашел — возвращает '-'.
    
    Использует нечеткое сравнение (расстояние Левенштейна через SequenceMatcher).
    """
    if not comment or not doctors_list:
        return "-"

    # Нормализация строки: убираем лишние символы
    normalized = comment.replace("/", " ").replace("\\", " ").replace(")", " ").replace("(", " ")
    words = normalized.split()[:5] # Проверяем первые 5 слов
    
    best_match = "-"
    highest_score = 0.0

    for word in words:
        # Очищаем слово от цифр и знаков препинания
        clean = "".join(filter(str.isalpha, word))
        if len(clean) < 3:
            continue
            
        clean_lower = clean.lower()
        for doc in doctors_list:
            doc_lower = doc.lower()
            score = SequenceMatcher(None, clean_lower, doc_lower).ratio()
            if score > highest_score and score >= 0.75:
                highest_score = score
                best_match = doc.strip().capitalize()
                
            # Если нашли точное совпадение — сразу выходим
            if score == 1.0:
                return best_match

    return best_match

def generate_daily_report(data_rows: List[Dict[str, Any]], report_date: Optional[date] = None, subfolder: str = "1RO") -> None:
    """
    Генерирует или обновляет ежедневный отчет.
    Если данных нет — удаляет файл отчета за этот день.
    """
    r_date = report_date or date.today()
    # Создаем структуру: Reports / Отделение / Год / Месяц
    target_dir = REPORTS_DIR / subfolder / f"{r_date.year}" / f"{r_date.month:02d}"
    file_path = target_dir / f"report_{r_date.isoformat()}.csv"

    # Если данных нет — удаляем старый файл, если он был
    if not data_rows:
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"[Reports] Файл {file_path.name} удален (нет новых пациентов)")
            except Exception as e:
                logger.error(f"[Reports] Ошибка при удалении файла {file_path}: {e}")
        return

    doctors_list = load_doctors()
    rows_to_write = []

    for item in data_rows:
        fio = item.get("patient_name", "ФИО не указано")
        is_repeat = item.get("is_repeat_plan", False)
        comment = item.get("comment", "")
        
        doctor = extract_doctor(comment, doctors_list)
        display_fio = f"{fio}{REPEAT_PLAN_SUFFIX}" if is_repeat else fio
        rows_to_write.append([display_fio, doctor])

    # Создаем директорию, если её нет
    if not target_dir.exists():
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"[Reports] Ошибка создания директории {target_dir}: {e}")
            return

    try:
        # Запись в формате CSV
        with open(file_path, mode="w", newline="", encoding=CSV_ENCODING) as f:
            writer = csv.writer(f, delimiter=CSV_DELIMITER)
            writer.writerow(CSV_HEADER)
            writer.writerows(rows_to_write)
    except Exception as e:
        logger.error(f"[Reports] Ошибка записи отчёта {file_path.name}: {e}")
        
def generate_custom_patient_excel_report(data_rows: List[List[Any]], dept_num: Union[int, str], period_str: str) -> Optional[Path]:
    """Генерирует детальный CSV-отчет (детальная история)."""
    filename = f"Detailed_Report_RO{dept_num}_{period_str}.csv"
    
    # Определяем папку назначения (Отделение / Год / Месяц)
    dept_label = f"{dept_num}RO"
    now = date.today()
    target_dir = REPORTS_DIR / dept_label / f"{now.year}" / f"{now.month:02d}"
    path = target_dir / filename
    
    header = [
        "ФИО пациента", 
        "ДР", 
        "Пол", 
        "Серия", 
        "Доза", 
        "Фракции", 
        "Начало", 
        "Конец"
    ]
    
    try:
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            
        with open(path, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(header)
            writer.writerows(data_rows)
        return path
    except Exception as e:
        logger.error(f"[Reports] Ошибка генерации кастомного отчета: {e}")
        return None