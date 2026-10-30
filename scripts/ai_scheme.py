#!/usr/bin/env python3
"""Validation, progression, and prompt rendering utility for ai-schemes initiatives."""

import dataclasses
import enum
import json
import re
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path

ALLOWED_STATUSES: frozenset[str] = frozenset({"TODO", "IN_PROGRESS", "BLOCKED", "DONE"})
SCHEMA_VERSION_CURRENT: int = 1

ROLE_TO_SKILL: dict[str, str] = {
    "plan": ".agents/skills/architect/SKILL.md",
    "implement": ".agents/skills/implementer/SKILL.md",
    "review": ".agents/skills/reviewer/SKILL.md",
}


class ExitCode(enum.IntEnum):
    """Exit codes for ai_scheme commands."""

    SUCCESS = 0
    ERROR = 1
    USAGE = 2
    NO_ELIGIBLE_BLOCK = 3


class SchemeValidationError(ValueError):
    """Raised when an initiative fails structural or schema validation."""


@dataclasses.dataclass(frozen=True)
class Block:
    """Representation of an implementation block."""

    id: str
    file: Path
    status: str
    depends: list[str]
    requirements: list[str]
    commit: str | None


@dataclasses.dataclass(frozen=True)
class Initiative:
    """Representation of an ai-schemes initiative."""

    path: Path
    is_legacy: bool
    schema_version: int | None
    specification_file: Path | None
    blocks: list[Block]
    lifecycle: str = "ACTIVE"


def parse_specification_requirements(spec_path: Path) -> list[str]:
    """Extract declared requirement identifiers from a specification markdown file."""
    if not spec_path.is_file():
        return []
    content = spec_path.read_text(encoding="utf-8")
    requirements: list[str] = []

    table_pattern = re.compile(r"^\s*\|\s*([A-Z]\d{1,3}[a-z]?)\s*\|", re.MULTILINE)
    for match in table_pattern.finditer(content):
        req_id = match.group(1).strip()
        if req_id not in ("ID", "Req", "Requirement"):
            requirements.append(req_id)

    bullet_pattern = re.compile(r"^\s*-\s*\*\*([A-Z]\d{1,3}[a-z]?)\b", re.MULTILINE)
    requirements.extend(match.group(1).strip() for match in bullet_pattern.finditer(content))

    return sorted(set(requirements))


def parse_status_table(status_path: Path) -> dict[str, str]:
    """Parse block status mapping from a STATUS.md markdown table."""
    if not status_path.is_file():
        return {}
    content = status_path.read_text(encoding="utf-8")
    pattern = re.compile(r"\|\s*\[([^\]]+)\]\([^\)]+\)\s*\|\s*([A-Za-z_]+)\s*\|")
    status_map: dict[str, str] = {}
    for match in pattern.finditer(content):
        block_id = match.group(1).strip()
        status = match.group(2).strip()
        status_map[block_id] = status
    return status_map


