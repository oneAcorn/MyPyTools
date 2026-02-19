import sys
import os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QLineEdit, QFileDialog, QMessageBox,
    QSizePolicy, QProgressBar
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl, QSettings, QThread, Signal
import cv2
from PIL import Image

# 转换线程类
class WebPConverterThread(QThread):
    finished = Signal(bool, str, str)  # 成功标志，消息，输出路径
    progress = Signal(int)  # 可选进度，这里简单使用

    def __init__(self, video_path, start_ms, end_ms, output_path):
        super().__init__()
        self.video_path = video_path
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.output_path = output_path

    def run(self):
        try:
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                raise RuntimeError("无法打开视频文件")

            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                fps = 25

            cap.set(cv2.CAP_PROP_POS_MSEC, self.start_ms)

            frames = []
            current_ms = self.start_ms
            while current_ms < self.end_ms:
                ret, frame = cap.read()
                if not ret:
                    break
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(frame_rgb)
                frames.append(pil_img)
                current_ms += 1000 / fps

            cap.release()

            if not frames:
                raise RuntimeError("未提取到任何帧")

            duration_per_frame = int(1000 / fps)

            frames[0].save(
                self.output_path,
                format='WEBP',
                save_all=True,
                append_images=frames[1:],
                duration=duration_per_frame,
                loop=0
            )

            self.finished.emit(True, "转换成功", self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e), "")

class VideoPlayerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频播放与WebP转换器")
        self.setGeometry(100, 100, 900, 700)

        self.settings = QSettings("YourCompany", "VideoToWebP")
        self.converter_thread = None  # 保存线程实例

        # 中心部件和布局
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

        # 媒体播放器和音频输出
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)

        # 播放控制区域
        control_layout = QHBoxLayout()
        self.play_pause_btn = QPushButton("播放")
        self.play_pause_btn.clicked.connect(self.toggle_play_pause)
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.sliderMoved.connect(self.set_position)
        self.time_label = QLabel("00:00 / 00:00")
        control_layout.addWidget(self.play_pause_btn)
        control_layout.addWidget(self.position_slider)
        control_layout.addWidget(self.time_label)
        main_layout.addLayout(control_layout)

        # 输出设置区域（新增）
        output_layout = QHBoxLayout()
        output_layout.addWidget(QLabel("输出文件夹:"))
        self.folder_btn = QPushButton("选择文件夹")
        self.folder_btn.clicked.connect(self.select_output_folder)
        self.folder_label = QLabel("未选择")
        output_layout.addWidget(self.folder_btn)
        output_layout.addWidget(self.folder_label)
        output_layout.addStretch()
        main_layout.addLayout(output_layout)

        prefix_layout = QHBoxLayout()
        prefix_layout.addWidget(QLabel("文件前缀:"))
        self.prefix_input = QLineEdit()
        self.prefix_input.setText("output")
        prefix_layout.addWidget(self.prefix_input)
        main_layout.addLayout(prefix_layout)

        # 转换区域
        convert_layout = QHBoxLayout()
        convert_layout.addWidget(QLabel("动图秒数:"))
        self.duration_input = QLineEdit()
        self.duration_input.setText("5")
        convert_layout.addWidget(self.duration_input)
        self.convert_btn = QPushButton("转换为WebP")
        self.convert_btn.clicked.connect(self.convert_to_webp)
        convert_layout.addWidget(self.convert_btn)

        # 状态标签（显示转换中）
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        convert_layout.addWidget(self.status_label)
        convert_layout.addStretch()
        main_layout.addLayout(convert_layout)

        # 连接媒体播放器的信号
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

    def generate_unique_filename(self, folder, prefix):
        """在文件夹中生成不重复的文件名：prefix.webp, prefix1.webp, prefix2.webp..."""
        base = os.path.join(folder, prefix)
        if not os.path.exists(base + ".webp"):
            return base + ".webp"
        counter = 1
        while True:
            candidate = f"{base}{counter}.webp"
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def convert_to_webp(self):
        # 转换前暂停视频
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.play_pause_btn.setText("播放")

        # 检查视频是否加载
        if not self.video_path or not os.path.exists(self.video_path):
            QMessageBox.warning(self, "警告", "请先打开一个视频文件")
            return

        # 检查输出文件夹
        if not self.output_folder or not os.path.isdir(self.output_folder):
            QMessageBox.warning(self, "警告", "请先选择一个输出文件夹")
            return

        # 获取输入秒数
        try:
            duration_sec = float(self.duration_input.text())
            if duration_sec <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "警告", "请输入有效的正数秒数")
            return

        # 获取当前播放位置
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

        # 获取前缀并生成唯一输出路径
        prefix = self.prefix_input.text().strip()
        if not prefix:
            prefix = "output"
        output_path = self.generate_unique_filename(self.output_folder, prefix)

        # 禁用按钮，显示状态
        self.convert_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)
        self.status_label.setText("转换中...")

        # 创建并启动转换线程
        self.converter_thread = WebPConverterThread(self.video_path, start_ms, end_ms, output_path)
        self.converter_thread.finished.connect(self.on_conversion_finished)
        self.converter_thread.start()

    def on_conversion_finished(self, success, message, output_path):
        # 恢复按钮
        self.convert_btn.setEnabled(True)
        self.open_btn.setEnabled(True)
        self.folder_btn.setEnabled(True)
        self.status_label.setText("")

        if success:
            QMessageBox.information(self, "成功", f"动图已保存到：{output_path}")
        else:
            QMessageBox.critical(self, "错误", f"转换失败：{message}")

        # 清理线程
        self.converter_thread = None

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoPlayerWindow()
    window.show()
    sys.exit(app.exec())