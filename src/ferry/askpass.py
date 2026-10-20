"""Minimal SSH_ASKPASS helper.

OpenSSH executes this program and reads the password from stdout. The secret is
passed transiently through the environment and never written to disk.
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    sys.stdout.write(os.environ.get("FERRY_ASKPASS_SECRET", "") + "\n")


if __name__ == "__main__":
    main()
