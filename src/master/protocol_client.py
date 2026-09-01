"""
protocol_client.py
Клиент для общения Master -> Slave-агент.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.protocol import request, DEFAULT_AGENT_PORT, EndpointRef


class AgentClient:
    def __init__(self, host: str, port: int = DEFAULT_AGENT_PORT, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def ping(self) -> bool:
        try:
            resp = request(self.host, self.port, {"cmd": "PING"}, timeout=2.0)
            return resp.get("status") == "ok"
        except OSError:
            return False

    def list_ifaces(self) -> list[dict]:
        resp = request(self.host, self.port, {"cmd": "LIST_IFACES"}, timeout=self.timeout)
        if resp.get("status") != "ok":
            raise RuntimeError(resp.get("message", "LIST_IFACES failed"))
        return resp["interfaces"]

    def start_test(self, spec: dict) -> str:
        msg = {"cmd": "START_TEST", **spec}
        resp = request(self.host, self.port, msg, timeout=self.timeout)
        if resp.get("status") != "ok":
            raise RuntimeError(resp.get("message", "START_TEST failed"))
        return resp["test_id"]

    def stop_test(self, test_id: str) -> None:
        request(self.host, self.port, {"cmd": "STOP_TEST", "test_id": test_id}, timeout=self.timeout)

    def get_result(self, test_id: str) -> dict:
        resp = request(self.host, self.port, {"cmd": "GET_RESULT", "test_id": test_id}, timeout=self.timeout)
        if resp.get("status") != "ok":
            raise RuntimeError(resp.get("message", "GET_RESULT failed"))
        return resp

    def stop_all(self) -> None:
        request(self.host, self.port, {"cmd": "STOP_ALL"}, timeout=self.timeout)

    def set_iface(self, iface: str, state: str) -> dict:
        """
        Управляет состоянием интерфейса.
        state: 'up' или 'down'
        """
        msg = {"cmd": "SET_IFACE", "iface": iface, "state": state}
        resp = request(self.host, self.port, msg, timeout=10.0)
        if resp.get("status") != "ok":
            raise RuntimeError(resp.get("message", "SET_IFACE failed"))
        return resp


def wait_for_result(client: AgentClient, test_id: str, poll_interval: float = 0.5, max_wait: float = 120.0) -> dict:
    """
    Ожидает завершения теста и возвращает результат.
    ВСЕГДА возвращает dict, даже при таймауте.
    """
    import time
    waited = 0.0
    last_resp = None
    
    while waited < max_wait:
        try:
            resp = client.get_result(test_id)
            last_resp = resp
            if resp.get("finished"):
                result = resp.get("result")
                if result is not None:
                    return result
                # Если result есть, но None, продолжаем ждать
        except Exception as e:
            print(f"⚠️ [wait_for_result] Ошибка: {e}")
        
        time.sleep(poll_interval)
        waited += poll_interval
    
    # === ТАЙМАУТ: возвращаем последний ответ или фиктивный результат ===
    print(f"⚠️ [wait_for_result] Таймаут для {test_id} ({max_wait} сек)")
    
    if last_resp and last_resp.get("result") is not None:
        return last_resp["result"]
    
    # Если ничего нет, возвращаем пустой результат с пометкой о таймауте
    return {
        "status": "error",
        "message": f"Таймаут {max_wait} сек",
        "packets_sent": 0,
        "bytes_sent": 0,
        "duration_s": 0,
        "nic_stats_delta": {},
        "_timeout": True
    }