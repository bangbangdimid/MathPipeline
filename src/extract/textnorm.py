"""Text normalization for hashing / dedup (accent-insensitive, latex-stripped)."""
from __future__ import annotations
import re
import hashlib
import unicodedata


def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


def normalize(s: str) -> str:
    s = strip_accents(s.lower())
    s = re.sub(r'\\[a-zA-Z]+', ' ', s)     # drop latex control words
    s = re.sub(r'[^a-z0-9]+', ' ', s)      # keep alphanumerics only
    return re.sub(r'\s+', ' ', s).strip()


def canonical_hash(s: str) -> str:
    return hashlib.sha1(normalize(s).encode('utf-8')).hexdigest()


def shingles(norm: str, k: int = 4) -> set[str]:
    toks = norm.split()
    if len(toks) < k:
        return set(toks) or {norm or ''}
    return set(' '.join(toks[i:i + k]) for i in range(len(toks) - k + 1))
