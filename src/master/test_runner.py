"""
test_runner.py
Оркестрация одного тестового прогона на паре интерфейсов.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional, List

from src.master.protocol_client import AgentClient, wait_for_result
from src.common.protocol import EndpointRef

def find_common_vlan(endpoints: List[EndpointRef]) -> Optional[int]:
    """
    Находит общий VLAN для всех интерфейсов (на основе статической информации).
    Возвращает None, если общего VLAN нет.
    """
    if not endpoints:
        return None
    common_vlans = set(endpoints[0].vlans)
    for ep in endpoints[1:]:
        common_vlans &= set(ep.vlans)
        if not common_vlans:
            return None
    return min(common_vlans) if common_vlans else None

def run_pair_test(
    sender: EndpointRef,
    receiver: EndpointRef,
    size_mode: str,
    size: int,
    size_min: int,
    size_max: int,
    rate_pps: int,
    duration_s: float,
    packet_count: int = 0,
    dst_mac_override: str = None,
    dst_type: int = 0,
) -> dict:
    # === ГЕНЕРИРУЕМ test_id САМЫМ ПЕРВЫМ ===
    test_id = str(uuid.uuid4())[:8]
    
    sender_client = AgentClient(sender.host)
    receiver_client = AgentClient(receiver.host)

    # Запускаем receiver
    recv_spec = {
        "test_id": test_id,
        "role": "receiver",
        "iface": receiver.iface,
        "size_mode": size_mode,
    }
    if packet_count is not None and packet_count > 0:
        recv_spec["duration_s"] = 0.0
    else:
        recv_spec["duration_s"] = duration_s + 2.0
        
    if size_mode == "random":
        recv_spec["size_min"] = size_min
        recv_spec["size_max"] = size_max
    else:
        recv_spec["size"] = size

    recv_test_id = receiver_client.start_test(recv_spec)
    time.sleep(0.3)

    # Запускаем sender
    send_spec = {
        "test_id": test_id,
        "role": "sender",
        "iface": sender.iface,
        "dst_mac": dst_mac_override if dst_mac_override else receiver.mac,
        "src_mac": sender.mac,
        "size_mode": size_mode,
        "rate_pps": rate_pps,
    }
    if packet_count is not None and packet_count > 0:
        send_spec["packet_count"] = packet_count
    else:
        send_spec["duration_s"] = duration_s

    if size_mode == "random":
        send_spec["size_min"] = size_min
        send_spec["size_max"] = size_max
    else:
        send_spec["size"] = size

    send_test_id = sender_client.start_test(send_spec)

    # Ждём результаты
    max_wait = 120.0
    if packet_count is not None and packet_count > 0:
        max_wait = (packet_count / max(rate_pps, 1)) + 30.0
    else:
        max_wait = duration_s + 30.0

    try:
        sender_result = wait_for_result(sender_client, send_test_id, max_wait=max_wait)
        time.sleep(0.5)
        receiver_client.stop_test(recv_test_id)
        receiver_result = wait_for_result(receiver_client, recv_test_id, max_wait=max_wait)
    except Exception as e:
        # Если ошибка при ожидании результатов, возвращаем результат с test_id
        return {
            "test_id": test_id,
            "rate_pps": rate_pps,
            "common_vlan": find_common_vlan([sender, receiver]),
            "dst_type": dst_type,
            "sender_stats": [{
                "mac": sender.mac,
                "packets_received": 0,
                "bytes_received": 0,
                "packets_expected": 0,
                "packets_lost": 0,
                "out_of_order": 0,
                "from_fallback": True
            }],
            "sender": {
                "slave": sender.slave,
                "iface": sender.iface,
                "packets_sent": 0,
                "bytes_sent": 0,
                "duration_s": duration_s or 0,
                "nic_stats": {},
            },
            "receiver": {
                "slave": receiver.slave,
                "iface": receiver.iface,
                "packets_received": 0,
                "bytes_received": 0,
                "packets_lost": 0,
                "packets_expected": 0,
                "out_of_order": 0,
                "duration_s": duration_s + 2 if duration_s else 2,
                "loss_pct": 100.0,
                "trailing_loss_detected": True,
                "nic_stats": {},
            },
            "nic_issue_detected": False,
            "vlan_warning": f"Ошибка ожидания результатов: {str(e)}",
            "is_fallback": True,
        }

    def extract_data(resp):
        if resp is None:
            return {}
        if isinstance(resp, dict):
            if "result" in resp and isinstance(resp["result"], dict):
                return resp["result"]
            return resp
        return {}

    sender_data = extract_data(sender_result)
    receiver_data = extract_data(receiver_result)

    # Проверяем, есть ли sender_stats в receiver_result ДО extract_data
    if receiver_result and isinstance(receiver_result, dict):
        raw_sender_stats = receiver_result.get("result", {}).get("sender_stats") if "result" in receiver_result else receiver_result.get("sender_stats")
    
    # Получаем данные sender'а
    sender_packets = sender_data.get("packets_sent", 0)
    sender_bytes = sender_data.get("bytes_sent", 0)
    sender_duration = sender_data.get("duration_s", duration_s or 0)
    sender_nic_stats = sender_data.get("nic_stats_delta", {})
    sender_has_snapshots = sender_data.get("has_snapshots", False)
    
    # Получаем данные receiver'а
    receiver_duration = receiver_data.get("duration_s", duration_s + 2 if duration_s else 0)
    receiver_nic_stats = receiver_data.get("nic_stats_delta", {})
    receiver_has_snapshots = receiver_data.get("has_snapshots", False)
        
    # Если нет данных от sender'а, но есть снимки
    if sender_packets == 0 and sender_nic_stats:
        tx_packets = sender_nic_stats.get("tx_packets", 0)
        if tx_packets > 0:
            sender_packets = tx_packets
            sender_bytes = sender_nic_stats.get("tx_bytes", 0)
    
    # Получаем sender_stats
    sender_stats = receiver_data.get("sender_stats") or []
    # Если нет sender_stats, но есть снимки receiver'а
    if not sender_stats and receiver_nic_stats:
        rx_packets = receiver_nic_stats.get("rx_packets", 0)
        if rx_packets > 0:
            sender_stats = [{
                "mac": sender.mac,
                "packets_received": rx_packets,
                "bytes_received": receiver_nic_stats.get("rx_bytes", 0),
                "packets_expected": rx_packets,
                "packets_lost": 0,
                "out_of_order": 0,
                "from_ethtool": True
            }]
    
    # Если все еще нет sender_stats, создаем фиктивные
    if not sender_stats:
        sender_stats = [{
            "mac": sender.mac,
            "packets_received": 0,
            "bytes_received": 0,
            "packets_expected": sender_packets,
            "packets_lost": sender_packets if sender_packets > 0 else 0,
            "out_of_order": 0,
            "from_fallback": True
        }]
    
    # Обработка receiver_stats
    our_stat = None
    for stat in sender_stats:
        if stat.get("mac") == sender.mac:
            our_stat = stat
            break
    
    if our_stat is None:
        our_stat = sender_stats[0] if sender_stats else {}
    
    receiver_packets = our_stat.get("packets_received", 0)
    receiver_bytes = our_stat.get("bytes_received", 0)
    receiver_expected = our_stat.get("packets_expected", 0)
    receiver_lost = our_stat.get("packets_lost", 0)
    receiver_out_of_order = our_stat.get("out_of_order", 0)
    
    # Расчет потерь
    reported_lost = receiver_lost
    reconciled_lost = max(0, sender_packets - receiver_packets)
    receiver_lost = max(reported_lost, reconciled_lost)
    
    if sender_packets == 0:
        loss_pct = 100.0
        trailing_loss_detected = False
    elif sender_packets > 0 and receiver_packets == 0:
        loss_pct = 100.0
        trailing_loss_detected = True
    else:
        trailing_loss_detected = reconciled_lost > reported_lost
        loss_pct = (receiver_lost / sender_packets) * 100.0 if sender_packets > 0 else 0.0
    
    # Формируем результат
    common_vlan = find_common_vlan([sender, receiver])
    
    sender_bytes_corrected = sender_bytes + (sender_packets * 4)
    receiver_bytes_corrected = receiver_bytes + (receiver_packets * 4)
    
    sender_nic_filtered = {k: v for k, v in sender_nic_stats.items() if k.startswith("tx_")}
    receiver_nic_filtered = {k: v for k, v in receiver_nic_stats.items() if k.startswith("rx_")}
    
    def has_nic_issue(stats: dict) -> bool:
        error_fields = (
            "rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
            "rx_fifo_errors", "tx_fifo_errors", "rx_over_errors",
            "rx_frame_errors", "rx_crc_errors", "collisions",
            "rx_mac_missed", "tx_aborted", "tx_underrun",
            "rx_jabber", "rx_oversize", "rx_undersize", "rx_align_errors",
        )
        return any((stats.get(f) or 0) > 0 for f in error_fields)
    
    nic_issue_detected = has_nic_issue(sender_nic_filtered) or has_nic_issue(receiver_nic_filtered)
    
    result = {
        "test_id": test_id,  # <-- УБЕЖДАЕМСЯ, ЧТО test_id СОХРАНЕН
        "rate_pps": rate_pps,
        "common_vlan": common_vlan,
        "dst_type": dst_type,
        "sender_stats": sender_stats,
        "sender": {
            "slave": sender.slave,
            "iface": sender.iface,
            "packets_sent": sender_packets,
            "bytes_sent": sender_bytes_corrected,
            "duration_s": sender_duration,
            "nic_stats": sender_nic_filtered,
            "from_ethtool": sender_packets > 0 and sender_data.get("packets_sent", 0) == 0,
        },
        "receiver": {
            "slave": receiver.slave,
            "iface": receiver.iface,
            "packets_received": receiver_packets,
            "bytes_received": receiver_bytes_corrected,
            "packets_lost": receiver_lost,
            "packets_expected": receiver_expected,
            "out_of_order": receiver_out_of_order,
            "duration_s": receiver_duration,
            "loss_pct": round(loss_pct, 3),
            "trailing_loss_detected": trailing_loss_detected,
            "nic_stats": receiver_nic_filtered,
        },
        "nic_issue_detected": nic_issue_detected,
        "data_source": {
            "sender_has_snapshots": sender_has_snapshots,
            "receiver_has_snapshots": receiver_has_snapshots,
            "sender_data_exists": bool(sender_data),
            "receiver_data_exists": bool(receiver_data),
        }
    }
    
    if common_vlan is None:
        result["vlan_warning"] = f"Нет общего VLAN: {sender.slave}:{sender.iface} → {receiver.slave}:{receiver.iface}"
    
    return result


def run_group_test(
    endpoints: list,
    size_mode: str,
    size: int,
    size_min: int,
    size_max: int,
    rate_pps: int,
    duration_s: float,
    packet_count: int = 0,
    dst_mac_override: str = None,
    dst_type: int = 0,
) -> list:
    """Запускает группу тестов по кольцевой схеме."""
    results = []
    n = len(endpoints)
    if n < 2:
        raise ValueError("Для группы нужно минимум 2 интерфейса")

    for i in range(n):
        sender = endpoints[i]
        receiver = endpoints[(i + 1) % n]
        result = run_pair_test(
            sender, receiver,
            size_mode, size, size_min, size_max,
            rate_pps, duration_s, packet_count=packet_count,
            dst_mac_override=dst_mac_override,
            dst_type=dst_type,
        )
        result["test_index"] = i + 1
        result["total_tests"] = n
        results.append(result)

    return results