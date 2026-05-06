import re
import logging
from typing import Tuple, List
from emodji import physicist_dict, first_ro_dict, second_ro_dict

logger = logging.getLogger(__name__)

def del_initials(text: str) -> str:
    """Удаляет инициалы в конце строки (например, 'Иванов И.И.')."""
    # Регулярное выражение для поиска инициалов в конце строки
    pattern = r'\s[А-Яа-я]\s*\.?\s*[А-Яа-я]\s*\.?\s?$'

    # Заменяем найденное совпадение на пустую строку
    result = re.sub(pattern, '', text)

    return result.strip()

def levenshtein_distance(s1: str, s2: str) -> int:
    """Вычисляет расстояние Левенштейна между двумя строками."""
    len_s1 = len(s1)
    len_s2 = len(s2)

    dp = [[0 for _ in range(len_s2 + 1)] for _ in range(len_s1 + 1)]

    for i in range(len_s1 + 1):
        dp[i][0] = i
    for j in range(len_s2 + 1):
        dp[0][j] = j

    for i in range(1, len_s1 + 1):
        for j in range(1, len_s2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)

    return dp[len_s1][len_s2]

def identification_func(note: str) -> Tuple[str, str, str, str]:
    """
    Разбирает строку заметки и идентифицирует врача, физика и укладку.
    
    Args:
        note: Строка заметки из БД (например, "Иванов/Петров\nУкладка")
        
    Returns:
        Tuple[str, str, str, str]: (Врач, Физик, Укладка, Номер отделения)
    """
    doctor = '🧑‍⚕ —'
    physicist = '☢ —'
    laying = '—'
    office = '0'

    if not note:
        return doctor, physicist, laying, office

    try:
        # Нормализация разделителей
        clean_note = note.replace('\\', '/').replace('//', '/')
        note_list = [p.strip() for p in clean_note.split('/')]

        if len(note_list) < 2:
            return doctor, physicist, laying, office

        # 1. Идентификация врача
        doctor_raw = del_initials(note_list[0])
        doctor_cap = doctor_raw.capitalize()
        
        # Поиск по словарям с учетом опечаток
        all_doctors = list(first_ro_dict.keys()) + list(second_ro_dict.keys())
        for key in all_doctors:
            if key == 'Врач': continue
            distance = levenshtein_distance(doctor_cap, key)
            if distance <= 2:
                doctor_raw = key
                break

        # 2. Идентификация физика и укладки
        phys_part = note_list[1]
        if '\n' in phys_part:
            phys_lines = phys_part.split('\n')
            physicist_raw = del_initials(phys_lines[0].strip())
            laying = phys_lines[1].strip().capitalize()
        else:
            physicist_raw = del_initials(phys_part)
            laying = '—'

        physicist_cap = physicist_raw.capitalize()
        for key in physicist_dict:
            if key == 'Физик': continue
            distance = levenshtein_distance(physicist_cap, key)
            if distance <= 2:
                physicist_raw = key
                break

        # Применение эмодзи и определение отделения
        physicist_cap = physicist_raw.capitalize()
        if physicist_cap in physicist_dict:
            physicist = physicist_dict[physicist_cap]
        else:
            physicist = f"{physicist_dict['Физик']} {physicist_cap}"

        doctor_cap = doctor_raw.capitalize()
        if doctor_cap in first_ro_dict:
            doctor = first_ro_dict[doctor_cap]
            office = '1'
        elif doctor_cap in second_ro_dict:
            doctor = second_ro_dict[doctor_cap]
            office = '2'
        else:
            doctor = f"{first_ro_dict['Врач']} {doctor_cap}"
            office = '0'

    except Exception as e:
        logger.error(f"[Identification] Ошибка при парсинге заметки '{note}': {e}")

    return doctor, physicist, laying, office