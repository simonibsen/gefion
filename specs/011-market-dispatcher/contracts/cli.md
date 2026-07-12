# CLI Contract — Market Dispatcher (011)

## `gefion macro derive` (door unchanged, engine replaced)
```
gefion macro derive [--series X,Y|all] [--min-stocks 100] [--full]
                    [--reseed <name>] [--db-url] [--json]
```
- Iterates enabled scope='market' registry functions (name-matched to series).
- Per-function report: written N | skipped (disabled) | FAILED (reason).
- Exit non-zero if any function failed; healthy functions still complete.
- `--reseed <name>`: explicitly overwrite one body from the repo seed
  (reported loudly; the ONLY path that clobbers a DB edit).

## `gefion feat-fx-list`
- Gains a `scope` column.

## `gefion feat-fx-import`
- Accepts market-scope function JSON (scope field in the payload); refuses a
  market body whose `inputs.features` reference unknown feature definitions.

## Honest refusals
- Market body raising / returning wrong shape → per-function failure, zero
  writes for it, named reason.
- Sandbox violation → the sandbox's own ImportError message, verbatim.
- Unknown series name → refusal listing available derived series.
