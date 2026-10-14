#!/bin/sh
# Configure native Cairo only for commands that need it.
set -eu

if [ "$#" -eq 0 ]; then
    echo 'Usage: sh scripts/with-cairo.sh COMMAND [ARGUMENT ...]' >&2
    exit 2
fi

missing_native() {
    echo 'Native Cairo dependencies are required for PNG rendering.' >&2
    echo 'Install Homebrew if needed, then run: brew install cairo libffi' >&2
    exit 1
}

if [ "$(uname -s)" = Darwin ]; then
    command -v brew >/dev/null 2>&1 || missing_native
    cairo_prefix=$(brew --prefix cairo 2>/dev/null) || missing_native
    libffi_prefix=$(brew --prefix libffi 2>/dev/null) || missing_native
    [ -n "$cairo_prefix" ] && [ -d "$cairo_prefix/lib" ] || missing_native
    [ -n "$libffi_prefix" ] && [ -d "$libffi_prefix/lib" ] || missing_native
    fallback=${AED_CAIRO_FALLBACK:-${DYLD_FALLBACK_LIBRARY_PATH:-}}
    DYLD_FALLBACK_LIBRARY_PATH="$cairo_prefix/lib:$libffi_prefix/lib${fallback:+:$fallback}"
    export DYLD_FALLBACK_LIBRARY_PATH
fi

unset AED_CAIRO_FALLBACK
exec "$@"
