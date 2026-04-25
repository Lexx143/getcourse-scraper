import os
import sys
import re
import logging
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QTextEdit, 
                             QLabel, QFileDialog, QDialog, QMessageBox, QProgressBar)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile

import main as grabber

class WorkerThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(str, str, str, int) # text, speed, eta, percent
    finished_signal = pyqtSignal(bool)

    def __init__(self, url, data_dir):
        super().__init__()
        self.url = url
        self.data_dir = data_dir

    def run(self):
        grabber.ABORT_DOWNLOAD = False
        grabber.PAUSE_DOWNLOAD = False
        grabber.CREATED_FILES.clear()
        
        try:
            logger = logging.getLogger()
            for h in logger.handlers[:]:
                logger.removeHandler(h)
                
            class SignalHandler(logging.Handler):
                def __init__(self, signal, progress_signal):
                    super().__init__()
                    self.signal = signal
                    self.progress_signal = progress_signal
                def emit(self, record):
                    msg = self.format(record)
                    self.signal.emit(msg)
                    
                    # Parse yt-dlp output for progress
                    if "YTDLP_OUT:" in msg and "[download]" in msg and "ETA" in msg:
                        # Пример: [download]  14.1% of ~  94.44MiB at    1.00MiB/s ETA 01:26
                        match = re.search(r'\[download\]\s+([\d\.]+)%\s+of.*?at\s+(.*?/s)\s+ETA\s+(.*)', msg)
                        if match:
                            percent = int(float(match.group(1)))
                            speed = match.group(2)
                            eta = match.group(3)
                            self.progress_signal.emit("Скачивание видео...", speed, eta, percent)
                    elif "Скачивание прикрепленного файла" in msg:
                        self.progress_signal.emit(msg.split('INFO] ')[-1], "-", "-", 0)
                    elif "Fetching lesson:" in msg:
                        self.progress_signal.emit(msg.split('INFO] ')[-1], "-", "-", 0)

            handler = SignalHandler(self.log_signal, self.progress_signal)
            formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

            base_dir = Path(grabber.__file__).parent.resolve()
            session = grabber.get_session(base_dir)
            
            if self.url.startswith("http"):
                grabber.crawl_course(self.url, session, Path(self.data_dir))
                self.log_signal.emit("✅ Скачивание успешно завершено!")
                self.progress_signal.emit("✅ Завершено", "-", "-", 100)
                self.finished_signal.emit(True)
            else:
                self.log_signal.emit("❌ Ошибка: Введите корректную ссылку (начинается с http).")
                self.finished_signal.emit(False)
        except InterruptedError as e:
            self.log_signal.emit(f"🛑 Процесс прерван: {e}")
            grabber.cleanup_session()
            self.progress_signal.emit("🛑 Отменено. Файлы удалены.", "-", "-", 0)
            self.finished_signal.emit(False)
        except Exception as e:
            self.log_signal.emit(f"❌ Ошибка в процессе: {e}")
            self.finished_signal.emit(False)

class LoginWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Авторизация GetCourse")
        self.resize(1000, 700)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.webview = QWebEngineView()
        self.layout.addWidget(self.webview)
        self.webview.setUrl(QUrl("https://niifittech.ru/cms/system/login"))
        
        bottom_panel = QWidget()
        bottom_layout = QHBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(10, 10, 10, 10)
        
        info_label = QLabel("Войдите в свой аккаунт, затем нажмите кнопку сохранения справа 👉")
        info_label.setStyleSheet("color: #555; font-size: 14px;")
        bottom_layout.addWidget(info_label)
        
        self.save_btn = QPushButton("✅ Сохранить авторизацию")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setStyleSheet("""
            QPushButton { background-color: #1a73e8; color: white; padding: 10px 20px; border-radius: 6px; font-weight: bold; font-size: 14px; }
            QPushButton:hover { background-color: #1557b0; }
        """)
        self.save_btn.clicked.connect(self.save_cookies)
        bottom_layout.addWidget(self.save_btn)
        self.layout.addWidget(bottom_panel)
        self.cookies_loaded = []

    def save_cookies(self):
        self.save_btn.setText("Сохранение...")
        self.save_btn.setEnabled(False)
        self.cookies_loaded.clear()
        
        profile = self.webview.page().profile()
        cookie_store = profile.cookieStore()
        def on_cookie_added(cookie): self.cookies_loaded.append(cookie)
        cookie_store.cookieAdded.connect(on_cookie_added)
        cookie_store.loadAllCookies()
        QTimer.singleShot(1000, self.write_cookies_to_file)

    def write_cookies_to_file(self):
        base_dir = Path(__file__).parent.resolve()
        cookie_file = base_dir / 'cookies.txt'
        with open(cookie_file, 'w') as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in self.cookies_loaded:
                domain = c.domain()
                name = bytearray(c.name()).decode()
                value = bytearray(c.value()).decode()
                path = c.path()
                secure = "TRUE" if c.isSecure() else "FALSE"
                expires = c.expirationDate().toSecsSinceEpoch() if not c.isSessionCookie() else 0
                if not domain.startswith('.'): domain = f".{domain}"
                f.write(f"{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
        self.accept()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GetCourse Grabber")
        self.resize(750, 600)
        self.setStyleSheet("""
            QMainWindow { background-color: #f8f9fa; }
            QLabel { font-family: 'Segoe UI', Arial; font-size: 14px; color: #202124; }
            QLineEdit { padding: 10px; border: 1px solid #dadce0; border-radius: 6px; font-size: 14px; background: white; color: #202124; }
            QLineEdit:focus { border: 2px solid #1a73e8; }
            QPushButton { font-family: 'Segoe UI', Arial; font-weight: bold; font-size: 14px; border-radius: 6px; padding: 10px 15px; cursor: pointer; color: #202124; }
            QProgressBar { border: 1px solid #dadce0; border-radius: 6px; text-align: center; color: #202124; background-color: #e8eaed; }
            QProgressBar::chunk { background-color: #4CAF50; border-radius: 6px; }
            QTextEdit { background-color: #282c34; color: #abb2bf; font-family: 'Consolas', monospace; font-size: 13px; border-radius: 6px; padding: 10px; border: none; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(15)

        # 1. URL Section
        url_layout = QVBoxLayout()
        url_layout.setSpacing(5)
        url_label = QLabel("🔗 Ссылка на тренинг (URL):")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://niifittech.ru/teach/control/stream/view/id/...")
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        main_layout.addLayout(url_layout)

        # 2. Directory Section
        dir_layout = QVBoxLayout()
        dir_layout.setSpacing(5)
        dir_label = QLabel("📁 Папка для сохранения материалов:")
        dir_row = QHBoxLayout()
        self.dir_input = QLineEdit()
        self.dir_input.setText(str(Path(__file__).parent.resolve() / "data"))
        self.browse_btn = QPushButton("Выбрать")
        self.browse_btn.setStyleSheet("background-color: #f1f3f4; border: 1px solid #dadce0;")
        self.browse_btn.clicked.connect(self.browse_directory)
        dir_row.addWidget(self.dir_input)
        dir_row.addWidget(self.browse_btn)
        dir_layout.addWidget(dir_label)
        dir_layout.addLayout(dir_row)
        main_layout.addLayout(dir_layout)

        # 3. Actions Section
        action_layout = QHBoxLayout()
        self.login_btn = QPushButton("🔑 Войти в GetCourse")
        self.login_btn.setStyleSheet("QPushButton { background-color: #fff; color: #1a73e8; border: 1px solid #1a73e8; } QPushButton:hover { background-color: #e8f0fe; }")
        self.login_btn.clicked.connect(self.open_login)
        self.start_btn = QPushButton("⬇️ Начать скачивание")
        self.start_btn.setStyleSheet("QPushButton { background-color: #1a73e8; color: white; border: none; } QPushButton:hover { background-color: #1557b0; }")
        self.start_btn.clicked.connect(self.start_download)
        action_layout.addWidget(self.login_btn)
        action_layout.addWidget(self.start_btn)
        main_layout.addLayout(action_layout)

        # 4. Progress Section
        self.progress_panel = QWidget()
        progress_layout = QVBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(0, 10, 0, 10)
        
        self.status_label = QLabel("Ожидание...")
        self.status_label.setStyleSheet("font-weight: bold; color: #1a73e8;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        
        info_row = QHBoxLayout()
        self.speed_label = QLabel("Скорость: -")
        self.eta_label = QLabel("Осталось: -")
        self.speed_label.setStyleSheet("color: #5f6368; font-size: 13px;")
        self.eta_label.setStyleSheet("color: #5f6368; font-size: 13px;")
        info_row.addWidget(self.speed_label)
        info_row.addWidget(self.eta_label)
        
        progress_layout.addWidget(self.status_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addLayout(info_row)
        main_layout.addWidget(self.progress_panel)

        # Control Buttons (Hidden by default)
        self.control_layout = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ Пауза")
        self.pause_btn.setStyleSheet("QPushButton { background-color: #fbbc04; color: #202124; border: none; } QPushButton:hover { background-color: #fce8b2; }")
        self.pause_btn.clicked.connect(self.toggle_pause)
        
        self.stop_btn = QPushButton("⏹ Стоп")
        self.stop_btn.setStyleSheet("QPushButton { background-color: #ea4335; color: white; border: none; } QPushButton:hover { background-color: #f28b82; }")
        self.stop_btn.clicked.connect(self.stop_download)
        
        self.control_layout.addWidget(self.pause_btn)
        self.control_layout.addWidget(self.stop_btn)
        self.progress_panel.layout().addLayout(self.control_layout)
        
        # Hide progress panel initially
        self.progress_panel.hide()

        # 5. Logs Toggle
        self.toggle_logs_btn = QPushButton("Показать детали 🔽")
        self.toggle_logs_btn.setStyleSheet("background: transparent; color: #1a73e8; border: none; text-align: left; padding: 0;")
        self.toggle_logs_btn.clicked.connect(self.toggle_logs)
        main_layout.addWidget(self.toggle_logs_btn)

        # 6. Logs Output
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.hide() # Hidden by default
        main_layout.addWidget(self.log_output)
        
        self.check_cookies_status()

    def check_cookies_status(self):
        base_dir = Path(__file__).parent.resolve()
        if (base_dir / 'cookies.txt').exists():
            self.log_output.append("✅ cookies.txt найден. Авторизация активна.")
        else:
            self.log_output.append("⚠️ cookies.txt не найден! Нажмите 'Войти в GetCourse' перед скачиванием.")

    def browse_directory(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения")
        if dir_path: self.dir_input.setText(dir_path)

    def open_login(self):
        dialog = LoginWindow(self)
        if dialog.exec():
            self.log_output.append("✅ Авторизация успешно сохранена!")
            self.check_cookies_status()

    def toggle_logs(self):
        if self.log_output.isHidden():
            self.log_output.show()
            self.toggle_logs_btn.setText("Скрыть детали 🔼")
        else:
            self.log_output.hide()
            self.toggle_logs_btn.setText("Показать детали 🔽")

    def toggle_pause(self):
        if grabber.PAUSE_DOWNLOAD:
            grabber.PAUSE_DOWNLOAD = False
            self.pause_btn.setText("⏸ Пауза")
            self.status_label.setText("Возобновлено...")
            if psutil and grabber.CURRENT_YTDLP_PID:
                try: psutil.Process(grabber.CURRENT_YTDLP_PID).resume()
                except: pass
        else:
            grabber.PAUSE_DOWNLOAD = True
            self.pause_btn.setText("▶️ Продолжить")
            self.status_label.setText("⏸ НА ПАУЗЕ")
            if psutil and grabber.CURRENT_YTDLP_PID:
                try: psutil.Process(grabber.CURRENT_YTDLP_PID).suspend()
                except: pass

    def stop_download(self):
        reply = QMessageBox.question(self, 'Подтверждение', 
            "Точно хотите завершить процесс?\nВсе файлы, скачанные в рамках этого запуска, будут удалены.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.status_label.setText("Остановка и удаление файлов...")
            grabber.ABORT_DOWNLOAD = True
            
            # Unpause if paused so thread can exit
            if grabber.PAUSE_DOWNLOAD:
                grabber.PAUSE_DOWNLOAD = False
                if psutil and grabber.CURRENT_YTDLP_PID:
                    try: psutil.Process(grabber.CURRENT_YTDLP_PID).resume()
                    except: pass

    def start_download(self):
        url = self.url_input.text().strip()
        data_dir = self.dir_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Ошибка", "Пожалуйста, введите ссылку на тренинг.")
            return
            
        self.start_btn.setEnabled(False)
        self.progress_panel.show()
        self.status_label.setText("Подготовка...")
        self.progress_bar.setValue(0)
        self.speed_label.setText("Скорость: -")
        self.eta_label.setText("Осталось: -")
        self.log_output.clear()
        
        self.thread = WorkerThread(url, data_dir)
        self.thread.log_signal.connect(self.append_log)
        self.thread.progress_signal.connect(self.update_progress)
        self.thread.finished_signal.connect(self.download_finished)
        self.thread.start()

    def update_progress(self, text, speed, eta, percent):
        self.status_label.setText(text)
        self.speed_label.setText(f"Скорость: {speed}")
        self.eta_label.setText(f"Осталось: {eta}")
        self.progress_bar.setValue(percent)

    def append_log(self, msg):
        self.log_output.append(msg)
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def download_finished(self, success):
        self.start_btn.setEnabled(True)
        self.pause_btn.setText("⏸ Пауза") # Reset
        if not success:
            self.progress_bar.setValue(0)
            self.speed_label.setText("Скорость: -")
            self.eta_label.setText("Осталось: -")

if __name__ == '__main__':
    if not psutil:
        print("Внимание: библиотека psutil не установлена. Кнопка Пауза может не работать корректно на Windows.")
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
