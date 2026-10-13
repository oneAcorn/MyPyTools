"""
录屏工具：把屏幕（或指定区域）录制为 WebP 动图
依赖：pip install PySide6 mss pillow
用法：python screen2webp.py
"""

import os
import sys
import time
import ctypes
import threading
from ctypes import wintypes
from datetime import datetime

import mss
from PIL import Image

from PySide6.QtCore import (
    Qt, QRect, QEvent, QThread, QTimer, QAbstractNativeEventFilter, Signal, QSettings
)
from PySide6.QtGui import (
    QGuiApplication, QColor, QCursor, QFont, QImage, QKeySequence, QPainter, QPen, QPixmap, QScreen
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QSlider, QLineEdit, QFileDialog, QMessageBox, QGroupBox, QProgressBar,
    QCheckBox
)

MSSFactory = getattr(mss, "MSS", None) or mss.mss

# ---------- 全局快捷键（Windows RegisterHotKey） ----------

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
if user32 is not None:
    user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.RegisterHotKey.restype = ctypes.c_bool
    user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.UnregisterHotKey.restype = ctypes.c_bool

DEFAULT_HOTKEY = "Alt+R"


def parse_hotkey(text: str):
    """把 'Ctrl+Shift+F5' 这类文本解析为 (修饰位, 虚拟键码)，无效返回 None"""
    mods = 0
    vk = 0
    for part in (text or "").replace("-", "+").split("+"):
        token = part.strip().upper()
        if not token:
            continue
        if token in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif token in ("ALT", "MENU"):
            mods |= MOD_ALT
        elif token == "SHIFT":
            mods |= MOD_SHIFT
        elif token in ("WIN", "META", "SUPER"):
            mods |= MOD_WIN
        elif len(token) == 1 and ("A" <= token <= "Z"):
            vk = ord(token)
        elif len(token) == 1 and ("0" <= token <= "9"):
            vk = ord(token)
        elif len(token) > 1 and token[0] == "F" and token[1:].isdigit():
            num = int(token[1:])
            if 1 <= num <= 24:
                vk = 0x70 + num - 1
    if not mods or not vk:
        return None
    return mods, vk


class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    """系统级快捷键：向 Windows 注册热键并接收 WM_HOTKEY 消息"""

    HOTKEY_ID = 0xB1B1

    def __init__(self):
        super().__init__()
        self.registered = False
        self.sequence = ""
        self.callback = None

    def register(self, sequence: str) -> bool:
        self.unregister()
        self.sequence = sequence or ""
        if user32 is None:
            return False
        parsed = parse_hotkey(sequence)
        if parsed is None:
            return False
        mods, vk = parsed
        self.registered = bool(user32.RegisterHotKey(
            None, self.HOTKEY_ID, mods | MOD_NOREPEAT, vk))
        return self.registered

    def unregister(self):
        if self.registered and user32 is not None:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)
        self.registered = False

    def nativeEventFilter(self, event_type, message):
        if user32 is not None:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                if self.callback is not None:
                    self.callback()
                return True, 0
        return False, 0


class ShortcutEdit(QLineEdit):
    """点击后按下组合键即可录入快捷键"""

    shortcutCaptured = Signal(str)
    shortcutCleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignCenter)
        self.setPlaceholderText("点击后按下快捷键")
        self.setFixedWidth(150)
        self._capturing = False

    def mousePressEvent(self, event):
        self._capturing = True
        self.setFocus(Qt.MouseFocusReason)
        self.setText("请按键…（Backspace 清除）")

    def focusOutEvent(self, event):
        self._capturing = False
        super().focusOutEvent(event)
        if self.text().startswith("请按键"):
            self.setText("")

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return

        if event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            self._capturing = False
            self.clear()
            self.shortcutCleared.emit()
            self.clearFocus()
            return

        modifiers = event.modifiers()
        real_mods = modifiers & (Qt.ControlModifier | Qt.AltModifier
                                 | Qt.ShiftModifier | Qt.MetaModifier)
        if real_mods == Qt.NoModifier:
            self.setText("需要包含 Ctrl / Alt / Shift / Win")
            return
        if event.key() in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
            return

        sequence = QKeySequence(int(event.key()) | modifiers)
        text = sequence.toString(QKeySequence.PortableText)
        if not text or parse_hotkey(text) is None:
            self.setText("该组合键暂不支持")
            return

        self._capturing = False
        self.setText(text)
        self.shortcutCaptured.emit(text)
        self.clearFocus()


