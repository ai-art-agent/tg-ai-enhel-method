# -*- coding: utf-8 -*-
"""
Единый тестовый UI: ручной диалог с ботом и режим «Авто» (два бота — психолог + симулятор пользователя).
Сообщения в облачках как в Telegram, кнопки под ответом бота; нажатия симулятора подсвечены зелёным.
Техническая информация (валидатор и т.п.) — в красных облачках.

Запуск: python test_dialog_ui.py (из папки проекта, .env с DEEPSEEK_API_KEY).
"""

import asyncio
import json
import logging
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk, font as tkfont

TEST_USER_ID = 0
LOG = logging.getLogger("test_dialog_ui")

# Цвета облачков (как в Telegram)
COLOR_BOT = "#E8F4FD"          # голубой — психолог
COLOR_USER = "#E7FDD3"        # зелёный — пользователь
COLOR_SIMULATOR_CLICK = "#B8E986"  # ярко-зелёный — кнопка, нажатая симулятором
COLOR_TECH = "#FFDDDD"        # красный — техническая информация
BORDER_BOT = "#C5E1F5"
BORDER_USER = "#A8D98A"
BORDER_TECH = "#E8A0A0"


def _setup_terminal_logging():
    log = logging.getLogger("test_dialog_ui")
    log.setLevel(logging.DEBUG)
    for h in log.handlers:
        if getattr(h, "stream", None) is sys.stdout:
            return
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter("[%(asctime)s] [Тест] %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(h)
    log.info("Тестовый UI запущен.")


def run_async_in_thread(dialog_runner):
    """Запускает dialog_runner(queue) в отдельном потоке. dialog_runner сам вызывает asyncio.run. Возвращает queue.Queue."""
    result_q = queue.Queue()
    def worker():
        try:
            dialog_runner(result_q)
        except Exception as e:
            result_q.put(("error", str(e)))
    threading.Thread(target=worker, daemon=True).start()
    return result_q


def main():
    from bot import (
        clear_history,
        get_bot_reply,
        get_simulator_reply,
        SIMULATOR_ENABLED,
    )

    _setup_terminal_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    root = tk.Tk()
    root.title("Тест бота (без Telegram)")
    root.geometry("560x620")
    root.minsize(420, 400)

    # Контейнер для облачков: Canvas + Scrollbar + внутренний Frame
    canvas_container = ttk.Frame(root)
    canvas_container.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

    canvas = tk.Canvas(canvas_container, highlightthickness=0)
    scrollbar = ttk.Scrollbar(canvas_container)
    content = ttk.Frame(canvas)

    content_id = canvas.create_window((0, 0), window=content, anchor=tk.NW)

    def _on_canvas_configure(_):
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig(content_id, width=canvas.winfo_width())

    def _on_content_configure(_):
        canvas.configure(scrollregion=canvas.bbox("all"))

    canvas.bind("<Configure>", _on_canvas_configure)
    content.bind("<Configure>", _on_content_configure)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.configure(command=canvas.yview)

    def _on_mousewheel(event):
        if getattr(event, "num", None) == 5 or (getattr(event, "delta", 0) < 0):
            canvas.yview_scroll(3, "units")
        elif getattr(event, "num", None) == 4 or (getattr(event, "delta", 0) > 0):
            canvas.yview_scroll(-3, "units")

    def _bind_mousewheel(_=None):
        root.bind_all("<MouseWheel>", _on_mousewheel)
        root.bind_all("<Button-4>", _on_mousewheel)
        root.bind_all("<Button-5>", _on_mousewheel)

    def _unbind_mousewheel(_=None):
        root.unbind_all("<MouseWheel>")
        root.unbind_all("<Button-4>")
        root.unbind_all("<Button-5>")

    canvas.bind("<MouseWheel>", _on_mousewheel)
    canvas.bind("<Button-4>", _on_mousewheel)
    canvas.bind("<Button-5>", _on_mousewheel)
    canvas_container.bind("<Enter>", _bind_mousewheel)
    canvas_container.bind("<Leave>", _unbind_mousewheel)
    content.bind("<MouseWheel>", _on_mousewheel)
    content.bind("<Button-4>", _on_mousewheel)
    content.bind("<Button-5>", _on_mousewheel)

    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # Рамка для кнопок бота (под облачками)
    buttons_frame = ttk.Frame(root)
    buttons_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=2)

    top_bar = ttk.Frame(root)
    top_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=4)

    stop_flag = [False]
    entry_frame = ttk.Frame(root)
    entry_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=6)
    entry = ttk.Entry(entry_frame, font=("Segoe UI", 11))
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

    bubble_font = tkfont.Font(family="Segoe UI", size=10)
    tech_font = tkfont.Font(family="Consolas", size=9)

    def _insert_telegram_style(txt_widget: tk.Text, text: str, base_font, fg: str):
        """Вставляет текст в Text, конвертируя **жирный** в выделение жирным шрифтом (как в Telegram)."""
        if not text:
            return
        try:
            actual = base_font.actual() if hasattr(base_font, "actual") else {}
        except Exception:
            actual = {}
        family = actual.get("family", "Segoe UI")
        size = actual.get("size", 10)
        bold_font = tkfont.Font(family=family, size=size, weight="bold")
        try:
            txt_widget.tag_configure("bold", font=bold_font, foreground=fg)
        except Exception:
            txt_widget.tag_configure("bold", foreground=fg)
        parts = re.split(r"\*\*(.+?)\*\*", text)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                txt_widget.insert(tk.END, part)
            else:
                start = txt_widget.index(tk.END)
                txt_widget.insert(tk.END, part)
                txt_widget.tag_add("bold", start, tk.END)

    def _make_selectable_text(parent, text: str, font, bg, fg, width_chars: int = 52):
        """Текст можно выделять мышью и копировать (Ctrl+C или правый клик → Копировать). Поддерживается **жирный** как в Telegram."""
        lines = (text or "").split("\n")
        height = max(1, min(25, sum(1 + len(l) // width_chars for l in lines)))
        txt = tk.Text(
            parent, font=font, wrap=tk.WORD, state=tk.NORMAL, height=height,
            bg=bg, fg=fg, padx=0, pady=0, relief=tk.FLAT, cursor="xterm",
            width=width_chars, insertbackground=fg, selectbackground="#B4D5FE", selectforeground="#000",
            takefocus=True, exportselection=True,
        )
        base_font = font if isinstance(font, tkfont.Font) else bubble_font
        try:
            _insert_telegram_style(txt, text or "", base_font, fg)
        except Exception:
            txt.insert(tk.END, text or "")
        def _do_copy(_=None):
            try:
                if txt.tag_ranges(tk.SEL):
                    sel = txt.get(tk.SEL_FIRST, tk.SEL_LAST)
                else:
                    sel = txt.get("1.0", tk.END)
                if sel and sel.strip():
                    root.clipboard_clear()
                    root.clipboard_append(sel.strip())
            except tk.TclError:
                pass
            return "break"
        txt.bind("<Control-c>", _do_copy)
        txt.bind("<Control-C>", _do_copy)
        def _on_key(e):
            if (e.state & 0x4) and e.keysym.lower() == 'c':
                _do_copy()
            return "break"
        txt.bind("<Key>", _on_key)

        def _on_right_click(event):
            try:
                txt.focus_set()
            except tk.TclError:
                pass
            menu = tk.Menu(txt, tearoff=0)
            menu.add_command(label="Копировать", command=lambda: _copy_from_text(txt))
            menu.tk_popup(event.x_root, event.y_root)

        def _copy_from_text(w):
            try:
                if w.tag_ranges(tk.SEL):
                    sel = w.get(tk.SEL_FIRST, tk.SEL_LAST)
                else:
                    sel = w.get("1.0", tk.END)
                if sel and sel.strip():
                    root.clipboard_clear()
                    root.clipboard_append(sel.strip())
            except tk.TclError:
                pass

        txt.bind("<Button-3>", _on_right_click)
        return txt

    def _copy_selection(_=None):
        w = root.focus_get()
        if isinstance(w, tk.Text):
            try:
                sel = w.get(tk.SEL_FIRST, tk.SEL_LAST)
                if sel:
                    root.clipboard_clear()
                    root.clipboard_append(sel)
            except tk.TclError:
                pass
    root.bind("<Control-c>", _copy_selection)

    def _format_validator_display(raw: str) -> str:
        """Если в строке есть валидный JSON — форматирует его с отступами для читаемого отображения."""
        if not raw or not raw.strip():
            return raw or ""
        s = raw.strip()
        if "{" in s and "}" in s:
            try:
                start, end = s.index("{"), s.rindex("}") + 1
                obj = json.loads(s[start:end])
                pretty = json.dumps(obj, ensure_ascii=False, indent=2)
                prefix = s[:start].strip()
                suffix = s[end:].strip()
                if prefix or suffix:
                    return (prefix + "\n" if prefix else "") + pretty + ("\n" + suffix if suffix else "")
                return pretty
            except (json.JSONDecodeError, ValueError):
                pass
        return raw

    def add_bubble(side: str, text: str, is_technical: bool = False, simulator_button: str = None, buttons: list = None, timing_sec: float = None, streaming_placeholder: bool = False):
        """timing_sec: показывать в облачке «Время: X.XX с». streaming_placeholder: создать левое облачко с «…» и вернуть ref для обновления."""
        outer = ttk.Frame(content)
        outer.pack(fill=tk.X, pady=4)
        if side == "right":
            outer.pack(anchor=tk.E)

        bg = COLOR_TECH if is_technical else (COLOR_BOT if side == "left" else COLOR_USER)
        border = BORDER_TECH if is_technical else (BORDER_BOT if side == "left" else BORDER_USER)
        use_font = tech_font if is_technical else bubble_font

        frame = tk.Frame(outer, bg=border, padx=2, pady=2)
        frame.pack(anchor=tk.W if side == "left" else tk.E)
        inner = tk.Frame(frame, bg=bg, padx=10, pady=8)
        inner.pack()

        count_lbl = None
        timing_lbl = None
        if side == "left" and not is_technical:
            top_row = tk.Frame(inner, bg=bg)
            top_row.pack(anchor=tk.NW)
            count_lbl = tk.Label(top_row, text=f"{len(text or '')} симв." if not streaming_placeholder else "— симв.", font=("Segoe UI", 8), bg=bg, fg="#666")
            count_lbl.pack(side=tk.LEFT)
            timing_lbl = tk.Label(top_row, text=f"Время: {timing_sec:.2f} с" if timing_sec is not None else "Время: —", font=("Segoe UI", 8), bg=bg, fg="#666")
            timing_lbl.pack(side=tk.LEFT, padx=(12, 0))
        if is_technical and timing_sec is not None:
            timing_lbl = tk.Label(inner, text=f"Время: {timing_sec:.2f} с", font=("Segoe UI", 8), bg=bg, fg="#666")
            timing_lbl.pack(anchor=tk.NW)

        is_only_button = side == "right" and simulator_button and not (text or "").strip()
        if not is_only_button:
            txt = _make_selectable_text(inner, "…" if streaming_placeholder else (text or ""), use_font, bg, "#000")
            txt.pack(anchor=tk.W)

        btn_frame = None
        if side == "left" and buttons and not streaming_placeholder:
            btn_frame = tk.Frame(inner, bg=bg)
            btn_frame.pack(anchor=tk.W, pady=(8, 0))
            for label, _ in buttons:
                pill = tk.Frame(btn_frame, bg=BORDER_BOT, padx=6, pady=3)
                pill.pack(side=tk.LEFT, padx=(0, 4), pady=2)
                bt = _make_selectable_text(pill, label, ("Segoe UI", 9), BORDER_BOT, "#333", width_chars=20)
                bt.config(height=1)
                bt.pack()
        if side == "left" and streaming_placeholder:
            btn_frame = tk.Frame(inner, bg=bg)
            btn_frame.pack(anchor=tk.W, pady=(8, 0))

        if simulator_button:
            pill = tk.Frame(inner, bg=COLOR_SIMULATOR_CLICK, padx=8, pady=4)
            pill.pack(anchor=tk.W, pady=(6, 0) if (text or "").strip() else (0, 0))
            st = _make_selectable_text(pill, "Нажато: " + simulator_button, bubble_font, COLOR_SIMULATOR_CLICK, "#1a5a1a", width_chars=30)
            st.config(height=1)
            st.pack()
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.yview_moveto(1.0)
        if streaming_placeholder:
            return {"text_widget": txt, "timing_lbl": timing_lbl, "count_lbl": count_lbl, "btn_frame": btn_frame}
        for w in buttons_frame.winfo_children():
            w.destroy()

    def clear_buttons():
        for w in buttons_frame.winfo_children():
            w.destroy()

    def show_buttons(buttons: list):
        clear_buttons()
        for label, callback in buttons:
            text = callback or label
            btn = ttk.Button(
                buttons_frame,
                text=label,
                command=lambda t=text: send_message(t),
            )
            btn.pack(side=tk.TOP, fill=tk.X, pady=2)

    streaming_ref = [None]

    def send_message(text: str):
        if not text.strip():
            return
        clear_buttons()
        add_bubble("right", text.strip())
        entry.delete(0, tk.END)
        root.update_idletasks()
        result_q = queue.Queue()
        streaming_ref[0] = add_bubble("left", "", streaming_placeholder=True)

        def worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                def on_chunk(t):
                    result_q.put(("chunk", t))
                reply, buttons, validator_outputs, timings, rejected_reply = loop.run_until_complete(
                    get_bot_reply(TEST_USER_ID, text.strip(), context=None, stream_callback=on_chunk)
                )
                result_q.put(("ok", (reply, buttons, timings, validator_outputs, rejected_reply)))
            except Exception as e:
                result_q.put(("err", str(e), [], None))
            finally:
                loop.close()
        threading.Thread(target=worker, daemon=True).start()
        root.after(100, lambda: check_result(result_q))

    def check_result(q: queue.Queue):
        try:
            item = q.get_nowait()
        except queue.Empty:
            root.after(200, lambda: check_result(q))
            return
        if len(item) == 2:
            status, a = item[0], item[1]
            b = c = None
        else:
            status, a, b, c = item[0], item[1], item[2] if len(item) > 2 else None, item[3] if len(item) > 3 else None
        if status == "chunk":
            ref = streaming_ref[0]
            if ref and ref.get("text_widget"):
                try:
                    w = ref["text_widget"]
                    w.config(state=tk.NORMAL)
                    w.delete(1.0, tk.END)
                    w.insert(tk.END, a or "")
                except Exception:
                    pass
            root.after(50, lambda: check_result(q))
            return
        if status == "err":
            if streaming_ref[0]:
                try:
                    streaming_ref[0]["text_widget"].config(state=tk.NORMAL)
                    streaming_ref[0]["text_widget"].delete(1.0, tk.END)
                    streaming_ref[0]["text_widget"].insert(tk.END, a or "")
                except Exception:
                    pass
            add_bubble("left", a, is_technical=True)
            streaming_ref[0] = None
            return
        if status == "ok":
            payload = a
            if isinstance(payload, tuple) and len(payload) >= 5:
                reply, buttons, timings, validator_outputs, rejected_reply = payload[0], payload[1], payload[2], payload[3], payload[4]
            else:
                reply, buttons, timings = a, b, c
                validator_outputs = []
                rejected_reply = None
            ref = streaming_ref[0]
            if rejected_reply and validator_outputs:
                # Три облака: отклонённый ответ → вердикт валидатора (без повтора текста) → новый ответ.
                if ref:
                    try:
                        w = ref["text_widget"]
                        w.config(state=tk.NORMAL)
                        w.delete(1.0, tk.END)
                        _insert_telegram_style(w, rejected_reply or "", bubble_font, "#000")
                    except Exception:
                        pass
                    if ref.get("count_lbl"):
                        ref["count_lbl"].config(text=f"{len(rejected_reply or '')} симв.")
                    if ref.get("timing_lbl"):
                        ref["timing_lbl"].config(text="Время: —")
                    if ref.get("btn_frame"):
                        for child in ref["btn_frame"].winfo_children():
                            child.destroy()
                streaming_ref[0] = None
                raw_val, _, val_ms = validator_outputs[0]
                timing_sec = (val_ms / 1000.0) if isinstance(val_ms, (int, float)) else None
                add_bubble("left", "Валидатор:\n" + _format_validator_display(raw_val or ""), is_technical=True, timing_sec=timing_sec)
                add_bubble("left", reply, buttons=buttons, timing_sec=(timings.get("psychologist_ms") or 0) / 1000.0 if timings else None)
            elif ref:
                try:
                    w = ref["text_widget"]
                    w.config(state=tk.NORMAL)
                    w.delete(1.0, tk.END)
                    _insert_telegram_style(w, reply or "", bubble_font, "#000")
                except Exception:
                    pass
                if ref.get("count_lbl"):
                    ref["count_lbl"].config(text=f"{len(reply or '')} симв.")
                if ref.get("timing_lbl") and timings:
                    ref["timing_lbl"].config(text=f"Время: {timings.get('psychologist_ms', 0) / 1000:.2f} с")
                if ref.get("btn_frame") and buttons:
                    for label, _ in buttons:
                        pill = tk.Frame(ref["btn_frame"], bg=BORDER_BOT, padx=6, pady=3)
                        pill.pack(side=tk.LEFT, padx=(0, 4), pady=2)
                        bt = _make_selectable_text(pill, label, ("Segoe UI", 9), BORDER_BOT, "#333", width_chars=20)
                        bt.config(height=1)
                        bt.pack()
                streaming_ref[0] = None
            if buttons:
                show_buttons(buttons)
            else:
                clear_buttons()
            return

    def on_new_dialog():
        clear_history(TEST_USER_ID)
        clear_buttons()
        for w in content.winfo_children():
            w.destroy()
        add_bubble("left", "Диалог сброшен. Нажмите «Начать» или введите сообщение.")
        show_buttons([("Начать", "Начать")])

    def on_enter(_=None):
        t = entry.get().strip()
        if t:
            send_message(t)

    def _is_terminal(sim_message: str) -> bool:
        s = (sim_message or "").strip()
        return s.startswith("pay:") or s in ("Еще думаю", "Оплатить")

    def run_auto_dialog(result_q: queue.Queue, stop_flag: list):
        async def _run(q: queue.Queue):
            if not SIMULATOR_ENABLED:
                q.put(("tech", "Симулятор отключён. Добавьте user_simulator_prompt.txt."))
                q.put(("done", None))
                return
            clear_history(TEST_USER_ID)
            q.put(("clear", None))
            q.put(("tech", "Старт автодиалога (два бота). Нажмите СТОП для остановки."))
            reply, buttons, validator_texts, timings, rejected_reply = await get_bot_reply(
                TEST_USER_ID, "start_chat", context=None,
                log_validator_full=True,
            )
            if stop_flag[0]:
                q.put(("tech", "Остановлено пользователем."))
                q.put(("done", None))
                return
            q.put(("bot", (reply, buttons, timings, validator_texts, rejected_reply)))
            if rejected_reply and validator_texts:
                for v in validator_texts[1:]:
                    q.put(("validator", v))
            else:
                for v in validator_texts:
                    q.put(("validator", v))
            steps = 0
            while steps < 60:
                if stop_flag[0]:
                    q.put(("tech", "Остановлено пользователем."))
                    break
                sim_msg = await get_simulator_reply(TEST_USER_ID, buttons)
                if stop_flag[0]:
                    q.put(("tech", "Остановлено пользователем."))
                    break
                if not sim_msg:
                    q.put(("tech", "Симулятор вернул пустой ответ."))
                    break
                q.put(("user", (sim_msg, buttons)))
                if _is_terminal(sim_msg):
                    q.put(("tech", "Диалог завершён: " + sim_msg + ". Запрашиваем SHOW_JSON…"))
                    break
                reply, buttons, validator_texts, timings, rejected_reply = await get_bot_reply(
                    TEST_USER_ID, sim_msg, context=None,
                    log_validator_full=True,
                )
                if stop_flag[0]:
                    q.put(("tech", "Остановлено пользователем."))
                    break
                steps += 1
                q.put(("bot", (reply, buttons, timings, validator_texts, rejected_reply)))
                if rejected_reply and validator_texts:
                    for v in validator_texts[1:]:
                        q.put(("validator", v))
                else:
                    for v in validator_texts:
                        q.put(("validator", v))
            if not stop_flag[0]:
                json_reply, _, json_validators, _, _ = await get_bot_reply(
                    TEST_USER_ID, "SHOW_JSON", context=None,
                    log_validator_full=True,
                )
                q.put(("json", json_reply))
                for v in json_validators:
                    q.put(("validator", v))
            q.put(("tech", "Готово. Проверьте JSON выше." if not stop_flag[0] else "Автодиалог остановлен."))
            q.put(("done", None))

        asyncio.run(_run(result_q))

    def process_auto_queue(q: queue.Queue):
        try:
            kind, payload = q.get_nowait()
        except queue.Empty:
            root.after(300, lambda: process_auto_queue(q))
            return
        if kind == "tech":
            add_bubble("left", payload, is_technical=True)
        elif kind == "clear":
            for w in content.winfo_children():
                w.destroy()
        elif kind == "validator":
            raw_validator = payload
            rejected_text = None
            validator_ms = None
            if isinstance(payload, tuple) and len(payload) >= 2:
                raw_validator, rejected_text = payload[0], payload[1]
                if len(payload) >= 3:
                    validator_ms = payload[2]
            display = "Валидатор (проверка ответа психолога выше):\n" + _format_validator_display(raw_validator or "")
            if rejected_text and (rejected_text or "").strip():
                try:
                    s = (raw_validator or "").strip()
                    if "{" in s and "}" in s:
                        start, end = s.index("{"), s.rindex("}") + 1
                        data = json.loads(s[start:end])
                        if not data.get("valid", True):
                            display = "Текст, отклонённый валидатором:\n\n" + (rejected_text or "").strip() + "\n\n---\nВалидатор:\n" + _format_validator_display(raw_validator or "")
                except Exception:
                    pass
            timing_sec = (validator_ms / 1000.0) if isinstance(validator_ms, (int, float)) else None
            add_bubble("left", display, is_technical=True, timing_sec=timing_sec)
        elif kind == "bot":
            reply, buttons = payload[0], payload[1]
            timings = payload[2] if len(payload) > 2 else None
            validator_texts = payload[3] if len(payload) > 3 else []
            rejected_reply = payload[4] if len(payload) > 4 else None
            timing_sec = (timings["psychologist_ms"] / 1000.0) if timings and timings.get("psychologist_ms") is not None else None
            if rejected_reply and validator_texts:
                add_bubble("left", rejected_reply)
                raw_val, _, val_ms = validator_texts[0]
                v_sec = (val_ms / 1000.0) if isinstance(val_ms, (int, float)) else None
                add_bubble("left", "Валидатор:\n" + _format_validator_display(raw_val or ""), is_technical=True, timing_sec=v_sec)
                add_bubble("left", reply, buttons=buttons, timing_sec=timing_sec)
            else:
                add_bubble("left", reply, buttons=buttons, timing_sec=timing_sec)
            if buttons:
                show_buttons(buttons)
            else:
                clear_buttons()
        elif kind == "user":
            sim_msg, prev_buttons = payload
            pressed_label = None
            if prev_buttons:
                for label, callback in prev_buttons:
                    if sim_msg == label or sim_msg == (callback or label):
                        pressed_label = label
                        break
            if pressed_label:
                add_bubble("right", "", simulator_button=pressed_label)
            else:
                add_bubble("right", sim_msg)
            clear_buttons()
        elif kind == "json":
            add_bubble("left", payload, is_technical=True)
            try:
                text = payload.strip()
                if "```" in text:
                    for part in text.split("```"):
                        part = part.strip()
                        if part.startswith("json"):
                            part = part[4:].strip()
                        if part.startswith("{"):
                            parsed = json.loads(part)
                            add_bubble("left", json.dumps(parsed, ensure_ascii=False, indent=2), is_technical=True)
                            break
                else:
                    parsed = json.loads(text)
                    add_bubble("left", json.dumps(parsed, ensure_ascii=False, indent=2), is_technical=True)
            except json.JSONDecodeError:
                pass
        elif kind == "error":
            add_bubble("left", payload, is_technical=True)
        elif kind == "done":
            try:
                if stop_btn.winfo_ismapped():
                    stop_btn.pack_forget()
                auto_btn.pack(side=tk.LEFT)
            except Exception:
                pass
            return
        root.after(50, lambda: process_auto_queue(q))

    def on_auto():
        stop_flag[0] = False
        for w in content.winfo_children():
            w.destroy()
        clear_buttons()
        auto_btn.pack_forget()
        stop_btn.pack(side=tk.LEFT)
        q = run_async_in_thread(lambda q: run_auto_dialog(q, stop_flag))
        process_auto_queue(q)

    def on_stop():
        stop_flag[0] = True

    entry.bind("<Return>", on_enter)
    ttk.Button(entry_frame, text="Отправить", command=on_enter).pack(side=tk.RIGHT, padx=(6, 0))
    ttk.Button(top_bar, text="Новый диалог", command=on_new_dialog).pack(side=tk.LEFT, padx=(0, 8))
    auto_btn = ttk.Button(top_bar, text="Авто", command=on_auto)
    auto_btn.pack(side=tk.LEFT)
    stop_btn = ttk.Button(top_bar, text="СТОП", command=on_stop)

    add_bubble("left", "Нажмите «Новый диалог» и кнопку «Начать» — или «Авто» для диалога двух ботов.")
    show_buttons([("Начать", "Начать")])

    def on_closing():
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
