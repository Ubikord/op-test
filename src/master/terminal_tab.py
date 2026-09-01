"""
terminal_tab.py
Вкладка с терминалами для управления агентами на R2S.
"""
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTabWidget, QTextEdit, QLineEdit, QGroupBox, QMessageBox, QApplication,
    QSplitter
)
from PyQt5.QtGui import QFont, QColor, QTextCursor
import subprocess
import threading
import time


class SSHCommandThread(QThread):
    """Поток для выполнения SSH-команды."""
    output_received = pyqtSignal(str, str)
    finished_signal = pyqtSignal(str, int)
    
    def __init__(self, hostname: str, command: str, label: str = ""):
        super().__init__()
        self.hostname = hostname
        self.command = command
        self.label = label or hostname
        self._running = True
        self.process = None
    
    def stop(self):
        """Останавливает выполнение."""
        self._running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.kill()  # Принудительно убиваем
            except:
                pass
    
    def run(self):
        try:
            cmd = [
                "ssh",
                "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=no",
                f"root@{self.hostname}",
                self.command
            ]
            
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            for line in iter(self.process.stdout.readline, ''):
                if not self._running:
                    try:
                        self.process.terminate()
                        self.process.kill()
                    except:
                        pass
                    break
                if line:
                    self.output_received.emit(self.label, line.rstrip())
            
            try:
                self.process.stdout.close()
            except:
                pass
            
            return_code = self.process.wait()
            self.finished_signal.emit(self.label, return_code)
            
        except Exception as e:
            self.output_received.emit(self.label, f"❌ Ошибка: {str(e)}")
            self.finished_signal.emit(self.label, -1)
        finally:
            self.process = None


