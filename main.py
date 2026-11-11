"""Uruchom aplikację PulsePC na Windows 10/11."""

import os
import sys


def main() -> None:
    if os.name != "nt":
        print("PulsePC obsługuje Windows 10 i Windows 11.", file=sys.stderr)
        sys.exit(1)
    try:
        from pulsepc.ui import PulsePC
        PulsePC().run()
    except ImportError as exc:
        print(f"Brak zależności: {exc}. Uruchom: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
