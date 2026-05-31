import tkinter as tk
from tkinter import ttk, messagebox
import cv2
import numpy as np
import mss
import ctypes
from ctypes import windll, create_unicode_buffer
from PIL import Image, ImageTk
import time
import json
import os
import random
from threading import Lock, Thread
import win32gui
import win32con
import win32api
from pynput import keyboard as pynput_keyboard

# DPI awareness
try:
    windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        windll.user32.SetDPIAware()
    except Exception:
        pass


# ============================================================
#  MEMORY PATTERN RANDOMIZATION — No static strings
#  Each run uses different XOR key → memory signature changes
#  Strings added individually, each temporary → no simultaneous plaintext
# ============================================================
class _X:
    """
    Runtime string vault.
    - Per-session random XOR key (os.urandom derived)
    - Strings stored as encoded byte arrays only
    - Plaintext exists only momentarily during g() calls
    - Decoy entries pollute pattern analysis
    """
    _k = 0
    _e = {}

    @classmethod
    def init(cls):
        cls._k = int.from_bytes(os.urandom(2), 'big') | 1

    @classmethod
    def add(cls, name, text):
        if not cls._k:
            cls.init()
        b = text.encode('utf-8')
        cls._e[name] = bytes(
            b1 ^ ((cls._k + i * 7) & 0xFF) for i, b1 in enumerate(b)
        )

    @classmethod
    def g(cls, name):
        if name not in cls._e:
            return name
        data = cls._e[name]
        return bytes(
            b1 ^ ((cls._k + i * 7) & 0xFF) for i, b1 in enumerate(data)
        ).decode('utf-8')

    @classmethod
    def noise(cls, n=24):
        """Decoy entries — random noise to confuse pattern scanners"""
        for i in range(n):
            cls._e[f'_{i}_{random.randint(0,0xFFFF):x}'] = os.urandom(
                random.randint(8, 40)
            )


_X.init()

# --- Add each string individually (temporary plaintext GC'd immediately) ---
_X.add('title',       'Virtual Scope - FPS Mode')
_X.add('config',      'scope_config.json')
_X.add('header',      '🎯 FPS Scope - Enemy Detection')
_X.add('hint',        '(V key toggle, F1-F3 filters, +/- zoom)')
_X.add('size_lf',     ' Scope Size (px) ')
_X.add('size',        'Size:')
_X.add('px',          'px')
_X.add('zoom_lf',     ' Zoom ')
_X.add('filter_lf',   ' Enemy Detection Filters ')
_X.add('key_lf',      ' Key Binding ')
_X.add('info_lf',     ' Filter Descriptions ')
_X.add('focus_lf',    ' Game Window ')
_X.add('focus_hint',  'Window name (empty = always active):')
_X.add('focus_det',   '📸 Detect')
_X.add('save_btn',    '💾 Save')
_X.add('reset_btn',   '↺ Reset')
_X.add('exit_btn',    '🚪 Exit')
_X.add('status_init', 'Status: Initializing...')
_X.add('font_ui',     'Segoe UI')
_X.add('font_mono',   'Consolas')
_X.add('scope_key',   'Scope Toggle:')
_X.add('fn_key',      'Normal (F1):')
_X.add('fi_key',      'Invert (F2):')
_X.add('fc_key',      'Canny Light (F3):')
_X.add('fm_key',      'Motion Detection (F4):')
_X.add('zu_key',      'Zoom Increase (+):')
_X.add('zd_key',      'Zoom Decrease (-):')
_X.add('f_normal',    '0 Normal (Original)')
_X.add('f_invert',    '1 Invert (Night Vision)')
_X.add('f_canny',     '2 Canny Light (Edge)')
_X.add('f_motion',    '3 Motion Detection')
_X.add('info_txt',
       '• NORMAL: Standard image\n'
       '• INVERT: Inverted colors, night vision\n'
       '• CANNY: Sharp edge detection\n'
       '• MOTION: Detect and highlight moving objects')
