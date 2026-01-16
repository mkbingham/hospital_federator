import json
from pathlib import Path

from hospital_federator.config import Peer, TLSConfig
from hospital_federator.db import OutboxDB
from hospital_federator.events import make_document_event, make_summary_event
from hospital_federator.utils import make_document_id, sha256_hex


def test_outbox_add_job_and_list(tmp_path: Path) -> None:
    db_path = tmp_path / "hf.db"
    db = OutboxDB(str(db_path))

    peer_a = Peer(peer_id="a", name="Peer A", url="https://example.invalid", tls=TLSConfig(verify=False))
    peer_b = Peer(peer_id="b", name="Peer B", url="https://example.invalid", tls=TLSConfig(verify=False))

    doc = "Hello world"
    source = "pytest"
    doc_id = make_document_id(doc, source)

    events = [
        make_document_event(origin_node=peer_a.peer_id, doc_id=doc_id, text=doc, source=source),
        make_summary_event(
            origin_node=peer_a.peer_id,
            doc_id=doc_id,
            input_hash=sha256_hex(doc.encode("utf-8")),
            summary_text="Summary",
            model_id="manual",
            prompt_version="manual_v1",
        ),
    ]

    job_id = db.add_job(origin_peer_id=peer_a.peer_id, label="doc+summary", events=events, targets=[peer_b])
    assert job_id

    jobs = db.list_jobs(limit=10)
    assert any(j["job_id"] == job_id for j in jobs)

    deliveries = db.get_pending_or_failed_targets(job_id)
    assert len(deliveries) == 1
    assert deliveries[0]["target_peer_id"] == "b"


def test_inbox_store_and_fetch(tmp_path: Path) -> None:
    db_path = tmp_path / "hf.db"
    db = OutboxDB(str(db_path))

    payload = {
        "event_id": "evt-1",
        "event_type": "ArtifactUpserted",
        "origin_node": "peer-x",
        "created_at": 100.0,
        "payload": {"doc_id": "doc-1", "kind": "summary", "payload_json": {"x": {"y": [1, 2]}}},
    }

    inserted = db.add_inbox_events([payload], from_peer_id="peer-x")
    assert inserted == 1

    rows = db.list_inbox_events(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_id"] == "evt-1"

    raw = db.get_inbox_event_payload("evt-1")
    assert raw is not None
    obj = json.loads(raw)
    assert obj["payload"]["payload_json"]["x"]["y"] == [1, 2]
