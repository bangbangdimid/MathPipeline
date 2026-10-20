"""Multi-key loading + COOLDOWN rotation for free-tier quota survival.

Keys are loaded (priority, de-duplicated):
  1. env GEMINI_API_KEYS (comma-separated)
  2. config/gemini_keys.txt (one per line; '#' comments) [gitignored]
  3. env GEMINI_API_KEY (single)

A key that hits a 429/limit is put on a short COOLDOWN (not retired forever), so a
per-minute rate limit causes a brief pause, not permanent failure of the process.
"""
from __future__ import annotations
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEYS_FILE = os.path.join(ROOT, "config", "gemini_keys.txt")


def load_keys() -> list[str]:
    keys: list[str] = []
    multi = os.environ.get("GEMINI_API_KEYS")
    if multi:
        keys += [k.strip() for k in multi.split(",") if k.strip()]
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    keys.append(line)
    single = os.environ.get("GEMINI_API_KEY")
    if single:
        keys.append(single)
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


class KeyRotator:
    def __init__(self, keys: list[str]):
        self.keys = keys
        self._cool: dict[int, float] = {}   # idx -> epoch available again
        self._rr = 0

    def __len__(self):
        return len(self.keys)

    def _available(self):
        now = time.time()
        return [i for i in range(len(self.keys)) if self._cool.get(i, 0) <= now]

    def pick(self):
        av = self._available()
        if not av:
            return None
        self._rr = (self._rr + 1) % len(av)
        i = av[self._rr]
        return i, self.keys[i]

    def penalize(self, idx: int, seconds: float):
        self._cool[idx] = time.time() + seconds

    def wait_hint(self) -> float:
        now = time.time()
        future = [v for v in self._cool.values() if v > now]
        return (min(future) - now) if future else 0.0

    def status(self) -> str:
        return f"{len(self._available())}/{len(self.keys)} key sẵn sàng"
