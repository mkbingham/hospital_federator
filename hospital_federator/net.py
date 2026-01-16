from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Dict, List, Optional, Tuple

import requests

from .config import Peer, SigningConfig
from .utils import stable_json_bytes

logger = logging.getLogger(__name__)

class FederationClient:
    """HTTP client to push events to other peers :)"""

    def __init__(self, signing: SigningConfig):
        self.signing = signing
        self._hmac_key: Optional[str] = None

        if signing.enabled:
            key = None
            if signing.key_env:
                key = os.getenv(signing.key_env)
            if not key:
                key = os.getenv("HOSPITAL_FEDERATOR_HMAC_KEY")
            if not key:
                raise ValueError(
                    "Signing is enabled but no key found. "
                    "Set signing.key_env in YAML and export that env var, "
                    "or export HOSPITAL_FEDERATOR_HMAC_KEY."
                )
            self._hmac_key = key

    def _sign_headers(self, body: bytes) -> Dict[str, str]:
        if not self.signing.enabled:
            return {}
        if self.signing.alg != "sha256":
            raise ValueError(f"Unsupported signing alg: {self.signing.alg}")
        assert self._hmac_key is not None
        sig = hmac.new(self._hmac_key.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return {
            "X-Signature": sig,
            "X-Signature-Alg": "hmac-sha256",
        }

    def push_events(self, peer: Peer, events: List[dict]) -> Tuple[bool, str, Optional[int]]:
        """Push a list of events to a peer.

        Returns: (ok, message, http_status)
        """

        url = f"{peer.url}/events/push"
        logger.debug("Pushing %d event(s) to %s (%s)", len(events), peer.peer_id, peer.name, url)

        body_obj = {"events": events}
        body = stable_json_bytes(body_obj)
        headers = {"Content-Type": "application/json", **self._sign_headers(body)}

        cert = None
        if peer.tls.client_cert and peer.tls.client_key:
            cert = (peer.tls.client_cert, peer.tls.client_key)
        elif peer.tls.client_cert:
            cert = peer.tls.client_cert

        try:
            r = requests.post(
                url,
                data=body,
                headers=headers,
                timeout=peer.tls.timeout_s,
                verify=peer.tls.verify,
                cert=cert,
            )
            logger.debug("Response %s %s", r.status_code, (r.text[:120] if r.text else ""))
            if r.status_code >= 400:
                msg = (r.text[:200] if r.text else f"HTTP {r.status_code}")
                logger.warning("Push failed to %s (%s): %s", peer.peer_id, url, msg)
                return False, msg, r.status_code

            logger.info("Push OK to %s (%s) status=%s", peer.peer_id, url, r.status_code)
            return True, "ok", r.status_code

        except Exception as e:
            logger.warning("Push to %s failed: %s", url, e)
            logger.exception("Push error to %s (%s)", peer.peer_id, url)
            return False, str(e), None
