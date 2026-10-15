# Application logging

Executable boundaries call `configure_logging()` from
`ask_every_day.logging_config`. The content-import and Classic prototype commands
configure the `ask_every_day` logger namespace at INFO by default. Library imports
do not configure handlers or the root logger. Repeated configuration reuses the
owned handler; unrelated handlers remain untouched. An embedding application that
owns its own logging can leave configuration to its host instead.

Each operational log is one JSON line on stderr. Functional output stays on
stdout (for example, the import count). Argparse usage/errors and the import
command's existing final failure diagnostic remain human-readable stderr lines;
stderr is therefore not a JSON-only protocol on command failure. The prototype
writes its artifact to disk and emits no functional stdout.

Every record has `timestamp` (UTC ISO-8601 with milliseconds and `Z`), `level`,
`logger`, and `event`. Events are stable snake_case identifiers. For example:

```json
{"timestamp":"2026-10-15T09:00:00.000Z","level":"INFO","logger":"ask_every_day.content_import","event":"content_imported","imported_count":400,"source_path":"data/prompts.csv","unchanged_count":0,"updated_count":0}
```

Modules use standard-library loggers with `extra={"event": ..., ...}`. The two
`python -m` entrypoints retain their canonical module logger names when executed
as `__main__`, so they stay within the configured application namespace. Existing
configuration, repository/schema, renderer, PNG and publication events use this
same formatter without changing their business logic. Pure domain, layout and
policy functions remain free of logging.

Context has an explicit allowlist in `logging_config.py`: identifiers, operation
and state names, paths, template/format, error type, integer counts, dimensions,
queue seed and rule gaps. Integer fields accept integers only; text fields accept
strings up to 1024 characters. Null is supported. Unknown fields, other types and
oversized strings are omitted. Missing/invalid event names become `log_record`.
Future operations can use these fields directly; extend the allowlist deliberately
when a new safe field is needed.

Messages, positional arguments, exception strings, stack traces and arbitrary
objects are never serialized. Log failures with a stable event, relevant IDs and
`error_type=type(error).__name__`. Never put credentials, tokens, prompts, provider
payloads, image bytes, SQL or environment dumps into allowed fields. This is a
schema-based exclusion policy, not a secret detector: callers must keep allowed
IDs, paths and event names safe. Local filesystem paths are intentional diagnostic
context. JSON escaping keeps embedded newlines within a single record.

`configure_logging("WARNING")` (or another stdlib level) changes the threshold.
No environment variables, config-file keys, logging framework or runtime
dependency are added.
