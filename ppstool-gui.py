#!/usr/bin/env python3
"""Terminal UI for the ppstool command line utility."""

from __future__ import annotations

import glob
import hashlib
import os
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
from typing import Callable, List, Optional, Tuple
import zipfile

try:
    import curses
except ModuleNotFoundError:
    curses = None


DEFAULT_DEVICE = "/dev/ptp0"
MAX_SAMPLES = 25
MAX_UI_INDEX = 1024
MAX_OUTPUT_LINES = 1000
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


def running_zipapp() -> Optional[Path]:
    argv_path = Path(sys.argv[0])
    if argv_path.exists() and zipfile.is_zipfile(argv_path):
        return argv_path.resolve()
    return None


def cache_root() -> Path:
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "ppstool"
    return Path.home() / ".cache" / "ppstool"


def bundled_command() -> Optional[str]:
    archive = running_zipapp()
    if archive is None:
        return None

    try:
        with zipfile.ZipFile(archive) as bundle:
            data = bundle.read("ppstool")
    except (KeyError, OSError, zipfile.BadZipFile):
        return None

    digest = hashlib.sha256(data).hexdigest()[:16]
    for base_dir in (cache_root(), Path(tempfile.gettempdir()) / "ppstool"):
        target_dir = base_dir / digest
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "ppstool"
            if not target.exists() or target.read_bytes() != data:
                target.write_bytes(data)
                target.chmod(0o755)
            return str(target)
        except OSError:
            continue
    return None


def default_command() -> str:
    bundled = bundled_command()
    if bundled:
        return bundled

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


