#!/bin/sh
# Keep release metadata in one commit before Commitizen creates the annotated tag.
set -eu

if [ -n "$(git status --porcelain)" ]; then
    echo "Release requires a clean workspace and index. Commit or set aside pending work first." >&2
    exit 1
fi

set -- --yes --no-verify --annotated-tag
if [ -n "${RELEASE_MESSAGE:-}" ]; then
    set -- "$@" --annotated-tag-message "$RELEASE_MESSAGE"
fi

if [ -n "${RELEASE_VERSION:-}" ]; then
    release_version=${RELEASE_VERSION#v}
    if [ -f CHANGES.md ] && ! grep -Fxq "## $release_version" CHANGES.md && ! grep -Fxq "## v$release_version" CHANGES.md; then
        echo "Notice: CHANGES.md has no entry for the requested release."
    fi
    set -- "$@" "$release_version"
elif [ -n "${RELEASE_INCREMENT:-}" ]; then
    set -- "$@" --increment "$RELEASE_INCREMENT"
fi

# Commitizen already generates the target version's changelog before committing.
# The ordinary post-commit generator cannot see the new tag until bump completes.
export HOVORUN_RELEASE_IN_PROGRESS=1
uv run --no-sync cz bump "$@"

echo "Created the release commit and annotated tag (not pushed)."
