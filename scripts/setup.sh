#!/usr/bin/env bash
set -euo pipefail

# =================================================================
# Author: Mark Bingham
#
# Updates apt, installs system level deps, then creates and
# activates a Python virtual environment, upgrades pip, and
# installs common dependencies for the hospital federator demo.
#
# Usage:
#   bash setup.sh
#
# Optional env vars:
#   PYTHON_BIN=python3        # which python to use
#   VENV_DIR=.venv            # venv directory name
#   REQUIREMENTS=requirements.txt  # install from file if present
#   EXTRA_PIP_ARGS="..."      # e.g. "--upgrade --no-cache-dir"
# =================================================================

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
REQUIREMENTS="${REQUIREMENTS:-requirements.txt}"
EXTRA_PIP_ARGS="${EXTRA_PIP_ARGS:-}"

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "NOTE: You ran this script with 'bash'."
  echo "The venv cannot stay active after the script ends."
  echo "Run it with:  source setup_venv.sh"
  echo
fi

sudo apt update
sudo apt install -y python3-tk

mkdir -p logs

echo "==> Using Python: ${PYTHON_BIN}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "ERROR: ${PYTHON_BIN} not found on PATH."
  echo "Try: PYTHON_BIN=python bash setup_venv.sh"
  exit 1
fi

echo "==> Python version:"
"${PYTHON_BIN}" --version

# Debian/Ubuntu often needs python3-venv installed
if ! "${PYTHON_BIN}" -m venv --help >/dev/null 2>&1; then
  echo "ERROR: venv module not available for ${PYTHON_BIN}."
  echo "On Debian/Ubuntu try: sudo apt-get update && sudo apt-get install -y python3-venv"
  exit 1
fi

if [[ -d "${VENV_DIR}" ]]; then
  echo "==> Virtual environment already exists at ${VENV_DIR}"
else
  echo "==> Creating virtual environment at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# Activate
echo "==> Activating ${VENV_DIR}"
source "${VENV_DIR}/bin/activate"

echo "==> Upgrading pip/setuptools/wheel"
python -m pip install ${EXTRA_PIP_ARGS} --upgrade pip setuptools wheel

if [[ -f "${REQUIREMENTS}" ]]; then
  echo "==> Installing from ${REQUIREMENTS}"
  pip install ${EXTRA_PIP_ARGS} -r "${REQUIREMENTS}"
else
  echo "==> Installing packages (requests, pyyaml, llama-cpp-python, llama-stack)"
  pip install ${EXTRA_PIP_ARGS} requests pyyaml llama-cpp-python llama-stack faker
fi



echo
echo "==> Verifying GUI and LLM dependencies"

# Check tkinter (already installed via apt, but verify import)
python - <<'EOF'
try:
    import tkinter
    print("tkinter available")
except Exception as e:
    print("tkinter import failed:", e)
    raise SystemExit(1)
EOF

# Check llama-cpp-python import
python - <<'EOF'
try:
    import llama_cpp
    print("llama-cpp-python available")
except Exception as e:
    print("llama-cpp-python NOT available")
    print("  Reason:", e)
    print()
    print("  The anonymous summary button will be disabled.")
    print("  This is usually due to a build or environment failure, or missing CPU features.")
    print()
    print("  Common fixes:")
    print("   - Ensure you are using the same Python as this venv")
    print("   - Try: pip install --force-reinstall llama-cpp-python")
    print("   - For AVX issues, see llama-cpp-python build docs")
    print()
    # This can be ignored if you do not wish to generate anonymous summaries
EOF

echo "Downloading a lightweight LLM model to use for summarisation."
mkdir -p ~/models
wget -O ~/models/llama-3.2-3b-instruct-q4_k_m.gguf \
https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-uncensored-GGUF/resolve/main/Llama-3.2-3B-Instruct-uncensored-Q5_K_S.gguf

# The censored version is below, but it won't process personal information or medical terms, which defeats our purpose.
# The version that this script grabs is uncensorted but should be used carefully and legally.
# Given that we are only planning to use made-up sample data for this demo, it shold be fine.
#
# https://huggingface.co/hugging-quants/Llama-3.2-3B-Instruct-Q4_K_M-GGUF/resolve/main/llama-3.2-3b-instruct-q4_k_m.gguf
#

echo "Done setting up venv, to activate it in your current shell:"
echo "  source .venv/bin/activate"



