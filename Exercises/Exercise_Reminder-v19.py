import csv
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import sys
import random
import platform
import webbrowser
import os
from pathlib import Path
from datetime import datetime, date

try:
    import winsound  # Windows-only; safely imported in try/except for cross-platform
except Exception:
    winsound = None


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

# ---------------------------------------------------------------------------
# Category definitions
# key → (display label, button-highlight color)
# ---------------------------------------------------------------------------
CATEGORIES = {
    "mobility":     ("🔵 Mobility / Reset",        "#BBDEFB"),   # light blue
    "stability":    ("🟢 Structural Stability",     "#C8E6C9"),   # light green
    "strength":     ("🔴 Primary Strength",         "#FFCDD2"),   # light red
    "shoulder":     ("🟣 Shoulder Integrity",       "#E1BEE7"),   # light purple
    "foot":         ("🟠 Foot / Ankle Integrity",   "#FFE0B2"),   # light orange
    "cardio":       ("❤️ VO₂ / Conditioning",       "#FCE4EC"),   # light pink
    "recovery":     ("🟡 Recovery / Circulation",   "#FFF9C4"),   # light yellow
    "grip":         ("🟤 Grip / Carry",             "#D7CCC8"),   # light brown
    "coordination": ("🧠 Daily / Coordination",     "#F8BBD9"),   # lavender pink
    "fullday":      ("📅 Full Day",                 "#EEEEEE"),   # light grey — original == exercises
}

NO_FOCUS_LABEL = "— No Focus —"