class TerminalWidget(QWidget):
    """Виджет терминала для одного хоста."""
    
    def __init__(self, hostname: str, label: str = ""):
        super().__init__()
        self.hostname = hostname
        self.label = label or hostname
        self.thread = None
        self.command_history = []
        self.history_index = -1
        self._is_executing = False
        self.setup_ui()
        self.check_connection()
        self.connection_timer = QTimer()
        self.connection_timer.timeout.connect(self.check_connection)
        self.connection_timer.start(5000)
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Заголовок
        header_layout = QHBoxLayout()
        self.status_led = QLabel("●")
        self.status_led.setStyleSheet("color: #888888; font-size: 16px;")
        header_layout.addWidget(self.status_led)
        
        self.header_label = QLabel(f"{self.label} ({self.hostname})")
        self.header_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        header_layout.addWidget(self.header_label)
        
        header_layout.addStretch()
        
        self.connection_status = QLabel("Проверка...")
        self.connection_status.setStyleSheet("color: #888888; font-size: 10px;")
        header_layout.addWidget(self.connection_status)
        
        layout.addLayout(header_layout)
        
        # Терминал
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setFont(QFont("Consolas", 10))
        self.terminal.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                font-family: Consolas, monospace;
                font-size: 10pt;
            }
        """)
        layout.addWidget(self.terminal)
        
        # Строка ввода
        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(0, 5, 0, 0)
        
        self.input_prefix = QLabel(f"{self.label}$")
        self.input_prefix.setStyleSheet("color: #4caf50; font-weight: bold;")
        input_layout.addWidget(self.input_prefix)
        
        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Введите команду...")
        self.input_line.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                padding: 4px;
                font-family: Consolas, monospace;
                font-size: 10pt;
            }
            QLineEdit:focus {
                border: 1px solid #4caf50;
            }
        """)
        self.input_line.returnPressed.connect(self.execute_input)
        input_layout.addWidget(self.input_line)
        
        self.btn_send = QPushButton("⏎")
        self.btn_send.setFixedWidth(30)
        self.btn_send.clicked.connect(self.execute_input)
        self.btn_send.setToolTip("Отправить команду")
        input_layout.addWidget(self.btn_send)
        
        layout.addLayout(input_layout)
        
        # Статус
        self.status_label = QLabel("Готов")
        self.status_label.setStyleSheet("color: #888888; padding: 2px; font-size: 9px;")
        layout.addWidget(self.status_label)
    
    def check_connection(self):
        """Проверяет SSH-соединение."""
        def check():
            try:
                cmd = ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", f"root@{self.hostname}", "echo OK"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and "OK" in result.stdout:
                    self.status_led.setStyleSheet("color: #4caf50; font-size: 16px;")
                    self.connection_status.setText("✅ Подключен")
                    self.connection_status.setStyleSheet("color: #4caf50; font-size: 10px;")
                else:
                    self.status_led.setStyleSheet("color: #f44336; font-size: 16px;")
                    self.connection_status.setText("❌ Нет доступа")
                    self.connection_status.setStyleSheet("color: #f44336; font-size: 10px;")
            except:
                self.status_led.setStyleSheet("color: #f44336; font-size: 16px;")
                self.connection_status.setText("❌ Ошибка")
                self.connection_status.setStyleSheet("color: #f44336; font-size: 10px;")
        
        threading.Thread(target=check, daemon=True).start()
    
    def execute_input(self):
        command = self.input_line.text().strip()
        if not command:
            return
        self.input_line.clear()
        self.execute(command)
    
    def execute(self, command: str):
        """Выполняет команду с правильной остановкой потока."""
        # Если уже выполняется — отменяем
        if self._is_executing:
            return
        
        self._is_executing = True
        
        # Останавливаем предыдущий поток с ожиданием
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            # Ждём завершения потока (с таймаутом)
            if not self.thread.wait(3000):
                # Если не завершился — принудительно завершаем
                self.thread.terminate()
                self.thread.wait(1000)
            self.thread = None
        
        # Добавляем в историю
        self.command_history.append(command)
        self.history_index = len(self.command_history)
        
        # Отображаем команду
        self.terminal.append(f"\n┌─ {self.label} ────────────────────────────────")
        self.terminal.append(f"│ $ {command}")
        self.terminal.append("└─────────────────────────────────────────────")
        
        self.status_label.setText("Выполняется...")
        self.status_label.setStyleSheet("color: #ffaa00; padding: 2px; font-size: 9px;")
        self.terminal.moveCursor(QTextCursor.MoveOperation.End)
        
        # Создаём и запускаем новый поток
        self.thread = SSHCommandThread(self.hostname, command, self.label)
        self.thread.finished_signal.connect(self.on_finished)
        self.thread.output_received.connect(self.on_output)
        self.thread.start()
    
    def on_output(self, label: str, line: str):
        if "✅" in line or "OK" in line or "success" in line.lower():
            line = f"<span style='color: #4caf50;'>{line}</span>"
        elif "❌" in line or "error" in line.lower() or "failed" in line.lower():
            line = f"<span style='color: #ffffff;'>{line}</span>"
        elif "⚠️" in line or "warning" in line.lower():
            line = f"<span style='color: #ffaa00;'>{line}</span>"
        elif "INFO" in line or "info" in line.lower():
            line = f"<span style='color: #64b5f6;'>{line}</span>"
        
        self.terminal.append(line)
        self.terminal.moveCursor(QTextCursor.MoveOperation.End)
    
    def on_finished(self, label: str, return_code: int):
        self._is_executing = False
        if return_code == 0:
            self.status_label.setText("✅ Готово")
            self.status_label.setStyleSheet("color: #4caf50; padding: 2px; font-size: 9px;")
        else:
            self.status_label.setText(f"❌ Ошибка (код: {return_code})")
            self.status_label.setStyleSheet("color: #f44336; padding: 2px; font-size: 9px;")
        self.thread = None
    
    def clear(self):
        self.terminal.clear()
        self.status_label.setText("Готов")
        self.status_label.setStyleSheet("color: #888888; padding: 2px; font-size: 9px;")
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Up:
            if self.history_index > 0:
                self.history_index -= 1
                self.input_line.setText(self.command_history[self.history_index])
        elif event.key() == Qt.Key.Key_Down:
            if self.history_index < len(self.command_history) - 1:
                self.history_index += 1
                self.input_line.setText(self.command_history[self.history_index])
            else:
                self.history_index = len(self.command_history)
                self.input_line.clear()
        else:
            super().keyPressEvent(event)
    
    def closeEvent(self, event):
        """При закрытии виджета останавливаем поток."""
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.thread.wait(3000)
        event.accept()


