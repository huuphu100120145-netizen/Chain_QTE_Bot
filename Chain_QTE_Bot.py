import sys
import time
import random
import os
import cv2
import numpy as np
import mss
import win32gui
import ctypes
from ctypes import wintypes

from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# ==========================================
# CTYPES WIN32 DIRECTINPUT (SCANCODES)
# ==========================================
PUL = ctypes.POINTER(ctypes.c_ulong)

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL)
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", PUL)]

class INPUT_I(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("ii", INPUT_I)]

SCANCODES = {
    'e': 0x12, 'r': 0x13, 't': 0x14, 'f': 0x21, 'g': 0x22
}

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

def press_scancode(hex_scancode):
    extra = ctypes.c_ulong(0)
    ii_ = INPUT_I()
    ii_.ki = KEYBDINPUT(0, hex_scancode, KEYEVENTF_SCANCODE, 0, ctypes.pointer(extra))
    x = INPUT(ctypes.c_ulong(1), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

def release_scancode(hex_scancode):
    extra = ctypes.c_ulong(0)
    ii_ = INPUT_I()
    ii_.ki = KEYBDINPUT(0, hex_scancode, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
    x = INPUT(ctypes.c_ulong(1), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

# ==========================================
# BOT THREAD (CÓ REACTION DELAY THẬT)
# ==========================================
class AutoBotUnifiedThread(QThread):
    status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.is_running = False
        self.sct = mss.MSS()
        
        self.monitor = {
            "top": 638,
            "left": 912,
            "width": 96,
            "height": 109
        }
        
        self.threshold_strength = 0.40
        self.threshold_key = 0.38
        
        self.template_strength = None
        self.templates_key = {}
        self.load_templates()

    def load_templates(self):
        strength_path = os.path.join(ASSETS_DIR, "strength.png")
        if os.path.exists(strength_path):
            self.template_strength = cv2.imread(strength_path, cv2.IMREAD_GRAYSCALE)

        keys = ['E', 'R', 'T', 'F', 'G']
        self.templates_key = {}
        for key in keys:
            file_path = os.path.join(ASSETS_DIR, f"key_{key}.png")
            if os.path.exists(file_path):
                img = cv2.imread(file_path, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    h, w = img.shape
                    if h > 4 and w > 4:
                        img = img[2:h-2, 2:w-2]
                    self.templates_key[key] = img

    def check_roblox_active(self):
        try:
            hwnd = win32gui.GetForegroundWindow()
            return "Roblox" in win32gui.GetWindowText(hwnd)
        except:
            return False

    def get_jitter_delay(self, mean, stddev):
        val = random.gauss(mean, stddev)
        return max(0.005, val)

    def run(self):
        last_action_time = time.perf_counter()
        is_chaining = False
        
        active_key_code = None
        key_release_time = 0.0
        
        # Biến đệm để tạo Reaction Delay TRƯỚC KHI bấm
        pending_key_code = None
        scheduled_press_time = 0.0
        next_action_allowed_time = 0.0

        while True:
            now = time.perf_counter()

            # 0. Lệnh nhả phím khi hết Hold Duration
            if active_key_code is not None and now >= key_release_time:
                release_scancode(active_key_code)
                active_key_code = None
                # Debounce nhỏ sau khi nhả phím
                next_action_allowed_time = now + self.get_jitter_delay(0.015, 0.003)

            # 0.1 Lệnh THỰC BẤM sau khi hết thời gian Reaction Delay
            if pending_key_code is not None and now >= scheduled_press_time and active_key_code is None:
                press_scancode(pending_key_code)
                active_key_code = pending_key_code
                pending_key_code = None
                
                # Hold duration (20-30ms)
                hold_duration = self.get_jitter_delay(0.025, 0.003)
                key_release_time = now + hold_duration
                last_action_time = now

            if not self.is_running:
                time.sleep(0.05)
                continue

            if not self.check_roblox_active():
                self.status_signal.emit("Paused (Open Roblox!)... 🟡")
                time.sleep(0.2)
                continue

            if self.template_strength is None or not self.templates_key:
                self.status_signal.emit("Assets not found! ❌")
                time.sleep(1)
                self.load_templates()
                continue

            # 1. Chụp ảnh liên tục bằng MSS
            sct_img = np.array(self.sct.grab(self.monitor))
            gray_frame = cv2.cvtColor(sct_img, cv2.COLOR_BGRA2GRAY)
            fh, fw = gray_frame.shape

            # 2. Check Strength bar
            is_strength_present = False
            if self.template_strength is not None:
                th_s, tw_s = self.template_strength.shape
                if th_s <= fh and tw_s <= fw:
                    res_s = cv2.matchTemplate(gray_frame, self.template_strength, cv2.TM_CCOEFF_NORMED)
                    _, max_val_s, _, _ = cv2.minMaxLoc(res_s)
                    if max_val_s >= self.threshold_strength:
                        is_strength_present = True

            # 3. Check Key Icon
            best_key = None
            max_val_found = 0.0

            for key_name, template_img in self.templates_key.items():
                th, tw = template_img.shape
                if th <= fh and tw <= fw:
                    res_k = cv2.matchTemplate(gray_frame, template_img, cv2.TM_CCOEFF_NORMED)
                    _, max_val_k, _, _ = cv2.minMaxLoc(res_k)
                    if max_val_k >= self.threshold_key and max_val_k > max_val_found:
                        max_val_found = max_val_k
                        best_key = key_name

            target_key = best_key if best_key else ('E' if is_strength_present else None)

            # Reset combo nếu ngừng quá 1.2s
            if is_chaining and (now - last_action_time > 1.2):
                is_chaining = False

            # 4. Đặt lịch bấm phím (Lên lịch trước, đợi Reaction Delay rồi mới bấm)
            if target_key and active_key_code is None and pending_key_code is None and now >= next_action_allowed_time:
                key_lower = target_key.lower()
                scancode = SCANCODES.get(key_lower, None)

                if scancode:
                    # - Phím đầu tiên: Đợi 90ms - 130ms (Reaction Delay chuẩn mắt người)
                    # - Nhịp Spam nối tiếp: Đợi 30ms - 45ms
                    if not is_chaining:
                        delay = self.get_jitter_delay(0.100, 0.015) 
                        is_chaining = True
                    else:
                        delay = self.get_jitter_delay(0.035, 0.005)

                    pending_key_code = scancode
                    scheduled_press_time = now + delay  # Hẹn giờ bấm trong tương lai

                    self.status_signal.emit(f"Reacting to [{target_key}]... ⏳")
            
            elif not target_key and active_key_code is None and pending_key_code is None:
                self.status_signal.emit("Waiting... 🟢")

            time.sleep(0.001)

    def stop(self):
        self.is_running = False

    def start_bot(self):
        self.load_templates()
        self.is_running = True

# ==========================================
# PYQT5 INTERFACE
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chain_QTE_Bot")
        self.setFixedSize(380, 160)
        self.setWindowFlags(Qt.WindowStaysOnTopHint)
        
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QLabel { color: #ffffff; font-family: 'Segoe UI', sans-serif; font-size: 11px; }
            QPushButton {
                background-color: #2d2d2d; color: white;
                border: 2px solid #007acc; border-radius: 8px;
                padding: 10px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background-color: #007acc; }
        """)

        self.bot_thread = AutoBotUnifiedThread()
        self.bot_thread.status_signal.connect(self.update_status)
        self.bot_thread.start()

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()

        self.status_label = QLabel("Status: Disabled 🔴", self)
        self.status_label.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.toggle_btn = QPushButton("ENABLE BOT", self)
        self.toggle_btn.clicked.connect(self.toggle_action)
        layout.addWidget(self.toggle_btn)

        central_widget.setLayout(layout)

    def toggle_action(self):
        if self.bot_thread.is_running:
            self.bot_thread.stop()
            self.toggle_btn.setText("ENABLE BOT")
            self.status_label.setText("Status: Disabled 🔴")
        else:
            self.bot_thread.start_bot()
            self.toggle_btn.setText("DISABLE BOT")
            self.status_label.setText("Status: Waiting... 🟢")

    def update_status(self, text):
        if self.bot_thread.is_running:
            self.status_label.setText(f"Status: {text}")

    def closeEvent(self, event):
        self.bot_thread.stop()
        self.bot_thread.terminate()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())