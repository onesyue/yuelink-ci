"""Host capacity evidence used only to choose a worker count."""
from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path

from flutter_coverage.settings import GIB, LIMITS, WORKER_MEMORY, WORKERS


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return None


def resource_snapshot(read_text_fn=None) -> dict:
    """Visible host/cgroup evidence only; unavailable interfaces remain null."""
    read = read_text if read_text_fn is None else read_text_fn
    cpu = os.cpu_count()
    if hasattr(os, "sched_getaffinity"):
        cpu = min(cpu or 1, len(os.sched_getaffinity(0)))
    system = platform.system()
    cgroup = Path("/sys/fs/cgroup")
    membership = read(Path("/proc/self/cgroup"))
    match = re.search(r"^0::(/.*)$", membership or "", re.MULTILINE)
    hierarchy_known = False
    bases = [cgroup]
    if match and ".." not in Path(match[1]).parts:
        candidate = cgroup / match[1].lstrip("/")
        if read(candidate / "cgroup.controllers") is not None:
            hierarchy_known = True
            bases = [candidate]
            while candidate != cgroup:
                candidate = candidate.parent
                bases.append(candidate)
    available = _available_memory(system, read)
    cpu, available, limits = _cgroup_limits(cpu, available, bases, cgroup, read)
    missing = _missing_interfaces(cpu, available, system, hierarchy_known, limits)
    return {"effective_cpus": cpu, "available_memory_bytes": available, "cgroup": limits,
            "capacity_status": "UNKNOWN" if missing else "VISIBLE_BOUNDS",
            "missing_interfaces": missing,
            "scope": "host/cgroup; peak may include other processes and prior work, not owned job peak"}


def _available_memory(system: str, read) -> int | None:
    meminfo = read(Path("/proc/meminfo"))
    if meminfo:
        match = re.search(r"^MemAvailable:\s+(\d+) kB$", meminfo, re.MULTILINE)
        if match:
            return int(match[1]) * 1024
        return None
    if system != "Darwin":
        return None
    try:
        vm = subprocess.check_output(
            ["vm_stat"], text=True, timeout=LIMITS["COVERAGE_VM_STAT_TIMEOUT_SECONDS"])
        page = int(re.search(r"page size of (\d+) bytes", vm)[1])
        return sum(int(re.search(rf"^{label}:\s+(\d+)\.", vm, re.MULTILINE)[1])
                   for label in ("Pages free", "Pages inactive")) * page
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None


def _cgroup_limits(cpu, available, bases, cgroup: Path, read):
    limits = []
    for base in bases:
        quota = read(base / "cpu.max")
        if quota and quota.split()[0] != "max":
            amount, period = map(int, quota.split())
            cpu = min(cpu or 1, max(1, amount // period))
        memory_max = read(base / "memory.max")
        root_uncontrolled = _root_uncontrolled(base, cgroup, quota, memory_max, read)
        current = read(base / "memory.current")
        limit = int(memory_max) if memory_max and memory_max.isdecimal() else None
        current_bytes = int(current) if current and current.isdecimal() else None
        if limit is not None and current_bytes is not None:
            headroom = max(0, limit - current_bytes)
            available = min(available, headroom) if available is not None else headroom
        limits.append({"path": str(base), "cpu_max": quota, "memory_max": memory_max,
                       "root_limits_not_applicable": root_uncontrolled,
                       "current_bytes": current_bytes, "peak_bytes": read(base / "memory.peak"),
                       "events": read(base / "memory.events")})
    return cpu, available, limits


def _root_uncontrolled(base: Path, cgroup: Path, quota: str | None, memory_max: str | None, read) -> bool:
    # Only the real v2 root omits cgroup.events. Namespace roots still
    # expose it and their limits; unreadable files do not prove absence.
    if base != cgroup or quota is not None or memory_max is not None:
        return False
    controllers = read(base / "cgroup.controllers")
    if controllers is None or not {"cpu", "memory"} <= set(controllers.split()):
        return False
    try:
        os.stat(base / "cgroup.events")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _missing_interfaces(cpu, available, system: str, hierarchy_known: bool, limits: list) -> list[str]:
    missing = []
    if cpu is None:
        missing.append("effective CPU count")
    if available is None:
        missing.append("host available memory or visible cgroup headroom")
    if system == "Linux" and (not hierarchy_known or any(
            (item["cpu_max"] is None or item["memory_max"] is None)
            and not item["root_limits_not_applicable"] for item in limits)):
        missing.append("complete applicable cgroup-v2 CPU/memory hierarchy (v1 is not measured)")
    return missing


def worker_count(snapshot: dict) -> int:
    explicit = os.environ.get("YUELINK_COVERAGE_MAX_WORKERS", str(WORKERS))
    if not re.fullmatch(r"[1-9][0-9]*", explicit):
        raise ValueError("worker upper bound must be a positive integer")
    cpu = snapshot["effective_cpus"]
    memory = snapshot["available_memory_bytes"]
    maximum = min(WORKERS, int(explicit), cpu or 1)
    if memory is not None:
        memory_workers = max(0, (memory - GIB // 2) // WORKER_MEMORY)
        if not memory_workers:
            raise ValueError("insufficient visible memory for one grouped compiler worker")
        maximum = min(maximum, memory_workers)
    if snapshot["capacity_status"] == "UNKNOWN":
        return 1  # Compatibility execution only, never resource/performance acceptance.
    return maximum


def timed_command(command: list[str], output: Path) -> list[str]:
    timer = Path("/usr/bin/time")
    if not timer.is_file():
        return command  # Resource receipt explicitly records unavailable data.
    if platform.system() == "Linux":
        return [str(timer), "-v", "-o", str(output), *command]
    return command
