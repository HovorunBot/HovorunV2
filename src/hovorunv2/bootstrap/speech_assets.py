"""Prepare or verify the pinned multilingual speech inventory explicitly."""

import argparse
from pathlib import Path

from hovorunv2.infrastructure.speech.model_store import MODEL_DOWNLOAD_SECONDS, SpeechModelHTTPClient, SpeechModelStore


def main() -> None:
    """Prepare public assets or verify them offline without allocating the model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "check"))
    parser.add_argument("--model-dir", type=Path, default=Path("/app/data/models/speech"))
    arguments = parser.parse_args()
    with SpeechModelHTTPClient(total_timeout=MODEL_DOWNLOAD_SECONDS) as http:
        store = SpeechModelStore(arguments.model_dir, http)
        if arguments.operation == "prepare":
            store.ensure()
        else:
            store.require_ready()
    print("Speech assets verified.")  # noqa: T201


if __name__ == "__main__":
    main()
