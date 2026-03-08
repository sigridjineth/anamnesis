from __future__ import annotations

import hashlib
import re
from typing import Iterable

import numpy as np
from numpy.typing import NDArray


_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def hash_embedding(text: str | None, *, dimensions: int = 64) -> NDArray[np.float32]:
    vector = np.zeros(dimensions, dtype=np.float32)
    tokens = tokenize(text)
    if not tokens:
        return vector
    for index, token in enumerate(tokens):
        digest = hashlib.sha1(f"{index}:{token}".encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + min(len(token), 24) / 24.0
        vector[bucket] += sign * weight
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return vector


def combine_text(parts: Iterable[str | None]) -> str:
    values = [str(part).strip() for part in parts if part is not None and str(part).strip()]
    return "\n".join(values)
