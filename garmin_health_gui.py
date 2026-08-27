#!/usr/bin/env python3
"""GUI for exporting Garmin data and managing the daily report task."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path
from tkinter import (
    BOTH,
    DISABLED,
    END,
    NORMAL,
    W,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    LabelFrame,
    StringVar,
    Tk,
    filedialog,
    messagebox,
)
from tkinter.scrolledtext import ScrolledText

from garmin_health_reporter import (
    ASSISTANT_DIR,
    DEFAULT_SOURCES,
    EXPORT_DIR,
    LOCAL_EXPORT_DIR,
    LOG_FILE,
    REPORT_DIR,
    ROOT,
    install_daily_task,
    query_daily_task,
    uninstall_daily_task,
    validate_time,
)


PYTHON_EXE = Path(r"C:\Program Files\Python311\python.exe")
REPORTER = ROOT / "garmin_health_reporter.py"


class GarminHealthGui:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Garmin Data Export and Health Report")
        self.root.geometry("980x720")
        self.root.minsize(880, 640)

        self.queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        yesterday = date.today() - timedelta(days=1)
        self.report_date = StringVar(value=yesterday.isoformat())
        self.days_back = StringVar(value="30")
        self.activity_limit = StringVar(value="20")
        self.local_export = StringVar(value=str(LOCAL_EXPORT_DIR))
        self.daily_time = StringVar(value="08:10")
        self.download_originals = BooleanVar(value=False)
        self.all_sources = BooleanVar(value=True)
        self.source_vars = {source: BooleanVar(value=True) for source in sorted(DEFAULT_SOURCES)}
        self.status = StringVar(value="Ready")

        self.build_ui()
        self.root.after(100, self.process_queue)
        self.refresh_task_status()

    def build_ui(self) -> None:
        outer = Frame(self.root, padx=14, pady=12)
        outer.pack(fill=BOTH, expand=True)

        Label(outer, text="Garmin Data Export and Daily Health Report", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, columnspan=4, sticky=W, pady=(0, 10)
        )

        controls = LabelFrame(outer, text="Export")
        controls.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)

        self.add_field(controls, 0, 0, "Date", self.report_date, width=16)
        self.add_field(controls, 0, 2, "Days Back", self.days_back, width=10)
        self.add_field(controls, 1, 0, "Activity Limit", self.activity_limit, width=10)
        Checkbutton(controls, text="Download original activity files", variable=self.download_originals).grid(
            row=1, column=2, columnspan=2, sticky=W, padx=8, pady=4
        )
        self.add_field(controls, 2, 0, "Local Garmin Export", self.local_export, width=64)
        Button(controls, text="Browse", width=12, command=self.browse_local_export).grid(row=2, column=2, sticky=W, padx=8)

        sources = LabelFrame(outer, text="Sources")
        sources.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        Checkbutton(sources, text="All possible source groups", variable=self.all_sources, command=self.toggle_all_sources).grid(
            row=0, column=0, sticky=W, padx=8, pady=4
        )
        for index, source in enumerate(sorted(DEFAULT_SOURCES), start=1):
            Checkbutton(sources, text=source, variable=self.source_vars[source], command=self.sync_all_checkbox).grid(
                row=index // 3 + 1,
                column=index % 3,
                sticky=W,
                padx=8,
                pady=4,
            )

        service = LabelFrame(outer, text="Daily Service")
        service.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        self.add_field(service, 0, 0, "Run Time", self.daily_time, width=10)
        Button(service, text="Install Daily Service", width=18, command=self.install_service).grid(row=0, column=2, sticky=W, padx=8)
        Button(service, text="Remove Service", width=16, command=self.remove_service).grid(row=0, column=3, sticky=W, padx=8)
        Button(service, text="Check Service", width=16, command=self.refresh_task_status).grid(row=0, column=4, sticky=W, padx=8)

        buttons = Frame(outer)
        buttons.grid(row=4, column=0, columnspan=4, sticky=W, pady=(0, 10))
        self.run_button = Button(buttons, text="Run Export Now", width=18, command=self.run_export)
        self.run_button.pack(side="left", padx=(0, 8))
        Button(buttons, text="Open Latest Report", width=18, command=self.open_latest_report).pack(side="left", padx=(0, 8))
        Button(buttons, text="Open Raw Exports", width=18, command=lambda: self.open_path(EXPORT_DIR)).pack(side="left", padx=(0, 8))
        Button(buttons, text="Open Assistant Context", width=22, command=lambda: self.open_path(ASSISTANT_DIR)).pack(side="left", padx=(0, 8))
        Button(buttons, text="Open Log", width=12, command=lambda: self.open_path(LOG_FILE)).pack(side="left")

        Label(outer, textvariable=self.status, anchor="w").grid(row=5, column=0, columnspan=4, sticky="ew", pady=(0, 6))

        self.log = ScrolledText(outer, height=22, wrap="word", state=DISABLED)
        self.log.grid(row=6, column=0, columnspan=4, sticky="nsew")
        outer.rowconfigure(6, weight=1)
        outer.columnconfigure(1, weight=1)

    def add_field(self, parent: Frame, row: int, column: int, label: str, variable: StringVar, width: int) -> None:
        Label(parent, text=label).grid(row=row, column=column, sticky=W, padx=(8, 6), pady=4)
        Entry(parent, textvariable=variable, width=width).grid(row=row, column=column + 1, sticky="ew", padx=(0, 8), pady=4)

    def browse_local_export(self) -> None:
        selected = filedialog.askopenfilename(title="Select Garmin export ZIP")
        if not selected:
            selected = filedialog.askdirectory(title="Select Garmin export folder")
        if selected:
            self.local_export.set(selected)

    def toggle_all_sources(self) -> None:
        value = self.all_sources.get()
        for var in self.source_vars.values():
            var.set(value)

    def sync_all_checkbox(self) -> None:
        self.all_sources.set(all(var.get() for var in self.source_vars.values()))

    def selected_sources(self) -> str:
        if self.all_sources.get():
            return "all"
        selected = [source for source, var in self.source_vars.items() if var.get()]
        if not selected:
            raise ValueError("Select at least one source group.")
        return ",".join(selected)

    def build_export_command(self) -> list[str]:
        if not PYTHON_EXE.exists():
            raise RuntimeError("Python was not found at {}.".format(PYTHON_EXE))
        date.fromisoformat(self.report_date.get().strip())
        days_back = int(self.days_back.get().strip())
        activity_limit = int(self.activity_limit.get().strip())
        if days_back < 1 or activity_limit < 0:
            raise ValueError("Days Back must be at least 1 and Activity Limit must be 0 or more.")

        command = [
            str(PYTHON_EXE),
            str(REPORTER),
            "--date",
            self.report_date.get().strip(),
            "--days-back",
            str(days_back),
            "--sources",
            self.selected_sources(),
            "--activity-limit",
            str(activity_limit),
            "--local-export",
            self.local_export.get().strip() or str(LOCAL_EXPORT_DIR),
        ]
        if self.download_originals.get():
            command.append("--download-activity-originals")
        return command

    def run_export(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Garmin Export", "Export is already running.")
            return
        try:
            command = self.build_export_command()
        except Exception as exc:
            messagebox.showerror("Invalid Export Settings", str(exc))
            return
        self.set_running(True)
        self.worker = threading.Thread(target=lambda: self.run_command(command), daemon=True)
        self.worker.start()

    def run_command(self, command: list[str]) -> None:
        self.queue.put("Running:\n{}\n".format(" ".join('"{}"'.format(part) if " " in part else part for part in command)))
        try:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                self.queue.put(line.rstrip())
            code = process.wait()
            self.queue.put("Finished with exit code {}.".format(code))
            self.status.set("Finished" if code == 0 else "Failed")
        except Exception as exc:
            self.queue.put("ERROR: {}".format(exc))
            self.status.set("Failed")
        finally:
            self.root.after(0, lambda: self.set_running(False))

    def install_service(self) -> None:
        try:
            start_time = self.daily_time.get().strip()
            validate_time(start_time)
            output = install_daily_task(start_time)
            self.log_line(output.strip() or "Installed daily service.")
            self.refresh_task_status()
        except Exception as exc:
            messagebox.showerror("Daily Service", str(exc))
            self.log_line("Failed to install service: {}".format(exc))

    def remove_service(self) -> None:
        try:
            output = uninstall_daily_task()
            self.log_line(output.strip() or "Removed daily service.")
            self.refresh_task_status()
        except Exception as exc:
            messagebox.showerror("Daily Service", str(exc))
            self.log_line("Failed to remove service: {}".format(exc))

    def refresh_task_status(self) -> None:
        output = query_daily_task()
        if output:
            self.status.set("Daily service installed")
            self.log_line("Daily service status:\n{}".format(output.strip()))
        else:
            self.status.set("Daily service is not installed")
            self.log_line("Daily service is not installed.")

    def open_latest_report(self) -> None:
        report = REPORT_DIR / "{}.md".format(self.report_date.get().strip())
        self.open_path(report if report.exists() else REPORT_DIR)

    def open_path(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path == LOG_FILE and not path.exists():
            path.write_text("", encoding="utf-8")
        if path.is_dir() or path.exists():
            os.startfile(str(path))
        else:
            os.startfile(str(path.parent))

    def set_running(self, running: bool) -> None:
        self.run_button.config(state=DISABLED if running else NORMAL)
        if running:
            self.status.set("Export running...")

    def log_line(self, text: str) -> None:
        self.queue.put(text)

    def process_queue(self) -> None:
        try:
            while True:
                text = self.queue.get_nowait()
                self.log.config(state=NORMAL)
                self.log.insert(END, text + "\n")
                self.log.see(END)
                self.log.config(state=DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)


def main() -> int:
    root = Tk()
    GarminHealthGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
