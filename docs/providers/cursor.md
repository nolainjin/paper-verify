# Cursor Harness

## Recommended Surface

Use CLI JSON from the current workspace file:

```bash
paper-verify path/to/paper.md --level L2 --judge keyword --json
paper-verify path/to/paper.md --level L2 --judge openai --json
```

## Rule Shape

- Keep Cursor rules short and operational.
- Ask Cursor to parse stdout as JSON.
- Use `L1` before longer checks when editing many references.
- Keep generated reports out of commits unless explicitly requested.

## Failure Policy

Cursor should treat nonzero exit code `2` as a real command error. A tier-F citation is not a command failure; inspect `has_failure`.

