import logging
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Callable, Set
from database import execute_query, DatabaseError
from reports import generate_daily_report, load_doctors, extract_doctor

logger = logging.getLogger(__name__)

class ReportManager:
    """Управление автоматической генерацией отчетов и расписанием."""

    def __init__(self, after_callback: Callable, status_ids: Dict[str, int]):
        self.after = after_callback
        self.STATUS_COMPLETED_ID = status_ids.get('COMPLETED', 4)
        self.STATUS_PLANNED_ID = status_ids.get('PLANNED', 1)

    def schedule_reports(self) -> None:
        """Проверяет время каждую минуту и запускает отчет в ключевые точки."""
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            
            # Список контрольных точек
            report_times = ["11:59", "14:59", "17:59", "20:59", "23:59"]
            
            if current_time in report_times:
                self.generate_daily_reports()
                # Чтобы не запустилось несколько раз в течение одной минуты
                self.after(61000, self.schedule_reports)
            else:
                # Проверяем снова через 30 секунд
                self.after(30000, self.schedule_reports)
        except Exception as e:
            logger.error(f"[ReportManager] Ошибка в планировщике: {e}")
            self.after(60000, self.schedule_reports)

    def generate_daily_reports(self) -> None:
        """Генерация отчетов по новым пациентам (первый день в курсе)."""
        r_date = datetime.now().date()
        
        query = '''
            SELECT tu.surname, tu.forename, ts.name, tc.calendar_status_id, ts.note, tu.patient_id, tc.series_id
            FROM tcalendar tc
            INNER JOIN tseries ts ON tc.series_id = ts.series_id
            INNER JOIN tpatient tu ON ts.patient_id = tu.patient_id
            WHERE tc.visitdate = %s
              AND NOT EXISTS (
                  SELECT 1 FROM tcalendar tc2
                  WHERE tc2.series_id = tc.series_id
                    AND tc2.visitdate < tc.visitdate
              )
        '''
        
        try:
            data = execute_query(query, (r_date,))
            if not isinstance(data, list) or not data:
                # Если данных нет, generate_daily_report сам очистит файлы
                generate_daily_report([], report_date=r_date, subfolder="1RO")
                generate_daily_report([], report_date=r_date, subfolder="2RO")
                return

            report_data_1ro: List[Dict[str, Any]] = []
            report_data_2ro: List[Dict[str, Any]] = []

            pids = [int(r[5]) for r in data]
            sids = [int(r[6]) for r in data]
            has_history: Dict[int, bool] = {}
            
            if pids and sids:
                ph_p = ','.join(['%s'] * len(pids))
                ph_s = ','.join(['%s'] * len(sids))
                h_query = f"""
                    SELECT DISTINCT patient_id 
                    FROM tseries ts 
                    INNER JOIN tcalendar tc ON ts.series_id = tc.series_id 
                    WHERE ts.patient_id IN ({ph_p}) 
                      AND ts.series_id NOT IN ({ph_s}) 
                      AND tc.calendar_status_id = %s
                """
                h_data = execute_query(h_query, tuple(pids) + tuple(sids) + (self.STATUS_COMPLETED_ID,))
                if isinstance(h_data, list):
                    has_history = {int(r[0]): True for r in h_data}

            for row in data:
                series_name = str(row[2]).strip()
                raw_surname = str(row[0]).strip()
                raw_forename = str(row[1]).strip() if row[1] else ""
                
                name_parts = raw_forename.split()
                i = f"{name_parts[0][0].upper()}." if len(name_parts) > 0 else ""
                o = f"{name_parts[1][0].upper()}." if len(name_parts) > 1 else ""
                
                patient_name = f"{raw_surname} {i}{o}".strip().upper()

                patient_info = {
                    "patient_name": patient_name,
                    "comment": str(row[4]) if row[4] else "",
                    "is_repeat_plan": has_history.get(int(row[5]), False)
                }
                
                if series_name.startswith("2"):
                    report_data_2ro.append(patient_info)
                elif series_name.startswith("1"):
                    report_data_1ro.append(patient_info)

            generate_daily_report(report_data_1ro, report_date=r_date, subfolder="1RO")
            generate_daily_report(report_data_2ro, report_date=r_date, subfolder="2RO")
            
        except DatabaseError as e:
            logger.error(f"[ReportManager] Ошибка БД при генерации отчета: {e}")
        except Exception as e:
            logger.error(f"[ReportManager] Ошибка генерации отчета: {e}")

    def collect_today_new_patients(self) -> List[Dict[str, Any]]:
        """Собирает список новых пациентов за сегодня (для бота)."""
        today_date = date.today()
        query = '''
            SELECT tu.surname, tu.forename, ts.name, tc.calendar_status_id, ts.note, tu.patient_id, tc.series_id, ts.series_id
            FROM tcalendar tc
            INNER JOIN tseries ts ON tc.series_id = ts.series_id
            INNER JOIN tpatient tu ON ts.patient_id = tu.patient_id
            WHERE tc.visitdate = %s
        '''
        
        try:
            data = execute_query(query, (today_date,))
            if not isinstance(data, list) or not data:
                return []

            # Поиск первой даты для каждой серии
            series_ids = list(set(int(r[7]) for r in data))
            placeholders = ','.join(['%s'] * len(series_ids))
            query_first = f"SELECT series_id, MIN(visitdate) FROM tcalendar WHERE series_id IN ({placeholders}) GROUP BY series_id"
            first_dates = execute_query(query_first, tuple(series_ids))
            
            first_date_by_series: Dict[int, date] = {}
            if isinstance(first_dates, list):
                for sid, vdate in first_dates:
                    if isinstance(vdate, datetime): 
                        vdate = vdate.date()
                    elif isinstance(vdate, str):
                        try:
                            vdate = datetime.strptime(vdate, '%Y-%m-%d').date()
                        except ValueError:
                            continue
                    first_date_by_series[sid] = vdate

            # Проверка истории (второй план)
            pids = list(set(int(r[5]) for r in data))
            has_real_history: Dict[int, bool] = {}
            if pids:
                ph_p = ','.join(['%s'] * len(pids))
                ph_s = ','.join(['%s'] * len(series_ids))
                query_history = f"""
                    SELECT DISTINCT ts.patient_id 
                    FROM tseries ts 
                    INNER JOIN tcalendar tc ON ts.series_id = tc.series_id
                    WHERE ts.patient_id IN ({ph_p})
                      AND ts.series_id NOT IN ({ph_s})
                      AND tc.calendar_status_id = %s
                """
                history_data = execute_query(query_history, tuple(pids) + tuple(series_ids) + (self.STATUS_COMPLETED_ID,))
                if isinstance(history_data, list):
                    for r in history_data:
                        has_real_history[int(r[0])] = True

            new_patients: List[Dict[str, Any]] = []
            for row in data:
                sid = int(row[7])
                if first_date_by_series.get(sid) == today_date:
                    pid = int(row[5])
                    surname = str(row[0]) if row[0] else ""
                    forename = str(row[1]) if row[1] else ""
                    # Форматируем ФИО: Фамилия И.О.
                    name_parts = forename.split()
                    initials = "".join([f" {p[0].upper()}." for p in name_parts if p])
                    
                    new_patients.append({
                        "patient_name": f"{surname}{initials}".strip(),
                        "comment": str(row[4]) if row[4] else "",
                        "is_repeat_plan": has_real_history.get(pid, False),
                    })
            return new_patients
            
        except DatabaseError as e:
            logger.error(f"[ReportManager] Ошибка БД при сборе новых пациентов: {e}")
            return []
        except Exception as e:
            logger.error(f"[ReportManager] Ошибка сбора новых пациентов: {e}")
            return []

    def get_report_text_for_bot(self) -> str:
        """Формирует текстовый отчет для Telegram."""
        try:
            # При запросе отчета ботом также обновляем файлы
            self.generate_daily_reports()
            patients = self.collect_today_new_patients()
            
            if not patients:
                return "ℹ️ На сегодня новых пациентов не найдено."

            lines = ["📊 <b>Отчет по новым пациентам (сегодня):</b>\n"]
            for i, p in enumerate(patients, 1):
                repeat_icon = "② " if p.get('is_repeat_plan') else ""
                comment = f" - <i>{p['comment']}</i>" if p.get('comment') else ""
                lines.append(f"{i}. {repeat_icon}{p['patient_name']}{comment}")

            lines.append("\n✅ <i>Файлы отчетов также обновлены.</i>")
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"[ReportManager] Ошибка отчета для бота: {e}")
            return f"❌ Ошибка генерации отчета: {e}"

