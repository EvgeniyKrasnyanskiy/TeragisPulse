from datetime import datetime

def get_formatted_date(date_val=None):
    """
    Возвращает строку вида: понедельник (23.03)
    Если date_val не передан, берет текущую дату.
    """
    if date_val is None:
        date_val = datetime.now().date()
        
    days = {
        0: "понедельник", 1: "вторник", 2: "среду", 
        3: "четверг", 4: "пятницу", 5: "субботу", 6: "воскресенье"
    }
    
    day_name = days.get(date_val.weekday(), "")
    return f"{day_name} ({date_val.strftime('%d.%m')})"

def get_time_with_date():
    """Возвращает строку времени с датой для подвала: 17:15 (23.03)"""
    return datetime.now().strftime("%H:%M (%d.%m)")
    
def format_name_short(full_str):
    """
    Превращает 'Иванов Иван Иванович (Петров)' в 'Иванов И.И. (Петров)'
    Безопасно: если уже сокращено (есть точки), возвращает как есть.
    """
    if not full_str or str(full_str).lower() == "свободно":
        return "свободно"
        
    # ЗАЩИТА: Если в строке уже есть точка, значит имя уже сокращено.
    # Возвращаем сразу, чтобы не испортить отчество и не потерять скобки.
    if "." in str(full_str):
        return full_str
        
    try:
        # 1. Выделяем врача/содержимое скобок
        doctor_part = ""
        if "(" in full_str and ")" in full_str:
            # Берем всё, что внутри скобок
            content = full_str.split('(')[1].split(')')[0]
            doctor_part = f" ({content})"
        
        # 2. Очищаем основное имя от скобок для обработки
        name_only = full_str.split("(")[0].strip()
        
        # 3. Разрезаем ФИО на части
        parts = name_only.split()
        if not parts:
            return full_str

        surname = parts[0].capitalize() # Делаем Фамилию с большой буквы
        initials = ""
        
        # Берем первую букву имени
        if len(parts) >= 2:
            initials += f" {parts[1][0].upper()}."
        
        # Берем первую букву отчества
        if len(parts) >= 3:
            initials += f"{parts[2][0].upper()}."

        # 4. Собираем всё вместе: Фамилия И.И. + (Док)
        return f"{surname}{initials}{doctor_part}"
        
    except Exception:
        # Если что-то пошло не так (например, пустая строка в середине), 
        # возвращаем оригинал без изменений
        return full_str