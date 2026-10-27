# Shared runtime contract — AED-027 / AED-053

Agreed before implementation on main `cfb33fa590afb70f6eeb3549b8309fe206c1b1eb`.
A owns scheduler policy/orchestration/CLI, the optional publishing dispatch guard,
LaunchAgent adapter, scheduler tests/docs, root Makefile/README and this note.
B owns Docker/Compose, container entrypoint, Linux file credential backend,
container tests/docs and its container Makefile. No shared implementation branch or
unreviewed cherry-pick is used. Merge A first; rebase/integrate B after A merges.

The portable process is:

```sh
python -m ask_every_day.scheduler_cli run \
  --config /state/aed.toml --schedule-config /state/schedule.toml \
  --auth-config /private/instagram-auth.toml --data-dir /app/data
```

`ask_every_day.scheduler_cli.main(argv=None, *, credential_factory=...) -> None`
accepts a `Callable[[Path], CredentialManager]`. Its default uses existing macOS
Keychain. The container adapter injects a manager backed by its selected Linux
`CredentialStore`; it does not reimplement publication, policy or token refresh.
The application `run_daily` also accepts injected clock/client/staging/credential
factories for deterministic nonpublishing tests. Existing Cloudflare staging stays
behind `Stage`; manual publish-next exact interactive approval is unchanged.

`aed.toml` remains application config. Separate `schedule.toml` specifies the
Europe/London local window and authorization lifetime. Paths may be absolute;
relative application paths resolve from the config file. Durable authority remains
SQLite queue/history/checkpoints and its matching palette sidecar. Authorization
lives at `<resolved-database>.schedule.json`; it must be persisted alongside state,
as must canonical assets and the common `<database>.publish.lock` inode. Logs may be
retained separately in a private bounded file. Setup/startup never authorizes posts.
Moving configuration, code, renderer environment or state requires fresh approval.

The command exits after zero or one publication. Subcommands are `preview`, `run`,
`status`, `enable`, `disable`, `check-credentials`. Exit 0 means success or a safe
no-op (disabled, outside window, daily limit, empty queue); exit 1 means operational
or policy failure; exit 2 means invalid CLI arguments. JSON stdout is a safe summary;
structured JSON logs go to stderr or the explicitly selected bounded log file.
Failure output/audit retains only validated operational IDs and fixed diagnostics.
Tokens, raw provider errors/bodies, capability URLs and editorial copy are omitted.

Only explicit, exact TTY confirmation enables an authorization. Once enabled, the
same `run` requires no TTY. A host timer supplies triggers only; the application
checks London dates/windows, durable history and authorization before dispatch.
The existing per-database advisory lock covers manual publishing, rendering,
recovery and the entire scheduled operation. It does not coordinate divergent
copies on different hosts. Exactly one environment may be active. Unknown remote
outcomes remain blocked under AED-024; there is no blind POST retry or backfill.

B can ship a gated container/credential milestone while A is unmerged, but cannot
claim unattended acceptance or activate an alternate publisher. After A merges,
B rebases, wires this same entrypoint, runs all checks and nonpublishing container
acceptance, and updates its PR. Both merges and activation/cutover need owner review.
