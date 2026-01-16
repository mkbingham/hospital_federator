#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Author: Mark Bingham
#
# This script:
#   1) Creates a CA, then creates keys/certs for each peer up to PEER_COUNT
#   2) Generates per-peer YAML config files by templating a base YAML
#
# YAML generation:
#   - Uses BASE_YAML_TEMPLATE as the source document
#   - Writes one YAML per peer: configs/peer<i>.yaml
#
# NOTE: Fo NOT distribute private keys or place real config in source control.
#
# DO NOT USE IN PRODUCTION ENVIRONMENTS - Seek appropriate security advice.
# ============================================================================



# ============================================================
# Configuration
# ============================================================

# Number of peers
PEER_COUNT=3

# Base directory for cert output
BASE_DIR="$(pwd)/certs"

# Where to write generated YAMLs
CONFIG_DIR="$(pwd)/configs"

# Base YAML template (input). This will be read and then modified per-peer
BASE_YAML_TEMPLATE="$(pwd)/templates/peer1.yaml"

# Host addressing (adjust this in the output YAML if you are running instances across multiple machines)
HOST="127.0.0.1"
PORT_BASE=8000   # peer1 -> 8000, peer2 -> 8001, ...


# CA settings
CA_NAME="HospitalFederator-CA"
CA_KEY_BITS=4096
CA_DAYS=3650

# Peer cert settings
PEER_KEY_BITS=2048
PEER_DAYS=825

# Subject defaults
COUNTRY="GB"
ORG="HospitalFederator"

# SAN defaults (adjust if needed)
SAN_DNS="localhost"
SAN_IP="127.0.0.1"

# ============================================================
# Setup
# ============================================================

echo "Creating certificate directory structure..."
mkdir -p "$BASE_DIR/ca"
mkdir -p "$CONFIG_DIR"

for i in $(seq 1 "$PEER_COUNT"); do
  mkdir -p "$BASE_DIR/peer$i"
done

# ============================================================
# Create CA
# ============================================================

echo "Generating CA private key..."
openssl genrsa -out "$BASE_DIR/ca/ca.key" "$CA_KEY_BITS"

echo "Generating CA certificate..."
openssl req -x509 -new -nodes   -key "$BASE_DIR/ca/ca.key"   -sha256   -days "$CA_DAYS"   -out "$BASE_DIR/ca/ca.crt"   -subj "/C=$COUNTRY/O=$ORG/CN=$CA_NAME"

echo "CA created:"
echo "  - $BASE_DIR/ca/ca.key"
echo "  - $BASE_DIR/ca/ca.crt"
echo

# ============================================================
# Create peers
# ============================================================

for i in $(seq 1 "$PEER_COUNT"); do
  PEER_ID="peer$i"
  PEER_DIR="$BASE_DIR/$PEER_ID"

  echo "------------------------------------------------------------"
  echo "Generating certs for $PEER_ID"
  echo "------------------------------------------------------------"

  # Private key
  openssl genrsa -out "$PEER_DIR/$PEER_ID.key" "$PEER_KEY_BITS"

  # CSR
  openssl req -new     -key "$PEER_DIR/$PEER_ID.key"     -out "$PEER_DIR/$PEER_ID.csr"     -subj "/C=$COUNTRY/O=$ORG/CN=$PEER_ID"

  # Extensions file
  cat > "$PEER_DIR/$PEER_ID.ext" <<EOF
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth, serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $SAN_DNS
IP.1 = $SAN_IP
EOF

  # Sign cert
  openssl x509 -req     -in "$PEER_DIR/$PEER_ID.csr"     -CA "$BASE_DIR/ca/ca.crt"     -CAkey "$BASE_DIR/ca/ca.key"     -CAcreateserial     -out "$PEER_DIR/$PEER_ID.crt"     -days "$PEER_DAYS"     -sha256     -extfile "$PEER_DIR/$PEER_ID.ext"

  # Verify
  openssl verify -CAfile "$BASE_DIR/ca/ca.crt" "$PEER_DIR/$PEER_ID.crt"

  # Clean up CSR + ext (optional but tidy)
  rm -f "$PEER_DIR/$PEER_ID.csr" "$PEER_DIR/$PEER_ID.ext"

  echo "Generated for $PEER_ID:"
  echo "  - $PEER_DIR/$PEER_ID.key"
  echo "  - $PEER_DIR/$PEER_ID.crt"
  echo
done

# ============================================================
# Generate per-peer YAML configs
# ============================================================

if [[ ! -f "$BASE_YAML_TEMPLATE" ]]; then
  echo "ERROR: Base YAML template not found: $BASE_YAML_TEMPLATE"
  exit 1
fi

echo "Generating per-peer YAML configs from: $BASE_YAML_TEMPLATE"
echo "Output directory: $CONFIG_DIR"
echo

# Export vars so the embedded Python can read them
export PEER_COUNT
export BASE_DIR
export CONFIG_DIR
export HOST
export PORT_BASE
export BASE_YAML_TEMPLATE

python3 - <<'PY'
import os, copy
import yaml

peer_count = int(os.environ.get("PEER_COUNT", "5"))
base_dir = os.environ["BASE_DIR"]
config_dir = os.environ["CONFIG_DIR"]
host = os.environ.get("HOST", "127.0.0.1")
port_base = int(os.environ.get("PORT_BASE", "8000"))
template_path = os.environ["BASE_YAML_TEMPLATE"]

with open(template_path, "r", encoding="utf-8") as f:
    base = yaml.safe_load(f)

def ensure(d, k):
    if k not in d or d[k] is None:
        d[k] = {}
    return d[k]

ca_crt = os.path.join(base_dir, "ca", "ca.crt")

for i in range(1, peer_count + 1):
    peer_id = f"peer{i}"
    cert = os.path.join(base_dir, peer_id, f"{peer_id}.crt")
    key = os.path.join(base_dir, peer_id, f"{peer_id}.key")

    cfg = copy.deepcopy(base)

    # self.peer_id
    self_sec = ensure(cfg, "self")
    self_sec["peer_id"] = peer_id

    # tls_defaults.verify -> CA bundle, plus outgoing client identity (for requests)
    tls_def = ensure(cfg, "tls_defaults")
    tls_def["verify"] = ca_crt
    tls_def["client_cert"] = cert
    tls_def["client_key"] = key

    # Replace peers list so IDs match cert CNs, and ports match too
    peers = []
    for j in range(1, peer_count + 1):
        pid = f"peer{j}"
        pport = port_base + (j - 1)
        p = {
            "id": pid,
            "name": f"Hospital {j}",
            "url": f"https://{host}:{pport}",
        }

        if pid == peer_id:
            p["tls"] = {
                "client_cert": cert,
                "client_key": key,
            }
        peers.append(p)
    cfg["peers"] = peers

    out_path = os.path.join(config_dir, f"{peer_id}.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)

print(f"Created {peer_count} configs in {config_dir}")
print("Example run:")
print("  python hospital_federator_demo.py --config configs/peer1.yaml --peer-id peer1 --listen-port 8000")
PY

echo
echo "============================================================"
echo "All certificates + YAML configs generated successfully."
echo
echo "Generated YAMLs:"
for i in $(seq 1 "$PEER_COUNT"); do
  echo "  - $CONFIG_DIR/peer$i.yaml"
done
echo
echo "Notes:"
echo "  - tls_defaults.verify is set to the generated CA cert:"
echo "      $BASE_DIR/ca/ca.crt"
echo "  - Peer URLs are set to https://$HOST:PORT_BASE+(i-1)"
echo "============================================================"
