# Docstring Standard

All modules, classes, and functions in `src/hovorunv2/` use one Google-style docstring standard. Ruff enforces structural correctness; review enforces the short-versus-full judgment.

## Format

- One summary line: imperative mood, capitalized, ends with a period. No leading "This"/"The".
- Optional description paragraph after the summary when needed to explain WHY and WHAT the function does; keep it to the non-obvious reason and behavior, never a restatement of the summary or parameters.
- Blank line after the summary (or description), then sections in order: `Args:`, `Returns:` or `Yields:`, `Raises:`.
- Types are never repeated in the docstring; they come from the signature's PEP 484 hints. No `:type:`/`:rtype:` fields.
- `Args:` lists every parameter, one line each (`name: description`), including `*args:`/`**kwargs:` when present.
- `Returns:` present whenever the function returns a non-`None` value; absent when it returns `None`.
- `Raises:` lists every exception the function can directly raise, one per line (`ExceptionType: condition`). Use concrete exception types; describe rollback or re-raise semantics in prose instead of a catch-all `Exception`.

```python
async def resolve(self, kind: TargetKind, reference: str) -> AdminTarget:
    """Reject unknown handles, tombstones and noncanonical routes without provisioning.

    Resolution never provisions a new identity and binds the result to the current
    generation, so a stale target is rejected on reuse.

    Args:
        kind: Kind of the target to resolve.
        reference: Handle or numeric Telegram ID supplied by the caller.

    Returns:
        Resolved target bound to the current identity generation.

    Raises:
        ValueError: If the reference cannot be resolved to an active canonical target.
    """
```

## Depth

- **Short** (summary only, no sections): obvious or self-evident functions and methods — trivial accessors, passthroughs, and self-documenting factories with no assumptions, validation, I/O, mutation, side effects, or raised exceptions.

  ```python
  def is_trusted(self) -> bool:
      """Return whether the target carries the trust flag."""
  ```

- **Full** (summary + sections): any public interface consumed across layer or module boundaries, and any function implementing non-trivial logic — assumptions, validation, I/O, mutation, side effects, multiple steps, or raised exceptions.

## Enforcement

- Ruff `D` rules enforce presence and summary format.
- `D417` (google convention) enforces that an `Args:` section documents every parameter.
- Pydoclint preview rules `DOC102`, `DOC201`, `DOC202`, `DOC402`, `DOC403`, `DOC501`, `DOC502` enforce that sections match the signature: no phantom parameters, returns, yields, or exceptions; no missing returns, yields, or directly-raised exceptions.
- `ignore-one-line-docstrings` exempts Short docstrings from the pydoclint checks.

## Rules

- No `# noqa` on any `D` rule. Every public module, class, function, and method keeps a real docstring.
- Do not add tooling, scripts, or generators for future docstring authoring or updating; the standard plus Ruff enforcement is the whole contract.
