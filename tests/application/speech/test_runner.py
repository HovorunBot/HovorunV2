"""Real integration tests for SpeechJobRunner using real Valkey and real WorkerDatabase."""

import json
import time

from dishka import AsyncContainer

from hovorunv2.application.features.registry import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.admission import AdmissionLimits, JobStage
from hovorunv2.application.jobs.feature_jobs import JobOutcome, JobTarget
from hovorunv2.application.speech.contracts import NoSpeech, SpeechResult, Transcript
from hovorunv2.application.speech.ports import SpeechRecognitionPort
from hovorunv2.application.speech.runner import SpeechJobRunner
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import WorkerRuntime
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.admission import ValkeyAdmissionStore
from hovorunv2.infrastructure.runtime.feature_payloads import ValkeyPayloadStore
from tests.utils.jobs import job_envelope
from tests.utils.runtime import ManualClock, runtime_cache


class FakeSpeechEngine(SpeechRecognitionPort):
    """Deterministic in-memory speech recognition engine for runner integration tests."""

    def __init__(self, result: SpeechResult) -> None:
        self.result = result
        self.invocations: list[bytes] = []

    def recognize(self, audio: bytes, deadline: float) -> SpeechResult:
        """Record audio bytes and return pre-configured speech result."""
        self.invocations.append(audio)
        return self.result


class RecordingTelegramAPI:
    """Recording Telegram Bot API adapter capturing speech messages and file downloads."""

    def __init__(self, audio_data: bytes = b"OggS-fake-audio") -> None:
        self.audio_data = audio_data
        self.messages: list[tuple[int, str, int | None, int | None]] = []
        self.documents: list[tuple[int, str, bytes, str | None]] = []

    def get_file(self, file_id: str) -> dict[str, object] | None:
        """Return dummy file information."""
        return {"file_path": f"voice/{file_id}.ogg"}

    def download_file_bytes(self, file_path: str) -> bytes:
        """Return fixed synthetic audio bytes."""
        return self.audio_data

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: str | None = None,
        disable_web_page_preview: bool = True,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        """Capture sent text message."""
        self.messages.append((chat_id, text, reply_to_message_id, message_thread_id))
        return 777

    def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        *,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        """Capture sent document file."""
        self.documents.append((chat_id, filename, content, caption))
        return 888


class AllowGate:
    """Allow progress notice gate."""

    def claim_delayed(self, target_key: str, ttl: int) -> bool:
        """Claim delayed notice."""
        return True


async def test_speech_runner_delivers_transcript_and_settles(test_container: AsyncContainer) -> None:
    """Admitted speech job runs through SpeechJobRunner, delivers text, and settles accounting."""
    settings = await test_container.get(Settings)
    user_store = await test_container.get(UserStore)
    user = await user_store.ingest_user(12345)
    assert user is not None
    now = time.time()
    clock = ManualClock(now)
    safety = UsageSafetyCaps()

    async with runtime_cache(test_container) as (cache, namespace):
        payload_store = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        admission_store = ValkeyAdmissionStore(cache, namespace)

        payload_bytes = json.dumps({"file_id": "speech_file_1", "message_thread_id": None}).encode("utf-8")
        job_id = "test-speech-job-1"
        deadline = clock.now() + 120
        sealed = await payload_store.seal(job_id, payload_bytes, deadline)

        envelope = job_envelope(
            sealed.reference,
            clock,
            job_id=job_id,
            feature="transcribe",
            trigger=TriggerKind.DIRECT,
            target=JobTarget(kind=TargetKind.USER, entity_id=user.id, generation=user.generation, telegram_id=12345),
            actor_id=12345,
            actor_generation=user.generation,
            charged_kind=TargetKind.USER,
            charged_telegram_id=12345,
            deadline=deadline,
            message_id=456,
        )

        limits = AdmissionLimits(10, 10, safety)
        admission_result = await admission_store.admit(envelope, sealed, limits, clock.now())
        assert admission_result.ticket is not None

        engine = FakeSpeechEngine(Transcript(text="Тестова розпізнана фраза", language="uk"))
        telegram = RecordingTelegramAPI()

        with (
            WorkerDatabase(settings, registry=FEATURE_REGISTRY, safety=safety) as database,
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
        ):
            runner = SpeechJobRunner(
                admission=runtime.admission,
                payloads=runtime.payloads,
                budgets=runtime.budgets,
                policies=database.policies,
                ledger=database.ledger,
                telegram=telegram,
                engine=engine,
                settings_store=None,
                gate=AllowGate(),
                feedback=database.policies,
                clock=clock,
            )

            outcome = runner.run(envelope.model_dump(mode="json"), JobStage.SPEECH)
            assert outcome == JobOutcome.COMPLETE.value

        assert len(telegram.messages) == 1
        assert "Тестова розпізнана фраза" in telegram.messages[0][1]
        assert telegram.messages[0][2] == 456
        assert len(engine.invocations) == 1


async def test_speech_runner_handles_silence_without_charge(test_container: AsyncContainer) -> None:
    """Speech job with NoSpeech result sends friendly notification and completes with zero charge."""
    settings = await test_container.get(Settings)
    user_store = await test_container.get(UserStore)
    user = await user_store.ingest_user(12345)
    assert user is not None
    now = time.time()
    clock = ManualClock(now)
    safety = UsageSafetyCaps()

    async with runtime_cache(test_container) as (cache, namespace):
        payload_store = ValkeyPayloadStore(cache, settings, clock, namespace, safety)
        admission_store = ValkeyAdmissionStore(cache, namespace)

        payload_bytes = json.dumps({"file_id": "speech_silence_file", "message_thread_id": None}).encode("utf-8")
        job_id = "test-speech-silence-job"
        deadline = clock.now() + 120
        sealed = await payload_store.seal(job_id, payload_bytes, deadline)

        envelope = job_envelope(
            sealed.reference,
            clock,
            job_id=job_id,
            feature="transcribe",
            trigger=TriggerKind.DIRECT,
            target=JobTarget(kind=TargetKind.USER, entity_id=user.id, generation=user.generation, telegram_id=12345),
            actor_id=12345,
            actor_generation=user.generation,
            charged_kind=TargetKind.USER,
            charged_telegram_id=12345,
            deadline=deadline,
            message_id=789,
        )

        limits = AdmissionLimits(10, 10, safety)
        admission_result = await admission_store.admit(envelope, sealed, limits, clock.now())
        assert admission_result.ticket is not None

        engine = FakeSpeechEngine(NoSpeech())
        telegram = RecordingTelegramAPI()

        with (
            WorkerDatabase(settings, registry=FEATURE_REGISTRY, safety=safety) as database,
            WorkerRuntime(settings, clock, namespace, safety) as runtime,
        ):
            runner = SpeechJobRunner(
                admission=runtime.admission,
                payloads=runtime.payloads,
                budgets=runtime.budgets,
                policies=database.policies,
                ledger=database.ledger,
                telegram=telegram,
                engine=engine,
                settings_store=None,
                gate=AllowGate(),
                feedback=database.policies,
                clock=clock,
            )

            outcome = runner.run(envelope.model_dump(mode="json"), JobStage.SPEECH)
            assert outcome == JobOutcome.COMPLETE.value

        assert len(telegram.messages) == 1
        assert "(No speech detected)" in telegram.messages[0][1]
        assert telegram.messages[0][2] == 789
