#!/usr/bin/env python3
"""Menu-driven Tkinter frontend for the ppstool command line utility."""

from __future__ import annotations

import glob
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import threading
from typing import List, Optional, Tuple, Union

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ModuleNotFoundError as exc:
    if exc.name and exc.name.startswith("tkinter"):
        print(
            "ppstool-gui requires Python Tkinter, but this Python cannot import tkinter.",
            file=sys.stderr,
        )
        print("Install the Tkinter package for the Python used to run ppstool-gui.", file=sys.stderr)
        print("Debian/Ubuntu: sudo apt install python3-tk", file=sys.stderr)
        print("Fedora/RHEL:   sudo dnf install python3-tkinter", file=sys.stderr)
        print("Arch:         sudo pacman -S tk", file=sys.stderr)
        raise SystemExit(1) from None
    raise


DEFAULT_DEVICE = "/dev/ptp0"
MAX_SAMPLES = 25
MAX_UI_INDEX = 1024
PIN_FUNCTIONS = {
    "None": 0,
    "External timestamp": 1,
    "Periodic output": 2,
    "Physical sync": 3,
}
PERIOD_PRESETS = {
    "1 Hz": "1000000000",
    "10 Hz": "100000000",
    "100 Hz": "10000000",
    "1 kHz": "1000000",
    "Custom": "",
}
MENU_ITEMS = [
    ("home", "Common setup"),
    ("status", "Status"),
    ("pps_input", "PPS input"),
    ("pps_output", "PPS output"),
    ("pins", "Pins"),
    ("time", "Time"),
    ("advanced", "Advanced"),
]


def default_command() -> str:
    candidates = [
        Path(sys.argv[0]).resolve().with_name("ppstool"),
        Path(__file__).resolve().with_name("ppstool"),
    ]
    for local_binary in candidates:
        if local_binary.exists():
            return str(local_binary)
    return "ppstool"


def ptp_devices() -> List[str]:
    devices = sorted(glob.glob("/dev/ptp*"))
    return devices or [DEFAULT_DEVICE]


