from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .config import AppConfig
from .db import OutboxDB
from .gui import PeerWindow
from .llm import LocalSummarizer
from .net import FederationClient


class HospitalFederatorApp:
    """Top-level application object."""

    def __init__(self, cfg: AppConfig, db_path: str):
        self.cfg = cfg
        self.outbox = OutboxDB(db_path)
        self.fed_client = FederationClient(cfg.signing)
        self.summarizer = LocalSummarizer(cfg.model)

        self.root = tk.Tk()
        self._apply_theme(self.root)

        peers_by_id = {p.peer_id: p for p in cfg.peers}
        self_peer = peers_by_id[cfg.self_peer_id]

        self.ui = PeerWindow(
            root=self.root,
            cfg=cfg,
            outbox=self.outbox,
            fed_client=self.fed_client,
            summarizer=self.summarizer,
            window_peer=self_peer,
            window=self.root,
        )

        # Populate outbox on launch.
        try:
            self.ui.refresh_outbox()
        except Exception:
            pass

    @staticmethod
    def _apply_theme(root: tk.Tk) -> None:
        try:
            style = ttk.Style(root)
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()
