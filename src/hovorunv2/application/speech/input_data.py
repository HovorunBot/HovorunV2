"""Validate the minimum encrypted source metadata owned by one speech request."""

from pydantic import BaseModel, ConfigDict, Field

from hovorunv2.application.speech.policies import MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS


class SpeechInput(BaseModel):
    """Own minimum sealed source metadata without exposing the private file reference."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    file_id: str = Field(min_length=1, max_length=2048, repr=False)
    message_thread_id: int | None = Field(default=None, gt=0)
    source_chat_id: int
    source_message_id: int = Field(gt=0)
    claimed_bytes: int = Field(gt=0, le=MAX_AUDIO_BYTES)
    claimed_seconds: int = Field(gt=0, le=MAX_AUDIO_SECONDS)