class ExerciseReminder:
    """
    Exercise Reminder with support for:
    - URLs (opened in default web browser)
    - Local video files (absolute or relative paths), opened via file:// in the browser
    - Category-based daily focus: focus exercises listed first in the reminder popup
    Visual markers:
    - '*' indicates a URL
    - '▶' indicates a local video file
    Data file: Exercise_Reminder.csv  (columns: name, categories, link)
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Exercise Reminder")
        self.root.geometry("460x400")
        self.root.resizable(True, True)

        # Timer variables
        self.interval_minutes = 45  # Default 45 minutes
        self.timer_thread = None
        self.is_running = False
        self.is_paused = False
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.elapsed_when_paused = 0

        # Exercise tracking
        self.exercise_count = 0
        self.session_start_time = time.time()

        # Data structures for content
        self.exercises = []                     # list[str] of exercise names
        self.exercise_urls = {}                 # name -> url
        self.exercise_videos = {}               # name -> file path
        self.exercise_click_counts = {}         # name -> int
        self.exercise_categories: dict[str, list[str]] = {}  # name -> list of category keys

        # Focus category (key from CATEGORIES, or None)
        self.focus_category: str | None = None

        # Load exercises
        self.load_exercises_from_file()

        # Build UI, initialize today's displayed log totals, and start
        self.setup_ui()
        self.update_time_today_display()
        self.start_timer()

    # ---------------------- Utility methods ----------------------
    @staticmethod
    def is_url(token: str) -> bool:
        return token.startswith("http://") or token.startswith("https://")

    @staticmethod
    def looks_like_path(token: str) -> bool:
        """
        Heuristic to decide if a token is a file path WITHOUT requiring the file to exist.
        """
        if not token:
            return False
        if (len(token) >= 2 and token[1] == ":" and (token[0].isalpha()) and (token[2:3] in ("\\", "/"))) or token.startswith("\\\\"):
            return True
        if ("/" in token) or ("\\" in token):
            return True
        ext = Path(token).suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return True
        return False

    def resolve_video_path(self, raw_path: str) -> Path:
        p = Path(raw_path)
        if p.is_absolute():
            return p
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        return (script_dir / p).resolve()

    def open_video(self, raw_path: str) -> None:
        try:
            abs_path = self.resolve_video_path(raw_path)
            file_url = abs_path.as_uri()
            webbrowser.open(file_url)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open video: {e}")

    def open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open URL: {e}")

    # ---------------------- File loading (CSV) ----------------------
    def load_exercises_from_file(self) -> None:
        """
        Parse Exercise_Reminder.csv.
        Expected columns (header row required): name, categories, link
          - name:       display name of the exercise
          - categories: semicolon-separated list of category keys, e.g. "mobility;shoulder"
          - link:       optional URL or local file path; empty if none
        """
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(script_dir, "Exercise_Reminder.csv")

            with open(file_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = (row.get("name") or "").strip()
                    if not name:
                        continue

                    # Parse semicolon-separated categories
                    cats_raw = (row.get("categories") or "").strip()
                    cats = [c.strip() for c in cats_raw.split(";") if c.strip()] if cats_raw else []

                    link = (row.get("link") or "").strip()

                    if name not in self.exercises:
                        self.exercises.append(name)

                    self.exercise_categories[name] = cats
                    self.exercise_click_counts.setdefault(name, 0)

                    if link:
                        if self.is_url(link):
                            self.exercise_urls[name] = link
                        elif self.looks_like_path(link):
                            self.exercise_videos[name] = link
                        # else: unrecognised link format — treat as name-only

            print(f"Loaded {len(self.exercises)} exercises from CSV")

        except FileNotFoundError:
            print("Exercise_Reminder.csv not found; using defaults")
            self.exercises = ["Push-ups", "Squats", "Jumping Jacks", "Plank", "Stretches", "Wall Push-ups"]
        except Exception as e:
            print(f"Error reading exercise CSV: {e}")
            self.exercises = []

    # ---------------------- Log helpers ----------------------
    def get_log_path(self) -> str:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, "Exercise_Reminder_Log.txt")

    @staticmethod
    def _format_duration_hhmmss(seconds: float) -> str:
        seconds = max(0, int(seconds))
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def calculate_time_today_seconds(self, target_day: date | None = None, assume_last_entry_closes: bool = True) -> int:
        if target_day is None:
            target_day = date.today()

        log_path = self.get_log_path()
        if not os.path.exists(log_path):
            return 0

        total_seconds = 0.0
        window_start = None
        last_entry_time = None

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if len(line) < 20:
                        continue
                    try:
                        entry_time = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    if entry_time.date() != target_day:
                        continue

                    entry_text = line[20:].strip()
                    last_entry_time = entry_time

                    if entry_text == "Exercise period closed":
                        if window_start is not None:
                            total_seconds += max(0.0, (entry_time - window_start).total_seconds())
                            window_start = None
                    else:
                        if window_start is None:
                            window_start = entry_time

            if assume_last_entry_closes and window_start is not None and last_entry_time is not None:
                total_seconds += max(0.0, (last_entry_time - window_start).total_seconds())

        except Exception:
            return 0

        return int(total_seconds)

    def update_time_today_display(self) -> None:
        if hasattr(self, "time_today_var"):
            seconds = self.calculate_time_today_seconds(assume_last_entry_closes=True)
            self.time_today_var.set(self._format_duration_hhmmss(seconds))

    # ---------------------- Logging ----------------------
    def log_button_press(self, button_name: str) -> None:
        try:
            log_path = self.get_log_path()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} {button_name}\n")
        except Exception:
            pass

    # ---------------------- Sound methods ----------------------
    def play_notification_sound(self) -> None:
        try:
            if platform.system() == "Windows" and winsound is not None:
                threading.Thread(target=self._play_windows_tones, daemon=True).start()
            else:
                threading.Thread(target=self._play_system_bells, daemon=True).start()
        except Exception:
            self.root.bell()

    def _play_windows_tones(self) -> None:
        try:
            frequencies = [523, 659, 784, 523]
            for freq in frequencies:
                winsound.Beep(freq, 200)
                time.sleep(0.1)
        except Exception:
            try:
                winsound.MessageBeep(getattr(winsound, "MB_ICONASTERISK", 0))
            except Exception:
                pass

    def _play_system_bells(self) -> None:
        for _ in range(4):
            self.root.bell()
            time.sleep(0.2)

    # ---------------------- UI setup ----------------------
    def setup_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # ---- Row 0: Title ----
        title_label = ttk.Label(main_frame, text="Exercise Reminder", font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 14))

        # ---- Row 1: Progress counters ----
        counter_frame = ttk.LabelFrame(main_frame, text="Exercise Progress", padding="10")
        counter_frame.grid(row=1, column=0, columnspan=2, pady=(0, 10), sticky="ew")

        ttk.Label(counter_frame, text="Exercises completed today:").grid(row=0, column=0, padx=(0, 10))
        self.count_var = tk.StringVar(value="0")
        count_label = ttk.Label(counter_frame, textvariable=self.count_var, font=("Arial", 14, "bold"), foreground="blue")
        count_label.grid(row=0, column=1, padx=(0, 20))

        ttk.Label(counter_frame, text="Time today:").grid(row=0, column=2, padx=(0, 10))
        self.time_today_var = tk.StringVar(value="00:00:00")
        time_today_label = ttk.Label(counter_frame, textvariable=self.time_today_var, font=("Arial", 14, "bold"), foreground="blue")
        time_today_label.grid(row=0, column=3)

        # ---- Row 2: Focus category selector (NEW) ----
        focus_outer = ttk.LabelFrame(main_frame, text="Today's Focus", padding="8")
        focus_outer.grid(row=2, column=0, columnspan=2, pady=(0, 10), sticky="ew")

        # Build the list of display names for the combobox
        combo_values = [NO_FOCUS_LABEL] + [v[0] for v in CATEGORIES.values()]
        self.focus_category_var = tk.StringVar(value=NO_FOCUS_LABEL)
        focus_combo = ttk.Combobox(
            focus_outer,
            textvariable=self.focus_category_var,
            values=combo_values,
            state="readonly",
            width=38,
        )
        focus_combo.pack(side="left", padx=(0, 8))
        focus_combo.bind("<<ComboboxSelected>>", self._on_focus_changed)

        # Small colour swatch that updates when a category is chosen
        self._focus_swatch = tk.Label(focus_outer, text="  ", relief="flat", width=3)
        self._focus_swatch.pack(side="left")

        # ---- Row 3: Interval controls ----
        interval_frame = ttk.Frame(main_frame)
        interval_frame.grid(row=3, column=0, columnspan=2, pady=(0, 8), sticky="ew")

        ttk.Label(interval_frame, text="Reminder Interval:").grid(row=0, column=0, padx=(0, 10))
        self.interval_var = tk.StringVar(value=str(self.interval_minutes))
        interval_spinbox = ttk.Spinbox(interval_frame, from_=1, to=120, textvariable=self.interval_var, width=8)
        interval_spinbox.grid(row=0, column=1, padx=5)
        ttk.Label(interval_frame, text="min.").grid(row=0, column=2, padx=(4, 0))

        update_btn = ttk.Button(interval_frame, text="Update Interval", command=self.update_interval)
        update_btn.grid(row=0, column=3, padx=(20, 0))

        exercise_now_btn = ttk.Button(interval_frame, text="Exercise Now", command=self.exercise_window_now)
        exercise_now_btn.grid(row=1, column=1, padx=(20, 0), pady=(8, 0), sticky="e")

        open_log_btn = ttk.Button(interval_frame, text="Open Log", command=self.open_log)
        open_log_btn.grid(row=1, column=2, padx=(10, 0), pady=(8, 0), sticky="w")

        # ---- Row 4: Status ----
        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=4, column=0, columnspan=2, pady=14, sticky="ew")

        ttk.Label(status_frame, text="Status:").grid(row=0, column=0, sticky="w")
        self.status_var = tk.StringVar(value="Timer running")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, foreground="green")
        self.status_label.grid(row=1, column=0, sticky="w")

        self.countdown_var = tk.StringVar(value="--:--")
        self.countdown_label = ttk.Label(status_frame, textvariable=self.countdown_var, font=("Arial", 14, "bold"))
        self.countdown_label.grid(row=1, column=1, sticky="w")

        # ---- Row 5: Controls ----
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=5, column=0, columnspan=2, pady=14)

        self.start_stop_btn = ttk.Button(button_frame, text="Stop Timer", command=self.toggle_timer)
        self.start_stop_btn.grid(row=0, column=0, padx=5)

        self.pause_btn = ttk.Button(button_frame, text="Pause", command=self.toggle_pause)
        self.pause_btn.grid(row=0, column=1, padx=5)

        quit_btn = ttk.Button(button_frame, text="Quit", command=self.quit_app)
        quit_btn.grid(row=0, column=2, padx=5)

        # Start countdown updater
        self.update_countdown()

    # ---------------------- Focus category handling ----------------------
    def _on_focus_changed(self, _event=None) -> None:
        """Called when the combobox selection changes."""
        selected = self.focus_category_var.get()
        self.focus_category = None
        swatch_color = self.root.cget("background")  # default (no colour)

        if selected != NO_FOCUS_LABEL:
            for key, (display, color) in CATEGORIES.items():
                if display == selected:
                    self.focus_category = key
                    swatch_color = color
                    break

        try:
            self._focus_swatch.config(bg=swatch_color)
        except Exception:
            pass

    # ---------------------- Timer control ----------------------
    def start_timer(self) -> None:
        if not self.is_running:
            self.is_running = True
            self.is_paused = False
            self.stop_event.clear()
            self.pause_event.clear()
            self.start_time = time.time() - self.elapsed_when_paused
            self.timer_thread = threading.Thread(target=self.timer_worker, daemon=True)
            self.timer_thread.start()
            self.status_var.set("Timer running")
            self.status_label.config(foreground="green")
            self.start_stop_btn.config(text="Stop Timer")
            self.pause_btn.config(text="Pause", state="normal")

    def stop_timer(self) -> None:
        if self.is_running:
            self.is_running = False
            self.is_paused = False
            self.stop_event.set()
            self.pause_event.set()
            self.elapsed_when_paused = 0
            self.status_var.set("Timer stopped")
            self.status_label.config(foreground="red")
            self.start_stop_btn.config(text="Start Timer")
            self.pause_btn.config(text="Pause", state="disabled")
            self.countdown_var.set("--:--")

    def toggle_pause(self) -> None:
        if not self.is_running:
            return
        if self.is_paused:
            self.is_paused = False
            self.pause_event.clear()
            self.start_time = time.time() - self.elapsed_when_paused
            self.status_var.set("Timer running")
            self.status_label.config(foreground="green")
            self.pause_btn.config(text="Pause")
        else:
            self.is_paused = True
            self.pause_event.set()
            self.elapsed_when_paused = time.time() - self.start_time
            self.status_var.set("Timer paused")
            self.status_label.config(foreground="orange")
            self.pause_btn.config(text="Unpause")

    def toggle_timer(self) -> None:
        if self.is_running:
            self.stop_timer()
        else:
            self.start_timer()

    def exercise_window_now(self) -> None:
        if not self.is_running:
            messagebox.showinfo("Timer not running", "Start the timer first, then use 'Exercise Window Now'.")
            return
        target_elapsed = max(0, (self.interval_minutes * 60) - 1)
        if self.is_paused:
            self.elapsed_when_paused = target_elapsed
        else:
            self.start_time = time.time() - target_elapsed
        try:
            self.update_countdown()
        except Exception:
            pass

    def timer_worker(self) -> None:
        while self.is_running and not self.stop_event.is_set():
            if self.pause_event.is_set():
                if self.stop_event.wait(0.1):
                    break
                continue
            elapsed = time.time() - self.start_time
            if elapsed >= self.interval_minutes * 60:
                break
            if self.stop_event.wait(1):
                break

        if self.is_running and not self.stop_event.is_set():
            self.root.after(0, self.show_reminder)

    # ---------------------- Reminder handling ----------------------
    def show_reminder(self) -> None:
        if not self.is_running:
            return
        self.play_notification_sound()
        self.show_exercise_recommendations()

    def handle_exercise_click(self, exercise_name: str, exercises_frame: tk.Frame) -> None:
        self.log_button_press(exercise_name)
        self.exercise_click_counts[exercise_name] = (
            self.exercise_click_counts.get(exercise_name, 0) + 1
        )
        if exercise_name in self.exercise_urls:
            self.open_url(self.exercise_urls[exercise_name])
        elif exercise_name in self.exercise_videos:
            self.open_video(self.exercise_videos[exercise_name])
        self.refresh_button_colors(exercises_frame)

    def refresh_button_colors(self, exercises_frame: tk.Frame) -> None:
        for child in exercises_frame.winfo_children():
            if isinstance(child, tk.Button):
                button_text = child.cget("text")
                if button_text.endswith(" *"):
                    exercise_name = button_text[:-2]
                elif button_text.endswith(" ▶"):
                    exercise_name = button_text[:-2]
                else:
                    exercise_name = button_text
                bg_color = self.get_button_color(exercise_name)
                text_color = self.get_text_color(exercise_name)
                child.configure(bg=bg_color, fg=text_color)

    def get_button_color(self, exercise_name: str) -> str:
        clicks = self.exercise_click_counts.get(exercise_name, 0)
        colors = ["white", "yellow", "lightgreen", "lightblue", "orange", "pink"]
        return colors[clicks % len(colors)]

    @staticmethod
    def get_text_color(_: str) -> str:
        return "black"

    def handle_reminder_response(self, window: tk.Toplevel, snooze: bool = False, close_button: bool = False) -> None:
        window.destroy()
        if not snooze and not close_button:
            self.exercise_count += 1
            self.count_var.set(str(self.exercise_count))
            self.log_button_press("Exercise period closed")
            self.update_time_today_display()
        self.stop_event.set()
        self.pause_event.set()
        if snooze:
            self.start_new_timer(5)
        else:
            self.start_new_timer(self.interval_minutes)

    def start_new_timer(self, minutes: int) -> None:
        self.elapsed_when_paused = 0
        self.is_paused = False
        self.is_running = True
        self.stop_event.clear()
        self.pause_event.clear()

        original_interval = self.interval_minutes
        self.interval_minutes = minutes

        self.start_time = time.time()
        self.timer_thread = threading.Thread(
            target=self.timer_worker_with_restore, args=(original_interval,), daemon=True
        )
        self.timer_thread.start()

        self.status_var.set("Timer running (5 min snooze)" if minutes == 5 else "Timer running")
        self.status_label.config(foreground="green")
        self.start_stop_btn.config(text="Stop Timer")
        self.pause_btn.config(text="Pause", state="normal")

    def timer_worker_with_restore(self, original_interval: int) -> None:
        while self.is_running and not self.stop_event.is_set():
            if self.pause_event.is_set():
                if self.stop_event.wait(0.1):
                    break
                continue
            elapsed = time.time() - self.start_time
            if elapsed >= self.interval_minutes * 60:
                break
            if self.stop_event.wait(1):
                break
        self.interval_minutes = original_interval
        if self.is_running and not self.stop_event.is_set():
            self.root.after(0, self.show_reminder)

    # ---------------------- Settings & status ----------------------
    def update_interval(self) -> None:
        try:
            new_interval = int(self.interval_var.get())
            if new_interval < 1 or new_interval > 120:
                messagebox.showerror("Invalid Input", "Please enter a value between 1 and 120 minutes.")
                self.interval_var.set(str(self.interval_minutes))
                return
            self.interval_minutes = new_interval
            if self.is_running:
                was_paused = self.is_paused
                self.stop_timer()
                self.start_timer()
                if was_paused:
                    self.toggle_pause()
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter a valid number.")
            self.interval_var.set(str(self.interval_minutes))

    def update_countdown(self) -> None:
        if self.is_running and hasattr(self, "start_time"):
            if self.is_paused:
                remaining = (self.interval_minutes * 60) - self.elapsed_when_paused
            else:
                elapsed = time.time() - self.start_time
                remaining = (self.interval_minutes * 60) - elapsed
            if remaining > 0:
                minutes = int(remaining // 60)
                seconds = int(remaining % 60)
                self.countdown_var.set(f"{minutes:02d}:{seconds:02d}")
            else:
                self.countdown_var.set("00:00")
        elif not self.is_running:
            self.countdown_var.set("--:--")
        self.root.after(1000, self.update_countdown)

    def open_log(self) -> None:
        log_path = self.get_log_path()
        if not os.path.exists(log_path):
            messagebox.showinfo("No Log Yet", "No log file found yet. Complete an exercise session to create one.")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(log_path)
            elif platform.system() == "Darwin":
                import subprocess
                subprocess.Popen(["open", "-e", log_path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", log_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open log file:\n{e}")

    def quit_app(self) -> None:
        self.stop_timer()
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        sys.exit(0)

    # ---------------------- Pomodoro window ----------------------
    def open_pomodoro_window(self, parent_window: tk.Toplevel) -> None:
        pomodoro_window = tk.Toplevel(parent_window)
        pomodoro_window.title("Pomodoro")
        pomodoro_window.geometry("360x220")
        pomodoro_window.resizable(False, False)
        pomodoro_window.transient(parent_window)
        pomodoro_window.grab_set()
        pomodoro_window.attributes("-topmost", True)

        pomodoro_window.update_idletasks()
        try:
            px = parent_window.winfo_rootx()
            py = parent_window.winfo_rooty()
            pw = parent_window.winfo_width()
            ph = parent_window.winfo_height()
            x = px + (pw // 2) - (360 // 2)
            y = py + (ph // 2) - (220 // 2)
            pomodoro_window.geometry(f"360x220+{x}+{y}")
        except Exception:
            pass

        state = {
            "running": False,
            "end_time": None,
            "remaining": 5 * 60,
            "after_id": None,
        }

        def parse_duration(text: str) -> int:
            t = (text or "").strip()
            if not t:
                return 5 * 60
            try:
                if ":" in t:
                    parts = t.split(":")
                    if len(parts) != 2:
                        raise ValueError
                    mins = int(parts[0].strip() or "0")
                    secs = int(parts[1].strip() or "0")
                    if mins < 0 or secs < 0 or secs >= 60:
                        raise ValueError
                    return mins * 60 + secs
                else:
                    mins = int(t)
                    if mins < 0:
                        raise ValueError
                    return mins * 60
            except ValueError:
                messagebox.showerror("Invalid Input", "Enter time as minutes (e.g., 5) or mm:ss (e.g., 2:30).")
                return state["remaining"] if state["remaining"] > 0 else 5 * 60

        def format_remaining(seconds: int) -> str:
            seconds = max(0, int(seconds))
            m = seconds // 60
            s = seconds % 60
            return f"{m:02d}:{s:02d}"

        main_frame = ttk.Frame(pomodoro_window, padding="15")
        main_frame.pack(expand=True, fill="both")

        countdown_var = tk.StringVar(value=format_remaining(state["remaining"]))
        countdown_label = ttk.Label(main_frame, textvariable=countdown_var, font=("Arial", 28, "bold"))
        countdown_label.pack(pady=(0, 10))

        entry_frame = ttk.Frame(main_frame)
        entry_frame.pack(pady=(0, 10))

        ttk.Label(entry_frame, text="Time (min or mm:ss):").pack(side="left", padx=(0, 8))
        time_entry_var = tk.StringVar(value="5:00")
        time_entry = ttk.Entry(entry_frame, textvariable=time_entry_var, width=10)
        time_entry.pack(side="left")

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(10, 0))

        def stop_after_loop():
            if state["after_id"] is not None:
                try:
                    pomodoro_window.after_cancel(state["after_id"])
                except Exception:
                    pass
                state["after_id"] = None

        def update_loop():
            if not state["running"]:
                return
            remaining = state["end_time"] - time.time()
            if remaining <= 0:
                countdown_var.set("00:00")
                state["running"] = False
                stop_after_loop()
                try:
                    self.play_notification_sound()
                except Exception:
                    pass
                pomodoro_window.destroy()
                return
            state["remaining"] = remaining
            countdown_var.set(format_remaining(remaining))
            state["after_id"] = pomodoro_window.after(200, update_loop)

        def start_timer(seconds: int):
            state["remaining"] = seconds
            state["end_time"] = time.time() + seconds
            state["running"] = True
            stop_after_loop()
            countdown_var.set(format_remaining(seconds))
            update_loop()

        def reset_timer():
            seconds = parse_duration(time_entry_var.get())
            start_timer(seconds)

        def close_window():
            state["running"] = False
            stop_after_loop()
            pomodoro_window.destroy()

        reset_btn = ttk.Button(btn_frame, text="Reset Timer", command=reset_timer, width=12)
        reset_btn.grid(row=0, column=0, padx=5)

        close_btn = ttk.Button(btn_frame, text="Close", command=close_window, width=10)
        close_btn.grid(row=0, column=1, padx=5)

        start_timer(parse_duration(time_entry_var.get()))
        pomodoro_window.protocol("WM_DELETE_WINDOW", close_window)
        pomodoro_window.focus_force()

    # ---------------------- Stopwatch ----------------------
    @staticmethod
    def _format_hhmmss_cs(seconds: float) -> str:
        if seconds < 0:
            seconds = 0
        total_cs = int(seconds * 100)
        cs = total_cs % 100
        total_s = total_cs // 100
        s = total_s % 60
        total_m = total_s // 60
        m = total_m % 60
        h = total_m // 60
        return f"{h:02d}:{m:02d}:{s:02d}.{cs:02d}"

    def _add_stopwatch_to_window(self, parent: tk.Toplevel, container: ttk.Frame) -> None:
        state = {
            "running": False,
            "start_perf": None,
            "elapsed": 0.0,
            "after_id": None,
        }

        sw_frame = ttk.LabelFrame(container, text="Stopwatch", padding="10")
        sw_frame.pack(fill="x", pady=(0, 15))

        time_var = tk.StringVar(value=self._format_hhmmss_cs(0.0))
        time_label = ttk.Label(sw_frame, textvariable=time_var, font=("Arial", 18, "bold"))
        time_label.pack(pady=(0, 8))

        btns = ttk.Frame(sw_frame)
        btns.pack()

        def cancel_loop():
            if state["after_id"] is not None:
                try:
                    parent.after_cancel(state["after_id"])
                except Exception:
                    pass
                state["after_id"] = None

        def tick():
            if not state["running"]:
                return
            now = time.perf_counter()
            current_elapsed = state["elapsed"] + (now - state["start_perf"])
            time_var.set(self._format_hhmmss_cs(current_elapsed))
            state["after_id"] = parent.after(100, tick)

        def start():
            if state["running"]:
                return
            state["running"] = True
            state["start_perf"] = time.perf_counter()
            cancel_loop()
            tick()

        def stop():
            if not state["running"]:
                return
            now = time.perf_counter()
            state["elapsed"] += (now - state["start_perf"])
            state["running"] = False
            state["start_perf"] = None
            cancel_loop()
            time_var.set(self._format_hhmmss_cs(state["elapsed"]))

        def reset():
            state["running"] = False
            state["start_perf"] = None
            state["elapsed"] = 0.0
            cancel_loop()
            time_var.set(self._format_hhmmss_cs(0.0))

        start_btn = ttk.Button(btns, text="Start", command=start, width=10)
        start_btn.grid(row=0, column=0, padx=5)

        stop_btn = ttk.Button(btns, text="Stop", command=stop, width=10)
        stop_btn.grid(row=0, column=1, padx=5)

        reset_btn = ttk.Button(btns, text="Reset", command=reset, width=10)
        reset_btn.grid(row=0, column=2, padx=5)

        comment_row = ttk.Frame(sw_frame)
        comment_row.pack(fill="x", pady=(10, 0))

        ttk.Label(comment_row, text="Comment:").pack(side="left", padx=(0, 8))

        comment_var = tk.StringVar(value="")
        comment_entry = ttk.Entry(comment_row, textvariable=comment_var)
        comment_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        add_btn = ttk.Button(comment_row, text="Add to Log", width=12, state="disabled")
        add_btn.pack(side="left")

        def _sync_add_state(*_args):
            add_btn.config(state="normal" if comment_var.get().strip() else "disabled")

        def add_comment_to_log(_event=None):
            comment = (comment_var.get() or "").strip()
            if not comment:
                return
            self.log_button_press(comment)
            comment_var.set("")
            _sync_add_state()

        comment_var.trace_add("write", _sync_add_state)
        add_btn.config(command=add_comment_to_log)
        comment_entry.bind("<Return>", add_comment_to_log)
        _sync_add_state()

        def on_destroy(_event=None):
            cancel_loop()

        parent.bind("<Destroy>", on_destroy, add="+")

    # ---------------------- Exercise recommendations UI ----------------------
    def show_exercise_recommendations(self, is_timer_reminder: bool = True) -> None:
        rec_window = tk.Toplevel(self.root)
        rec_window.title(
            "⏰ Time to Exercise! - Recommendations ⏰" if is_timer_reminder
            else "Exercise Recommendations"
        )
        rec_window.geometry("700x620")
        rec_window.resizable(True, True)
        rec_window.transient(self.root)
        rec_window.grab_set()

        rec_window.update_idletasks()
        x = (rec_window.winfo_screenwidth() // 2) - (700 // 2)
        y = (rec_window.winfo_screenheight() // 2) - (620 // 2)
        rec_window.geometry(f"700x620+{x}+{y}")
        rec_window.attributes("-topmost", True)

        if is_timer_reminder:
            rec_window.protocol("WM_DELETE_WINDOW", lambda: self.handle_reminder_response(rec_window, close_button=True))

        main_frame = ttk.Frame(rec_window, padding="20")
        main_frame.pack(expand=True, fill="both")

        # ---- Header & controls ----
        if is_timer_reminder:
            title_label = ttk.Label(main_frame, text="⏰ Time to Exercise! ⏰", font=("Arial", 16, "bold"))
            title_label.pack(pady=(0, 10))
            control_frame = ttk.Frame(main_frame)
            control_frame.pack(pady=(0, 10))

            snooze_btn = ttk.Button(
                control_frame, text="Snooze (5 min)",
                command=lambda: self.handle_reminder_response(rec_window, snooze=True),
                width=15, padding=(15, 10),
            )
            snooze_btn.pack(side="left", padx=10)

            done_btn = ttk.Button(
                control_frame, text="Done",
                command=lambda: self.handle_reminder_response(rec_window, snooze=False),
                width=15, padding=(15, 10),
            )
            done_btn.pack(side="left", padx=10)

            pomodoro_btn = ttk.Button(
                control_frame, text="Pomodoro",
                command=lambda: self.open_pomodoro_window(rec_window),
                width=15, padding=(15, 10),
            )
            pomodoro_btn.pack(side="left", padx=10)

            self._add_stopwatch_to_window(rec_window, main_frame)

        else:
            title_label = ttk.Label(main_frame, text="💪 Exercise Recommendations 💪", font=("Arial", 16, "bold"))
            title_label.pack(pady=(0, 15))

        # ---- Focus indicator in the popup ----
        focus = self.focus_category
        if focus and focus in CATEGORIES:
            cat_name, cat_color = CATEGORIES[focus]
            focus_banner = tk.Label(
                main_frame,
                text=f"🎯 Today's Focus: {cat_name}",
                font=("Arial", 10, "bold"),
                bg=cat_color,
                anchor="w",
                padx=10,
                pady=4,
            )
            focus_banner.pack(fill="x", pady=(0, 6))

        # ---- Instruction subtitle ----
        if not self.exercises:
            error_label = ttk.Label(
                main_frame,
                text="No exercises found.\nPlease check the Exercise_Reminder.csv file.",
                font=("Arial", 12), justify="center", foreground="red",
            )
            error_label.pack(pady=50)
            if is_timer_reminder:
                rec_window.bell()
                rec_window.focus_force()
            return

        subtitle_label = ttk.Label(
            main_frame,
            text="Click any exercise to get started (* = link, ▶ = video):",
            font=("Arial", 10),
        )
        subtitle_label.pack(pady=(0, 8))

        # ---- Scrollable exercise grid ----
        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        def on_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        scrollable_frame.bind("<Configure>", on_configure)
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ---- Build ordered exercise lists ----
        if focus and focus in CATEGORIES:
            focus_exercises = [e for e in self.exercises if focus in self.exercise_categories.get(e, [])]
            other_exercises = [e for e in self.exercises if focus not in self.exercise_categories.get(e, [])]
            random.shuffle(focus_exercises)
            random.shuffle(other_exercises)
        else:
            focus_exercises = []
            other_exercises = self.exercises.copy()
            random.shuffle(other_exercises)

        buttons_per_row = 3

        # Helper: create one exercise button at a given (row, col) in the grid
        def make_exercise_button(exercise: str, grid_row: int, col: int) -> None:
            self.exercise_click_counts.setdefault(exercise, 0)
            bg_color = self.get_button_color(exercise)
            text_color = self.get_text_color(exercise)
            if exercise in self.exercise_urls:
                button_text = f"{exercise} *"
            elif exercise in self.exercise_videos:
                button_text = f"{exercise} ▶"
            else:
                button_text = exercise
            btn = tk.Button(
                scrollable_frame,
                text=button_text,
                command=lambda ex=exercise: self.handle_exercise_click(ex, scrollable_frame),
                width=22,
                height=2,
                bg=bg_color,
                fg=text_color,
                cursor="hand2",
                relief="raised",
                bd=2,
                wraplength=160,
                font=("Arial", 9),
            )
            btn.grid(row=grid_row, column=col, padx=8, pady=8, sticky="ew")
            scrollable_frame.grid_columnconfigure(col, weight=1)

        current_row = 0

        # ---- Focus section ----
        if focus_exercises:
            cat_name, cat_color = CATEGORIES[focus]

            # Section header spanning all columns
            header_frame = tk.Frame(scrollable_frame, bg=cat_color)
            header_frame.grid(row=current_row, column=0, columnspan=buttons_per_row,
                               sticky="ew", padx=4, pady=(6, 2))
            tk.Label(
                header_frame,
                text=f"🎯 Focus Exercises — {cat_name}",
                font=("Arial", 10, "bold"),
                bg=cat_color,
                anchor="w",
            ).pack(padx=8, pady=3)
            current_row += 1

            for i, exercise in enumerate(focus_exercises):
                make_exercise_button(exercise, current_row + i // buttons_per_row, i % buttons_per_row)
            current_row += (len(focus_exercises) + buttons_per_row - 1) // buttons_per_row

            # Visual separator
            sep_frame = tk.Frame(scrollable_frame, height=2, bg="#AAAAAA")
            sep_frame.grid(row=current_row, column=0, columnspan=buttons_per_row,
                           sticky="ew", padx=8, pady=(6, 2))
            current_row += 1

            # "Other" sub-heading
            other_label = tk.Label(
                scrollable_frame,
                text="Other Exercises",
                font=("Arial", 9, "italic"),
                fg="#666666",
                anchor="w",
            )
            other_label.grid(row=current_row, column=0, columnspan=buttons_per_row,
                             sticky="ew", padx=14, pady=(2, 4))
            current_row += 1

        # ---- Other (or all) exercises ----
        for i, exercise in enumerate(other_exercises):
            make_exercise_button(exercise, current_row + i // buttons_per_row, i % buttons_per_row)

        # ---- Mouse wheel scrolling ----
        def _on_mousewheel(event):
            delta = int(-1 * (event.delta / 120)) if event.delta else -1 if event.num == 5 else 1
            canvas.yview_scroll(delta, "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)   # Linux scroll up
        canvas.bind_all("<Button-5>", _on_mousewheel)   # Linux scroll down

        # Bell + focus for reminders
        if is_timer_reminder:
            rec_window.bell()
            rec_window.focus_force()


def main() -> None:
    root = tk.Tk()
    app = ExerciseReminder(root)
    root.protocol("WM_DELETE_WINDOW", app.quit_app)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.quit_app()


if __name__ == "__main__":
    main()
