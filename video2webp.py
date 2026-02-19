import sys
import os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QLineEdit, QFileDialog, QMessageBox,
    QSizePolicy
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl, QSettings
import cv2
from PIL import Image

class VideoPlayerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频播放与WebP转换器")
        self.setGeometry(100, 100, 900, 700)

        # 用于存储上次路径的QSettings对象
        self.settings = QSettings("YourCompany", "VideoToWebP")

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

        # 视频播放控件（设置拉伸策略，使其占据尽可能多的空间）
        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setMinimumSize(320, 240)  # 设置最小尺寸
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

        # 转换区域
        convert_layout = QHBoxLayout()
        convert_layout.addWidget(QLabel("动图秒数:"))
        self.duration_input = QLineEdit()
        self.duration_input.setText("5")  # 默认5秒
        convert_layout.addWidget(self.duration_input)
        self.convert_btn = QPushButton("转换为WebP")
        self.convert_btn.clicked.connect(self.convert_to_webp)
        convert_layout.addWidget(self.convert_btn)
        convert_layout.addStretch()
        main_layout.addLayout(convert_layout)

        # 连接媒体播放器的信号
        self.media_player.positionChanged.connect(self.update_position)
        self.media_player.durationChanged.connect(self.update_duration)

        # 当前视频路径
        self.video_path = ""

    def open_file(self):
        # 从设置中读取上次路径，如果没有则使用默认路径
        last_dir = self.settings.value("last_dir", "")
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", last_dir, "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)"
        )
        if file_path:
            self.video_path = file_path
            self.file_label.setText(os.path.basename(file_path))
            self.media_player.setSource(QUrl.fromLocalFile(file_path))
            self.play_pause_btn.setText("播放")

            # 保存当前目录到设置中
            current_dir = os.path.dirname(file_path)
            self.settings.setValue("last_dir", current_dir)

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

    def convert_to_webp(self):
        # 转换前暂停视频
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.play_pause_btn.setText("播放")

        # 检查视频是否加载
        if not self.video_path or not os.path.exists(self.video_path):
            QMessageBox.warning(self, "警告", "请先打开一个视频文件")
            return

        # 获取输入秒数
        try:
            duration_sec = float(self.duration_input.text())
            if duration_sec <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "警告", "请输入有效的正数秒数")
            return

        # 获取当前播放位置（毫秒）
        start_ms = self.media_player.position()
        total_duration_ms = self.media_player.duration()

        # 如果视频未播放，默认从0开始
        if start_ms < 0:
            start_ms = 0

        # 计算结束时间
        end_ms = start_ms + duration_sec * 1000
        if end_ms > total_duration_ms:
            end_ms = total_duration_ms
            actual_duration_sec = (end_ms - start_ms) / 1000
            if actual_duration_sec <= 0:
                QMessageBox.warning(self, "警告", "当前播放位置已到视频末尾，无法截取")
                return

        # 选择输出文件（也使用上次保存的目录）
        last_save_dir = self.settings.value("last_save_dir", "")
        output_path, _ = QFileDialog.getSaveFileName(
            self, "保存WebP动图", last_save_dir, "WebP图像 (*.webp)"
        )
        if not output_path:
            return

        # 保存输出目录
        self.settings.setValue("last_save_dir", os.path.dirname(output_path))

        # 执行转换
        try:
            self.convert_video_to_webp(self.video_path, start_ms, end_ms, output_path)
            QMessageBox.information(self, "成功", f"动图已保存到：{output_path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"转换失败：{str(e)}")

    def convert_video_to_webp(self, video_path, start_ms, end_ms, output_path):
        """使用 OpenCV 读取视频片段，并用 Pillow 保存为 WebP 动图"""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError("无法打开视频文件")

        # 获取视频帧率
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 25  # 默认帧率

        # 定位到起始时间（毫秒）
        cap.set(cv2.CAP_PROP_POS_MSEC, start_ms)

        frames = []
        current_ms = start_ms
        while current_ms < end_ms:
            ret, frame = cap.read()
            if not ret:
                break
            # OpenCV 读取的是 BGR，转换为 RGB
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)
            frames.append(pil_img)

            # 下一帧的时间
            current_ms += 1000 / fps

        cap.release()

        if not frames:
            raise RuntimeError("未提取到任何帧")

        # 计算每帧持续时间（毫秒）
        duration_per_frame = int(1000 / fps)

        # 保存为 WebP 动图
        frames[0].save(
            output_path,
            format='WEBP',
            save_all=True,
            append_images=frames[1:],
            duration=duration_per_frame,
            loop=0
        )

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoPlayerWindow()
    window.show()
    sys.exit(app.exec())