#!/usr/bin/env python3
"""
agent.py
Slave-агент для Orange Pi R2S (OpenWrt).
Поддержка параллельных тестов на разных интерфейсах.
"""
import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
import re
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from protocol import (
    DEFAULT_AGENT_PORT,
    ProtocolError,
    recv_message,
    send_message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pktgen-agent")

# Поля статистики, которые мы получаем из ethtool -S
ETHTOOL_STATS_FIELDS = [
    "tx_octets", "rx_octets", "tx_packets", "rx_packets",
    "tx_errors", "rx_errors", "rx_missed", "rx_mac_missed",
    "tx_dropped", "rx_dropped", "tx_fifo_errors", "rx_fifo_errors",
    "rx_over_errors", "rx_frame_errors", "rx_crc_errors",
    "collisions", "tx_aborted", "tx_underrun",
]


def get_nic_stats_ethtool(iface: str) -> dict:
    stats = {
        "rx_bytes": 0, "tx_bytes": 0,
        "rx_packets": 0, "tx_packets": 0,
        "rx_errors": 0, "tx_errors": 0,
        "rx_dropped": 0, "tx_dropped": 0,
        "rx_fifo_errors": 0, "tx_fifo_errors": 0,
        "rx_over_errors": 0,
        "rx_frame_errors": 0,
        "rx_crc_errors": 0,
        "collisions": 0,
        "rx_mac_missed": 0,
        "tx_aborted": 0,
        "tx_underrun": 0,
        "rx_jabber": 0,
        "rx_oversize": 0,
        "rx_undersize": 0,
        "rx_align_errors": 0,
    }
    try:
        output = subprocess.check_output(
            ['ethtool', '-S', iface],
            stderr=subprocess.DEVNULL,
            text=True
        )
        for line in output.splitlines():
            line = line.strip()
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            try:
                val_int = int(value)
                val_int = normalize_counter(val_int)
            except ValueError:
                continue
            # Маппинг (как было ранее)
            if key == "tx_ok_pkts":
                stats["tx_packets"] = val_int
            elif key == "tx_ok_bytes":
                stats["tx_bytes"] = val_int
            elif key == "rx_ok_pkts":
                stats["rx_packets"] = val_int
            elif key == "rx_ok_bytes":
                stats["rx_bytes"] = val_int
            elif key == "rx_crc_err_pkts":
                stats["rx_crc_errors"] = val_int
            elif key == "rx_err_total_pkts":
                stats["rx_errors"] = val_int
            elif key == "rx_align_err_pkts":
                stats["rx_frame_errors"] = val_int
                stats["rx_align_errors"] = val_int
            elif key == "rx_drp_fifo_full_pkts":
                stats["rx_fifo_errors"] = val_int
                stats["rx_dropped"] = val_int
            elif key == "rx_len_jabber_pkts":
                stats["rx_jabber"] = val_int
            elif key == "rx_len_oversize_pkts":
                stats["rx_oversize"] = val_int
            elif key == "rx_len_undersize_pkts":
                stats["rx_undersize"] = val_int
            elif key == "rx_len_fragment_pkts":
                stats["rx_undersize"] = val_int
            elif key == "tx_octets":
                stats["tx_bytes"] = val_int
            elif key == "rx_octets":
                stats["rx_bytes"] = val_int
            elif key == "tx_packets":
                stats["tx_packets"] = val_int
            elif key == "rx_packets":
                stats["rx_packets"] = val_int
            elif key == "tx_errors":
                stats["tx_errors"] = val_int
            elif key == "rx_errors":
                stats["rx_errors"] = val_int
            elif key == "rx_missed":
                stats["rx_dropped"] = val_int
            elif key == "rx_mac_missed":
                stats["rx_mac_missed"] = val_int
            elif key == "tx_aborted":
                stats["tx_aborted"] = val_int
            elif key == "tx_underrun":
                stats["tx_underrun"] = val_int
            elif key == "tx_dropped":
                stats["tx_dropped"] = val_int
            elif key == "rx_dropped":
                stats["rx_dropped"] = val_int
            elif key == "tx_fifo_errors":
                stats["tx_fifo_errors"] = val_int
            elif key == "rx_fifo_errors":
                stats["rx_fifo_errors"] = val_int
            elif key == "rx_over_errors":
                stats["rx_over_errors"] = val_int
            elif key == "rx_frame_errors":
                stats["rx_frame_errors"] = val_int
            elif key == "rx_crc_errors":
                stats["rx_crc_errors"] = val_int
            elif key == "collisions":
                stats["collisions"] = val_int
        return stats
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log.error(f"ethtool -S не доступен для {iface}: {e}")
        return stats

def normalize_counter(val: int) -> int:
    """Преобразует отрицательное значение в беззнаковое 32-битное."""
    if val < 0:
        print("DEBUG: 1 << 32")
        return val + 4294967296  # 2^32
    return val

def diff_nic_stats(before: dict, after: dict) -> dict:
    delta = {}
    all_fields = set(before.keys()) | set(after.keys())
    for field in all_fields:
        b = before.get(field, 0)
        a = after.get(field, 0)
        # Корректируем переполнение 32-битного счётчика
        if a < b:
            a += (1 << 32)
        delta[field] = a - b
    return delta


def iface_operstate(iface: str) -> str:
    try:
        with open(f"/sys/class/net/{iface}/operstate", "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "absent"


def iface_carrier(iface: str):
    try:
        with open(f"/sys/class/net/{iface}/carrier", "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def iface_is_up(iface: str) -> bool:
    state = iface_operstate(iface)
    if state == "absent":
        return False
    if state == "up":
        return True
    carrier = iface_carrier(iface)
    return carrier == 1


class TestJob:
    def __init__(self, job_id, role, iface, proc, result_file, nic_stats_before=None):
        self.job_id = job_id
        self.role = role
        self.iface = iface
        self.proc = proc
        self.result_file = result_file
        self.nic_stats_before = nic_stats_before or {}
        self.nic_stats_after = None
        self.nic_stats_snapshots = []
        self.start_time = time.time()
        self.finished = False
        self.result = None
        self.stopped = False
        self.snapshot_interval = 2.0  # <-- НОВОЕ: интервал сбора (сек)
        self.last_snapshot_time = 0
        self.snapshot_lock = threading.Lock()

class Agent:
    def __init__(self, config: dict, pktgen_path: str):
        self.config = config
        self.pktgen_path = pktgen_path
        self.lock = threading.RLock()
        self.jobs: dict = {}
        self.active_receivers: set = set()

    def get_iface_info(self, iface: str):
        for entry in self.config.get("interfaces", []):
            if entry["iface"] == iface:
                return entry
        return None

    def _get_iface_speed(self, iface: str) -> Optional[int]:
        speed_path = f"/sys/class/net/{iface}/speed"
        try:
            with open(speed_path, 'r') as f:
                speed = f.read().strip()
                if speed.isdigit():
                    return int(speed)
        except:
            pass
        return None

    def _take_snapshot(self, job: TestJob):
        """Снимает снимок статистики ethtool для задачи."""
        if job.finished or job.stopped:
            return
        
        try:
            snapshot = get_nic_stats_ethtool(job.iface)
            with job.snapshot_lock:
                job.nic_stats_snapshots.append({
                    "timestamp": time.time(),
                    "stats": snapshot
                })
            log.debug(f"📸 Снимок для {job.job_id} ({job.role}): rx_packets={snapshot.get('rx_packets', 0)}, tx_packets={snapshot.get('tx_packets', 0)}")
        except Exception as e:
            log.error(f"Ошибка снятия снимка для {job.job_id}: {e}")
    
    def _snapshot_collector(self, job: TestJob):
        """Поток для периодического снятия снимков."""
        while not job.finished and not job.stopped:
            time.sleep(job.snapshot_interval)
            if job.finished or job.stopped:
                break
            self._take_snapshot(job)
        log.debug(f"📸 Сбор снимков завершен для {job.job_id}")

    def handle_start_test(self, msg: dict) -> dict:
        role = msg.get("role")
        iface = msg.get("iface")
        wire_test_id = msg.get("test_id") or str(uuid.uuid4())[:8]
        job_id = str(uuid.uuid4())

        if role not in ("sender", "receiver"):
            return {"status": "error", "message": "role must be sender|receiver"}

        iface_info = self.get_iface_info(iface)
        if iface_info is None:
            return {"status": "error", "message": f"iface {iface} not in config"}

        if not iface_is_up(iface):
            return {"status": "error", "message": f"iface {iface} is down/absent"}

        #with self.lock:
        #    if role == "receiver":
        #        if iface in self.active_receivers:
        #            return {
        #                "status": "error",
        #                "message": f"iface {iface} already acting as receiver",
        #            }
        #        self.active_receivers.add(iface)

        numeric_test_id = int.from_bytes(
            str(wire_test_id).encode(), "little", signed=False
        ) & 0xFFFFFFFF

        cmd = [
            self.pktgen_path,
            "--mode", "send" if role == "sender" else "recv",
            "--iface", iface,
            "--test-id", str(numeric_test_id),
            "--size-mode", msg.get("size_mode", "fixed"),
        ]

        packet_count = msg.get("packet_count")
        if packet_count is not None and packet_count > 0:
            cmd += ["--count", str(packet_count)]
        else:
            cmd += ["--duration", str(msg.get("duration_s", 5.0))]

        if role == "sender":
            dst_mac = msg.get("dst_mac")
            if not dst_mac:
                with self.lock:
                    self.active_receivers.discard(iface)
                return {"status": "error", "message": "dst_mac required for sender"}
            cmd += ["--dst-mac", dst_mac]
            if msg.get("src_mac"):
                cmd += ["--src-mac", msg["src_mac"]]
            cmd += ["--rate-pps", str(msg.get("rate_pps", 1000))]
            if msg.get("size_mode") == "random":
                cmd += ["--size-min", str(msg.get("size_min", 64)),
                        "--size-max", str(msg.get("size_max", 1500))]
            else:
                cmd += ["--size", str(msg.get("size", 512))]

        log.info("Запуск задачи job_id=%s wire_test_id=%s (%s) на %s: %s",
                  job_id, wire_test_id, role, iface, " ".join(cmd))

        nic_stats_before = get_nic_stats_ethtool(iface)

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
        except OSError as e:
            with self.lock:
                self.active_receivers.discard(iface)
            return {"status": "error", "message": f"failed to start pktgen: {e}"}

        job = TestJob(job_id, role, iface, proc, None, nic_stats_before=nic_stats_before)
        with self.lock:
            self.jobs[job_id] = job

        threading.Thread(target=self._snapshot_collector, args=(job,), daemon=True).start()
        threading.Thread(target=self._wait_job, args=(job,), daemon=True).start()
        return {"status": "ok", "test_id": job_id}

    def _wait_job(self, job: TestJob):
        out, err = job.proc.communicate()

        if job.role == "sender":
            time.sleep(1.0)

        # Останавливаем сбор снимков
        job.finished = True
        
        # Снимаем финальную статистику
        if job.nic_stats_after is not None:
            nic_stats_after = job.nic_stats_after
        else:
            nic_stats_after = get_nic_stats_ethtool(job.iface)
        
        # Агрегируем статистику из снимков
        nic_stats_delta = self._aggregate_snapshots(job, nic_stats_after)
        
        with self.lock:
            job.finished = True
            if job.role == "receiver":
                self.active_receivers.discard(job.iface)
            
            # Парсим вывод pktgen
            try:
                lines = out.strip().splitlines()
                if lines:
                    job.result = json.loads(lines[-1])
                else:
                    # === НЕТ ВЫВОДА от pktgen ===
                    job.result = {
                        "status": "error",
                        "message": "no output from pktgen",
                        "role": job.role,
                        "iface": job.iface,
                    }
            except (json.JSONDecodeError, IndexError) as e:
                job.result = {
                    "status": "error",
                    "message": f"bad output: {out!r} {err!r}",
                    "raw": out,
                    "role": job.role,
                    "iface": job.iface,
                }
            
            # === ВСЕГДА добавляем статистику, даже если pktgen не вернул результат ===
            if isinstance(job.result, dict):
                job.result["nic_stats_delta"] = nic_stats_delta
                job.result["snapshots_count"] = len(job.nic_stats_snapshots)
                job.result["has_snapshots"] = len(job.nic_stats_snapshots) > 0
                if job.stopped:
                    job.result["stopped"] = True
                
                # Если pktgen не вернул packets_sent, используем ethtool
                if job.role == "sender" and job.result.get("packets_sent", 0) == 0:
                    tx_packets = nic_stats_delta.get("tx_packets", 0)
                    if tx_packets > 0:
                        job.result["packets_sent"] = tx_packets
                        job.result["packets_sent_from_ethtool"] = True
                
                if job.role == "receiver" and not job.result.get("sender_stats"):
                    # Создаем фиктивные sender_stats из ethtool
                    rx_packets = nic_stats_delta.get("rx_packets", 0)
                    if rx_packets > 0:
                        job.result["sender_stats"] = [{
                            "mac": "unknown",
                            "packets_received": rx_packets,
                            "bytes_received": nic_stats_delta.get("rx_bytes", 0),
                            "packets_expected": rx_packets,
                            "packets_lost": 0,
                            "out_of_order": 0,
                            "from_ethtool": True
                        }]
        
        log.info("Задача %s завершена: %s", job.job_id, job.result)

    def _aggregate_snapshots(self, job: TestJob, final_stats: dict) -> dict:
        """Агрегирует все снимки статистики для коррекции переполнения."""
        if not job.nic_stats_snapshots:
            # Если снимков нет, используем обычный diff
            return diff_nic_stats(job.nic_stats_before, final_stats)
        
        # Суммируем дельты между последовательными снимками
        total_delta = {}
        prev_stats = job.nic_stats_before
        
        for snapshot in job.nic_stats_snapshots:
            current_stats = snapshot["stats"]
            delta = diff_nic_stats(prev_stats, current_stats)
            
            # Суммируем
            for key, value in delta.items():
                total_delta[key] = total_delta.get(key, 0) + value
            
            prev_stats = current_stats
        
        # Добавляем дельту от последнего снимка до финальной статистики
        final_delta = diff_nic_stats(prev_stats, final_stats)
        for key, value in final_delta.items():
            total_delta[key] = total_delta.get(key, 0) + value
        
        log.debug(f"📊 Агрегировано {len(job.nic_stats_snapshots)} снимков для {job.job_id}")
        return total_delta
        
    def handle_stop_test(self, msg: dict) -> dict:
        job_id = msg.get("test_id")
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            return {"status": "error", "message": "unknown test_id"}
        
        if not job.finished:
            # === НОВОЕ: снимаем финальный снимок перед остановкой ===
            self._take_snapshot(job)
            
            try:
                job.proc.terminate()
                try:
                    job.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    job.proc.kill()
                    job.proc.wait(timeout=1)
            except Exception as e:
                log.error(f"Ошибка остановки {job_id}: {e}")
            
            job.nic_stats_after = get_nic_stats_ethtool(job.iface)
            job.stopped = True
        
        return {"status": "ok"}

    def handle_get_result(self, msg: dict) -> dict:
        job_id = msg.get("test_id")
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            return {"status": "error", "message": "unknown test_id"}
        if not job.finished:
            return {"status": "ok", "finished": False}
        return {"status": "ok", "finished": True, "result": job.result}

    def handle_stop_all(self) -> dict:
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            if not job.finished:
                job.proc.terminate()
        return {"status": "ok"}

    def handle_list_ifaces(self) -> dict:
        """Возвращает информацию об интерфейсах (без VLAN, только физическое состояние)."""
        result = []
        for entry in self.config.get("interfaces", []):
            iface = entry["iface"]
            up = iface_is_up(iface)
            mac = entry.get("mac", "")
            speed = self._get_iface_speed(iface) if up else None
            result.append({
                "iface": iface,
                "mac": mac,
                "up": up,
                "speed": speed,
            })
        return {"status": "ok", "interfaces": result}

    def dispatch(self, msg: dict) -> dict:
        cmd = msg.get("cmd")
        if cmd == "PING":
            return {"status": "ok", "pong": True}
        if cmd == "LIST_IFACES":
            return self.handle_list_ifaces()
        if cmd == "START_TEST":
            return self.handle_start_test(msg)
        if cmd == "STOP_TEST":
            return self.handle_stop_test(msg)
        if cmd == "GET_RESULT":
            return self.handle_get_result(msg)
        if cmd == "STOP_ALL":
            return self.handle_stop_all()
        return {"status": "error", "message": f"unknown cmd {cmd}"}


def serve(agent: Agent, host: str, port: int):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    log.info("Агент слушает %s:%d", host, port)

    def handle_client(conn, addr):
        try:
            with conn:
                while True:
                    try:
                        msg = recv_message(conn)
                    except ProtocolError:
                        break
                    try:
                        resp = agent.dispatch(msg)
                    except Exception as e:
                        log.exception("Ошибка обработки команды")
                        resp = {"status": "error", "message": str(e)}
                    send_message(conn, resp)
        except Exception:
            log.exception("Ошибка соединения с %s", addr)

    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="./config.json")
    parser.add_argument("--pktgen", default="./pktgen")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_AGENT_PORT)
    args = parser.parse_args()

    if os.geteuid() != 0:
        log.warning("Агент не запущен от root - raw sockets в pktgen скорее всего откажут")

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    try:
        subprocess.run(['/root/op-test/clean_network.sh'], check=False, timeout=5)
        log.info("Скрипт очистки сети выполнен")
    except Exception as e:
        log.warning(f"Не удалось выполнить скрипт очистки сети: {e}")

    agent = Agent(config, args.pktgen)
    serve(agent, args.host, args.port)


if __name__ == "__main__":
    main()