#!/usr/bin/env python3
"""Hospital Federator Demo entrypoint.

This file is intentionally thin.

Run examples:
  python hospital_federator_demo.py --config peers.yaml --peer-id peer-a
  python hospital_federator_demo.py --config peers.yaml --peer-id peer-a --listen-port 8443
"""

from hospital_federator.cli import main


if __name__ == "__main__":
    main()