# ---------- 基础工具 ----------

def screen_physical_rect(screen: QScreen) -> dict:
    """把 Qt 的逻辑坐标屏幕区域换算为 mss 使用的物理像素区域"""
    g = screen.geometry()
    dpr = screen.devicePixelRatio()
    return {
        "left": int(g.x() * dpr),
        "top": int(g.y() * dpr),
        "width": int(g.width() * dpr),
        "height": int(g.height() * dpr),
    }


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    rgb = img if img.mode == "RGB" else img.convert("RGB")
    data = rgb.tobytes()
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def pil_thumbnail(img: Image.Image, box: tuple) -> Image.Image:
    thumb = img.copy()
    thumb.thumbnail(box, Image.Resampling.BILINEAR)
    return thumb


def default_output_dir() -> str:
    home = os.path.expanduser("~")
    for name in ("Videos", "Desktop", "Pictures"):
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    return home


def unique_path(folder: str, prefix: str, suffix: str = ".webp") -> str:
    base = os.path.join(folder, prefix or "output")
    candidate = base + suffix
    counter = 1
    while os.path.exists(candidate):
        candidate = f"{base}{counter}{suffix}"
        counter += 1
    return candidate


def grab_region(region: dict) -> Image.Image:
    with MSSFactory() as sct:
        shot = sct.grab(region)
        return Image.frombytes("RGB", shot.size, shot.rgb)


# ---------- 区域选择窗口 ----------

class RegionSelector(QWidget):
    """在鼠标所在的显示器上拖拽选择录制区域（返回物理像素坐标）"""

    regionSelected = Signal(dict)
    cancelled = Signal()

    MIN_SIZE = 8

    def __init__(self, screen: QScreen):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._dpr = screen.devicePixelRatio()
        self.setGeometry(screen.geometry())

        background = pil_to_qpixmap(grab_region(screen_physical_rect(screen)))
        background.setDevicePixelRatio(self._dpr)
        self._background = background

        self._anchor = None
        self._current = None
        self._hint = "拖拽鼠标框选录制区域   Esc 取消"

    def _to_physical(self, rect: QRect) -> dict:
        dpr = self._dpr
        return {
            "left": int(rect.x() * dpr),
            "top": int(rect.y() * dpr),
            "width": int(rect.width() * dpr),
            "height": int(rect.height() * dpr),
        }

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self._background, self._background.rect())
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        if self._current is not None:
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(self._current, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            painter.setPen(QPen(QColor("#00e5ff"), 2))
            painter.drawRect(self._current)
            painter.setPen(QPen(QColor("#00e5ff"), 1))
            for point in (self._current.topLeft(), self._current.topRight(),
                          self._current.bottomLeft(), self._current.bottomRight()):
                painter.fillRect(point.x() - 3, point.y() - 3, 6, 6, QColor("#00e5ff"))

            pw, ph = int(self._current.width() * self._dpr), int(self._current.height() * self._dpr)
            text = f"{pw} × {ph}"
            box = QRect(self._current.topLeft().x(), self._current.topLeft().y() - 26,
                        self._current.width(), 22)
            if box.top() < 0:
                box.moveTop(self._current.bottom() + 4)
            painter.setPen(QColor("#00e5ff"))
            painter.setFont(QFont("Consolas", 11, QFont.Bold))
            painter.drawText(box, Qt.AlignCenter, text)

        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Microsoft YaHei", 12))
        painter.drawText(self.rect().adjusted(0, 24, 0, 0), Qt.AlignHCenter | Qt.AlignTop, self._hint)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._anchor = event.position().toPoint()
            self._current = QRect(self._anchor, self._anchor)
            self.update()

    def mouseMoveEvent(self, event):
        if self._anchor is not None:
            self._current = QRect(self._anchor, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._current is not None:
            rect = self._current
            self._anchor = None
            self._current = None
            if rect.width() >= self.MIN_SIZE and rect.height() >= self.MIN_SIZE:
                self.regionSelected.emit(self._to_physical(rect))
                self.close()
            else:
                self._hint = "区域太小，请重新拖拽框选   Esc 取消"
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()


# ---------- 倒计时窗口 ----------

class CountdownWindow(QWidget):
    finished = Signal()
    cancelled = Signal()

    def __init__(self, seconds: int = 3):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(260, 170)
        self._remaining = seconds
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("color: #ffffff; font-size: 60px; font-weight: bold;")
        self.hint = QLabel("准备录制，Esc 取消")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet("color: #dddddd; font-size: 13px;")
        layout.addWidget(self.label)
        layout.addWidget(self.hint)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._update_label()

    def _update_label(self):
        self.label.setText(str(self._remaining) if self._remaining > 0 else "●")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 16, 16, 215))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)

    def start_at_center(self):
        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry()
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()
        self._timer.start(1000)

    def _tick(self):
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.close()
            self.finished.emit()
        else:
            self._update_label()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._timer.stop()
            self.close()
            self.cancelled.emit()


