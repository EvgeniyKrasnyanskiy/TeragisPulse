# -*- coding: utf-8 -*- 
# tg_bot_ws_commands.py
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
        if target_dept not in ['1', '2']:
            return await event.respond("❌ Укажите отделение 1 или 2.")

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
            SELECT ts.name, ts.note, tu.patient_id
            FROM tcalendar tc
            INNER JOIN tseries ts ON tc.series_id = ts.series_id
            INNER JOIN tpatient tu ON ts.patient_id = tu.patient_id
            WHERE tc.calendar_status_id = 4
              AND tc.visitdate BETWEEN %s AND %s
        '''

        try:
            # Предварительно загружаем справочники врачей для подстраховки
            from constants import first_ro_dict, second_ro_dict
            from identification import identification_func # Импорт функции[cite: 5]
            
            try:
                res = await asyncio.to_thread(self.execute_query, query, (start_date, end_date))
            except DatabaseError:
                return await event.respond("⚠️ Ошибка подключения к базе данных.")

            count_1ro = set()    # Все пациенты 1 отделения
            count_2ro = set()    # Все пациенты 2 отделения
            count_unknown = set() # Все остальные

            for row in res:
                s_name, s_note, p_id = row
                series_name = str(s_name).strip()
                detected_dept = None

                # 1. Сначала проверяем серию
                if series_name.startswith('1'):
                    detected_dept = '1'
                elif series_name.startswith('2'):
                    detected_dept = '2'
                
                # 2. Если серия не помогла, используем вашу функцию[cite: 5]
                if detected_dept is None:
                    try:
                        _, _, _, office = identification_func(s_note) #[cite: 5]
                        if office in ['1', '2']:
                            detected_dept = office
                    except:
                        # Ручной поиск если функция упала[cite: 5]
                        if s_note:
                            first_word = s_note.strip().split()[0].split('/')[0].capitalize()
                            if first_word in first_ro_dict: detected_dept = '1'
                            elif first_word in second_ro_dict: detected_dept = '2'

                # РАСПРЕДЕЛЯЕМ ПО ГРУППАМ[cite: 5]
                if detected_dept == '1':
                    count_1ro.add(p_id)
                elif detected_dept == '2':
                    count_2ro.add(p_id)
                else:
                    count_unknown.add(p_id)

            # Теперь из неуточненных убираем тех, кто уже найден в 1 или 2 отделении[cite: 5]
            # (чтобы не было дублей, если у пациента разные серии)
            final_unknown = count_unknown - count_1ro - count_2ro
            
            # Выбираем результат для ответа
            current_count = len(count_1ro) if target_dept == '1' else len(count_2ro)

            df = "%d.%m.%Y"
            period_str = start_date.strftime(df) if start_date == end_date else f"с {start_date.strftime(df)} по {end_date.strftime(df)}"
                
            final_text = (
                f"Пациентов {target_dept}РО отлечено за период {period_str}: {current_count}.\n"
                f"Неуточнённых за этот же период: {len(final_unknown)}."
            )
            await event.respond(final_text)

        except Exception as e:
            logger.error(f"[Bot_CMD] Ошибка: {e}")
            await event.respond("❌ Ошибка при обработке данных.")

    async def help_handler(self, event):
        asyncio.create_task(self._silent_delete(event))
        text = ("<b>Доступные команды:</b>\n\n"
                "/list — список на сегодня\n"
                "/list <07.04> — список на дату\n"
                "/summary <1> <01.04> — кол-во за день\n"
                "/summary <1> <02.04-20.04> — за период\n"
                "/excel <1> <05.05.2026> — отчет в файл\n"
                "/edit <ссылка> <текст> — редактировать\n"
                "/msg_to_group <1/2> <текст> — сообщение в группу\n"
                "/ping — статус связи\n"
                "/help — справка")
        await event.respond(text, parse_mode='html')

    async def ping_handler(self, event):
        asyncio.create_task(self._silent_delete(event))
        start_time = event.message.date.timestamp()
        latency = max(0, round((datetime.now().timestamp() - start_time) * 1000))
        await event.respond(f"🏓 Pong! ({latency} ms)")

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
            SELECT 
                TO_CHAR(tc.visitdate, 'DD.MM.YYYY') as day_group,
                (tp.surname || ' ' || COALESCE(tp.forename, '') || ' ' || COALESCE(tp.title, '')) as fio,
                TO_CHAR(tp.birthdate, 'DD.MM.YYYY') as bd,
                CASE 
                    WHEN tp.sex = 'male' THEN 'М' 
                    WHEN tp.sex = 'female' THEN 'Ж' 
                    ELSE tp.sex 
                END as gender,
                ts.name as series_name,
                ROUND((ts.totaldose / NULLIF(ts.fractionsnumber, 0))::numeric, 2) as dose_per_fr,
                ts.fractionsnumber as plan_fr,
                ROW_NUMBER() OVER (PARTITION BY ts.series_id ORDER BY tc.visitdate, tc.insert_tms) as fact_fr,
                TO_CHAR(MIN(tc.visitdate) OVER (PARTITION BY ts.series_id), 'DD.MM.YYYY') as course_start,
                TO_CHAR(MAX(tc.visitdate) OVER (PARTITION BY ts.series_id), 'DD.MM.YYYY') as course_end
            FROM public.tcalendar tc
            JOIN public.tseries ts ON tc.series_id = ts.series_id
            JOIN public.tpatient tp ON ts.patient_id = tp.patient_id
            WHERE tc.calendar_status_id = 4 
              AND tc.visitdate BETWEEN %s AND %s
              AND (
                  (%s = '1' AND ts.name LIKE %s) OR 
                  (%s = '2' AND ts.name LIKE %s)
              )
            ORDER BY tc.visitdate ASC, fio ASC;
        """

        try:
            try:
                # Передаем 6 параметров: start, end, dept, '1%', dept, '2%'
                res = await asyncio.to_thread(self.execute_query, query, (start_date, end_date, dept_num, '1%', dept_num, '2%'))
            except DatabaseError:
                return await event.respond("📭 Ошибка подключения к базе данных или превышен таймаут.")

            if not res:
                return await event.respond("📭 Данных не найдено за указанный период.")

            final_rows = []
            current_day = ""
            seen_fios = set() # Для удаления дубликатов по ФИО
            unique_patients_count = 0
            
            for row in res:
                day_str, fio, bd, sex, s_name, d_per_fr, p_fr, f_fr, c_start, c_end = row
                fio_upper = fio.strip().upper()

                # Проверка на дубликаты ФИО
                if fio_upper in seen_fios:
                    continue
                seen_fios.add(fio_upper)
                unique_patients_count += 1
                
                if day_str != current_day:
                    if current_day != "": 
                        final_rows.append([""] * 8)
                    final_rows.append([f"--- ДЕНЬ: {day_str} ---", "", "", "", "", "", "", ""])
                    current_day = day_str
                
                accumulated_dose = float(d_per_fr) * int(f_fr)
                fr_format = f"'{f_fr} / {p_fr}" 
                
                final_rows.append([fio_upper, bd, sex, s_name, round(accumulated_dose, 2), fr_format, c_start, c_end])

            final_rows.append([""] * 8)
            final_rows.append([f"ВСЕГО ПАЦИЕНТОВ - {unique_patients_count}", "", "", "", "", "", "", ""])
            final_rows.append([""] * 8)
            final_rows.append(["ОТЧЕТ СФОРМИРОВАН АВТОМАТИЧЕСКИ", "", "", "", "", "", "", ""])
            final_rows.append([f"Выборка: Отделение {dept_num}, Период {period_raw}", "", "", "", "", "", "", ""])

            from reports import generate_custom_patient_excel_report
            file_path = generate_custom_patient_excel_report(final_rows, dept_num, period_raw)

            if file_path and os.path.exists(file_path):
                await self.client.send_file(event.chat_id, str(file_path), caption=f"📊 Отчет по пациентам (Отд. {dept_num})")
                os.remove(str(file_path))
            else:
                await event.respond("❌ Ошибка при сохранении файла.")

        except Exception as e:
            logger.error(f"[Bot_CMD] report_list final error: {e}")
            await event.respond(f"❌ Критическая ошибка: {e}")