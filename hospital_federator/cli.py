from __future__ import annotations

import argparse
import logging
import sys

from .app import HospitalFederatorApp
from .config import load_config
from .receiver import EmbeddedReceiver


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Hospital Federator Demo (GUI + federation + outbox + llama-cpp)")
    ap.add_argument("--config", required=True, help="Path to peers.yaml (shared across instances)")
    ap.add_argument("--peer-id", default=None, help="This instance's peer id (must match YAML peers[].id)")
    ap.add_argument(
        "--db",
        default=None,
        help="Path to SQLite DB (default: ./dbs/hospital_federator_<peer-id>.db)",
    )
    ap.add_argument(
        "--listen-host",
        default="127.0.0.1",
        help="Host/interface for embedded receiver (default 127.0.0.1). Use 0.0.0.0 to accept from other machines.",
    )
    ap.add_argument(
        "--listen-port",
        type=int,
        default=0,
        help="Port for embedded HTTPS receiver. If 0, receiver is disabled (default 0).",
    )
    ap.add_argument(
        "--log-level",
        default="INFO",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Logging level (default INFO)",
    )
    return ap.parse_args()


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> None:
    args = parse_args()
    _configure_logging(args.log_level)

    log = logging.getLogger(__name__)
    cfg = load_config(args.config, args.peer_id)
    db_path = args.db or f"./dbs/hospital_federator_{cfg.self_peer_id}.db"

    log.info("Starting Hospital Federator (self_peer_id=%s, db=%s)", cfg.self_peer_id, db_path)

    app = HospitalFederatorApp(cfg, db_path=db_path)

    # Receiver uses the *self peer* TLS settings for its server identity
    self_peer_tls = next(p.tls for p in cfg.peers if p.peer_id == cfg.self_peer_id)
    allowed_peer_ids = {p.peer_id for p in cfg.peers if p.peer_id != cfg.self_peer_id}

    receiver = EmbeddedReceiver(
        host=args.listen_host,
        port=args.listen_port,
        outbox=app.outbox,
        signing=cfg.signing,
        tls=self_peer_tls,
        allowed_peer_ids=allowed_peer_ids,
    )
    receiver.start()

    app.run()


if __name__ == "__main__":
    main()