def parse_block_header_status(block_file: Path) -> str | None:
    """Extract Status value from a block markdown file header."""
    if not block_file.is_file():
        return None
    content = block_file.read_text(encoding="utf-8")
    match = re.search(r"^Status:\s*([A-Za-z_]+)", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def parse_legacy_blocks_from_status(initiative_dir: Path, status_path: Path) -> list[Block]:
    """Fallback parser for legacy initiatives lacking manifest.json."""
    if not status_path.is_file():
        return []
    content = status_path.read_text(encoding="utf-8")
    row_pattern = re.compile(r"\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*([A-Za-z_]+)\s*\|\s*([^|]*)\s*\|")
    blocks: list[Block] = []
    for match in row_pattern.finditer(content):
        block_id = match.group(1).strip()
        rel_file = match.group(2).strip()
        status = match.group(3).strip()
        deps_raw = match.group(4).strip()
        deps = [d.strip() for d in re.findall(r"[A-Za-z0-9_]+", deps_raw) if d not in ("—", "-", "None")]
        block_path = (status_path.parent / rel_file).resolve()
        blocks.append(
            Block(
                id=block_id,
                file=block_path,
                status=status,
                depends=deps,
                requirements=[],
                commit=None,
            )
        )
    return blocks


def parse_block_entry(raw: dict[str, object], resolved_path: Path) -> Block:
    """Parse a single block dictionary from manifest data."""
    b_id = str(raw.get("id", "")).strip()
    rel_file = raw.get("file")
    if not rel_file:
        slug = raw.get("slug")
        rel_file = f"blocks/{b_id}-{slug}.md" if slug else f"blocks/{b_id}.md"

    b_file = (resolved_path / str(rel_file)).resolve()
    b_status = str(raw.get("status", "TODO"))
    raw_deps = raw.get("depends")
    if raw_deps is None:
        raw_deps = raw.get("dependencies", [])
    b_deps_seq = raw_deps if isinstance(raw_deps, list) else []
    b_deps = [str(d).strip() for d in b_deps_seq]
    raw_reqs = raw.get("requirements", [])
    b_reqs_seq = raw_reqs if isinstance(raw_reqs, list) else []
    b_reqs = [str(r) for r in b_reqs_seq]
    raw_commit = raw.get("commit")
    b_commit = str(raw_commit) if raw_commit is not None else None

    return Block(
        id=b_id,
        file=b_file,
        status=b_status,
        depends=b_deps,
        requirements=b_reqs,
        commit=b_commit,
    )


def load_initiative(path: Path) -> Initiative:
    """Load an initiative from an initiative directory path."""
    resolved_path = path.resolve()
    manifest_file = resolved_path / "manifest.json"
    status_file = resolved_path / "STATUS.md"

    if manifest_file.is_file():
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            msg = f"{manifest_file}: Invalid JSON syntax: {err}"
            raise SchemeValidationError(msg) from err

        if isinstance(data, list):
            schema_version = None
            is_legacy = True
            spec_file = None
            raw_blocks = data
        else:
            schema_version = data.get("schema_version")
            is_legacy = schema_version != SCHEMA_VERSION_CURRENT
            spec_name = data.get("specification")
            spec_file = (resolved_path / spec_name).resolve() if spec_name else None
            raw_blocks = data.get("blocks", [])

        lifecycle = data.get("lifecycle", "ACTIVE") if isinstance(data, dict) else "ACTIVE"
        if not isinstance(lifecycle, str) or lifecycle not in {"ACTIVE", "CLOSED"}:
            msg = f"{manifest_file}: Unknown initiative lifecycle {lifecycle!r}"
            raise SchemeValidationError(msg)

        blocks = [parse_block_entry(raw, resolved_path) for raw in raw_blocks if isinstance(raw, dict)]

        return Initiative(
            path=resolved_path,
            is_legacy=is_legacy,
            schema_version=schema_version,
            specification_file=spec_file,
            blocks=blocks,
            lifecycle=lifecycle,
        )

    blocks = parse_legacy_blocks_from_status(resolved_path, status_file)
    spec_candidates = [
        resolved_path / "SPECIFICATION.md",
        resolved_path / "user_functionality.md",
    ]
    spec_file = next((s for s in spec_candidates if s.is_file()), None)

    return Initiative(
        path=resolved_path,
        is_legacy=True,
        schema_version=None,
        specification_file=spec_file,
        blocks=blocks,
    )


def detect_cycle(blocks_by_id: dict[str, Block]) -> list[str] | None:
    """Check for cycles in the dependency graph using depth-first search."""
    visited: dict[str, int] = {}
    cycle_path: list[str] = []

    def dfs(node: str, stack: list[str]) -> bool:
        visited[node] = 0
        stack.append(node)
        block = blocks_by_id.get(node)
        if block:
            for dep in block.depends:
                if dep not in blocks_by_id:
                    continue
                if visited.get(dep) == 0:
                    cycle_start = stack.index(dep)
                    cycle_path.extend([*stack[cycle_start:], dep])
                    return True
                if dep not in visited and dfs(dep, stack):
                    return True
        stack.pop()
        visited[node] = 1
        return False

    for node_id in blocks_by_id:
        if node_id not in visited and dfs(node_id, []):
            return cycle_path
    return None


def validate_block_ids(initiative: Initiative) -> tuple[dict[str, Block], list[str]]:
    """Validate block ID uniqueness and build ID to block mapping."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    blocks_by_id: dict[str, Block] = {}

    for block in initiative.blocks:
        if not block.id:
            errors.append(f"{initiative.path / 'manifest.json'}: Block missing id")
            continue
        if block.id in seen_ids:
            errors.append(f"{initiative.path / 'manifest.json'}: Duplicate block ID '{block.id}'")
        seen_ids.add(block.id)
        blocks_by_id[block.id] = block

    return blocks_by_id, errors


def validate_single_block(
    block: Block, initiative_path: Path, status_map: dict[str, str], seen_ids: set[str]
) -> list[str]:
    """Validate status, headers, and prerequisites for a single block."""
    errors: list[str] = []
    if block.status not in ALLOWED_STATUSES:
        errors.append(f"{block.file}: Invalid status '{block.status}' in manifest")

    if not block.file.is_file():
        errors.append(f"{block.file}: Block file does not exist")
    else:
        header_status = parse_block_header_status(block.file)
        if header_status and header_status != block.status:
            errors.append(
                f"{block.file}: Header status '{header_status}' does not match manifest status '{block.status}'"
            )

    status_file = initiative_path / "STATUS.md"
    if block.id in status_map and status_map[block.id] != block.status:
        errors.append(
            f"{status_file}: Table status '{status_map[block.id]}' does not match manifest status "
            f"'{block.status}' for '{block.id}'"
        )

    errors.extend(
        f"{block.file}: Prerequisite '{dep}' does not exist in manifest" for dep in block.depends if dep not in seen_ids
    )

    if block.status == "DONE":
        report_file = initiative_path / "reports" / f"{block.id}.md"
        review_report = initiative_path / "reports" / f"{block.id}-review.md"
        if not report_file.is_file() and not review_report.is_file():
            errors.append(f"{report_file}: Report file required for DONE block '{block.id}' does not exist")

    return errors


def validate_specification_coverage(initiative: Initiative) -> list[str]:
    """Validate that declared specification requirements are covered by blocks."""
    errors: list[str] = []
    if initiative.specification_file and initiative.specification_file.is_file():
        spec_reqs = parse_specification_requirements(initiative.specification_file)
        covered_reqs: set[str] = set()
        for b in initiative.blocks:
            covered_reqs.update(b.requirements)
        errors.extend(
            f"{initiative.specification_file}: Requirement '{req}' not covered by any block"
            for req in spec_reqs
            if req not in covered_reqs
        )
    return errors


def validate_initiative(initiative: Initiative) -> list[str]:
    """Validate initiative invariants and return a list of error strings."""
    blocks_by_id, id_errors = validate_block_ids(initiative)
    if id_errors:
        return id_errors

    if initiative.is_legacy:
        return validate_closure(initiative)

    errors: list[str] = []
    status_file = initiative.path / "STATUS.md"
    status_map = parse_status_table(status_file)
    seen_ids = set(blocks_by_id.keys())

    for block in initiative.blocks:
        errors.extend(validate_single_block(block, initiative.path, status_map, seen_ids))

    cycle = detect_cycle(blocks_by_id)
    if cycle:
        errors.append(f"{initiative.path}: Dependency cycle detected: {' -> '.join(cycle)}")

    errors.extend(validate_specification_coverage(initiative))
    errors.extend(validate_closure(initiative))
    return errors


def validate_closure(initiative: Initiative) -> list[str]:
    """Require completion evidence and durable adoption before marking work closed."""
    if initiative.lifecycle != "CLOSED":
        return []
    errors = []
    if not initiative.blocks or any(block.status != "DONE" for block in initiative.blocks):
        errors.append(f"{initiative.path}: CLOSED initiative requires all blocks DONE")
    for name in ("CLOSURE.md", "ADOPTION.md"):
        path = initiative.path / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"{path}: CLOSED initiative requires a nonempty closure record")
    for block in initiative.blocks:
        report = initiative.path / "reports" / f"{block.id}.md"
        if not report.is_file():
            errors.append(f"{report}: CLOSED initiative requires an implementation report")
    return errors


def command_validate(args: Namespace) -> int:
    """Execute the 'validate' subcommand."""
    has_errors = False
    for path_str in args.paths:
        path = Path(path_str)
        try:
            initiative = load_initiative(path)
        except SchemeValidationError as err:
            sys.stderr.write(f"{err}\n")
            has_errors = True
            continue

        if initiative.is_legacy:
            sys.stdout.write(f"LEGACY: {initiative.path}\n")

        errors = validate_initiative(initiative)
        if errors:
            has_errors = True
            for err in errors:
                sys.stderr.write(f"{err}\n")
        else:
            sys.stdout.write(f"VALID: {initiative.path}\n")

    return ExitCode.ERROR if has_errors else ExitCode.SUCCESS


def command_next(args: Namespace) -> int:
    """Execute the 'next' subcommand."""
    path = Path(args.path)
    try:
        initiative = load_initiative(path)
    except SchemeValidationError as err:
        sys.stderr.write(f"{err}\n")
        return ExitCode.ERROR

    if initiative.lifecycle == "CLOSED" or initiative.path.parent.name == "archive":
        sys.stdout.write(f"CLOSED: {initiative.path}; create a linked follow-up initiative.\n")
        return ExitCode.NO_ELIGIBLE_BLOCK

    status_by_id = {b.id: b.status for b in initiative.blocks}

    for block in initiative.blocks:
        if block.status != "TODO":
            continue
        all_deps_done = True
        for dep in block.depends:
            if status_by_id.get(dep) != "DONE":
                all_deps_done = False
                break
        if all_deps_done:
            sys.stdout.write(f"{block.id}\t{block.file}\n")
            return ExitCode.SUCCESS

    return ExitCode.NO_ELIGIBLE_BLOCK


def build_required_reads(initiative: Initiative, block: Block) -> str:
    """Build bullet list of required context files for prompt rendering."""
    rel_init = initiative.path
    items = [
        f"- {rel_init}/README.md",
        f"- {rel_init}/EXECUTION.md",
        f"- {rel_init}/CONTRACTS.md",
        f"- {rel_init}/DECISIONS.md",
        f"- {block.file}",
    ]
    items.extend(f"- {rel_init}/reports/{dep}.md" for dep in block.depends)
    return "\n".join(items)


def command_prompt(args: Namespace) -> int:
    """Execute the 'prompt' subcommand."""
    path = Path(args.path)
    try:
        initiative = load_initiative(path)
    except SchemeValidationError as err:
        sys.stderr.write(f"{err}\n")
        return ExitCode.ERROR

    if (initiative.lifecycle == "CLOSED" or initiative.path.parent.name == "archive") and args.role != "review":
        sys.stderr.write("Closed initiatives allow explicit historical review only; create a linked follow-up.\n")
        return ExitCode.ERROR

    block_id: str = args.block_id
    matching_block: Block | None = next((b for b in initiative.blocks if b.id == block_id), None)
    if not matching_block:
        sys.stderr.write(f"Error: Block ID '{block_id}' not found in initiative {initiative.path}\n")
        return ExitCode.ERROR

    role: str = args.role
    prompt_template_file = Path(".agents/prompts") / f"{role}.md"
    if not prompt_template_file.is_file():
        sys.stderr.write(f"Error: Prompt template file {prompt_template_file} not found\n")
        return ExitCode.ERROR

    template_text = prompt_template_file.read_text(encoding="utf-8")
    role_skill = ROLE_TO_SKILL.get(role, "")
    depends_str = ", ".join(matching_block.depends) if matching_block.depends else "None"
    required_reads_str = build_required_reads(initiative, matching_block)
    report_file_str = (
        f"{initiative.path}/reports/{matching_block.id}-review.md"
        if role == "review"
        else f"{initiative.path}/reports/{matching_block.id}.md"
    )

    replacements: dict[str, str] = {
        "{{initiative_path}}": str(initiative.path),
        "{{block_id}}": matching_block.id,
        "{{block_file}}": str(matching_block.file),
        "{{depends}}": depends_str,
        "{{required_reads}}": required_reads_str,
        "{{role_skill}}": role_skill,
        "{{report_path}}": report_file_str,
    }

    rendered = template_text
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    unreplaced = re.findall(r"\{\{[a-z_]+\}\}", rendered)
    if unreplaced:
        sys.stderr.write(f"Error: Unreplaced placeholders in prompt template: {', '.join(unreplaced)}\n")
        return ExitCode.ERROR

    sys.stdout.write(rendered)
    return ExitCode.SUCCESS


def build_parser() -> ArgumentParser:
    """Build the command-line argument parser for ai_scheme."""
    parser = ArgumentParser(description="Validator and runner helper for ai-schemes initiatives.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate initiative manifest and files.")
    validate_parser.add_argument("paths", nargs="+", help="One or more initiative directory paths.")

    next_parser = subparsers.add_parser("next", help="Find the next eligible TODO block in manifest order.")
    next_parser.add_argument("path", help="Initiative directory path.")

    prompt_parser = subparsers.add_parser("prompt", help="Render role prompt template for a block.")
    prompt_parser.add_argument("path", help="Initiative directory path.")
    prompt_parser.add_argument("block_id", help="Target block ID.")
    prompt_parser.add_argument("--role", choices=["plan", "implement", "review"], required=True, help="Agent role.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "validate":
        return command_validate(args)
    if args.subcommand == "next":
        return command_next(args)
    if args.subcommand == "prompt":
        return command_prompt(args)

    return ExitCode.USAGE


if __name__ == "__main__":
    sys.exit(main())