_X.add('small',       'Small (120)')
_X.add('medium',      'Medium (200)')
_X.add('normal_s',    'Normal (300)')
_X.add('large',       'Large (450)')
_X.add('change',      'Change')
_X.add('hk_ok',       '[Hotkeys] Registered.')
_X.add('cfg_err',     '[Config Error] ')
_X.add('cfg_sv_err',  '[Config Save Error] ')
_X.add('aff_err',     '[Affinity Error] ')
_X.add('open_err',    '[Open Error] ')
_X.add('close_err',   '[Close Error] ')
_X.add('rend_err',    '[Render Error] ')
_X.add('kcap_err',    '[Key Capture Error] ')
_X.add('warn_t',      'Warning')
_X.add('warn_m',
       'Cannot change keys while system is active!\n'
       'Exit first (X button), then change.')
_X.add('kw_t',        'Waiting for Key...')
_X.add('kw_m',        'Press the key you want to assign...')
_X.add('kw_esc',      '(ESC = Cancel)')
_X.add('conflict_t',  'Conflict')
_X.add('saved_t',     'Saved')
_X.add('saved_m',     'Key bindings saved successfully.')
_X.add('reset_t',     'Reset')
_X.add('reset_m',     'All keys will be reset to defaults. Are you sure?')
_X.add('hk_err_t',    'Hotkey Error')
_X.add('hk_err_m',    'Error registering key:\n')
_X.add('foc_on',      'ACTIVE')
_X.add('foc_off',     'INACTIVE')
_X.add('foc_wait',    'WAITING')
_X.add('foc_always',  '● Always active')
_X.add('foc_tgt',     '● Target: ')
_X.add('foc_miss',    '● Window not detected')
_X.add('foc_yes',     '● In focus: ')
_X.add('foc_no',      '● Waiting for: ')
_X.add('foc_return',  'Return to game window')
_X.add('foc_open',    ' to open scope')

# Add noise — disrupts pattern analysis
_X.noise(30)

# Short access alias
_T = _X.g


# ============================================================
#  SAFE HOTKEY HANDLER - Uses pynput instead of keyboard module
#  pynput has lower VAC detection risk as it uses standard Windows hooks
# ============================================================
class SafeHotkeyManager:
    def __init__(self):
        self.listener = None
        self.hotkeys = {}
        self.running = False
        self.listener_thread = None
        
    def add_hotkey(self, key_combo, callback):
        """Add a hotkey (single key for simplicity)"""
        self.hotkeys[key_combo.lower()] = callback
        
    def remove_hotkey(self, key_combo):
        """Remove a hotkey"""
        if key_combo.lower() in self.hotkeys:
            del self.hotkeys[key_combo.lower()]
            
    def clear_hotkeys(self):
        """Clear all hotkeys"""
        self.hotkeys.clear()
        
    def _on_press(self, key):
        """Handle key press events"""
        try:
            # Handle regular keys
            if hasattr(key, 'char') and key.char:
                key_name = key.char.lower()
            else:
                # Handle special keys
                key_name = str(key).replace('Key.', '').lower()
                
            if key_name in self.hotkeys:
                callback = self.hotkeys[key_name]
                # Execute callback in a separate thread to avoid blocking
                Thread(target=callback, daemon=True).start()
        except Exception:
            pass
        return True  # Allow other handlers to process
        
    def start(self):
        """Start the keyboard listener"""
        if not self.running:
            self.running = True
            self.listener = pynput_keyboard.Listener(on_press=self._on_press)
            self.listener_thread = Thread(target=self.listener.run, daemon=True)
            self.listener_thread.start()
            
    def stop(self):
        """Stop the keyboard listener"""
        self.running = False
        if self.listener:
            self.listener.stop()
            self.listener = None


# ============================================================
#  GAME WINDOW FOCUS DETECTION — Win32 API
#  Focus change checking instead of constant polling
# ============================================================
def _get_fg_title():
    """Get foreground window title"""
    hwnd = windll.user32.GetForegroundWindow()
    if not hwnd:
        return ''
    buf = create_unicode_buffer(512)
    windll.user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _is_target_focused(target):
    """Is target window in focus? Empty target = always True"""
    if not target or not target.strip():
        return True
    fg = _get_fg_title()
    return target.lower() in fg.lower()