# ---------- 录制时的悬浮控制条 ----------

class FloatingBar(QWidget):
    stopRequested = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(300, 52)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        self.info = QLabel("00:00 / 0 帧")
        self.info.setStyleSheet("color: #ffffff; font-size: 14px;")
        self.stop_button = QPushButton("停止录制")
        self.stop_button.setFixedSize(110, 32)
        self.stop_button.setStyleSheet(
            "background:#e04b4b;color:#ffffff;border:none;border-radius:4px;font-size:14px;"
        )
        self.stop_button.clicked.connect(self.stopRequested.emit)
        layout.addWidget(self.info)
        layout.addStretch()
        layout.addWidget(self.stop_button)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 16, 16, 200))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)

    def place_at_region(self, region: dict):
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        x = int((region["left"] + region["width"] - 320 * dpr) / dpr)
        y = int((region["top"] + region["height"] - 80 * dpr) / dpr)
        screen = QGuiApplication.primaryScreen().geometry()
        self.move(min(max(x, screen.left() + 8), screen.right() - self.width() - 8),
                  min(max(y, screen.top() + 8), screen.bottom() - self.height() - 8))
        self.show()
        self.raise_()


# ---------- 录制线程 ----------

class RecorderThread(QThread):
    progress = Signal(int, float)
    previewReady = Signal(object)
    stageChanged = Signal(str)
    finishedWithResult = Signal(bool, str)

    def __init__(self, region, scale, frame_ms, quality, max_seconds, output_path):
        super().__init__()
        self.region = dict(region)
        self.scale = float(scale)
        self.frame_ms = int(frame_ms)
        self.quality = int(quality)
        self.max_seconds = float(max_seconds)
        self.output_path = output_path
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        frames = []
        try:
            interval = max(self.frame_ms, 20) / 1000.0
            deadline = self.max_seconds if self.max_seconds > 0 else 0.0

            with MSSFactory() as sct:
                started = time.perf_counter()
                next_at = started
                while True:
                    shot = sct.grab(self.region)
                    frame = Image.frombytes("RGB", shot.size, shot.rgb)
                    if self.scale != 1.0:
                        frame = frame.resize(
                            (max(1, round(frame.width / self.scale)),
                             max(1, round(frame.height / self.scale))),
                            Image.Resampling.BILINEAR,
                        )
                    frames.append(frame)
                    count = len(frames)
                    elapsed = time.perf_counter() - started

                    self.progress.emit(count, elapsed)
                    if count % 6 == 0:
                        self.previewReady.emit(pil_thumbnail(frame, (480, 300)))

                    if self._stop.is_set():
                        break
                    if deadline and elapsed >= deadline:
                        break

                    next_at += interval
                    delay = next_at - time.perf_counter()
                    if delay > 0:
                        self._stop.wait(delay)
                    else:
                        next_at = time.perf_counter()

            if not frames:
                raise RuntimeError("未采集到任何画面")

            self.stageChanged.emit(f"正在编码 {len(frames)} 帧...")
            frames[0].save(
                self.output_path,
                format="WEBP",
                save_all=True,
                append_images=frames[1:],
                duration=self.frame_ms,
                loop=0,
                quality=self.quality,
            )
            size_kb = os.path.getsize(self.output_path) / 1024
            self.finishedWithResult.emit(
                True, f"{self.output_path}\n{len(frames)} 帧 · {size_kb:.1f} KB"
            )
        except Exception as e:  # noqa: BLE001
            self.finishedWithResult.emit(False, str(e))


