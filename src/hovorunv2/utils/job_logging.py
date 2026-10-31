"""Transient operational job context shared by worker logging across application layers."""

from contextvars import ContextVar, Token

current_job_id: ContextVar[str] = ContextVar("worker_job_id", default="-")
current_job_stage: ContextVar[str] = ContextVar("worker_job_stage", default="-")


def set_job_log_context(job_id: str, stage: str) -> tuple[Token[str], Token[str]]:
    """Bind a validated envelope's operational identity for this execution."""
    return current_job_id.set(job_id), current_job_stage.set(stage)


def reset_job_log_context(tokens: tuple[Token[str], Token[str]]) -> None:
    """Restore the previous context so the next task cannot inherit this identity."""
    current_job_id.reset(tokens[0])
    current_job_stage.reset(tokens[1])
