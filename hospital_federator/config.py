from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Optional

import yaml


@dataclass(frozen=True)
class TLSConfig:
    """TLS settings for a peer connection."""

    verify: Any = True  # bool or path to CA bundle
    timeout_s: float = 5.0
    client_cert: Optional[str] = None
    client_key: Optional[str] = None


@dataclass(frozen=True)
class Peer:
    peer_id: str
    name: str
    url: str
    tls: TLSConfig


@dataclass(frozen=True)
class ModelConfig:
    path: str
    n_ctx: int = 4096
    n_threads: int = 8
    n_gpu_layers: int = 0
    max_tokens: int = 256


@dataclass(frozen=True)
class SigningConfig:
    enabled: bool = False
    key_env: Optional[str] = None
    alg: str = "sha256"  # currently supports sha256


@dataclass(frozen=True)
class AppConfig:
    peers: List[Peer]
    model: Optional[ModelConfig]
    signing: SigningConfig
    self_peer_id: str


def _parse_tls(data: Optional[dict], defaults: TLSConfig) -> TLSConfig:
    if not data:
        return defaults
    verify = data.get("verify", defaults.verify)
    timeout_s = float(data.get("timeout_s", defaults.timeout_s))
    client_cert = data.get("client_cert", defaults.client_cert)
    client_key = data.get("client_key", defaults.client_key)
    return TLSConfig(verify=verify, timeout_s=timeout_s, client_cert=client_cert, client_key=client_key)


def load_config(path: str, self_peer_id_cli: Optional[str]) -> AppConfig:
    """Load YAML config and validate required fields."""

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    peers_raw = data.get("peers")
    if not isinstance(peers_raw, list) or not peers_raw:
        raise ValueError("YAML must contain non-empty 'peers:' list")

    # Self recognition: CLI > env > YAML self.peer_id
    self_peer_id = self_peer_id_cli or os.getenv("PEER_ID") or (data.get("self") or {}).get("peer_id")
    if not self_peer_id:
        raise ValueError("Self peer id not set. Provide --peer-id or set PEER_ID or YAML self.peer_id")

    tls_def_raw = data.get("tls_defaults") or {}
    tls_defaults = TLSConfig(
        verify=tls_def_raw.get("verify", True),
        timeout_s=float(tls_def_raw.get("timeout_s", 5.0)),
        client_cert=tls_def_raw.get("client_cert"),
        client_key=tls_def_raw.get("client_key"),
    )

    peers: List[Peer] = []
    seen: set[str] = set()
    for i, p in enumerate(peers_raw):
        if not isinstance(p, dict):
            raise ValueError(f"peers[{i}] must be a mapping")
        pid = str(p.get("id", "")).strip()
        if not pid:
            raise ValueError(f"peers[{i}].id is required")
        if pid in seen:
            raise ValueError(f"Duplicate peer id: {pid}")
        seen.add(pid)

        name = str(p.get("name", pid)).strip() or pid
        url = str(p.get("url", "")).strip()
        if not url:
            raise ValueError(f"peers[{i}].url is required")

        tls = _parse_tls(p.get("tls"), tls_defaults)
        peers.append(Peer(peer_id=pid, name=name, url=url.rstrip("/"), tls=tls))

    if self_peer_id not in {p.peer_id for p in peers}:
        raise ValueError(f"Self peer id '{self_peer_id}' not found in peers list")

    model_cfg = None
    model_raw = data.get("model")
    if isinstance(model_raw, dict) and model_raw.get("path"):
        model_cfg = ModelConfig(
            path=str(model_raw["path"]),
            n_ctx=int(model_raw.get("n_ctx", 4096)),
            n_threads=int(model_raw.get("n_threads", 8)),
            n_gpu_layers=int(model_raw.get("n_gpu_layers", 0)),
            max_tokens=int(model_raw.get("max_tokens", 256)),
        )

    signing_raw = data.get("signing") or {}
    signing = SigningConfig(
        enabled=bool(signing_raw.get("enabled", False)),
        key_env=signing_raw.get("key_env"),
        alg=str(signing_raw.get("alg", "sha256")).lower(),
    )

    return AppConfig(peers=peers, model=model_cfg, signing=signing, self_peer_id=str(self_peer_id))
