# -*- coding: utf-8 -*- 
# tg_bot_ws_cmd.py
from datetime import datetime, time as dt_time
from telethon import events
import asyncio
import logging
import re
import os
import html
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
            (self.find_patient_handler, r'/find_patient(?:\s+.*)?'),
            (self.patient_info_handler, r'/patient_info(?:\s+.*)?'),
            (self.patient_info_handler, r'/patient_(\d+)'),
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

    async def _get_patients_dept_map(self, patient_ids: list) -> dict:
        """
        Собирает все серии для списка пациентов и определяет отделение для каждого.
        Логика: если во всех сериях (где есть данные) одно отделение - используем его.
        Если данные противоречивы - отделение '0'.
        """
        if not patient_ids:
            return {}
        
        # Уникальные ID для запроса
        u_pids = list(set(patient_ids))
        placeholders = ','.join(['%s'] * len(u_pids))
        query = f"SELECT patient_id, name, note FROM tseries WHERE patient_id IN ({placeholders})"
        
        try:
            res = await asyncio.to_thread(self.execute_query, query, tuple(u_pids))
        except DatabaseError:
            return {}

        patient_series = {}
        for p_id, s_name, s_note in res:
            if p_id not in patient_series:
                patient_series[p_id] = []
            patient_series[p_id].append((s_name, s_note))
            
        dept_map = {}
        for p_id, series_list in patient_series.items():
            doc_depts = set()
            name_depts = set()
            
            for s_name, s_note in series_list:
                # Собираем все упоминания отделений по врачам
                _, _, _, office = identification_func(s_note)
                if office in ['1', '2']:
                    doc_depts.add(office)
                
                # Собираем все упоминания отделений по названиям серий
                if str(s_name).startswith('1'):
                    name_depts.add('1')
                elif str(s_name).startswith('2'):
                    name_depts.add('2')
            
            # ПРИНЯТИЕ РЕШЕНИЯ (Иерархия)
            if len(doc_depts) == 1:
                # 1. Приоритет: Есть однозначный врач во всех сериях
                dept_map[p_id] = list(doc_depts)[0]
            elif len(doc_depts) > 1:
                # 2. Конфликт врачей: Пытаемся разрешить через номера серий
                if len(name_depts) == 1:
                    dept_map[p_id] = list(name_depts)[0]
                else:
                    dept_map[p_id] = '0' # Неразрешимый конфликт
            else:
                # 3. Врачей нет: Идентифицируем только по номерам серий
                if len(name_depts) == 1:
                    dept_map[p_id] = list(name_depts)[0]
                else:
                    dept_map[p_id] = '0'
                
        return dept_map

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

            # 1. Собираем все ID пациентов для массового определения отделений
            all_patient_ids = [row[0] for row in res]
            dept_map = await self._get_patients_dept_map(all_patient_ids)

            # 2. Считаем уникальных пациентов с учетом истории всех их серий
            unique_patients = set()
            for row in res:
                p_id = row[0]
                effective_dept = dept_map.get(p_id, '0')

                if effective_dept == target_dept:
                    unique_patients.add(p_id)

            current_count = len(unique_patients)

            df = "%d.%m.%Y"
            period_str = start_date.strftime(df) if start_date == end_date else f"с {start_date.strftime(df)} по {end_date.strftime(df)}"
                
            final_text = (
                f"Пациентов <b>{target_dept_str}</b> отлечено за период {period_str}: <b>{current_count}</b>."
            )
            await event.respond(final_text, parse_mode='html')

        except Exception as e:
            logger.error(f"[Bot_CMD] Ошибка: {e}")
            await event.respond("❌ Ошибка при обработке данных.")

    async def help_handler(self, event):
        asyncio.create_task(self._silent_delete(event))
        text = ("<b>Доступные команды:</b>\n\n"
                "/list [дата] — список пациентов\n"
                "/find_patient [запрос] — нечеткий поиск пациентов\n"
                "/patient_info [ФИО/ID] — детальная инфо о пациенте\n"
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
            unknown_rows = []
            seen_patients = set() # Используем patient_id для 100% точности
            unique_patients_count = 0
            unknown_count = 0
            
            # 1. Массовая классификация пациентов по всем сериям
            all_pids = [r[1] for r in res]
            dept_map = await self._get_patients_dept_map(all_pids)

            current_day = ""
            for row in res:
                # row[0] - day_group, row[1] - patient_id, row[2] - fio...
                day_str = row[0]
                p_id = row[1]
                fio, bd, sex, s_name, s_note, p_dose, r_dose, r_fr, p_fr, c_start, c_end = row[2:]
                
                effective_dept = dept_map.get(p_id, '0')

                if p_id in seen_patients:
                    continue
                seen_patients.add(p_id)
                
                # Форматируем Дозу: Предписано(1-я сер) / Получено(всего)
                dose_format = f"{p_dose} / {round(float(r_dose), 2)}"
                # Форматируем Фракции: Отпущено(всего) / Предписано(1-я сер)
                fr_format = f"'{r_fr} / {p_fr}" 
                patient_data = [fio.upper(), bd, sex, s_name, dose_format, fr_format, c_start, c_end]

                if effective_dept == dept_num:
                    unique_patients_count += 1
                    # Добавляем заголовок дня только если нашли в нем уникального пациента
                    if day_str != current_day:
                        if current_day != "": 
                            final_rows.append([""] * 8)
                        final_rows.append([f"--- ДЕНЬ: {day_str} ---", "", "", "", "", "", "", ""])
                        current_day = day_str
                    final_rows.append(patient_data)
                elif effective_dept == '0' and dept_num != '0':
                    # Собираем "потеряшек" для вывода в конце
                    unknown_count += 1
                    unknown_rows.append(patient_data)

            # Итоговый блок для целевого отделения
            final_rows.append([""] * 8)
            final_rows.append([f"ИТОГО ПАЦИЕНТОВ ({dept_label}): {unique_patients_count}", "", "", "", "", "", "", ""])
            final_rows.append(["", "", "", "", "", "", "", ""])
            final_rows.append(["ПРАВИЛА ИДЕНТИФИКАЦИИ (Иерархия):", "", "", "", "", "", "", ""])
            final_rows.append(["1. Уникальный врач во всех сериях пациента (приоритет).", "", "", "", "", "", "", ""])
            final_rows.append(["2. При отсутствии или конфликте врачей - уникальный номер серии (1/2).", "", "", "", "", "", "", ""])
            final_rows.append(["3. В остальных случаях пациент попадает в группу 0.", "", "", "", "", "", "", ""])
            
            # Если есть неидентифицированные и мы в отчете РО - добавляем их
            if unknown_rows and dept_num != '0':
                final_rows.append([""] * 8)
                final_rows.append(["--- НЕИДЕНТИФИЦИРОВАННЫЕ ПАЦИЕНТЫ (ГРУППА 0) ---", "", "", "", "", "", "", ""])
                final_rows.append(["(Бот не нашел фамилию врача или номер отделения в сериях)", "", "", "", "", "", "", ""])
                final_rows.extend(unknown_rows)
                final_rows.append([f"ВСЕГО НЕИДЕНТИФИЦИРОВАННЫХ: {unknown_count}", "", "", "", "", "", "", ""])

            final_rows.append([""] * 8)
            final_rows.append(["ОТЧЕТ СФОРМИРОВАН АВТОМАТИЧЕСКИ", "", "", "", "", "", "", ""])
            final_rows.append([f"Выборка: {dept_label}, Период {period_raw}", "", "", "", "", "", "", ""])

            from reports import generate_custom_patient_excel_report
            file_path = generate_custom_patient_excel_report(final_rows, dept_num, period_raw)

            if file_path and os.path.exists(file_path):
                caption = (f"📊 Отчет по пациентам ({dept_label})\n"
                           f"🏁 Период: {period_raw}\n"
                           f"👥 Уникальных: {unique_patients_count}")
                if unknown_count > 0 and dept_num != '0':
                    caption += f"\n⚠️ Не определено: {unknown_count} (в конце файла)"
                
                await self.client.send_file(event.chat_id, str(file_path), caption=caption)
                os.remove(str(file_path))
            else:
                await event.respond("❌ Ошибка при сохранении файла.")

        except Exception as e:
            logger.error(f"[Bot_CMD] report_list final error: {e}")
            await event.respond(f"❌ Критическая ошибка: {e}")

    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """Вычисляет расстояние Левенштейна между двумя строками"""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
            
        return previous_row[-1]

    def _fuzzy_match(self, query: str, patient_fio: str) -> float:
        """Рассчитывает нечеткое совпадение токенов на основе Левенштейна (0.0 - 1.0)"""
        q_tokens = [t.lower() for t in query.split() if t]
        p_tokens = [t.lower() for t in patient_fio.split() if t]
        
        if not q_tokens or not p_tokens:
            return 0.0
            
        total_score = 0.0
        for q_tok in q_tokens:
            best_tok_score = 0.0
            for p_tok in p_tokens:
                if q_tok == p_tok:
                    score = 1.0
                elif p_tok.startswith(q_tok) or q_tok.startswith(p_tok):
                    score = min(len(q_tok), len(p_tok)) / max(len(q_tok), len(p_tok)) * 0.8
                else:
                    dist = self._levenshtein_distance(q_tok, p_tok)
                    max_len = max(len(q_tok), len(p_tok))
                    score = (max_len - dist) / max_len if max_len > 0 else 0.0
                
                if score > best_tok_score:
                    best_tok_score = score
            total_score += best_tok_score
            
        return total_score / len(q_tokens)

    async def find_patient_handler(self, event):
        """Нечеткий поиск пациентов по ФИО"""
        asyncio.create_task(self._silent_delete(event))
        raw_text = event.message.message
        query_text = re.sub(r'^/find_patient\s*', '', raw_text).strip()
        
        if not query_text:
            return await event.respond("ℹ️ Пример: <code>/find_patient иванов иван</code>", parse_mode='html')
            
        # Загружаем всех пациентов для поиска
        sql = "SELECT patient_id, surname, forename, title, birthdate FROM tpatient"
        try:
            res = await asyncio.to_thread(self.execute_query, sql)
        except DatabaseError as e:
            logger.error(f"[Bot_CMD] Ошибка поиска пациентов в БД: {e}")
            return await event.respond("⚠️ Ошибка подключения к базе данных.")
            
        if not res:
            return await event.respond("📭 База данных пациентов пуста.")
            
        candidates = []
        for p_id, surname, forename, title, birthdate in res:
            fio = f"{surname or ''} {forename or ''} {title or ''}".strip()
            score = self._fuzzy_match(query_text, fio)
            if score >= 0.4:  # Порог сходства
                candidates.append((p_id, fio, birthdate, score))
                
        # Сортируем по убыванию очков сходства
        candidates.sort(key=lambda x: x[3], reverse=True)
        
        if not candidates:
            return await event.respond(f"📭 Пациенты по запросу «<b>{html.escape(query_text)}</b>» не найдены.", parse_mode='html')
            
        # Выводим топ-10 совпадений
        response_lines = [f"🔍 Результаты поиска по запросу «<b>{html.escape(query_text)}</b>»:\n"]
        for i, (p_id, fio, birthdate, score) in enumerate(candidates[:10], 1):
            bd_str = birthdate.strftime('%d.%m.%Y') if birthdate else "—"
            response_lines.append(f"{i}. 👤 <b>{fio.upper()}</b> (ДР: {bd_str})")
            response_lines.append(f"   👉 Получить инфо: /patient_{p_id}\n")
            
        await event.respond("\n".join(response_lines), parse_mode='html')

    async def patient_info_handler(self, event):
        """Получение подробной информации о пациенте по ID или нечеткому поиску имени"""
        asyncio.create_task(self._silent_delete(event))
        raw_text = event.message.message
        
        # Проверяем, вызвано ли через /patient_123
        match_click = re.match(r'^/patient_(\d+)', raw_text)
        patient_id = None
        
        if match_click:
            patient_id = int(match_click.group(1))
            query_arg = str(patient_id)
        else:
            query_arg = re.sub(r'^/patient_info\s*', '', raw_text).strip()
            if not query_arg:
                return await event.respond("ℹ️ Пример: <code>/patient_info иванов иван</code> или <code>/patient_info 125</code>", parse_mode='html')
            
            if query_arg.isdigit():
                patient_id = int(query_arg)
                
        # Если у нас есть patient_id, запрашиваем конкретного пациента
        if patient_id is not None:
            sql_pat = "SELECT patient_id, surname, forename, title, birthdate, sex, patient_unique_number FROM tpatient WHERE patient_id = %s"
            try:
                res_pat = await asyncio.to_thread(self.execute_query, sql_pat, (patient_id,))
            except DatabaseError as e:
                logger.error(f"[Bot_CMD] Ошибка получения пациента: {e}")
                return await event.respond("⚠️ Ошибка подключения к базе данных.")
                
            if not res_pat:
                return await event.respond(f"❌ Пациент с ID <code>{patient_id}</code> не найден.", parse_mode='html')
            
            patient_data = res_pat[0]
        else:
            # Ищем нечетким поиском
            sql_all = "SELECT patient_id, surname, forename, title, birthdate, sex, patient_unique_number FROM tpatient"
            try:
                res_all = await asyncio.to_thread(self.execute_query, sql_all)
            except DatabaseError as e:
                logger.error(f"[Bot_CMD] Ошибка поиска пациентов в БД: {e}")
                return await event.respond("⚠️ Ошибка подключения к базе данных.")
                
            candidates = []
            for row in res_all:
                p_id, surname, forename, title, birthdate, sex, pun = row
                fio = f"{surname or ''} {forename or ''} {title or ''}".strip()
                score = self._fuzzy_match(query_arg, fio)
                if score >= 0.4:
                    candidates.append((row, fio, score))
                    
            candidates.sort(key=lambda x: x[2], reverse=True)
            
            if not candidates:
                return await event.respond(f"📭 Пациенты по запросу «<b>{html.escape(query_arg)}</b>» не найдены.", parse_mode='html')
                
            # Если точное совпадение одно (score >= 0.9) или кандидат ровно один с хорошим счетом
            if len(candidates) == 1 or (candidates[0][2] >= 0.9 and (len(candidates) == 1 or candidates[1][2] < 0.8)):
                patient_data = candidates[0][0]
            else:
                # Предлагаем список
                response_lines = [f"❓ Найдено несколько похожих пациентов по запросу «<b>{html.escape(query_arg)}</b>». Какого именно прислать?\n"]
                for i, (row, fio, score) in enumerate(candidates[:10], 1):
                    p_id, _, _, _, birthdate, _, _ = row
                    bd_str = birthdate.strftime('%d.%m.%Y') if birthdate else "—"
                    response_lines.append(f"{i}. 👤 <b>{fio.upper()}</b> (ДР: {bd_str})")
                    response_lines.append(f"   👉 Получить инфо: /patient_{p_id}\n")
                return await event.respond("\n".join(response_lines), parse_mode='html')
                
        # Формируем полную красивую карточку для конкретного patient_data
        p_id, surname, forename, title, birthdate, sex, pun = patient_data
        fio = f"{surname or ''} {forename or ''} {title or ''}".strip().upper()
        bd_str = birthdate.strftime('%d.%m.%Y') if birthdate else "—"
        
        # Рассчитаем возраст
        age_str = ""
        if birthdate:
            today = datetime.now().date()
            age = today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))
            age_str = f" ({age} лет)"
            
        gender = "Мужской" if sex == "male" else ("Женский" if sex == "female" else str(sex))
        
        # Получаем серии (курсы лечения)
        sql_series = """
            SELECT series_id, name, totaldose, fractionsnumber, note, insert_tms, series_status_id
            FROM tseries
            WHERE patient_id = %s
            ORDER BY insert_tms DESC
        """
        try:
            res_series = await asyncio.to_thread(self.execute_query, sql_series, (p_id,))
        except DatabaseError as e:
            logger.error(f"[Bot_CMD] Ошибка получения серий: {e}")
            res_series = []
            
        series_blocks = []
        for s_id, s_name, plan_dose, plan_fr, note, insert_tms, s_status_id in res_series:
            # 1. Дата создания серии
            creation_date = insert_tms.strftime('%d.%m.%Y') if insert_tms else "—"
            
            # 2. Определяем РО отделение по врачу / серии
            _, _, _, office = identification_func(note) if note else ('🧑‍⚕ —', '☢ —', '—', '0')
            dept_str = f"{office}РО" if office in ['1', '2'] else "Прочее (0)"
            
            # 3. Получаем даты первого и последнего завершенного сеанса в tcalendar
            sql_dates = """
                SELECT MIN(visitdate), MAX(visitdate), COUNT(calendar_id)
                FROM tcalendar
                WHERE series_id = %s AND calendar_status_id = %s
            """
            try:
                res_dates = await asyncio.to_thread(self.execute_query, sql_dates, (s_id, self.STATUS_COMPLETED))
            except DatabaseError:
                res_dates = [(None, None, 0)]
                
            first_date_val, last_date_val, completed_fr = res_dates[0] if res_dates else (None, None, 0)
            
            first_session_str = first_date_val.strftime('%d.%m.%Y') if first_date_val else "—"
            last_session_str = last_date_val.strftime('%d.%m.%Y') if last_date_val else "—"
            
            # 4. Подсчитываем реальную полученную дозу (суммируем dose_real по сеансам)
            # В tcalendar завершенный сеанс означает, что фракция отпущена.
            # Доза за одну фракцию = plan_dose / plan_fr (если plan_fr > 0)
            single_dose = plan_dose / plan_fr if plan_fr and plan_fr > 0 else 0.0
            received_dose = completed_fr * single_dose
            
            # 5. Статус серии
            status_map = {
                1: "В лечении ☢",
                2: "Не начато 🆕",
                3: "Перерыв ⏸",
                4: "Приостановлено 🛑",
                5: "Остановлено ❌",
                6: "Завершено 🏁"
            }
            status_str = status_map.get(s_status_id, f"Статус {s_status_id}")
            
            # Формируем блок серии
            block = (
                f"🔸 <b>Серия:</b> «{html.escape(str(s_name))}»\n"
                f"   🏥 <b>Отделение:</b> {dept_str} | 📌 <b>Статус:</b> {status_str}\n"
                f"   📅 <b>Создана:</b> {creation_date}\n"
                f"   📅 <b>Первый сеанс:</b> {first_session_str}\n"
                f"   📅 <b>Последний сеанс:</b> {last_session_str}\n"
                f"   📈 <b>Фракции:</b> {completed_fr} из {plan_fr or 0}\n"
                f"   ☢ <b>Доза:</b> {round(received_dose, 2)} Гр из {plan_dose or 0} Гр\n"
                f"   📝 <b>Примечание:</b> {html.escape(note or '—')}"
            )
            series_blocks.append(block)
            
        series_text = "\n\n".join(series_blocks) if series_blocks else "📭 Лечебные курсы не найдены."
        
        card_text = (
            f"👤 <b>КАРТОЧКА ПАЦИЕНТА:</b> {fio}\n"
            f"─────────────────────\n"
            f"🆔 <b>ID пациента:</b> <code>{p_id}</code>\n"
            f"🔢 <b>Уникальный номер:</b> <code>{pun or '—'}</code>\n"
            f"📅 <b>Дата рождения:</b> {bd_str}{age_str}\n"
            f"🚻 <b>Пол:</b> {gender}\n\n"
            f"📚 <b>КУРСЫ ЛЕЧЕНИЯ (СЕРИИ):</b>\n\n"
            f"{series_text}"
        )
        await event.respond(card_text, parse_mode='html')