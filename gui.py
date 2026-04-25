import os
import sys
import logging
from pathlib import Path

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QTextEdit, 
                             QLabel, QFileDialog, QDialog)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile

import main as grabber  # Import backend logic

class WorkerThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, url, data_dir):
        super().__init__()
        self.url = url
        self.data_dir = data_dir

    def run(self):
        try:
            logger = logging.getLogger()
            for h in logger.handlers[:]:
                logger.removeHandler(h)
                
            class SignalHandler(logging.Handler):
                def __init__(self, signal):
                    super().__init__()
                    self.signal = signal
                def emit(self, record):
                    msg = self.format(record)
                    self.signal.emit(msg)

            handler = SignalHandler(self.log_signal)
            formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

            base_dir = Path(grabber.__file__).parent.resolve()
            session = grabber.get_session(base_dir)
            
            if self.url.startswith("http"):
                grabber.crawl_course(self.url, session, Path(self.data_dir))
                self.log_signal.emit("✅ Скачивание успешно завершено!")
            else:
                self.log_signal.emit("❌ Ошибка: Введите корректную ссылку (начинается с http).")
        except Exception as e:
            self.log_signal.emit(f"❌ Ошибка в процессе: {e}")
        finally:
            self.finished_signal.emit()


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
            QPushButton {
                background-color: #1a73e8; color: white; padding: 10px 20px; 
                border-radius: 6px; font-weight: bold; font-size: 14px;
            }
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
        
        def on_cookie_added(cookie):
            self.cookies_loaded.append(cookie)
            
        cookie_store.cookieAdded.connect(on_cookie_added)
        cookie_store.loadAllCookies()
        
        # Даем 1 секунду на асинхронную загрузку всех кукисов
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
                
                # GetCourse fix for dot-prefixed domains in netscape format
                if not domain.startswith('.'):
                    domain = f".{domain}"
                    
                f.write(f"{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
        
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GetCourse Grabber")
        self.resize(700, 500)
        self.setStyleSheet("""
            QMainWindow { background-color: #f8f9fa; }
            QLabel { font-family: 'Segoe UI', Arial; font-size: 14px; color: #202124; }
            QLineEdit { 
                padding: 10px; border: 1px solid #dadce0; border-radius: 6px; 
                font-size: 14px; background: white;
            }
            QLineEdit:focus { border: 2px solid #1a73e8; }
            QPushButton {
                font-family: 'Segoe UI', Arial; font-weight: bold; font-size: 14px;
                border-radius: 6px; padding: 10px 15px; cursor: pointer;
            }
            QTextEdit {
                background-color: #282c34; color: #abb2bf; font-family: 'Consolas', monospace;
                font-size: 13px; border-radius: 6px; padding: 10px; border: none;
            }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(20)

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
        default_dir = str(Path(__file__).parent.resolve() / "data")
        self.dir_input.setText(default_dir)
        
        self.browse_btn = QPushButton("Выбрать")
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.setStyleSheet("background-color: #f1f3f4; color: #3c4043; border: 1px solid #dadce0;")
        self.browse_btn.clicked.connect(self.browse_directory)
        
        dir_row.addWidget(self.dir_input)
        dir_row.addWidget(self.browse_btn)
        dir_layout.addWidget(dir_label)
        dir_layout.addLayout(dir_row)
        main_layout.addLayout(dir_layout)

        # 3. Actions Section
        action_layout = QHBoxLayout()
        action_layout.setSpacing(15)
        
        self.login_btn = QPushButton("🔑 Войти в GetCourse")
        self.login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_btn.setStyleSheet("""
            QPushButton { background-color: #fff; color: #1a73e8; border: 1px solid #1a73e8; }
            QPushButton:hover { background-color: #e8f0fe; }
        """)
        self.login_btn.clicked.connect(self.open_login)
        
        self.start_btn = QPushButton("⬇️ Начать скачивание")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setStyleSheet("""
            QPushButton { background-color: #1a73e8; color: white; border: none; }
            QPushButton:hover { background-color: #1557b0; }
        """)
        self.start_btn.clicked.connect(self.start_download)
        
        action_layout.addWidget(self.login_btn)
        action_layout.addWidget(self.start_btn)
        main_layout.addLayout(action_layout)

        # 4. Logs Section
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
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
        if dir_path:
            self.dir_input.setText(dir_path)

    def open_login(self):
        dialog = LoginWindow(self)
        if dialog.exec():
            self.log_output.append("✅ Авторизация успешно сохранена!")
            self.check_cookies_status()

    def start_download(self):
        url = self.url_input.text().strip()
        data_dir = self.dir_input.text().strip()
        
        if not url:
            self.log_output.append("❌ Пожалуйста, введите ссылку на тренинг.")
            return
            
        self.start_btn.setEnabled(False)
        self.start_btn.setText("⏳ Загрузка...")
        self.start_btn.setStyleSheet("background-color: #8ab4f8; color: white; border: none;")
        self.log_output.clear()
        
        self.thread = WorkerThread(url, data_dir)
        self.thread.log_signal.connect(self.append_log)
        self.thread.finished_signal.connect(self.download_finished)
        self.thread.start()

    def append_log(self, msg):
        self.log_output.append(msg)
        # Scroll to bottom
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def download_finished(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("⬇️ Начать скачивание")
        self.start_btn.setStyleSheet("""
            QPushButton { background-color: #1a73e8; color: white; border: none; }
            QPushButton:hover { background-color: #1557b0; }
        """)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    # Modern font
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
