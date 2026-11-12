# Local speech recognition

Speech is optional and disabled by default (`SPEECH_ENABLED=False`). It uses the multilingual Whisper small q8_0 model through a separate CPU-only whisper.cpp worker. Ukrainian and English use the same inventory and automatic language detection. Transcripts preserve the spoken language; translation is a separate feature.

A private chat owner or authorized group administrator selects **Off**, **Manual only**, or **Manual + automatic** in `/settings`. The default is Off. `/transcribe` must reply to a voice or audio message in the same chat and exact topic. Automatic mode processes new voice/audio messages. The normal approval, permission, emergency-stop and usage policies apply to both triggers. Duplicate triggers for one source share a claim.

Inputs require positive duration and file-size metadata and are capped at 120 seconds and 10,000,000 compressed bytes. The worker also enforces decoded duration and streamed byte limits. HTTP body reads check the original deadline and current cancellation between available reads; initial connection, TLS and response-header operations retain the bounded socket timeouts and are not hard real-time interruptible. One source produces one text reply or a UTF-8 `transcript.txt` attachment. Text is capped at 32,000 characters, with an explicit truncation notice when needed. No speech consumes zero requests. An acknowledged transcript consumes one request; failed or cancelled work consumes zero. Ambiguous Telegram acceptance retains the claim and reservation for recovery and is never automatically resent.

## Prepare and enable

The qualified runtime requires Linux x86-64 with AVX2, FMA and F16C. The model is downloaded only by explicit preparation, never by a user request. The whisper.cpp v1.9.5 release and its archive SHA-256 are pinned in `Dockerfile`; model revision, size and SHA-256 are pinned in `infrastructure/speech/model_store.py`.

1. Build the optional image and populate its dedicated named volume with `make prepare-speech`. This does not start application services or enable speech. Use `COMPOSE_ENV=dev` and `APP_ENV_FILE=...` to select the intended stack as usual.
2. Keep `SPEECH_ENABLED=False` until the target-server release gate below passes.
3. Set `SPEECH_ENABLED=True` in the selected environment and run the normal build/start workflow. The operator script includes the speech profile and image only when enabled. A fixed service container name prevents scaling this deployment beyond one speech worker.
4. Worker startup verifies every pinned asset without loading the model. Missing or corrupt assets fail startup. To check manually, run the speech image's `/app/.venv/bin/python -m hovorunv2.bootstrap.speech_assets check` with the model volume mounted read-only at `/app/data/models/speech`.

The dedicated `speech_models` volume survives restarts. Normal workers mount it read-only at `/app/data/models/speech`. Explicit preparation mounts the same volume writable at `/speech-models` and passes `--model-dir /speech-models`; this separate target avoids inheriting the worker mount's read-only flag from Compose. Preparation installs complete verified artifacts atomically under a lock and retains MIT notices. No model weights are bundled in the image. There is no additional-model selection or first-use download path in this release; changing the inventory requires qualification and an intentional version change.

The service consumes only `work:{work}:speech`, with concurrency and prefetch both one, two CPUs, 2 GB memory, 128 PIDs, no core dumps, a read-only root filesystem, no additional capabilities and an init process for descendant reaping. The Python worker, decoder and native helper belong to the worker child. The helper uses two compute threads, anonymous pipes, a secret-free environment, network-denying seccomp, bounded IPC, and a fresh recognition state for every request. Native helper libraries are scoped to this image; no browser, GPU or translation runtime is installed.

The model loads into RAM on the first accepted request after cold start and remains warm between requests. `SPEECH_MODEL_IDLE_TIMEOUT` defaults to 300 seconds and must be positive. The helper exits after that interval without incoming work; active inference is not interrupted by its idle timer. Cancellation, revoked authority and deadlines stop and reap private subprocesses before zero settlement. Unproven cleanup retains execution ownership for recovery.

## Privacy and operations

Compressed audio, decoded PCM and transcript text remain in process memory and anonymous pipes. Queued file references and minimal source-routing metadata use the existing encrypted payload store and expiry. Persistent accounting and admission data contain operational identifiers and counts, not transcript text, audio or display names. The model volume contains only public assets and notices. There is no Premium or cloud recognition fallback. Telegram transports audio and the resulting reply as usual.

Container settings disable core dumps but cannot establish host swap or crash-capture policy. Verify encrypted swap or disabled swap and private-memory handling on the target host. Do not enable HTTP wire debugging or capture private payloads for capacity measurements.

To disable, turn off the two speech features through the existing emergency-stop controls, let active effects stop and pending claims settle or enter recovery, then set `SPEECH_ENABLED=False` and apply the normal stack workflow. This removes an old speech worker and prevents new speech admission. Do not delete Valkey claims or reservations to force a release after ambiguous delivery. Use the existing job recovery/operations workflow.

Before upgrading this implementation's private payload/helper protocol or replacing model assets, stop new speech admissions and drain accepted work on the old version. Do not run incompatible workers against the same tickets or overwrite a model while it is serving requests. Prepare and verify the new immutable inventory before restart.

## Production release gate

Local image and inference verification does **not** qualify the four-core/8 GB production host. Before enabling speech there, measure Ukrainian and English quality, worst-case 120-second latency within the original five-minute job deadline, peak memory, CPU, queueing, cancellation and shutdown while PostgreSQL, Valkey, bot, operations and media run together. Include browser, compression and translation when deployed. Record CPU instruction support, memory headroom, OOM events, host swap policy and supported coexistence. Lower the enabled workload set or keep speech disabled if that envelope fails. The user explicitly retained this server qualification as a release gate.

## Development verification

Prepare a model directory explicitly and compile the qualified helper (or use the built image). Set `HOVORUN_SPEECH_TEST_MODEL_DIR` and `HOVORUN_SPEECH_TEST_HELPER` for genuine inference tests. Missing test assets produce an explicit skip; no tests download a model automatically. Public speech fixtures and their attribution are listed in `THIRD_PARTY_NOTICES.md`.

Run `PYTHONPATH=src uv run pytest tests/application/speech tests/infrastructure/speech tests/interface/telegram/test_speech_command.py tests/interface/telegram/test_settings_speech.py tests/bootstrap/test_deployment_contracts.py tests/bootstrap/test_docker_stack.py`, followed by Ruff, ty, import-linter and the full suite. PostgreSQL and Valkey must be available for integration coverage.
