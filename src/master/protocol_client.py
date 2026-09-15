"""
protocol_client.py
Клиент для общения Master -> Slave-агент.
"""
from __future__ import annotations

import time
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


def wait_for_result(client, test_id, poll_interval=0.5, max_wait=120.0,
                    hard_limit_factor=3.0, progress_cb=None):
    soft_wait = max_wait
    hard_wait = max_wait * hard_limit_factor
    waited = 0.0
    while waited < hard_wait:
        try:
            resp = client.get_result(test_id)
            if resp.get("finished"):
                return resp.get("result") or {}
        except Exception:
            pass
        
        if waited > soft_wait and progress_cb and int(waited) % 30 == 0:
            progress_cb(f"⏳ Тест {test_id} всё ещё выполняется, прошло {int(waited)} с")
        
        time.sleep(poll_interval)
        waited += poll_interval
    
    return {"status": "error", "message": f"Таймаут {hard_wait} сек", "_timeout": True}