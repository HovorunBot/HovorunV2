"""Initiative lifecycle contracts through the real filesystem and CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ai_scheme.py"


@pytest.fixture
def initiative(tmp_path: Path) -> Path:
    """Build a minimal completed initiative with real evidence files."""
    root = tmp_path / "feature"
    (root / "blocks").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "blocks/B01.md").write_text("# B01\n\nStatus: DONE\n")
    (root / "reports/B01.md").write_text("Verified outcome.\n")
    (root / "STATUS.md").write_text("| [B01](blocks/B01.md) | DONE |\n")
    manifest = {"schema_version": 1, "blocks": [{"id": "B01", "file": "blocks/B01.md", "status": "DONE"}]}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the actual tooling without application infrastructure."""
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def close_manifest(root: Path) -> None:
    """Supply concrete adoption and closure evidence in the test initiative."""
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["lifecycle"] = "CLOSED"
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "ADOPTION.md").write_text("No lasting rules: synthetic CLI test only.\n")
    (root / "CLOSURE.md").write_text("Acceptance: reports/B01.md; no open findings.\n")


def test_legacy_lifecycle_defaults_to_active(initiative: Path) -> None:
    """Existing manifests remain valid without lifecycle metadata."""
    result = run_cli("validate", str(initiative))
    assert result.returncode == 0, result.stderr


def test_closed_initiative_validates_and_has_no_next_block(initiative: Path) -> None:
    """Closed work validates explicitly but cannot schedule implementation."""
    close_manifest(initiative)
    assert run_cli("validate", str(initiative)).returncode == 0
    result = run_cli("next", str(initiative))
    assert result.returncode == 3
    assert "CLOSED" in result.stdout
    result = run_cli("prompt", str(initiative), "B01", "--role", "implement")
    assert result.returncode == 1
    assert "historical review only" in result.stderr
    assert run_cli("prompt", str(initiative), "B01", "--role", "review").returncode == 0


@pytest.mark.parametrize("missing", ["ADOPTION.md", "CLOSURE.md", "reports/B01.md"])
def test_closure_requires_evidence(initiative: Path, missing: str) -> None:
    """Missing durable adoption, acceptance or implementation evidence blocks closure."""
    close_manifest(initiative)
    (initiative / missing).unlink()
    assert run_cli("validate", str(initiative)).returncode == 1


def test_closed_work_cannot_have_pending_blocks(initiative: Path) -> None:
    """A false closure declaration cannot conceal unfinished work."""
    close_manifest(initiative)
    manifest = json.loads((initiative / "manifest.json").read_text())
    manifest["blocks"][0]["status"] = "TODO"
    (initiative / "manifest.json").write_text(json.dumps(manifest))
    result = run_cli("validate", str(initiative))
    assert result.returncode == 1
    assert "all blocks DONE" in result.stderr


def test_unknown_lifecycle_rejected(initiative: Path) -> None:
    """Misspelled lifecycle values cannot silently resume an initiative."""
    manifest = json.loads((initiative / "manifest.json").read_text())
    manifest["lifecycle"] = "FINISHED"
    (initiative / "manifest.json").write_text(json.dumps(manifest))
    assert run_cli("validate", str(initiative)).returncode == 1
