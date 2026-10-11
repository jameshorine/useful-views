---
title: "Environment"
guide-section: Reference
---

## How steps run

Each step runs as its own process:

- with the same Python interpreter as useful-views (`sys.executable`), so the
  libraries a step imports must be installed in that environment;
- with the project root as its working directory;
- with no standard input, so `input()` and `breakpoint()` fail;
- with `PYTHONIOENCODING=utf-8` and `PYTHONUNBUFFERED=1` added to the
  environment, so its output appears as it runs, in any terminal or notebook.

A step that fails with `ModuleNotFoundError` gets a message naming the
interpreter and the command to install the missing package into it.

## Environment variables

| Variable | Effect |
|---|---|
| `USEFUL_VIEWS_DATA_URL` | Where `example` downloads from, instead of the pinned tag of the useful-views-data repository. A mirror, or a local copy as a `file://` URL laid out like the repository (`examples/<name>/...`). Downloads are still checked against the pinned SHA-256 checksums. |

## Output

- Human-readable results go to standard output; warnings, progress lines and
  error messages go to standard error.
- With `--json`, standard output carries exactly one JSON document, also on
  errors (`{"ok": false, "error": {...}}`); everything else, including the
  steps' own output, goes to standard error. The JSON is ASCII-escaped, so it
  stays valid whatever the console's encoding.

The exit codes are listed in the [command-line reference](cli.md#exit-codes).
