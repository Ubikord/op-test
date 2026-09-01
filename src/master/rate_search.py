"""
rate_search.py
Бинарный поиск максимальной скорости (pps), при которой не происходит
потерь пакетов "из-за системы".

ВАЖНО: min_rate_pps и max_rate_pps - это ЖЁСТКИЕ границы, заданные
пользователем в GUI (например, реальная пропускная способность порта
или сознательно выбранный предел теста). Алгоритм НИКОГДА не должен
возвращать скорость за пределами [min_rate_pps, max_rate_pps] и не
должен самостоятельно "расширять" диапазон поиска - если даже на
max_rate_pps потерь нет, это означает, что максимум и есть ответ,
а не повод пробовать более высокие скорости.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class RateSearchResult:
    rate_pps: int
    last_trial_result: dict
    iterations: int
    no_common_vlan: bool = False  # <-- НОВОЕ поле


def find_max_no_loss_rate(
    run_trial: Callable[[int], dict],
    min_rate_pps: int = 1000,
    max_rate_pps: int = 1_000_000,
    tolerance: float = 0.05,
    max_iterations: int = 8,
    confirm_trials: int = 3,
    backoff_factor: float = 0.9,
    max_backoff_steps: int = 5,
) -> RateSearchResult:
    """
    run_trial(rate_pps) -> результат вида
        {"packets_sent": N, "packets_lost": M, ...}
    должен запустить полноценный тест sender+receiver на данной паре
    интерфейсов на скорости rate_pps и вернуть агрегированный результат.

    Возвращаемая rate_pps ВСЕГДА лежит в диапазоне
    [min_rate_pps, max_rate_pps].
    """
    low, high = min_rate_pps, max_rate_pps
    iterations = 0

    # === ПРОВЕРКА НА МИНИМАЛЬНОЙ СКОРОСТИ ===
    trial_low = run_trial(low)
    iterations += 1
    
    packets_sent = trial_low.get("packets_sent", 0)
    packets_lost = trial_low.get("packets_lost", 0)
    
    # Если пакетов не отправлено или потери 100%
    if packets_sent == 0 or (packets_lost > 0 and packets_lost >= packets_sent):
        return RateSearchResult(
            rate_pps=low,
            last_trial_result=trial_low,
            iterations=iterations,
            no_common_vlan=True  # <-- 100% потери = нет общего VLAN
        )
    
    if trial_low.get("packets_lost", 0) > 0:
        # Если на минимальной скорости есть потери, но не 100%
        # Возвращаем минимальную скорость
        return RateSearchResult(
            rate_pps=low,
            last_trial_result=trial_low,
            iterations=iterations,
            no_common_vlan=False
        )
    
    last_ok_result = trial_low

    # Проверяем на максимальной скорости
    trial_high = run_trial(high)
    iterations += 1
    
    packets_sent = trial_high.get("packets_sent", 0)
    packets_lost = trial_high.get("packets_lost", 0)
    
    # Если на максимальной скорости тоже 100% потерь (маловероятно, но проверим)
    if packets_sent == 0 or (packets_lost > 0 and packets_lost >= packets_sent):
        return RateSearchResult(
            rate_pps=high,
            last_trial_result=trial_high,
            iterations=iterations,
            no_common_vlan=True
        )
    
    if trial_high.get("packets_lost", 0) == 0:
        candidate = high
        last_ok_result = trial_high
    else:
        while iterations < max_iterations and (high - low) / max(high, 1) > tolerance:
            mid = (low + high) // 2
            result = run_trial(mid)
            iterations += 1
            
            packets_sent = result.get("packets_sent", 0)
            packets_lost = result.get("packets_lost", 0)
            
            # Проверка на 100% потерь во время поиска
            if packets_sent == 0 or (packets_lost > 0 and packets_lost >= packets_sent):
                # 100% потерь - прерываем поиск
                return RateSearchResult(
                    rate_pps=low,
                    last_trial_result=result,
                    iterations=iterations,
                    no_common_vlan=True
                )
            
            if result.get("packets_lost", 0) == 0:
                low = mid
                last_ok_result = result
            else:
                high = mid
        candidate = low

    # --- Подтверждение финального кандидата дополнительными прогонами ---
    backoff_steps = 0
    while True:
        confirmed = True
        for _ in range(confirm_trials):
            result = run_trial(candidate)
            iterations += 1
            
            packets_sent = result.get("packets_sent", 0)
            packets_lost = result.get("packets_lost", 0)
            
            # Проверка на 100% потерь при подтверждении
            if packets_sent == 0 or (packets_lost > 0 and packets_lost >= packets_sent):
                return RateSearchResult(
                    rate_pps=candidate,
                    last_trial_result=result,
                    iterations=iterations,
                    no_common_vlan=True
                )
            
            if result.get("packets_lost", 0) > 0:
                confirmed = False
                break
            last_ok_result = result
        if confirmed or backoff_steps >= max_backoff_steps or candidate <= min_rate_pps:
            break
        candidate = max(min_rate_pps, int(candidate * backoff_factor))
        backoff_steps += 1

    final_rate = max(min_rate_pps, min(candidate, max_rate_pps))

    print(f"DEBUG find_max_no_loss_rate: returning rate_pps={final_rate}")
    return RateSearchResult(
        rate_pps=final_rate,
        last_trial_result=last_ok_result,
        iterations=iterations,
        no_common_vlan=False
    )