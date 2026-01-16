from __future__ import annotations

import logging
import hashlib
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Set
from urllib.parse import urlparse

from .config import SigningConfig, TLSConfig
from .db import OutboxDB


logger = logging.getLogger(__name__)


class EmbeddedReceiver:
    """HTTPS receiver for /events/push.

    Notes:
    - If TLSConfig.verify is False, client certs are not required and no peer identity
      will be authenticated.
    - If TLSConfig.verify is True or a CA path, client certificates are required
      and the client cert CN is enforced to match a known peer ID.
    """

    def __init__(
        self,
        host: str,
        port: int,
        outbox: OutboxDB,
        signing: SigningConfig,
        tls: TLSConfig,
        allowed_peer_ids: Set[str],
    ):
        self.host = host
        self.port = port
        self.outbox = outbox
        self.signing = signing
        self.tls = tls
        self.allowed_peer_ids = allowed_peer_ids

        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

        self._hmac_key: Optional[str] = None
        if signing.enabled:
            key = None
            if signing.key_env:
                key = os.getenv(signing.key_env)
            if not key:
                key = os.getenv("HOSPITAL_FEDERATOR_HMAC_KEY")
            self._hmac_key = key

    def _make_ssl_context(self):
        import ssl

        certfile = self.tls.client_cert
        keyfile = self.tls.client_key

        if not certfile or not keyfile:
            raise RuntimeError(
                "Embedded HTTPS receiver requires tls.client_cert and tls.client_key for the *self* peer in YAML config."
            )

        certfile = os.path.abspath(os.path.expanduser(certfile))
        keyfile = os.path.abspath(os.path.expanduser(keyfile))

        if not os.path.exists(certfile):
            raise RuntimeError(f"Server cert not found: {certfile}")
        if not os.path.exists(keyfile):
            raise RuntimeError(f"Server key not found: {keyfile}")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)

        verify = self.tls.verify
        if verify is False:
            # No mTLS; no authenticated peer identity.
            ctx.verify_mode = ssl.CERT_NONE
            require_client_cert = False
        else:
            # Require a client certificate.
            ctx.verify_mode = ssl.CERT_REQUIRED
            require_client_cert = True
            if isinstance(verify, str):
                ca_path = os.path.abspath(os.path.expanduser(verify))
                if not os.path.exists(ca_path):
                    raise RuntimeError(f"CA verify path not found: {ca_path}")
                ctx.load_verify_locations(cafile=ca_path)
            else:
                ctx.load_default_certs(purpose=ssl.Purpose.CLIENT_AUTH)

        ctx.minimum_version = getattr(ssl.TLSVersion, "TLSv1_2", None) or ssl.TLSVersion.TLSv1_2
        return ctx, require_client_cert

    def start(self) -> None:
        if self.port <= 0:
            logger.info("EmbeddedReceiver disabled (listen_port=%s)", self.port)
            return

        logger.info("Starting EmbeddedReceiver on https://%s:%s", self.host, self.port)

        outbox = self.outbox
        signing = self.signing
        hmac_key = self._hmac_key

        ctx, require_client_cert = self._make_ssl_context()
        logger.info("Starting embedded receiver on https://%s:%s (mTLS=%s, signing=%s)", self.host, self.port, require_client_cert, signing.enabled)
        allowed_peer_ids = set(self.allowed_peer_ids)

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: str) -> None:
                b = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):  # noqa: N802
                try:
                    parsed = urlparse(self.path)
                    logger.debug("Incoming POST %s from %s", parsed.path, self.client_address[0] if self.client_address else "?")
                    if parsed.path != "/events/push":
                        self._send(404, "not found")
                        return

                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length > 0 else b""

                    # Enforce mTLS peer identity if required.
                    peer_cn: Optional[str] = None
                    if require_client_cert:
                        try:
                            cert = self.connection.getpeercert()  # type: ignore[attr-defined]
                            subj = cert.get("subject", []) if isinstance(cert, dict) else []
                            for rdn in subj:
                                for k, v in rdn:
                                    if str(k).lower() == "commonname":
                                        peer_cn = v
                                        break
                                if peer_cn:
                                    break
                        except Exception:
                            peer_cn = None

                        if peer_cn is None:
                            logger.warning("Rejected request: client cert required from %s", self.client_address)
                            self._send(401, "client certificate required")
                            return
                        if peer_cn not in allowed_peer_ids:
                            logger.warning("Rejected request: unknown peer CN=%s from %s", peer_cn, self.client_address)
                            self._send(403, f"unknown peer CN: {peer_cn}")
                            return

                    logger.info(
                        "Received /events/push from %s (%d bytes)",
                        peer_cn or "<unauthenticated>",
                        len(raw),
                    )

                    # HMAC verification.
                    if signing.enabled:
                        if not hmac_key:
                            self._send(500, "signing enabled but receiver has no key")
                            return
                        sig = self.headers.get("X-Signature", "")
                        alg = self.headers.get("X-Signature-Alg", "")
                        if alg.lower() != "hmac-sha256":
                            self._send(401, "bad signature alg")
                            return
                        want = hmac.new(hmac_key.encode("utf-8"), raw, hashlib.sha256).hexdigest()
                        if not hmac.compare_digest(want, sig):
                            self._send(401, "bad signature")
                            return

                    obj = json.loads(raw.decode("utf-8")) if raw else {}
                    events = obj.get("events") or []
                    if not isinstance(events, list):
                        self._send(400, "events must be a list")
                        return

                    # Store the push and individual events.
                    try:
                        remote_addr = self.client_address[0] if self.client_address else None
                    except Exception:
                        remote_addr = None

                    outbox.add_inbox_push(
                        from_peer_id=peer_cn,
                        remote_addr=remote_addr,
                        raw_bytes_len=len(raw),
                        events_count=len(events),
                        body_obj=obj,
                    )
                    inserted = outbox.add_inbox_events(events, from_peer_id=peer_cn)
                    logger.info(
                        "Stored inbound push (%d bytes, %d event(s), %d new) from %s",
                        len(raw),
                        len(events),
                        inserted,
                        peer_cn or "<unknown>",
                    )
                    self._send(200, "ok")
                except Exception as e:
                    self._send(500, f"error: {e}")

            def log_message(self, _fmt, *_args):
                return

        self._server = HTTPServer((self.host, self.port), Handler)
        self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)

        def run() -> None:
            try:
                self._server.serve_forever(poll_interval=0.5)
            except Exception:
                pass

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass
