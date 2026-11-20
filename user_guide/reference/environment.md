---
title: "Environment"
---

How steps run, how commands stop, and what they read and print.

## How a step runs

`run` and `sync` run each step as its own process:

| Setting | Value |
|---|---|
| Interpreter | The Python that runs useful-views (`sys.executable`), so the libraries a step imports must be installed there. After a `ModuleNotFoundError`, the message names the interpreter and the command that installs the package into it. |
| Working directory | The project root. |
| Standard input | None, so `input()` and `breakpoint()` fail. |
| Environment variables | Yours, including any passwords or tokens, plus the three below. |
| `PYTHONIOENCODING=utf-8`, `PYTHONUNBUFFERED=1` | The step's output appears as it runs, in any terminal or notebook. |
| `PYTHONUTF8=1` | `open()` and `read_text()` read and write UTF-8 by default, on Windows as on macOS and Linux. Pass `encoding=` for a file in another encoding. |
| Permissions and network access | Yours. There is no sandbox. |

Steps are code, so read the recipes of a project you did not write before
you run it (see [Steps](../concepts/steps.md)). `check`, `rules` and
`cookbook` never run a step.

## Stopping a command

Ctrl-C stops the running step, removes the views of its recipe, and exits.
The command line treats these signals the same way:

| Signal | Exit code |
|---|---|
| Ctrl-C | 130 |
| SIGTERM, sent when a CI job is cancelled or a tool's time limit runs out | 143 |
| SIGHUP, sent when a terminal closes | 129 |
| Ctrl-Break, on Windows | 149 |

A signal that was already ignored stays ignored, so
`nohup useful-views sync &` keeps running after you log out. When `sync`
stops, its message says what it had done and which command to run next.

## The project lock

`run`, `sync` and `cookbook` (when it writes) lock the project while they
work, in `work/.useful-views.lock`. A second command started meanwhile is
refused, so it cannot overwrite the first one's records. The operating
system releases the lock when the command ends, however it ends.

## Environment variables

| Variable | Effect |
|---|---|
| `USEFUL_VIEWS_DATA_URL` | Where `example` downloads from, in place of the pinned tag of the useful-views-data repository. A mirror's URL, or a local copy (a folder path or `file://` URL) laid out like the repository (`examples/<name>/...`), for use without internet access. Downloads are still checked against the pinned SHA-256 checksums. |
| `HTTPS_PROXY` | A proxy for `example` downloads. Python reads it. |
| `SSL_CERT_FILE` | A certificate file for `example` downloads, on networks that inspect encrypted traffic. Python reads it. |

## Output

- Standard output: human-readable results.
- Standard error: warnings, progress lines and error messages.
- With `--json`, standard output carries exactly one JSON document, also on
  errors (`{"ok": false, "error": {...}}`). Everything else goes to standard
  error, including the steps' own output. The JSON is ASCII-escaped, so it
  stays valid whatever the console's encoding.

The exit codes are in the [command-line reference](cli.md#exit-codes).
