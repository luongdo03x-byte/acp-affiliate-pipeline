"""Linux host metrics sampler for adaptive AVD capacity decisions."""
from __future__ import annotations

import os
import time

from .resource_policy import HostSample


class HostMetricsSampler:
    def __init__(self, *, proc_root: str = "/proc", clock=time.monotonic):
        self.proc_root = proc_root
        self.clock = clock
        self._last_cpu = None
        self._last_swap_in = None
        self._last_time = None
        self.ram_total_mb = 0

    def _read_lines(self, name: str) -> list[str]:
        with open(os.path.join(self.proc_root, name), "r", encoding="utf-8") as handle:
            return handle.readlines()

    def _cpu_counters(self) -> tuple[int, int]:
        line = self._read_lines("stat")[0]
        parts = [int(value) for value in line.split()[1:]]
        total = sum(parts)
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return total, idle

    def _memory(self) -> tuple[int, int, int]:
        values = {}
        for line in self._read_lines("meminfo"):
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])
        total = values.get("MemTotal", 0) // 1024
        available = values.get("MemAvailable", values.get("MemFree", 0)) // 1024
        swap_used = max(0, values.get("SwapTotal", 0) - values.get("SwapFree", 0)) // 1024
        return total, available, swap_used

    def _swap_in_pages(self) -> int:
        for line in self._read_lines("vmstat"):
            key, value = line.split(None, 1)
            if key == "pswpin":
                return int(value)
        return 0

    def sample(self) -> HostSample:
        timestamp = self.clock()
        total_cpu, idle_cpu = self._cpu_counters()
        ram_total, ram_available, swap_used = self._memory()
        swap_in_pages = self._swap_in_pages()
        self.ram_total_mb = ram_total

        cpu_percent = 0.0
        swap_in_rate = 0.0
        if self._last_cpu is not None and self._last_time is not None:
            previous_total, previous_idle = self._last_cpu
            delta_total = total_cpu - previous_total
            delta_idle = idle_cpu - previous_idle
            if delta_total > 0:
                cpu_percent = max(0.0, min(100.0, 100.0 * (delta_total - delta_idle) / delta_total))
            elapsed = max(0.001, timestamp - self._last_time)
            if self._last_swap_in is not None:
                pages_per_second = max(0, swap_in_pages - self._last_swap_in) / elapsed
                swap_in_rate = pages_per_second * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)

        self._last_cpu = (total_cpu, idle_cpu)
        self._last_swap_in = swap_in_pages
        self._last_time = timestamp
        load_1m, load_5m, _ = os.getloadavg()
        return HostSample(
            cpu_percent=cpu_percent,
            ram_available_mb=ram_available,
            swap_used_mb=swap_used,
            swap_in_rate=swap_in_rate,
            load_1m=load_1m,
            load_5m=load_5m,
        )
