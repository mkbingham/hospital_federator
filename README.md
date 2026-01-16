# Hospital Federator

A Python-based GUI to show multiple peers (e.g. hospitals or trusted organisations) can securely exchange JSON “events”, whilst preserving privacy.

An LLM is used to summarise away any personally identifiable information before sharing.

It provides functionality for generating representative test data and GP notes for the demo.

Each peer:
- Runs as its own process with individual YAML config
- Hosts an authenticated `/events/push` receiver
- Pushes events to other peers over **TLS**; mutually verifying that both client and server certificates are signed by a trusted CA
- Enforces that the presented **client certificate CN matches a known peer ID**
- Signs outbound messages and verifies the signature on receipt (HMAC)

All data generated and transmitted is **TEST / DEMO DATA ONLY**.

---

## Prerequisites

- Python **3.10+** recommended
- OpenSSL (for certificate generation scripts)
- llama-cpp-python package
- Linux / macOS recommended (Windows will require tweaks)
- Remaining dependancies installed by script

---

## Repository Structure (typical)

```
.
├── hospital_federator_demo.py     # Entrypoint (CLI → app)
├── hospital_federator/            # Application package
│   ├── gui.py                     # Tkinter GUI
│   ├── receiver.py                # HTTPS receiver
│   ├── net.py                     # Outbound federation client
│   ├── db.py                      # SQLite persistence
│   ├── llm.py                     # Local LLM wrapper
│   ├── logging_config.py          # Logging setup
│   └── config.py                  # YAML parsing and dataclasses
├── scripts/                       # Helper scripts
│   ├── setup.sh                   # Initial setup (Python venv, pip deps, LLM model etc)
│   ├── generate_peers.sh          # Generates demo CA, peer cryptomat and configs
│   ├── run_all.sh                 # Runs the demo, using all configured peers
│   └── kill_all.sh                # Kills the demo (be sure to run when complete)
├── config/                        # Per-peer YAML configs (gitignored)
│   └── peer.example.yaml
├── certs/                         # Generated certificates (gitignored)
├── dbs/                           # Generated databases for each peer (gitignored)
├── keys/                          # Generated keys (gitignored)
├── data/                          # SQLite databases (gitignored)
└── tests/                         # pytest smoke tests
```

---

## Quickstart

This will get you started, so long as the prerequisites, above, are already met:

```bash
./scripts/setup.sh
source .venv/bin/activate
pytest
./scripts/generate_peers.sh
./scripts/run_all.sh
```

The default script will generate three peers, this is adjustable via the global variables in setup.sh.

When you are done:

```bash
./scripts/kill_all.sh
```

More detail below.


## 1) Setup

Run the setup script to create a virtual environment and install dependencies.

```bash
./scripts/setup.sh
```

Behaviour:
- Creates a Python virtual environment (e.g. `.venv/`)
- Installs required dependencies (e.g. Tkinter helpers, PyYAML, requests, faker, etc.)
- Installs `llama-cpp-python` (for local LLM support)
- Downloads a usable LLM model

Activate the environment (if you need to manually):

```bash
source .venv/bin/activate
```

Verify everything works:

```bash
pytest
```

---

## 2) Generate Peers (certificates + configs)

Generate peer certificates and per-peer YAML config files:

```bash
./scripts/generate_peers.sh
```

This script:
- Creates a local CA (if not already present)
- Generates **client certificates** with `CN == peer_id`
- Generates server certificates
- Produces one YAML file per peer from a base template

Example output:

```
config/peer1.yaml
config/peer2.yaml
config/peer3.yaml
certs/ca.crt
certs/peer1.crt
keys/peer1.key
certs/peer2.crt
keys/peer2.key
...
```

**Important**
- The receiver enforces that the TLS client cert CN matches the peer ID.
- Ensure your generation script it configured to set the CN correctly.

---

## 3) To run Everything

Start one process per peer configuration:

```bash
./scripts/run_all.sh
```

This script:
- Finds all `config/*.yaml`
- Starts one instance of the app per config
- Writes PID files for clean shutdown

You should see:
- One GUI window per peer
- One HTTPS receiver port per peer (e.g. 8000, 8001, …)

### To run a single peer manually

```bash
source .venv/bin/activate
python hospital_federator_demo.py --config config/peer1.yaml
```

With logging options:

```bash
python hospital_federator_demo.py   --config config/peer1.yaml   --log-level DEBUG   --log-file logs/peer1.log
```

---

## 4) To kill Everything

If peers were started using `run_all.sh`, stop them cleanly with:

```bash
./scripts/kill_all.sh
```

Typical behaviour:
- Reads PID files
- Sends `SIGTERM`
- Escalates to `SIGKILL` if needed
- Cleans up PID files

### Emergency fallback (not recommended)

Kill by process pattern:

```bash
pkill -f "hospital_federator_demo.py --config"
```

---

## GUI Notes

### Compose tab
- **Summary** is always sent if it exists
- **Original document** is only sent if the “Send original document” checkbox is ticked. This expressly permits sending of original, unanonymised, data
- If the original is sent, the summary will be sent alongside it, if it exists
- “Generate Fake Information” creates a document containing realistic test data (demographics, symptoms, diagnoses)
- "Generate Summary" summarises the original document, and should remove personally identifiable information via LMM (this will need some refinement)
- Deselect hospitals if you don't want to send them that particular document
- "Share data with other hospitals" will initiate the transfer to all selected hospitals

### Outbox / resend
- Shows the messages that hospital has sent
- "View deliveries" shows you the delivery state for each node attempted
- If any failed (for example network connectivity) you can click "Resend pending/failed" to have another go

### Received tab
- Inbox entries received will show here and will be visible via the JSON viewer
- Auto-refresh updates the list
- Messages received are persisted within a database for each simulated hospital

---

## Testing

Basic smoke tests are provided:

```bash
pytest
```

They cover:
- Config loading
- Database initialisation
- Inbox / outbox persistence
- Event shape sanity checks

Production environments will require a lot more testing.

---

## Troubleshooting

### Authentication failures / CN mismatch
- Ensure peer certificate CN exactly matches the peer ID in YAML
- Ensure the CA certificate is trusted by all peers
- Ensure the receiver is configured to require client certificates

### LLM not available
If the local model is missing:
- Structured fake data is still generated
- GP paragraph generation is skipped with a warning
