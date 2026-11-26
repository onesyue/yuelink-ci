"""One Flutter test process, including detached workers and split retries."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from flutter_coverage.settings import LIMITS


def run_flutter_worker(command: list[str], worker: Path, log):
    # Keep the trusted workflow baseline in the real Flutter process, including
    # detached workers and split retries. Git worktrees share the fetched object.
    return subprocess.run(command, cwd=worker, env=os.environ.copy(),
                          stdout=log, stderr=subprocess.STDOUT,
                          timeout=LIMITS["COVERAGE_WORKER_TIMEOUT_SECONDS"], check=False)
