"""Record external Docker CLI calls for disposable deployment-script scenarios."""

import json
import os
import signal
import sys
from pathlib import Path


def _handle_image_command(arguments: list[str]) -> bool:
    """Respond to image inspection and listing operations.

    Args:
        arguments: Executable command-line arguments.

    Returns:
        True if the image command was handled, False otherwise.
    """
    if len(arguments) < 2:
        return False
    if arguments[1] == "ls":
        content = (
            os.environ.get("DOCKER_TEST_LABELLED", "")
            if "--quiet" in arguments
            else os.environ.get("DOCKER_TEST_IMAGES", "")
        )
        sys.stdout.write(content)
        return True
    if arguments[1] == "inspect":
        existing = os.environ.get("DOCKER_TEST_EXISTING_IMAGES", "").splitlines()
        target = arguments[-1]
        if target not in existing:
            sys.exit(1)
        sys.stdout.write(json.dumps([{"Id": f"sha256:{target}"}]) + "\n")
        return True
    return False


def _handle_buildx_command(arguments: list[str]) -> bool:
    """Respond to buildx builder management and image build steps.

    Args:
        arguments: Executable command-line arguments.

    Returns:
        True if the buildx command was handled, False otherwise.
    """
    if len(arguments) < 2:
        return False
    if arguments[1] == "ls":
        sys.stdout.write(os.environ.get("DOCKER_TEST_BUILDERS", ""))
        return True
    if arguments[1] == "build":
        if os.environ.get("DOCKER_TEST_TERMINATE"):
            os.kill(os.getppid(), signal.SIGTERM)
        if os.environ.get("DOCKER_TEST_FAIL"):
            sys.exit(7)
        return True
    return False


def main() -> None:
    """Serve synthetic Docker responses without accessing a daemon or real credentials."""
    arguments = sys.argv[1:]
    with Path(os.environ["DOCKER_TEST_LOG"]).open("a") as output:
        output.write(json.dumps(arguments) + "\n")
    if "config" in arguments and "--format" in arguments:
        sys.stdout.write(os.environ["DOCKER_TEST_CONFIG"])
    elif "up" in arguments and os.environ.get("DOCKER_TEST_UP_FAIL"):
        sys.exit(8)
    elif (arguments[:1] == ["image"] and _handle_image_command(arguments)) or (
        arguments[:1] == ["buildx"] and _handle_buildx_command(arguments)
    ):
        return
    elif arguments[:2] == ["container", "ls"] and arguments[-1] == "ancestor=sha256:running":
        sys.stdout.write("running-container\n")


if __name__ == "__main__":
    main()
