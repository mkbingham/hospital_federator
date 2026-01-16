from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from .config import ModelConfig
from .utils import normalize_text

# llama-cpp-python is optional unless you use generative functionality.
try:
    from llama_cpp import Llama
except Exception:  # pragma: no cover
    Llama = None  # type: ignore


class LocalSummarizer:
    """Wrapper around llama-cpp-python for summaries and GP-style notes."""

    def __init__(self, model_cfg: Optional[ModelConfig]):
        self.model_cfg = model_cfg
        self._lock = threading.RLock()
        self._llm: Optional[Any] = None

    def available(self) -> bool:
        return self.model_cfg is not None and Llama is not None

    def _ensure(self) -> Any:
        if not self.model_cfg:
            raise RuntimeError("No model configured (missing YAML model.path)")
        if Llama is None:
            raise RuntimeError("llama-cpp-python is not installed")

        model_path = os.path.abspath(os.path.expanduser(self.model_cfg.path))
        if not os.path.exists(model_path):
            raise RuntimeError(f"Model path does not exist: {model_path}")
        if not os.access(model_path, os.R_OK):
            raise RuntimeError(f"Model file is not readable: {model_path}")

        with self._lock:
            if self._llm is None:
                self._llm = Llama(
                    model_path=model_path,
                    n_ctx=self.model_cfg.n_ctx,
                    n_threads=self.model_cfg.n_threads,
                    n_gpu_layers=self.model_cfg.n_gpu_layers,
                    verbose=False,
                )
            return self._llm

    def summarize(self, text: str) -> str:
        text = normalize_text(text)
        if not text:
            return ""
        llm = self._ensure()

        system = (
            "You are a concise assistant. Remove any specific personally identifiable information then summarize the user-provided document. "
            "Keep gender and medical information as summaries only.\n"
            "Output requirements:\n"
            "- 3 to 8 bullet points\n"
            "- keep it factual\n"
            "- no speculation\n"
            "- no preamble\n"
            "- bullets only"
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ]

        resp = llm.create_chat_completion(
            messages=messages,
            temperature=0.0,
            top_p=1.0,
            top_k=0,
            max_tokens=(self.model_cfg.max_tokens if self.model_cfg else 256),
            repeat_penalty=1.1,
        )
        return (resp["choices"][0]["message"]["content"] or "").strip()

    def gp_note_from_case(self, case: Dict[str, Any]) -> str:
        """Turn structured (fake) clinical fields into a single UK GP-style paragraph."""
        llm = self._ensure()

        def _csv(x: Any) -> str:
            if not x:
                return ""
            if isinstance(x, list):
                return ", ".join(str(i) for i in x if str(i).strip())
            return str(x)

        system = (
            "You are a UK GP writing a concise consultation note. This is TEST DATA ONLY. "
            "Write a single paragraph suitable for a clinical record. "
            "Use plain clinical language and typical GP phrasing. "
            "Give medical advice, propose a management plan, but do NOT speculate. "
            "Do NOT include headings or bullet points. "
            "Do NOT include the patient's full address or any identifiers beyond gender and age."
        )

        name = str(case.get("name", "")).strip()
        first_name = name.split()[0] if name else "Patient"

        user = (
            f"Patient: {first_name}, age {case.get('age', 'unknown')}\n"
            f"Presenting symptoms: {_csv(case.get('symptoms'))}\n"
            f"Onset/duration: {case.get('onset', '')}\n"
            f"Severity: {case.get('severity', '')}\n"
            f"Associated symptoms: {_csv(case.get('associated'))}\n"
            f"Denies: {_csv(case.get('negatives'))}\n"
            f"PMH: {_csv(case.get('pmh'))}\n"
            f"Meds: {_csv(case.get('meds'))}\n"
            f"Allergies: {case.get('allergies', '')}\n"
            f"Obs (approx): T {case.get('temp_c', '')}°C, HR {case.get('hr', '')}, BP {case.get('bp', '')}, SpO2 {case.get('spo2', '')}%\n"
        ).strip()

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        resp = llm.create_chat_completion(
            messages=messages,
            temperature=0.2,
            top_p=0.95,
            top_k=40,
            max_tokens=min(256, (self.model_cfg.max_tokens if self.model_cfg else 256)),
            repeat_penalty=1.05,
        )
        return (resp["choices"][0]["message"]["content"] or "").strip()
