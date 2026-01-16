from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional


HOSPITAL_ICON = "🏥"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_text(text: str) -> str:
    """Normalize multi-line text for stable hashing/transmission."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()


def make_document_id(text: str, source: Optional[str]) -> str:
    blob = (source or "").encode("utf-8") + b"\n" + text.encode("utf-8")
    return sha256_hex(blob)


def make_virtual_doc_id_for_summary(summary_text: str, source: str) -> str:
    blob = ("summary-only:" + source + "\n" + summary_text).encode("utf-8")
    return sha256_hex(blob)


def now_ts() -> float:
    return time.time()


def stable_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
