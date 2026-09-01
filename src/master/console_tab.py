"""
console_tab.py - Terminal emulator for serial port (COM3)
"""

import serial
import serial.tools.list_ports
import threading
import asyncio
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTextEdit, QLineEdit, QComboBox, QLabel,
                             QGroupBox, QMessageBox, QSpinBox)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QTextCursor, QFont


class SerialReaderThread(QThread):
    """Поток для чтения данных из последовательного порта"""
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, port, baudrate):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.serial_conn = None
        self.running = False
    
    def stop(self):
        """Останавливает поток"""
        self.running = False
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except:
                pass
        self.wait()
    
    def run(self):
        self.running = True
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1
            )
            
            while self.running:
                if self.serial_conn and self.serial_conn.is_open:
                    if self.serial_conn.in_waiting > 0:
                        data = self.serial_conn.read(self.serial_conn.in_waiting)
                        try:
                            text = data.decode('utf-8', errors='replace')
                            self.data_received.emit(text)
                        except:
                            self.data_received.emit(str(data))
                self.msleep(50)
                
        except serial.SerialException as e:
            self.error_occurred.emit(f"Serial error: {str(e)}")
        except Exception as e:
            self.error_occurred.emit(f"Error: {str(e)}")
        finally:
            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.close()
                except:
                    pass
    
    def write(self, data):
        """Отправить данные в порт"""
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.write(data.encode('utf-8'))
                return True
            except Exception as e:
                self.error_occurred.emit(f"Write error: {str(e)}")
                return False
        return False