def parse_int(value: str, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def build_base_command(command_text: str, device: str) -> List[str]:
    command_text = command_text.strip()
    if not command_text:
        raise ValueError("Command is required")
    command = shlex.split(command_text)
    device = device.strip()
    if device:
        command.extend(["-d", device])
    return command


Action = Tuple[str, Callable[[], None]]
Section = Tuple[str, Callable[[], List[Action]]]


class PpsToolTui:
    def __init__(self, stdscr: "curses.window") -> None:
        self.stdscr = stdscr
        self.command_text = default_command()
        self.device = ptp_devices()[0]
        self.channel = 0
        self.pin = 0
        self.section_index = 0
        self.action_index = 0
        self.status = "Ready"
        self.output_lines = [""]
        self.output_scroll = 0
        self.output_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.current_process: Optional[subprocess.Popen] = None
        self.sections: List[Section] = [
            ("Common", self.common_actions),
            ("Status", self.status_actions),
            ("PPS In", self.pps_input_actions),
            ("PPS Out", self.pps_output_actions),
            ("Pins", self.pin_actions),
            ("Time", self.time_actions),
            ("Advanced", self.advanced_actions),
            ("Settings", self.settings_actions),
        ]

    def run(self) -> int:
        self.configure_screen()
        while True:
            self.drain_output()
            self.draw()
            key = self.stdscr.getch()
            if key == -1:
                continue
            if self.handle_key(key):
                return 0

    def configure_screen(self) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        self.stdscr.keypad(True)
        self.stdscr.timeout(100)
        if curses.has_colors():
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(2, curses.COLOR_CYAN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_GREEN, -1)
            curses.init_pair(5, curses.COLOR_RED, -1)

    def color(self, pair: int) -> int:
        if curses.has_colors():
            return curses.color_pair(pair)
        return 0

    def handle_key(self, key: int) -> bool:
        if key in (ord("q"), ord("Q")):
            self.stop_command()
            return True
        if key in (curses.KEY_LEFT, ord("h")):
            self.move_section(-1)
        elif key in (curses.KEY_RIGHT, ord("l"), ord("\t")):
            self.move_section(1)
        elif key in (curses.KEY_UP, ord("k")):
            self.move_action(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move_action(1)
        elif key in (curses.KEY_NPAGE,):
            self.output_scroll = max(0, self.output_scroll - 8)
        elif key in (curses.KEY_PPAGE,):
            self.output_scroll += 8
        elif key in (ord("c"), ord("C")):
            self.output_lines = [""]
            self.output_scroll = 0
        elif key in (ord("x"), ord("X")):
            self.stop_command()
        elif key in (ord("s"), ord("S")):
            self.section_index = len(self.sections) - 1
            self.action_index = 0
        elif key in (curses.KEY_ENTER, 10, 13):
            self.invoke_selected()
        return False

    def move_section(self, delta: int) -> None:
        self.section_index = (self.section_index + delta) % len(self.sections)
        self.action_index = 0

    def move_action(self, delta: int) -> None:
        actions = self.current_actions()
        if actions:
            self.action_index = (self.action_index + delta) % len(actions)

    def current_actions(self) -> List[Action]:
        return self.sections[self.section_index][1]()

    def invoke_selected(self) -> None:
        actions = self.current_actions()
        if not actions:
            return
        self.action_index = min(self.action_index, len(actions) - 1)
        label, action = actions[self.action_index]
        try:
            self.status = label
            action()
        except ValueError as exc:
            self.status = str(exc)

    def draw(self) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        if height < 14 or width < 60:
            self.addstr(0, 0, "Terminal too small for ppstool TUI")
            self.addstr(1, 0, "Resize to at least 60x14, or press q.")
            self.stdscr.refresh()
            return

        self.draw_header(width)
        nav_width = min(22, max(16, width // 5))
        output_top = max(9, height // 2)
        self.draw_nav(2, 0, output_top - 2, nav_width)
        self.draw_actions(2, nav_width, output_top - 2, width - nav_width)
        self.draw_output(output_top, 0, height - output_top - 2, width)
        self.draw_footer(height - 2, width)
        self.draw_status(height - 1, width)
        self.stdscr.refresh()

    def draw_header(self, width: int) -> None:
        running = "running" if self.is_running() else "idle"
        header = f" ppstool TUI [{running}] "
        self.addstr(0, 0, header.ljust(width), self.color(1) | curses.A_BOLD)
        context = f"Device {self.device} | Pin {self.pin} | Channel {self.channel}"
        self.addstr(1, 1, context[: width - 2], self.color(2))

    def draw_nav(self, top: int, left: int, height: int, width: int) -> None:
        self.box(top, left, height, width, " Sections ")
        for index, (title, _) in enumerate(self.sections):
            attr = self.color(1) | curses.A_BOLD if index == self.section_index else 0
            self.addstr(top + 1 + index, left + 2, title[: width - 4].ljust(width - 4), attr)

    def draw_actions(self, top: int, left: int, height: int, width: int) -> None:
        title = f" {self.sections[self.section_index][0]} "
        self.box(top, left, height, width, title)
        actions = self.current_actions()
        self.action_index = min(self.action_index, max(0, len(actions) - 1))
        for index, (label, _) in enumerate(actions[: max(0, height - 2)]):
            attr = self.color(1) | curses.A_BOLD if index == self.action_index else 0
            marker = ">" if index == self.action_index else " "
            self.addstr(top + 1 + index, left + 2, f"{marker} {label}"[: width - 4].ljust(width - 4), attr)

        detail_top = top + len(actions) + 2
        if detail_top < top + height - 1:
            self.addstr(detail_top, left + 2, f"Command: {self.command_text}"[: width - 4], self.color(2))

    def draw_output(self, top: int, left: int, height: int, width: int) -> None:
        self.box(top, left, height, width, " Output ")
        visible_height = max(0, height - 2)
        lines = self.output_lines[:-1] if self.output_lines[-1] == "" else self.output_lines
        start = max(0, len(lines) - visible_height - self.output_scroll)
        stop = max(0, len(lines) - self.output_scroll)
        visible = lines[start:stop]
        for row, line in enumerate(visible[:visible_height]):
            self.addstr(top + 1 + row, left + 2, line[: width - 4])

    def draw_footer(self, y: int, width: int) -> None:
        help_text = "Arrows/hjkl move | Enter run/edit | s settings | x stop | c clear | PgUp/PgDn output | q quit"
        self.addstr(y, 0, help_text[:width].ljust(width), self.color(3))

    def draw_status(self, y: int, width: int) -> None:
        self.addstr(y, 0, self.status[:width].ljust(width), self.color(4 if not self.is_running() else 3))

    def box(self, top: int, left: int, height: int, width: int, title: str = "") -> None:
        if height <= 1 or width <= 1:
            return
        window = self.stdscr.derwin(height, width, top, left)
        window.box()
        if title:
            window.addstr(0, 2, title[: max(0, width - 4)])

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        try:
            self.stdscr.addstr(y, x, text[: width - x], attr)
        except curses.error:
            pass

    def prompt(self, label: str, current: str = "") -> str:
        height, width = self.stdscr.getmaxyx()
        prompt = f"{label}"
        if current:
            prompt += f" [{current}]"
        prompt += ": "
        y = height - 1
        self.stdscr.timeout(-1)
        curses.echo()
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        self.addstr(y, 0, " " * width)
        self.addstr(y, 0, prompt[: width - 1], self.color(3))
        self.stdscr.refresh()
        try:
            raw = self.stdscr.getstr(y, min(len(prompt), width - 1), max(1, width - len(prompt) - 1))
            value = raw.decode("utf-8", errors="replace").strip()
        finally:
            curses.noecho()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.stdscr.timeout(100)
        return current if value == "" else value

    def prompt_int(self, label: str, current: int, minimum: int, maximum: int) -> int:
        value = self.prompt(label, str(current))
        return parse_int(value, label, minimum, maximum)

    def base_command(self) -> List[str]:
        return build_base_command(self.command_text, self.device)

    def is_running(self) -> bool:
        return self.current_process is not None and self.current_process.poll() is None

    def run_command(self, args: List[str]) -> None:
        if self.is_running():
            self.status = "A command is already running. Press x to stop it."
            return
        command = self.base_command() + args
        self.append_output(f"$ {command_to_text(command)}\n")
        self.status = "Running command"
        thread = threading.Thread(target=self.run_worker, args=(command,), daemon=True)
        thread.start()

    def run_worker(self, command: List[str]) -> None:
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
            self.output_queue.put(("done", process.wait()))
        except FileNotFoundError:
            self.output_queue.put(("line", f"Command not found: {command[0]}\n"))
            self.output_queue.put(("done", 127))
        except OSError as exc:
            self.output_queue.put(("line", f"{exc}\n"))
            self.output_queue.put(("done", 1))

    def drain_output(self) -> None:
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "line":
                    self.append_output(str(payload))
                elif kind == "done":
                    self.current_process = None
                    self.append_output(f"\n[exit {payload}]\n\n")
                    self.status = f"Command exited {payload}"
        except queue.Empty:
            pass

    def append_output(self, text: str) -> None:
        for part in text.splitlines(True):
            if part.endswith("\n"):
                self.output_lines[-1] += part[:-1]
                self.output_lines.append("")
            else:
                self.output_lines[-1] += part
        if len(self.output_lines) > MAX_OUTPUT_LINES:
            self.output_lines = self.output_lines[-MAX_OUTPUT_LINES:]
        self.output_scroll = 0

    def stop_command(self) -> None:
        process = self.current_process
        if process and process.poll() is None:
            self.append_output("\n[terminating]\n")
            process.terminate()
            self.status = "Terminating command"

    def run_with_index(self, args: List[str]) -> None:
        self.run_command(["-i", str(self.channel)] + args)

    def common_actions(self) -> List[Action]:
        return [
            ("Inspect device", lambda: self.run_command(["-c", "-l"])),
            ("Configure PPS input", self.configure_pps_input),
            ("Configure 1 Hz PPS output", self.configure_one_hz_output),
            ("Read PPS input until stopped", lambda: self.run_with_index(["-e", "-1"])),
            ("Enable system PPS", lambda: self.run_command(["-P", "1"])),
            ("Disable periodic output", self.disable_perout),
        ]

    def status_actions(self) -> List[Action]:
        return [
            ("Capabilities", lambda: self.run_command(["-c"])),
            ("Pin configuration", lambda: self.run_command(["-l"])),
            ("Clock time", lambda: self.run_command(["-g"])),
            ("Cross timestamp", lambda: self.run_command(["-X"])),
            ("Measure offset", self.measure_offset),
            ("Read extended time", self.read_extended),
        ]

    def pps_input_actions(self) -> List[Action]:
        return [
            ("Set pin as input", self.configure_pps_input),
            ("Read events", self.read_events),
            ("Read until stopped", lambda: self.run_with_index(["-e", "-1"])),
        ]

    def pps_output_actions(self) -> List[Action]:
        return [
            ("Set pin as output", self.configure_pps_output_pin),
            ("Apply output", self.enable_perout),
            ("Set pin and apply", lambda: self.enable_perout(configure_pin=True)),
            ("Disable output", self.disable_perout),
        ]

    def pin_actions(self) -> List[Action]:
        return [
            ("Apply pin function", self.set_pin_function),
            ("List pins", lambda: self.run_command(["-l"])),
        ]

    def time_actions(self) -> List[Action]:
        return [
            ("Get PTP clock time", lambda: self.run_command(["-g"])),
            ("Set PTP from system time", lambda: self.run_command(["-s"])),
            ("Set system from PTP time", lambda: self.run_command(["-S"])),
            ("Apply frequency adjustment", self.adjust_frequency),
            ("Shift PTP time", self.shift_time),
            ("Apply phase offset", self.adjust_phase),
            ("Set PTP to seconds", self.set_time_seconds),
        ]

    def advanced_actions(self) -> List[Action]:
        return [
            ("Flag test", lambda: self.run_with_index(["-z"])),
            ("Enable system PPS", lambda: self.run_command(["-P", "1"])),
            ("Disable system PPS", lambda: self.run_command(["-P", "0"])),
            ("Enable single mask channel", self.enable_mask_channel),
            ("Run raw args", self.run_raw_args),
        ]

    def settings_actions(self) -> List[Action]:
        return [
            ("Edit command", self.edit_command),
            ("Select device", self.select_device),
            ("Edit pin", self.edit_pin),
            ("Edit channel", self.edit_channel),
            ("Refresh devices", self.refresh_devices),
        ]

    def edit_command(self) -> None:
        self.command_text = self.prompt("Command", self.command_text)
        self.status = "Command updated"

    def select_device(self) -> None:
        devices = ptp_devices()
        options = ", ".join(f"{index + 1}={device}" for index, device in enumerate(devices))
        self.status = f"Devices: {options}"
        value = self.prompt("Device number/path", self.device)
        try:
            self.device = devices[int(value) - 1]
        except (ValueError, IndexError):
            self.device = value
        self.status = "Device updated"

    def edit_pin(self) -> None:
        self.pin = self.prompt_int("Pin", self.pin, 0, MAX_UI_INDEX)
        self.status = "Pin updated"

    def edit_channel(self) -> None:
        self.channel = self.prompt_int("Channel", self.channel, 0, MAX_UI_INDEX)
        self.status = "Channel updated"

    def refresh_devices(self) -> None:
        devices = ptp_devices()
        if self.device not in devices:
            self.device = devices[0]
        self.status = f"Devices refreshed: {', '.join(devices)}"

    def measure_offset(self) -> None:
        samples = self.prompt_int("Samples", 5, 1, MAX_SAMPLES)
        self.run_command(["-k", str(samples)])

    def read_extended(self) -> None:
        samples = self.prompt_int("Samples", 5, 1, MAX_SAMPLES)
        self.run_command(["-x", str(samples)])

    def configure_pps_input(self) -> None:
        self.run_command(["-i", str(self.channel), "-L", f"{self.pin},{PIN_FUNCTIONS['External timestamp']}"])

    def configure_pps_output_pin(self) -> None:
        self.run_command(["-i", str(self.channel), "-L", f"{self.pin},{PIN_FUNCTIONS['Periodic output']}"])

    def configure_one_hz_output(self) -> None:
        self.enable_perout(configure_pin=True, preset_period=PERIOD_PRESETS["1 Hz"])

    def set_pin_function(self) -> None:
        names = list(PIN_FUNCTIONS.keys())
        options = ", ".join(f"{index + 1}={name}" for index, name in enumerate(names))
        self.status = f"Functions: {options}"
        value = self.prompt("Function number/name", "External timestamp")
        try:
            function_name = names[int(value) - 1]
        except (ValueError, IndexError):
            function_name = value
        if function_name not in PIN_FUNCTIONS:
            raise ValueError("Function must be one of: " + ", ".join(names))
        self.run_command(["-i", str(self.channel), "-L", f"{self.pin},{PIN_FUNCTIONS[function_name]}"])

    def enable_perout(self, configure_pin: bool = False, preset_period: str = "") -> None:
        period = parse_int(preset_period, "Period", 0, 2**63 - 1) if preset_period else self.prompt_period()
        width_text = self.prompt("Pulse width ns (optional)", "")
        phase_text = self.prompt("Phase ns (optional)", "")

        args = ["-i", str(self.channel)]
        if configure_pin:
            args.extend(["-L", f"{self.pin},{PIN_FUNCTIONS['Periodic output']}"])
        args.extend(["-p", str(period)])
        if width_text:
            args.extend(["-w", str(parse_int(width_text, "Pulse width", 0, 2**63 - 1))])
        if phase_text:
            args.extend(["-H", str(parse_int(phase_text, "Phase", 0, 2**63 - 1))])
        self.run_command(args)

    def prompt_period(self) -> int:
        presets = list(PERIOD_PRESETS.items())
        options = ", ".join(
            f"{index + 1}={label}{' ' + value + 'ns' if value else ''}"
            for index, (label, value) in enumerate(presets)
        )
        self.status = f"Presets: {options}"
        value = self.prompt("Preset number or period ns", "1")
        try:
            _, period = presets[int(value) - 1]
        except (ValueError, IndexError):
            period = value
        if not period:
            period = self.prompt("Period ns", PERIOD_PRESETS["1 Hz"])
        return parse_int(period, "Period", 0, 2**63 - 1)

    def disable_perout(self) -> None:
        self.run_command(["-i", str(self.channel), "-p", "0"])

    def read_events(self) -> None:
        count = self.prompt_int("Event count (-1 until stopped)", 1, -1, 1000000)
        self.run_with_index(["-e", str(count)])

    def adjust_frequency(self) -> None:
        value = self.prompt_int("Frequency adjustment ppb", 0, -(2**31), 2**31 - 1)
        self.run_command(["-f", str(value)])

    def shift_time(self) -> None:
        seconds = self.prompt_int("Shift seconds", 0, -(2**31), 2**31 - 1)
        nanoseconds = self.prompt_int("Shift nanoseconds", 0, -(2**31), 2**31 - 1)
        self.run_command(["-t", str(seconds), "-n", str(nanoseconds)])

    def adjust_phase(self) -> None:
        value = self.prompt_int("Phase offset ns", 0, -(2**31), 2**31 - 1)
        self.run_command(["-o", str(value)])

    def set_time_seconds(self) -> None:
        value = self.prompt_int("PTP seconds", 0, 0, 2**31 - 1)
        self.run_command(["-T", str(value)])

    def enable_mask_channel(self) -> None:
        value = self.prompt_int("Mask channel", 0, 0, MAX_UI_INDEX)
        self.run_command(["-F", str(value)])

    def run_raw_args(self) -> None:
        raw_args = self.prompt("Raw args")
        if not raw_args:
            raise ValueError("Raw args are required")
        self.run_command(shlex.split(raw_args))


class PlainFallback:
    def run(self) -> int:
        print("ppstool requires a terminal for the curses TUI.")
        print("Run it from an interactive terminal, or pass --help for usage.")
        return 1


def run_curses() -> int:
    if curses is None:
        print("Python curses support is not available.", file=sys.stderr)
        return 1
    return curses.wrapper(lambda stdscr: PpsToolTui(stdscr).run())


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: ppstool-gui [--help]")
        print()
        print("Launches the ppstool terminal UI. Build with `make zipapp`")
        print("to embed the ppstool CLI inside ppstool-gui.pyz.")
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return PlainFallback().run()
    return run_curses()


if __name__ == "__main__":
    raise SystemExit(main())