class TerminalTab(QWidget):
    """Вкладка с терминалами для всех R2S."""
    
    def __init__(self, hosts: dict):
        super().__init__()
        self.hosts = hosts
        self.terminal_widgets = {}
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Кнопки
        btn_layout = QHBoxLayout()
        
        self.btn_start = QPushButton("▶️ Запустить агентов")
        self.btn_start.clicked.connect(self.start_all_agents)
        self.btn_start.setStyleSheet("""
            QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px; }
            QPushButton:hover { background-color: #45a049; }
        """)
        btn_layout.addWidget(self.btn_start)
        
        self.btn_stop = QPushButton("⏹ Остановить агентов")
        self.btn_stop.clicked.connect(self.stop_all_agents)
        self.btn_stop.setStyleSheet("""
            QPushButton { background-color: #f44336; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px; }
            QPushButton:hover { background-color: #da190b; }
        """)
        btn_layout.addWidget(self.btn_stop)
        
        self.btn_status = QPushButton("🔍 Проверить статус")
        self.btn_status.clicked.connect(self.check_status_all)
        btn_layout.addWidget(self.btn_status)
        
        self.btn_clear = QPushButton("🗑 Очистить все")
        self.btn_clear.clicked.connect(self.clear_all)
        btn_layout.addWidget(self.btn_clear)
        
        btn_layout.addStretch()
        self.status_label = QLabel("Готов")
        self.status_label.setStyleSheet("color: #888888;")
        btn_layout.addWidget(self.status_label)
        
        layout.addLayout(btn_layout)
        
        # Вкладки с терминалами
        self.terminal_tabs = QTabWidget()
        self.terminal_tabs.setTabsClosable(False)
        self.terminal_tabs.setDocumentMode(True)
        self.terminal_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #3d3d3d; border-radius: 3px; }
            QTabBar::tab { padding: 6px 12px; background-color: #2d2d2d; color: #d4d4d4; border: 1px solid #3d3d3d; border-bottom: none; }
            QTabBar::tab:selected { background-color: #1e1e1e; color: #ffffff; border-bottom: 2px solid #4caf50; }
        """)
        
        if not self.hosts:
            empty = QWidget()
            empty_layout = QVBoxLayout(empty)
            label = QLabel("⚠️ Нет настроенных агентов в топологии")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("font-size: 16px; color: #f44336;")
            empty_layout.addWidget(label)
            self.terminal_tabs.addTab(empty, "Нет агентов")
        else:
            for name, ip in self.hosts.items():
                widget = TerminalWidget(ip, name)
                self.terminal_tabs.addTab(widget, f"📡 {name}")
                self.terminal_widgets[name] = widget
        
        layout.addWidget(self.terminal_tabs)
    
    def get_current_widget(self) -> TerminalWidget:
        idx = self.terminal_tabs.currentIndex()
        if idx >= 0:
            return self.terminal_tabs.currentWidget()
        return None
    
    def start_all_agents(self):
        reply = QMessageBox.question(self, "Запуск агентов", "Запустить агентов на всех R2S?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self.btn_start.setEnabled(False)
        self.status_label.setText("⏳ Запуск...")
        self.status_label.setStyleSheet("color: #ffaa00;")
        QApplication.processEvents()
        
        command = (
            "cd /root/op-test && "
            "pid=$(ps | grep 'python3 agent.py' | grep -v grep | awk '{print $1}') && "
            "[ -n \"$pid\" ] && kill $pid 2>/dev/null || true && "
            "python3 agent.py > agent.log 2>&1 & "
            "sleep 1 && "
            "ps | grep 'python3 agent.py' | grep -v grep && "
            "echo '✅ Агент запущен' || echo '❌ Ошибка запуска'"
        )
        
        for widget in self.terminal_widgets.values():
            widget.execute(command)
        
        self.btn_start.setEnabled(True)
        self.status_label.setText("✅ Запуск выполняется...")
        self.status_label.setStyleSheet("color: #4caf50;")
    
    def stop_all_agents(self):
        reply = QMessageBox.question(self, "Остановка агентов", "Остановить агентов на всех R2S?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self.status_label.setText("⏳ Остановка...")
        self.status_label.setStyleSheet("color: #ffaa00;")
        QApplication.processEvents()
        
        command = (
            "pid=$(ps | grep 'python3 agent.py' | grep -v grep | awk '{print $1}') && "
            "[ -n \"$pid\" ] && kill $pid && echo '✅ Агент остановлен' || "
            "echo '⚠️ Агент не был запущен'"
        )
        
        for widget in self.terminal_widgets.values():
            widget.execute(command)
        
        self.status_label.setText("✅ Остановка выполнена")
        self.status_label.setStyleSheet("color: #4caf50;")
    
    def check_status_all(self):
        self.status_label.setText("⏳ Проверка...")
        self.status_label.setStyleSheet("color: #ffaa00;")
        QApplication.processEvents()
        
        command = (
            "echo '=== Проверка агента ===' && "
            "if ps | grep 'python3 agent.py' | grep -v grep > /dev/null; then "
            "ps | grep 'python3 agent.py' | grep -v grep && echo '✅ Агент запущен'; "
            "else echo '❌ Агент НЕ запущен'; fi && "
            "echo '=== Лог (последние 5 строк) ===' && "
            "tail -5 /root/op-test/agent.log 2>/dev/null || echo 'Нет логов'"
        )
        
        for widget in self.terminal_widgets.values():
            widget.execute(command)
        
        self.status_label.setText("✅ Проверка выполнена")
        self.status_label.setStyleSheet("color: #4caf50;")
    
    def clear_all(self):
        for widget in self.terminal_widgets.values():
            widget.clear()
        self.status_label.setText("✅ Очищено")
        self.status_label.setStyleSheet("color: #4caf50;")
    
    def execute_current(self, command: str):
        widget = self.get_current_widget()
        if widget:
            widget.execute(command)