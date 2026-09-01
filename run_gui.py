"""
run_gui.py
PyQt5 GUI для master-приложения системы тестирования коммутаторов.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import sys
import threading
import uuid
import time
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QThreadPool, QRunnable, QObject, QEventLoop
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QSpinBox, QDoubleSpinBox, QRadioButton, QButtonGroup, QPushButton,
    QTableWidget, QTableWidgetItem, QLabel, QFileDialog, QMessageBox, QGroupBox,
    QHeaderView, QCheckBox, QTabWidget, QSplitter, QTextEdit, QListWidget, QListWidgetItem,
    QDialog, QInputDialog, QLineEdit
)

from src.master.protocol_client import AgentClient, wait_for_result
from src.master.test_runner import EndpointRef, run_pair_test, run_group_test, find_common_vlan
from src.master.rate_search import find_max_no_loss_rate
from src.master.terminal_tab import TerminalTab
from src.master.console_tab import ConsoleTab

FCS_OVERHEAD = 4


# ============================================================================
# TestWorker
# ============================================================================
class TestWorker(QThread):
    finished_ok = pyqtSignal(dict)
    finished_err = pyqtSignal(str)
    progress = pyqtSignal(str)
    log_message = pyqtSignal(str)
    autoprobe_started = pyqtSignal()
    autoprobe_finished = pyqtSignal()
    def __init__(self, sender: EndpointRef, receiver: EndpointRef, params: dict, group_name: str = "", dst_mac_override: str = None, dst_type: int = 0):
        super().__init__()
        self.sender = sender
        self.receiver = receiver
        self.params = params
        self.group_name = group_name
        self.dst_mac_override = dst_mac_override
        self.dst_type = dst_type
        self._stop_requested = False
        self._sender_client = None
        self._receiver_client = None
    
    def stop(self):
        self._stop_requested = True
        if self._sender_client:
            try:
                self._sender_client.stop_all()
            except:
                pass
        if self._receiver_client:
            try:
                self._receiver_client.stop_all()
            except:
                pass

    def run(self):
        try:
            common_vlan = find_common_vlan([self.sender, self.receiver])
            if common_vlan is None:
                self.progress.emit(f"⚠️ {self.sender.slave}:{self.sender.iface} и {self.receiver.slave}:{self.receiver.iface} не имеют общего VLAN (или VLAN не заданы).")
            else:
                self.progress.emit(f"ℹ️ Общий VLAN: {common_vlan}")

            self._sender_client = AgentClient(self.sender.host)
            self._receiver_client = AgentClient(self.receiver.host)

            test_id = str(uuid.uuid4())[:8]
            self.progress.emit(f"[{self.group_name}] Запуск теста {test_id}...")

            # === АВТОПОДБОР СКОРОСТИ ===
            if self.params.get("auto_rate"):
                self.autoprobe_started.emit()
                self.progress.emit("⏳ Автоподбор скорости... (остановка недоступна)")
                
                def trial(rate_pps: int) -> dict:
                    # Временный тест для поиска скорости
                    res = run_pair_test(
                        self.sender, self.receiver,
                        self.params["size_mode"], self.params["size"],
                        self.params["size_min"], self.params["size_max"],
                        rate_pps,
                        duration_s=self.params.get("probe_duration_s", 10.0),
                        dst_mac_override=self.dst_mac_override,
                        dst_type=self.dst_type,
                    )
                    sent = res["sender"].get("packets_sent", 0)
                    lost = res["receiver"].get("packets_lost", 0)
                    return {"packets_sent": sent, "packets_lost": lost, "_full": res}
                
                search = find_max_no_loss_rate(
                    trial,
                    min_rate_pps=self.params.get("rate_min", 1000),
                    max_rate_pps=self.params.get("rate_max", 100000),
                )
                
                found_rate = search.rate_pps

                from datetime import datetime, timedelta

                if self.params.get("packet_count", 0) > 0:
                    seconds = self.params["packet_count"] / max(found_rate, 1)
                else:
                    seconds = self.params.get("duration_s", 5.0)

                eta = datetime.now() + timedelta(seconds=seconds)
                eta_str = eta.strftime("%H:%M:%S")

                # Вывод в лог через отдельный сигнал
                self.log_message.emit(f"✅ Найдена скорость: {found_rate} pps (за {search.iterations} итераций)")
                self.log_message.emit(f"⏳ Тест запущен, завершится в {eta_str}")

                # Статусная строка (кратко)
                self.progress.emit(f"✅ Найдена скорость: {found_rate} pps, тест завершится в {eta_str}")

                if search.no_common_vlan:
                    self.progress.emit(f"⚠️ 100% потерь на минимальной скорости ({found_rate} pps) - вероятно, нет общего VLAN")
                    # Используем результат пробного теста как финальный
                    trial_result = search.last_trial_result
                    # Извлекаем полный результат из пробного теста
                    if "_full" in trial_result:
                        result = trial_result["_full"]
                        result["auto_rate_pps"] = found_rate
                        result["auto_rate_iterations"] = search.iterations
                        result["no_common_vlan"] = True
                        self.finished_ok.emit(result)
                        return
                    else:
                        # Если нет полного результата, запускаем финальный тест с минимальной скоростью
                        found_rate = self.params.get("rate_min", 1000)
                        self.progress.emit(f"ℹ️ Запуск финального теста с минимальной скоростью {found_rate} pps")
                        rate_to_use = found_rate
                        auto_rate_result = True
                else:
                    self.progress.emit(f"✅ Найдена скорость: {found_rate} pps")
                    rate_to_use = found_rate
                    auto_rate_result = True
                
                self.autoprobe_finished.emit()
            else:
                rate_to_use = self.params["rate_pps"]
                auto_rate_result = False
                
            # Запускаем receiver
            recv_spec = {
                "test_id": test_id,
                "role": "receiver",
                "iface": self.receiver.iface,
                "size_mode": self.params["size_mode"],
                "duration_s": 0.0,
            }
            if self.params["size_mode"] == "random":
                recv_spec["size_min"] = self.params["size_min"]
                recv_spec["size_max"] = self.params["size_max"]
            else:
                recv_spec["size"] = self.params["size"]

            recv_test_id = self._receiver_client.start_test(recv_spec)
            time.sleep(0.3)

            if self._stop_requested:
                self.progress.emit(f"[{self.group_name}] Тест остановлен до запуска sender")
                self._receiver_client.stop_test(recv_test_id)
                return

            # Запускаем sender
            send_spec = {
                "test_id": test_id,
                "role": "sender",
                "iface": self.sender.iface,
                "dst_mac": self.dst_mac_override if self.dst_mac_override else self.receiver.mac,
                "dst_type": self.dst_type,
                "src_mac": self.sender.mac,
                "size_mode": self.params["size_mode"],
                "rate_pps": rate_to_use,
                "duration_s": self.params.get("duration_s", 5.0),
                "packet_count": self.params.get("packet_count", 0),
            }
            if self.params["size_mode"] == "random":
                send_spec["size_min"] = self.params["size_min"]
                send_spec["size_max"] = self.params["size_max"]
            else:
                send_spec["size"] = self.params["size"]

            send_test_id = self._sender_client.start_test(send_spec)

            # Ждём результаты
            if self.params.get("packet_count", 0):
                theoretical_time = self.params["packet_count"] / max(self.params["rate_pps"], 1)
                max_wait = theoretical_time * 1.2 + 60.0  # 20% запас + 60 секунд
            else:
                max_wait = self.params.get("duration_s", 5.0) * 1.2 + 60.0

            poll_interval = 0.5
            waited = 0.0
            sender_result = None
            receiver_result = None

            while waited < max_wait and not self._stop_requested:
                try:
                    resp = self._sender_client.get_result(send_test_id)
                    if resp.get("finished"):
                        sender_result = resp["result"]
                        break
                except:
                    pass
                time.sleep(poll_interval)
                waited += poll_interval

            # Если тест не завершился — принудительно останавливаем
            if self._stop_requested or not sender_result:
                if self._sender_client and send_test_id:
                    try:
                        self._sender_client.stop_test(send_test_id)
                    except:
                        pass
                time.sleep(0.5)
                try:
                    if not sender_result:
                        resp = self._sender_client.get_result(send_test_id)
                        if resp.get("finished"):
                            sender_result = resp["result"]
                except:
                    pass

            # Останавливаем receiver
            if self._receiver_client and recv_test_id:
                try:
                    self._receiver_client.stop_test(recv_test_id)
                except:
                    pass
            time.sleep(2)
            try:
                resp = self._receiver_client.get_result(recv_test_id)
                if resp.get("finished"):
                    receiver_result = resp["result"]
            except:
                pass

            # ============ КЛЮЧЕВОЕ: правильный расчёт потерь ============
            if sender_result:
                sender_packets = sender_result.get("packets_sent", 0)
                sender_bytes = sender_result.get("bytes_sent", 0)
                
                if receiver_result:
                    # Извлекаем статистику из sender_stats
                    sender_stats = receiver_result.get("sender_stats", [])
                    reported_lost = 0
                    receiver_packets = 0
                    receiver_bytes = 0
                    packets_expected = 0
                    
                    for stat in sender_stats:
                        reported_lost += stat.get("packets_lost", 0)
                        receiver_packets += stat.get("packets_received", 0)
                        receiver_bytes += stat.get("bytes_received", 0)
                        packets_expected += stat.get("packets_expected", 0)
                    
                    if packets_expected == 0:
                        packets_expected = sender_packets
                    
                    reconciled_lost = max(0, sender_packets - receiver_packets)
                    receiver_lost = max(reported_lost, reconciled_lost)
                    
                    common_vlan = find_common_vlan([self.sender, self.receiver])
                    trailing_loss_detected = False

                    if self._stop_requested:
                        trailing_loss_detected = False
                    else:
                        if reconciled_lost > 0 and common_vlan is not None:
                            trailing_loss_detected = reconciled_lost > reported_lost
                            
                    if sender_packets > 0:
                        loss_pct = receiver_lost / sender_packets * 100.0
                    else:
                        loss_pct = 0.0
                    
                    receiver_data = {
                        "slave": self.receiver.slave,
                        "iface": self.receiver.iface,
                        "packets_received": receiver_packets,
                        "bytes_received": receiver_bytes + (receiver_packets * 4),
                        "packets_lost": receiver_lost,
                        "packets_expected": packets_expected,
                        "out_of_order": receiver_result.get("out_of_order", 0),
                        "duration_s": receiver_result.get("duration_s", 0),
                        "loss_pct": round(loss_pct, 3),
                        "trailing_loss_detected": trailing_loss_detected,
                        "nic_stats": receiver_result.get("nic_stats_delta", {}),
                    }
                else:
                    receiver_data = {
                        "slave": self.receiver.slave,
                        "iface": self.receiver.iface,
                        "packets_received": 0,
                        "bytes_received": 0,
                        "packets_lost": sender_packets,
                        "packets_expected": sender_packets,
                        "out_of_order": 0,
                        "duration_s": 0,
                        "loss_pct": 100.0,
                        "trailing_loss_detected": True,
                        "nic_stats": {},
                    }

                sender_data = {
                    "slave": self.sender.slave,
                    "iface": self.sender.iface,
                    "packets_sent": sender_packets,
                    "bytes_sent": sender_bytes + (sender_packets * 4),
                    "duration_s": sender_result.get("duration_s", 0),
                    "nic_stats": sender_result.get("nic_stats_delta", {}),
                }

                def has_nic_issue(stats):
                    error_fields = (
                        "rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
                        "rx_fifo_errors", "rx_over_errors", "tx_fifo_errors",
                        "rx_frame_errors", "rx_crc_errors", "collisions",
                    )
                    return any((stats.get(f) or 0) > 0 for f in error_fields)

                nic_issue = has_nic_issue(sender_data.get("nic_stats", {})) or has_nic_issue(receiver_data.get("nic_stats", {}))

                result = {
                    "test_id": test_id,
                    "rate_pps": rate_to_use,
                    "common_vlan": common_vlan,
                    "dst_type": self.dst_type,
                    "mac": self.sender.mac,
                    "sender": sender_data,
                    "receiver": receiver_data,
                    "group_name": self.group_name,
                    "is_stopped": self._stop_requested,
                    "incomplete": not receiver_result,
                    "nic_issue_detected": nic_issue,
                }
                if common_vlan is None:
                    result["vlan_warning"] = "Нет общего VLAN (или VLAN не заданы)"
                
                self.finished_ok.emit(result)
            else:
                self.finished_err.emit(f"Тест {test_id} не завершился и был остановлен")

        except Exception as e:
            self.autoprobe_finished.emit()
            self.finished_err.emit(str(e))


# ============================================================================
# GroupTestSignals, GroupTestRunnable, GroupTestWorker
# ============================================================================
class GroupTestSignals(QObject):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    all_finished = pyqtSignal()
    progress = pyqtSignal(str)
    log_message = pyqtSignal(str)

class GroupTestRunnable(QRunnable):
    def __init__(self, sender, receiver, params, group_name, test_index, total_tests, dst_mac_override: str = None, dst_type: int = 0):
        super().__init__()
        self.sender = sender
        self.receiver = receiver
        self.params = params
        self.group_name = group_name
        self.test_index = test_index
        self.total_tests = total_tests
        self.dst_type = dst_type
        self.dst_mac_override = dst_mac_override
        self.signals = GroupTestSignals()
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if self.params.get("auto_rate"):
                print("DEBUG: Запуск автоподбора скорости")
                print(f"DEBUG: min_rate_pps={self.params.get('rate_min')}, max_rate_pps={self.params.get('rate_max')}")
                def trial(rate_pps: int) -> dict:
                    print(f"DEBUG: trial() вызван с rate_pps={rate_pps}")
                    res = run_pair_test(
                        self.sender, self.receiver,
                        self.params["size_mode"], self.params["size"],
                        self.params["size_min"], self.params["size_max"],
                        rate_pps,  # <-- пробная скорость
                        duration_s=self.params["probe_duration_s"],
                        dst_mac_override=self.dst_mac_override,
                        dst_type=self.dst_type,
                    )
                    sent = res["sender"].get("packets_sent", 0)
                    lost = res["receiver"].get("packets_lost", 0)
                    print(f"DEBUG: trial() результат: sent={sent}, lost={lost}")
                    return {"packets_sent": sent, "packets_lost": lost, "_full": res}
                
                search = find_max_no_loss_rate(
                    trial,
                    min_rate_pps=self.params["rate_min"],
                    max_rate_pps=self.params["rate_max"],
                )

                print(f"DEBUG: search.rate_pps = {search.rate_pps}")
                print(f"DEBUG: search.iterations = {search.iterations}")
                print(f"DEBUG: search.last_trial_result = {search.last_trial_result}")
          
                # Используем найденную скорость
                found_rate = search.rate_pps
                from datetime import datetime, timedelta

                if self.params.get("packet_count", 0) > 0:
                    seconds = self.params["packet_count"] / max(found_rate, 1)
                else:
                    seconds = self.params.get("duration_s", 5.0)

                eta = datetime.now() + timedelta(seconds=seconds)
                eta_str = eta.strftime("%H:%M:%S")

                # Вывод в лог через сигнал
                self.signals.log_message.emit(f"✅ [Группа] Найдена скорость: {found_rate} pps (за {search.iterations} итераций)")
                self.signals.log_message.emit(f"⏳ [Группа] Тест {self.test_index}/{self.total_tests} запущен, завершится в {eta_str}")

                print(f"DEBUG: found_rate = {found_rate}")
                result = run_pair_test(
                    self.sender, self.receiver,
                    self.params["size_mode"], self.params["size"],
                    self.params["size_min"], self.params["size_max"],
                    found_rate,  # <-- используем найденную скорость
                    duration_s=self.params.get("duration_s"),
                    packet_count=self.params.get("packet_count"),
                    dst_mac_override=self.dst_mac_override,
                    dst_type=self.dst_type,
                )
                print(f"DEBUG: результат основного теста: rate_pps={result.get('rate_pps')}")
                result["auto_rate_pps"] = found_rate
                result["auto_rate_iterations"] = search.iterations
            else:
                result = run_pair_test(
                    self.sender, self.receiver,
                    self.params["size_mode"], self.params["size"],
                    self.params["size_min"], self.params["size_max"],
                    self.params["rate_pps"],  # <-- фиксированная скорость
                    duration_s=self.params.get("duration_s"),
                    packet_count=self.params.get("packet_count"),
                    dst_mac_override=self.dst_mac_override,
                    dst_type=self.dst_type,
                )
            if result is None or not isinstance(result, dict):
                # Создаем результат из того, что есть
                result = {
                    "test_id": f"fallback_{self.test_index}",
                    "rate_pps": self.params.get("rate_pps", 0),
                    "common_vlan": find_common_vlan([self.sender, self.receiver]),
                    "dst_type": self.dst_type,
                    "sender_stats": [{
                        "mac": self.sender.mac,
                        "packets_received": 0,
                        "bytes_received": 0,
                        "packets_expected": 0,
                        "packets_lost": 0,
                        "out_of_order": 0,
                        "from_fallback": True
                    }],
                    "sender": {
                        "slave": self.sender.slave,
                        "iface": self.sender.iface,
                        "packets_sent": 0,
                        "bytes_sent": 0,
                        "duration_s": 0,
                        "nic_stats": {},
                    },
                    "receiver": {
                        "slave": self.receiver.slave,
                        "iface": self.receiver.iface,
                        "packets_received": 0,
                        "bytes_received": 0,
                        "packets_lost": 0,
                        "packets_expected": 0,
                        "out_of_order": 0,
                        "duration_s": 0,
                        "loss_pct": 0.0,
                        "trailing_loss_detected": False,
                        "nic_stats": {},
                    },
                    "nic_issue_detected": False,
                    "vlan_warning": "Ошибка выполнения теста, использованы фиктивные данные",
                    "is_fallback": True,
                }
            
            # Добавляем метаданные
            result["group_name"] = self.group_name
            result["test_index"] = self.test_index
            result["total_tests"] = self.total_tests
            
            result["is_stopped"] = self._stop_requested
            if self._stop_requested and "receiver" in result:
                result["receiver"]["trailing_loss_detected"] = False

            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.all_finished.emit()


class GroupTestWorker(QThread):
    finished_ok = pyqtSignal(list)
    finished_err = pyqtSignal(str)
    progress = pyqtSignal(str)
    test_complete = pyqtSignal(dict)
    autoprobe_started = pyqtSignal()
    autoprobe_finished = pyqtSignal()
    log_message = pyqtSignal(str)

    def __init__(self, endpoints: List[Tuple[EndpointRef, EndpointRef]], params: dict, group_name: str, dst_mac_override: str = None, dst_type: int = 0):
        super().__init__()
        self.endpoints = endpoints
        self.params = params
        self.group_name = group_name
        self.dst_mac_override = dst_mac_override
        self.dst_type = dst_type
        self.results = []
        self.errors = []
        self.thread_pool = QThreadPool.globalInstance()
        self.pending_tests = 0
        self.completed_tests = 0
        self.lock = threading.Lock()
        self._stop_requested = False
        self.runnables = []

    def stop(self):
        self._stop_requested = True
        self.progress.emit("⏹ Остановка группы тестов...")
        
        # Останавливаем тесты ПОСЛЕДОВАТЕЛЬНО
        for idx, runnable in enumerate(self.runnables):
            if hasattr(runnable, 'stop'):
                self.progress.emit(f"⏹ Остановка теста {idx+1}/{len(self.runnables)}...")
                runnable.stop()
                time.sleep(0.3)  # Даем время на завершение
        
        # Останавливаем все клиенты
        for endpoint in self.endpoints:
            sender, receiver = endpoint
            for host in [sender.host, receiver.host]:
                try:
                    client = AgentClient(host)
                    client.stop_all()
                except:
                    pass
        
        self.thread_pool.waitForDone(5000)

    def run(self):
        self.results.clear()
        self.errors.clear()
        self.completed_tests = 0
        self.pending_tests = len(self.endpoints)

        # === АВТОПОДБОР: блокируем кнопку ===
        if self.params.get("auto_rate"):
            self.autoprobe_started.emit()
            self.progress.emit("⏳ Автоподбор скорости для группы...")
        else:
            # Для фиксированной скорости тоже блокируем кнопку,
            # но разблокируем сразу после запуска тестов
            self.autoprobe_started.emit()
            self.autoprobe_finished.emit()

        for idx, (sender, receiver) in enumerate(self.endpoints, 1):
            runnable = GroupTestRunnable(
                sender, receiver, self.params,
                self.group_name, idx, self.pending_tests,
                dst_mac_override=self.dst_mac_override,
                dst_type=self.dst_type
            )
            runnable.signals.finished.connect(self.on_test_finished)
            runnable.signals.error.connect(self.on_test_error)
            runnable.signals.all_finished.connect(self.on_test_completed)
            runnable.signals.progress.connect(self.progress.emit)
            runnable.signals.log_message.connect(self.log_message.emit)
            self.runnables.append(runnable)
            self.thread_pool.start(runnable)

        # Если автоподбор был, разблокируем после завершения всех runnables
        if self.params.get("auto_rate"):
            # Ждём завершения
            loop = QEventLoop()
            self.finished_ok.connect(loop.quit)
            self.finished_err.connect(loop.quit)
            QTimer.singleShot(300000, loop.quit)
            loop.exec()
            self.autoprobe_finished.emit()
        else:
            # Для фиксированной скорости уже разблокировали
            pass

    def on_test_finished(self, result):
        with self.lock:
            self.results.append(result)
        self.test_complete.emit(result)
        self.progress.emit(f"[{self.group_name}] Тест {result['test_index']}/{result['total_tests']} завершён")

    def on_test_error(self, error_msg):
        with self.lock:
            self.errors.append(error_msg)
        self.progress.emit(f"[{self.group_name}] Ошибка: {error_msg}")

    def on_test_completed(self):
        with self.lock:
            self.completed_tests += 1
            if self.completed_tests == self.pending_tests:
                if self._stop_requested:
                    self.finished_ok.emit(self.results)
                elif self.errors:
                    self.finished_err.emit("\n".join(self.errors))
                else:
                    self.finished_ok.emit(self.results)


class MulticastGroupTestWorker(QThread):
    """Запускает multicast/broadcast тест для одной VLAN."""
    finished_ok = pyqtSignal(list)
    finished_err = pyqtSignal(str)
    progress = pyqtSignal(str)
    test_complete = pyqtSignal(dict)

    def __init__(self, interfaces: List[EndpointRef], vlan_id: int, params: dict, dst_type: int, 
                 group_name: str = "", all_senders: List[EndpointRef] = None):
        """
        interfaces: список интерфейсов для этой VLAN (receiver'ы)
        vlan_id: ID VLAN
        params: параметры теста
        dst_type: 1=multicast, 2=broadcast
        group_name: имя группы
        all_senders: список всех уникальных sender'ов для всех VLAN
        """
        super().__init__()
        self.interfaces = interfaces
        self.vlan_id = vlan_id
        self.params = params
        self.dst_type = dst_type
        self.group_name = group_name
        self.all_senders = all_senders or interfaces  # если не передан, используем interfaces
        self._stop_requested = False
        self.results = []
        self.errors = []
        self.clients = {}  # хост -> AgentClient

    def stop(self):
        self._stop_requested = True
        for client in self.clients.values():
            try:
                client.stop_all()
            except:
                pass

    def get_key(self, ep: EndpointRef) -> str:
        return f"{ep.slave}:{ep.iface}"

    def run(self):
        try:
            test_id = str(uuid.uuid4())[:8]
            self.progress.emit(f"📡 Запуск multicast теста для VLAN {self.vlan_id} (test_id={test_id})...")

            # Создаём клиентов для каждого уникального хоста
            hosts = {}
            for ep in self.interfaces:
                if ep.host not in hosts:
                    hosts[ep.host] = AgentClient(ep.host)
            self.clients = hosts

            for ep in self.all_senders:
                if ep.host not in self.clients:
                    self.clients[ep.host] = AgentClient(ep.host)
                    
            # Определяем dst_mac
            if self.dst_type == 1:
                dst_mac = "01:00:5E:00:00:01"
            else:
                dst_mac = "ff:ff:ff:ff:ff:ff"

            # 1. Запускаем receiver на всех интерфейсах VLAN
            recv_test_ids = {}
            recv_spec = {
                "test_id": test_id,
                "role": "receiver",
                "size_mode": self.params["size_mode"],
                "duration_s": 0.0,
            }
            if self.params["size_mode"] == "random":
                recv_spec["size_min"] = self.params["size_min"]
                recv_spec["size_max"] = self.params["size_max"]
            else:
                recv_spec["size"] = self.params["size"]

            for key, (ep, vlan_id) in self.all_receivers.items():
                client = self.clients[ep.host]
                recv_spec["iface"] = ep.iface
                try:
                    tid = client.start_test(recv_spec)
                    recv_key = self.get_key(ep, vlan_id)
                    recv_test_ids[recv_key] = (tid, ep, vlan_id)
                    self.progress.emit(f"✅ Receiver запущен на {ep.slave}:{ep.iface} (VLAN {vlan_id})")
                except Exception as e:
                    self.errors.append(f"Ошибка запуска receiver на {ep.slave}:{ep.iface} (VLAN {vlan_id}): {e}")
            time.sleep(0.5)

            if self._stop_requested:
                self.cleanup(recv_test_ids)
                self.finished_err.emit("Тест остановлен до запуска sender")
                return
            
            for ep in self.interfaces:
                client = hosts[ep.host]
                recv_spec["iface"] = ep.iface
                try:
                    tid = client.start_test(recv_spec)
                    key = self.get_key(ep)
                    recv_test_ids[key] = (tid, ep)
                    self.progress.emit(f"✅ Receiver запущен на {ep.slave}:{ep.iface}")
                except Exception as e:
                    self.errors.append(f"Ошибка запуска receiver на {ep.slave}:{ep.iface}: {e}")
            time.sleep(0.5)

            if self._stop_requested:
                self.cleanup(recv_test_ids)
                self.finished_err.emit("Тест остановлен до запуска sender")
                return

            # 2. Запускаем sender на ВСЕХ уникальных интерфейсах (один раз!)
            send_test_ids = {}
            send_spec = {
                "test_id": test_id,
                "role": "sender",
                "dst_mac": dst_mac,
                "size_mode": self.params["size_mode"],
                "rate_pps": self.params["rate_pps"],
                "duration_s": self.params.get("duration_s", 5.0),
                "dst_type": self.dst_type,  # передаём тип для pktgen
            }
            if self.params.get("packet_count", 0):
                send_spec["packet_count"] = self.params["packet_count"]
            if self.params["size_mode"] == "random":
                send_spec["size_min"] = self.params["size_min"]
                send_spec["size_max"] = self.params["size_max"]
            else:
                send_spec["size"] = self.params["size"]

            # Запускаем sender на всех уникальных интерфейсах
            for ep in self.all_senders:
                client = hosts[ep.host]
                send_spec["iface"] = ep.iface
                send_spec["src_mac"] = ep.mac
                try:
                    tid = client.start_test(send_spec)
                    key = self.get_key(ep)
                    send_test_ids[key] = (tid, ep)
                    self.progress.emit(f"✅ Sender запущен на {ep.slave}:{ep.iface}")
                except Exception as e:
                    self.errors.append(f"Ошибка запуска sender на {ep.slave}:{ep.iface}: {e}")

            # 3. Ждём завершения всех sender'ов
            max_wait = 120.0
            if self.params.get("packet_count", 0):
                theoretical_time = self.params["packet_count"] / max(self.params["rate_pps"], 1)
                max_wait = theoretical_time * 1.2 + 60.0  # 20% запас + 60 секунд
            else:
                max_wait = self.params.get("duration_s", 5.0) * 1.2 + 60.0

            # Собираем TX статистику для каждого sender'а
            sender_tx_results = {}  # mac -> tx_stats
            for key, (tid, ep) in send_test_ids.items():
                if tid is None:
                    continue
                client = hosts[ep.host]
                try:
                    result = wait_for_result(client, tid, max_wait=max_wait)
                    nic_stats = result.get("nic_stats_delta", {})
                    tx_stats = {k: v for k, v in nic_stats.items() if k.startswith("tx_")}
                    sender_tx_results[ep.mac] = tx_stats
                    self.progress.emit(f"✅ Sender на {ep.slave}:{ep.iface} завершён")
                except TimeoutError:
                    self.errors.append(f"Sender на {ep.slave}:{ep.iface} не завершился за {max_wait} с")
                    try:
                        client.stop_test(tid)
                    except:
                        pass

            # 4. Останавливаем все receiver'ы
            self.cleanup(recv_test_ids)

            # 5. Получаем результаты receiver'ов
            receiver_results = {}
            for key, (tid, ep) in recv_test_ids.items():
                if tid is None:
                    continue
                client = hosts[ep.host]
                try:
                    resp = client.get_result(tid)
                    if resp.get("finished"):
                        receiver_results[key] = resp["result"]
                        self.progress.emit(f"✅ Результат receiver на {ep.slave}:{ep.iface} получен")
                    else:
                        self.errors.append(f"Receiver на {ep.slave}:{ep.iface} не завершился")
                except Exception as e:
                    self.errors.append(f"Ошибка получения результата receiver на {ep.slave}:{ep.iface}: {e}")

            # 6. Формируем результаты для каждого receiver'а
            for key, recv_data in receiver_results.items():
                sender_stats = recv_data.get("sender_stats", [])
                recv_nic_stats = recv_data.get("nic_stats_delta", {})

                # Находим EP по ключу
                ep = None
                for e in self.interfaces:
                    if self.get_key(e) == key:
                        ep = e
                        break

                if ep is None:
                    continue

                # Фильтруем sender_stats: оставляем только те MAC, которые есть в all_senders
                # и которые принадлежат этой VLAN (имеют vlan_id в vlans)
                all_sender_macs = {s.mac for s in self.all_senders}
                # Для каждого sender'а проверяем, есть ли у него этот VLAN
                filtered_sender_stats = []
                for stat in sender_stats:
                    mac = stat.get("mac")
                    # Находим интерфейс по MAC
                    sender_ep = None
                    for s in self.all_senders:
                        if s.mac == mac:
                            sender_ep = s
                            break
                    if sender_ep and self.vlan_id in sender_ep.vlans:
                        filtered_sender_stats.append(stat)

                total_packets = sum(s.get("packets_received", 0) for s in filtered_sender_stats)
                total_bytes = sum(s.get("bytes_received", 0) for s in filtered_sender_stats)

                # Собираем TX статистику для отфильтрованных sender'ов
                sender_tx_stats = {}
                for stat in filtered_sender_stats:
                    mac = stat.get("mac")
                    if mac in sender_tx_results:
                        sender_tx_stats[mac] = sender_tx_results[mac]

                result = {
                    "test_id": test_id,
                    "rate_pps": self.params["rate_pps"],
                    "common_vlan": self.vlan_id,
                    "dst_type": self.dst_type,
                    "sender_stats": filtered_sender_stats,
                    "sender_tx_stats": sender_tx_stats,
                    "receiver_nic_stats": recv_nic_stats,
                    "sender": {
                        "slave": ep.slave,
                        "iface": ep.iface,
                        "mac": ep.mac,
                        "packets_sent": total_packets,
                        "bytes_sent": total_bytes + (total_packets * 4),
                        "duration_s": self.params.get("duration_s", 0),
                        "nic_stats": {},
                    },
                    "receiver": {
                        "slave": ep.slave,
                        "iface": ep.iface,
                        "packets_received": total_packets,
                        "bytes_received": total_bytes + (total_packets * 4),
                        "packets_lost": 0,
                        "packets_expected": 0,
                        "out_of_order": 0,
                        "duration_s": recv_data.get("duration_s", 0),
                        "loss_pct": 0.0,
                        "trailing_loss_detected": False,
                        "nic_stats": recv_nic_stats,
                    },
                    "nic_issue_detected": False,
                    "group_name": self.group_name,
                    "is_stopped": False,
                    "incomplete": False,
                }
                self.results.append(result)

            if self.errors:
                self.finished_err.emit("\n".join(self.errors))
            else:
                self.finished_ok.emit(self.results)

        except Exception as e:
            self.finished_err.emit(str(e))

    def cleanup(self, recv_test_ids):
        for key, (tid, ep) in recv_test_ids.items():
            if tid is None:
                continue
            client = self.clients.get(ep.host)
            if client:
                try:
                    client.stop_test(tid)
                except:
                    pass

class MulticastMasterWorker(QThread):
    """Запускает multicast/broadcast тест для всех VLAN одновременно с одним test_id."""
    finished_ok = pyqtSignal(list)
    finished_err = pyqtSignal(str)
    progress = pyqtSignal(str)
    test_complete = pyqtSignal(dict)
    autoprobe_started = pyqtSignal()
    autoprobe_finished = pyqtSignal()
    log_message = pyqtSignal(str)

    def __init__(self, vlan_to_interfaces: Dict[int, List[EndpointRef]], params: dict, dst_type: int, group_name: str = "", is_probe: bool = False):
        super().__init__()
        self.vlan_to_interfaces = vlan_to_interfaces
        self.params = params
        self.dst_type = dst_type
        self.group_name = group_name
        self.is_probe = is_probe
        self._stop_requested = False
        self.results = []
        self.errors = []
        self.clients = {}
        self.receiver_test_ids = {}

        # Собираем все уникальные интерфейсы для sender'ов
        self.all_senders = []
        seen_macs = set()
        for ifaces in vlan_to_interfaces.values():
            for ep in ifaces:
                if ep.mac not in seen_macs:
                    seen_macs.add(ep.mac)
                    self.all_senders.append(ep)

        # Собираем все уникальные интерфейсы для receiver'ов (по VLAN)
        self.all_receivers = {}
        for vlan_id, ifaces in vlan_to_interfaces.items():
            for ep in ifaces:
                key = (ep.slave, ep.iface, vlan_id)
                self.all_receivers[key] = (ep, vlan_id)

        self.total_tests = len(self.all_receivers)
        self.test_index_counter = 0

    def stop(self):
        """Останавливает multicast тест."""
        self._stop_requested = True
        self.progress.emit("⏹ Остановка multicast теста...")
        
        # Останавливаем ВСЕ тесты на ВСЕХ клиентах
        for host, client in self.clients.items():
            try:
                client.stop_all()
                self.progress.emit(f"⏹ Остановлены все тесты на {host}")
            except Exception as e:
                self.progress.emit(f"⚠️ Ошибка остановки на {host}: {e}")
        
        # Пауза для завершения (чтобы пакеты дошли)
        time.sleep(1.0)
        
        # Дополнительно останавливаем receiver'ы по их test_id (если есть)
        if hasattr(self, 'recv_test_ids'):
            for recv_key, (tid, ep, vlan_id) in self.recv_test_ids.items():
                try:
                    client = self.clients.get(ep.host)
                    if client:
                        client.stop_test(tid)
                except:
                    pass
        
        self.progress.emit("✅ Multicast тест остановлен")

    def get_key(self, ep: EndpointRef, vlan_id: int = None) -> str:
        if vlan_id is not None:
            return f"{ep.slave}:{ep.iface}:vlan{vlan_id}"
        return f"{ep.slave}:{ep.iface}"

    def get_ep_key(self, ep: EndpointRef) -> str:
        return f"{ep.slave}:{ep.iface}"

    def cleanup(self, recv_test_ids):
        for recv_key, (tid, ep, vlan_id) in recv_test_ids.items():
            if tid is None:
                continue
            client = self.clients.get(ep.host)
            if client:
                try:
                    client.stop_test(tid)
                except:
                    pass

    def _run_probe_test(self, rate_pps: int, dst_mac: str) -> dict:
        """
        Запускает пробный multicast тест с заданной скоростью.
        Возвращает словарь с суммарными отправленными и потерянными пакетами.
        """
        print(f"DEBUG: _run_probe_test() вызван с rate_pps={rate_pps}")
        
        # Создаём временный тест с теми же параметрами, но с probe_duration
        temp_params = self.params.copy()
        temp_params["rate_pps"] = rate_pps
        temp_params["duration_s"] = self.params.get("probe_duration_s", 10.0)
        # Убираем packet_count, если он есть, чтобы тест шёл по времени
        temp_params.pop("packet_count", None)
        
        # Создаём временный воркер
        temp_worker = MulticastMasterWorker(
            vlan_to_interfaces=self.vlan_to_interfaces,
            params=temp_params,
            dst_type=self.dst_type,
            group_name=f"__probe_{rate_pps}__",
            is_probe=True
        )
        # Передаём клиентов, чтобы не создавать новые подключения
        temp_worker.clients = self.clients
        
        self._probe_worker = temp_worker

        # Запускаем синхронно (блокирующий вызов)
        # Используем QEventLoop для ожидания завершения
        loop = QEventLoop()
        temp_worker.finished_ok.connect(loop.quit)
        temp_worker.finished_err.connect(loop.quit)
        temp_worker.start()
        loop.exec()
        
        temp_worker.stop()
        if not temp_worker.wait(5000):
            temp_worker.terminate()
            temp_worker.wait(2000)
            
        # Собираем результаты
        total_sent = 0
        total_lost = 0
        for result in temp_worker.results:
            total_sent += result["sender"].get("packets_sent", 0)
            total_lost += result["receiver"].get("packets_lost", 0)

        self._probe_worker = None

        print(f"DEBUG: _run_probe_test() результат: sent={total_sent}, lost={total_lost}")
        return {"packets_sent": total_sent, "packets_lost": total_lost}
    
    def run(self):
        try:
            if self.is_probe:
                # Убираем auto_rate из параметров
                self.params["auto_rate"] = False
            test_id = str(uuid.uuid4())[:8]
            self.progress.emit(f"📡 Запуск multicast теста для {len(self.vlan_to_interfaces)} VLAN (test_id={test_id})...")

            # Создаём клиентов для всех хостов
            for ep in self.all_senders:
                if ep.host not in self.clients:
                    self.clients[ep.host] = AgentClient(ep.host)

            # Определяем dst_mac
            if self.dst_type == 1:
                dst_mac = "01:00:5E:00:00:01"
            else:
                dst_mac = "ff:ff:ff:ff:ff:ff"

            # === АВТОПОДБОР СКОРОСТИ ===
            rate_to_use = self.params.get("rate_pps", 1000)
            auto_rate_result = False
            search = None

            if self.params.get("auto_rate"):
                self.autoprobe_started.emit()
                self.progress.emit("⏳ Автоподбор скорости... (остановка недоступна)")
                
                try:
                    # Собираем все пары для всех VLAN
                    all_pairs = []
                    for vlan_id, ifaces in self.vlan_to_interfaces.items():
                        if len(ifaces) >= 2:
                            all_pairs.append((ifaces[0], ifaces[1], vlan_id))
                    
                    if len(all_pairs) == 0:
                        self.progress.emit("⚠️ Недостаточно интерфейсов для автоподбора")
                    else:
                        def trial(rate_pps: int) -> dict:
                            return self._run_probe_test(rate_pps, dst_mac)
                        
                        search = find_max_no_loss_rate(
                            trial,
                            min_rate_pps=self.params.get("rate_min", 1000),
                            max_rate_pps=self.params.get("rate_max", 100000),
                            confirm_trials=2,
                        )
                        
                        rate_to_use = search.rate_pps
                        auto_rate_result = True

                        # === РАСЧЕТ ВРЕМЕНИ ЗАВЕРШЕНИЯ ===
                        from datetime import datetime, timedelta

                        if self.params.get("packet_count", 0) > 0:
                            seconds = self.params["packet_count"] / max(rate_to_use, 1)
                        else:
                            seconds = self.params.get("duration_s", 5.0)

                        eta = datetime.now() + timedelta(seconds=seconds)
                        eta_str = eta.strftime("%H:%M:%S")

                        # Вывод в лог через сигнал
                        self.log_message.emit(f"✅ Найдена скорость для multicast: {rate_to_use} pps (за {search.iterations} итераций)")
                        self.log_message.emit(f"⏳ Тест запущен, завершится в {eta_str}")
                finally:
                    self.autoprobe_finished.emit()
                    
            # ===== 1. Запускаем receiver'ы на всех интерфейсах во всех VLAN =====
            recv_test_ids = {}
            recv_spec = {
                "test_id": test_id,
                "role": "receiver",
                "size_mode": self.params["size_mode"],
                "duration_s": 0.0,
            }
            if self.params["size_mode"] == "random":
                recv_spec["size_min"] = self.params["size_min"]
                recv_spec["size_max"] = self.params["size_max"]
            else:
                recv_spec["size"] = self.params["size"]

            for key, (ep, vlan_id) in self.all_receivers.items():
                client = self.clients[ep.host]
                recv_spec["iface"] = ep.iface
                try:
                    tid = client.start_test(recv_spec)
                    recv_key = self.get_key(ep, vlan_id)
                    self.receiver_test_ids[recv_key] = tid
                    recv_test_ids[recv_key] = (tid, ep, vlan_id)
                    self.progress.emit(f"✅ Receiver запущен на {ep.slave}:{ep.iface} (VLAN {vlan_id})")
                except Exception as e:
                    self.errors.append(f"Ошибка запуска receiver на {ep.slave}:{ep.iface} (VLAN {vlan_id}): {e}")
            time.sleep(0.5)

            if self._stop_requested:
                self.cleanup(recv_test_ids)
                self.finished_err.emit("Тест остановлен до запуска sender")
                return

            # ===== 2. Запускаем sender'ов на всех уникальных интерфейсах (один раз!) =====
            send_test_ids = {}
            send_spec = {
                "test_id": test_id,
                "role": "sender",
                "dst_mac": dst_mac,
                "size_mode": self.params["size_mode"],
                "rate_pps": rate_to_use,
                "duration_s": self.params.get("duration_s", 5.0),
                "dst_type": self.dst_type,
            }
            if self.params.get("packet_count", 0):
                send_spec["packet_count"] = self.params["packet_count"]
            if self.params["size_mode"] == "random":
                send_spec["size_min"] = self.params["size_min"]
                send_spec["size_max"] = self.params["size_max"]
            else:
                send_spec["size"] = self.params["size"]

            for ep in self.all_senders:
                client = self.clients[ep.host]
                send_spec["iface"] = ep.iface
                send_spec["src_mac"] = ep.mac
                try:
                    tid = client.start_test(send_spec)
                    send_key = self.get_ep_key(ep)
                    send_test_ids[send_key] = (tid, ep)
                    self.progress.emit(f"✅ Sender запущен на {ep.slave}:{ep.iface}")
                except Exception as e:
                    self.errors.append(f"Ошибка запуска sender на {ep.slave}:{ep.iface}: {e}")

            # ===== 3. Ждём завершения всех sender'ов =====
            max_wait = 120.0
            if self.params.get("packet_count", 0):
                theoretical_time = self.params["packet_count"] / max(self.params["rate_pps"], 1)
                max_wait = theoretical_time * 1.2 + 60.0  # 20% запас + 60 секунд
            else:
                max_wait = self.params.get("duration_s", 5.0) * 1.2 + 60.0

            # Собираем TX статистику для каждого sender'а
            sender_tx_results = {}  # mac -> tx_stats
            sender_results = {}
            for send_key, (tid, ep) in send_test_ids.items():
                if tid is None:
                    continue
                client = self.clients[ep.host]
                try:
                    result = wait_for_result(client, tid, max_wait=max_wait)
                    nic_stats = result.get("nic_stats_delta", {})
                    packets_sent = result.get("packets_sent", 0)
                    bytes_sent = result.get("bytes_sent", 0)
                    duration_s = result.get("duration_s", 0)
                    sender_results[ep.mac] = {
                        "packets_sent": packets_sent,
                        "bytes_sent": bytes_sent,
                        "duration_s": duration_s,
                        "nic_stats": nic_stats,
                    }
                    tx_stats = {k: v for k, v in nic_stats.items() if k.startswith("tx_")}
                    sender_tx_results[ep.mac] = tx_stats
                    self.progress.emit(f"✅ Sender на {ep.slave}:{ep.iface} завершён")
                except TimeoutError:
                    self.errors.append(f"Sender на {ep.slave}:{ep.iface} не завершился за {max_wait} с")
                    try:
                        client.stop_test(tid)
                    except:
                        pass

            # ===== 4. Останавливаем все receiver'ы =====
            self.cleanup(recv_test_ids)

            # ===== 5. Получаем результаты receiver'ов =====
            receiver_results = {}  # recv_key -> recv_data
            for recv_key, (tid, ep, vlan_id) in recv_test_ids.items():
                if tid is None:
                    continue
                client = self.clients[ep.host]
                try:
                    resp = client.get_result(tid)
                    if resp.get("finished"):
                        receiver_results[recv_key] = resp["result"]
                        self.progress.emit(f"✅ Результат receiver на {ep.slave}:{ep.iface} (VLAN {vlan_id}) получен")
                    else:
                        self.errors.append(f"Receiver на {ep.slave}:{ep.iface} (VLAN {vlan_id}) не завершился")
                except Exception as e:
                    self.errors.append(f"Ошибка получения результата receiver на {ep.slave}:{ep.iface} (VLAN {vlan_id}): {e}")

            # ===== 6. Формируем результаты =====
            # Строим словарь интерфейсов по MAC для быстрого доступа
            sender_by_mac = {s.mac: s for s in self.all_senders}
            error_fields = (
                "rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
                "rx_fifo_errors", "tx_fifo_errors", "rx_over_errors",
                "rx_frame_errors", "rx_crc_errors", "collisions",
                "rx_mac_missed", "tx_aborted", "tx_underrun",
                "rx_jabber", "rx_oversize", "rx_undersize", "rx_align_errors",
            )

            def has_nic_issue(stats):
                return any((stats.get(f) or 0) > 0 for f in error_fields)

            self.test_index_counter = 0

            for recv_key, recv_data in receiver_results.items():
                # Извлекаем ep и vlan_id из ключа
                parts = recv_key.split(":vlan")
                ep_key = parts[0]
                vlan_id = int(parts[1])
                # Находим ep
                ep = None
                for e in self.all_senders:
                    if self.get_ep_key(e) == ep_key:
                        ep = e
                        break
                if ep is None:
                    continue

                sender_stats = recv_data.get("sender_stats", [])
                recv_nic_stats = recv_data.get("nic_stats_delta", {})

                # Фильтруем sender_stats: оставляем только тех sender'ов, у которых есть этот VLAN
                filtered_sender_stats = []
                for stat in sender_stats:
                    mac = stat.get("mac")
                    sender_ep = sender_by_mac.get(mac)
                    if sender_ep and vlan_id in sender_ep.vlans:
                        filtered_sender_stats.append(stat)

                packets_sent_total = 0
                bytes_sent_total = 0
                packets_expected = 0
                packets_received = 0
                bytes_received = 0
                packets_lost = 0
                duration_s = 0
                for stat in filtered_sender_stats:
                    mac = stat.get("mac")
                    sender_info = sender_results.get(mac, {})
                    if duration_s == 0:
                        duration_s = sender_info.get("duration_s", 0)
                    packets_sent_total += sender_info.get("packets_sent", 0)
                    bytes_sent_total += sender_info.get("bytes_sent", 0)
                    packets_expected += stat.get("packets_expected", 0)
                    packets_received += stat.get("packets_received", 0)
                    bytes_received += stat.get("bytes_received", 0)
                    packets_lost += stat.get("packets_lost", 0)

                if duration_s == 0:
                    duration_s = self.params.get("duration_s", 0)
                reconciled_lost = max(0, packets_sent_total - packets_received)
                if self._stop_requested:
                    trailing_loss_detected = False
                else:
                    trailing_loss_detected = reconciled_lost > packets_lost
                loss_pct = (reconciled_lost / max(packets_sent_total, 1)) * 100.0
                sender_nic_issue = False
                for mac, info in sender_results.items():
                    sender_ep = sender_by_mac.get(mac)
                    if sender_ep and vlan_id in sender_ep.vlans:
                        if has_nic_issue(info.get("nic_stats", {})):
                            sender_nic_issue = True
                            break

                # Проверка NIC ошибок у receiver'а (RX)
                receiver_nic_issue = has_nic_issue(recv_nic_stats)
                nic_issue_detected = sender_nic_issue or receiver_nic_issue

                # TX статистика для sender'ов в этом VLAN
                sender_tx_stats = {}
                for stat in filtered_sender_stats:
                    mac = stat.get("mac")
                    sender_info = sender_results.get(mac, {})
                    nic_stats = sender_info.get("nic_stats", {})
                    if mac in sender_tx_results:
                        sender_tx_stats[mac] = sender_tx_results[mac]

                self.test_index_counter += 1

                if auto_rate_result and search is not None:
                    result["auto_rate_pps"] = rate_to_use
                    result["auto_rate_iterations"] = search.iterations
                    
                result = {
                    "test_id": test_id,
                    "rate_pps": rate_to_use,
                    "common_vlan": vlan_id,
                    "dst_type": self.dst_type,
                    "sender_stats": filtered_sender_stats,
                    "sender_tx_stats": sender_tx_stats,
                    "receiver_nic_stats": {k: v for k, v in recv_nic_stats.items() if k.startswith("rx_")},
                    "test_index": self.test_index_counter,
                    "total_tests": self.total_tests,
                    "sender": {
                        "slave": ep.slave,
                        "iface": ep.iface,
                        "mac": ep.mac,
                        "packets_sent": packets_sent_total,
                        "bytes_sent": bytes_sent_total + (packets_sent_total * 4),
                        "duration_s": duration_s,
                        "nic_stats": {},
                    },
                    "receiver": {
                        "slave": ep.slave,
                        "iface": ep.iface,
                        "packets_received": packets_received,
                        "bytes_received": bytes_received + (packets_received * 4),
                        "packets_lost": max(packets_lost, reconciled_lost),
                        "packets_expected": 0,
                        "out_of_order": 0,
                        "duration_s": recv_data.get("duration_s", duration_s),
                        "loss_pct": (reconciled_lost / max(packets_sent_total, 1)) * 100.0,
                        "trailing_loss_detected": trailing_loss_detected,
                        "nic_stats": {k: v for k, v in recv_nic_stats.items() if k.startswith("rx_")},
                    },
                    "nic_issue_detected": nic_issue_detected,
                    "group_name": self.group_name,
                    "is_stopped": False,
                    "_sender_results": sender_results,
                    "incomplete": False,
                }

                if auto_rate_result and search is not None:
                    result["auto_rate_pps"] = rate_to_use
                    result["auto_rate_iterations"] = search.iterations
                    
                self.results.append(result)
                self.test_complete.emit(result)

            if self.errors:
                self.finished_err.emit("\n".join(self.errors))
            else:
                self.finished_ok.emit(self.results)

        except Exception as e:    
            import traceback
            traceback.print_exc()
            self.finished_err.emit(str(e))
        finally:
            if self.params.get("auto_rate"):
                self.autoprobe_finished.emit()
            # === ВАЖНО: очищаем все ресурсы ===
            self.stop()
            # Ждём завершения всех дочерних потоков
            for client in self.clients.values():
                try:
                    client.stop_all()
                except:
                    pass

    def cleanup(self, recv_test_ids):
        for recv_key, (tid, ep, vlan_id) in recv_test_ids.items():
            if tid is None:
                continue
            client = self.clients.get(ep.host)
            if client:
                try:
                    client.stop_test(tid)
                except:
                    pass
                
# ============================================================================
# MainWindow
# ============================================================================
class MainWindow(QMainWindow):
    update_ui_signal = pyqtSignal()

    def __init__(self, topology_path: str):
        super().__init__()
        self.setWindowTitle("Тестирование коммутаторов - Master")
        self.resize(1400, 900)

        self.topology_path = topology_path
        self.topology = {}
        self.results = []
        self.workers = []
        self.group_worker = None
        self.groups = {}
        self.iface_status = {}
        self.agent_online = {}
        self.prev_agent_status = {}
        self._new_status = {}
        self._new_online = {}

        # 1. СНАЧАЛА загружаем топологию (без UI)
        self._load_topology_data(topology_path)

        # 2. ПОТОМ строим UI
        self._build_ui()

        # 3. ПОТОМ применяем топологию к UI
        self.apply_topology_to_ui()

        # 4. ПОТОМ запускаем таймеры
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_slaves)
        self.refresh_timer.start(30000)
        QTimer.singleShot(500, self.refresh_slaves)

        self.update_ui_signal.connect(self._update_ui)

    # ------------------------------------------------------------------------
    # UI построение
    # ------------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- Верхняя панель ---
        topo_row = QHBoxLayout()
        
        self.topo_label = QLabel(f"Топология: {self.topology_path}")
        topo_row.addWidget(self.topo_label, 2)
        
        btn_refresh = QPushButton("🔄 Обновить устройства")
        btn_refresh.clicked.connect(self.refresh_slaves)
        topo_row.addWidget(btn_refresh)
        
        # Верхний статус (только для системы)
        self.status_label = QLabel("✅ Готово")
        self.status_label.setStyleSheet("color: #4caf50; font-weight: bold;")
        self.status_label.setMinimumWidth(150)
        topo_row.addWidget(self.status_label)
        
        self.auto_refresh_check = QCheckBox("Автообновление")
        self.auto_refresh_check.setChecked(True)
        self.auto_refresh_check.stateChanged.connect(self.on_auto_refresh_toggled)
        topo_row.addWidget(self.auto_refresh_check)
        
        topo_row.addStretch()
        root.addLayout(topo_row)

        # --- Основной сплиттер ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Левый блок - вкладки
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._build_devices_tab(), "Устройства")
        self.tab_widget.addTab(self._build_single_tab(), "Одиночный тест")
        self.tab_widget.addTab(self._build_group_tab(), "Группа тестов")
        self.tab_widget.addTab(self._build_terminal_tab(), "Управление агентами")
        self.tab_widget.addTab(ConsoleTab(), "Консоль")
        left_layout.addWidget(self.tab_widget)

        # Правый блок - результаты
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels([
            "Group", "Test ID", "Sender", "Receiver", "Отправлено",
            "Байт отправ.", "Получено", "Байт получ.", "Потеряно",
            "Потери %", "Скорость (pps)", "Длит., с", "NIC"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.on_table_double_click)
        right_layout.addWidget(self.table, 1)

        # Логи
        log_label = QLabel("Лог выполнения:")
        right_layout.addWidget(log_label)

        self.log_tabs = QTabWidget()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_tabs.addTab(self.log_text, "Лог")

        self.debug_log_text = QTextEdit()
        self.debug_log_text.setReadOnly(True)
        self.debug_log_text.setStyleSheet("color: gray; font-family: Consolas, monospace; font-size: 10px;")
        self.log_tabs.addTab(self.debug_log_text, "Отладка")

        self.log_tabs.setMaximumHeight(220)
        right_layout.addWidget(self.log_tabs)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([600, 800])
        root.addWidget(splitter, 1)

        # Кнопки экспорта
        export_row = QHBoxLayout()
        btn_export = QPushButton("Экспорт в JSON")
        btn_export.clicked.connect(self.on_export_clicked)
        btn_clear = QPushButton("Очистить таблицу")
        btn_clear.clicked.connect(self.on_clear_clicked)
        export_row.addStretch(1)
        export_row.addWidget(btn_export)
        export_row.addWidget(btn_clear)
        root.addLayout(export_row)
        
        self.apply_topology_to_ui()

    # ------------------------------------------------------------------------
    # Вкладка "Устройства"
    # ------------------------------------------------------------------------
    def _build_devices_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # VLAN Profiles
        profile_group = QGroupBox("VLAN-профили")
        profile_layout = QHBoxLayout(profile_group)

        profile_layout.addWidget(QLabel("Профиль:"))
        self.profile_label = QLabel("—")
        self.profile_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        profile_layout.addWidget(self.profile_label)

        profile_layout.addStretch()

        btn_load = QPushButton("📂 Загрузить профиль")
        btn_load.clicked.connect(self.on_load_vlan_profile)
        profile_layout.addWidget(btn_load)

        btn_save = QPushButton("💾 Сохранить профиль")
        btn_save.clicked.connect(self.on_save_vlan_profile)
        profile_layout.addWidget(btn_save)

        btn_clear = QPushButton("🗑 Очистить все VLAN")
        btn_clear.clicked.connect(self.on_clear_all_vlans)
        btn_clear.setStyleSheet("color: #f44336;")
        profile_layout.addWidget(btn_clear)

        layout.addWidget(profile_group)

        # Device table
        label = QLabel("Состояние устройств и интерфейсов (двойной клик для редактирования VLAN)")
        layout.addWidget(label)

        self.device_table = QTableWidget(0, 6)
        self.device_table.setHorizontalHeaderLabels([
            "Слейв", "Интерфейс", "MAC", "Состояние", "Скорость (Мбит/с)", "VLAN"
        ])
        self.device_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.device_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.device_table.cellDoubleClicked.connect(self.on_device_table_double_click)
        layout.addWidget(self.device_table)

        return tab

    # ------------------------------------------------------------------------
    # Вкладка "Одиночный тест"
    # ------------------------------------------------------------------------
    def _build_single_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        pair_box = QGroupBox("Выбор пары интерфейсов")
        pair_layout = QFormLayout(pair_box)
        self.sender_combo = QComboBox()
        self.sender_combo.currentIndexChanged.connect(self.on_sender_changed)
        self.receiver_combo = QComboBox()
        pair_layout.addRow("Sender:", self.sender_combo)
        pair_layout.addRow("Receiver:", self.receiver_combo)
        layout.addWidget(pair_box)

        self.single_params_box = self._build_params_group("Параметры одиночного теста", show_dst_type=False)
        layout.addWidget(self.single_params_box)

        run_row = QHBoxLayout()
        self.run_btn = QPushButton("Запустить одиночный тест")
        self.run_btn.clicked.connect(self.on_run_single_clicked)
        self.stop_btn = QPushButton("⏹ Остановить")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white;")
        self.stop_btn.clicked.connect(self.on_stop_single_clicked)
        
        # СТАТУС ТЕСТА (НЕ верхний)
        self.test_status_label = QLabel("")
        self.test_status_label.setMinimumWidth(200)
        
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.stop_btn)
        run_row.addWidget(self.test_status_label, 1)
        layout.addLayout(run_row)

        layout.addStretch()
        return tab

    # ------------------------------------------------------------------------
    # Вкладка "Группа тестов"
    # ------------------------------------------------------------------------
    def _build_group_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group_control = QGroupBox("Управление группами")
        group_control_layout = QVBoxLayout(group_control)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Название группы:"))
        self.group_name_edit = QComboBox()
        self.group_name_edit.setEditable(True)
        self.group_name_edit.currentTextChanged.connect(self.on_group_changed)
        name_row.addWidget(self.group_name_edit, 1)

        btn_add_group = QPushButton("➕ Создать группу")
        btn_add_group.clicked.connect(self.on_add_group_clicked)
        name_row.addWidget(btn_add_group)

        btn_remove_group = QPushButton("🗑 Удалить группу")
        btn_remove_group.clicked.connect(self.on_remove_group_clicked)
        name_row.addWidget(btn_remove_group)
        group_control_layout.addLayout(name_row)

        manage_row = QHBoxLayout()
        btn_save_groups = QPushButton("💾 Сохранить группы")
        btn_save_groups.clicked.connect(self.save_groups)
        manage_row.addWidget(btn_save_groups)

        btn_load_groups = QPushButton("📂 Загрузить группы")
        btn_load_groups.clicked.connect(self.load_groups)
        manage_row.addWidget(btn_load_groups)

        manage_row.addStretch()
        group_control_layout.addLayout(manage_row)

        iface_row = QHBoxLayout()
        iface_row.addWidget(QLabel("Добавить интерфейс:"))
        self.group_iface_combo = QComboBox()
        iface_row.addWidget(self.group_iface_combo, 1)

        btn_add_iface = QPushButton("➕ Добавить")
        btn_add_iface.clicked.connect(self.on_add_iface_to_group)
        iface_row.addWidget(btn_add_iface)
        group_control_layout.addLayout(iface_row)

        group_control_layout.addWidget(QLabel("Интерфейсы в группе:"))
        self.group_iface_list = QListWidget()
        group_control_layout.addWidget(self.group_iface_list)

        btn_remove_iface = QPushButton("🗑 Удалить выбранный интерфейс")
        btn_remove_iface.clicked.connect(self.on_remove_iface_from_group)
        group_control_layout.addWidget(btn_remove_iface)

        layout.addWidget(group_control)

        self.group_params_box = self._build_params_group("Параметры группового теста", show_dst_type=True)
        layout.addWidget(self.group_params_box)

        run_row = QHBoxLayout()
        self.run_group_btn = QPushButton("▶▶ Запустить группу тестов")
        self.run_group_btn.clicked.connect(self.on_run_group_clicked)
        self.stop_group_btn = QPushButton("⏹ Остановить группу")
        self.stop_group_btn.setEnabled(False)
        self.stop_group_btn.setStyleSheet("background-color: #f44336; color: white;")
        self.stop_group_btn.clicked.connect(self.on_stop_group_clicked)
        self.group_status_label = QLabel("")
        run_row.addWidget(self.run_group_btn)
        run_row.addWidget(self.stop_group_btn)
        run_row.addWidget(self.group_status_label, 1)
        layout.addLayout(run_row)

        group_results_label = QLabel("Результаты группы:")
        layout.addWidget(group_results_label)
        self.group_results_table = QTableWidget(0, 6)
        self.group_results_table.setHorizontalHeaderLabels([
            "Тест", "Sender", "Receiver", "Потеряно", "Потери %", "Скорость (pps)"
        ])
        self.group_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.group_results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.group_results_table)

        return tab

    # ------------------------------------------------------------------------
    # Вкладка "Управление агентами"
    # ------------------------------------------------------------------------
    def _build_terminal_tab(self) -> QWidget:
        """Создаёт вкладку управления агентами с терминалами."""
        agents = self.get_agents()
        if not agents:
            # Создаём виджет с сообщением
            widget = QWidget()
            layout = QVBoxLayout(widget)
            label = QLabel("⚠️ Нет настроенных агентов в топологии")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("font-size: 16px; color: #f44336;")
            layout.addWidget(label)
            return widget
        return TerminalTab(agents)

    def get_agents(self) -> dict:
        """Возвращает словарь {имя: ip} для всех агентов."""
        agents = {}
        slaves = self.topology.get("slaves", {})
        for name, info in slaves.items():
            host = info.get("host")
            if host:
                agents[name] = host
        return agents

    # ------------------------------------------------------------------------
    # Параметры теста
    # ------------------------------------------------------------------------
    def _build_params_group(self, title: str, show_dst_type: bool = False) -> QGroupBox:
        pkt_box = QGroupBox(title)
        pkt_layout = QFormLayout(pkt_box)

        size_row = QHBoxLayout()
        radio_fixed = QRadioButton("Фикс. размер")
        radio_random = QRadioButton("Случайный размер")
        radio_fixed.setChecked(True)
        group = QButtonGroup(pkt_box)
        group.addButton(radio_fixed)
        group.addButton(radio_random)
        size_row.addWidget(radio_fixed)
        size_row.addWidget(radio_random)
        pkt_layout.addRow("Тип размера:", size_row)

        size_spin = QSpinBox()
        size_spin.setRange(64, 1500)
        size_spin.setValue(512)
        pkt_layout.addRow("Размер (байт):", size_spin)

        rng_row = QHBoxLayout()
        size_min_spin = QSpinBox()
        size_min_spin.setRange(64, 1500)
        size_min_spin.setValue(64)
        size_max_spin = QSpinBox()
        size_max_spin.setRange(64, 1500)
        size_max_spin.setValue(1500)
        rng_row.addWidget(QLabel("мин:"))
        rng_row.addWidget(size_min_spin)
        rng_row.addWidget(QLabel("макс:"))
        rng_row.addWidget(size_max_spin)
        pkt_layout.addRow("Диапазон:", rng_row)

        def on_size_mode_toggled():
            if radio_fixed.isChecked():
                size_spin.setEnabled(True)
                size_min_spin.setEnabled(False)
                size_max_spin.setEnabled(False)
            else:
                size_spin.setEnabled(False)
                size_min_spin.setEnabled(True)
                size_max_spin.setEnabled(True)

        radio_fixed.toggled.connect(on_size_mode_toggled)
        radio_random.toggled.connect(on_size_mode_toggled)
        on_size_mode_toggled()

        test_type_row = QHBoxLayout()
        radio_time = QRadioButton("По времени")
        radio_count = QRadioButton("По количеству пакетов")
        radio_time.setChecked(True)
        type_group = QButtonGroup(pkt_box)
        type_group.addButton(radio_time)
        type_group.addButton(radio_count)
        test_type_row.addWidget(radio_time)
        test_type_row.addWidget(radio_count)
        pkt_layout.addRow("Тип теста:", test_type_row)

        duration_spin = QDoubleSpinBox()
        duration_spin.setRange(1.0, 3600.0)
        duration_spin.setValue(5.0)
        pkt_layout.addRow("Длительность (сек):", duration_spin)

        count_spin = QSpinBox()
        count_spin.setRange(1, 10_000_000)
        count_spin.setValue(10000)
        count_spin.setEnabled(False)
        pkt_layout.addRow("Количество пакетов:", count_spin)

        def on_test_type_toggled():
            if radio_time.isChecked():
                duration_spin.setEnabled(True)
                count_spin.setEnabled(False)
            else:
                duration_spin.setEnabled(False)
                count_spin.setEnabled(True)

        radio_time.toggled.connect(on_test_type_toggled)
        radio_count.toggled.connect(on_test_type_toggled)
        on_test_type_toggled()

        rate_mode_row = QHBoxLayout()
        radio_fixed_rate = QRadioButton("Фиксированная скорость")
        radio_auto_rate = QRadioButton("Автоподбор")
        radio_fixed_rate.setChecked(True)
        rate_group = QButtonGroup(pkt_box)
        rate_group.addButton(radio_fixed_rate)
        rate_group.addButton(radio_auto_rate)
        rate_mode_row.addWidget(radio_fixed_rate)
        rate_mode_row.addWidget(radio_auto_rate)
        pkt_layout.addRow("Режим скорости:", rate_mode_row)

        rate_spin = QSpinBox()
        rate_spin.setRange(1, 2_000_000)
        rate_spin.setValue(1000)
        pkt_layout.addRow("Скорость (pps):", rate_spin)

        rate_range_row = QHBoxLayout()
        rate_min_spin = QSpinBox()
        rate_min_spin.setRange(1, 2_000_000)
        rate_min_spin.setValue(1000)
        rate_max_spin = QSpinBox()
        rate_max_spin.setRange(1, 2_000_000)
        rate_max_spin.setValue(100000)
        rate_range_row.addWidget(QLabel("мин:"))
        rate_range_row.addWidget(rate_min_spin)
        rate_range_row.addWidget(QLabel("макс:"))
        rate_range_row.addWidget(rate_max_spin)
        pkt_layout.addRow("Диапазон поиска:", rate_range_row)

        def on_rate_mode_toggled():
            if radio_fixed_rate.isChecked():
                rate_spin.setEnabled(True)
                rate_min_spin.setEnabled(False)
                rate_max_spin.setEnabled(False)
            else:
                rate_spin.setEnabled(False)
                rate_min_spin.setEnabled(True)
                rate_max_spin.setEnabled(True)

        radio_fixed_rate.toggled.connect(on_rate_mode_toggled)
        radio_auto_rate.toggled.connect(on_rate_mode_toggled)
        on_rate_mode_toggled()

        # --- Тип адреса назначения ---
        if show_dst_type:
            # --- Тип адреса назначения ---
            dst_type_layout = QHBoxLayout()
            self.dst_type_combo = QComboBox()
            self.dst_type_combo.addItems(["Unicast (по MAC)", "Multicast", "Broadcast"])
            self.dst_type_combo.setToolTip("Выберите тип адреса назначения")
            dst_type_layout.addWidget(self.dst_type_combo)
            pkt_layout.addRow("Тип адреса:", dst_type_layout)
            pkt_box.dst_type_combo = self.dst_type_combo

        pkt_box.radio_fixed = radio_fixed
        pkt_box.radio_random = radio_random
        pkt_box.size_spin = size_spin
        pkt_box.size_min_spin = size_min_spin
        pkt_box.size_max_spin = size_max_spin
        pkt_box.radio_time = radio_time
        pkt_box.radio_count = radio_count
        pkt_box.duration_spin = duration_spin
        pkt_box.count_spin = count_spin
        pkt_box.radio_fixed_rate = radio_fixed_rate
        pkt_box.radio_auto_rate = radio_auto_rate
        pkt_box.rate_spin = rate_spin
        pkt_box.rate_min_spin = rate_min_spin
        pkt_box.rate_max_spin = rate_max_spin

        return pkt_box

    def _get_params_from_box(self, box: QGroupBox) -> dict:
        size_mode = "random" if box.radio_random.isChecked() else "fixed"
        wire_size = box.size_spin.value()
        wire_min = box.size_min_spin.value()
        wire_max = box.size_max_spin.value()

        params = {
            "size_mode": size_mode,
            "size": wire_size - FCS_OVERHEAD if size_mode == "fixed" else 0,
            "size_min": wire_min - FCS_OVERHEAD if size_mode == "random" else 0,
            "size_max": wire_max - FCS_OVERHEAD if size_mode == "random" else 0,
            "wire_size": wire_size,
            "wire_min": wire_min,
            "wire_max": wire_max,
            "rate_pps": box.rate_spin.value(),
            "auto_rate": box.radio_auto_rate.isChecked(),
            "rate_min": box.rate_min_spin.value(),
            "rate_max": box.rate_max_spin.value(),
            "probe_duration_s": 10.0,
        }
        if hasattr(box, 'dst_type_combo') and box.dst_type_combo is not None:
            params["dst_type"] = box.dst_type_combo.currentIndex()
        else:
            params["dst_type"] = 0  # unicast по умолчанию

        if box.radio_time.isChecked():
            params["duration_s"] = box.duration_spin.value()
        else:
            params["packet_count"] = box.count_spin.value()
        return params

    # ------------------------------------------------------------------------
    # Загрузка топологии
    # ------------------------------------------------------------------------
    def load_topology(self, path: str):
        """Загружает топологию из файла (без обновления UI)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.topology = json.load(f)
        except Exception as e:
            print(f"❌ Ошибка загрузки топологии: {e}")
            self.topology = {"slaves": {}}

    def apply_topology_to_ui(self):
        """Применяет загруженную топологию к UI."""
        if hasattr(self, 'topo_label'):
            self.topo_label.setText(f"Топология: {self.topology_path}")
        self.iface_status.clear()
        self.agent_online.clear()
        self.prev_agent_status.clear()
        self.rebuild_endpoint_lists()
        self.update_group_iface_combo()

    def _load_topology_data(self, path: str):
        """Загружает топологию из файла (без UI)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.topology = json.load(f)
        except FileNotFoundError:
            print(f"❌ Файл не найден: {path}")
            self.topology = {"slaves": {}}
        except json.JSONDecodeError as e:
            print(f"❌ Ошибка JSON: {e}")
            self.topology = {"slaves": {}}
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            self.topology = {"slaves": {}}

    # ------------------------------------------------------------------------
    # Управление интерфейсами
    # ------------------------------------------------------------------------
    def all_endpoints(self) -> list[tuple[str, str]]:
        eps = set()
        for link in self.topology.get("links", []):
            eps.add((link["a"]["slave"], link["a"]["iface"]))
            eps.add((link["b"]["slave"], link["b"]["iface"]))
        return sorted(eps)

    def is_interface_up(self, slave: str, iface: str) -> bool:
        info = self.iface_status.get((slave, iface))
        return info is not None and info.get("up", False)

    def rebuild_endpoint_lists(self):
        # Сохраняем ТЕКСТОВЫЕ ПРЕДСТАВЛЕНИЯ
        current_sender_text = self.sender_combo.currentText()
        current_receiver_text = self.receiver_combo.currentText()
        
        self.sender_combo.blockSignals(True)
        self.sender_combo.clear()
        count = 0
        for (slave, iface) in self.all_endpoints():
            if self.is_interface_up(slave, iface):
                vlans = self.iface_status.get((slave, iface), {}).get("vlans", [])
                vlan_str = f"[{', '.join(str(v) for v in vlans)}]" if vlans else "[нет VLAN]"
                text = f"{slave}:{iface} {vlan_str}"
                self.sender_combo.addItem(text, (slave, iface))
                count += 1
        self.sender_combo.blockSignals(False)
        
        # Восстанавливаем по тексту
        if current_sender_text:
            index = self.sender_combo.findText(current_sender_text)
            if index >= 0:
                self.sender_combo.setCurrentIndex(index)
            elif count > 0:
                self.sender_combo.setCurrentIndex(0)
        elif count > 0:
            self.sender_combo.setCurrentIndex(0)
        
        self.on_sender_changed(restore_text=current_receiver_text)

    def on_sender_changed(self, restore_index=None, restore_text=None):
        self.receiver_combo.blockSignals(True)
        self.receiver_combo.clear()
        sender_ep = self.sender_combo.currentData()
        if sender_ep is None:
            self.receiver_combo.blockSignals(False)
            return
        
        count = 0
        for (slave, iface) in self.all_endpoints():
            if (slave, iface) == sender_ep:
                continue
            if self.is_interface_up(slave, iface):
                vlans = self.iface_status.get((slave, iface), {}).get("vlans", [])
                vlan_str = f"[{', '.join(str(v) for v in vlans)}]" if vlans else "[нет VLAN]"
                text = f"{slave}:{iface} {vlan_str}"
                self.receiver_combo.addItem(text, (slave, iface))
                count += 1
        self.receiver_combo.blockSignals(False)
        
        # Восстанавливаем по индексу (если передан)
        if restore_index is not None and restore_index >= 0 and restore_index < self.receiver_combo.count():
            self.receiver_combo.setCurrentIndex(restore_index)
        # Или по тексту
        elif restore_text:
            index = self.receiver_combo.findText(restore_text)
            if index >= 0:
                self.receiver_combo.setCurrentIndex(index)
            elif count > 0:
                self.receiver_combo.setCurrentIndex(0)
        elif count > 0:
            self.receiver_combo.setCurrentIndex(0)

    # ------------------------------------------------------------------------
    # VLAN Profiles
    # ------------------------------------------------------------------------
    def on_save_vlan_profile(self):
        vlans = {}
        for slave_name, slave_info in self.topology.get("slaves", {}).items():
            for iface_name, iface_info in slave_info.get("interfaces", {}).items():
                key = f"{slave_name}:{iface_name}"
                vlans[key] = iface_info.get("vlans", [])

        if not vlans:
            QMessageBox.warning(self, "Внимание", "Нет VLAN-информации для сохранения")
            return

        name, ok = QInputDialog.getText(self, "Имя профиля", "Введите название профиля:")
        if not ok or not name:
            return

        description, ok = QInputDialog.getText(self, "Описание профиля", "Введите описание профиля:")
        if not ok:
            return

        default_filename = f"vlan_{name.replace(' ', '_')}.json"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить VLAN-профиль",
            default_filename,
            "JSON (*.json)"
        )
        if not file_path:
            return

        data = {
            "name": name,
            "description": description or "",
            "created": datetime.now().isoformat(),
            "vlans": vlans
        }

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log_user(f"✅ VLAN-профиль '{name}' сохранён в {file_path}")
            self.profile_label.setText(name)
            QMessageBox.information(self, "Готово", f"Профиль '{name}' сохранён")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить профиль: {e}")

    def on_load_vlan_profile(self):
        """Загружает VLAN-профиль из файла и обновляет группы."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить VLAN-профиль", "", "JSON (*.json)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "vlans" not in data:
                QMessageBox.critical(self, "Ошибка", "Неверный формат файла: отсутствует секция 'vlans'")
                return

            name = data.get("name", Path(file_path).stem)
            description = data.get("description", "")
            vlans = data.get("vlans", {})

            msg = f"Профиль: {name}\n"
            if description:
                msg += f"Описание: {description}\n"
            msg += f"Интерфейсов: {len(vlans)}"

            reply = QMessageBox.question(
                self, "Загрузка профиля",
                f"{msg}\n\nПрименить этот профиль и обновить группы?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                self._apply_vlans_from_dict(vlans)
                self.profile_label.setText(name)
                self.log_user(f"✅ VLAN-профиль '{name}' загружен и применён")
                # _apply_vlans_from_dict уже обновляет группы и UI

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить профиль: {e}")

    def _apply_vlans_from_dict(self, vlans: dict):
        """Применяет VLAN-привязки из словаря к топологии и обновляет группы."""
        applied = 0
        
        # === 1. Обновляем топологию ===
        for key, vlan_list in vlans.items():
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            slave, iface = parts[0], parts[1]

            if slave in self.topology.get("slaves", {}):
                if iface in self.topology["slaves"][slave].get("interfaces", {}):
                    self.topology["slaves"][slave]["interfaces"][iface]["vlans"] = vlan_list
                    applied += 1

        try:
            with open(self.topology_path, "w", encoding="utf-8") as f:
                json.dump(self.topology, f, ensure_ascii=False, indent=2)
            self.log_user(f"✅ Применено VLAN для {applied} интерфейсов")
        except Exception as e:
            self.log_user(f"❌ Ошибка сохранения топологии: {e}")
            return

        # === 2. Обновляем iface_status ===
        # Перезагружаем данные с агентов (асинхронно)
        self.refresh_slaves()
        
        # === 3. Обновляем существующие группы ===
        self._update_groups_with_new_vlans()
        
        # === 4. Обновляем UI ===
        self.rebuild_endpoint_lists()
        self.update_group_iface_combo()
        self.refresh_group_iface_list()

    def _update_groups_with_new_vlans(self):
        """Обновляет все существующие группы новыми VLAN-привязками."""
        if not self.groups:
            return
        
        updated_count = 0
        
        for group_name, endpoints in self.groups.items():
            new_endpoints = []
            for ep in endpoints:
                # Находим новые VLAN для этого интерфейса в топологии
                slave_cfg = self.topology.get("slaves", {}).get(ep.slave, {})
                iface_cfg = slave_cfg.get("interfaces", {}).get(ep.iface, {})
                new_vlans = iface_cfg.get("vlans", [])
                
                # Создаём новый EndpointRef с обновлёнными VLAN
                new_ep = EndpointRef(
                    slave=ep.slave,
                    host=ep.host,
                    iface=ep.iface,
                    mac=ep.mac,
                    vlans=new_vlans
                )
                new_endpoints.append(new_ep)
                if new_vlans != ep.vlans:
                    updated_count += 1
            
            self.groups[group_name] = new_endpoints
        
        if updated_count > 0:
            self.log_user(f"🔄 Обновлены VLAN для {updated_count} интерфейсов в группах")
        
        # Обновляем отображение групп
        self.refresh_group_iface_list()
        self.update_group_iface_combo()

    def on_clear_all_vlans(self):
        reply = QMessageBox.question(
            self, "Очистка VLAN",
            "Удалить все VLAN-привязки для всех интерфейсов?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        count = 0
        for slave_name, slave_info in self.topology.get("slaves", {}).items():
            for iface_name in slave_info.get("interfaces", {}).keys():
                self.topology["slaves"][slave_name]["interfaces"][iface_name]["vlans"] = []
                count += 1

        try:
            with open(self.topology_path, "w", encoding="utf-8") as f:
                json.dump(self.topology, f, ensure_ascii=False, indent=2)
            self.log_user(f"🗑 Очищены VLAN для {count} интерфейсов")
            self.profile_label.setText("—")
            self.refresh_slaves()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить топологию: {e}")

    # ------------------------------------------------------------------------
    # Редактирование VLAN (двойной клик)
    # ------------------------------------------------------------------------
    def on_device_table_double_click(self, row: int, column: int):
        if column != 5:
            return
        slave = self.device_table.item(row, 0).text()
        iface = self.device_table.item(row, 1).text()
        self._edit_vlan_dialog(slave, iface)

    def _edit_vlan_dialog(self, slave: str, iface: str):
        slave_cfg = self.topology.get("slaves", {}).get(slave, {})
        iface_cfg = slave_cfg.get("interfaces", {}).get(iface, {})
        current_vlans = iface_cfg.get("vlans", [])

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Редактирование VLAN для {slave}:{iface}")
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel(f"Интерфейс: {slave}:{iface}"))
        layout.addWidget(QLabel(f"Текущие VLAN: {', '.join(str(v) for v in current_vlans) if current_vlans else '(нет)'}"))
        layout.addWidget(QLabel(" "))
        layout.addWidget(QLabel("Введите VLAN ID через запятую (например: 10, 20, 30):"))

        input_field = QLineEdit()
        input_field.setText(", ".join(str(v) for v in current_vlans))
        input_field.setPlaceholderText("10, 20, 30")
        layout.addWidget(input_field)

        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        btn_ok.clicked.connect(dialog.accept)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(dialog.reject)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            text = input_field.text().strip()
            if text:
                try:
                    parts = text.replace(",", " ").split()
                    vlans = []
                    for part in parts:
                        if part.strip():
                            vlan_id = int(part.strip())
                            if 1 <= vlan_id <= 4094:
                                vlans.append(vlan_id)
                    vlans = sorted(set(vlans))
                    self._save_vlans_to_topology(slave, iface, vlans)
                except ValueError:
                    QMessageBox.warning(self, "Ошибка", f"Неверный формат: '{text}'")
            else:
                self._save_vlans_to_topology(slave, iface, [])

    def _save_vlans_to_topology(self, slave: str, iface: str, vlans: List[int]):
        if "slaves" not in self.topology:
            self.topology["slaves"] = {}
        if slave not in self.topology["slaves"]:
            self.topology["slaves"][slave] = {"interfaces": {}}
        if "interfaces" not in self.topology["slaves"][slave]:
            self.topology["slaves"][slave]["interfaces"] = {}

        self.topology["slaves"][slave]["interfaces"][iface]["vlans"] = vlans

        try:
            with open(self.topology_path, "w", encoding="utf-8") as f:
                json.dump(self.topology, f, ensure_ascii=False, indent=2)
            vlan_str = ", ".join(str(v) for v in vlans) if vlans else "(нет)"
            self.log_user(f"✅ VLAN [{vlan_str}] сохранены для {slave}:{iface}")
            self.refresh_slaves()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить топологию: {e}")

    # ------------------------------------------------------------------------
    # Управление группами
    # ------------------------------------------------------------------------
    def on_group_changed(self, name: str):
        self.refresh_group_iface_list()
        self.update_group_iface_combo()

    def update_group_iface_combo(self):
        group_name = self.group_name_edit.currentText().strip()
        self.group_iface_combo.clear()
        if not group_name or group_name not in self.groups:
            return

        used_interfaces = set()
        for g_name, eps in self.groups.items():
            if g_name == group_name:
                continue
            for ep in eps:
                used_interfaces.add((ep.slave, ep.iface))

        existing = {(ep.slave, ep.iface) for ep in self.groups[group_name]}
        count = 0
        for (slave, iface) in self.all_endpoints():
            if (slave, iface) in existing or (slave, iface) in used_interfaces:
                continue
            if self.is_interface_up(slave, iface):
                vlans = self.iface_status.get((slave, iface), {}).get("vlans", [])
                vlan_str = f"[{', '.join(str(v) for v in vlans)}]" if vlans else "[нет VLAN]"
                self.group_iface_combo.addItem(f"{slave}:{iface} {vlan_str}", (slave, iface))
                count += 1
        if count > 0:
            self.group_iface_combo.setCurrentIndex(0)

    def refresh_group_iface_list(self):
        name = self.group_name_edit.currentText().strip()
        self.group_iface_list.clear()
        for ep in self.groups.get(name, []):
            up = self.is_interface_up(ep.slave, ep.iface)
            info = self.iface_status.get((ep.slave, ep.iface), {})
            vlans = info.get("vlans", [])
            vlan_str = f"[{', '.join(str(v) for v in vlans)}]" if vlans else "[нет VLAN]"

            if up:
                item = QListWidgetItem(f"{ep.slave}:{ep.iface} {vlan_str}")
            else:
                item = QListWidgetItem(f"⚠ {ep.slave}:{ep.iface} {vlan_str} (недоступен)")
                item.setForeground(QColor("red"))
            self.group_iface_list.addItem(item)

    def on_add_group_clicked(self):
        name = self.group_name_edit.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Внимание", "Введите название группы")
            return
        if name in self.groups:
            QMessageBox.warning(self, "Внимание", f"Группа '{name}' уже существует")
            return
        self.groups[name] = []
        if self.group_name_edit.findText(name) < 0:
            self.group_name_edit.addItem(name)
        self.group_name_edit.setCurrentText(name)
        self.log_debug(f"➕ Создана группа: {name}")
        self.refresh_group_iface_list()
        self.update_group_iface_combo()

    def on_remove_group_clicked(self):
        name = self.group_name_edit.currentText().strip()
        if not name or name not in self.groups:
            return
        if self.groups[name]:
            reply = QMessageBox.question(
                self, "Подтверждение",
                f"Группа '{name}' содержит {len(self.groups[name])} интерфейсов. Удалить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        del self.groups[name]
        idx = self.group_name_edit.findText(name)
        if idx >= 0:
            self.group_name_edit.removeItem(idx)
        self.log_debug(f"🗑 Удалена группа: {name}")
        self.refresh_group_iface_list()
        self.update_group_iface_combo()

    def on_add_iface_to_group(self):
        name = self.group_name_edit.currentText().strip()
        if not name or name not in self.groups:
            QMessageBox.warning(self, "Внимание", "Сначала создайте или выберите группу")
            return
        data = self.group_iface_combo.currentData()
        if data is None:
            return
        slave, iface = data

        for g_name, eps in list(self.groups.items()):
            if g_name == name:
                continue
            for ep in eps:
                if ep.slave == slave and ep.iface == iface:
                    eps.remove(ep)
                    self.log_debug(f"🔄 Интерфейс {slave}:{iface} перемещён из группы '{g_name}' в '{name}'")
                    break

        try:
            endpoint = self.make_endpoint((slave, iface))
            self.groups[name].append(endpoint)
            info = self.iface_status.get((slave, iface), {})
            vlans = info.get("vlans", [])
            vlan_str = f"VLAN {', '.join(str(v) for v in vlans)}" if vlans else "нет VLAN"
            self.log_user(f"➕ Добавлен {slave}:{iface} ({vlan_str}) в группу '{name}'")
            self.refresh_group_iface_list()
            self.update_group_iface_combo()
        except RuntimeError as e:
            QMessageBox.warning(self, "Внимание", str(e))

    def on_remove_iface_from_group(self):
        name = self.group_name_edit.currentText().strip()
        if not name or name not in self.groups:
            return
        current_row = self.group_iface_list.currentRow()
        if current_row < 0 or current_row >= len(self.groups[name]):
            QMessageBox.warning(self, "Внимание", "Выберите интерфейс для удаления")
            return
        ep = self.groups[name].pop(current_row)
        self.log_user(f"🗑 Удалён {ep.slave}:{ep.iface} из группы '{name}'")
        self.refresh_group_iface_list()
        self.update_group_iface_combo()

    def save_groups(self):
        if not self.groups:
            QMessageBox.information(self, "Нет групп", "Нет групп для сохранения")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить группы", "groups.json", "JSON (*.json)"
        )
        if not file_path:
            return

        data = {"groups": {}}
        for group_name, endpoints in self.groups.items():
            data["groups"][group_name] = []
            for ep in endpoints:
                data["groups"][group_name].append({
                    "slave": ep.slave,
                    "host": ep.host,
                    "iface": ep.iface,
                    "mac": ep.mac,
                    "vlans": ep.vlans
                })

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log_debug(f"💾 Группы сохранены в {file_path}")
            QMessageBox.information(self, "Готово", f"Группы сохранены в {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить группы: {e}")

    def load_groups(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить группы", "", "JSON (*.json)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "groups" not in data:
                QMessageBox.critical(self, "Ошибка", "Неверный формат файла")
                return

            loaded_groups = 0
            for group_name, endpoints_data in data["groups"].items():
                if group_name in self.groups:
                    reply = QMessageBox.question(
                        self, "Группа существует",
                        f"Группа '{group_name}' уже существует. Заменить?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.No:
                        continue
                    del self.groups[group_name]
                    idx = self.group_name_edit.findText(group_name)
                    if idx >= 0:
                        self.group_name_edit.removeItem(idx)

                endpoints = []
                for ep_data in endpoints_data:
                    try:
                        ep = EndpointRef(
                            slave=ep_data["slave"],
                            host=ep_data["host"],
                            iface=ep_data["iface"],
                            mac=ep_data["mac"],
                            vlans=ep_data.get("vlans", [])
                        )
                        slave_cfg = self.topology.get("slaves", {}).get(ep.slave, {})
                        iface_cfg = slave_cfg.get("interfaces", {}).get(ep.iface, {})
                        ep.vlans = iface_cfg.get("vlans", [])
                        endpoints.append(ep)
                    except KeyError:
                        continue

                if endpoints:
                    self.groups[group_name] = endpoints
                    if self.group_name_edit.findText(group_name) < 0:
                        self.group_name_edit.addItem(group_name)
                    loaded_groups += 1

            if loaded_groups > 0:
                self.log_debug(f"📂 Загружено {loaded_groups} групп из {file_path}")
                self.group_name_edit.setCurrentIndex(0)
                self.refresh_group_iface_list()
                self.update_group_iface_combo()
                QMessageBox.information(self, "Готово", f"Загружено {loaded_groups} групп")
            else:
                QMessageBox.warning(self, "Внимание", "Не удалось загрузить ни одной группы")

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить группы: {e}")

    # ------------------------------------------------------------------------
    # Обновление устройств
    # ------------------------------------------------------------------------
    def update_status(self, text: str, status_type: str = "info"):
        """
        Обновляет статусную метку с цветом.
        """
        colors = {
            "info": "#2196F3",
            "ok": "#4caf50",
            "warning": "#ff9800",
            "error": "#f44336",
            "loading": "#ffaa00"
        }
        
        color = colors.get(status_type, "#888888")
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.status_label.repaint()
        
    def on_auto_refresh_toggled(self, state):
        if state == Qt.CheckState.Checked:
            self.refresh_timer.start(30000)
            self.update_status("ℹ️ Автообновление вкл", "info")
        else:
            self.refresh_timer.stop()
            self.update_status("ℹ️ Автообновление выкл", "warning")

    def refresh_slaves(self):
        self.update_status("⏳ Обновление...", "loading")
        threading.Thread(target=self._refresh_slaves_thread, daemon=True).start()

    def _refresh_slaves_thread(self):
        slaves = self.topology.get("slaves", {})
        new_status = {}
        new_online = {}

        for slave_name, info in slaves.items():
            host = info.get("host")
            port = info.get("port", 5959)
            client = AgentClient(host, port, timeout=3.0)
            was_online = self.prev_agent_status.get(slave_name, False)
            try:
                if client.ping():
                    new_online[slave_name] = True
                    if not was_online:
                        self.log_debug(f"✅ Агент {slave_name} стал онлайн")
                    ifaces = client.list_ifaces()
                    for iface_info in ifaces:
                        iface = iface_info["iface"]
                        mac = iface_info.get("mac", "")
                        up = iface_info.get("up", False)
                        speed = iface_info.get("speed")
                        slave_cfg = self.topology.get("slaves", {}).get(slave_name, {})
                        iface_cfg = slave_cfg.get("interfaces", {}).get(iface, {})
                        vlans = iface_cfg.get("vlans", [])
                        new_status[(slave_name, iface)] = {
                            "mac": mac,
                            "up": up,
                            "speed": speed,
                            "vlans": vlans,
                        }
                    self.log_debug(f"📡 {slave_name}: получено {len(ifaces)} интерфейсов")
                else:
                    new_online[slave_name] = False
                    if was_online:
                        self.log_debug(f"❌ Агент {slave_name} стал офлайн")
            except Exception as e:
                new_online[slave_name] = False
                if was_online:
                    self.log_debug(f"❌ Ошибка подключения к {slave_name}: {e}")

        self.prev_agent_status = new_online.copy()
        self._new_status = new_status
        self._new_online = new_online
        self.update_ui_signal.emit()

    def _update_ui(self):
        self.iface_status = self._new_status
        self.agent_online = self._new_online
        
        self.rebuild_endpoint_lists()
        self.update_group_iface_combo()
        self.refresh_group_iface_list()
        self.update_device_table()
        
        # Верхний статус — всегда "Готово" после обновления
        self.update_status("✅ Готово", "ok")
        self.repaint()

    def update_device_table(self):
        self.device_table.setRowCount(0)
        row = 0
        for (slave, iface), info in sorted(self.iface_status.items()):
            self.device_table.insertRow(row)
            status = "UP" if info.get("up") else "DOWN"
            speed = str(info.get("speed")) if info.get("speed") is not None else "—"
            vlans = info.get("vlans", [])
            vlans_str = ", ".join(str(v) for v in vlans) if vlans else "—"

            values = [
                slave,
                iface,
                info.get("mac", "—"),
                status,
                speed,
                vlans_str,
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.device_table.setItem(row, col, item)
            row += 1

    def make_endpoint(self, ep: tuple[str, str]) -> EndpointRef:
        slave, iface = ep
        host = self.topology["slaves"][slave]["host"]
        info = self.iface_status.get(ep)
        
        if info is None:
            raise RuntimeError(f"Нет информации об интерфейсе {slave}:{iface}")
        if not info.get("up", False):
            raise RuntimeError(f"Интерфейс {slave}:{iface} не активен (DOWN)")
        
        mac = info.get("mac")
        if not mac:
            raise RuntimeError(f"Нет MAC для {slave}:{iface}")
        
        # Сначала пробуем взять VLAN из iface_status
        vlans = info.get("vlans", [])
        
        # Если VLAN пустые — берём из topology.json (fallback)
        if not vlans:
            slave_cfg = self.topology.get("slaves", {}).get(slave, {})
            iface_cfg = slave_cfg.get("interfaces", {}).get(iface, {})
            vlans = iface_cfg.get("vlans", [])
        
        return EndpointRef(slave=slave, host=host, iface=iface, mac=mac, vlans=vlans)

    # ------------------------------------------------------------------------
    # Запуск тестов
    # ------------------------------------------------------------------------
    def on_run_single_clicked(self):
        sender_ep = self.sender_combo.currentData()
        receiver_ep = self.receiver_combo.currentData()
        if not sender_ep or not receiver_ep:
            QMessageBox.warning(self, "Внимание", "Выберите sender и receiver")
            return

        try:
            sender = self.make_endpoint(sender_ep)
            receiver = self.make_endpoint(receiver_ep)
        except RuntimeError as e:
            QMessageBox.warning(self, "Внимание", str(e))
            return

        params = self._get_params_from_box(self.single_params_box)
        dst_type = params.pop("dst_type", 0)
        self.log_debug(f"🔍 on_run_single_clicked: dst_type = {dst_type}")

        # === MULTICAST: определить dst_mac ===
        dst_mac_override = None
        if dst_type == 1:   # Multicast
            dst_mac_override = "01:00:5E:00:00:01"
        elif dst_type == 2: # Broadcast
            dst_mac_override = "ff:ff:ff:ff:ff:ff"
        # иначе unicast — оставляем None

        common_vlan = find_common_vlan([sender, receiver])
        if common_vlan is None:
            self.log_user(f"⚠️ ВНИМАНИЕ: {sender.slave}:{sender.iface} и {receiver.slave}:{receiver.iface} не имеют общего VLAN (или VLAN не заданы). Изоляция не гарантируется.")
        else:
            self.log_user(f"ℹ️ Общий VLAN: {common_vlan}")

        self.log_debug(f"🔧 Параметры одиночного теста: {params}")
        eta = self._estimate_finish_time(params)
        self.log_user(
            f"▶️ Тест запущен: {sender.slave}:{sender.iface} → {receiver.slave}:{receiver.iface}, "
            f"ожидаемое завершение: {eta}"
        )

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.test_status_label.setText("⏳ Тест выполняется...")
        self.test_status_label.setStyleSheet("color: #ffaa00; font-weight: bold;")

        worker = TestWorker(sender, receiver, params, "single", dst_mac_override=dst_mac_override, dst_type=dst_type)
        worker.log_message.connect(self.log_user)
        worker.autoprobe_started.connect(lambda: self.stop_btn.setEnabled(False))
        worker.autoprobe_finished.connect(lambda: self.stop_btn.setEnabled(True))

        worker.finished_ok.connect(self.on_single_test_finished)
        worker.finished_err.connect(self.on_single_test_error)
        worker.progress.connect(self.on_test_progress)
        worker.start()
        self.workers.append(worker)

    def on_test_progress(self, msg: str):
        """Обновляет статус теста."""
        self.test_status_label.setText(msg)
        self.test_status_label.setStyleSheet("color: #ffaa00; font-weight: bold;")
        
    def on_stop_single_clicked(self):
        if self.workers:
            worker = self.workers[-1]
            if isinstance(worker, TestWorker):
                self.stop_btn.setEnabled(False)
                self.log_user("⏹ Остановка одиночного теста...")
                worker.stop()

    def on_stop_group_clicked(self):
        if self.group_worker:
            self.stop_group_btn.setEnabled(False)
            self.log_user("⏹ Остановка группы тестов...")
            self.group_worker.stop()
            QTimer.singleShot(2000, lambda: self.stop_group_btn.setEnabled(True))
        elif self.multicast_worker:
            self.stop_group_btn.setEnabled(False)
            self.log_user("⏹ Остановка multicast теста...")
            self.multicast_worker.stop()
            QTimer.singleShot(2000, lambda: self.stop_group_btn.setEnabled(True))

    def on_run_group_clicked(self):
        group_name = self.group_name_edit.currentText().strip()
        if not group_name or group_name not in self.groups:
            QMessageBox.warning(self, "Внимание", "Выберите существующую группу")
            return
        endpoints = self.groups[group_name]
        if len(endpoints) < 2:
            QMessageBox.warning(self, "Внимание", "В группе должно быть минимум 2 интерфейса")
            return

        down_members = [ep for ep in endpoints if not self.is_interface_up(ep.slave, ep.iface)]
        if down_members:
            names = ", ".join(f"{ep.slave}:{ep.iface}" for ep in down_members)
            QMessageBox.warning(
                self, "Недоступные интерфейсы",
                f"Следующие интерфейсы группы '{group_name}' сейчас недоступны: {names}"
            )
            return

        params = self._get_params_from_box(self.group_params_box)
        dst_type = params.pop("dst_type", 0)
        self.log_debug(f"🔍 on_run_group_clicked: dst_type = {dst_type}")

        # === MULTICAST / BROADCAST ===
        if dst_type == 1 or dst_type == 2:
            self.log_debug(f"🔍 Запуск multicast/broadcast для группы '{group_name}', dst_type={dst_type}")

            vlan_to_interfaces = {}
            for ep in endpoints:
                for vlan in ep.vlans:
                    if vlan not in vlan_to_interfaces:
                        vlan_to_interfaces[vlan] = []
                    vlan_to_interfaces[vlan].append(ep)

            valid_vlans = {vlan: ifaces for vlan, ifaces in vlan_to_interfaces.items() if len(ifaces) >= 2}
            if not valid_vlans:
                self.log_user("⚠️ Нет VLAN с двумя и более интерфейсами для multicast/broadcast")
                QMessageBox.warning(self, "Внимание", "Нет VLAN с двумя и более интерфейсами для multicast/broadcast")
                return

            self.log_user(f"▶️▶️ Запуск multicast/broadcast для VLAN: {list(valid_vlans.keys())}")

            # === ВЫЧИСЛЯЕМ ОЖИДАЕМОЕ ВРЕМЯ ЗАВЕРШЕНИЯ ===
            eta = self._estimate_finish_time(params)
            self.log_user(f"⏳ Ожидаемое завершение каждого теста: ~{eta} (параллельно)")

            self.run_group_btn.setEnabled(False)
            self.stop_group_btn.setEnabled(True)
            self.group_status_label.setText("Запуск multicast...")
            self.group_results_table.setRowCount(0)

            self.multicast_worker = MulticastMasterWorker(
                vlan_to_interfaces=valid_vlans,
                params=params,
                dst_type=dst_type,
                group_name=group_name
            )
            self.multicast_worker.log_message.connect(self.log_user)
            self.multicast_worker.autoprobe_started.connect(lambda: self.stop_group_btn.setEnabled(False))
            self.multicast_worker.autoprobe_finished.connect(lambda: self.stop_group_btn.setEnabled(True))
            self.multicast_worker.finished_ok.connect(self.on_multicast_master_finished)
            self.multicast_worker.finished_err.connect(self.on_multicast_master_error)
            self.multicast_worker.progress.connect(lambda msg: self.group_status_label.setText(msg))
            self.multicast_worker.test_complete.connect(self.on_group_test_complete)
            self.multicast_worker.start()

            return

        # === ОБЫЧНЫЙ ГРУППОВОЙ ТЕСТ ===
        pairs = []
        n = len(endpoints)
        for i in range(n):
            sender = endpoints[i]
            receiver = endpoints[(i + 1) % n]
            pairs.append((sender, receiver))

        for idx, (sender, receiver) in enumerate(pairs, 1):
            common_vlan = find_common_vlan([sender, receiver])
            if common_vlan is None:
                self.log_user(f"⚠️ Пара {idx}: {sender.slave}:{sender.iface} → {receiver.slave}:{receiver.iface} — нет общего VLAN (ожидаются 100% потерь)")
            else:
                self.log_user(f"ℹ️ Пара {idx}: {sender.slave}:{sender.iface} → {receiver.slave}:{receiver.iface} — общий VLAN {common_vlan}")

        self.log_debug(f"🔧 Параметры группового теста: {params}")
        eta = self._estimate_finish_time(params)
        self.log_user(
            f"▶️▶️ Группа '{group_name}' запущена: {len(pairs)} тест(ов), "
            f"ожидаемое завершение каждого теста: ~{eta} (параллельно)"
        )
        self.run_group_btn.setEnabled(False)
        self.stop_group_btn.setEnabled(True)
        self.group_status_label.setText("Запуск группы (параллельно)...")
        self.group_results_table.setRowCount(0)

        self.group_worker = GroupTestWorker(pairs, params, group_name)
        self.group_worker.log_message.connect(self.log_user)
        self.group_worker.autoprobe_started.connect(lambda: self.stop_group_btn.setEnabled(False))
        self.group_worker.autoprobe_finished.connect(lambda: self.stop_group_btn.setEnabled(True))
        self.group_worker.finished_ok.connect(self.on_group_finished)
        self.group_worker.finished_err.connect(self.on_group_error)
        self.group_worker.progress.connect(lambda msg: self.group_status_label.setText(msg))
        self.group_worker.test_complete.connect(self.on_group_test_complete)
        self.group_worker.start()

    def on_multicast_master_finished(self, results):
        """Обработка завершения multicast мастер-воркера."""
        self.results.extend(results)
        self.group_results_table.setRowCount(0)
        for res in results:
            self.append_group_result_row(res)
            self.append_row(res)
        self.run_group_btn.setEnabled(True)
        self.stop_group_btn.setEnabled(False)
        self.group_status_label.setText(f"Multicast завершён: {len(results)} результатов")
        self.log_user(f"✅ Multicast тест завершён: {len(results)} результатов")
        self.multicast_worker = None

    def on_multicast_master_error(self, error_msg):
        """Обработка ошибки multicast мастер-воркера."""
        self.run_group_btn.setEnabled(True)
        self.stop_group_btn.setEnabled(False)
        self.group_status_label.setText("Ошибка")
        self.log_user(f"❌ Ошибка multicast: {error_msg}")
        QMessageBox.critical(self, "Ошибка multicast", error_msg)
        self.multicast_worker = None
        
        
    def on_test_finished(self, result: dict):
        self.run_btn.setEnabled(True)
        self.status_label.setText("Готово")
        self.results.append(result)
        self.append_row(result)

        receiver = result["receiver"]
        loss_pct = receiver.get("loss_pct", 0.0)
        if receiver.get("trailing_loss_detected"):
            msg = (f"⚠️ ОБРЫВ СВЯЗИ: {receiver['slave']}:{receiver['iface']} - "
                f"потери {loss_pct:.1f}% не были определены внутренним счётчиком "
                f"(похоже на физический разрыв во время теста)")
            self.log_user(msg)
            QMessageBox.warning(self, "Обнаружен обрыв связи", msg)

        if result.get("nic_issue_detected"):
            s_nic = result["sender"].get("nic_stats", {})
            r_nic = receiver.get("nic_stats", {})
            problems = []
            for label, stats in (("sender", s_nic), ("receiver", r_nic)):
                for field, value in stats.items():
                    if value:
                        problems.append(f"{label}.{field}={value}")
            self.log_user(f"⚠️ Обнаружены ошибки/дропы на уровне NIC: {', '.join(problems)}")

    # ------------------------------------------------------------------------
    # Результаты тестов
    # ------------------------------------------------------------------------
    def on_single_test_finished(self, result: dict):
        """Обрабатывает завершение одиночного теста."""
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Готово")
        self.test_status_label.setText("✅ Тест завершён")
        self.test_status_label.setStyleSheet("color: #4caf50; font-weight: bold;")
        self.results.append(result)
        self.append_row(result)
        receiver = result.get("receiver", {})
        sender = result.get("sender", {})
        loss_pct = receiver.get("loss_pct", 0.0)
        common_vlan = result.get("common_vlan")

        # Логируем результат
        if common_vlan is None:
            self.log_user(
                f"⚠️ Тест без общего VLAN: {sender.get('slave', '?')}:{sender.get('iface', '?')} → "
                f"{receiver.get('slave', '?')}:{receiver.get('iface', '?')}, "
                f"потери {loss_pct:.2f}% (изоляция не гарантирована)"
            )
        else:
            self.log_user(
                f"✅ Тест завершён (VLAN {common_vlan}): {sender.get('slave', '?')}:{sender.get('iface', '?')} → "
                f"{receiver.get('slave', '?')}:{receiver.get('iface', '?')}, потери {loss_pct:.2f}%"
            )

        # === ОБРЫВ СВЯЗИ (trailing loss) ===
        if receiver.get("trailing_loss_detected"):
            msg = (f"⚠️ ОБРЫВ СВЯЗИ: {receiver.get('slave', '?')}:{receiver.get('iface', '?')} - "
                f"потери {loss_pct:.1f}% не были определены внутренним счётчиком "
                f"(похоже на физический разрыв во время теста)")
            self.log_user(msg)
            QMessageBox.warning(self, "Обнаружен обрыв связи", msg)

        # === ОШИБКИ NIC ===
        if result.get("nic_issue_detected"):
            s_nic = sender.get("nic_stats", {})
            r_nic = receiver.get("nic_stats", {})
            problems = []
            
            for label, stats in (("sender", s_nic), ("receiver", r_nic)):
                for field, value in stats.items():
                    if value:
                        problems.append(f"{label}.{field}={value}")
            
            if problems:
                self.log_user(f"⚠️ Обнаружены ошибки/дропы на уровне NIC: {', '.join(problems)}")

        # === ОСТАНОВКА ТЕСТА ===
        if result.get("is_stopped"):
            self.log_user(f"⏹ Тест был остановлен досрочно")

    def on_single_test_error(self, message: str):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        # Статус теста
        self.test_status_label.setText("❌ Ошибка")
        self.test_status_label.setStyleSheet("color: #f44336; font-weight: bold;")
        
        self.log_user(f"❌ Ошибка: {message}")
        QMessageBox.critical(self, "Ошибка теста", message)

    def on_group_finished(self, results: list):
        self.run_group_btn.setEnabled(True)
        self.stop_group_btn.setEnabled(False)
        self.group_status_label.setText(f"Группа завершена: {len(results)} тестов")

        valid_results = [r for r in results if isinstance(r, dict) and "sender" in r and "receiver" in r]
        if not valid_results:
            self.log_user("⚠️ Группа завершена без успешных результатов")
            return

        self.results.extend(valid_results)
        self.group_results_table.setRowCount(0)
        for res in valid_results:
            self.append_group_result_row(res)
            self.append_row(res)

        if valid_results:
            self.log_user(f"✅ Группа '{valid_results[0].get('group_name', '')}' завершена: {len(valid_results)} тестов")

    def on_group_error(self, message: str):
        self.run_group_btn.setEnabled(True)
        self.stop_group_btn.setEnabled(False)
        self.group_status_label.setText("Ошибка")
        self.log_debug(f"❌ Ошибка группы: {message}")
        QMessageBox.critical(self, "Ошибка группы", message)

    def on_group_test_complete(self, result: dict):
        self.append_group_result_row(result)
        receiver = result.get("receiver", {})
        sender = result.get("sender", {})
        loss_pct = receiver.get("loss_pct", 0.0)
        common_vlan = result.get("common_vlan")
        group_name = result.get("group_name", "")
        test_index = result.get("test_index", "?")
        total_tests = result.get("total_tests", "?")

        dst_type = result.get("dst_type", 0)  # 0=unicast, 1=multicast, 2=broadcast

        # Формируем сообщение в зависимости от типа теста
        if dst_type == 1:
            sender_display = f"VLAN {common_vlan} (multicast)" if common_vlan is not None else "VLAN None (multicast)"
        elif dst_type == 2:
            sender_display = f"VLAN {common_vlan} (broadcast)" if common_vlan is not None else "VLAN None (broadcast)"
        else:
            # Unicast — показываем sender интерфейс
            sender_display = f"{sender.get('slave', '?')}:{sender.get('iface', '?')}"

        if common_vlan is None:
            self.log_user(
                f"⚠️ [{group_name}] Тест {test_index}/{total_tests} (без общего VLAN): "
                f"{sender_display} → {receiver.get('slave', '?')}:{receiver.get('iface', '?')}, потери {loss_pct:.2f}%"
            )
        else:
            self.log_user(
                f"✅ [{group_name}] Тест {test_index}/{total_tests} (VLAN {common_vlan}): "
                f"{sender_display} → {receiver.get('slave', '?')}:{receiver.get('iface', '?')}, потери {loss_pct:.2f}%"
            )

        # === ОБРЫВ СВЯЗИ ===
        if result.get("is_stopped", False):
            # Если тест остановлен, не показываем обрыв
            pass
        elif receiver.get("trailing_loss_detected"):
            self.log_user(
                f"⚠️ ОБРЫВ СВЯЗИ на {receiver.get('slave', '?')}:{receiver.get('iface', '?')} - "
                f"потери {loss_pct:.1f}% не видны внутреннему счётчику (физический разрыв)"
            )
        a = result.get("is_stopped") 
        b = receiver.get("trailing_loss_detected")
        # === ОШИБКИ NIC ===
        if result.get("nic_issue_detected"):
            # Для multicast/broadcast ошибки могут быть как у sender'ов, так и у receiver
            if dst_type == 1 or dst_type == 2:
                # Получаем полные данные о NIC
                sender_stats = result.get("sender_stats", [])
                sender_tx_stats = result.get("sender_tx_stats", {})  # mac -> tx_stats
                receiver_nic_stats = result.get("receiver_nic_stats", {})  # rx_stats
                
                # Собираем все проблемы
                problems = []
                
                # Проверяем RX ошибки receiver
                error_fields = (
                    "rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
                    "rx_fifo_errors", "tx_fifo_errors", "rx_over_errors",
                    "rx_frame_errors", "rx_crc_errors", "collisions",
                    "rx_mac_missed", "tx_aborted", "tx_underrun",
                    "rx_jabber", "rx_oversize", "rx_undersize", "rx_align_errors",
                )
                
                for field in error_fields:
                    value = receiver_nic_stats.get(field, 0)
                    if value:
                        problems.append(f"receiver.{field}={value}")
                
                # Проверяем TX ошибки каждого sender'а
                for stat in sender_stats:
                    mac = stat.get("mac", "")
                    tx_stats = sender_tx_stats.get(mac, {})
                    # Находим имя sender'а
                    sender_name = "unknown"
                    for ep in self.groups.get(group_name, []):
                        if ep.mac == mac:
                            sender_name = f"{ep.slave}:{ep.iface}"
                            break
                    
                    for field in error_fields:
                        value = tx_stats.get(field, 0)
                        if value:
                            problems.append(f"sender {sender_name}.{field}={value}")
                
                if problems:
                    self.log_user(
                        f"⚠️ [{receiver.get('slave', '?')}:{receiver.get('iface', '?')}] обнаружены ошибки/дропы NIC: {', '.join(problems)}"
                    )
            else:
                # Unicast - используем существующую логику
                s_nic = sender.get("nic_stats", {})
                r_nic = receiver.get("nic_stats", {})
                error_fields = (
                    "rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
                    "rx_fifo_errors", "tx_fifo_errors", "rx_over_errors",
                    "rx_frame_errors", "rx_crc_errors", "collisions",
                    "rx_mac_missed", "tx_aborted", "tx_underrun",
                    "rx_jabber", "rx_oversize", "rx_undersize", "rx_align_errors",
                )
                problems = []
                for label, stats in (("sender", s_nic), ("receiver", r_nic)):
                    for field in error_fields:
                        value = stats.get(field, 0)
                        if value:
                            problems.append(f"{label}.{field}={value}")
                if problems:
                    self.log_user(
                        f"⚠️ [{receiver.get('slave', '?')}:{receiver.get('iface', '?')}] обнаружены ошибки/дропы NIC: {', '.join(problems)}"
                    )

    def append_row(self, result: dict):
        row = self.table.rowCount()
        self.table.insertRow(row)

        dst_type = result.get("dst_type", 0)
        sender = result.get("sender", {})
        receiver = result.get("receiver", {})
        common_vlan = result.get("common_vlan")
        sender_stats = result.get("sender_stats", [])
        if dst_type == 1:
            sender_display = f"VLAN {common_vlan} (multicast)" if common_vlan is not None else "VLAN None (multicast)"
        elif dst_type == 2:
            sender_display = f"VLAN {common_vlan} (broadcast)" if common_vlan is not None else "VLAN None (broadcast)"
        else:
            sender_display = f"{sender.get('slave', '?')}:{sender.get('iface', '?')}"

        packets_sent = sender.get("packets_sent", 0)
        bytes_sent = sender.get("bytes_sent", 0)
        
        sender_packets = sender.get("packets_sent", 0)
        receiver_packets = receiver.get("packets_received", 0)

        sender_bytes = sender.get("bytes_sent", 0)
        receiver_bytes = receiver.get("bytes_received", 0)

        rate = result.get("auto_rate_pps") or result.get("rate_pps", "")
        lost = receiver.get("packets_lost", 0)
        sent = sender_packets
        lost_percent = (lost / sent * 100) if sent > 0 else 0.0
        nic_issue = result.get("nic_issue_detected", False)
        nic_status = "⚠️" if nic_issue else "✅"
        nic_tooltip = "Ошибки/дропы NIC обнаружены" if nic_issue else "NIC OK"

        values = [
            result.get("group_name", ""),
            result.get("test_id", ""),
            sender_display,
            f"{receiver.get('slave', '?')}:{receiver.get('iface', '?')}",            
            f"{packets_sent:,}",
            f"{bytes_sent:,}",
            f"{receiver_packets:,}",
            f"{receiver_bytes:,}",
            f"{lost:,}",
            f"{lost_percent:.2f}%",
            str(rate),
            f"{sender.get('duration_s', 0):.2f}",
            nic_status,
        ]
        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if col == 12:
                item.setToolTip(nic_tooltip)
            self.table.setItem(row, col, item)
        self.table.scrollToBottom()

    def append_group_result_row(self, result: dict):
        row = self.group_results_table.rowCount()
        self.group_results_table.insertRow(row)
        
        dst_type = result.get("dst_type", 0)
        sender = result.get("sender", {})
        receiver = result.get("receiver", {})
        common_vlan = result.get("common_vlan")
        
        if dst_type == 1:
            sender_display = f"VLAN {common_vlan} (multicast)" if common_vlan is not None else "VLAN None (multicast)"
            lost = receiver.get("packets_lost", 0)
            lost_percent = receiver.get("loss_pct", 0)
        elif dst_type == 2:
            sender_display = f"VLAN {common_vlan} (broadcast)" if common_vlan is not None else "VLAN None (broadcast)"
            lost = receiver.get("packets_lost", 0)
            lost_percent = receiver.get("loss_pct", 0)
        else:
            sender_display = f"{sender.get('slave', '?')}:{sender.get('iface', '?')}"
            sent = sender.get("packets_sent", 1)
            lost = receiver.get("packets_lost", 0)
            lost_percent = (lost / sent) * 100.0 if sent > 0 else 0.0
        rate = result.get("auto_rate_pps") or result.get("rate_pps", "")
        values = [
            str(row + 1),
            sender_display,
            f"{receiver.get('slave', '?')}:{receiver.get('iface', '?')}",
            str(lost),
            f"{lost_percent:.2f}%",
            str(rate),
        ]
        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.group_results_table.setItem(row, col, item)

    # ------------------------------------------------------------------------
    # NIC статистика (двойной клик)
    # ------------------------------------------------------------------------
    def on_table_double_click(self, row: int, col: int):
        if row >= len(self.results):
            return
        result = self.results[row]
        dst_type = result.get("dst_type", 0)
        
        if dst_type == 1 or dst_type == 2:
            sender_results = result.get("_sender_results", {})
            if not sender_results and hasattr(self, 'multicast_worker') and self.multicast_worker:
                # Если результат не содержит sender_results, пытаемся получить из worker'а
                sender_results = getattr(self.multicast_worker, 'sender_results', {})
            self.show_multicast_senders(result, sender_results)
        else:
            self.show_nic_details(result)

    def normalize_mac(self, mac: str) -> str:
        """Приводит MAC-адрес к единому формату (нижний регистр, без пробелов)."""
        if not mac:
            return ""
        return mac.strip().lower()

    def show_multicast_senders(self, result: dict, sender_results: dict = None):
        """Показывает диалог со списком sender'ов для multicast/broadcast теста."""
        receiver = result.get("receiver", {})
        sender_stats = result.get("sender_stats", [])
        common_vlan = result.get("common_vlan")
        group_name = result.get("group_name", "")
        sender_tx_stats = result.get("sender_tx_stats", {})       # mac -> tx_stats
        receiver_nic_stats = result.get("receiver_nic_stats", {}) # RX статистика receiver'а
        receiver_name = f"{receiver.get('slave')}:{receiver.get('iface')}"

        if not receiver:
            QMessageBox.information(self, "Информация", "Нет информации о receiver")
            return

        if not sender_stats:
            QMessageBox.information(self, "Информация", "Нет статистики по sender'ам")
            return

        # Получаем интерфейсы группы по имени
        endpoints = self.groups.get(group_name, [])
        if not endpoints:
            # Если группа не найдена, ищем по VLAN
            for g_name, eps in self.groups.items():
                for ep in eps:
                    if common_vlan in ep.vlans:
                        endpoints = eps
                        group_name = g_name
                        break
                if endpoints:
                    break

        # Если всё ещё нет — берём все интерфейсы из всех групп
        if not endpoints:
            for eps in self.groups.values():
                endpoints.extend(eps)

        # Строим словарь нормализованных MAC -> EndpointRef
        mac_to_interface = {}
        for ep in endpoints:
            if ep.mac:
                mac_to_interface[self.normalize_mac(ep.mac)] = ep

        # Сопоставляем sender_stats с интерфейсами
        filtered_stats = []
        for stat in sender_stats:
            mac = stat.get("mac", "")
            if not mac:
                continue
            norm_mac = self.normalize_mac(mac)
            ep = mac_to_interface.get(norm_mac)
            filtered_stats.append((stat, ep, mac))

        if not filtered_stats:
            QMessageBox.information(
                self,
                "Информация",
                f"Нет sender'ов для receiver {receiver.get('slave')}:{receiver.get('iface')}"
            )
            return

        # Создаём диалог
        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"Sender'ы для {receiver.get('slave')}:{receiver.get('iface')} "
            f"(VLAN {common_vlan if common_vlan is not None else 'None'})"
        )
        dialog.setMinimumSize(800, 450)
        layout = QVBoxLayout(dialog)

        label = QLabel(
            f"Список sender'ов, отправлявших на {receiver.get('slave')}:{receiver.get('iface')} "
            f"(multicast/broadcast, VLAN {common_vlan if common_vlan is not None else 'None'}):"
        )
        layout.addWidget(label)

        # ========== ДОБАВЛЯЕМ ФУНКЦИЮ ПРОВЕРКИ ОШИБОК ==========
        error_fields = (
            "rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
            "rx_fifo_errors", "tx_fifo_errors", "rx_over_errors",
            "rx_frame_errors", "rx_crc_errors", "collisions",
            "rx_mac_missed", "tx_aborted", "tx_underrun",
            "rx_jabber", "rx_oversize", "rx_undersize", "rx_align_errors",
        )
        
        def has_issue(stats):
            return any((stats.get(f) or 0) > 0 for f in error_fields)
        
        # Проверяем ошибки у receiver
        receiver_has_issue = has_issue(receiver_nic_stats)
        # ========================================================

        table = QTableWidget(len(filtered_stats), 8)  # увеличиваем количество колонок
        table.setHorizontalHeaderLabels([
            "Sender",
            "Отправлено",      # packets_sent от sender'а
            "Байт отправ.",    # bytes_sent от sender'а
            "Получено",        # packets_received от receiver'а
            "Байт получ.",     # bytes_received от receiver'а
            "Потери %",        # (packets_sent - packets_received) / packets_sent * 100
            "Out of Order",
            "NIC"
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for idx, (stat, ep, mac) in enumerate(filtered_stats):
            sender_info = sender_results.get(mac, {})
            packets_sent = sender_info.get("packets_sent", 0)
            bytes_sent = sender_info.get("bytes_sent", 0) + packets_sent * 4
            packets_received = stat.get("packets_received", 0)
            bytes_received = stat.get("bytes_received", 0) + packets_received * 4
            lost = max(stat.get("packets_lost", 0), packets_sent - packets_received)
            lost_pct = (lost / packets_sent) * 100.0
            out_of_order = stat.get("out_of_order", 0)
            if ep is not None:
                sender_name = f"{ep.slave}:{ep.iface}"
            else:
                sender_name = mac

            # Проверяем ошибки у sender'а (TX)
            tx_stats = sender_tx_stats.get(mac, {})
            sender_has_issue = has_issue(tx_stats)
            
            # ========== ИСПРАВЛЕНИЕ: комбинируем ошибки sender и receiver ==========
            nic_issue = receiver_has_issue or sender_has_issue
            # ======================================================================

            table.setItem(idx, 0, QTableWidgetItem(sender_name))
            table.setItem(idx, 1, QTableWidgetItem(f"{packets_sent:,}"))
            table.setItem(idx, 2, QTableWidgetItem(f"{bytes_sent:,}"))
            table.setItem(idx, 3, QTableWidgetItem(f"{packets_received:,}"))
            table.setItem(idx, 4, QTableWidgetItem(f"{bytes_received:,}"))
            table.setItem(idx, 5, QTableWidgetItem(f"{lost_pct:.2f}%"))
            table.setItem(idx, 6, QTableWidgetItem(f"{out_of_order:,}"))
            table.setItem(idx, 7, QTableWidgetItem("⚠️" if nic_issue else "✅"))

        # === ДВОЙНОЙ КЛИК: показываем TX статистику sender'а и RX статистику receiver'а ===
        def on_sender_double_click(row, col):
            stat = filtered_stats[row][0]
            mac = stat.get("mac", "")
            # Находим имя sender'а
            ep = mac_to_interface.get(self.normalize_mac(mac))
            sender_name = f"{ep.slave}:{ep.iface}" if ep else mac
            # Получаем TX статистику для этого sender'а
            tx_stats = sender_tx_stats.get(mac, {})
            # RX статистика receiver'а (уже отфильтрована)
            rx_stats = receiver_nic_stats

            self.show_multicast_nic_details(sender_name, tx_stats, receiver_name, rx_stats, mac)

        table.cellDoubleClicked.connect(on_sender_double_click)
        layout.addWidget(table)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        layout.addWidget(close_btn)

        dialog.exec()

    def show_multicast_nic_details(self, sender_name, tx_stats, receiver_name, rx_stats, mac):
        """Показывает диалог с TX статистикой sender'а и RX статистикой receiver'а."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Статистика для sender'а {sender_name} (MAC: {mac})")
        dialog.setMinimumSize(700, 500)
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        tabs = QTabWidget()

        # === Вкладка Sender (TX) ===
        tab_sender = QWidget()
        tab_layout = QVBoxLayout(tab_sender)
        header = QLabel(f"📤 Sender: {sender_name} (TX)")
        header.setStyleSheet("font-weight: bold;")
        tab_layout.addWidget(header)

        if tx_stats:
            table = QTableWidget(0, 2)
            table.setHorizontalHeaderLabels(["Параметр", "Значение"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

            for idx, (key, value) in enumerate(sorted(tx_stats.items())):
                display = f"{value:,}" if isinstance(value, int) else str(value) if value else "0"
                table.insertRow(idx)
                table.setItem(idx, 0, QTableWidgetItem(key))
                table.setItem(idx, 1, QTableWidgetItem(display))

            tab_layout.addWidget(table)
        else:
            tab_layout.addWidget(QLabel("Нет TX статистики для этого sender'а"))

        tabs.addTab(tab_sender, "Sender (TX)")

        # === Вкладка Receiver (RX) ===
        tab_receiver = QWidget()
        tab_layout = QVBoxLayout(tab_receiver)
        header = QLabel(f"📥 Receiver: {receiver_name} (RX)")
        header.setStyleSheet("font-weight: bold;")
        tab_layout.addWidget(header)

        if rx_stats:
            table = QTableWidget(0, 2)
            table.setHorizontalHeaderLabels(["Параметр", "Значение"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

            for idx, (key, value) in enumerate(sorted(rx_stats.items())):
                display = f"{value:,}" if isinstance(value, int) else str(value) if value else "0"
                table.insertRow(idx)
                table.setItem(idx, 0, QTableWidgetItem(key))
                table.setItem(idx, 1, QTableWidgetItem(display))

            tab_layout.addWidget(table)
        else:
            tab_layout.addWidget(QLabel("Нет RX статистики для этого receiver'а"))

        tabs.addTab(tab_receiver, "Receiver (RX)")

        layout.addWidget(tabs)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        layout.addWidget(close_btn)

        dialog.exec()
                
    def show_nic_details(self, result: dict):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"NIC статистика для теста {result.get('test_id', '')}")
        dialog.setMinimumSize(700, 500)
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)

        # Информация о тесте
        info_label = QLabel(
            f"Тест: {result.get('test_id', '')} | "
            f"Скорость: {result.get('rate_pps', 0)} pps | "
            f"Общий VLAN: {result.get('common_vlan', '—')}"
        )
        info_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(info_label)

        tabs = QTabWidget()

        # Sender
        sender = result.get("sender", {})
        sender_nic = sender.get("nic_stats", {})
        if sender_nic:
            tab_sender = QWidget()
            tab_layout = QVBoxLayout(tab_sender)
            header = QLabel(f"📤 Sender: {sender.get('slave', '?')}:{sender.get('iface', '?')}")
            header.setStyleSheet("font-weight: bold;")
            tab_layout.addWidget(header)

            table = QTableWidget(0, 2)
            table.setHorizontalHeaderLabels(["Параметр", "Значение"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

            for idx, (key, value) in enumerate(sorted(sender_nic.items())):
                display = f"{value:,}" if isinstance(value, int) else str(value) if value else "0"
                table.insertRow(idx)
                table.setItem(idx, 0, QTableWidgetItem(key))
                table.setItem(idx, 1, QTableWidgetItem(display))

            tab_layout.addWidget(table)
            tabs.addTab(tab_sender, "Sender (TX)")

        # Receiver
        receiver = result.get("receiver", {})
        receiver_nic = receiver.get("nic_stats", {})
        if receiver_nic:
            tab_receiver = QWidget()
            tab_layout = QVBoxLayout(tab_receiver)
            header = QLabel(f"📥 Receiver: {receiver.get('slave', '?')}:{receiver.get('iface', '?')}")
            header.setStyleSheet("font-weight: bold;")
            tab_layout.addWidget(header)

            table = QTableWidget(0, 2)
            table.setHorizontalHeaderLabels(["Параметр", "Значение"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

            for idx, (key, value) in enumerate(sorted(receiver_nic.items())):
                display = f"{value:,}" if isinstance(value, int) else str(value) if value else "0"
                table.insertRow(idx)
                table.setItem(idx, 0, QTableWidgetItem(key))
                table.setItem(idx, 1, QTableWidgetItem(display))

            tab_layout.addWidget(table)
            tabs.addTab(tab_receiver, "Receiver (RX)")

        layout.addWidget(tabs)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        layout.addWidget(close_btn)

        dialog.exec()

    # ------------------------------------------------------------------------
    # Вспомогательные
    # ------------------------------------------------------------------------
    def _estimate_finish_time(self, params: dict) -> str:
        if params.get("auto_rate"):
            return "неизвестно (идёт автоподбор скорости)"
        if "duration_s" in params:
            seconds = params["duration_s"]
        else:
            seconds = params.get("packet_count", 0) / max(params.get("rate_pps", 1), 1)
        eta = datetime.now() + timedelta(seconds=seconds)
        return eta.strftime("%H:%M:%S")

    def log_user(self, msg: str):
        if hasattr(self, 'log_text'):
            self.log_text.append(msg)

    def log_debug(self, msg: str):
        if hasattr(self, 'debug_log_text'):
            self.debug_log_text.append(msg)

    def on_export_clicked(self):
        if not self.results:
            QMessageBox.information(self, "Нет данных", "Нет результатов для экспорта")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить результаты", "results.json", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        QMessageBox.information(self, "Готово", f"Сохранено в {path}")

    def on_clear_clicked(self):
        self.results.clear()
        self.table.setRowCount(0)
        self.group_results_table.setRowCount(0)


# ============================================================================
# Запуск
# ============================================================================
def main():
    app = QApplication(sys.argv)

    if len(sys.argv) > 1:
        topo_path = sys.argv[1]
    else:
        config_path = Path(__file__).resolve().parent / "config" / "topology.json"
        if config_path.exists():
            topo_path = str(config_path)
        else:
            topo_path = "config/topology.json"

    win = MainWindow(topo_path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()