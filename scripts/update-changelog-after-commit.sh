#!/bin/sh
# Generate from the new HEAD, then amend only the generated changelog.
set -eu

cd "$(git rev-parse --show-toplevel)"
original_head=$(git rev-parse HEAD)

if ! git diff --quiet -- CHANGELOG.md || ! git diff --cached --quiet -- CHANGELOG.md; then
    echo "Changelog hook: CHANGELOG.md has local edits; preserve them and run make changelog manually." >&2
    exit 1
fi

if [ -n "$(git tag --points-at HEAD)" ]; then
    echo "Changelog hook: refusing to amend a tagged commit; regenerate before tagging." >&2
    exit 1
fi

generated_changelog=$(mktemp "${TMPDIR:-/tmp}/hovorun-changelog.XXXXXX.md")
trap 'rm -f "$generated_changelog"' EXIT
trap 'exit 1' HUP INT TERM

# Generate separately so generator failure cannot overwrite the tracked file.
uv run cz changelog --file-name "$generated_changelog"

if cmp -s CHANGELOG.md "$generated_changelog"; then
    exit 0
fi

if [ "$(git rev-parse HEAD)" != "$original_head" ]; then
    echo "Changelog hook: HEAD changed during generation; refusing to amend another commit." >&2
    exit 1
fi

cp "$generated_changelog" CHANGELOG.md
# --only preserves unrelated staged changes. Disable hooks for this generated-only
# amendment: the original commit already ran quality/message checks, and running
# post-commit again would recurse. Git preserves the original message and author.
git -c core.hooksPath=/dev/null commit --amend --no-edit --only -- CHANGELOG.md
