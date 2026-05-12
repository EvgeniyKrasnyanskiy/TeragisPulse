# -*- coding: utf-8 -*- 
# tg_bot_ws_cmd.py
from datetime import datetime, time as dt_time
from telethon import events
import asyncio
import logging
import re
import os
from database import execute_query, DatabaseError 
from identification import identification_func

logger = logging.getLogger("TeragisPulse")
LINK_PATTERN = re.compile(r"t\.me/(?:c/)?([^/]+)/(\d+)")

class BotCommands:
    def __init__(self, client, admin_ids, list_callback, build_message_func, **kwargs):
        self.client = client
        self.admin_ids = [str(aid).strip() for aid in admin_ids]
        self.list_callback = list_callback
        self.build_message = build_message_func
        self.execute_query = execute_query
        self.channel_1ro = kwargs.get('channel_1ro')
        self.channel_2ro = kwargs.get('channel_2ro')
        self.add_admin_callback = kwargs.get('add_admin_callback')
        self.config = kwargs.get('config')
        # ID статуса 'Завершено' из БД
        self.STATUS_COMPLETED = self.config.getint('db_status', 'calendar_completed', fallback=4) if self.config else 4
        
        self._cmd_list_msg_id = None
        self._cmd_list_date = None

    async def init_commands(self):
        """Регистрация обработчиков"""
        handlers = [
            (self.help_handler, '/help'),
            (self.ping_handler, '/ping'),
            (self.list_handler, r'/list(?:\s+(\d{2}\.\d{2}))?'),
            (self.report_list_handler, r'/excel(?:\s+.*)?'),
            (self.report_handler, r'/summary(?:\s+.*)?'),
            (self.edit_handler, r'/edit(?:\s+.*)?'),
            (self.auth_handler, r'/auth(?:\s+(\d+))?'),
            (self.msg_to_group_handler, r'/msg_to_group(?:\s+.*)?')
        ]
        
        for handler, pattern in handlers:
            self.client.add_event_handler(
                handler, 
                events.NewMessage(pattern=pattern, incoming=True, func=self._is_admin)
            )
        
        asyncio.create_task(self._cleanup_task())

    def _is_admin(self, event):
        return str(event.sender_id) in self.admin_ids

    async def _silent_delete(self, event):
        try: 
            await asyncio.sleep(10)
            await event.delete()
        except: pass

    def _parse_date(self, d_str):
        """Парсер: ДД.ММ или ДД.ММ.ГГ(ГГ)"""
        d_str = d_str.strip()
        parts = d_str.split('.')
        current_year = datetime.now().year
        
        if len(parts) == 2:
            return datetime.strptime(f"{d_str}.{current_year}", "%d.%m.%Y").date()
        elif len(parts) == 3:
            year_part = parts[2]
            fmt = "%d.%m.%y" if len(year_part) == 2 else "%d.%m.%Y"
            return datetime.strptime(d_str, fmt).date()
        raise ValueError("Invalid date")

    async def report_handler(self, event):
        """Команда формирования отчета"""
        asyncio.create_task(self._silent_delete(event))
        args = event.message.message.split()
        
        if len(args) < 2:
            return await event.respond("ℹ️ Пример: <code>/summary 1</code> или <code>/summary 2 01.05-15.05</code>", parse_mode='html')

        target_dept = args[1]
        if target_dept not in ['1', '2', '0']:
            return await event.respond("❌ Укажите отделение 1, 2 или 0 (Прочее).")

        target_dept_str = f"{target_dept}РО" if target_dept != '0' else "Прочее"

        date_param = args[2] if len(args) > 2 else None
        
        try:
            if not date_param:
                start_date = end_date = datetime.now().date()
            elif '-' in date_param:
                s_str, e_str = date_param.split('-')
                start_date, end_date = self._parse_date(s_str), self._parse_date(e_str)
            else:
                start_date = end_date = self._parse_date(date_param)
        except Exception:
            return await event.respond("❌ Ошибка формата даты.")

        query = '''
            SELECT tu.patient_id, ts.name, ts.note
            FROM tcalendar tc
            INNER JOIN tseries ts ON tc.series_id = ts.series_id
            INNER JOIN tpatient tu ON ts.patient_id = tu.patient_id
            WHERE tc.calendar_status_id = %s
              AND tc.visitdate BETWEEN %s AND %s
        '''

        try:
            try:
                # Получаем всех пациентов за период для классификации в Python
                res = await asyncio.to_thread(self.execute_query, query, (self.STATUS_COMPLETED, start_date, end_date))
            except DatabaseError:
                return await event.respond("⚠️ Ошибка подключения к базе данных.")

            # Считаем уникальных пациентов с учетом умной классификации
            unique_patients = set()
            for row in res:
                p_id, s_name, s_note = row
                
                # Логика: Сначала маска серии, потом врач
                if str(s_name).startswith('1'):
                    effective_dept = '1'
                elif str(s_name).startswith('2'):
                    effective_dept = '2'
                else:
                    _, _, _, office = identification_func(s_note)
                    effective_dept = office # '1', '2' или '0'

                if effective_dept == target_dept:
                    unique_patients.add(p_id)

            current_count = len(unique_patients)

            df = "%d.%m.%Y"
            period_str = start_date.strftime(df) if start_date == end_date else f"с {start_date.strftime(df)} по {end_date.strftime(df)}"
                
            final_text = (
                f"Пациентов <b>{target_dept_str}</b> отлечено за период {period_str}: <b>{current_count}</b>.\n\n"
                f"<i>Примечание: Подсчет ведется по уникальным ID пациентов. Метод включает проверку ФИО врача, если номер серии не указан. Данные могут отличаться от табло GUI.</i>"
            )
            await event.respond(final_text, parse_mode='html')

        except Exception as e:
            logger.error(f"[Bot_CMD] Ошибка: {e}")
            await event.respond("❌ Ошибка при обработке данных.")

    async def help_handler(self, event):
        asyncio.create_task(self._silent_delete(event))
        text = ("<b>Доступные команды:</b>\n\n"
                "/list [дата] — список пациентов\n"
                "/summary [1/2/0][дата/пер.] — кол-во\n"
                "/excel [1/2/0][дата/пер.] — отчет\n"
                "/edit [ссылка][текст] — редактир.\n"
                "/auth [id] — добавить юзера\n"
                "/msg_to_group [1/2][текст] — смс\n"
                "/ping — пингануть бота\n"
                "/help — справка")
        await event.respond(text, parse_mode='html')

    async def ping_handler(self, event):
        asyncio.create_task(self._silent_delete(event))
        start_time = event.message.date.timestamp()
        latency = max(0, round((datetime.now().timestamp() - start_time) * 1000))
        await event.respond(f"🏓 Pong! ({latency} ms)\nUser ID: <code>{event.sender_id}</code>", parse_mode='html')

    async def auth_handler(self, event):
        """Добавление нового ID администратора"""
        asyncio.create_task(self._silent_delete(event))
        if not self.add_admin_callback:
            return await event.respond("❌ Ошибка: callback авторизации не настроен.")
            
        new_id = event.pattern_match.group(1)
        if not new_id:
            return await event.respond("ℹ️ Пример: <code>/auth 12345678</code>", parse_mode='html')
            
        if self.add_admin_callback(new_id):
            await event.respond(f"✅ Пользователь <code>{new_id}</code> теперь в списке доверенных.", parse_mode='html')
        else:
            await event.respond(f"ℹ️ Пользователь <code>{new_id}</code> уже был в списке.")

    async def edit_handler(self, event):
        asyncio.create_task(self._silent_delete(event))
        raw_text = event.message.message
        args = re.sub(r'^/edit\s*', '', raw_text).strip()
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            await self.client.edit_message(event.chat_id, reply_msg.id, args, parse_mode='html')

    async def list_handler(self, event):
        asyncio.create_task(self._silent_delete(event))
        date_param = event.pattern_match.group(1) 
        target_date = date_param if date_param else datetime.now().strftime("%d.%m")
        raw_data = await asyncio.to_thread(self.list_callback, target_date)
        data = raw_data if isinstance(raw_data, dict) else {'list_text': raw_data}
        await event.respond(self.build_message(data), parse_mode='html')

    async def _cleanup_task(self):
        while True:
            now = datetime.now()
            target = datetime.combine(now.date(), dt_time(23, 59, 50))
            wait = (target - now).total_seconds()
            if wait < 0: wait += 86400
            await asyncio.sleep(wait)
            if self._cmd_list_msg_id:
                try: await self.client.delete_messages(None, self._cmd_list_msg_id)
                except: pass
                finally:
                    self._cmd_list_msg_id, self._cmd_list_date = None, None

    async def msg_to_group_handler(self, event):
        """Отправка произвольного сообщения в группу 1 или 2"""
        asyncio.create_task(self._silent_delete(event))
        raw_text = event.message.message
        # Парсим команду: /msg_to_group <номер> <текст>
        match = re.match(r'^/msg_to_group\s+([12])\s+(.+)', raw_text, re.DOTALL)
        if not match:
            return await event.respond("ℹ️ Пример: <code>/msg_to_group 1 Важное сообщение</code>", parse_mode='html')
        
        dept_num = match.group(1)
        msg_text = match.group(2).strip()
        
        target_chat = self.channel_1ro if dept_num == '1' else self.channel_2ro
        if not target_chat:
            return await event.respond("❌ ID группы не настроен.")

        try:
            await self.client.send_message(int(target_chat), msg_text, parse_mode='html')
            await event.respond(f"✅ Сообщение отправлено в группу {dept_num}РО.")
        except Exception as e:
            logger.error(f"[Bot_CMD] Ошибка отправки в группу: {e}")
            await event.respond(f"❌ Ошибка отправки: {e}")
                    
    async def report_list_handler(self, event):
        """Оптимизированная команда /report_list без технических меток в коде"""
        asyncio.create_task(self._silent_delete(event))
        args = event.message.message.split()
        
        if len(args) < 3:
            return await event.respond("ℹ️ Пример: <code>/excel 1 05.05.2026</code>", parse_mode='html')

        dept_num = args[1]
        if dept_num not in ['1', '2', '0']:
            return await event.respond("❌ Укажите отделение 1, 2 или 0 (Прочее).")
        
        dept_label = f"{dept_num}РО" if dept_num != '0' else "Прочее"
        period_raw = args[2]

        try:
            if '-' in period_raw:
                s_str, e_str = period_raw.split('-')
                start_date, end_date = self._parse_date(s_str), self._parse_date(e_str)
            else:
                start_date = end_date = self._parse_date(period_raw)
        except Exception:
            return await event.respond("❌ Ошибка формата даты.")

        # Безопасный SQL запрос на чтение
        query = """
            WITH patient_course_stats AS (
                -- Считаем общую полученную дозу и фракции по ВСЕМ сериям пациента за всё время
                SELECT 
                    ts.patient_id,
                    COUNT(tc.calendar_id) as total_released_fr,
                    SUM(ROUND((ts.totaldose / NULLIF(ts.fractionsnumber, 0))::numeric, 2)) as total_received_dose,
                    MIN(ts.series_id) as first_series_id
                FROM public.tcalendar tc
                JOIN public.tseries ts ON tc.series_id = ts.series_id
                WHERE tc.calendar_status_id = %s
                GROUP BY ts.patient_id
            ),
            first_series_data AS (
                -- Берем предписанные дозу и фракции именно из ПЕРВОЙ серии
                SELECT 
                    series_id,
                    totaldose as first_plan_dose,
                    fractionsnumber as first_plan_fr
                FROM public.tseries
            )
            SELECT 
                TO_CHAR(tc.visitdate, 'DD.MM.YYYY') as day_group,
                tp.patient_id,
                (tp.surname || ' ' || COALESCE(tp.forename, '') || ' ' || COALESCE(tp.title, '')) as fio,
                TO_CHAR(tp.birthdate, 'DD.MM.YYYY') as bd,
                CASE 
                    WHEN tp.sex = 'male' THEN 'М' 
                    WHEN tp.sex = 'female' THEN 'Ж' 
                    ELSE tp.sex 
                END as gender,
                ts.name as series_name,
                ts.note,
                -- Новые поля статистики
                fsd.first_plan_dose,
                pcs.total_received_dose,
                pcs.total_released_fr,
                fsd.first_plan_fr,
                -- Даты курса (по конкретной серии)
                TO_CHAR(MIN(tc.visitdate) OVER (PARTITION BY ts.series_id), 'DD.MM.YYYY') as course_start,
                TO_CHAR(MAX(tc.visitdate) OVER (PARTITION BY ts.series_id), 'DD.MM.YYYY') as course_end
            FROM public.tcalendar tc
            JOIN public.tseries ts ON tc.series_id = ts.series_id
            JOIN public.tpatient tp ON ts.patient_id = tp.patient_id
            JOIN patient_course_stats pcs ON tp.patient_id = pcs.patient_id
            JOIN first_series_data fsd ON pcs.first_series_id = fsd.series_id
            WHERE tc.calendar_status_id = %s 
              AND tc.visitdate BETWEEN %s AND %s
            ORDER BY tc.visitdate ASC, fio ASC;
        """

        try:
            try:
                # Передаем параметры: статус для CTE и статус/даты для основного запроса
                res = await asyncio.to_thread(self.execute_query, query, (self.STATUS_COMPLETED, self.STATUS_COMPLETED, start_date, end_date))
            except DatabaseError:
                return await event.respond("📭 Ошибка подключения к базе данных или превышен таймаут.")

            if not res:
                return await event.respond("📭 Данных не найдено за указанный период.")

            final_rows = []
            current_day = ""
            seen_patients = set() # Используем patient_id для 100% точности
            unique_patients_count = 0
            
            for row in res:
                # row[0] - day_group, row[1] - patient_id, row[2] - fio...
                day_str = row[0]
                p_id = row[1]
                fio, bd, sex, s_name, s_note, p_dose, r_dose, r_fr, p_fr, c_start, c_end = row[2:]
                
                # КЛАССИФИКАЦИЯ (Умное определение отделения)
                if str(s_name).startswith('1'):
                    effective_dept = '1'
                elif str(s_name).startswith('2'):
                    effective_dept = '2'
                else:
                    _, _, _, office = identification_func(s_note)
                    effective_dept = office

                if effective_dept != dept_num:
                    continue
                if p_id in seen_patients:
                    continue
                seen_patients.add(p_id)
                unique_patients_count += 1
                
                # Добавляем заголовок дня только если нашли в нем уникального пациента
                if day_str != current_day:
                    if current_day != "": 
                        final_rows.append([""] * 8)
                    final_rows.append([f"--- ДЕНЬ: {day_str} ---", "", "", "", "", "", "", ""])
                    current_day = day_str
                
                # Форматируем Дозу: Предписано(1-я сер) / Получено(всего)
                dose_format = f"{p_dose} / {round(float(r_dose), 2)}"
                
                # Форматируем Фракции: Отпущено(всего) / Предписано(1-я сер)
                fr_format = f"'{r_fr} / {p_fr}" 
                
                final_rows.append([fio.upper(), bd, sex, s_name, dose_format, fr_format, c_start, c_end])

            final_rows.append([""] * 8)
            final_rows.append([f"ВСЕГО ПАЦИЕНТОВ ({dept_label}) - {unique_patients_count}", "", "", "", "", "", "", ""])
            final_rows.append([""] * 8)
            final_rows.append(["ПРИМЕЧАНИЕ: Статистика курса (Доза/Фр) берется из ПЕРВОЙ серии пациента.", "", "", "", "", "", "", ""])
            final_rows.append(["Отчет может отличаться от GUI за счет умной привязки по врачу.", "", "", "", "", "", "", ""])
            final_rows.append([""] * 8)
            final_rows.append(["ОТЧЕТ СФОРМИРОВАН АВТОМАТИЧЕСКИ", "", "", "", "", "", "", ""])
            final_rows.append([f"Выборка: {dept_label}, Период {period_raw}", "", "", "", "", "", "", ""])

            from reports import generate_custom_patient_excel_report
            file_path = generate_custom_patient_excel_report(final_rows, dept_num, period_raw)

            if file_path and os.path.exists(file_path):
                caption = (f"📊 Отчет по пациентам ({dept_label})\n"
                           f"🏁 Период: {period_raw}\n"
                           f"👥 Уникальных: {unique_patients_count}\n"
                           f"⚠️ Считается по ID + фамилии врача.")
                await self.client.send_file(event.chat_id, str(file_path), caption=caption)
                os.remove(str(file_path))
            else:
                await event.respond("❌ Ошибка при сохранении файла.")

        except Exception as e:
            logger.error(f"[Bot_CMD] report_list final error: {e}")
            await event.respond(f"❌ Критическая ошибка: {e}")