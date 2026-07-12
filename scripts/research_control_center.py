"""Russian operator UI for independent paper/research contours.

The control center is deliberately a process supervisor, not a trading brain.
It never imports project runtimes, reads ``.env``, or exposes an execution path.
All switches start the existing paper/research entrypoints as isolated child
processes and keep their output visible in one window.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import messagebox, ttk
from typing import Callable

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised by the Linux CI import path
    import fcntl


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(
    os.environ.get("TRADING_BOT_RUNTIME_ROOT", "").strip()
    or Path.home() / "trading-bot-v2"
)
GPU_MASK_ENV_NAMES = ("CUDA_VISIBLE_DEVICES", "GGML_VK_VISIBLE_DEVICES")
_configured_private_root = os.environ.get("TRADING_BOT_RESEARCH_ROOT", "").strip()
PRIVATE_ROOT = (
    Path(_configured_private_root)
    if _configured_private_root
    else Path.home() / "github_projects" / "trading-bot-research" / "strategy-lab"
)
STATE_DIR = PRIVATE_ROOT / "state" / "control-center"
STOP_FARM = PRIVATE_ROOT / "state" / "STOP_FARM_FULL_CYCLE.txt"


def _python(*args: str) -> tuple[str, ...]:
    return (sys.executable, "-X", "utf8", *args)


def format_age(seconds: int | float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} с"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} мин {sec} с"
    hours, minute = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minute} мин"
    days, hour = divmod(hours, 24)
    return f"{days} д {hour} ч"


@dataclass(frozen=True)
class ContourSpec:
    key: str
    title: str
    description: str
    command: tuple[str, ...]
    network: bool = False
    telegram: bool = False
    browser: bool = False
    graceful_stop: Callable[[], None] | None = None
    env: dict[str, str] = field(default_factory=dict)
    owner_group: str = ""
    graceful_seconds: float = 15.0


def _request_farm_stop() -> None:
    STOP_FARM.parent.mkdir(parents=True, exist_ok=True)
    STOP_FARM.write_text(f"control center stop requested at {time.time()}\n", encoding="utf-8")


def _request_named_stop(filename: str) -> Callable[[], None]:
    def request() -> None:
        target = PRIVATE_ROOT / "state" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"control center stop requested at {time.time()}\n", encoding="utf-8")

    return request


REQUEST_SCANNER_STOP = _request_named_stop("STOP_NEWS_SCANNER.txt")
REQUEST_PUBLIC_NEWS_STOP = _request_named_stop("STOP_PUBLIC_NEWS.txt")


def contour_specs() -> tuple[ContourSpec, ...]:
    """Return the allowlisted surface. There is intentionally no live entry."""
    ollama = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
    return (
        ContourSpec(
            "ollama",
            "Ollama / CPU-калькулятор",
            "Локальная LLM на CPU; видеокарта оставлена числовому перебору",
            (str(ollama), "serve"),
            env={
                "OLLAMA_LLM_LIBRARY": "cpu",
                "CUDA_VISIBLE_DEVICES": "-1",
                "GGML_VK_VISIBLE_DEVICES": "-1",
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_KEEP_ALIVE": "5m",
                "OLLAMA_NUM_PARALLEL": "1",
            },
        ),
        ContourSpec(
            "public_news",
            "Публичный новостной канал",
            "Собирает новости и публикует редакционные карточки",
            ("cmd.exe", "/d", "/c", str(ROOT / "bat" / "public_news_loop.bat")),
            network=True,
            telegram=True,
            graceful_stop=REQUEST_PUBLIC_NEWS_STOP,
            graceful_seconds=300.0,
        ),
        ContourSpec(
            "scanner",
            "Сканер → очередь фермы",
            "Собирает сигнальный контекст; Telegram-отправка отключена",
            (
                "cmd.exe",
                "/d",
                "/c",
                str(ROOT / "bat" / "news_scanner_loop.bat"),
            ),
            network=True,
            env={"SCANNER_SEND_TELEGRAM": "0"},
            graceful_stop=REQUEST_SCANNER_STOP,
            graceful_seconds=300.0,
        ),
        ContourSpec(
            "farm",
            "Ферма / валидатор / paper-наблюдение",
            "Канонический исследовательский цикл с калькулятором",
            ("cmd.exe", "/d", "/c", str(ROOT / "bat" / "paper_product_headless_loop.bat")),
            network=True,
            graceful_stop=_request_farm_stop,
            owner_group="canonical_farm",
            graceful_seconds=120.0,
            env={
                "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM": "0",
                "STRATEGY_LAB_CALCULATOR_BASE_URL": "http://127.0.0.1:11434/v1",
            },
        ),
        ContourSpec(
            "paper_cards",
            "Бумажные карточки Telegram",
            "Та же ферма, но с доставкой готовых paper-карточек",
            (
                "cmd.exe",
                "/d",
                "/c",
                str(ROOT / "bat" / "paper_product_headless_send_loop.bat"),
            ),
            network=True,
            telegram=True,
            graceful_stop=_request_farm_stop,
            owner_group="canonical_farm",
            graceful_seconds=120.0,
        ),
        ContourSpec(
            "telegram_bot",
            "Интерактивный Telegram-бот",
            "Анализ, VIP, обучение и статусы; без исполнения сделок",
            _python("-u", "scripts/telegram_bot.py"),
            network=True,
            telegram=True,
            env={
                "AUTO_TRADE": "0",
                "TELEGRAM_BOT_ALLOW_AUTO_EXECUTE": "0",
                "PRODUCT_ANALYZER_LLM_ROUTER": "llm_client",
            },
        ),
        ContourSpec(
            "dashboard",
            "Dashboard",
            "Локальная страница состояния на 127.0.0.1:8765",
            _python("scripts/strategy_lab/serve_dashboard.py", "--host", "127.0.0.1", "--port", "8765"),
            browser=True,
        ),
        ContourSpec(
            "graphs",
            "Графы",
            "Однократно перестраивает и открывает локальный граф",
            ("cmd.exe", "/d", "/c", str(ROOT / "bat" / "strategy_lab_graph_viewer.bat")),
            browser=True,
        ),
    )


class ManagedContour:
    def __init__(self, spec: ContourSpec, events: queue.Queue[tuple[str, str, str]]) -> None:
        self.spec = spec
        self.events = events
        self.process: subprocess.Popen[str] | None = None
        self.started_at = 0.0
        self.stopping = False

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def start(self) -> None:
        if self.running:
            return
        self.stopping = False
        env = os.environ.copy()
        for name in GPU_MASK_ENV_NAMES:
            env.pop(name, None)
        env.update(self.spec.env)
        env["TRADING_BOT_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
        env["PYTHONUTF8"] = "1"
        env["TRADING_BOT_RESEARCH_ROOT"] = str(PRIVATE_ROOT)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            self.spec.command,
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        self.started_at = time.time()
        self.events.put((self.spec.key, "state", f"работает · PID {self.process.pid}"))
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.events.put((self.spec.key, "log", line.rstrip()))
        code = self.process.wait()
        self.events.put((self.spec.key, "state", f"остановлен · код {code}"))

    def stop(self, timeout: float | None = None) -> None:
        if not self.running or not self.process:
            return
        self.stopping = True
        try:
            if self.spec.graceful_stop:
                self.spec.graceful_stop()
            else:
                try:
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                except (OSError, ValueError):
                    self.process.terminate()
            deadline = time.monotonic() + (self.spec.graceful_seconds if timeout is None else timeout)
            while self.running and time.monotonic() < deadline:
                time.sleep(0.2)
            if self.running:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                        capture_output=True, text=True, timeout=15, check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    self.process.terminate()
        finally:
            self.stopping = False


class SingleInstance:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("Центр управления уже открыт") from exc

    def close(self) -> None:
        if self.handle.closed:
            return
        if os.name == "nt":
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


class ControlCenter(tk.Tk):
    def __init__(self, instance: SingleInstance, autostart: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.title("Исследовательский центр trading-bot-v2")
        self.geometry("1180x760")
        self.minsize(900, 600)
        self.configure(background="#0f172a")
        self._configure_style()
        self.events: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self.instance = instance
        self.contours = {spec.key: ManagedContour(spec, self.events) for spec in contour_specs()}
        self.status_vars: dict[str, tk.StringVar] = {}
        self.buttons: dict[str, ttk.Button] = {}
        self.selected_key = "ollama"
        self._closing = False
        self._logs: dict[str, list[str]] = {key: [] for key in self.contours}
        self.system_var = tk.StringVar(value="Состояние фермы загружается…")
        self.manual_symbol = tk.StringVar(value="BTC")
        self.manual_timeframe = tk.StringVar(value="15m")
        self.manual_reason = tk.StringVar(value="срочная ручная проверка")
        self._build()
        self.after(150, self._poll)
        self.after(1000, self._heartbeat)
        if autostart:
            self.after(500, lambda: self._start_authorized(autostart))
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#0f172a")
        style.configure("TLabel", background="#0f172a", foreground="#dbeafe", font=("Segoe UI", 10))
        style.configure("Header.TLabel", foreground="#f8fafc", font=("Segoe UI", 22, "bold"))
        style.configure("Muted.TLabel", foreground="#93a4b8", font=("Segoe UI", 9))
        style.configure(
            "Status.TLabel", background="#132238", foreground="#a7f3d0",
            font=("Segoe UI", 10, "bold"), padding=(10, 8),
        )
        style.configure("Card.TLabelframe", background="#172033", foreground="#e2e8f0", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background="#172033", foreground="#7dd3fc", font=("Segoe UI", 10, "bold"))
        style.configure("Card.TLabel", background="#172033", foreground="#dbeafe")
        style.configure("Accent.TButton", background="#0284c7", foreground="#ffffff", padding=(12, 7))
        style.map("Accent.TButton", background=[("active", "#0369a1")])
        style.configure("Danger.TButton", background="#b91c1c", foreground="#ffffff", padding=(12, 7))
        style.map("Danger.TButton", background=[("active", "#991b1b")])
        style.configure("TButton", background="#334155", foreground="#f8fafc", padding=(9, 5))
        style.map("TButton", background=[("active", "#475569")])

    def _build(self) -> None:
        ttk.Label(self, text="Исследовательский центр", style="Header.TLabel").pack(
            anchor="w", padx=18, pady=(14, 2)
        )
        ttk.Label(
            self,
            text="Paper/research only · сделки, AUTO_TRADE и private endpoints отсутствуют",
        ).pack(anchor="w", padx=18, pady=(0, 6))
        ttk.Label(self, text=f"Приватные данные: {PRIVATE_ROOT}", style="Muted.TLabel").pack(anchor="w", padx=18, pady=(0, 10))
        ttk.Label(self, textvariable=self.system_var, style="Status.TLabel", wraplength=1120).pack(
            anchor="w", fill=tk.X, padx=18, pady=(0, 10)
        )
        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=18, pady=(0, 10))
        ttk.Button(
            actions,
            text="Запустить рабочий комплекс",
            command=self._start_research_profile,
            style="Accent.TButton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions,
            text="Остановить рабочий комплекс",
            command=self._stop_research_profile,
            style="Danger.TButton",
        ).pack(side=tk.LEFT, padx=8)
        urgent = ttk.LabelFrame(self, text="Срочный ручной расчёт (paper-only)", padding=9, style="Card.TLabelframe")
        urgent.pack(fill=tk.X, padx=18, pady=(0, 8))
        ttk.Label(urgent, text="Монета", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(urgent, textvariable=self.manual_symbol, width=14).grid(row=0, column=1, padx=(6, 12))
        ttk.Label(urgent, text="Таймфрейм", style="Card.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            urgent, textvariable=self.manual_timeframe, values=("15m", "1h", "4h", "1d"),
            state="readonly", width=7,
        ).grid(row=0, column=3, padx=(6, 12))
        ttk.Label(urgent, text="Причина", style="Card.TLabel").grid(row=0, column=4, sticky="w")
        ttk.Entry(urgent, textvariable=self.manual_reason).grid(row=0, column=5, padx=6, sticky="ew")
        ttk.Button(urgent, text="Поставить первой", command=self._enqueue_manual_urgent, style="Accent.TButton").grid(
            row=0, column=6, padx=(8, 0)
        )
        urgent.columnconfigure(5, weight=1)
        body = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=2)
        body.add(right, weight=3)
        canvas = tk.Canvas(left, background="#0f172a", highlightthickness=0)
        scrollbar = ttk.Scrollbar(left, orient=tk.VERTICAL, command=canvas.yview)
        cards = ttk.Frame(canvas)
        cards.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        card_window = canvas.create_window((0, 0), window=cards, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(card_window, width=event.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        for spec in contour_specs():
            frame = ttk.LabelFrame(cards, text=spec.title, padding=9, style="Card.TLabelframe")
            frame.pack(fill=tk.X, pady=4)
            ttk.Label(frame, text=spec.description, style="Card.TLabel").grid(row=0, column=0, sticky="w")
            var = tk.StringVar(value="выключен")
            self.status_vars[spec.key] = var
            ttk.Label(frame, textvariable=var, style="Card.TLabel").grid(row=1, column=0, sticky="w")
            button = ttk.Button(frame, text="Включить", command=lambda key=spec.key: self._toggle(key))
            button.grid(row=0, column=1, rowspan=2, padx=8)
            self.buttons[spec.key] = button
            ttk.Button(frame, text="Журнал", command=lambda key=spec.key: self._select(key)).grid(
                row=0, column=2, rowspan=2
            )
            frame.columnconfigure(0, weight=1)
        ttk.Label(right, text="Журнал выбранного контура", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=6, pady=(2, 6)
        )
        self.log = tk.Text(
            right,
            wrap="word",
            state="disabled",
            font=("Cascadia Mono", 9),
            background="#08101f",
            foreground="#cbd5e1",
            insertbackground="#f8fafc",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=6)
        ttk.Button(right, text="Обновить статусы", command=self._refresh).pack(anchor="e", padx=6, pady=8)

    def _select(self, key: str) -> None:
        self.selected_key = key
        self._render_log()

    def _enqueue_manual_urgent(self) -> None:
        symbol = self.manual_symbol.get().strip()
        timeframe = self.manual_timeframe.get().strip()
        reason = self.manual_reason.get().strip()
        if not symbol:
            messagebox.showwarning("Срочный расчёт", "Укажи монету, например BTC.")
            return

        def enqueue() -> None:
            command = _python(
                "-m", "scripts.strategy_lab.enqueue_manual_urgent", symbol,
                "--timeframe", timeframe, "--reason", reason,
                "--private-root", str(PRIVATE_ROOT),
            )
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["TRADING_BOT_RESEARCH_ROOT"] = str(PRIVATE_ROOT)
            completed = subprocess.run(
                command, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=15, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode == 0:
                self.after(0, lambda: messagebox.showinfo(
                    "Срочный расчёт",
                    f"{symbol.upper()} {timeframe} поставлен первым в очередь. Сделки не исполняются.",
                ))
            else:
                error = (completed.stderr or completed.stdout or "неизвестная ошибка").strip()[:300]
                self.after(0, lambda: messagebox.showerror("Срочный расчёт", error))

        threading.Thread(target=enqueue, daemon=True).start()

    def _toggle(self, key: str) -> None:
        item = self.contours[key]
        if item.running:
            self.status_vars[key].set("останавливается…")
            threading.Thread(target=item.stop, daemon=True).start()
            return
        if item.spec.owner_group:
            conflict = next(
                (
                    other
                    for other in self.contours.values()
                    if other.running
                    and other.spec.key != key
                    and other.spec.owner_group == item.spec.owner_group
                ),
                None,
            )
            if conflict:
                messagebox.showwarning(
                    "Двойной запуск запрещён",
                    f"Уже работает «{conflict.spec.title}». Сначала выключи его.",
                )
                return
        if item.spec.telegram and not messagebox.askyesno(
            "Подтверждение Telegram",
            f"Включить «{item.spec.title}»? Контур может отправлять сообщения в Telegram.",
        ):
            return
        try:
            item.start()
        except OSError as exc:
            self.status_vars[key].set(f"ошибка запуска: {exc}")

    def _start_authorized(self, keys: tuple[str, ...]) -> None:
        """Start only contours explicitly named on this process command line."""
        for key in keys:
            item = self.contours[key]
            if not item.running and not self._external_running(key):
                try:
                    item.start()
                except OSError as exc:
                    self.status_vars[key].set(f"ошибка запуска: {exc}")

    def _start_research_profile(self) -> None:
        if not messagebox.askyesno(
            "Запуск рабочего комплекса",
            "Запустить public-news, scanner, paper-ферму с карточками и интерактивный бот? "
            "Будет разрешена Telegram-доставка, но не сделки.",
        ):
            return
        if not self.contours["ollama"].running and not self._external_running("ollama"):
            self.contours["ollama"].start()
        self.after(
            2000,
            lambda: self._start_authorized(
                ("public_news", "scanner", "paper_cards", "telegram_bot")
            ),
        )

    def _stop_research_profile(self) -> None:
        owned = [
            self.contours[key]
            for key in ("telegram_bot", "paper_cards", "farm", "scanner", "public_news", "ollama")
            if self.contours[key].running
        ]
        if not owned:
            return
        if not messagebox.askyesno(
            "Остановка рабочего комплекса",
            "Запросить штатную остановку всех работающих контуров?",
        ):
            return

        def stop_owned() -> None:
            workers = [threading.Thread(target=item.stop, daemon=True) for item in owned]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

        threading.Thread(target=stop_owned, daemon=True).start()

    def _poll(self) -> None:
        while True:
            try:
                key, kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if key == "__app__" and kind == "close":
                self.instance.close()
                self.destroy()
                return
            if kind == "state":
                self.status_vars[key].set(value)
            else:
                rows = self._logs[key]
                rows.append(value)
                del rows[:-500]
            if key == self.selected_key:
                self._render_log()
        self._refresh_buttons()
        self.after(250, self._poll)

    def _refresh_buttons(self) -> None:
        for key, item in self.contours.items():
            if item.stopping:
                self.buttons[key].configure(text="Останавливается…", state="disabled")
                self.status_vars[key].set("штатная остановка…")
                continue
            external = not item.running and self._external_running(key)
            if external:
                self.buttons[key].configure(text="Работает вне центра", state="disabled")
                self.status_vars[key].set("работает вне центра")
            else:
                self.buttons[key].configure(
                    text="Выключить" if item.running else "Включить",
                    state="normal",
                )
                if item.running:
                    self.status_vars[key].set(self._health_text(key, item))

    @staticmethod
    def _file_age(path: Path) -> int | None:
        try:
            return max(0, int(time.time() - path.stat().st_mtime))
        except OSError:
            return None

    def _health_text(self, key: str, item: ManagedContour) -> str:
        pid = item.process.pid if item.process else "?"
        if key == "ollama":
            return f"готов · API 11434 · PID {pid}" if self._port_open(11434) else f"запускается · PID {pid}"
        if key == "public_news":
            age = self._file_age(ROOT / "logs" / "scout" / "public_channel" / "publisher_audit.jsonl")
            return f"цикл работает · audit {format_age(age)} назад · PID {pid}" if age is not None else f"первый проход · PID {pid}"
        if key == "scanner":
            age = self._file_age(ROOT / "logs" / "scout" / "scanner_journal.jsonl")
            return f"очередь работает · journal {format_age(age)} назад · PID {pid}" if age is not None else f"первый проход · PID {pid}"
        if key in {"farm", "paper_cards"}:
            status_path = PRIVATE_ROOT / "state" / "farm_loop_status.json"
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                stage = str(data.get("stage") or "неизвестно")
                age = max(0, int(time.time() - float(data.get("updated_at") or 0)))
                base = f"этап {stage} · heartbeat {format_age(age)} назад · PID {pid}"
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                base = f"первый цикл · PID {pid}"
            if key == "paper_cards":
                delivery_path = PRIVATE_ROOT / "state" / "derived" / "paper_telegram_delivery.json"
                try:
                    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
                    base += (
                        f" · Telegram: sent={int(delivery.get('sent_cards') or 0)}"
                        f" errors={int(delivery.get('errors') or 0)}"
                    )
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pass
            return base
        if key == "telegram_bot":
            age = self._file_age(ROOT / "logs" / "telegram_bot.log")
            return f"онлайн · log {format_age(age)} назад · PID {pid}" if age is not None else f"онлайн · PID {pid}"
        if key == "dashboard":
            return f"готов · http://127.0.0.1:8765 · PID {pid}" if self._port_open(8765) else f"запускается · PID {pid}"
        return f"работает · PID {pid}"

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                return True
        except OSError:
            return False

    def _external_running(self, key: str) -> bool:
        if key == "ollama":
            return self._port_open(11434)
        if key == "dashboard":
            return self._port_open(8765)
        return False

    def _render_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.insert(tk.END, "\n".join(self._logs[self.selected_key][-500:]))
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _refresh(self) -> None:
        for key, item in self.contours.items():
            if item.running and item.process:
                age = int(time.time() - item.started_at)
                self.status_vars[key].set(f"работает · PID {item.process.pid} · {format_age(age)}")

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _open_readonly_db(path: Path) -> sqlite3.Connection | None:
        if not path.is_file():
            return None
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=0.2)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    def _queue_snapshot(self) -> dict:
        conn = self._open_readonly_db(PRIVATE_ROOT / "state" / "farm_tasks.sqlite")
        if conn is None:
            return {}
        try:
            row = conn.execute(
                """SELECT
                     SUM(CASE WHEN state IN ('queued','running') AND priority<=0 THEN 1 ELSE 0 END) manual,
                     SUM(CASE WHEN state IN ('queued','running') AND priority BETWEEN 1 AND 10 THEN 1 ELSE 0 END) go_n,
                     SUM(CASE WHEN state IN ('queued','running') AND priority BETWEEN 11 AND 20 THEN 1 ELSE 0 END) watch_n,
                     SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END) queued,
                     SUM(CASE WHEN state='running' THEN 1 ELSE 0 END) running
                   FROM tasks"""
            ).fetchone()
            current = conn.execute(
                "SELECT task_type, symbol, timeframe, family, priority FROM tasks "
                "WHERE state='running' ORDER BY priority, updated_at LIMIT 1"
            ).fetchone()
            waiting_manual = conn.execute(
                "SELECT COUNT(*) FROM intake_events WHERE consumed=0 AND priority<=0"
            ).fetchone()[0]
            return {
                "manual": int(row["manual"] or 0) + int(waiting_manual or 0),
                "go": int(row["go_n"] or 0),
                "watch": int(row["watch_n"] or 0), "queued": int(row["queued"] or 0),
                "running": int(row["running"] or 0), "current": dict(current) if current else {},
            }
        except sqlite3.Error:
            return {}
        finally:
            conn.close()

    def _backend_snapshot(self) -> dict:
        conn = self._open_readonly_db(PRIVATE_ROOT / "state" / "strategy_lab.sqlite")
        if conn is None:
            return {}
        try:
            row = conn.execute(
                "SELECT effective_backend, signal_backend, simulation_backend, accelerated_runs, created_at "
                "FROM runtime_stats ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else {}
        except sqlite3.Error:
            return {}
        finally:
            conn.close()

    @staticmethod
    def _gpu_snapshot() -> str:
        try:
            completed = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=2, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            parts = [part.strip() for part in completed.stdout.strip().split(",")]
            if completed.returncode == 0 and len(parts) >= 4:
                return f"GPU {parts[0]}% · VRAM {parts[1]}/{parts[2]} МБ · {parts[3]}°C"
        except (OSError, subprocess.SubprocessError):
            pass
        return "GPU: нет данных"

    def _system_snapshot(self) -> str:
        queue_state = self._queue_snapshot()
        backend = self._backend_snapshot()
        farm = self._read_json(PRIVATE_ROOT / "state" / "farm_loop_status.json")
        stage = (
            str(farm.get("stage") or "работает")
            if self.contours.get("farm") and self.contours["farm"].running
            else "остановлена"
        )
        current = queue_state.get("current") or {}
        current_text = "нет активного слота"
        if current:
            target = "/".join(
                str(current.get(key) or "") for key in ("symbol", "timeframe", "family")
            ).strip("/")
            current_text = f"{current.get('task_type')} {target} · приоритет {current.get('priority')}"
        queue_text = (
            f"очередь {queue_state.get('queued', 0)} · работа {queue_state.get('running', 0)} · "
            f"ручные {queue_state.get('manual', 0)} · GO {queue_state.get('go', 0)} · "
            f"WATCH {queue_state.get('watch', 0)}"
        )
        backend_text = (
            f"backend {backend.get('effective_backend') or '?'} "
            f"(сигналы {backend.get('signal_backend') or '?'}, "
            f"симуляция {backend.get('simulation_backend') or '?'})"
        )
        return (
            f"{self._gpu_snapshot()}  |  {queue_text}  |  этап: {stage}  |  "
            f"сейчас: {current_text}  |  {backend_text}"
        )

    def _heartbeat(self) -> None:
        self.system_var.set(self._system_snapshot())
        payload = {
            "schema": "ResearchControlCenterHeartbeat.v1",
            "updated_at": time.time(),
            "pid": os.getpid(),
            "paper_only": True,
            "execution_allowed": False,
            "contours": {
                key: {
                    "running": item.running,
                    "pid": item.process.pid if item.running and item.process else None,
                    "status": self._health_text(key, item) if item.running else "выключен",
                }
                for key, item in self.contours.items()
            },
        }
        target = STATE_DIR / "heartbeat.json"
        temporary = target.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
        except OSError as exc:
            rows = self._logs[self.selected_key]
            rows.append(f"heartbeat error: {exc}")
            del rows[:-500]
            self._render_log()
        self.after(5000, self._heartbeat)

    def _close(self) -> None:
        if self._closing:
            return
        running = [item for item in self.contours.values() if item.running]
        if running and not messagebox.askyesno(
            "Закрыть центр",
            "Есть работающие контуры. Запросить их штатную остановку и закрыть окно?",
        ):
            return
        if not running:
            self.instance.close()
            self.destroy()
            return
        self._closing = True
        self.withdraw()

        def stop_all() -> None:
            workers = [threading.Thread(target=item.stop, daemon=True) for item in running]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.events.put(("__app__", "close", ""))

        threading.Thread(target=stop_all, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--start",
        action="append",
        choices=tuple(spec.key for spec in contour_specs()),
        default=[],
        help="explicitly start one allowlisted contour after opening the UI",
    )
    args = parser.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        instance = SingleInstance(STATE_DIR / "control-center.lock")
    except RuntimeError as exc:
        messagebox.showerror("Исследовательский центр", str(exc))
        return 2
    app = ControlCenter(instance, tuple(args.start))
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
