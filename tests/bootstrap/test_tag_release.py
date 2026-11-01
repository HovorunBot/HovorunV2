"""Verify release commits and annotated tags with real Commitizen in isolated Git repositories."""

import asyncio
import os
import shlex
import sys
import tomllib
from pathlib import Path

import pytest
import tomlkit

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.unit, pytest.mark.subprocess]
EXPECTED_VERSION = "0.3.2"


async def _command(repo: Path, *args: str) -> tuple[int, str]:
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


async def _git(repo: Path, *args: str) -> str:
    code, output = await _command(repo, "git", *args)
    assert code == 0, output
    return output.strip()


@pytest.fixture
async def release_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run real Commitizen without resolving the fixture project's synthetic dependencies."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    tool = tool_dir / "uv"
    tool.write_text(
        '#!/bin/sh\nset -eu\n[ "$1" = run ]\nshift\n'
        '[ "${1:-}" != --no-sync ] || shift\n[ "$1" = cz ]\nshift\n'
        f'exec {shlex.quote(sys.executable)} -m commitizen "$@"\n'
    )
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tool_dir}:{os.environ['PATH']}")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "scripts").mkdir()
    for name in ("update-changelog-after-commit.sh", "tag-release.sh"):
        source = ROOT / "scripts" / name
        if source.exists():
            (repo / "scripts" / name).write_text(source.read_text())
    (repo / "Makefile").write_text((ROOT / "Makefile").read_text())
    document = tomlkit.parse((ROOT / "pyproject.toml").read_text())
    document["project"]["version"] = "0.3.1"
    (repo / "pyproject.toml").write_text(tomlkit.dumps(document))
    (repo / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "hovorunv2"\nversion = "0.3.1"\n'
        'source = { editable = "." }\n\n[[package]]\nname = "unrelated"\nversion = "0.3.1"\n'
    )
    (repo / "CHANGELOG.md").write_text("## v0.3.1\n\n### Fix\n\n- Previous release.\n")
    (repo / "CHANGES.md").write_text("## 0.3.2\n\nConcise client notes.\n")
    (repo / "pending.txt").write_text("Original contents\n")
    await _git(repo, "init", "--quiet")
    await _git(repo, "config", "user.name", "Release Test")
    await _git(repo, "config", "user.email", "release@example.invalid")
    await _git(repo, "config", "commit.gpgsign", "false")
    await _git(repo, "add", ".")
    await _git(repo, "commit", "--quiet", "-m", "chore: initialize release fixture")
    await _git(repo, "tag", "-a", "v0.3.1", "-m", "Previous release")
    (repo / "change.txt").write_text("Synthetic fix\n")
    await _git(repo, "add", "change.txt")
    await _git(repo, "commit", "--quiet", "-m", "fix(media): keep requests moving")
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\nexec sh scripts/update-changelog-after-commit.sh\n")
    hook.chmod(0o755)
    return repo


@pytest.mark.parametrize("arguments", [("VERSION=v0.3.2",), ("TAG=v0.3.2",), ("INCREMENT=patch",), ()])
async def test_tag_release_commits_all_release_files_before_tagging(
    release_repo: Path, arguments: tuple[str, ...]
) -> None:
    """The tagged commit contains matching project and lock versions plus the release changelog."""
    previous = await _git(release_repo, "rev-parse", "HEAD")
    code, output = await _command(release_repo, "make", "tag-release", *arguments)
    assert code == 0, output
    assert await _git(release_repo, "status", "--porcelain") == ""
    assert await _git(release_repo, "rev-parse", "HEAD^") == previous
    assert await _git(release_repo, "rev-parse", "v0.3.2^{commit}") == await _git(release_repo, "rev-parse", "HEAD")
    assert await _git(release_repo, "cat-file", "-t", "v0.3.2") == "tag"
    project = tomllib.loads(await _git(release_repo, "show", "v0.3.2:pyproject.toml"))
    lock = tomllib.loads(await _git(release_repo, "show", "v0.3.2:uv.lock"))
    assert project["project"]["version"] == EXPECTED_VERSION
    assert {package["name"]: package["version"] for package in lock["package"]} == {
        "hovorunv2": EXPECTED_VERSION,
        "unrelated": "0.3.1",
    }
    changelog = await _git(release_repo, "show", "v0.3.2:CHANGELOG.md")
    assert "## v0.3.2" in changelog
    assert "keep requests moving" in changelog
    assert "Unreleased" not in changelog
    assert set((await _git(release_repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")).splitlines()) == {
        "pyproject.toml",
        "uv.lock",
        "CHANGELOG.md",
    }


@pytest.mark.parametrize("staged", [False, True])
async def test_tag_release_rejects_unrelated_work_before_modifying_files(release_repo: Path, *, staged: bool) -> None:
    """A release cannot sweep pending application work into Commitizen's commit-all operation."""
    previous = await _git(release_repo, "rev-parse", "HEAD")
    (release_repo / "pending.txt").write_text("Unrelated pending changes\n")
    if staged:
        await _git(release_repo, "add", "pending.txt")
    original_index = await _git(release_repo, "diff", "--cached")
    code, output = await _command(release_repo, "make", "tag-release", "VERSION=v0.3.2")
    assert code != 0
    assert "clean workspace" in output
    assert await _git(release_repo, "rev-parse", "HEAD") == previous
    assert await _git(release_repo, "diff", "--cached") == original_index
    assert await _git(release_repo, "tag", "--list", "v0.3.2") == ""
    assert tomllib.loads((release_repo / "pyproject.toml").read_text())["project"]["version"] == "0.3.1"
    assert (release_repo / "pending.txt").read_text() == "Unrelated pending changes\n"


async def test_release_tag_message_is_preserved_as_literal_text(release_repo: Path) -> None:
    """Quotes, newlines and shell syntax remain annotation text and never execute."""
    message = 'Combined fixes: "quoted"\n$(touch release-substitution) and `touch release-backticks`'
    code, output = await _command(release_repo, "make", "release-tag", "VERSION=v0.3.2", "MESSAGE=" + message)
    assert code == 0, output
    assert await _git(release_repo, "for-each-ref", "--format=%(contents)", "refs/tags/v0.3.2") == message
    assert not (release_repo / "release-substitution").exists()
    assert not (release_repo / "release-backticks").exists()
