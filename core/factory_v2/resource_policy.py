"""Pure adaptive resource policy for Account Factory V2 AVD workers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import ceil


@dataclass(frozen=True)
class HostSample:
    cpu_percent: float
    ram_available_mb: int
    swap_used_mb: int
    swap_in_rate: float
    load_1m: float
    load_5m: float


class CapacityState(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    EMERGENCY = "EMERGENCY"


@dataclass(frozen=True)
class ResourceThresholds:
    green_cpu_max: float = 65.0
    yellow_cpu_max: float = 85.0
    green_ram_min_mb: int = 6144
    red_ram_below_mb: int = 3072
    emergency_ram_below_mb: int = 1536
    yellow_swap_in_rate: float = 0.0
    red_swap_in_rate: float = 64.0


DEFAULT_THRESHOLDS = ResourceThresholds()


def classify_capacity(
    sample: HostSample,
    thresholds: ResourceThresholds = DEFAULT_THRESHOLDS,
) -> CapacityState:
    if sample.ram_available_mb < thresholds.emergency_ram_below_mb:
        return CapacityState.EMERGENCY
    if (
        sample.cpu_percent > thresholds.yellow_cpu_max
        or sample.ram_available_mb < thresholds.red_ram_below_mb
        or sample.swap_in_rate >= thresholds.red_swap_in_rate
    ):
        return CapacityState.RED
    if (
        sample.cpu_percent < thresholds.green_cpu_max
        and sample.ram_available_mb > thresholds.green_ram_min_mb
        and sample.swap_in_rate <= thresholds.yellow_swap_in_rate
    ):
        return CapacityState.GREEN
    return CapacityState.YELLOW


def waiting_human_limit(active_pool: int) -> int:
    if active_pool <= 0:
        return 1
    return max(1, min(3, ceil(active_pool * 0.4)))


def next_worker_target(
    current_total: int,
    waiting_human: int,
    stable_state: CapacityState,
    learned_avd_ram_mb: int,
) -> int:
    del learned_avd_ram_mb  # Reserved for a later theoretical ceiling; live state is authoritative.
    current_total = max(0, int(current_total))
    waiting_human = max(0, min(int(waiting_human), current_total))
    state = CapacityState(stable_state)

    if state == CapacityState.GREEN:
        if waiting_human >= waiting_human_limit(current_total):
            return current_total
        return current_total + 1
    if state == CapacityState.YELLOW:
        return current_total
    if state in {CapacityState.RED, CapacityState.EMERGENCY}:
        if current_total <= waiting_human:
            return current_total
        return max(waiting_human, current_total - 1)
    return current_total