# ============================================================
#  MAIN APP
# ============================================================
class VirtualScopeApp:
    def __init__(self, root):
        self.root = root
        self.root.title(_T('title'))
        self.root.geometry("480x1050")
        self.root.resizable(False, False)

        # Default key bindings
        self.default_keys = {
            "scope": "v",
            "filter_normal": "f1",
            "filter_invert": "f2",
            "filter_canny_light": "f3",
            "filter_motion": "f4",
            "zoom_up": "+",
            "zoom_down": "-"
        }
        self.keys = dict(self.default_keys)
        self._load_config()

        # Settings
        self.zoom_factor = tk.IntVar(value=1)
        self.filter_mode = tk.IntVar(value=0)
        self.scope_size = 225
        self.scope_size_var = tk.IntVar(value=self.scope_size)
        self.is_active = False
        self.scope_visible = False

        # --- Game window focus detection ---
        self.game_window_title = tk.StringVar(value='')
        self.game_focused = True
        self._focus_hid_overlay = False
        self.last_focus_check = 0.0
        self.focus_check_interval = 0.15  # Check every 150ms

        # FPS Optimization
        self.target_fps = 60
        self.frame_interval = 1.0 / self.target_fps
        self.last_frame_time = 0.0
        self.fps_counter = 0
        self.fps_timer = time.perf_counter()

        # Render lock
        self.render_lock = Lock()
        self.last_render_time = 0
        self.min_render_interval = 1.0 / 60

        # Caching
        self.cached_mask = None
        self.cached_size = None
        self.last_zoom = None
        self.last_filter = None

        # Screen
        self.screen_w = windll.user32.GetSystemMetrics(0)
        self.screen_h = windll.user32.GetSystemMetrics(1)
        self.cx = self.screen_w // 2
        self.cy = self.screen_h // 2

        # Overlay
        self.overlay = None
        self.canvas = None
        self.canvas_img_id = None
        self.photo_image = None

        # MSS
        self.sct = mss.mss()

        # Pre-allocated buffer for performance
        self.frame_buffer = None

        # Previous frame (for motion detection)
        self.prev_frame = None

        # Mask (square)
        self._update_mask()

        # Safe hotkey manager (replaces keyboard module)
        self.hotkey_manager = SafeHotkeyManager()

        # UI
        self._build_ui()

        # Window close handler
        self.root.protocol("WM_DELETE_WINDOW", self._stop)

        # Auto start
        self.root.after(100, self._start)

    # ============================================================
    #  CONFIG
    # ============================================================
    def _load_config(self):
        cfg = _T('config')
        if os.path.exists(cfg):
            try:
                with open(cfg, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k in self.default_keys:
                        if k in data and data[k]:
                            self.keys[k] = data[k].lower()
                    if 'game_window' in data and data['game_window']:
                        self.game_window_title.set(data['game_window'])
            except Exception as e:
                print(f"{_T('cfg_err')}{e}")

    def _save_config(self):
        cfg = _T('config')
        try:
            data = dict(self.keys)
            data['game_window'] = self.game_window_title.get()
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"{_T('cfg_sv_err')}{e}")

    # ============================================================
    #  UI
    # ============================================================
    def _build_ui(self):
        ttk.Label(self.root, text=_T('header'),
                  font=(_T('font_ui'), 15, "bold")).pack(pady=8)

        ttk.Label(self.root,
                  text=_T('hint'),
                  font=(_T('font_ui'), 8, "italic"),
                  foreground="gray").pack()

        # ─── Game Window Focus Detection ───
        f_focus = ttk.LabelFrame(self.root, text=_T('focus_lf'))
        f_focus.pack(pady=5, padx=15, fill="x")

        ttk.Label(f_focus, text=_T('focus_hint'),
                  font=(_T('font_ui'), 8)).pack(anchor="w", padx=8, pady=(5, 0))

        focus_row = ttk.Frame(f_focus)
        focus_row.pack(fill="x", padx=8, pady=5)

        self.focus_entry = ttk.Entry(focus_row, width=28,
                                     font=(_T('font_mono'), 9))
        self.focus_entry.pack(side="left", padx=(0, 5))
        self.focus_entry.insert(0, self.game_window_title.get())

        ttk.Button(focus_row, text=_T('focus_det'), width=10,
                   command=self._detect_game_window).pack(side="left", padx=2)

        self.focus_status_label = ttk.Label(
            f_focus, text=_T('foc_always'),
            font=(_T('font_ui'), 8, "italic"),
            foreground="#0078D4")
        self.focus_status_label.pack(anchor="w", padx=8, pady=(0, 5))

        # ─── Scope Size ───
        f_size = ttk.LabelFrame(self.root, text=_T('size_lf'))
        f_size.pack(pady=5, padx=15, fill="x")

        size_top = ttk.Frame(f_size)
        size_top.pack(fill="x", padx=8, pady=(5, 0))

        ttk.Label(size_top, text=_T('size'),
                  font=(_T('font_ui'), 9, "bold")).pack(side="left")

        self.size_label = ttk.Label(
            size_top, text=f"{self.scope_size}{_T('px')}",
            font=(_T('font_mono'), 11, "bold"),
            foreground="#0078D4")
        self.size_label.pack(side="right")

        self.size_slider = ttk.Scale(f_size, from_=80, to=600,
                                     orient="horizontal",
                                     variable=self.scope_size_var,
                                     command=self._on_size_slider_change)
        self.size_slider.pack(fill="x", padx=8, pady=(2, 0))

        size_presets = ttk.Frame(f_size)
        size_presets.pack(fill="x", padx=8, pady=(4, 8))

        for lk, val in [('small', 120), ('medium', 200),
                        ('normal_s', 300), ('large', 450)]:
            ttk.Button(size_presets, text=_T(lk), width=14,
                       command=lambda v=val: self._set_scope_size(v)
                       ).pack(side="left", padx=2, expand=True)

        # ─── Zoom ───
        f1 = ttk.LabelFrame(self.root, text=_T('zoom_lf'))
        f1.pack(pady=5, padx=15, fill="x")
        for t, v in [("1x", 1), ("2x", 2), ("3x", 3), ("4x", 4), ("6x", 6)]:
            ttk.Radiobutton(f1, text=t, variable=self.zoom_factor,
                            value=v).pack(side="left", expand=True, pady=5)

        # ─── Filters ───
        f2 = ttk.LabelFrame(self.root, text=_T('filter_lf'))
        f2.pack(pady=5, padx=15, fill="x")

        filter_frame = ttk.Frame(f2)
        filter_frame.pack(fill="x", padx=5, pady=5)

        for fk, val in [('f_normal', 0), ('f_invert', 1),
                        ('f_canny', 2), ('f_motion', 3)]:
            ttk.Radiobutton(filter_frame, text=_T(fk),
                            variable=self.filter_mode,
                            value=val).pack(anchor="w", pady=3)

        # ─── Key Binding ───
        key_frame = ttk.LabelFrame(self.root, text=_T('key_lf'))
        key_frame.pack(pady=8, padx=15, fill="x")

        key_info = [
            ("scope",              'scope_key'),
            ("filter_normal",      'fn_key'),
            ("filter_invert",      'fi_key'),
            ("filter_canny_light", 'fc_key'),
            ("filter_motion",      'fm_key'),
            ("zoom_up",            'zu_key'),
            ("zoom_down",          'zd_key'),
        ]

        self.key_entries = {}

        for key_id, lbl_key in key_info:
            row = ttk.Frame(key_frame)
            row.pack(fill="x", padx=8, pady=2)

            ttk.Label(row, text=_T(lbl_key), width=18,
                      anchor="w").pack(side="left")

            entry = ttk.Entry(row, width=14, justify="center",
                              font=(_T('font_mono'), 10, "bold"))
            entry.insert(0, self.keys[key_id].upper())
            entry.configure(state="readonly")
            entry.pack(side="left", padx=5)

            ttk.Button(row, text=_T('change'), width=10,
                       command=lambda k=key_id: self._capture_key(k)
                       ).pack(side="left", padx=2)

            self.key_entries[key_id] = entry

        # ─── Info ───
        info_frame = ttk.LabelFrame(self.root, text=_T('info_lf'))
        info_frame.pack(pady=5, padx=15, fill="both", expand=True)

        info_text = tk.Text(info_frame, height=6, width=50,
                            font=(_T('font_mono'), 8))
        info_text.pack(padx=5, pady=5)
        info_text.insert("1.0", _T('info_txt'))
        info_text.configure(state="disabled")

        # ─── Button Bar ───
        btn_bar = ttk.Frame(self.root)
        btn_bar.pack(pady=8)

        ttk.Button(btn_bar, text=_T('save_btn'),
                   command=self._save_keys).pack(side="left", padx=4)
        ttk.Button(btn_bar, text=_T('reset_btn'),
                   command=self._reset_keys).pack(side="left", padx=4)
        ttk.Button(btn_bar, text=_T('exit_btn'),
                   command=self._stop).pack(side="left", padx=4)

        # Status
        self.status = tk.StringVar(value=_T('status_init'))
        ttk.Label(self.root, textvariable=self.status,
                  foreground="gray",
                  font=(_T('font_ui'), 9)).pack(pady=4)

    # ============================================================
    #  GAME WINDOW DETECTION
    # ============================================================
    def _detect_game_window(self):
        """Capture current foreground window as target"""
        title = _get_fg_title()
        if title:
            self.focus_entry.delete(0, tk.END)
            self.focus_entry.insert(0, title)
            self.game_window_title.set(title)
            self.focus_status_label.config(
                text=f"{_T('foc_tgt')}{title[:40]}",
                foreground="#00A000")
        else:
            self.focus_status_label.config(
                text=_T('foc_miss'),
                foreground="#C00000")

    def _update_focus_state(self):
        """
        Update focus state — no constant polling.
        Only calls Win32 API at focus_check_interval.
        Automatically hide/show overlay when focus changes.
        """
        now = time.perf_counter()
        if now - self.last_focus_check < self.focus_check_interval:
            return self.game_focused

        self.last_focus_check = now
        target = self.game_window_title.get()

        if not target or not target.strip():
            self.game_focused = True
            self.focus_status_label.config(
                text=_T('foc_always'),
                foreground="#0078D4")
            return True

        was_focused = self.game_focused
        self.game_focused = _is_target_focused(target)

        # Auto hide/show overlay
        if was_focused and not self.game_focused and self.scope_visible:
            self._focus_hid_overlay = True
            try:
                self.overlay.withdraw()
            except Exception:
                pass
        elif not was_focused and self.game_focused and self._focus_hid_overlay:
            self._focus_hid_overlay = False
            try:
                self.overlay.deiconify()
                self.overlay.lift()
            except Exception:
                pass

        # Update status label
        if self.game_focused:
            self.focus_status_label.config(
                text=f"{_T('foc_yes')}{target[:30]}",
                foreground="#00A000")
        else:
            self.focus_status_label.config(
                text=f"{_T('foc_no')}{target[:30]}",
                foreground="#C00000")
            if was_focused:
                self.prev_frame = None

        return self.game_focused

    # ============================================================
    #  SCOPE SIZE
    # ============================================================
    def _on_size_slider_change(self, value_str):
        val = int(float(value_str))
        val = max(80, min(600, val))
        self.size_label.config(text=f"{val}{_T('px')}")
        if val != self.scope_size:
            self._apply_scope_size(val)

    def _set_scope_size(self, val):
        self.scope_size_var.set(val)
        self.size_slider.set(val)
        self.size_label.config(text=f"{val}{_T('px')}")
        if val != self.scope_size:
            self._apply_scope_size(val)

    def _apply_scope_size(self, new_size):
        was_visible = self.scope_visible

        if self.overlay:
            try:
                self.overlay.withdraw()
                self.overlay.destroy()
            except Exception:
                pass
            self.overlay = None
            self.canvas = None

        self.scope_size = new_size
        self._update_mask()

        if self.is_active:
            self._create_overlay()
            if was_visible:
                self.scope_visible = True
                try:
                    self.overlay.deiconify()
                    self.overlay.lift()
                except Exception:
                    pass

    # ============================================================
    #  KEY CAPTURE - Using safer method without low-level hooks
    # ============================================================
    def _capture_key(self, key_id):
        if self.is_active:
            messagebox.showwarning(_T('warn_t'), _T('warn_m'))
            return

        capture_win = tk.Toplevel(self.root)
        capture_win.title(_T('kw_t'))
        capture_win.geometry("320x150")
        capture_win.resizable(False, False)
        capture_win.transient(self.root)
        capture_win.grab_set()

        capture_win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 320) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 150) // 2
        capture_win.geometry(f"+{x}+{y}")

        ttk.Label(capture_win, text="🎹",
                  font=(_T('font_ui'), 30)).pack(pady=5)
        ttk.Label(capture_win, text=_T('kw_m'),
                  font=(_T('font_ui'), 10)).pack()
        ttk.Label(capture_win, text=_T('kw_esc'),
                  font=(_T('font_ui'), 8), foreground="gray").pack(pady=2)

        # Use tkinter's key binding for capture (safe)
        def on_key(event):
            pressed = event.keysym.lower()
            
            if pressed == "escape":
                capture_win.destroy()
                return

            # Check for conflicts
            for other_id, other_key in self.keys.items():
                if other_id != key_id and other_key == pressed:
                    messagebox.showerror(
                        _T('conflict_t'),
                        f"Key '{pressed.upper()}' is already "
                        f"used for '{other_id}'!"
                    )
                    capture_win.destroy()
                    return

            self.keys[key_id] = pressed
            self._update_key_entry(key_id)
            capture_win.destroy()

        capture_win.bind('<Key>', on_key)
        capture_win.focus_set()

    def _update_key_entry(self, key_id):
        entry = self.key_entries[key_id]
        entry.configure(state="normal")
        entry.delete(0, tk.END)
        entry.insert(0, self.keys[key_id].upper())
        entry.configure(state="readonly")

    def _save_keys(self):
        self.game_window_title.set(self.focus_entry.get())
        self._save_config()
        messagebox.showinfo(_T('saved_t'), _T('saved_m'))

    def _reset_keys(self):
        if messagebox.askyesno(_T('reset_t'), _T('reset_m')):
            self.keys = dict(self.default_keys)
            for k in self.keys:
                self._update_key_entry(k)
            self.game_window_title.set('')
            self.focus_entry.delete(0, tk.END)
            self._save_config()

    # ============================================================
    #  MASK
    # ============================================================
    def _update_mask(self):
        size = self.scope_size
        mask = np.ones((size, size), dtype=np.uint8) * 255
        self.scope_mask = mask
        self.cached_mask = mask
        self.cached_size = size

    # ============================================================
    #  OVERLAY
    # ============================================================
    def _create_overlay(self):
        if self.overlay:
            try:
                self.overlay.destroy()
            except Exception:
                pass

        self.overlay = tk.Toplevel()
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)

        x = self.cx - self.scope_size // 2
        y = self.cy - self.scope_size // 2
        self.overlay.geometry(f"{self.scope_size}x{self.scope_size}+{x}+{y}")

        self.canvas = tk.Canvas(self.overlay, width=self.scope_size,
                                height=self.scope_size, bg="black",
                                highlightthickness=0)
        self.canvas.pack()

        self._blank = ImageTk.PhotoImage(
            Image.new("RGB", (self.scope_size, self.scope_size), (0, 0, 0)))
        self.canvas_img_id = self.canvas.create_image(
            0, 0, anchor="nw", image=self._blank)

        self.overlay.update_idletasks()

        hwnd = windll.user32.GetParent(self.overlay.winfo_id())
        if not hwnd:
            hwnd = self.overlay.winfo_id()

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080

        style = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
        )
        windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, 0x02)

        try:
            r = windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
            if not r:
                windll.user32.SetWindowDisplayAffinity(hwnd, 0x01)
        except Exception as e:
            print(f"{_T('aff_err')}{e}")

        self.overlay.withdraw()

    # ============================================================
    #  TOGGLE
    # ============================================================
    def _toggle_scope(self):
        self.scope_visible = not self.scope_visible
        if self.scope_visible:
            try:
                self.overlay.deiconify()
                self.overlay.lift()
            except Exception as e:
                print(f"{_T('open_err')}{e}")
        else:
            self._focus_hid_overlay = False
            try:
                self.overlay.withdraw()
            except Exception as e:
                print(f"{_T('close_err')}{e}")

    # ============================================================
    #  HOTKEYS - Using safe pynput manager
    # ============================================================
    def _register_hotkeys(self):
        self._unregister_hotkeys()
        try:
            self.hotkey_manager.add_hotkey(self.keys["scope"], self._toggle_scope)
            self.hotkey_manager.add_hotkey(self.keys["filter_normal"],
                                    lambda: self.filter_mode.set(0))
            self.hotkey_manager.add_hotkey(self.keys["filter_invert"],
                                    lambda: self.filter_mode.set(1))
            self.hotkey_manager.add_hotkey(self.keys["filter_canny_light"],
                                    lambda: self.filter_mode.set(2))
            self.hotkey_manager.add_hotkey(self.keys["filter_motion"],
                                    lambda: self.filter_mode.set(3))
            self.hotkey_manager.add_hotkey(self.keys["zoom_up"], self._zoom_up)
            self.hotkey_manager.add_hotkey(self.keys["zoom_down"], self._zoom_down)
            
            self.hotkey_manager.start()
            print(_T('hk_ok'))
        except Exception as e:
            messagebox.showerror(_T('hk_err_t'), f"{_T('hk_err_m')}{e}")

    def _unregister_hotkeys(self):
        self.hotkey_manager.stop()
        self.hotkey_manager.clear_hotkeys()

    def _zoom_up(self):
        v = self.zoom_factor.get()
        if v < 10:
            self.zoom_factor.set(v + 1)

    def _zoom_down(self):
        v = self.zoom_factor.get()
        if v > 1:
            self.zoom_factor.set(v - 1)

    # ============================================================
    #  START / STOP
    # ============================================================
    def _start(self):
        if self.is_active:
            return
        self.game_window_title.set(self.focus_entry.get())
        self._create_overlay()
        self._register_hotkeys()
        self.is_active = True
        self.status.set(
            f"Active | Press '{self.keys['scope'].upper()}' to toggle scope")
        self._toggle_scope()
        self._loop()

    def _stop(self):
        if not self.is_active:
            self.root.destroy()
            return
        self.is_active = False
        self.scope_visible = False
        self._unregister_hotkeys()
        try:
            self.overlay.destroy()
        except Exception:
            pass
        self.root.destroy()

    # ============================================================
    #  MAIN LOOP — Focus-driven, no constant polling
    #  ├─ Game in focus + scope open → full FPS render
    #  ├─ Game not in focus → slow polling, no render
    #  └─ Scope closed → slow polling
    # ============================================================
    def _loop(self):
        if not self.is_active:
            return

        now = time.perf_counter()
        focused = self._update_focus_state()

        if self.scope_visible and focused:
            # ── Active render: target FPS ──
            if now - self.last_frame_time >= self.frame_interval:
                self.last_frame_time = now
                self._render()
                self.fps_counter += 1
                if now - self.fps_timer >= 1.0:
                    self.status.set(
                        f"{_T('foc_on')} | {self.fps_counter} FPS | "
                        f"Zoom: {self.zoom_factor.get()}x | "
                        f"Size: {self.scope_size}{_T('px')}")
                    self.fps_counter = 0
                    self.fps_timer = now
            self.root.after(1, self._loop)

        elif self.scope_visible and not focused:
            # ── Waiting for focus → slow polling, save CPU ──
            if now - self.fps_timer >= 1.0:
                self.status.set(
                    f"{_T('foc_wait')} | {_T('foc_return')} | "
                    f"Size: {self.scope_size}{_T('px')}")
                self.fps_timer = now
            self.root.after(50, self._loop)

        else:
            # ── Scope closed → slow polling ──
            if now - self.fps_timer >= 1.0:
                self.status.set(
                    f"{_T('foc_off')} | "
                    f"Press '{self.keys['scope'].upper()}'"
                    f"{_T('foc_open')} | "
                    f"Size: {self.scope_size}{_T('px')}")
                self.fps_timer = now
            self.root.after(50, self._loop)

    # ============================================================
    #  RENDER
    # ============================================================
    def _render(self):
        zoom = self.zoom_factor.get()
        sz = self.scope_size

        cap_w = max(4, sz // zoom)
        cap_h = max(4, sz // zoom)

        left = max(0, min(self.cx - cap_w // 2, self.screen_w - cap_w))
        top = max(0, min(self.cy - cap_h // 2, self.screen_h - cap_h))

        monitor = {"top": top, "left": left,
                   "width": cap_w, "height": cap_h}

        try:
            shot = self.sct.grab(monitor)
            arr = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(
                cap_h, cap_w, 3)
            arr = arr.copy()

            mode = self.filter_mode.get()

            if mode == 0:
                pass

            elif mode == 1:
                arr = cv2.bitwise_not(arr)

            elif mode == 2:
                gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
                blurred = cv2.GaussianBlur(gray, (3, 3), 0)
                edges = cv2.Canny(blurred, 29, 32)
                arr = cv2.cvtColor(edges, cv2.COLOR_GRAY2RGB)

            elif mode == 3:
                gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
                gray = cv2.GaussianBlur(gray, (5, 5), 0)

                if self.prev_frame is not None:
                    diff = cv2.absdiff(self.prev_frame, gray)
                    threshold = cv2.threshold(
                        diff, 30, 255, cv2.THRESH_BINARY)[1]

                    kernel = cv2.getStructuringElement(
                        cv2.MORPH_ELLIPSE, (3, 3))
                    threshold = cv2.dilate(
                        threshold, kernel, iterations=1)

                    motion_rgb = np.zeros_like(arr)
                    motion_rgb[:, :, 0] = 255
                    motion_rgb[:, :, 1] = 0
                    motion_rgb[:, :, 2] = 0

                    threshold_3ch = cv2.cvtColor(
                        threshold, cv2.COLOR_GRAY2RGB)
                    arr = np.where(threshold_3ch > 127, motion_rgb, arr)

                self.prev_frame = gray.copy()

            # Resize
            arr = cv2.resize(arr, (sz, sz),
                             interpolation=cv2.INTER_NEAREST)

            # Reticle
            c = sz // 2
            gap = max(8, sz // 12)

            reticle_colors = {
                0: (0, 255, 0),
                1: (255, 255, 0),
                2: (0, 255, 0),
                3: (255, 0, 0),
            }
            col = reticle_colors.get(mode, (0, 255, 0))

            arr[c, c - gap:c + gap] = col
            arr[c - gap:c + gap, c] = col
            arr[c - 1:c + 2, c - 1:c + 2] = (255, 100, 100)

            cv2.rectangle(arr, (2, 2), (sz - 2, sz - 2),
                          (200, 200, 200), 2)

            corner_size = max(8, sz // 20)
            cv2.rectangle(arr, (0, 0),
                          (corner_size, corner_size), col, 1)
            cv2.rectangle(arr, (sz - corner_size, 0),
                          (sz, corner_size), col, 1)
            cv2.rectangle(arr, (0, sz - corner_size),
                          (corner_size, sz), col, 1)
            cv2.rectangle(arr, (sz - corner_size, sz - corner_size),
                          (sz, sz), col, 1)

            img = Image.fromarray(arr)
            self.photo_image = ImageTk.PhotoImage(image=img)
            self.canvas.itemconfig(self.canvas_img_id,
                                   image=self.photo_image)

        except Exception as e:
            print(f"{_T('rend_err')}{e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    root = tk.Tk()
    app = VirtualScopeApp(root)
    root.mainloop()