def command_to_text(command: List[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


class PpsToolGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ppstool")
        self.geometry("1120x760")
        self.minsize(920, 620)

        self.output_queue: "queue.Queue[Tuple[str, Optional[Union[str, int]]]]" = queue.Queue()
        self.current_process: Optional[subprocess.Popen] = None
        self.pages = {}

        self.command_var = tk.StringVar(value=default_command())
        self.device_var = tk.StringVar(value=ptp_devices()[0])
        self.index_var = tk.StringVar(value="0")
        self.offset_samples_var = tk.StringVar(value="5")
        self.extended_samples_var = tk.StringVar(value="5")
        self.event_count_var = tk.StringVar(value="1")
        self.pin_index_var = tk.StringVar(value="0")
        self.pin_function_var = tk.StringVar(value="External timestamp")
        self.period_preset_var = tk.StringVar(value="1 Hz")
        self.perout_period_var = tk.StringVar(value=PERIOD_PRESETS["1 Hz"])
        self.perout_width_var = tk.StringVar(value="")
        self.perout_phase_var = tk.StringVar(value="")
        self.freq_adjust_var = tk.StringVar(value="0")
        self.shift_seconds_var = tk.StringVar(value="0")
        self.shift_ns_var = tk.StringVar(value="0")
        self.phase_offset_var = tk.StringVar(value="0")
        self.set_seconds_var = tk.StringVar(value="0")
        self.mask_channel_var = tk.StringVar(value="0")
        self.raw_args_var = tk.StringVar(value="")
        self.page_title_var = tk.StringVar(value="")

        self._build_ui()
        self.show_page("home")
        self.after(100, self._drain_output)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_header(self)

        body = ttk.PanedWindow(self, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew")

        nav = ttk.Frame(body, padding=(12, 10, 6, 12), width=210)
        main = ttk.PanedWindow(body, orient="vertical")
        body.add(nav, weight=0)
        body.add(main, weight=1)

        self._build_navigation(nav)

        page_shell = ttk.Frame(main, padding=(6, 10, 12, 6))
        output_shell = ttk.Frame(main, padding=(6, 6, 12, 12))
        main.add(page_shell, weight=3)
        main.add(output_shell, weight=2)

        page_shell.columnconfigure(0, weight=1)
        page_shell.rowconfigure(1, weight=1)
        ttk.Label(page_shell, textvariable=self.page_title_var, font=("TkDefaultFont", 16, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.page_area = ttk.Frame(page_shell)
        self.page_area.grid(row=1, column=0, sticky="nsew")
        self.page_area.columnconfigure(0, weight=1)
        self.page_area.rowconfigure(0, weight=1)
        self._build_pages(self.page_area)
        self._build_output(output_shell)

    def _build_header(self, parent: tk.Misc) -> None:
        top = ttk.Frame(parent, padding=(12, 12, 12, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Command").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(top, textvariable=self.command_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(top, text="Device").grid(row=0, column=2, sticky="w", padx=(12, 6))
        self.device_box = ttk.Combobox(top, textvariable=self.device_var, values=ptp_devices(), width=18)
        self.device_box.grid(row=0, column=3, sticky="ew")
        ttk.Button(top, text="Refresh", command=self.refresh_devices).grid(row=0, column=4, padx=(6, 0))
        self.stop_button = ttk.Button(top, text="Stop", command=self.stop_command, state="disabled")
        self.stop_button.grid(row=0, column=5, padx=(6, 0))

    def _build_navigation(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        ttk.Label(parent, text="Menu", font=("TkDefaultFont", 14, "bold")).grid(row=0, column=0, sticky="w")
        self.nav_list = tk.Listbox(parent, exportselection=False, height=len(MENU_ITEMS), activestyle="dotbox")
        self.nav_list.grid(row=1, column=0, sticky="nsew", pady=(8, 10))
        for _, title in MENU_ITEMS:
            self.nav_list.insert("end", title)
        self.nav_list.bind("<<ListboxSelect>>", self._on_menu_select)
        self.nav_list.bind("<Return>", self._on_menu_select)
        self.nav_list.bind("<Double-Button-1>", self._on_menu_select)

        ttk.Button(parent, text="Quit", command=self.destroy).grid(row=2, column=0, sticky="ew")

    def _build_pages(self, parent: ttk.Frame) -> None:
        builders = {
            "home": self._build_home_page,
            "status": self._build_status_page,
            "pps_input": self._build_pps_input_page,
            "pps_output": self._build_pps_output_page,
            "pins": self._build_pins_page,
            "time": self._build_time_page,
            "advanced": self._build_advanced_page,
        }
        for name, _ in MENU_ITEMS:
            page = ttk.Frame(parent)
            page.grid(row=0, column=0, sticky="nsew")
            page.columnconfigure(0, weight=1)
            builders[name](page)
            self.pages[name] = page

    def _build_output(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        bar = ttk.Frame(parent)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, text="Output", font=("TkDefaultFont", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Clear", command=self.clear_output).grid(row=0, column=1, sticky="e")
        self.output = ScrolledText(parent, wrap="word", height=12, font=("Menlo", 12))
        self.output.grid(row=1, column=0, sticky="nsew")

    def _on_menu_select(self, _event: tk.Event) -> None:
        selection = self.nav_list.curselection()
        if selection:
            self.show_page(MENU_ITEMS[selection[0]][0])

    def show_page(self, name: str) -> None:
        page = self.pages[name]
        page.tkraise()
        for index, (page_name, title) in enumerate(MENU_ITEMS):
            if page_name == name:
                self.page_title_var.set(title)
                self.nav_list.selection_clear(0, "end")
                self.nav_list.selection_set(index)
                self.nav_list.activate(index)
                break

    def _build_home_page(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=0, sticky="new")
        for column in range(3):
            panel.columnconfigure(column, weight=1)

        self._build_context_fields(panel, row=0, columnspan=3)

        ttk.Button(panel, text="Inspect device", command=lambda: self.run(["-c", "-l"])).grid(
            row=1, column=0, sticky="ew", padx=(0, 8), pady=(12, 8)
        )
        ttk.Button(panel, text="Configure PPS input", command=self.configure_pps_input).grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=(12, 8)
        )
        ttk.Button(panel, text="Configure 1 Hz PPS output", command=self.configure_one_hz_output).grid(
            row=1, column=2, sticky="ew", pady=(12, 8)
        )
        ttk.Button(panel, text="Read PPS input until stopped", command=lambda: self.run_with_index(["-e", "-1"])).grid(
            row=2, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(panel, text="Enable system PPS", command=lambda: self.run(["-P", "1"])).grid(
            row=2, column=1, sticky="ew", padx=(0, 8)
        )
        ttk.Button(panel, text="Disable periodic output", command=self.disable_perout).grid(
            row=2, column=2, sticky="ew"
        )

    def _build_status_page(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=0, sticky="new")
        for column in range(2):
            panel.columnconfigure(column, weight=1)

        ttk.Button(panel, text="Capabilities", command=lambda: self.run(["-c"])).grid(
            row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 8)
        )
        ttk.Button(panel, text="Pin configuration", command=lambda: self.run(["-l"])).grid(
            row=0, column=1, sticky="ew", pady=(0, 8)
        )
        ttk.Button(panel, text="Clock time", command=lambda: self.run(["-g"])).grid(
            row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 8)
        )
        ttk.Button(panel, text="Cross timestamp", command=lambda: self.run(["-X"])).grid(
            row=1, column=1, sticky="ew", pady=(0, 8)
        )

        sample_box = ttk.Labelframe(panel, text="Offset measurement", padding=10)
        sample_box.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        sample_box.columnconfigure(1, weight=1)
        ttk.Label(sample_box, text="Samples").grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Spinbox(sample_box, from_=1, to=MAX_SAMPLES, textvariable=self.offset_samples_var, width=8).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Button(sample_box, text="Measure offset", command=self.measure_offset).grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )

        extended_box = ttk.Labelframe(panel, text="Extended timestamp", padding=10)
        extended_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        extended_box.columnconfigure(1, weight=1)
        ttk.Label(extended_box, text="Samples").grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Spinbox(extended_box, from_=1, to=MAX_SAMPLES, textvariable=self.extended_samples_var, width=8).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Button(extended_box, text="Read extended time", command=self.read_extended).grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )

    def _build_pps_input_page(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=0, sticky="new")
        panel.columnconfigure(1, weight=1)
        self._build_pin_channel_fields(panel, row=0)

        ttk.Label(panel, text="Event count").grid(row=2, column=0, sticky="w", pady=(10, 0), padx=(0, 8))
        tk.Spinbox(panel, from_=-1, to=1000000, textvariable=self.event_count_var, width=10).grid(
            row=2, column=1, sticky="w", pady=(10, 0)
        )

        actions = ttk.Frame(panel)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        for column in range(3):
            actions.columnconfigure(column, weight=1)
        ttk.Button(actions, text="Set pin as input", command=self.configure_pps_input).grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(actions, text="Read events", command=self.read_events).grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )
        ttk.Button(actions, text="Read until stopped", command=lambda: self.run_with_index(["-e", "-1"])).grid(
            row=0, column=2, sticky="ew"
        )

    def _build_pps_output_page(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=0, sticky="new")
        panel.columnconfigure(1, weight=1)
        self._build_pin_channel_fields(panel, row=0)

        ttk.Label(panel, text="Preset").grid(row=2, column=0, sticky="w", pady=(10, 0), padx=(0, 8))
        preset = ttk.Combobox(
            panel,
            textvariable=self.period_preset_var,
            values=list(PERIOD_PRESETS.keys()),
            state="readonly",
            width=16,
        )
        preset.grid(row=2, column=1, sticky="w", pady=(10, 0))
        preset.bind("<<ComboboxSelected>>", self._on_period_preset)

        ttk.Label(panel, text="Period ns").grid(row=3, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        ttk.Entry(panel, textvariable=self.perout_period_var).grid(row=3, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(panel, text="Pulse width ns").grid(row=4, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        ttk.Entry(panel, textvariable=self.perout_width_var).grid(row=4, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(panel, text="Phase ns").grid(row=5, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        ttk.Entry(panel, textvariable=self.perout_phase_var).grid(row=5, column=1, sticky="ew", pady=(8, 0))

        actions = ttk.Frame(panel)
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        for column in range(3):
            actions.columnconfigure(column, weight=1)
        ttk.Button(actions, text="Set pin as output", command=self.configure_pps_output_pin).grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(actions, text="Apply output", command=self.enable_perout).grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )
        ttk.Button(actions, text="Set pin and apply", command=lambda: self.enable_perout(configure_pin=True)).grid(
            row=0, column=2, sticky="ew"
        )
        ttk.Button(actions, text="Disable output", command=self.disable_perout).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0)
        )

    def _build_pins_page(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=0, sticky="new")
        panel.columnconfigure(1, weight=1)
        self._build_pin_channel_fields(panel, row=0)

        ttk.Label(panel, text="Function").grid(row=2, column=0, sticky="w", pady=(10, 0), padx=(0, 8))
        ttk.Combobox(
            panel,
            textvariable=self.pin_function_var,
            values=list(PIN_FUNCTIONS.keys()),
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", pady=(10, 0))

        actions = ttk.Frame(panel)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        for column in range(2):
            actions.columnconfigure(column, weight=1)
        ttk.Button(actions, text="Apply pin function", command=self.set_pin_function).grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(actions, text="List pins", command=lambda: self.run(["-l"])).grid(row=0, column=1, sticky="ew")

    def _build_time_page(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=0, sticky="new")
        panel.columnconfigure(1, weight=1)

        ttk.Button(panel, text="Get PTP clock time", command=lambda: self.run(["-g"])).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )
        ttk.Button(panel, text="Set PTP from system time", command=lambda: self.run(["-s"])).grid(
            row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 8)
        )
        ttk.Button(panel, text="Set system from PTP time", command=lambda: self.run(["-S"])).grid(
            row=1, column=1, sticky="ew", pady=(0, 8)
        )

        self._entry_row(panel, 2, "Frequency adjust ppb", self.freq_adjust_var)
        ttk.Button(panel, text="Apply frequency adjustment", command=self.adjust_frequency).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(6, 10)
        )

        self._entry_row(panel, 4, "Shift seconds", self.shift_seconds_var)
        self._entry_row(panel, 5, "Shift nanoseconds", self.shift_ns_var)
        ttk.Button(panel, text="Shift PTP time", command=self.shift_time).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(6, 10)
        )

        self._entry_row(panel, 7, "Phase offset ns", self.phase_offset_var)
        ttk.Button(panel, text="Apply phase offset", command=self.adjust_phase).grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=(6, 10)
        )

        self._entry_row(panel, 9, "Set PTP seconds", self.set_seconds_var)
        ttk.Button(panel, text="Set PTP to seconds", command=self.set_time_seconds).grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

    def _build_advanced_page(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent)
        panel.grid(row=0, column=0, sticky="new")
        panel.columnconfigure(1, weight=1)

        ttk.Button(panel, text="Flag test", command=lambda: self.run_with_index(["-z"])).grid(
            row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 8)
        )
        ttk.Button(panel, text="Enable system PPS", command=lambda: self.run(["-P", "1"])).grid(
            row=0, column=1, sticky="ew", pady=(0, 8)
        )
        ttk.Button(panel, text="Disable system PPS", command=lambda: self.run(["-P", "0"])).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )

        self._entry_row(panel, 2, "Debug mask channel", self.mask_channel_var)
        ttk.Button(panel, text="Enable single mask channel", command=self.enable_mask_channel).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(6, 12)
        )

        ttk.Label(panel, text="Raw args").grid(row=4, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(panel, textvariable=self.raw_args_var).grid(row=4, column=1, sticky="ew")
        ttk.Button(panel, text="Run raw args", command=self.run_raw_args).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

    def _build_context_fields(self, parent: ttk.Frame, row: int, columnspan: int) -> None:
        box = ttk.Labelframe(parent, text="Context", padding=10)
        box.grid(row=row, column=0, columnspan=columnspan, sticky="ew")
        box.columnconfigure(1, weight=1)
        self._pin_channel_fields(box, row=0)

    def _build_pin_channel_fields(self, parent: ttk.Frame, row: int) -> None:
        box = ttk.Labelframe(parent, text="Pin and channel", padding=10)
        box.grid(row=row, column=0, columnspan=2, sticky="ew")
        box.columnconfigure(1, weight=1)
        self._pin_channel_fields(box, row=0)

    def _pin_channel_fields(self, parent: ttk.Frame, row: int) -> None:
        ttk.Label(parent, text="Pin").grid(row=row, column=0, sticky="w", padx=(0, 8))
        tk.Spinbox(parent, from_=0, to=MAX_UI_INDEX, textvariable=self.pin_index_var, width=10).grid(
            row=row, column=1, sticky="w"
        )
        ttk.Label(parent, text="Channel").grid(row=row + 1, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        tk.Spinbox(parent, from_=0, to=MAX_UI_INDEX, textvariable=self.index_var, width=10).grid(
            row=row + 1, column=1, sticky="w", pady=(8, 0)
        )

    def _entry_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(6, 0), padx=(0, 8))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=(6, 0))

    def refresh_devices(self) -> None:
        devices = ptp_devices()
        self.device_box.configure(values=devices)
        if self.device_var.get() not in devices:
            self.device_var.set(devices[0])

    def clear_output(self) -> None:
        self.output.delete("1.0", "end")

    def append_output(self, text: str) -> None:
        self.output.insert("end", text)
        self.output.see("end")

    def parse_int(self, value: str, name: str, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value, 0)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return parsed

    def build_base_command(self) -> List[str]:
        command_text = self.command_var.get().strip()
        if not command_text:
            raise ValueError("Command is required")
        command = shlex.split(command_text)
        device = self.device_var.get().strip()
        if device:
            command.extend(["-d", device])
        return command

    def selected_index(self) -> int:
        return self.parse_int(self.index_var.get(), "Channel", 0, MAX_UI_INDEX)

    def selected_pin(self) -> int:
        return self.parse_int(self.pin_index_var.get(), "Pin", 0, MAX_UI_INDEX)

    def run_with_index(self, args: List[str]) -> None:
        try:
            index = self.selected_index()
        except ValueError as exc:
            messagebox.showerror("Invalid channel", str(exc))
            return
        self.run(["-i", str(index)] + args)

    def run(self, args: List[str]) -> None:
        if self.current_process and self.current_process.poll() is None:
            messagebox.showwarning("Command running", "Stop the current command before starting another one.")
            return

        try:
            command = self.build_base_command() + args
        except ValueError as exc:
            messagebox.showerror("Invalid command", str(exc))
            return

        self.append_output(f"$ {command_to_text(command)}\n")
        self.set_running(True)
        thread = threading.Thread(target=self._run_worker, args=(command,), daemon=True)
        thread.start()

    def _run_worker(self, command: List[str]) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self.current_process = process
            assert process.stdout is not None
            for line in process.stdout:
                self.output_queue.put(("line", line))
            return_code = process.wait()
            self.output_queue.put(("done", return_code))
        except FileNotFoundError:
            self.output_queue.put(("line", f"Command not found: {command[0]}\n"))
            self.output_queue.put(("done", 127))
        except OSError as exc:
            self.output_queue.put(("line", f"{exc}\n"))
            self.output_queue.put(("done", 1))

    def _drain_output(self) -> None:
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "line":
                    self.append_output(str(payload))
                elif kind == "done":
                    self.current_process = None
                    self.set_running(False)
                    self.append_output(f"\n[exit {payload}]\n\n")
        except queue.Empty:
            pass
        self.after(100, self._drain_output)

    def set_running(self, running: bool) -> None:
        self.stop_button.configure(state="normal" if running else "disabled")

    def stop_command(self) -> None:
        process = self.current_process
        if process and process.poll() is None:
            self.append_output("\n[terminating]\n")
            process.terminate()

    def _on_period_preset(self, _event: tk.Event) -> None:
        value = PERIOD_PRESETS[self.period_preset_var.get()]
        if value:
            self.perout_period_var.set(value)

    def measure_offset(self) -> None:
        try:
            samples = self.parse_int(self.offset_samples_var.get(), "Offset samples", 1, MAX_SAMPLES)
        except ValueError as exc:
            messagebox.showerror("Invalid samples", str(exc))
            return
        self.run(["-k", str(samples)])

    def read_extended(self) -> None:
        try:
            samples = self.parse_int(self.extended_samples_var.get(), "Extended samples", 1, MAX_SAMPLES)
        except ValueError as exc:
            messagebox.showerror("Invalid samples", str(exc))
            return
        self.run(["-x", str(samples)])

    def configure_pps_input(self) -> None:
        try:
            index = self.selected_index()
            pin = self.selected_pin()
        except ValueError as exc:
            messagebox.showerror("Invalid PPS input", str(exc))
            return
        self.run(["-i", str(index), "-L", f"{pin},{PIN_FUNCTIONS['External timestamp']}"])

    def configure_pps_output_pin(self) -> None:
        try:
            index = self.selected_index()
            pin = self.selected_pin()
        except ValueError as exc:
            messagebox.showerror("Invalid PPS output", str(exc))
            return
        self.run(["-i", str(index), "-L", f"{pin},{PIN_FUNCTIONS['Periodic output']}"])

    def configure_one_hz_output(self) -> None:
        self.period_preset_var.set("1 Hz")
        self.perout_period_var.set(PERIOD_PRESETS["1 Hz"])
        self.enable_perout(configure_pin=True)

    def set_pin_function(self) -> None:
        try:
            index = self.selected_index()
            pin = self.selected_pin()
        except ValueError as exc:
            messagebox.showerror("Invalid pin", str(exc))
            return
        function = PIN_FUNCTIONS[self.pin_function_var.get()]
        self.run(["-i", str(index), "-L", f"{pin},{function}"])

    def enable_perout(self, configure_pin: bool = False) -> None:
        try:
            index = self.selected_index()
            pin = self.selected_pin()
            period = self.parse_int(self.perout_period_var.get(), "Period", 0, 2**63 - 1)
            width_text = self.perout_width_var.get().strip()
            phase_text = self.perout_phase_var.get().strip()
            width = None if not width_text else self.parse_int(width_text, "Pulse width", 0, 2**63 - 1)
            phase = None if not phase_text else self.parse_int(phase_text, "Phase", 0, 2**63 - 1)
        except ValueError as exc:
            messagebox.showerror("Invalid output", str(exc))
            return

        args = ["-i", str(index)]
        if configure_pin:
            args.extend(["-L", f"{pin},{PIN_FUNCTIONS['Periodic output']}"])
        args.extend(["-p", str(period)])
        if width is not None:
            args.extend(["-w", str(width)])
        if phase is not None:
            args.extend(["-H", str(phase)])
        self.run(args)

    def disable_perout(self) -> None:
        try:
            index = self.selected_index()
        except ValueError as exc:
            messagebox.showerror("Invalid output", str(exc))
            return
        self.run(["-i", str(index), "-p", "0"])

    def read_events(self) -> None:
        try:
            count = self.parse_int(self.event_count_var.get(), "Event count", -1, 1000000)
        except ValueError as exc:
            messagebox.showerror("Invalid event count", str(exc))
            return
        self.run_with_index(["-e", str(count)])

    def adjust_frequency(self) -> None:
        try:
            value = self.parse_int(self.freq_adjust_var.get(), "Frequency adjustment", -(2**31), 2**31 - 1)
        except ValueError as exc:
            messagebox.showerror("Invalid frequency", str(exc))
            return
        self.run(["-f", str(value)])

    def shift_time(self) -> None:
        try:
            seconds = self.parse_int(self.shift_seconds_var.get(), "Shift seconds", -(2**31), 2**31 - 1)
            nanoseconds = self.parse_int(self.shift_ns_var.get(), "Shift nanoseconds", -(2**31), 2**31 - 1)
        except ValueError as exc:
            messagebox.showerror("Invalid time shift", str(exc))
            return
        self.run(["-t", str(seconds), "-n", str(nanoseconds)])

    def adjust_phase(self) -> None:
        try:
            value = self.parse_int(self.phase_offset_var.get(), "Phase offset", -(2**31), 2**31 - 1)
        except ValueError as exc:
            messagebox.showerror("Invalid phase", str(exc))
            return
        self.run(["-o", str(value)])

    def set_time_seconds(self) -> None:
        try:
            value = self.parse_int(self.set_seconds_var.get(), "PTP seconds", 0, 2**31 - 1)
        except ValueError as exc:
            messagebox.showerror("Invalid time", str(exc))
            return
        self.run(["-T", str(value)])

    def enable_mask_channel(self) -> None:
        try:
            value = self.parse_int(self.mask_channel_var.get(), "Mask channel", 0, MAX_UI_INDEX)
        except ValueError as exc:
            messagebox.showerror("Invalid mask channel", str(exc))
            return
        self.run(["-F", str(value)])

    def run_raw_args(self) -> None:
        raw_args = self.raw_args_var.get().strip()
        if not raw_args:
            messagebox.showerror("Invalid arguments", "Raw args are required")
            return
        try:
            args = shlex.split(raw_args)
        except ValueError as exc:
            messagebox.showerror("Invalid arguments", str(exc))
            return
        self.run(args)


def main() -> int:
    app = PpsToolGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
