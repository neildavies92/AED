# Local configuration

Copy `aed.example.toml` to `aed.toml` in the repository root and edit it for your
machine. `aed.toml` is Git-ignored. No credentials belong in this file.

Load it through the Python API from the repository root:

```python
from ask_every_day.config import load_config

config = load_config()  # Reads aed.toml in the current working directory.
config = load_config("/path/to/another.toml")  # Or pass a pathlib.Path.
```

There is no application CLI yet. The loader reads only the requested file; it
performs no configuration discovery, merging, environment overrides or writes.
Missing files raise `ConfigError`, rather than silently using another source.

## Settings

`database_path`, `assets_path`, and `account_handle` are required nonblank strings.
Paths have no implicit defaults: explicitly choose the local storage locations.
The example uses `data/aed.sqlite3`, `assets/generated`, and `@ask.every.day`.
Account handles retain their supplied text, including an optional `@`; format
validation beyond nonblank text is deferred with account integration.

The optional `[renderer]` table defaults to `template = "Classic"`, `width = 1080`,
and `height = 1350`, using the existing `RendererConfig` defaults. Each field can
be overridden independently. Dimensions must be positive integers (not booleans
or floats); the template must be nonblank. Template availability is not checked.
Unknown keys, incorrect types, malformed TOML and invalid domain values raise
`ConfigError` with the source path and reason. The original exception is chained.

Relative data paths are anchored to the configuration file's containing directory,
not the process working directory. Absolute paths remain absolute; `.` and `..`
are collapsed lexically. Symlinks are not resolved: a symlinked configuration file
uses the directory of the supplied path. `~` and environment variable expressions
are literal path components, not expanded. Use explicit paths instead. Configured
files/directories need not exist; loading never creates them or opens a database.

Successful loading emits one standard-library INFO log with structured `event`
(`config_loaded`) and `config_path` fields. Logging handlers and formatting are
left to the caller; configuration contents are not logged.
