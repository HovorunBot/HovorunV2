"""Record external Docker CLI calls for disposable deployment-script scenarios."""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    """Serve synthetic Docker responses without accessing a daemon or real credentials."""
    arguments = sys.argv[1:]
    with Path(os.environ["DOCKER_TEST_LOG"]).open("a") as output:
        output.write(json.dumps(arguments) + "\n")
    if "config" in arguments and "--format" in arguments:
        sys.stdout.write(os.environ["DOCKER_TEST_CONFIG"])
    elif arguments[:2] == ["image", "ls"]:
        if "--quiet" in arguments:
            sys.stdout.write(os.environ.get("DOCKER_TEST_LABELLED", ""))
        else:
            sys.stdout.write(os.environ.get("DOCKER_TEST_IMAGES", ""))
    elif arguments[:2] == ["container", "ls"]:
        if arguments[-1] == "ancestor=sha256:running":
            sys.stdout.write("running-container\n")
    elif arguments[:2] == ["buildx", "build"] and os.environ.get("DOCKER_TEST_FAIL"):
        sys.exit(7)


if __name__ == "__main__":
    main()
