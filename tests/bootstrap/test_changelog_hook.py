"""Exercise the changelog amendment hook against isolated real Git repositories."""

import asyncio
import os
import shlex
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "update-changelog-after-commit.sh"

pytestmark = [pytest.mark.subprocess]


async def run_command(repo: Path, *args: str) -> tuple[int, str]:
    """Run an external command with isolated Git configuration."""
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=repo,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_") or key in {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"}
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode is not None
    return process.returncode, output.decode()


async def git(repo: Path, *args: str) -> str:
    """Run a successful command against the test repository."""
    code, output = await run_command(repo, "git", *args)
    assert code == 0, output
    return output.strip()


@pytest.fixture
async def hook_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real repository and a deterministic external Commitizen stand-in."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    tool = tool_dir / "uv"
    tool.write_text(
        "#!/bin/sh\nset -eu\n[ ! -f .generator-fails ] || exit 9\n"
        'case "$5" in *.md) ;; *) exit 29 ;; esac\ncp .generated "$5"\n',
    )
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tool_dir}:{os.environ['PATH']}")
    repo = tmp_path / "repo"
    repo.mkdir()
    await git(repo, "init", "--quiet")
    await git(repo, "config", "user.name", "Hook Test")
    await git(repo, "config", "user.email", "hook@example.invalid")
    await git(repo, "config", "commit.gpgsign", "false")
    (repo / "CHANGELOG.md").write_text("Old changelog\n")
    (repo / ".generated").write_text("Generated changelog\n")
    await git(repo, "add", "CHANGELOG.md")
    await git(repo, "commit", "--quiet", "-m", "chore: initialize hook fixture")
    return repo


async def test_post_commit_amends_only_changelog(hook_repo: Path) -> None:
    """Preserve staged work and avoid recursive execution on a generated amendment."""
    hook = hook_repo / ".git" / "hooks" / "post-commit"
    hook.write_text(f"#!/bin/sh\nprintf 'run\\n' >> .hook-runs\nexec sh {shlex.quote(str(HOOK))}\n")
    hook.chmod(0o755)
    (hook_repo / "pending.txt").write_text("Unrelated staged work\n")
    await git(hook_repo, "add", "pending.txt")
    await git(hook_repo, "commit", "--allow-empty", "--only", "-m", "feat(hooks): verify amendment")

    assert await git(hook_repo, "log", "-1", "--format=%s") == "feat(hooks): verify amendment"
    assert await git(hook_repo, "show", "HEAD:CHANGELOG.md") == "Generated changelog"
    assert await git(hook_repo, "diff", "--cached", "--name-only") == "pending.txt"
    assert (hook_repo / ".hook-runs").read_text() == "run\n"
    assert await git(hook_repo, "rev-list", "--count", "HEAD") == "2"


@pytest.mark.parametrize("scenario", ["unchanged", "dirty", "staged", "failure", "tagged"])
async def test_hook_preserves_head_when_no_safe_update(hook_repo: Path, scenario: str) -> None:
    """Leave commits and user edits intact on no-op or unsafe generation paths."""
    if scenario == "unchanged":
        (hook_repo / ".generated").write_text("Old changelog\n")
    elif scenario in {"dirty", "staged"}:
        (hook_repo / "CHANGELOG.md").write_text("Manual changelog edits\n")
        if scenario == "staged":
            await git(hook_repo, "add", "CHANGELOG.md")
    elif scenario == "failure":
        (hook_repo / ".generator-fails").touch()
    else:
        await git(hook_repo, "tag", "v0.0.1")
    original_head = await git(hook_repo, "rev-parse", "HEAD")
    original_contents = (hook_repo / "CHANGELOG.md").read_text()
    original_index = await git(hook_repo, "diff", "--cached")

    code, output = await run_command(hook_repo, "sh", str(HOOK))

    assert (code == 0) == (scenario == "unchanged"), output
    assert await git(hook_repo, "rev-parse", "HEAD") == original_head
    assert (hook_repo / "CHANGELOG.md").read_text() == original_contents
    assert await git(hook_repo, "diff", "--cached") == original_index
