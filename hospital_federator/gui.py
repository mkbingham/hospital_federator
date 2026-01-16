from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import messagebox, ttk

from .config import AppConfig, Peer
from .db import OutboxDB
from .events import make_document_event, make_summary_event
from .llm import LocalSummarizer
from .net import FederationClient
from .utils import (
    HOSPITAL_ICON,
    make_document_id,
    make_virtual_doc_id_for_summary,
    normalize_text,
    now_ts,
    sha256_hex,
)

# faker is optional unless you use "Generate Fake Information"
try:
    from faker import Faker
except Exception:  # pragma: no cover
    Faker = None  # type: ignore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FakeCase:
    name: str
    age: int
    dob: str
    address: str
    phone: str
    email: str
    nhs_no: str
    ref: str
    gp: str
    symptoms: List[str]
    associated: List[str]
    negatives: List[str]
    onset: str
    severity: str
    temp_c: float
    hr: int
    bp: str
    spo2: int
    differentials: List[str]


class FakeDataGenerator:
    """Generates realistic-looking *test* clinical documents."""

    def __init__(self) -> None:
        if Faker is None:
            raise RuntimeError("faker is not installed")
        try:
            self.fk = Faker("en_GB")
        except Exception:
            self.fk = Faker()

        self.symptoms_bank = self._build_symptom_bank()
        self.differential_bank = self._build_differential_bank()
        self.duration_phrases = [
            "since yesterday",
            "for 2–3 days",
            "for about a week",
            "for 10 days",
            "for 2 weeks",
            "intermittently over the last month",
        ]
        self.severities = ["mild", "moderate", "severe"]
        self.negatives_pool = [
            "no chest pain",
            "no shortness of breath",
            "no fever",
            "no vomiting",
            "no haematuria",
            "no blood in stool",
            "no recent travel",
            "no known sick contacts",
            "no rash",
            "no focal weakness",
        ]

    @staticmethod
    def _build_symptom_bank() -> List[str]:
        return [
            # General
            "fever",
            "chills",
            "rigors",
            "fatigue",
            "malaise",
            "lethargy",
            "night sweats",
            "unintentional weight loss",
            "weight gain",
            "loss of appetite",
            "dehydration",
            "generalised weakness",
            "reduced exercise tolerance",
            # ENT
            "sore throat",
            "runny nose",
            "nasal congestion",
            "sinus pressure",
            "earache",
            "reduced hearing",
            "tinnitus",
            "hoarseness",
            "post-nasal drip",
            "facial pain",
            "blocked nose",
            "sneezing",
            # Respiratory
            "cough",
            "productive cough",
            "dry cough",
            "shortness of breath",
            "wheeze",
            "pleuritic chest pain",
            "chest tightness",
            "stridor",
            "hemoptysis",
            "noisy breathing",
            # Cardiovascular
            "chest pain",
            "palpitations",
            "ankle swelling",
            "orthopnoea",
            "paroxysmal nocturnal dyspnoea",
            "syncope",
            "near-syncope",
            "light-headedness",
            "claudication",
            "cold extremities",
            # Gastrointestinal
            "abdominal pain",
            "epigastric pain",
            "heartburn",
            "nausea",
            "vomiting",
            "diarrhoea",
            "constipation",
            "bloating",
            "flatulence",
            "early satiety",
            "blood in stool",
            "black tarry stool",
            "mucus in stool",
            "rectal pain",
            "jaundice",
            "difficulty swallowing",
            "pain on swallowing",
            # Genitourinary
            "dysuria",
            "urinary frequency",
            "urgency",
            "nocturia",
            "haematuria",
            "flank pain",
            "reduced urine output",
            "urinary incontinence",
            "pelvic pain",
            # Neurological
            "headache",
            "migraine-like headache",
            "dizziness",
            "vertigo",
            "pins and needles",
            "numbness",
            "weakness",
            "tremor",
            "confusion",
            "memory difficulty",
            "visual disturbance",
            "double vision",
            "slurred speech",
            "photophobia",
            "neck stiffness",
            "poor balance",
            "unsteady gait",
            # MSK
            "joint pain",
            "joint swelling",
            "muscle aches",
            "back pain",
            "neck pain",
            "morning stiffness",
            "reduced range of movement",
            "tenderness",
            "muscle cramps",
            # Dermatological
            "rash",
            "itching",
            "hives",
            "eczema flare",
            "psoriasis flare",
            "skin redness",
            "bruising",
            "skin lesion",
            "wound not healing",
            "localized swelling",
            "warmth over an area",
            # Endocrine/metabolic
            "increased thirst",
            "increased urination",
            "heat intolerance",
            "cold intolerance",
            "sweating",
            "shakiness",
            "hungry all the time",
            # Mental health / sleep
            "anxiety",
            "low mood",
            "panic episodes",
            "poor concentration",
            "insomnia",
            "hypersomnia",
            "irritability",
            # Other
            "generalised pain",
            "chest discomfort",
            "back ache",
            "recurrent infections",
            "swollen glands",
            "mouth ulcers",
        ]

    @staticmethod
    def _build_differential_bank() -> List[str]:
        return [
            "viral upper respiratory tract infection",
            "influenza",
            "COVID-19",
            "acute bronchitis",
            "pneumonia",
            "asthma exacerbation",
            "COPD exacerbation",
            "allergic rhinitis",
            "acute sinusitis",
            "gastroenteritis",
            "GERD",
            "gastritis",
            "peptic ulcer disease",
            "IBS",
            "constipation",
            "UTI",
            "pyelonephritis",
            "renal colic",
            "dehydration",
            "migraine",
            "tension-type headache",
            "benign positional vertigo",
            "labyrinthitis",
            "anxiety/panic",
            "musculoskeletal strain",
            "costochondritis",
            "viral syndrome",
            "community-acquired infection",
            "medication side effect",
        ]

    def _sample_unique(self, items: List[str], min_n: int, max_n: int) -> List[str]:
        # Faker's random_elements sometimes returns duplicates when unique=True across versions.
        # Normalise with set then sort for stable-ish output.
        n = self.fk.random_int(min=min_n, max=max_n)
        picked = self.fk.random_elements(elements=items, length=n, unique=True)
        return sorted(set(picked))

    def generate(self) -> FakeCase:
        fk = self.fk

        name = fk.name()
        address = fk.address()
        phone = fk.phone_number()
        email = fk.email()

        dob_dt = fk.date_of_birth(minimum_age=18, maximum_age=95)
        dob = dob_dt.strftime("%Y-%m-%d")
        # keep age deterministic from faker dob
        today = time.localtime()
        age = today.tm_year - dob_dt.year - ((today.tm_mon, today.tm_mday) < (dob_dt.month, dob_dt.day))

        nhs_no = fk.bothify(text="##########")
        ref = fk.bothify(text="REF-????-######")
        gp = fk.name()

        symptoms = self._sample_unique(self.symptoms_bank, 3, 6)
        associated = self._sample_unique(self.symptoms_bank, 0, 2)
        negatives = self._sample_unique(self.negatives_pool, 2, 4)
        onset = fk.random_element(elements=self.duration_phrases)
        severity = fk.random_element(elements=self.severities)

        differentials = self._sample_unique(self.differential_bank, 3, 6)

        temp_c = round(float(fk.pyfloat(min_value=36.2, max_value=38.9, right_digits=1)), 1)
        hr = int(fk.random_int(min=58, max=118))
        bp = f"{fk.random_int(min=98, max=158)}/{fk.random_int(min=60, max=96)}"
        spo2 = int(fk.random_int(min=93, max=100))

        return FakeCase(
            name=name,
            age=age,
            dob=dob,
            address=address,
            phone=phone,
            email=email,
            nhs_no=nhs_no,
            ref=ref,
            gp=gp,
            symptoms=symptoms,
            associated=associated,
            negatives=negatives,
            onset=onset,
            severity=severity,
            temp_c=temp_c,
            hr=hr,
            bp=bp,
            spo2=spo2,
            differentials=differentials,
        )

    @staticmethod
    def format_document(case: FakeCase, gp_note: Optional[str]) -> str:
        block = (
            "PATIENT DETAILS (FAKE TEST DATA)\n"
            "------------------------------\n"
            f"Name: {case.name}\n"
            f"DOB: {case.dob} (Age: {case.age})\n"
            f"NHS No: {case.nhs_no}\n"
            f"Reference: {case.ref}\n"
            f"Address:\n{case.address}\n"
            f"Telephone: {case.phone}\n"
            f"Email: {case.email}\n"
            f"Registered GP: {case.gp}\n\n"
            "CLINICAL (FAKE TEST DATA)\n"
            "-------------------------\n"
            f"Presenting symptoms: {', '.join(case.symptoms)}\n"
            f"Onset/duration: {case.onset}\n"
            f"Severity: {case.severity}\n"
            f"Associated: {', '.join(case.associated) if case.associated else 'none reported'}\n"
            f"Denies: {', '.join(case.negatives)}\n"
            f"Obs (approx): T {case.temp_c}°C, HR {case.hr}, BP {case.bp}, SpO₂ {case.spo2}%\n\n"
            "POTENTIAL DIAGNOSIS OPTIONS (FAKE TEST DATA)\n"
            "----------------------------------\n"
            + "- "
            + "\n- ".join(case.differentials)
            + "\n"
        )

        if gp_note:
            block += (
                "\nGP NOTE (LLM GENERATED — TEST DATA)\n"
                "-------------------------------\n"
                f"{gp_note.strip()}\n"
            )

        return block


