"""Verify deferred model allocation, native inference, and process ownership."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hovorunv2.application.translation.ports import TranslationError
from hovorunv2.infrastructure.translation.ctranslate2_engine import CTranslate2Engine
from hovorunv2.infrastructure.translation.model_store import ModelHTTPClient, ModelStore
from tests.utils.sync_http import HTTPFixture


@pytest.mark.unit
def test_construction_and_invalid_input_do_not_load_native_libraries() -> None:
    """Constructing or rejecting a request never imports or provisions the neural model."""
    script = """
import sys
from pathlib import Path
from hovorunv2.infrastructure.translation.ctranslate2_engine import CTranslate2Engine
from hovorunv2.infrastructure.translation.model_store import ModelStore, ModelHTTPClient
with ModelHTTPClient() as http:
    engine = CTranslate2Engine(ModelStore(Path('/unused/model'), http))
    try:
        engine.translate('Bonjour', 'xx', 'uk')
    except ValueError:
        pass
    else:
        raise AssertionError('unsupported language accepted')
    engine.close()
assert 'ctranslate2' not in sys.modules
assert 'sentencepiece' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-W", "error", "-c", script], check=False)  # noqa: S603
    assert result.returncode == 0


@pytest.mark.subprocess
def test_engine_rejects_forked_process_and_closed_use(tmp_path: Path) -> None:
    """Inherited engines cannot translate or unload their parent's resources."""
    script = """
import os
import sys
from pathlib import Path
from hovorunv2.infrastructure.translation.ctranslate2_engine import CTranslate2Engine
from hovorunv2.infrastructure.translation.model_store import ModelHTTPClient, ModelStore
with ModelHTTPClient() as http:
    engine = CTranslate2Engine(ModelStore(Path(sys.argv[1]), http))
    child = os.fork()
    if child == 0:
        for operation in (lambda: engine.translate('Bonjour', 'fr', 'uk'), engine.close):
            try:
                operation()
            except ValueError:
                continue
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    engine.close()
    engine.close()
    try:
        engine.translate('Bonjour', 'fr', 'uk')
    except ValueError:
        pass
    else:
        raise AssertionError('closed engine accepted inference')
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-W", "error", "-c", script, str(tmp_path / "model")], check=False
    )
    assert result.returncode == 0


def test_unavailable_model_raises_safe_failure_and_cleans_partial_files(tmp_path: Path) -> None:
    """Provisioning failure contains no caption text and leaves no installed partial model."""
    caption = "Bonjour, voici une photographie de notre voyage."
    with (
        HTTPFixture({}) as server,
        ModelHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        engine = CTranslate2Engine(ModelStore(tmp_path / "model", http, base_url=server.url("")))
        try:
            with pytest.raises(TranslationError) as failure:
                engine.translate(caption, "fr", "uk")
            assert caption not in str(failure.value)
            assert not list(tmp_path.rglob("*.part"))
        finally:
            engine.close()


@pytest.mark.slow
def test_real_nllb_translation_preserves_paragraphs() -> None:
    """Run actual CPU inference when the explicitly provisioned test model is available."""
    directory = os.environ.get("HOVORUN_TRANSLATION_TEST_MODEL_DIR")
    if directory is None:
        pytest.skip("Set HOVORUN_TRANSLATION_TEST_MODEL_DIR to run qualified model inference")
    with ModelHTTPClient() as http:
        engine = CTranslate2Engine(ModelStore(Path(directory), http))
        try:
            translated = engine.translate(
                "Bonjour, voici une photographie de notre voyage.\n\nLa nature est très belle.\n", "fr", "uk"
            )
            assert translated.endswith("\n")
            lines = translated.split("\n")
            assert len(lines) == 4
            assert lines[1] == lines[3] == ""
            assert any("\u0430" <= character.lower() <= "\u044f" for character in lines[0])
            assert lines[2].strip()
        finally:
            engine.close()


@pytest.mark.slow
def test_incomplete_translation_is_rejected_and_engine_remains_usable() -> None:
    """Reject a capped hypothesis instead of presenting a silently incomplete translation."""
    directory = os.environ.get("HOVORUN_TRANSLATION_TEST_MODEL_DIR")
    if directory is None:
        pytest.skip("Set HOVORUN_TRANSLATION_TEST_MODEL_DIR to run qualified model inference")
    caption = " ".join(
        f"Le voyage numéro {index} commence demain et nous allons visiter la montagne." for index in range(1, 30)
    )
    with ModelHTTPClient() as http:
        engine = CTranslate2Engine(ModelStore(Path(directory), http))
        try:
            with pytest.raises(TranslationError):
                engine.translate(caption, "fr", "uk")
            translated = engine.translate("Bonjour, voici une photographie de notre voyage.", "fr", "uk")
            assert translated.strip()
            assert "</s>" not in translated
        finally:
            engine.close()
