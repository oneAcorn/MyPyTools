import sys
import os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QLineEdit, QFileDialog, QMessageBox,
    QSizePolicy, QSpinBox, QComboBox
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl, QSettings, QThread, Signal
import cv2
from PIL import Image

# 转换线程（动图）
class WebPConverterThread(QThread):
    finished = Signal(bool, str, str)

    def __init__(self, video_path, start_ms, end_ms, output_path,
                 quality=90, target_fps=20, scale_percent=50):
        super().__init__()
        self.video_path = video_path
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.output_path = output_path
        self.quality = quality
        self.target_fps = target_fps
        self.scale_percent = scale_percent

    def run(self):
        try:
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                raise RuntimeError("无法打开视频文件")

            orig_fps = cap.get(cv2.CAP_PROP_FPS)
            if orig_fps <= 0:
                orig_fps = 25

            if self.target_fps > orig_fps:
                self.target_fps = orig_fps

            frame_interval = orig_fps / self.target_fps
            scale_factor = self.scale_percent / 100.0

            cap.set(cv2.CAP_PROP_POS_MSEC, self.start_ms)

            frames = []
            current_ms = self.start_ms
            accum = 0
            while current_ms < self.end_ms:
                ret, frame = cap.read()
                if not ret:
                    break
                accum += 1
                if accum >= frame_interval:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(frame_rgb)
                    if scale_factor != 1.0:
                        new_size = (int(pil_img.width * scale_factor),
                                    int(pil_img.height * scale_factor))
                        pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)
                    frames.append(pil_img)
                    accum -= frame_interval
                current_ms += 1000 / orig_fps

            cap.release()

            if not frames:
                raise RuntimeError("未提取到任何帧")

            duration_per_frame = int(1000 / self.target_fps)
            frames[0].save(
                self.output_path,
                format='WEBP',
                save_all=True,
                append_images=frames[1:],
                duration=duration_per_frame,
                loop=0,
                quality=self.quality,
                # method=6 #6压缩最大,但是很慢
            )
            self.finished.emit(True, "转换成功", self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e), "")

# 静态帧保存线程（可选，但为了不阻塞UI也使用线程）
class FrameSaveThread(QThread):
    finished = Signal(bool, str, str)

    def __init__(self, video_path, position_ms, output_path, quality=90, scale_percent=50):
        super().__init__()
        self.video_path = video_path
        self.position_ms = position_ms
        self.output_path = output_path
        self.quality = quality
        self.scale_percent = scale_percent

    def run(self):
        try:
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                raise RuntimeError("无法打开视频文件")

            cap.set(cv2.CAP_PROP_POS_MSEC, self.position_ms)
            ret, frame = cap.read()
            cap.release()

            if not ret:
                raise RuntimeError("无法读取当前帧")

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)

            scale_factor = self.scale_percent / 100.0
            if scale_factor != 1.0:
                new_size = (int(pil_img.width * scale_factor),
                            int(pil_img.height * scale_factor))
                pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)

            # 保存为静态WebP（无动画参数）
            pil_img.save(
                self.output_path,
                format='WEBP',
                quality=self.quality,
                method=6
            )
            self.finished.emit(True, "帧保存成功", self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e), "")

class VideoPlayerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频播放与WebP转换器")
        self.setGeometry(100, 100, 1000, 750)

        self.settings = QSettings("YourCompany", "VideoToWebP")
        self.converter_thread = None
        self.frame_save_thread = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # 文件选择区域
        file_layout = QHBoxLayout()
        self.open_btn = QPushButton("打开视频文件")
        self.open_btn.clicked.connect(self.open_file)
        self.file_label = QLabel("未选择文件")
        file_layout.addWidget(self.open_btn)
        file_layout.addWidget(self.file_label)
        file_layout.addStretch()
        main_layout.addLayout(file_layout)

        # 视频播放控件
        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setMinimumSize(320, 240)
        main_layout.addWidget(self.video_widget, stretch=1)

        # 媒体播放器
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)

        # 播放控制区域（新增速度选择）
        control_layout = QHBoxLayout()
        self.play_pause_btn = QPushButton("播放")
        self.play_pause_btn.clicked.connect(self.toggle_play_pause)
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.sliderMoved.connect(self.set_position)
        self.time_label = QLabel("00:00 / 00:00")
        control_layout.addWidget(self.play_pause_btn)
        control_layout.addWidget(self.position_slider)
        control_layout.addWidget(self.time_label)

        # 速度选择
        control_layout.addWidget(QLabel("速度:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["1x", "2x", "4x", "8x"])
        self.speed_combo.currentTextChanged.connect(self.change_speed)
        control_layout.addWidget(self.speed_combo)

        main_layout.addLayout(control_layout)

        # 输出文件夹和前缀
        output_folder_layout = QHBoxLayout()
        output_folder_layout.addWidget(QLabel("输出文件夹:"))
        self.folder_btn = QPushButton("选择文件夹")
        self.folder_btn.clicked.connect(self.select_output_folder)
        self.folder_label = QLabel("未选择")
        output_folder_layout.addWidget(self.folder_btn)
        output_folder_layout.addWidget(self.folder_label)
        output_folder_layout.addStretch()
        main_layout.addLayout(output_folder_layout)

        prefix_layout = QHBoxLayout()
        prefix_layout.addWidget(QLabel("文件前缀:"))
        self.prefix_input = QLineEdit()
        self.prefix_input.setText("output")
        prefix_layout.addWidget(self.prefix_input)
        main_layout.addLayout(prefix_layout)

        # 质量、帧率、分辨率设置
        settings_layout = QHBoxLayout()

        settings_layout.addWidget(QLabel("质量 (1-100):"))
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(90)
        self.quality_slider.setTickPosition(QSlider.TicksBelow)
        self.quality_slider.setTickInterval(10)
        self.quality_label = QLabel("90")
        self.quality_slider.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        settings_layout.addWidget(self.quality_slider)
        settings_layout.addWidget(self.quality_label)

        settings_layout.addWidget(QLabel("帧率 (fps):"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(20)
        settings_layout.addWidget(self.fps_spin)

        settings_layout.addWidget(QLabel("分辨率 (%):"))
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(10, 100)
        self.scale_spin.setValue(50)
        self.scale_spin.setSuffix("%")
        settings_layout.addWidget(self.scale_spin)

        settings_layout.addStretch()
        main_layout.addLayout(settings_layout)

        # 转换区域 + 保存帧按钮
        action_layout = QHBoxLayout()
        action_layout.addWidget(QLabel("动图秒数:"))
        self.duration_input = QLineEdit()
        self.duration_input.setText("5")
        action_layout.addWidget(self.duration_input)

        self.convert_btn = QPushButton("转换为WebP")
        self.convert_btn.clicked.connect(self.convert_to_webp)
        action_layout.addWidget(self.convert_btn)

        self.save_frame_btn = QPushButton("保存当前帧为WebP")
        self.save_frame_btn.clicked.connect(self.save_current_frame)
        action_layout.addWidget(self.save_frame_btn)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        action_layout.addWidget(self.status_label)

        action_layout.addStretch()
        main_layout.addLayout(action_layout)

        # 连接信号
        self.media_player.positionChanged.connect(self.update_position)
        self.media_player.durationChanged.connect(self.update_duration)

        self.video_path = ""
        self.output_folder = ""

    def open_file(self):
        last_dir = self.settings.value("last_dir", "")
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", last_dir, "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)"
        )
        if file_path:
            self.video_path = file_path
            self.file_label.setText(os.path.basename(file_path))
            self.media_player.setSource(QUrl.fromLocalFile(file_path))
            self.play_pause_btn.setText("播放")
            current_dir = os.path.dirname(file_path)
            self.settings.setValue("last_dir", current_dir)

    def select_output_folder(self):
        last_folder = self.settings.value("last_output_folder", "")
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹", last_folder)
        if folder:
            self.output_folder = folder
            self.folder_label.setText(folder)
            self.settings.setValue("last_output_folder", folder)

    def toggle_play_pause(self):
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.play_pause_btn.setText("播放")
        else:
            self.media_player.play()
            self.play_pause_btn.setText("暂停")

    def change_speed(self, text):
        rate = float(text.replace('x', ''))
        self.media_player.setPlaybackRate(rate)

    def set_position(self, position):
        self.media_player.setPosition(position)

    def update_position(self, position):
        self.position_slider.setValue(position)
        total = self.media_player.duration()
        self.time_label.setText(self.format_time(position, total))

    def update_duration(self, duration):
        self.position_slider.setRange(0, duration)
        self.time_label.setText(self.format_time(0, duration))

    @staticmethod
    def format_time(ms, total_ms):
        def to_mmss(ms):
            s = ms // 1000
            m = s // 60
            s = s % 60
            return f"{m:02d}:{s:02d}"
        return f"{to_mmss(ms)} / {to_mmss(total_ms)}"

    def generate_unique_filename(self, folder, prefix, suffix="_frame"):
        """生成唯一文件名：prefix.webp 或 prefix_frame.webp，若存在则加数字"""
        base = os.path.join(folder, prefix)
        if suffix:
            base = base + suffix
        if not os.path.exists(base + ".webp"):
            return base + ".webp"
        counter = 1
        while True:
            candidate = f"{base}{counter}.webp"
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def convert_to_webp(self):
        # 暂停视频
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.play_pause_btn.setText("播放")

        if not self.video_path or not os.path.exists(self.video_path):
            QMessageBox.warning(self, "警告", "请先打开一个视频文件")
            return
        if not self.output_folder or not os.path.isdir(self.output_folder):
            QMessageBox.warning(self, "警告", "请先选择一个输出文件夹")
            return

        try:
            duration_sec = float(self.duration_input.text())
            if duration_sec <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "警告", "请输入有效的正数秒数")
            return

        start_ms = self.media_player.position()
        total_duration_ms = self.media_player.duration()
        if start_ms < 0:
            start_ms = 0

        end_ms = start_ms + duration_sec * 1000
        if end_ms > total_duration_ms:
            end_ms = total_duration_ms
            actual_duration_sec = (end_ms - start_ms) / 1000
            if actual_duration_sec <= 0:
                QMessageBox.warning(self, "警告", "当前播放位置已到视频末尾，无法截取")
                return

        quality = self.quality_slider.value()
        target_fps = self.fps_spin.value()
        scale_percent = self.scale_spin.value()

        prefix = self.prefix_input.text().strip()
        if not prefix:
            prefix = "output"
        # 动图命名：直接使用前缀，可能加数字
        output_path = self.generate_unique_filename(self.output_folder, prefix, suffix="")

        self.convert_btn.setEnabled(False)
        self.save_frame_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)
        self.status_label.setText("转换中...")

        self.converter_thread = WebPConverterThread(
            self.video_path, start_ms, end_ms, output_path,
            quality=quality, target_fps=target_fps, scale_percent=scale_percent
        )
        self.converter_thread.finished.connect(self.on_conversion_finished)
        self.converter_thread.start()

    def on_conversion_finished(self, success, message, output_path):
        self.convert_btn.setEnabled(True)
        self.save_frame_btn.setEnabled(True)
        self.open_btn.setEnabled(True)
        self.folder_btn.setEnabled(True)
        self.status_label.setText("")

        if success:
            QMessageBox.information(self, "成功", f"动图已保存到：{output_path}")
        else:
            QMessageBox.critical(self, "错误", f"转换失败：{message}")

        self.converter_thread = None

    def save_current_frame(self):
        # 暂停视频
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.play_pause_btn.setText("播放")

        if not self.video_path or not os.path.exists(self.video_path):
            QMessageBox.warning(self, "警告", "请先打开一个视频文件")
            return
        if not self.output_folder or not os.path.isdir(self.output_folder):
            QMessageBox.warning(self, "警告", "请先选择一个输出文件夹")
            return

        position_ms = self.media_player.position()
        if position_ms < 0:
            QMessageBox.warning(self, "警告", "无效的播放位置")
            return

        quality = self.quality_slider.value()
        scale_percent = self.scale_spin.value()

        prefix = self.prefix_input.text().strip()
        if not prefix:
            prefix = "output"
        # 静态帧命名：前缀_frame[数字].webp
        output_path = self.generate_unique_filename(self.output_folder, prefix, suffix="")

        self.convert_btn.setEnabled(False)
        self.save_frame_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)
        self.status_label.setText("保存帧中...")

        self.frame_save_thread = FrameSaveThread(
            self.video_path, position_ms, output_path,
            quality=quality, scale_percent=scale_percent
        )
        self.frame_save_thread.finished.connect(self.on_frame_save_finished)
        self.frame_save_thread.start()

    def on_frame_save_finished(self, success, message, output_path):
        self.convert_btn.setEnabled(True)
        self.save_frame_btn.setEnabled(True)
        self.open_btn.setEnabled(True)
        self.folder_btn.setEnabled(True)
        self.status_label.setText("")

        if success:
            QMessageBox.information(self, "成功", f"帧已保存到：{output_path}")
        else:
            QMessageBox.critical(self, "错误", f"保存失败：{message}")

        self.frame_save_thread = None

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoPlayerWindow()
    window.show()
    sys.exit(app.exec())