class PeerWindow:
    """Main UI window with tabs: Compose, Outbox/Resend, Received."""

    def __init__(
        self,
        root: tk.Tk,
        cfg: AppConfig,
        outbox: OutboxDB,
        fed_client: FederationClient,
        summarizer: LocalSummarizer,
        window_peer: Peer,
        window: Optional[tk.Tk] = None,
    ):
        self.root = root
        self.cfg = cfg
        self.outbox = outbox
        self.fed_client = fed_client
        self.summarizer = summarizer
        self.window_peer = window_peer

        self.peers_by_id = {p.peer_id: p for p in cfg.peers}
        self.self_peer = self.peers_by_id[cfg.self_peer_id]

        self.win = window or root
        self.win.title(f"Hospital Federator — {cfg.self_peer_id} — {window_peer.name}")
        self._maximize_window_best_effort()

        self.nb = ttk.Notebook(self.win)
        self.nb.pack(fill="both", expand=True)

        self.compose = ttk.Frame(self.nb)
        self.outbox_tab = ttk.Frame(self.nb)
        self.inbox_tab = ttk.Frame(self.nb)

        self.nb.add(self.compose, text="Compose")
        self.nb.add(self.outbox_tab, text="Outbox / Resend")
        self.nb.add(self.inbox_tab, text="Received")

        self._build_compose()
        self._build_outbox()
        self._build_inbox()

    def _maximize_window_best_effort(self) -> None:
        """Best-effort maximise across platforms/window-managers."""
        try:
            self.win.update_idletasks()
        except Exception:
            pass

        try:
            self.win.state("zoomed")
            return
        except Exception:
            pass

        try:
            self.win.attributes("-zoomed", True)
            return
        except Exception:
            pass

        try:
            sw = int(self.win.winfo_screenwidth())
            sh = int(self.win.winfo_screenheight())
            self.win.geometry(f"{sw}x{sh}+0+0")
        except Exception:
            pass

    # ---------------------------
    # Compose
    # ---------------------------

    def _build_compose(self) -> None:
        top = ttk.Frame(self.compose)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Send to peers:").grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.check_vars: Dict[str, tk.BooleanVar] = {}
        col = 1
        for p in self.cfg.peers:
            is_self = p.peer_id == self.cfg.self_peer_id
            var = tk.BooleanVar(value=(not is_self))
            self.check_vars[p.peer_id] = var
            cb = ttk.Checkbutton(top, text=f"{p.name} ({p.peer_id})", variable=var)
            if is_self:
                cb.state(["disabled"])
                var.set(False)
            cb.grid(row=0, column=col, sticky="w", padx=6)
            col += 1

        doc_frame = ttk.LabelFrame(self.compose, text="Original document")
        doc_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        doc_hdr = ttk.Frame(doc_frame)
        doc_hdr.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(doc_hdr, text=HOSPITAL_ICON, font=("Segoe UI Emoji", 14)).pack(side="left", padx=(0, 8))
        ttk.Label(
            doc_hdr,
            text=(
                "Paste the original document here. It will NOT be sent unless you permit sending of personally identifiable information by ticking 'Send original document'."
                " (The summary is always sent when non-empty.)"
            ),
        ).pack(side="left", anchor="w")

        self.send_original_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(doc_hdr, text="Send original document", variable=self.send_original_var).pack(
            side="right", padx=(8, 8)
        )

        self.original_doc_text = tk.Text(doc_frame, wrap="word", height=12)
        self.original_doc_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tools = ttk.Frame(doc_frame)
        tools.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(tools, text="Generate Fake Information", command=self._on_generate_fake_information).pack(
            side="left"
        )
        ttk.Button(tools, text="Clear document", command=lambda: self.original_doc_text.delete("1.0", "end")).pack(
            side="right"
        )

        sum_frame = ttk.LabelFrame(self.compose, text="Summary (sent if non-empty)")
        sum_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        sum_hdr = ttk.Frame(sum_frame)
        sum_hdr.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(sum_hdr, text=HOSPITAL_ICON, font=("Segoe UI Emoji", 14)).pack(side="left", padx=(0, 8))
        ttk.Label(sum_hdr, text="Generate summary from document, or tick manual to paste summarised data.").pack(side="left", anchor="w")

        self.summary_edit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            sum_hdr, text="Manual edit", variable=self.summary_edit_var, command=self._on_toggle_summary_edit
        ).pack(side="right", padx=(8, 8))

        self.gen_btn = ttk.Button(sum_hdr, text="Generate Summary", command=self._on_generate_summary)
        self.gen_btn.pack(side="right")

        if not self.summarizer.available():
            self.gen_btn.state(["disabled"])
            self.gen_btn.configure(text="Summary unavailable)")

        self.sum_text = tk.Text(sum_frame, wrap="word", height=8)
        self.sum_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.sum_text.config(state="disabled")  # read-only by default

        status_frame = ttk.LabelFrame(self.compose, text="Delivery status (last submit)")
        status_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(status_frame, textvariable=self.status_var).pack(anchor="w", padx=10, pady=(6, 6))

        cols = ("peer", "url", "status", "detail")
        self.status_tree = ttk.Treeview(status_frame, columns=cols, show="headings", height=6)
        for c in cols:
            self.status_tree.heading(c, text=c)
            self.status_tree.column(c, width=200 if c in ("peer", "status") else 360)
        self.status_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        bottom = ttk.Frame(self.compose)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bottom, text="Share data with other hospitals", command=self._on_submit).pack(side="right")

    def _on_toggle_summary_edit(self) -> None:
        editable = bool(self.summary_edit_var.get())
        self.sum_text.config(state=("normal" if editable else "disabled"))

    def _set_summary_text(self, text: str) -> None:
        prev = str(self.sum_text.cget("state"))
        self.sum_text.config(state="normal")
        self.sum_text.delete("1.0", "end")
        self.sum_text.insert("1.0", text)
        self.sum_text.config(state=prev)

    def _clear_summary_text(self) -> None:
        prev = str(self.sum_text.cget("state"))
        self.sum_text.config(state="normal")
        self.sum_text.delete("1.0", "end")
        self.sum_text.config(state=prev)

    def _selected_dests(self) -> List[Peer]:
        dests: List[Peer] = []
        for pid, var in self.check_vars.items():
            if pid == self.cfg.self_peer_id:
                continue
            if var.get():
                dests.append(self.peers_by_id[pid])
        return dests

    def _clear_status_tree(self) -> None:
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)

    def _append_status(self, peer: Peer, status: str, detail: str) -> None:
        self.status_tree.insert("", "end", values=(f"{peer.name} ({peer.peer_id})", peer.url, status, detail))

    def _on_generate_fake_information(self) -> None:
        if Faker is None:
            messagebox.showwarning("Missing dependency", "Install faker with: pip install faker")
            return

        try:
            gen = FakeDataGenerator()
            case = gen.generate()

            gp_note = None
            if self.summarizer.available():
                try:
                    gp_note = self.summarizer.gp_note_from_case(
                        {
                            "name": case.name,
                            "age": case.age,
                            "symptoms": case.symptoms,
                            "onset": case.onset,
                            "severity": case.severity,
                            "associated": case.associated,
                            "negatives": case.negatives,
                            "pmh": ["none reported"],
                            "meds": ["none"],
                            "allergies": "NKDA",
                            "temp_c": case.temp_c,
                            "hr": case.hr,
                            "bp": case.bp,
                            "spo2": case.spo2,
                        }
                    )
                except Exception as e:
                    gp_note = f"[LLM GP note generation failed: {e}]"

            block = gen.format_document(case, gp_note)

            self.original_doc_text.delete("1.0", "end")
            self.original_doc_text.insert("1.0", block)
        except Exception as e:
            messagebox.showerror("Fake data generation failed", str(e))

    def _on_generate_summary(self) -> None:
        doc = normalize_text(self.original_doc_text.get("1.0", "end"))
        if not doc:
            messagebox.showwarning("No document", "Paste a document first.")
            return
        if not self.summarizer.available():
            messagebox.showwarning("Not configured", "Configure YAML model.path and install llama-cpp-python.")
            return

        self.status_var.set("Generating summary…")
        try:
            self.win.config(cursor="watch")
        except Exception:
            pass

        def work() -> None:
            try:
                summary = self.summarizer.summarize(doc)

                def done_ok() -> None:
                    self._set_summary_text(summary)
                    self.status_var.set("Summary generated.")
                    try:
                        self.win.config(cursor="")
                    except Exception:
                        pass

                self.root.after(0, done_ok)
            except Exception as e:
                err = str(e)

                def done_err() -> None:
                    self.status_var.set("Summary generation failed.")
                    try:
                        self.win.config(cursor="")
                    except Exception:
                        pass
                    messagebox.showerror("Summary generation failed", err)

                self.root.after(0, done_err)

        threading.Thread(target=work, daemon=True).start()

    def _on_submit(self) -> None:
        doc = ""
        if bool(self.send_original_var.get()):
            doc = normalize_text(self.original_doc_text.get("1.0", "end"))
        summary = normalize_text(self.sum_text.get("1.0", "end"))

        if not doc and not summary:
            messagebox.showwarning("Nothing to send", "Both Document and Summary are empty.")
            return

        dests = self._selected_dests()
        if not dests:
            messagebox.showwarning("No destinations", "Select at least one non-self peer to send to.")
            return

        source = f"gui:{self.self_peer.peer_id}:{self.window_peer.peer_id}"
        events: List[Dict[str, Any]] = []

        if doc:
            doc_id = make_document_id(doc, source)
            events.append(make_document_event(self.self_peer.peer_id, doc_id, doc, source))

        if summary:
            if doc:
                doc_id_for_summary = make_document_id(doc, source)
                input_hash = sha256_hex(doc.encode("utf-8"))
            else:
                doc_id_for_summary = make_virtual_doc_id_for_summary(summary, source)
                input_hash = sha256_hex(summary.encode("utf-8"))

            events.append(
                make_summary_event(
                    origin_node=self.self_peer.peer_id,
                    doc_id=doc_id_for_summary,
                    input_hash=input_hash,
                    summary_text=summary,
                    model_id=("llama-cpp" if self.summarizer.available() else "manual"),
                    prompt_version=("summary_bullets_v1" if self.summarizer.available() else "manual_v1"),
                )
            )

        label = "doc" if doc and not summary else "summary" if summary and not doc else "doc+summary"
        job_id = self.outbox.add_job(self.self_peer.peer_id, label, events, dests)

        self.status_var.set(f"Queued job {job_id}. Sending…")
        self._clear_status_tree()

        def send_job() -> None:
            for peer in dests:
                ok, msg, http_status = self.fed_client.push_events(peer, events)

                deliveries = self.outbox.get_pending_or_failed_targets(job_id)
                attempt = 1
                for d in deliveries:
                    if d["target_peer_id"] == peer.peer_id:
                        attempt = int(d["attempts"]) + 1
                        break

                self.outbox.update_delivery(
                    job_id=job_id,
                    target_peer_id=peer.peer_id,
                    status=("SENT" if ok else "FAILED"),
                    attempts=attempt,
                    last_error=(None if ok else msg),
                    last_attempt_at=now_ts(),
                    last_http_status=http_status,
                )

                def ui_update(peer: Peer = peer, ok: bool = ok, msg: str = msg) -> None:
                    self._append_status(peer, "SENT" if ok else "FAILED", msg)

                self.root.after(0, ui_update)

            def finish() -> None:
                self.status_var.set(f"Send complete for job {job_id}.")
                if doc:
                    self.original_doc_text.delete("1.0", "end")
                self._clear_summary_text()
                self._refresh_outbox()

            self.root.after(0, finish)

        threading.Thread(target=send_job, daemon=True).start()

    # ---------------------------
    # Outbox
    # ---------------------------

    def _build_outbox(self) -> None:
        top = ttk.Frame(self.outbox_tab)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Outbox. Failed jobs marked as pending/failed, select and retry if needed.").pack(
            side="left", anchor="w"
        )
        ttk.Button(top, text="Refresh", command=self._refresh_outbox).pack(side="right")

        cols = ("job_id", "created_at", "label", "targets")
        self.jobs_tree = ttk.Treeview(self.outbox_tab, columns=cols, show="headings", height=12)
        for c in cols:
            self.jobs_tree.heading(c, text=c)
            self.jobs_tree.column(c, width=220 if c != "targets" else 420)
        self.jobs_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        btns = ttk.Frame(self.outbox_tab)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="View deliveries", command=self._view_deliveries).pack(side="left")
        ttk.Button(btns, text="Resend pending/failed", command=self._resend_selected).pack(side="right")

        self.deliveries_text = tk.Text(self.outbox_tab, height=10, wrap="word")
        self.deliveries_text.pack(fill="both", expand=False, padx=10, pady=(0, 10))

        self._refresh_outbox()

    def _refresh_outbox(self) -> None:
        for item in self.jobs_tree.get_children():
            self.jobs_tree.delete(item)

        jobs = self.outbox.list_jobs(limit=200)
        for j in jobs:
            created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(j["created_at"]))
            self.jobs_tree.insert("", "end", values=(j["job_id"], created, j["label"], ", ".join(j["targets"])))

    def _selected_job_id(self) -> Optional[str]:
        sel = self.jobs_tree.selection()
        if not sel:
            return None
        vals = self.jobs_tree.item(sel[0], "values")
        return str(vals[0]) if vals else None

    def _view_deliveries(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            messagebox.showinfo("Select a job", "Select a job first.")
            return

        deliveries = self.outbox.list_deliveries(job_id)
        lines = [f"Deliveries for job {job_id}:"]
        for d in deliveries:
            ts = d["last_attempt_at"]
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"
            lines.append(
                f"- {d['target_peer_id']} [{d['status']}] attempts={d['attempts']} http={d['last_http_status']} last={when} err={d['last_error']}"
            )

        self.deliveries_text.delete("1.0", "end")
        self.deliveries_text.insert("1.0", "\n".join(lines))

    def _resend_selected(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            messagebox.showinfo("Select a job", "Select a job first.")
            return

        pending = self.outbox.get_pending_or_failed_targets(job_id)
        if not pending:
            messagebox.showinfo("Nothing to resend", "No pending/failed deliveries for this job.")
            return

        events = self.outbox.get_job_events(job_id)
        if not events:
            messagebox.showerror("Missing events", "Could not load events for the selected job.")
            return

        targets: List[Peer] = []
        for p in pending:
            pid = p["target_peer_id"]
            if pid in self.peers_by_id and pid != self.cfg.self_peer_id:
                targets.append(self.peers_by_id[pid])

        if not targets:
            messagebox.showinfo("No targets", "No valid targets to resend to.")
            return

        self.status_var.set(f"Resending pending/failed for job {job_id}…")
        self._clear_status_tree()

        def work() -> None:
            for peer in targets:
                ok, msg, http_status = self.fed_client.push_events(peer, events)

                pend = self.outbox.get_pending_or_failed_targets(job_id)
                attempt = 1
                for d in pend:
                    if d["target_peer_id"] == peer.peer_id:
                        attempt = int(d["attempts"]) + 1
                        break

                self.outbox.update_delivery(
                    job_id=job_id,
                    target_peer_id=peer.peer_id,
                    status=("SENT" if ok else "FAILED"),
                    attempts=attempt,
                    last_error=(None if ok else msg),
                    last_attempt_at=now_ts(),
                    last_http_status=http_status,
                )

                def ui_update(peer: Peer = peer, ok: bool = ok, msg: str = msg) -> None:
                    self._append_status(peer, "SENT" if ok else "FAILED", msg)

                self.root.after(0, ui_update)

            def done() -> None:
                self.status_var.set(f"Resend complete for job {job_id}.")
                self._refresh_outbox()

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------
    # Inbox
    # ---------------------------


    def _build_inbox(self) -> None:
        top = ttk.Frame(self.inbox_tab)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(
            top,
            text="Received events).",
        ).pack(side="left", anchor="w")
        ttk.Button(top, text="Refresh", command=self._refresh_inbox).pack(side="right")

        cols = ("received_at", "from_peer", "events", "bytes", "push_id")
        self.inbox_tree = ttk.Treeview(self.inbox_tab, columns=cols, show="headings", height=8)
        for c in cols:
            self.inbox_tree.heading(c, text=c)
        self.inbox_tree.column("received_at", width=160)
        self.inbox_tree.column("from_peer", width=140)
        self.inbox_tree.column("events", width=80, anchor="center")
        self.inbox_tree.column("bytes", width=90, anchor="center")
        self.inbox_tree.column("push_id", width=380)
        self.inbox_tree.pack(fill="x", expand=False, padx=10, pady=(0, 10))
        self.inbox_tree.bind("<<TreeviewSelect>>", lambda _e: self._view_inbox_payload())

        viz = ttk.LabelFrame(self.inbox_tab, text="JSON visualiser")
        viz.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        viz_hdr = ttk.Frame(viz)
        viz_hdr.pack(fill="x", padx=8, pady=(6, 4))
        ttk.Label(viz_hdr, text="Structured view of the selected payload").pack(side="left")
        ttk.Button(viz_hdr, text="Expand all", command=lambda: self._json_tree_set_open(True)).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(viz_hdr, text="Collapse all", command=lambda: self._json_tree_set_open(False)).pack(
            side="right"
        )

        # JSON tree
        try:
            style = ttk.Style(self.win)
            style.configure("Json.Treeview", rowheight=22)
            style.configure("Json.Treeview.Heading", font=("TkDefaultFont", 10, "bold"))
        except Exception:
            pass

        self.json_tree = ttk.Treeview(
            viz,
            columns=("value",),
            show="tree headings",
            height=16,
            style="Json.Treeview",
        )
        self.json_tree.heading("#0", text="Key")
        self.json_tree.heading("value", text="Value")
        self.json_tree.column("#0", width=260, anchor="w")
        self.json_tree.column("value", width=820, anchor="w")

        ysb = ttk.Scrollbar(viz, orient="vertical", command=self.json_tree.yview)
        xsb = ttk.Scrollbar(viz, orient="horizontal", command=self.json_tree.xview)
        self.json_tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        self.json_tree.pack(side="top", fill="both", expand=True, padx=8)
        #xsb.pack(side="top", fill="x", padx=8)
        #ysb.pack(side="right", fill="y")

        # Subtle striping to improve readability
        try:
            self.json_tree.tag_configure("odd", background="#f6f6f6")
            self.json_tree.tag_configure("even", background="#ffffff")
            self.json_tree.tag_configure("container", font=("TkDefaultFont", 10, "bold"))
        except Exception:
            pass

        self._refresh_inbox()
        self._schedule_inbox_refresh()

    def _schedule_inbox_refresh(self) -> None:
        def tick() -> None:
            try:
                self._refresh_inbox(quiet=True)
            finally:
                self.root.after(3000, tick)

        self.root.after(3000, tick)

    def _refresh_inbox(self, quiet: bool = False) -> None:
        # Track which push_id is currently rendered in the JSON pane so we
        # don't repaint them on every periodic refresh (causes annoying behaviour if you do).
        current_rendered = getattr(self, "_inbox_current_push_id", None)

        selected_push = None
        sel = self.inbox_tree.selection()
        if sel:
            vals = self.inbox_tree.item(sel[0], "values")
            if vals:
                selected_push = vals[-1]

        for item in self.inbox_tree.get_children():
            self.inbox_tree.delete(item)

        rows = self.outbox.list_inbox_pushes(limit=500)
        for r in rows:
            received = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["received_at"]))
            self.inbox_tree.insert(
                "",
                "end",
                values=(
                    received,
                    r.get("from_peer_id") or "",
                    str(r.get("events_count") or 0),
                    str(r.get("bytes_len") or 0),
                    r.get("push_id") or "",
                ),
            )

        restored = False
        if selected_push:
            for item in self.inbox_tree.get_children():
                vals = self.inbox_tree.item(item, "values")
                if vals and vals[-1] == selected_push:
                    self.inbox_tree.selection_set(item)
                    restored = True
                    break

        # During auto-refresh: avoid repainting the JSON pane unless the
        # selection actually changed from what is currently displayed.
        # This prevents the UI from looking "glitchy".
        if quiet:
            sel2 = self.inbox_tree.selection()
            new_selected_push = None
            if sel2:
                vals2 = self.inbox_tree.item(sel2[0], "values")
                if vals2:
                    new_selected_push = vals2[-1]

            # If the selected push differs from what we are currently showing,
            # update the JSON pane; otherwise leave it untouched.
            if new_selected_push and new_selected_push != current_rendered:
                try:
                    self._view_inbox_payload(force=True)
                except Exception:
                    pass
            # If a selection disappeared (e.g., push removed), clear panes once.
            elif (current_rendered and not new_selected_push):
                self._inbox_current_push_id = None
                try:
                    self._json_tree_clear()
                    self._json_tree_set_message("Select a row to view the parsed JSON.")
                except Exception:
                    pass

        if not quiet:
            self._json_tree_clear()
            self._json_tree_set_message("Select a row to view the parsed JSON.")
            try:
                self._json_tree_clear()
                self.json_tree.insert("", "end", text="(select an item above)", values=("Structured view will appear here",))
            except Exception:
                pass


    def _json_tree_clear(self) -> None:
        if not hasattr(self, "json_tree"):
            return
        for item in self.json_tree.get_children():
            self.json_tree.delete(item)

    def _json_tree_set_message(self, message: str) -> None:
        self._json_tree_clear()
        if hasattr(self, "json_tree"):
            self.json_tree.insert("", "end", text=message, values=("",))

    def _json_tree_fill(self, obj) -> None:
        self._json_tree_clear()

        row_idx = 0

        def add(parent: str, key: str, value) -> None:
            nonlocal row_idx
            # Render primitives in the value column; containers become nodes.
            if isinstance(value, dict):
                tag = "odd" if (row_idx % 2) else "even"
                node = self.json_tree.insert(parent, "end", text=str(key), values=("{...}",), tags=(tag, "container"))
                row_idx += 1
                # Stable ordering for easier scanning.
                for k in sorted(value.keys(), key=lambda x: str(x)):
                    v = value[k]
                    add(node, k, v)
            elif isinstance(value, list):
                tag = "odd" if (row_idx % 2) else "even"
                node = self.json_tree.insert(parent, "end", text=str(key), values=(f"[{len(value)}]",), tags=(tag, "container"))
                row_idx += 1
                for i, v in enumerate(value):
                    add(node, f"[{i}]", v)
            else:
                # keep it single-line and friendly
                s = "" if value is None else str(value)
                s = s.replace("\n", " ").strip()
                if len(s) > 200:
                    s = s[:200] + "…"
                tag = "odd" if (row_idx % 2) else "even"
                self.json_tree.insert(parent, "end", text=str(key), values=(s,), tags=(tag,))
                row_idx += 1

        # Root
        if isinstance(obj, dict):
            for k in sorted(obj.keys(), key=lambda x: str(x)):
                add("", k, obj[k])
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                add("", f"[{i}]", v)
        else:
            self.json_tree.insert("", "end", text="value", values=(str(obj),))

        # Open the first level for convenience.
        for item in self.json_tree.get_children():
            self.json_tree.item(item, open=True)

    # Backwards-compatible wrappers used by older call sites.
    def _json_tree_populate(self, obj) -> None:
        self._json_tree_fill(obj)

    def _json_tree_insert(self, parent: str, key: str, value: Any) -> None:
        s = "" if value is None else str(value)
        s = s.replace("\n", " ").strip()
        self.json_tree.insert(parent, "end", text=str(key), values=(s,))

    def _json_tree_set_open(self, open_state: bool) -> None:
        """Expand/collapse all nodes in the JSON tree."""

        def walk(item: str) -> None:
            try:
                self.json_tree.item(item, open=open_state)
            except Exception:
                return
            for child in self.json_tree.get_children(item):
                walk(child)

        for root_item in self.json_tree.get_children(""):
            walk(root_item)

    def _view_inbox_payload(self, force: bool = False) -> None:
        sel = self.inbox_tree.selection()
        if not sel:
            return
        vals = self.inbox_tree.item(sel[0], "values")
        if not vals:
            return
        push_id = vals[-1]

        # Avoid repainting the panes if we're already showing this push.
        # Auto-refresh calls into this method; without this guard the UI looks
        # like it's constantly flickering.
        if not force and getattr(self, "_inbox_current_push_id", None) == push_id:
            return

        self._inbox_current_push_id = push_id
        payload = self.outbox.get_inbox_push_body(str(push_id))

        try:
            self._json_tree_clear()
        except Exception:
            pass

        if payload:
            try:
                obj = json.loads(payload)
                self._json_tree_fill(obj)
            except Exception:
                # Not JSON; show a single raw node.
                self._json_tree_clear()
                self._json_tree_insert("", "raw", payload)
        else:
            self._json_tree_clear()
            self._json_tree_insert("", "payload", "(payload not found)")

        # Fully expand whenever the user selects an entry.
        try:
            self._json_tree_set_open(True)
        except Exception:
            pass
