"""Multi-key loading + round-robin rotation for free-tier quota survival.

Keys are loaded (in priority order, de-duplicated):
  1. env GEMINI_API_KEYS  (comma-separated)
  2. config/gemini_keys.txt  (one key per line; '#' comments ignored) [gitignored]
  3. env GEMINI_API_KEY  (single)

The rotator cycles keys and marks ones that hit a daily/quota limit as exhausted
for this run, so a long OCR/compose batch keeps going across several free keys.
"""
from __future__ import annotations
import os

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
        self.i = 0
        self.exhausted: set[int] = set()

    def __len__(self):
        return len(self.keys)

    def _available(self):
        return [j for j in range(len(self.keys)) if j not in self.exhausted]

    def current(self):
        av = self._available()
        if not av:
            return None
        if self.i not in av:
            self.i = av[0]
        return self.keys[self.i]

    def mark_exhausted(self):
        self.exhausted.add(self.i)

    def rotate(self):
        av = self._available()
        if not av:
            return None
        later = [j for j in av if j > self.i]
        self.i = later[0] if later else av[0]
        return self.keys[self.i]

    def status(self):
        return f"{len(self._available())}/{len(self.keys)} keys available"
