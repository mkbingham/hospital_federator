from __future__ import annotations

import uuid
from typing import Any, Dict

from .utils import now_ts, sha256_hex, stable_json_bytes


def make_document_event(origin_node: str, doc_id: str, text: str, source: str) -> Dict[str, Any]:
    t = now_ts()
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "DocumentAdded",
        "origin_node": origin_node,
        "created_at": t,
        "payload": {
            "doc_id": doc_id,
            "source": source,
            "text": text,
            "text_hash": sha256_hex(text.encode("utf-8")),
            "created_at": t,
        },
    }


def make_summary_event(
    origin_node: str,
    doc_id: str,
    input_hash: str,
    summary_text: str,
    model_id: str,
    prompt_version: str,
) -> Dict[str, Any]:
    t = now_ts()
    payload_json = {"text": summary_text, "input_text_hash": input_hash, "prompt_version": prompt_version}
    payload_hash = sha256_hex(stable_json_bytes(payload_json))
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "ArtifactUpserted",
        "origin_node": origin_node,
        "created_at": t,
        "payload": {
            "artifact_id": str(uuid.uuid4()),
            "doc_id": doc_id,
            "kind": "summary",
            "payload_json": payload_json,
            "payload_hash": payload_hash,
            "model_id": model_id,
            "created_at": t,
        },
    }