# ---------- 主窗口 ----------

class ScreenRecorderWindow(QMainWindow):
    MODE_FULL = "全屏（主显示器）"
    MODE_ALL = "全屏（所有显示器拼接）"
    MODE_CUSTOM = "自定义区域"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("屏幕录制 → WebP 动图")
        self.setFixedWidth(540)

        self.settings = QSettings("YourCompany", "ScreenToWebP")
        self.recorder = None
        self.selector = None
        self.countdown = None
        self.floating_bar = None
        self.region = {}
        self.region_name = ""
        self._pending_record = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        layout.addWidget(self._build_region_group())
        layout.addWidget(self._build_options_group())
        layout.addWidget(self._build_output_group())
        layout.addStretch()

        action_layout = QHBoxLayout()
        self.record_btn = QPushButton("开始录制")
        self.record_btn.setFixedHeight(38)
        self.record_btn.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.record_btn.clicked.connect(self.start_recording)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setFixedSize(100, 38)
        self.stop_btn.clicked.connect(self.stop_recording)
        self.stop_btn.setEnabled(False)
        action_layout.addWidget(self.record_btn)
        action_layout.addWidget(self.stop_btn)
        layout.addLayout(action_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self._init_defaults()
        self._refresh_preview()
        QApplication.instance().installEventFilter(self)

    # ---- 界面构建 ----

    def _build_region_group(self) -> QGroupBox:
        group = QGroupBox("录制区域")
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self.region_combo = QComboBox()
        self.region_combo.addItems([self.MODE_FULL, self.MODE_ALL, self.MODE_CUSTOM])
        self.region_combo.currentTextChanged.connect(self._on_region_mode_changed)
        self.pick_btn = QPushButton("框选区域")
        self.pick_btn.clicked.connect(self.pick_region)
        row.addWidget(QLabel("范围:"))
        row.addWidget(self.region_combo, 1)
        row.addWidget(self.pick_btn)
        layout.addLayout(row)

        preview_row = QHBoxLayout()
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(268, 152)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet(
            "background:#1e1e1e; border:1px solid #444; color:#888; font-size:12px;"
        )
        info = QVBoxLayout()
        self.region_info = QLabel()
        self.region_info.setWordWrap(True)
        self.region_info.setStyleSheet("font-size: 12px; color: #444;")
        info.addWidget(self.region_info)
        info.addStretch()
        preview_row.addWidget(self.preview_label)
        preview_row.addLayout(info, 1)
        layout.addLayout(preview_row)
        return group

    def _build_options_group(self) -> QGroupBox:
        group = QGroupBox("输出参数")
        layout = QVBoxLayout(group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("分辨率缩小倍数:"))
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["不缩放 (1)", "1.25", "1.5", "2"])
        self.scale_combo.currentIndexChanged.connect(self._refresh_preview)
        row1.addWidget(self.scale_combo)
        row1.addStretch()
        self.size_hint = QLabel("")
        self.size_hint.setStyleSheet("font-size: 12px; color: #444;")
        row1.addWidget(self.size_hint)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("帧时长 (ms):"))
        self.frame_ms_spin = QSpinBox()
        self.frame_ms_spin.setRange(20, 2000)
        self.frame_ms_spin.setSingleStep(20)
        self.frame_ms_spin.setValue(50)
        self.frame_ms_spin.setSuffix(" ms")
        self.frame_ms_spin.valueChanged.connect(self._on_frame_ms_changed)
        row2.addWidget(self.frame_ms_spin)
        self.fps_label = QLabel("")
        self.fps_label.setStyleSheet("font-size: 12px; color: #444;")
        row2.addWidget(self.fps_label)

        row2.addWidget(QLabel("最长时长 (秒):"))
        self.max_seconds_spin = QDoubleSpinBox()
        self.max_seconds_spin.setRange(0, 600)
        self.max_seconds_spin.setDecimals(1)
        self.max_seconds_spin.setSingleStep(1)
        self.max_seconds_spin.setValue(20)
        self.max_seconds_spin.setSpecialValueText("不限")
        self.max_seconds_spin.setSuffix(" s")
        row2.addWidget(self.max_seconds_spin)
        row2.addStretch()
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("质量:"))
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(85)
        self.quality_slider.setTickPosition(QSlider.TicksBelow)
        self.quality_slider.setTickInterval(10)
        self.quality_label = QLabel("85")
        self.quality_label.setFixedWidth(30)
        self.quality_slider.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        row3.addWidget(self.quality_slider, 1)
        row3.addWidget(self.quality_label)
        layout.addLayout(row3)

        self.floating_check = QCheckBox("录制时显示悬浮控制条")
        self.floating_check.setChecked(False)
        layout.addWidget(self.floating_check)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("全局开始录制快捷键:"))
        self.shortcut_edit = ShortcutEdit()
        self.shortcut_edit.shortcutCaptured.connect(self._on_shortcut_captured)
        self.shortcut_edit.shortcutCleared.connect(self._on_shortcut_cleared)
        row4.addWidget(self.shortcut_edit)
        self.hotkey_hint = QLabel("")
        self.hotkey_hint.setStyleSheet("font-size: 12px; color: #444;")
        row4.addWidget(self.hotkey_hint, 1)
        layout.addLayout(row4)
        return group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("保存位置")
        layout = QVBoxLayout(group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("文件夹:"))
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(False)
        self.folder_btn = QPushButton("选择")
        self.folder_btn.clicked.connect(self.select_output_folder)
        self.open_btn = QPushButton("打开")
        self.open_btn.clicked.connect(self.open_output_folder)
        row1.addWidget(self.folder_label, 1)
        row1.addWidget(self.folder_btn)
        row1.addWidget(self.open_btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("文件名前缀:"))
        self.prefix_input = QLineEdit()
        row2.addWidget(self.prefix_input, 1)
        layout.addLayout(row2)
        return group

    # ---- 默认值 ----

    def _init_defaults(self):
        self.scale_combo.setCurrentIndex(1)
        folder = self.settings.value("output_dir", default_output_dir())
        if not os.path.isdir(folder):
            folder = default_output_dir()
        self.output_dir = folder
        self.folder_label.setText(folder)
        self.prefix_input.setText("screen_%s" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        self._on_region_mode_changed(self.MODE_FULL)
        self._on_frame_ms_changed(self.frame_ms_spin.value())

        self.hotkey = GlobalHotkeyFilter()
        self.hotkey.callback = self._hotkey_triggered
        QApplication.instance().installNativeEventFilter(self.hotkey)
        shortcut = self.settings.value("hotkey", DEFAULT_HOTKEY) or ""
        self.shortcut_edit.setText(shortcut)
        self._apply_hotkey(shortcut)

    # ---- 全局快捷键 ----

    def _apply_hotkey(self, sequence: str):
        ok = self.hotkey.register(sequence) if sequence else False
        if not sequence:
            self.hotkey_hint.setText("未启用")
        elif ok:
            self.hotkey_hint.setText(f"已生效：任意窗口按 {sequence} 直接开始录制")
        else:
            self.hotkey_hint.setText(f"{sequence} 注册失败，可能被其他软件占用")
        self.record_btn.setToolTip(f"开始录制（快捷键 {sequence}）" if ok else "开始录制")

    def _on_shortcut_captured(self, sequence: str):
        self.settings.setValue("hotkey", sequence)
        self._apply_hotkey(sequence)

    def _on_shortcut_cleared(self):
        self.settings.setValue("hotkey", "")
        self._apply_hotkey("")

    def _hotkey_triggered(self):
        self.start_recording(countdown=False)

    def _current_region(self) -> dict:
        mode = self.region_combo.currentText()
        with MSSFactory() as sct:
            if mode == self.MODE_ALL:
                return dict(sct.monitors[0])
            if mode == self.MODE_CUSTOM and self.region:
                return dict(self.region)
            return dict(sct.monitors[1])

    def _scale_factor(self) -> float:
        text = self.scale_combo.currentText()
        if text.startswith("不缩放"):
            return 1.0
        return float(text)

    def _on_region_mode_changed(self, text):
        self.pick_btn.setEnabled(text == self.MODE_CUSTOM)
        self._refresh_preview()

    def _on_frame_ms_changed(self, value):
        fps = 1000.0 / value
        self.fps_label.setText(f"≈ {fps:.1f} fps")

    # ---- 区域选择与预览 ----

    def pick_region(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.selector = RegionSelector(screen)
        self.selector.regionSelected.connect(self._on_region_selected)
        self.selector.cancelled.connect(self._close_selector)
        self.selector.showFullScreen()
        self.selector.raise_()
        self.selector.activateWindow()

    def _on_region_selected(self, region):
        self.region = region
        self.region_name = "自定义 %d×%d" % (region["width"], region["height"])
        self._close_selector()
        self.region_combo.setCurrentText(self.MODE_CUSTOM)
        self._refresh_preview()

    def _close_selector(self):
        if self.selector is not None:
            self.selector.close()
            self.selector = None

    def _refresh_preview(self):
        region = self._current_region()
        if not region or region["width"] <= 0 or region["height"] <= 0:
            self.region_info.setText("未检测到可用屏幕")
            return

        scale = self._scale_factor()
        out_w = max(1, round(region["width"] / scale))
        out_h = max(1, round(region["height"] / scale))

        if self.region_combo.currentText() == self.MODE_CUSTOM:
            title = self.region_name or "自定义区域"
        elif self.region_combo.currentText() == self.MODE_ALL:
            title = "所有显示器拼接"
        else:
            title = "主显示器"

        self.region_info.setText(
            f"{title}\n"
            f"位置 ({region['left']}, {region['top']})  原始 {region['width']}×{region['height']}\n"
            f"动图尺寸 {out_w}×{out_h}"
        )
        self.size_hint.setText(f"→ {out_w}×{out_h} px")

        try:
            thumb = pil_thumbnail(grab_region(region), (266, 150))
            self.preview_label.setPixmap(pil_to_qpixmap(thumb))
        except Exception as e:  # noqa: BLE001
            self.preview_label.clear()
            self.preview_label.setText(f"预览失败\n{e}")

    # ---- 输出目录 ----

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹", self.output_dir)
        if folder:
            self.output_dir = folder
            self.folder_label.setText(folder)
            self.settings.setValue("output_dir", folder)

    def open_output_folder(self):
        if os.path.isdir(self.output_dir):
            os.startfile(self.output_dir)
        else:
            QMessageBox.warning(self, "警告", "输出文件夹不存在")

    # ---- 录制流程 ----

    def start_recording(self, countdown: bool = True):
        if self.recorder is not None:
            return

        region = self._current_region()
        if not region or region["width"] < 16 or region["height"] < 16:
            QMessageBox.warning(self, "警告", "录制区域无效")
            return
        if not os.path.isdir(self.output_dir):
            QMessageBox.warning(self, "警告", "输出文件夹不存在")
            return

        frame_ms = self.frame_ms_spin.value()
        max_seconds = self.max_seconds_spin.value()
        out_w = round(region["width"] / self._scale_factor())
        out_h = round(region["height"] / self._scale_factor())
        memory_hint = max_seconds * (1000 / frame_ms) * out_w * out_h * 3 / 1024 / 1024

        self._pending_record = (
            region, frame_ms, max_seconds, self.quality_slider.value(),
            self._scale_factor(), unique_path(self.output_dir, self.prefix_input.text().strip()),
        )
        self.status_label.setText(
            f"预计内存占用约 {memory_hint:.0f} MB（上限 {max_seconds:.0f} 秒 / {max_seconds * (1000 / frame_ms):.0f} 帧）"
        )

        self._set_controls_enabled(False)
        if countdown:
            self.countdown = CountdownWindow(3)
            self.countdown.finished.connect(self._on_countdown_finished)
            self.countdown.cancelled.connect(self._on_countdown_cancelled)
            self.countdown.start_at_center()
        else:
            self._on_countdown_finished()

    def _on_countdown_finished(self):
        if self.countdown is not None:
            self.countdown.close()
            self.countdown = None
        region, frame_ms, max_seconds, quality, scale, output_path = self._pending_record

        self.recorder = RecorderThread(
            region, scale, frame_ms, quality, max_seconds, output_path
        )
        self.recorder.progress.connect(self._on_progress)
        self.recorder.previewReady.connect(self._on_preview)
        self.recorder.stageChanged.connect(lambda text: self.status_label.setText(text))
        self.recorder.finishedWithResult.connect(self._on_record_finished)

        self.hide()
        self.status_label.setText("录制中...")
        self.stop_btn.setEnabled(True)

        if self.floating_check.isChecked():
            self.floating_bar = FloatingBar()
            self.floating_bar.stopRequested.connect(self.stop_recording)
            self.floating_bar.place_at_region(region)

        self.recorder.start()

    def _on_countdown_cancelled(self):
        self.countdown = None
        self._pending_record = None
        self.status_label.setText("已取消录制")
        self._set_controls_enabled(True)

    def stop_recording(self):
        if self.recorder is not None and self.recorder.isRunning():
            self.status_label.setText("正在停止并编码...")
            self.recorder.stop()

    def _on_progress(self, frames, seconds):
        limit = self.max_seconds_spin.value()
        text = f"录制中 {seconds:.1f}s · {frames} 帧"
        if limit > 0:
            self.progress_bar.setValue(min(100, int(seconds / limit * 100)))
            text += f" / 上限 {limit:.0f}s"
        self.status_label.setText(text)
        if self.floating_bar is not None:
            self.floating_bar.info.setText(f"{seconds:04.1f}s · {frames} 帧")

    def _on_preview(self, image):
        if self.isVisible():
            self.preview_label.setPixmap(pil_to_qpixmap(image))

    def _on_record_finished(self, success, message):
        if self.floating_bar is not None:
            self.floating_bar.close()
            self.floating_bar = None

        self.recorder = None
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self._set_controls_enabled(True)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._refresh_preview()

        if success:
            path, detail = message.split("\n", 1)
            self.status_label.setText(f"已保存：{path}")
            box = QMessageBox(self)
            box.setWindowTitle("录制完成")
            box.setIcon(QMessageBox.Information)
            box.setText(f"动图已保存：\n{path}\n\n{detail}")
            open_button = box.addButton("打开所在文件夹", QMessageBox.ActionRole)
            box.addButton("好", QMessageBox.AcceptRole)
            box.exec()
            if box.clickedButton() is open_button:
                os.startfile(os.path.dirname(path))
        else:
            self.status_label.setText("录制失败")
            QMessageBox.critical(self, "错误", f"录制失败：\n{message}")

    def _set_controls_enabled(self, enabled):
        self.record_btn.setEnabled(enabled)
        self.stop_btn.setEnabled(not enabled)
        self.region_combo.setEnabled(enabled)
        self.pick_btn.setEnabled(enabled and self.region_combo.currentText() == self.MODE_CUSTOM)
        self.scale_combo.setEnabled(enabled)
        self.frame_ms_spin.setEnabled(enabled)
        self.max_seconds_spin.setEnabled(enabled)
        self.quality_slider.setEnabled(enabled)
        self.folder_btn.setEnabled(enabled)
        self.prefix_input.setEnabled(enabled)
        self.shortcut_edit.setEnabled(enabled)
        self.progress_bar.setVisible(not enabled)

    def eventFilter(self, obj, event):
        if obj is self.shortcut_edit and event.type() == QEvent.KeyPress:
            return False
        if (event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape
                and self.recorder is not None and self.recorder.isRunning()):
            self.stop_recording()
            return True
        if (event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape
                and self.countdown is not None):
            self.countdown.keyPressEvent(event)
            return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if self.recorder is not None and self.recorder.isRunning():
            QMessageBox.information(self, "提示", "正在录制中，请先停止")
            event.ignore()
            return
        if self.selector is not None:
            self.selector.close()
        if self.countdown is not None:
            self.countdown.close()
        if self.floating_bar is not None:
            self.floating_bar.close()
        self.hotkey.unregister()
        self.settings.setValue("output_dir", self.output_dir)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ScreenRecorderWindow()
    window.show()
    sys.exit(app.exec())
