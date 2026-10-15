---
title: "Environment"
---

## How steps run

Steps are code: `run` and `sync` execute every step in `recipes/` as you,
with your permissions, your full environment (including any passwords or
tokens in environment variables) and your network access, with no sandbox.
Read the recipes of a project you did not write before running it. `check`,
`rules` and `cookbook` never run a step.

Each step runs as its own process:

- with the same Python interpreter as useful-views (`sys.executable`), so the
  libraries a step imports must be installed in that environment;
- with the project root as its working directory;
- with no standard input, so `input()` and `breakpoint()` fail;
- with `PYTHONIOENCODING=utf-8` and `PYTHONUNBUFFERED=1` added to the
  environment, so its output appears as it runs, in any terminal or notebook;
- in UTF-8 mode (`PYTHONUTF8=1`), so `open()` and `read_text()` read and
  write UTF-8 by default on Windows as on macOS and Linux. Pass `encoding=`
  to read a file in another encoding.

A step that fails with `ModuleNotFoundError` gets a message naming the
interpreter and the command to install the missing package into it.

## Stopping a command

Ctrl-C stops the running step, removes the views of the recipe it belongs
to, and exits with 130. On the command line, SIGTERM and SIGHUP (sent when a
CI job is cancelled, when a tool's time limit runs out, or when a terminal
closes) do the same, exiting with 143 and 129; on Windows, Ctrl-Break exits
with 149. A signal that was already being ignored stays ignored, so
`nohup useful-views sync &` keeps running after you log out. When `sync` is
stopped, its message says what it had already done and which command to run
next.

`run`, `sync` and `cookbook` hold a lock on the project while they work, in
`work/.useful-views.lock`, so a second one started meanwhile is refused
instead of overwriting the first one's records. The operating system
releases the lock when the command ends, however it ends.

## Environment variables

| Variable | Effect |
|---|---|
| `USEFUL_VIEWS_DATA_URL` | Where `example` downloads from, instead of the pinned tag of the useful-views-data repository: a mirror's URL, or a local copy (a folder path or `file://` URL) laid out like the repository (`examples/<name>/...`). Downloads are still checked against the pinned SHA-256 checksums. Useful without internet access. |
| `HTTPS_PROXY`, `SSL_CERT_FILE` | Read by Python when `example` downloads: a proxy to go through, and a certificate file for networks that inspect encrypted traffic. |

## Output

- Human-readable results go to standard output; warnings, progress lines and
  error messages go to standard error.
- With `--json`, standard output carries exactly one JSON document, also on
  errors (`{"ok": false, "error": {...}}`); everything else, including the
  steps' own output, goes to standard error. The JSON is ASCII-escaped, so it
  stays valid whatever the console's encoding.

The exit codes are listed in the [command-line reference](cli.md#exit-codes).
