"""Set a parent-death signal before replacing this process with Chromium."""

import ctypes
import signal
import sys
from os import execv

PR_SET_PDEATHSIG = 1


def main() -> None:
    """Terminate the browser when its owning worker child disappears, then exec Chromium."""
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    executable = sys.argv[1]
    execv(executable, sys.argv[1:])  # noqa: S606  # Exec a validated browser path without an intermediate shell.


if __name__ == "__main__":
    main()
