from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from time import monotonic
from tkinter import filedialog, messagebox, ttk

from .core import ConversionCancelled, ConversionError, ConversionOptions, convert_file, find_ffmpeg, is_supported
from .effects import (
    EFFECTS,
    CustomEffect,
    SoundAdjustments,
    build_adjustment_filter,
    build_effect_filter,
    load_custom_effects,
    new_custom_effect,
    save_custom_effects,
    unique_effect_output_path,
)
from .i18n import LANGUAGES, translate
from .live_voice import LiveVoiceEngine
from .recording import (
    InputDevice,
    MicrophoneRecorder,
    RecordingError,
    list_input_devices,
    list_output_devices,
)


BG = "#101827"
PANEL = "#172235"
PANEL_ALT = "#1D2A40"
TEXT = "#F4F7FB"
MUTED = "#99A8BC"
ACCENT = "#42D3A3"
DANGER = "#FF6B7A"
BORDER = "#2A3951"


def bundled_path(*parts: str) -> Path:
    """Find an application file both in source checkouts and PyInstaller bundles."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parents[1]
    return root.joinpath(*parts)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


class RecordingDialog(tk.Toplevel):
    def __init__(self, parent: "MicxioApp") -> None:
        super().__init__(parent)
        self.parent = parent
        self.language = parent.language
        self.recorder: MicrophoneRecorder | None = None
        self.started_at = 0.0
        self.levels: queue.Queue[float] = queue.Queue()
        self.devices: list[InputDevice] = []
        self.device_var = tk.StringVar()
        self.timer_var = tk.StringVar(value="00:00")
        self.message_var = tk.StringVar(value=self._tr("recording_ready"))
        self.level_var = tk.DoubleVar(value=0)

        self.title(self._tr("recorder_title"))
        self.geometry("520x350")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._load_devices()
        self._poll_id = self.after(60, self._poll_recording)
        self.grab_set()

    def _tr(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, style="Panel.TFrame", padding=(26, 22))
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=self._tr("recorder_title"), style="Section.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(frame, text=self._tr("microphone"), style="PanelMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(18, 5))
        self.device_combo = ttk.Combobox(frame, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=2, column=0, sticky="ew")

        meter_header = ttk.Frame(frame, style="Panel.TFrame")
        meter_header.grid(row=3, column=0, sticky="ew", pady=(18, 5))
        meter_header.columnconfigure(0, weight=1)
        ttk.Label(meter_header, text=self._tr("input_level"), style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(meter_header, textvariable=self.timer_var, style="Panel.TLabel").grid(row=0, column=1, sticky="e")
        self.level_meter = ttk.Progressbar(frame, variable=self.level_var, maximum=100)
        self.level_meter.grid(row=4, column=0, sticky="ew")
        ttk.Label(frame, textvariable=self.message_var, style="PanelMuted.TLabel").grid(
            row=5, column=0, sticky="w", pady=(12, 12))
        self.action_button = ttk.Button(frame, text=self._tr("start_recording"), style="Record.TButton",
                                        command=self._toggle_recording)
        self.action_button.grid(row=6, column=0, sticky="ew")

    def _load_devices(self) -> None:
        try:
            self.devices = list_input_devices()
        except RecordingError as error:
            self.action_button.configure(state="disabled")
            messagebox.showerror(self._tr("recording_error"), str(error), parent=self)
            return
        labels = [f"{device.name}  •  {device.sample_rate / 1000:g} kHz" for device in self.devices]
        self.device_combo.configure(values=labels)
        if self.devices:
            self.device_combo.current(0)
        else:
            self.action_button.configure(state="disabled")
            messagebox.showinfo(self._tr("no_microphone_title"), self._tr("no_microphone_body"), parent=self)

    def _toggle_recording(self) -> None:
        if self.recorder and self.recorder.active:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        selected = self.device_combo.current()
        if selected < 0 or selected >= len(self.devices):
            messagebox.showinfo(self._tr("no_microphone_title"), self._tr("no_microphone_body"), parent=self)
            return
        recording_dir = Path.cwd() / "recordings"
        filename = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.wav"
        path = recording_dir / filename
        self.recorder = MicrophoneRecorder(self.devices[selected], on_level=self.levels.put)
        try:
            self.recorder.start(path)
        except RecordingError as error:
            self.recorder = None
            messagebox.showerror(self._tr("recording_error"), str(error), parent=self)
            return
        self.started_at = monotonic()
        self.device_combo.configure(state="disabled")
        self.action_button.configure(text=self._tr("stop_recording"), style="Danger.TButton")
        self.message_var.set(self._tr("recording_status"))

    def _stop_recording(self) -> None:
        if not self.recorder:
            return
        self.action_button.configure(state="disabled")
        try:
            path = self.recorder.stop()
        except RecordingError as error:
            self._reset_controls()
            messagebox.showerror(self._tr("recording_error"), str(error), parent=self)
            return
        self.parent._recording_finished(path)
        self._cancel_poll()
        self.grab_release()
        self.destroy()

    def _reset_controls(self) -> None:
        self.recorder = None
        self.timer_var.set("00:00")
        self.level_var.set(0)
        self.device_combo.configure(state="readonly")
        self.action_button.configure(text=self._tr("start_recording"), style="Record.TButton", state="normal")
        self.message_var.set(self._tr("recording_ready"))

    def _poll_recording(self) -> None:
        try:
            exists = self.winfo_exists()
        except tk.TclError:
            return
        if not exists:
            return
        latest_level: float | None = None
        try:
            while True:
                latest_level = self.levels.get_nowait()
        except queue.Empty:
            pass
        if latest_level is not None:
            self.level_var.set(latest_level * 100)
        elif self.recorder and self.recorder.active:
            self.level_var.set(float(self.level_var.get()) * 0.82)
        if self.recorder and self.recorder.active:
            elapsed = max(0, int(monotonic() - self.started_at))
            self.timer_var.set(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        self._poll_id = self.after(60, self._poll_recording)

    def _cancel_poll(self) -> None:
        poll_id = getattr(self, "_poll_id", None)
        if poll_id:
            try:
                self.after_cancel(poll_id)
            except tk.TclError:
                pass
            self._poll_id = None

    def _on_close(self) -> None:
        if self.recorder and self.recorder.active:
            try:
                self.recorder.stop()
            except RecordingError:
                pass
        self._cancel_poll()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class LiveVoiceDialog(tk.Toplevel):
    def __init__(self, parent: "MicxioApp") -> None:
        super().__init__(parent)
        self.parent = parent
        self.language = parent.language
        self.effect = parent.selected_effect
        self.intensity = float(parent.intensity_var.get())
        self.engine: LiveVoiceEngine | None = None
        self.inputs = []
        self.outputs = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.input_var = tk.StringVar()
        self.output_device_var = tk.StringVar()
        self.level_var = tk.DoubleVar(value=0)
        self.status_var = tk.StringVar(value=self._tr("live_ready"))

        self.title(self._tr("live_title"))
        self.geometry("600x500")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._load_devices()
        self._poll_id = self.after(50, self._poll_events)
        self.grab_set()

    def _tr(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, style="Panel.TFrame", padding=(26, 22))
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=self._tr("live_title"), style="Section.TLabel").grid(row=0, column=0, sticky="w")

        effect_box = ttk.Frame(frame, style="Alt.TFrame", padding=(13, 11))
        effect_box.grid(row=1, column=0, sticky="ew", pady=(14, 8))
        effect_box.columnconfigure(0, weight=1)
        effect_name = f"{self.parent.effect_icon(self.effect)}  {self.parent.effect_name(self.effect)}"
        if self.effect not in self.parent.custom_effects:
            effect_name += f"  •  {round(self.intensity)}%"
        effect_label = ttk.Label(effect_box, text=effect_name, style="Panel.TLabel")
        effect_label.configure(background=PANEL_ALT, foreground=ACCENT)
        effect_label.grid(row=0, column=0, sticky="w")

        warning = ttk.Label(frame, text=self._tr("headphones_warning"), style="PanelMuted.TLabel", wraplength=515)
        warning.configure(foreground="#FFCB6B")
        warning.grid(row=2, column=0, sticky="w", pady=(0, 15))

        ttk.Label(frame, text=self._tr("input_device"), style="PanelMuted.TLabel").grid(row=3, column=0, sticky="w")
        self.input_combo = ttk.Combobox(frame, textvariable=self.input_var, state="readonly")
        self.input_combo.grid(row=4, column=0, sticky="ew", pady=(5, 13))
        ttk.Label(frame, text=self._tr("output_device"), style="PanelMuted.TLabel").grid(row=5, column=0, sticky="w")
        self.output_combo = ttk.Combobox(frame, textvariable=self.output_device_var, state="readonly")
        self.output_combo.grid(row=6, column=0, sticky="ew", pady=(5, 15))

        meter_row = ttk.Frame(frame, style="Panel.TFrame")
        meter_row.grid(row=7, column=0, sticky="ew")
        meter_row.columnconfigure(0, weight=1)
        ttk.Label(meter_row, text=self._tr("input_level"), style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w")
        self.level_meter = ttk.Progressbar(frame, variable=self.level_var, maximum=100)
        self.level_meter.grid(row=8, column=0, sticky="ew", pady=(5, 9))
        ttk.Label(frame, textvariable=self.status_var, style="PanelMuted.TLabel").grid(row=9, column=0, sticky="w")
        frame.rowconfigure(10, weight=1)
        self.action_button = ttk.Button(frame, text=self._tr("start_live"), style="Live.TButton",
                                        command=self._toggle)
        self.action_button.grid(row=11, column=0, sticky="ew")

    @staticmethod
    def _device_labels(devices) -> list[str]:
        return [f"{device.name}  •  {device.sample_rate / 1000:g} kHz" for device in devices]

    def _load_devices(self) -> None:
        try:
            self.inputs = list_input_devices()
            self.outputs = list_output_devices()
        except RecordingError as error:
            self.action_button.configure(state="disabled")
            messagebox.showerror(self._tr("live_error"), str(error), parent=self)
            return
        self.input_combo.configure(values=self._device_labels(self.inputs))
        self.output_combo.configure(values=self._device_labels(self.outputs))
        if self.inputs:
            self.input_combo.current(0)
        if self.outputs:
            self.output_combo.current(0)
        if not self.inputs:
            self.action_button.configure(state="disabled")
            messagebox.showinfo(self._tr("no_microphone_title"), self._tr("no_microphone_body"), parent=self)
        elif not self.outputs:
            self.action_button.configure(state="disabled")
            messagebox.showinfo(self._tr("no_output_title"), self._tr("no_output_body"), parent=self)

    def _toggle(self) -> None:
        if self.engine and self.engine.active:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        input_index = self.input_combo.current()
        output_index = self.output_combo.current()
        if input_index < 0 or output_index < 0:
            return
        self.engine = LiveVoiceEngine(
            self.inputs[input_index], self.outputs[output_index], self.effect, self.intensity,
            custom_effect=self.parent.custom_effects.get(self.effect),
            adjustments=self.parent.current_adjustments(),
            on_level=lambda level: self.events.put(("level", level)),
            on_error=lambda error: self.events.put(("error", error)),
        )
        try:
            self.engine.start()
        except RecordingError as error:
            self.engine = None
            messagebox.showerror(self._tr("live_error"), str(error), parent=self)
            return
        self.input_combo.configure(state="disabled")
        self.output_combo.configure(state="disabled")
        self.parent._register_live_processor(self.engine.processor)
        self.action_button.configure(text=self._tr("stop_live"), style="Danger.TButton")
        effect_name = self.parent.effect_name(self.effect)
        self.status_var.set(self._tr("live_running", effect=effect_name))

    def _stop(self) -> None:
        if self.engine:
            try:
                self.engine.stop()
            except Exception as error:
                messagebox.showerror(self._tr("live_error"), str(error), parent=self)
            self.parent._unregister_live_processor(self.engine.processor)
        self.engine = None
        self.level_var.set(0)
        self.input_combo.configure(state="readonly")
        self.output_combo.configure(state="readonly")
        self.action_button.configure(text=self._tr("start_live"), style="Live.TButton")
        self.status_var.set(self._tr("live_ready"))

    def _poll_events(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        latest_level = None
        latest_error = None
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "level":
                    latest_level = float(value)
                elif kind == "error":
                    latest_error = str(value)
        except queue.Empty:
            pass
        if latest_level is not None:
            self.level_var.set(min(100, latest_level * 100))
        elif self.engine and self.engine.active:
            self.level_var.set(float(self.level_var.get()) * 0.82)
        if latest_error:
            self.status_var.set(latest_error)
        self._poll_id = self.after(50, self._poll_events)

    def _on_close(self) -> None:
        if self.engine and self.engine.active:
            self._stop()
        poll_id = getattr(self, "_poll_id", None)
        if poll_id:
            try:
                self.after_cancel(poll_id)
            except tk.TclError:
                pass
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

class MicxioApp(tk.Tk):
    def __init__(self) -> None:
        # A stable application class lets Linux desktop environments associate
        # the window with micxio instead of showing Tk's generic icon.
        super().__init__(className="micxio")
        self.language = "en"
        self.geometry("1320x860")
        self.minsize(1120, 820)
        self.configure(bg=BG)
        self.app_icons: list[tk.PhotoImage] = []
        self.header_icon: tk.PhotoImage | None = None
        self._load_app_icon()

        self.files: dict[str, Path] = {}
        self.output_paths: dict[str, Path] = {}
        self.file_states: dict[str, str] = {}
        self.events: queue.Queue[tuple] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.output_dir: Path | None = None
        self.selected_effect = "female"
        self.custom_effects: dict[str, CustomEffect] = load_custom_effects()
        self.status_state: tuple[str, dict[str, object]] = ("ready", {})

        self.output_var = tk.StringVar()
        self.summary_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.language_button_var = tk.StringVar()
        self.intensity_var = tk.DoubleVar(value=65)
        self.intensity_text_var = tk.StringVar(value="65%")
        self.adjust_pitch_var = tk.DoubleVar(value=0)
        self.adjust_bass_var = tk.DoubleVar(value=0)
        self.adjust_treble_var = tk.DoubleVar(value=0)
        self.adjust_reverb_var = tk.DoubleVar(value=0)
        self.effect_description_var = tk.StringVar()
        self.save_format_var = tk.StringVar(value="MP3")
        self.progress_var = tk.DoubleVar(value=0)
        self.live_processors = []

        self._configure_styles()
        self._build_ui()
        self._apply_language()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Alt.TFrame", background=PANEL_ALT)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Noto Sans", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Noto Sans", 9))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Noto Sans", 10))
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=("Noto Sans", 9))
        style.configure("AltMuted.TLabel", background=PANEL_ALT, foreground=MUTED, font=("Noto Sans", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Noto Sans", 22, "bold"))
        style.configure("Logo.TLabel", background=ACCENT, foreground=BG, font=("Noto Sans", 18, "bold"), padding=(12, 6))
        style.configure("LogoImage.TLabel", background=BG)
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT, font=("Noto Sans", 13, "bold"))
        style.configure("Accent.TButton", background=ACCENT, foreground=BG, borderwidth=0, focuscolor=ACCENT,
                        font=("Noto Sans", 11, "bold"), padding=(18, 11))
        style.map("Accent.TButton", background=[("active", "#64E4BA"), ("disabled", "#385F59")],
                  foreground=[("disabled", "#829993")])
        style.configure("Secondary.TButton", background=PANEL_ALT, foreground=TEXT, borderwidth=1,
                        bordercolor=BORDER, focuscolor=PANEL_ALT, font=("Noto Sans", 9), padding=(12, 8))
        style.map("Secondary.TButton", background=[("active", "#263750")])
        style.configure("Danger.TButton", background=PANEL_ALT, foreground=DANGER, borderwidth=1,
                        bordercolor=BORDER, font=("Noto Sans", 9), padding=(12, 8))
        style.map("Danger.TButton", background=[("active", "#3A2938")])
        style.configure("Record.TButton", background="#D9485F", foreground=TEXT, borderwidth=0,
                        focuscolor="#D9485F", font=("Noto Sans", 10, "bold"), padding=(14, 10))
        style.map("Record.TButton", background=[("active", "#F05B72"), ("disabled", "#563642")],
                  foreground=[("disabled", "#9E8389")])
        style.configure("Live.TButton", background="#397FD8", foreground=TEXT, borderwidth=0,
                        focuscolor="#397FD8", font=("Noto Sans", 10, "bold"), padding=(14, 10))
        style.map("Live.TButton", background=[("active", "#55A6FF"), ("disabled", "#344967")],
                  foreground=[("disabled", "#8293AA")])
        style.configure("Secondary.TMenubutton", background=PANEL_ALT, foreground=TEXT, borderwidth=1,
                        bordercolor=BORDER, focuscolor=PANEL_ALT, arrowcolor=MUTED,
                        font=("Noto Sans", 9), padding=(12, 8))
        style.map("Secondary.TMenubutton", background=[("active", "#263750")])
        style.configure("TCombobox", fieldbackground=PANEL_ALT, background=PANEL_ALT, foreground=TEXT,
                        arrowcolor=MUTED, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        padding=7, font=("Noto Sans", 9))
        style.map("TCombobox", fieldbackground=[("readonly", PANEL_ALT)], foreground=[("readonly", TEXT)])
        style.configure("Voice.Horizontal.TScale", background=PANEL, troughcolor=PANEL_ALT)
        style.configure("Treeview", background=PANEL_ALT, fieldbackground=PANEL_ALT, foreground=TEXT,
                        rowheight=38, borderwidth=0, font=("Noto Sans", 9))
        style.configure("Treeview.Heading", background=PANEL, foreground=MUTED, borderwidth=0,
                        font=("Noto Sans", 9, "bold"), padding=(8, 8))
        style.map("Treeview", background=[("selected", "#234C52")], foreground=[("selected", TEXT)])
        style.map("Treeview.Heading", background=[("active", PANEL)])
        style.configure("Horizontal.TProgressbar", troughcolor=PANEL_ALT, background=ACCENT,
                        lightcolor=ACCENT, darkcolor=ACCENT, borderwidth=0, thickness=8)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(28, 22, 28, 24))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.columnconfigure(1, weight=1)
        if self.header_icon:
            ttk.Label(header, image=self.header_icon, style="LogoImage.TLabel").grid(
                row=0, column=0, rowspan=2, padx=(0, 14))
        else:
            ttk.Label(header, text="AC", style="Logo.TLabel").grid(row=0, column=0, rowspan=2, padx=(0, 14))
        ttk.Label(header, text="micxio", style="Title.TLabel").grid(row=0, column=1, sticky="sw")
        self.subtitle_label = ttk.Label(header, style="Muted.TLabel")
        self.subtitle_label.grid(row=1, column=1, sticky="nw")
        self.language_button = ttk.Menubutton(header, textvariable=self.language_button_var,
                                              style="Secondary.TMenubutton")
        language_menu = tk.Menu(self.language_button, tearoff=False, bg=PANEL_ALT, fg=TEXT,
                                activebackground="#263750", activeforeground=TEXT, borderwidth=0)
        for code, label in LANGUAGES.items():
            language_menu.add_command(label=label, command=lambda selected=code: self._change_language(selected))
        self.language_button.configure(menu=language_menu)
        self.language_button.grid(row=0, column=2, rowspan=2, padx=(0, 10))
        self.live_button = ttk.Button(header, style="Live.TButton", command=self._open_live_voice)
        self.live_button.grid(row=0, column=3, rowspan=2, padx=(0, 10))
        self.record_button = ttk.Button(header, style="Record.TButton", command=self._open_recorder)
        self.record_button.grid(row=0, column=4, rowspan=2, padx=(0, 10))
        self.add_button = ttk.Button(header, style="Accent.TButton", command=self._choose_files)
        self.add_button.grid(row=0, column=5, rowspan=2)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.summary_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.remove_button = ttk.Button(toolbar, style="Secondary.TButton", command=self._remove_selected)
        self.remove_button.grid(row=0, column=1, padx=(8, 0))
        self.clear_button = ttk.Button(toolbar, style="Danger.TButton", command=self._clear_files)
        self.clear_button.grid(row=0, column=2, padx=(8, 0))

        content = ttk.Frame(root)
        content.grid(row=2, column=0, sticky="nsew")
        content.columnconfigure(0, weight=7)
        content.columnconfigure(1, weight=5)
        content.rowconfigure(0, weight=1)

        list_panel = ttk.Frame(content, style="Panel.TFrame", padding=2)
        list_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        list_panel.columnconfigure(0, weight=1)
        list_panel.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(list_panel, columns=("name", "type", "size", "status"), show="headings",
                                 selectmode="extended")
        self.tree.column("name", minwidth=210, width=300, anchor="w")
        self.tree.column("type", minwidth=65, width=70, anchor="center", stretch=False)
        self.tree.column("size", minwidth=80, width=85, anchor="e", stretch=False)
        self.tree.column("status", minwidth=100, width=115, anchor="center", stretch=False)
        scrollbar = ttk.Scrollbar(list_panel, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._play_finished_file)

        settings = ttk.Frame(content, style="Panel.TFrame", padding=(20, 18))
        settings.grid(row=0, column=1, sticky="nsew")
        settings.columnconfigure(0, weight=1)
        settings.columnconfigure(1, weight=1)
        self.settings_title_label = ttk.Label(settings, style="Section.TLabel")
        self.settings_title_label.grid(row=0, column=0, columnspan=2, sticky="w")
        self.effect_header_label = ttk.Label(settings, style="PanelMuted.TLabel")
        self.effect_header_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(14, 7))

        effect_grid = ttk.Frame(settings, style="Panel.TFrame")
        effect_grid.grid(row=2, column=0, columnspan=2, sticky="ew")
        effect_grid.columnconfigure(0, weight=1)
        effect_grid.columnconfigure(1, weight=1)
        effect_grid.columnconfigure(2, weight=1)
        self.effect_grid = effect_grid
        self.effect_buttons: dict[str, tk.Button] = {}
        self._rebuild_effect_buttons()

        custom_actions = ttk.Frame(settings, style="Panel.TFrame")
        custom_actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        custom_actions.columnconfigure(0, weight=1)
        self.custom_label = ttk.Label(custom_actions, style="PanelMuted.TLabel")
        self.custom_label.grid(row=0, column=0, sticky="w")
        self.add_custom_button = ttk.Button(custom_actions, style="Secondary.TButton", command=self._add_custom_effect)
        self.add_custom_button.grid(row=0, column=1, padx=(8, 0))
        self.edit_custom_button = ttk.Button(custom_actions, style="Secondary.TButton", command=self._edit_custom_effect)
        self.edit_custom_button.grid(row=0, column=2, padx=(8, 0))
        self.delete_custom_button = ttk.Button(custom_actions, style="Danger.TButton", command=self._delete_custom_effect)
        self.delete_custom_button.grid(row=0, column=3, padx=(8, 0))

        self.effect_description_label = ttk.Label(settings, textvariable=self.effect_description_var,
                                                   style="PanelMuted.TLabel", wraplength=350)
        self.effect_description_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 13))

        intensity_row = ttk.Frame(settings, style="Panel.TFrame")
        intensity_row.grid(row=5, column=0, columnspan=2, sticky="ew")
        intensity_row.columnconfigure(0, weight=1)
        self.intensity_label = ttk.Label(intensity_row, style="PanelMuted.TLabel")
        self.intensity_label.grid(row=0, column=0, sticky="w")
        ttk.Label(intensity_row, textvariable=self.intensity_text_var, style="Panel.TLabel").grid(row=0, column=1)
        self.intensity_scale = ttk.Scale(settings, from_=0, to=100, variable=self.intensity_var,
                                         command=self._update_intensity_text, style="Voice.Horizontal.TScale")
        self.intensity_scale.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(5, 8))

        fine_tune = ttk.Frame(settings, style="Panel.TFrame")
        fine_tune.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        fine_tune.columnconfigure(0, weight=1)
        fine_tune.columnconfigure(1, weight=1)
        fine_tune.columnconfigure(2, weight=1)
        fine_tune.columnconfigure(3, weight=1)
        self.fine_tune_label = ttk.Label(fine_tune, style="PanelMuted.TLabel")
        self.fine_tune_label.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self.adjustment_labels: dict[str, ttk.Label] = {}
        self.adjustment_value_labels: dict[str, ttk.Label] = {}
        self.adjustment_scales: list[ttk.Scale] = []
        adjustment_specs = (
            ("bass", self.adjust_bass_var, -12, 12),
            ("treble", self.adjust_treble_var, -12, 12),
            ("pitch", self.adjust_pitch_var, -12, 12),
            ("reverb", self.adjust_reverb_var, 0, 100),
        )
        for index, (name, variable, minimum, maximum) in enumerate(adjustment_specs):
            control = ttk.Frame(fine_tune, style="Panel.TFrame")
            control.grid(row=1, column=index, sticky="ew", padx=3)
            control.columnconfigure(0, weight=1)
            adjustment_label = ttk.Label(control, style="PanelMuted.TLabel")
            adjustment_label.grid(row=0, column=0, sticky="w")
            self.adjustment_labels[name] = adjustment_label
            value_label = ttk.Label(control, style="Panel.TLabel")
            value_label.grid(row=0, column=1, sticky="e")
            self.adjustment_value_labels[name] = value_label
            adjustment_scale = ttk.Scale(control, from_=minimum, to=maximum, variable=variable,
                                         command=self._update_adjustments, style="Voice.Horizontal.TScale")
            adjustment_scale.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 1))
            self.adjustment_scales.append(adjustment_scale)

        save_row = ttk.Frame(settings, style="Panel.TFrame")
        save_row.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(0, 13))
        save_row.columnconfigure(0, weight=1)
        self.save_as_label = ttk.Label(save_row, style="PanelMuted.TLabel")
        self.save_as_label.grid(row=0, column=0, sticky="w")
        self.format_combo = ttk.Combobox(save_row, textvariable=self.save_format_var,
                                         values=("MP3", "WAV", "FLAC"), state="readonly", width=8)
        self.format_combo.grid(row=0, column=1, sticky="e")

        self.output_folder_label = ttk.Label(settings, style="PanelMuted.TLabel")
        self.output_folder_label.grid(row=9, column=0, columnspan=2, sticky="w", pady=(0, 5))
        output_box = ttk.Frame(settings, style="Alt.TFrame", padding=(10, 8))
        output_box.grid(row=10, column=0, columnspan=2, sticky="ew")
        output_box.columnconfigure(0, weight=1)
        self.output_label = ttk.Label(output_box, textvariable=self.output_var, style="AltMuted.TLabel", wraplength=245)
        self.output_label.grid(row=0, column=0, sticky="w")
        self.folder_button = ttk.Button(output_box, style="Secondary.TButton", command=self._choose_output_folder)
        self.folder_button.grid(row=0, column=1, padx=(8, 0))

        settings.rowconfigure(11, weight=1)
        self.tip_label = ttk.Label(settings, style="PanelMuted.TLabel")
        self.process_button = ttk.Button(settings, style="Accent.TButton", command=self._start_processing)
        self.process_button.grid(row=12, column=0, columnspan=2, sticky="ew")
        self.cancel_button = ttk.Button(settings, style="Danger.TButton", command=self._cancel_processing)

        footer = ttk.Frame(root)
        footer.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Progressbar(footer, variable=self.progress_var, maximum=100, length=280).grid(row=0, column=1)

    def _tr(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def _load_app_icon(self) -> None:
        assets_dir = bundled_path("assets")
        try:
            icon_sizes = (16, 32, 48, 64, 128, 256)
            self.app_icons = [
                tk.PhotoImage(file=str(assets_dir / f"micxio-icon-{size}.png"))
                for size in icon_sizes
            ]
            self.header_icon = self.app_icons[3]
            self.iconphoto(True, *self.app_icons)
            # Reapply after the native window exists. This is needed by some
            # Linux/Wayland window managers when Tk runs through XWayland.
            self.after_idle(lambda: self.iconphoto(True, *self.app_icons))
        except tk.TclError:
            self.app_icons = []
            self.header_icon = None

    def effect_name(self, effect_key: str) -> str:
        custom_effect = self.custom_effects.get(effect_key)
        if custom_effect:
            return custom_effect.name
        return self._tr(EFFECTS[effect_key].name_key)

    def effect_icon(self, effect_key: str) -> str:
        return "✦" if effect_key in self.custom_effects else EFFECTS[effect_key].icon

    def _rebuild_effect_buttons(self) -> None:
        for button in self.effect_buttons.values():
            button.destroy()
        self.effect_buttons = {}
        effect_keys = [*EFFECTS, *self.custom_effects]
        for index, effect_key in enumerate(effect_keys):
            button = tk.Button(
                self.effect_grid, command=lambda selected=effect_key: self._select_effect(selected),
                bg=PANEL_ALT, fg=TEXT, activebackground="#263750", activeforeground=TEXT,
                disabledforeground=MUTED, relief="flat", bd=0, highlightthickness=1,
                highlightbackground=BORDER, highlightcolor=ACCENT, font=("Noto Sans", 9, "bold"),
                padx=7, pady=7, cursor="hand2",
            )
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=3)
            self.effect_buttons[effect_key] = button

    def _add_custom_effect(self) -> None:
        self._open_custom_effect_editor()

    def _edit_custom_effect(self) -> None:
        effect = self.custom_effects.get(self.selected_effect)
        if not effect:
            messagebox.showinfo(self._tr("custom_effects"), self._tr("select_custom_effect"), parent=self)
            return
        self._open_custom_effect_editor(effect)

    def _delete_custom_effect(self) -> None:
        effect = self.custom_effects.get(self.selected_effect)
        if not effect:
            messagebox.showinfo(self._tr("custom_effects"), self._tr("select_custom_effect"), parent=self)
            return
        if not messagebox.askyesno(
            self._tr("delete_custom_title"), self._tr("delete_custom_body", name=effect.name), parent=self,
        ):
            return
        del self.custom_effects[effect.key]
        try:
            save_custom_effects(self.custom_effects)
        except OSError as error:
            self.custom_effects[effect.key] = effect
            messagebox.showerror(self._tr("custom_effects"), str(error), parent=self)
            return
        self.selected_effect = "female"
        self._rebuild_effect_buttons()
        self._apply_language()

    def _open_custom_effect_editor(self, existing: CustomEffect | None = None) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(self._tr("custom_effects"))
        dialog.geometry("480x490")
        dialog.resizable(False, False)
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()

        name_var = tk.StringVar(value=existing.name if existing else "")
        pitch_var = tk.DoubleVar(value=existing.pitch_semitones if existing else 0)
        echo_var = tk.DoubleVar(value=existing.echo_percent if existing else 0)
        bass_var = tk.DoubleVar(value=existing.bass_db if existing else 0)
        tone_var = tk.DoubleVar(value=existing.tone_db if existing else 0)
        frame = ttk.Frame(dialog, style="Panel.TFrame", padding=(24, 20))
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=self._tr("custom_effects"), style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=self._tr("custom_name"), style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(16, 5))
        name_entry = ttk.Entry(frame, textvariable=name_var)
        name_entry.grid(row=2, column=0, sticky="ew")

        def slider(row: int, label: str, variable: tk.DoubleVar, minimum: float, maximum: float,
                   unit: str) -> None:
            header = ttk.Frame(frame, style="Panel.TFrame")
            header.grid(row=row, column=0, sticky="ew", pady=(13, 3))
            header.columnconfigure(0, weight=1)
            ttk.Label(header, text=label, style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w")
            value_label = ttk.Label(header, style="Panel.TLabel")
            value_label.grid(row=0, column=1, sticky="e")
            def update(_value: str = "") -> None:
                if unit == "%":
                    value_label.configure(text=f"{variable.get():.0f}%")
                else:
                    value_label.configure(text=f"{variable.get():+.1f} {unit}")
            scale = ttk.Scale(frame, from_=minimum, to=maximum, variable=variable,
                              command=update, style="Voice.Horizontal.TScale")
            scale.grid(row=row + 1, column=0, sticky="ew")
            update()

        slider(3, self._tr("custom_pitch"), pitch_var, -12, 12, "st")
        slider(5, self._tr("custom_echo"), echo_var, 0, 100, "%")
        slider(7, self._tr("custom_bass"), bass_var, -12, 12, "dB")
        slider(9, self._tr("custom_tone"), tone_var, -12, 12, "dB")

        def save() -> None:
            name = name_var.get()
            if not name.strip():
                messagebox.showerror(self._tr("custom_effects"), self._tr("custom_name_required"), parent=dialog)
                return
            key = existing.key if existing else None
            try:
                effect = new_custom_effect(
                    name, pitch_var.get(), echo_var.get(), tone_var.get(), key, bass_db=bass_var.get(),
                )
            except ValueError as error:
                messagebox.showerror(self._tr("custom_effects"), str(error), parent=dialog)
                return
            if any(candidate.name.casefold() == effect.name.casefold() and candidate.key != effect.key
                   for candidate in self.custom_effects.values()):
                messagebox.showerror(self._tr("custom_effects"), self._tr("custom_name_duplicate"), parent=dialog)
                return
            previous = self.custom_effects.get(effect.key)
            self.custom_effects[effect.key] = effect
            try:
                save_custom_effects(self.custom_effects)
            except OSError as error:
                if previous:
                    self.custom_effects[effect.key] = previous
                else:
                    del self.custom_effects[effect.key]
                messagebox.showerror(self._tr("custom_effects"), str(error), parent=dialog)
                return
            self.selected_effect = effect.key
            self._rebuild_effect_buttons()
            self._apply_language()
            dialog.grab_release()
            dialog.destroy()

        ttk.Button(frame, text=self._tr("save_custom"), style="Accent.TButton", command=save).grid(
            row=11, column=0, sticky="ew", pady=(18, 0))
        name_entry.focus_set()

    def _change_language(self, language: str) -> None:
        if language in LANGUAGES and language != self.language:
            self.language = language
            self._apply_language()

    def _apply_language(self) -> None:
        self.title(self._tr("window_title"))
        self.language_button_var.set(f"🌐  {LANGUAGES[self.language]}")
        self.subtitle_label.configure(text=self._tr("subtitle"))
        self.live_button.configure(text=self._tr("live_voice"))
        self.record_button.configure(text=self._tr("record"))
        self.add_button.configure(text=self._tr("add_files"))
        self.remove_button.configure(text=self._tr("remove_selected"))
        self.clear_button.configure(text=self._tr("clear_all"))
        for column, key in (("name", "head_name"), ("type", "head_source"),
                            ("size", "head_size"), ("status", "head_status")):
            self.tree.heading(column, text=self._tr(key))
        self.settings_title_label.configure(text=self._tr("settings"))
        self.effect_header_label.configure(text=self._tr("effect"))
        self.custom_label.configure(text=self._tr("custom_effects"))
        self.add_custom_button.configure(text=self._tr("add_custom"))
        self.edit_custom_button.configure(text=self._tr("edit_custom"))
        self.delete_custom_button.configure(text=self._tr("delete_custom"))
        self.intensity_label.configure(text=self._tr("intensity"))
        self.fine_tune_label.configure(text=self._tr("fine_tune"))
        self.adjustment_labels["bass"].configure(text=self._tr("custom_bass"))
        self.adjustment_labels["treble"].configure(text=self._tr("custom_tone"))
        self.adjustment_labels["pitch"].configure(text=self._tr("custom_pitch"))
        self.adjustment_labels["reverb"].configure(text=self._tr("custom_echo"))
        self.save_as_label.configure(text=self._tr("save_as"))
        self.output_folder_label.configure(text=self._tr("output_folder"))
        self.folder_button.configure(text=self._tr("choose"))
        self.tip_label.configure(text=self._tr("double_click_tip"))
        self.process_button.configure(text=self._tr("convert_all"))
        self.cancel_button.configure(text=self._tr("cancel_conversion"))
        for effect_key, button in self.effect_buttons.items():
            button.configure(text=f"{self.effect_icon(effect_key)}  {self.effect_name(effect_key)}")
        if self.output_dir is None:
            self.output_var.set(self._tr("same_folder"))
        self._update_effect_selection()
        self._update_adjustments()
        self._update_summary()
        self._refresh_status()
        for iid, state_key in self.file_states.items():
            self._render_tree_status(iid, state_key)

    def _select_effect(self, effect_key: str) -> None:
        if effect_key in EFFECTS or effect_key in self.custom_effects:
            self.selected_effect = effect_key
            self._update_effect_selection()

    def _update_effect_selection(self) -> None:
        for effect_key, button in self.effect_buttons.items():
            selected = effect_key == self.selected_effect
            button.configure(bg="#234C52" if selected else PANEL_ALT, fg=ACCENT if selected else TEXT,
                             highlightbackground=ACCENT if selected else BORDER)
        custom_effect = self.custom_effects.get(self.selected_effect)
        if custom_effect:
            self.effect_description_var.set(self._tr(
                "custom_effect_desc", pitch=custom_effect.pitch_semitones,
                echo=custom_effect.echo_percent, bass=custom_effect.bass_db, tone=custom_effect.tone_db,
            ))
            self.intensity_scale.configure(state="disabled")
        else:
            self.effect_description_var.set(self._tr(EFFECTS[self.selected_effect].description_key))
            self.intensity_scale.configure(state="normal")

    def _update_intensity_text(self, value: str) -> None:
        try:
            self.intensity_text_var.set(f"{round(float(value))}%")
        except ValueError:
            pass

    def current_adjustments(self) -> SoundAdjustments:
        return SoundAdjustments(
            pitch_semitones=float(self.adjust_pitch_var.get()),
            bass_db=float(self.adjust_bass_var.get()),
            treble_db=float(self.adjust_treble_var.get()),
            reverb_percent=float(self.adjust_reverb_var.get()),
        )

    def _update_adjustments(self, _value: str = "") -> None:
        adjustments = self.current_adjustments()
        values = {
            "bass": f"{adjustments.bass_db:+.1f} dB",
            "treble": f"{adjustments.treble_db:+.1f} dB",
            "pitch": f"{adjustments.pitch_semitones:+.1f} st",
            "reverb": f"{adjustments.reverb_percent:.0f}%",
        }
        for name, text in values.items():
            self.adjustment_value_labels[name].configure(text=text)
        for processor in self.live_processors.copy():
            processor.set_adjustments(adjustments)

    def _register_live_processor(self, processor) -> None:
        if processor not in self.live_processors:
            self.live_processors.append(processor)

    def _unregister_live_processor(self, processor) -> None:
        if processor in self.live_processors:
            self.live_processors.remove(processor)

    def _set_status(self, key: str, **values: object) -> None:
        self.status_state = (key, values)
        self._refresh_status()

    def _refresh_status(self) -> None:
        key, values = self.status_state
        self.status_var.set(self._tr(key, **values))

    def _choose_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title=self._tr("choose_audio_files"),
            filetypes=[
                (self._tr("audio_files"), "*.mp3 *.wav *.flac *.ogg *.oga *.m4a *.aac *.opus *.wma *.aiff *.aif *.ac3 *.webm *.mp4"),
                (self._tr("all_files"), "*.*"),
            ],
        )
        self._add_paths(paths)

    def _open_recorder(self) -> None:
        RecordingDialog(self)

    def _open_live_voice(self) -> None:
        LiveVoiceDialog(self)

    def _recording_finished(self, path: Path) -> None:
        self._add_paths([str(path)])
        children = self.tree.get_children()
        if children:
            newest = children[-1]
            self.tree.selection_set(newest)
            self.tree.see(newest)
        self._set_status("recording_saved", name=path.name)

    def _add_paths(self, paths: tuple[str, ...] | list[str]) -> None:
        existing = {path.resolve() for path in self.files.values()}
        skipped = 0
        for raw_path in paths:
            path = Path(raw_path)
            if not path.is_file() or not is_supported(path) or path.resolve() in existing:
                skipped += 1
                continue
            iid = self.tree.insert("", "end", values=(path.name, path.suffix[1:].upper(),
                                   human_size(path.stat().st_size), self._tr("waiting")))
            self.files[iid] = path
            self.file_states[iid] = "waiting"
            existing.add(path.resolve())
        self._update_summary()
        if skipped:
            self._set_status("skipped", count=skipped)

    def _remove_selected(self) -> None:
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.files.pop(iid, None)
            self.output_paths.pop(iid, None)
            self.file_states.pop(iid, None)
        self._update_summary()

    def _clear_files(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.files.clear()
        self.output_paths.clear()
        self.file_states.clear()
        self.progress_var.set(0)
        self._set_status("ready")
        self._update_summary()

    def _update_summary(self) -> None:
        count = len(self.files)
        total = sum(path.stat().st_size for path in self.files.values() if path.exists())
        self.summary_var.set(self._tr("files_summary", count=count, size=human_size(total))
                             if count else self._tr("no_files"))

    def _choose_output_folder(self) -> None:
        folder = filedialog.askdirectory(title=self._tr("choose_output_folder"))
        if folder:
            self.output_dir = Path(folder)
            self.output_var.set(folder)

    def _start_processing(self) -> None:
        if not self.files:
            messagebox.showinfo(self._tr("no_files_title"), self._tr("no_files_body"))
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            messagebox.showerror(self._tr("ffmpeg_title"), self._tr("ffmpeg_body"))
            return
        if self.output_dir:
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                messagebox.showerror(self._tr("folder_error"), str(error))
                return

        for iid in self.files:
            self._set_tree_status(iid, "waiting")
        self.output_paths.clear()
        self.cancel_event.clear()
        self.progress_var.set(0)
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._processing_worker,
            args=(ffmpeg, list(self.files.items()), self.selected_effect,
                  float(self.intensity_var.get()), self.current_adjustments(), self.save_format_var.get()),
            daemon=True,
        )
        self.worker.start()

    def _processing_worker(self, ffmpeg: str, items: list[tuple[str, Path]], effect: str,
                           intensity: float, adjustments: SoundAdjustments, output_format: str) -> None:
        total = len(items)
        succeeded = 0
        failed = 0
        effect_filter = build_effect_filter(effect, intensity, self.custom_effects)
        adjustment_filter = build_adjustment_filter(adjustments)
        options = ConversionOptions(
            output_format=output_format, bitrate="192k", normalize=True, preserve_metadata=True,
            audio_filter=",".join(filter(None, (effect_filter, adjustment_filter))),
        )
        for index, (iid, source) in enumerate(items):
            if self.cancel_event.is_set():
                self.events.put(("cancelled", succeeded, failed))
                return
            destination = unique_effect_output_path(source, self.output_dir or source.parent, effect, output_format)
            self.events.put(("file_start", iid, index + 1, total, source.name))

            def update_progress(file_fraction: float, current=index) -> None:
                self.events.put(("progress", ((current + file_fraction) / total) * 100))

            try:
                convert_file(ffmpeg, source, destination, options, update_progress, self.cancel_event.is_set)
                succeeded += 1
                self.events.put(("file_done", iid, str(destination)))
            except ConversionCancelled:
                self.events.put(("cancelled", succeeded, failed))
                return
            except (ConversionError, OSError) as error:
                failed += 1
                self.events.put(("file_error", iid, str(error)))
        self.events.put(("all_done", succeeded, failed))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "file_start":
                    _, iid, current, total, name = event
                    self._set_tree_status(iid, "converting_file")
                    self._set_status("converting_status", current=current, total=total, name=name)
                elif kind == "progress":
                    self.progress_var.set(event[1])
                elif kind == "file_done":
                    _, iid, destination = event
                    self.output_paths[iid] = Path(destination)
                    self._set_tree_status(iid, "success")
                elif kind == "file_error":
                    _, iid, error = event
                    self._set_tree_status(iid, "failed")
                    self._set_status("error_status", error=error)
                elif kind == "all_done":
                    _, succeeded, failed = event
                    self.progress_var.set(100)
                    self._set_running(False)
                    status_key = "done_status_failed" if failed else "done_status"
                    message_key = "done_message_failed" if failed else "done_message"
                    self._set_status(status_key, succeeded=succeeded, failed=failed)
                    messagebox.showinfo(self._tr("done_title"),
                                        self._tr(message_key, succeeded=succeeded, failed=failed))
                elif kind == "cancelled":
                    _, succeeded, _failed = event
                    self._set_running(False)
                    self._set_status("cancelled_status", succeeded=succeeded)
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

    def _set_tree_status(self, iid: str, state_key: str) -> None:
        self.file_states[iid] = state_key
        self._render_tree_status(iid, state_key)

    def _render_tree_status(self, iid: str, state_key: str) -> None:
        if self.tree.exists(iid):
            values = list(self.tree.item(iid, "values"))
            values[3] = self._tr(state_key)
            self.tree.item(iid, values=values)

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in (self.add_button, self.live_button, self.record_button, self.remove_button, self.clear_button,
                       self.folder_button, self.process_button, self.intensity_scale, self.add_custom_button,
                       self.edit_custom_button, self.delete_custom_button):
            widget.configure(state=state)
        for scale in self.adjustment_scales:
            scale.configure(state=state)
        self.format_combo.configure(state="disabled" if running else "readonly")
        for button in self.effect_buttons.values():
            button.configure(state=state, cursor="arrow" if running else "hand2")
        if running:
            self.process_button.grid_remove()
            self.cancel_button.configure(state="normal")
            self.cancel_button.grid(row=12, column=0, columnspan=2, sticky="ew")
        else:
            self.cancel_button.grid_remove()
            self.process_button.grid()
            self._update_effect_selection()

    def _cancel_processing(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self._set_status("cancelling")

    def _play_finished_file(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        destination = self.output_paths.get(selection[0])
        if not destination or not destination.exists():
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(destination)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(destination)])
            else:
                subprocess.Popen(["xdg-open", str(destination)])
        except OSError:
            pass

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(self._tr("close_title"), self._tr("close_body")):
                return
            self.cancel_event.set()
        self.destroy()


def run() -> None:
    app = MicxioApp()
    app.mainloop()