class ConsoleTab(QWidget):
    """Вкладка для управления через последовательный порт"""
    
    def __init__(self, controller=None):
        super().__init__()
        self.controller = controller
        self.serial_thread = None
        self.input_buffer = ""
        self.ports_to_show = []
        self.setup_ui()
        self.setup_connections()
        self.refresh_ports()
        
    def setup_ui(self):
        """Настройка интерфейса"""
        layout = QVBoxLayout(self)
        
        # Панель настроек
        settings_group = QGroupBox("Serial Port Settings")
        settings_layout = QHBoxLayout(settings_group)
        
        # Выбор порта
        settings_layout.addWidget(QLabel("Порт:"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(120)
        self.port_combo.setEditable(True)
        self.port_combo.addItems(["COM1", "COM2", "COM3", "COM4", "COM5", "/dev/ttyUSB0", "/dev/ttyS0"])
        settings_layout.addWidget(self.port_combo)
        
        self.refresh_ports_btn = QPushButton("🔄 Обновить")
        self.refresh_ports_btn.setMaximumWidth(80)
        settings_layout.addWidget(self.refresh_ports_btn)
        
        # Скорость
        settings_layout.addWidget(QLabel("Baudrate:"))
        self.baudrate_combo = QComboBox()
        self.baudrate_combo.addItems(["9600", "19200", "38400", "57600", "115200", "230400"])
        self.baudrate_combo.setCurrentText("115200")
        self.baudrate_combo.setEditable(True)
        settings_layout.addWidget(self.baudrate_combo)
        
        # Кнопки управления
        self.connect_btn = QPushButton("🔌 Подключиться")
        self.connect_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        settings_layout.addWidget(self.connect_btn)
        
        self.disconnect_btn = QPushButton("🔌 Отключиться")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.setStyleSheet("background-color: #f44336; color: white;")
        settings_layout.addWidget(self.disconnect_btn)
        
        settings_layout.addStretch()
        layout.addWidget(settings_group)
        
        # Область вывода (терминал)
        output_group = QGroupBox("Консольный вывод")
        output_layout = QVBoxLayout(output_group)
        
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setFont(QFont("Courier New", 10))
        self.output_text.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        output_layout.addWidget(self.output_text)
        
        layout.addWidget(output_group)
        
        # Область ввода
        input_group = QGroupBox("Ввод команды")
        input_layout = QHBoxLayout(input_group)
        
        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Введите команду и нажмите Enter...")
        self.input_line.setEnabled(False)
        input_layout.addWidget(self.input_line)
        
        self.send_btn = QPushButton("Отправить")
        self.send_btn.setEnabled(False)
        self.send_btn.setMinimumWidth(80)
        input_layout.addWidget(self.send_btn)
        
        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.setMinimumWidth(80)
        input_layout.addWidget(self.clear_btn)

        self.clean_all_btn = QPushButton("🗑 Очистить все порты (1-8)")
        self.clean_all_btn.setToolTip("Очистить статистику для портов 1-8")
        self.clean_all_btn.clicked.connect(self.clean_all_ports)
        
        self.show_all_btn = QPushButton("📊 Показать все порты (1-8)")
        self.show_all_btn.setToolTip("Показать статистику для портов 1-8")
        self.show_all_btn.clicked.connect(self.show_all_ports)
        
        input_layout.addWidget(self.clean_all_btn)
        input_layout.addWidget(self.show_all_btn)
        
        layout.addWidget(input_group)
        
        # Статус бар
        self.status_label = QLabel("Отключен")
        self.status_label.setStyleSheet("color: red;")
        layout.addWidget(self.status_label)
        
        # Таймер для обновления
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.check_connection)
        self.update_timer.start(1000)
    
    def clean_all_ports(self):
        """Очистка статистики для портов 1-8 (с задержкой)"""
        if not self.serial_thread:
            self.log_output("❌ Не соединен с COM портом. Подключите сначала.")
            return
        
        self.log_output("🧹 Очистка статистики портов 1-8...")
        cmd = "clear statistics ports all"
        self.serial_thread.write(cmd + "\r\n")
        self.log_output(f"> {cmd}", is_command=True)

    def show_all_ports(self):
        """Показать статистику для портов 1-8 (с задержкой)"""
        if not self.serial_thread:
            self.log_output("❌ Не соединен с COM портом. Подключите сначала.")
            return
        
        self.log_output("📊 Запрос статистики портов 1-8...")
        self.ports_to_show = list(range(1, 9))
        self._send_next_show_command()

    def _send_next_show_command(self):
        """Отправка следующей команды show"""
        if not self.ports_to_show:
            self.log_output("✅ Команда show отправлена на все порты")
            return
        
        port = self.ports_to_show.pop(0)
        cmd = f"show statistics port {port}"
        self.serial_thread.write(cmd + "\r\n")
        self.log_output(f"> {cmd}", is_command=True)
        
        QTimer.singleShot(1500, self._send_next_show_command)
        
    def setup_connections(self):
        """Настройка соединений"""
        self.refresh_ports_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.connect_serial)
        self.disconnect_btn.clicked.connect(self.disconnect_serial)
        self.send_btn.clicked.connect(self.send_command)
        self.clear_btn.clicked.connect(self.clear_output)
        self.input_line.returnPressed.connect(self.send_command)
    
    def refresh_ports(self):
        """Обновить список доступных портов"""
        current_port = self.port_combo.currentText()
        self.port_combo.clear()
        
        try:
            ports = serial.tools.list_ports.comports()
            for port in ports:
                self.port_combo.addItem(f"{port.device} - {port.description}")
            
            if not ports:
                for port in ["COM1", "COM2", "COM3", "COM4", "COM5"]:
                    self.port_combo.addItem(port)
                    
        except Exception as e:
            print(f"Error listing ports: {e}")
            for port in ["COM1", "COM2", "COM3", "COM4", "COM5"]:
                self.port_combo.addItem(port)
        
        index = self.port_combo.findText(current_port)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)
    
    def connect_serial(self):
        """Подключение к последовательному порту"""
        port = self.port_combo.currentText().split(' - ')[0]
        baudrate = int(self.baudrate_combo.currentText())
        
        try:
            self.serial_thread = SerialReaderThread(port, baudrate)
            self.serial_thread.data_received.connect(self.on_data_received)
            self.serial_thread.error_occurred.connect(self.on_error)
            self.serial_thread.start()
            
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.input_line.setEnabled(True)
            self.send_btn.setEnabled(True)
            
            self.status_label.setText(f"Connected to {port} at {baudrate} baud")
            self.status_label.setStyleSheet("color: green;")
            
            self.log_output(f"--- Connected to {port} at {baudrate} baud ---")
            
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Failed to connect: {str(e)}")
            self.log_output(f"--- Connection failed: {str(e)} ---")
    
    def disconnect_serial(self):
        """Отключение от последовательного порта"""
        if self.serial_thread:
            self.serial_thread.stop()
            self.serial_thread = None
        
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.input_line.setEnabled(False)
        self.send_btn.setEnabled(False)
        
        self.status_label.setText("Отключён")
        self.status_label.setStyleSheet("color: red;")
        
        self.log_output("--- Отключён ---")
    
    def send_command(self):
        """Отправить команду"""
        if not self.serial_thread:
            return
        
        command = self.input_line.text()
        if not command:
            return
        
        if self.serial_thread.write(command + "\r\n"):
            self.input_line.clear()
        else:
            self.log_output("--- Ошибка отправления команды ---")
    
    def on_data_received(self, data):
        """Обработка полученных данных"""
        self.log_output(data, is_response=True)
    
    def on_error(self, error_msg):
        """Обработка ошибок"""
        self.log_output(f"--- ОШИБКА: {error_msg} ---")
        self.status_label.setText(f"Ошибка: {error_msg}")
        self.status_label.setStyleSheet("color: orange;")
    
    def log_output(self, text, is_command=False, is_response=False):
        """Вывод текста в консоль (без QTextCursor в потоках)"""
        # Используем обычный append с HTML-разметкой
        if is_command:
            self.output_text.append(f'<span style="color: #00ffff;">{text}</span>')
        elif is_response:
            self.output_text.append(f'<span style="color: #ffffff;">{text}</span>')
        else:
            self.output_text.append(f'<span style="color: #888888;">{text}</span>')
        
        # Прокручиваем вниз
        self.output_text.moveCursor(QTextCursor.MoveOperation.End)
        
        # Ограничиваем размер буфера
        if len(self.output_text.toPlainText()) > 100000:
            cursor = self.output_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor, 100)
            cursor.removeSelectedText()
    
    def clear_output(self):
        """Очистка вывода"""
        self.output_text.clear()
        self.log_output("--- Консоль очищена ---")
    
    def check_connection(self):
        """Проверка состояния соединения"""
        if self.serial_thread and not self.serial_thread.isRunning():
            self.disconnect_serial()
            self.log_output("--- Соединение потеряно ---")