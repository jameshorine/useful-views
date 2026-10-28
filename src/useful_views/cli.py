"""The useful-views command: a thin wrapper over the Python functions."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import sys
import threading
import warnings

from . import __version__, check, cookbook, example, init, new_recipe, run, sync
from .errors import UsefulViewsError, UsefulViewsWarning
from .rulebook import RULES, describe_rules
from .status import syncable

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2
EXIT_FAILED = 3

EXIT_INTERRUPTED = 130  # 128 + SIGINT; a stop by another signal exits 128 + its number

EXIT_CODES = """exit codes:
  0    success
  1    check found problems, or cookbook --check found the cookbook out of date
  2    usage error (unknown command or option, or a bad -C path)
  3    refused or failed (the message says what to fix, and in which file)
  130  interrupted (Ctrl-C)
  143  terminated (SIGTERM, from a CI cancel or a tool's time limit; 129 SIGHUP; 149 Ctrl-Break)"""


class UsageError(Exception):
    """The command line itself is wrong."""


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)  # --js is an error, not --json
        super().__init__(*args, **kwargs)

    def error(self, message):  # raise instead of exiting, so --json can still answer
        raise UsageError(f"{self.prog}: {message}")


def _error(kind: str, message: str) -> dict:
    """An error as JSON, with the same keys as UsefulViewsError.to_dict()."""
    return {"type": kind, "message": message, "recipe": None, "sync_result": None}


class Terminated(KeyboardInterrupt):
    """The command was stopped by a signal other than Ctrl-C. Raised from the
    signal handler, so the step is stopped and cleaned up as for Ctrl-C."""

    def __init__(self, signum: int):
        super().__init__()
        self.signum = signum


@contextlib.contextmanager
def _stop_signals():
    """Treat SIGTERM and SIGHUP (POSIX) and SIGBREAK (Windows) like Ctrl-C
    while the command runs, so a cancelled CI job or a tool's timeout does
    not leave a step running on its own. Only the command line does this;
    the Python API leaves signal handling to its caller."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(signum, frame):
        raise Terminated(signum)

    previous = {}
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, name, None)
        # Only replace the default: a signal ignored on purpose (nohup ignores
        # SIGHUP, and steps inherit that) or handled by a caller stays so.
        if sig is None or signal.getsignal(sig) is not signal.SIG_DFL:
            continue
        try:
            previous[sig] = signal.signal(sig, stop)
        except (OSError, ValueError):
            pass
    try:
        yield
    finally:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                pass


def _parser() -> argparse.ArgumentParser:
    formatter = argparse.RawDescriptionHelpFormatter
    parser = _Parser(
        prog="useful-views",
        description=(
            "Turn raw data into documented derived tables (useful views). Each recipe in recipes/ "
            "turns declared inputs into declared views by running numbered Python steps, and every "
            "run is recorded in datapackage.json."
        ),
        epilog=EXIT_CODES + "\n\nAlso runs as: python -m useful_views",
        formatter_class=formatter,
    )
    parser.add_argument("--version", action="version", version=f"useful-views {__version__}")
    project_help = "run as if started in PATH (like git -C), for example a project folder"
    json_help = "print the result as JSON on standard output; other output goes to standard error"
    parser.add_argument("-C", "--project", metavar="PATH", help=project_help)
    parser.add_argument("--json", action="store_true", help=json_help)
    # The same options after the command; SUPPRESS keeps a value given before it.
    common = _Parser(add_help=False)
    common.add_argument("-C", "--project", metavar="PATH", default=argparse.SUPPRESS, help=project_help)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=json_help)
    commands = parser.add_subparsers(dest="command", required=True, metavar="command", parser_class=_Parser)

    def add(name: str, help: str, description: str) -> argparse.ArgumentParser:
        return commands.add_parser(name, help=help, description=description, epilog=EXIT_CODES,
                                   parents=[common], formatter_class=formatter)

    p = add("init", "create the project structure (safe to run twice)",
            "Create the project structure in PATH: raw/, recipes/, work/, useful-views/, a starter "
            "README.md, AGENTS.md (for AI coding assistants), .gitignore and datapackage.json. Never "
            "overwrites a file.")
    p.add_argument("path", nargs="?", default=".", help="project folder (default: the current folder)")
    p.add_argument("--force", action="store_true",
                   help="create the project even in your home folder or at the top of a drive")

    p = add("example", "download a ready-to-run example project",
            "Download an example project (raw data and recipes) from the useful-views-data repository, "
            "check every file's SHA-256, and set it up like init. Then run: useful-views sync")
    p.add_argument("name", help="the example to download, such as pwt")
    p.add_argument("path", nargs="?", default=None, help="folder to create (default: <name>-example)")

    p = add("new-recipe", "create recipes/<name>/ from a template",
            "Create recipes/NAME/ with a template recipe.toml and a stub step, 01_first_step.py. Both say "
            "TODO and are refused until you fill them in.")
    p.add_argument("name", help="the recipe's name: lowercase letters, digits and underscores, like pwt_panel")

    p = add("run", "run one recipe and record what it produced",
            "Run one recipe. It is checked against the rules first (useful-views rules), then its views are "
            "deleted and work/RECIPE/ emptied, each step runs in numeric order as its own process with this "
            "Python and the project root as working directory, every declared view must be written, and the "
            "run is recorded in datapackage.json and useful-views/README.md. Other recipes are not run.")
    p.add_argument("recipe", help="the recipe's name, as in pwt_panel or recipes/pwt_panel/")

    add("check", "report the state of the project",
        "Report what is missing or out of place, every rule a recipe breaks (refused), and recipes that "
        "sync would run. Changes nothing. Does not notice changes inside a step's code.")

    p = add("sync", "make the views match the recipe folders, like uv sync",
            "Make the views and datapackage.json match the recipe folders, like uv sync. Checks every recipe "
            "first and changes nothing if any would be refused. Then removes the records and view files of "
            "deleted recipes and outputs, and runs, upstream first, every recipe that is new, changed or "
            "missing its views, plus everything downstream of those.")
    p.add_argument("--all", action="store_true",
                   help="run every recipe again, upstream first (sync does not notice edits to a step's code)")

    p = add("cookbook", "write cookbook.json, the ledger of the recipes used",
            "Write cookbook.json at the project root: every recorded recipe, in run order, with its "
            "description, inputs, outputs and steps, and the raw inputs the recipes read. Generated from "
            "datapackage.json, so the project must be in sync to write it. Commit it, like a lock file.")
    p.add_argument("--check", action="store_true",
                   help="do not write; exit 1 if cookbook.json is missing or out of date, or a recipe changed "
                        "without a sync. Needs no data, so it works in CI")

    add("rules", "list the rules a recipe must follow",
        "List the numbered rules. A refusal names the rule it broke.")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    argv = sys.argv[1:] if argv is None else list(argv)
    wants_json = any(a == "--json" or a.startswith("--json=") for a in argv)
    try:
        if wants_json:
            # --help and --version print and stop inside argparse; answer them
            # in JSON too, so standard output always carries one document.
            printed = io.StringIO()
            try:
                with contextlib.redirect_stdout(printed):
                    args = _parser().parse_args(argv)
            except SystemExit as exc:
                text = printed.getvalue()
                print(text, end="", file=sys.stderr)
                if text.startswith("usage:"):
                    data = {"ok": True, "help": text, "warnings": []}
                else:
                    data = {"ok": True, "version": __version__, "warnings": []}
                print(json.dumps(data, indent=2))
                return exc.code if isinstance(exc.code, int) else EXIT_OK
        else:
            args = _parser().parse_args(argv)
    except UsageError as exc:
        return _fail(wants_json, str(exc), _error("UsageError", str(exc)), code=EXIT_USAGE)
    try:
        start = os.getcwd()
    except OSError:  # the folder this was started in has been deleted
        start = None
        if not args.project:
            message = "the current folder no longer exists; cd into the project, or use -C <path>"
            return _fail(args.json, message, _error("UsageError", message), code=EXIT_USAGE)
    try:
        if args.project:
            try:
                os.chdir(os.path.expanduser(args.project))
            except OSError as exc:
                message = f"cannot use -C {args.project}: {exc.strerror or exc}"
                return _fail(args.json, message, _error("UsageError", message), code=EXIT_USAGE)
        with _stop_signals():
            return _main(args)
    finally:
        try:
            if start is not None:
                os.chdir(start)
        except OSError:
            pass  # the folder it started in was deleted meanwhile


def _main(args: argparse.Namespace) -> int:
    shown: list[str] = []  # every warning, for the JSON output
    with warnings.catch_warnings():
        original = warnings.showwarning

        def show(message, category, filename, lineno, file=None, line=None):
            if issubclass(category, UsefulViewsWarning):
                print(f"warning: {message}", file=sys.stderr)
                if str(message) not in shown:
                    shown.append(str(message))
            else:
                original(message, category, filename, lineno, file, line)

        # Warnings are part of the command's output (and of its JSON), so show
        # them whatever filters the environment sets.
        warnings.simplefilter("always", UsefulViewsWarning)
        warnings.showwarning = show
        out = sys.stdout
        try:
            if args.json:
                # Step output and progress go to standard error; standard
                # output carries only the JSON.
                with contextlib.redirect_stdout(sys.stderr):
                    code, data = _dispatch(args, shown)
                data["warnings"] = list(dict.fromkeys([*data.get("warnings", []), *shown]))
                print(json.dumps(data, indent=2), file=out)
                return code
            code, _ = _dispatch(args, shown)
            return code
        except UsefulViewsError as exc:
            return _fail(args.json, str(exc), exc.to_dict(), out, shown=shown)
        except KeyboardInterrupt as exc:
            if isinstance(exc, Terminated):
                name = signal.Signals(exc.signum).name
                message, code = f"terminated ({name})", 128 + exc.signum
            else:
                message, code = "interrupted", EXIT_INTERRUPTED
            note = str(exc)  # what sync had done, when it was sync that stopped
            if note:
                message = f"{message}\n\n{note}"
            error = _error("Interrupted", message)
            result = getattr(exc, "sync_result", None)
            error["sync_result"] = result.to_dict() if result is not None else None
            return _fail(args.json, message, error, out, code=code, shown=shown)
        except Exception as exc:  # an unexpected failure: still readable, still exit 3
            message = f"{type(exc).__name__}: {exc}"
            return _fail(args.json, message, _error(type(exc).__name__, str(exc)), out, shown=shown)


def _fail(as_json: bool, message: str, error: dict, out=None, *, code: int = EXIT_FAILED,
          shown: list[str] | None = None) -> int:
    print(f"error: {message}", file=sys.stderr)
    if as_json:
        # ASCII-escaped, so the JSON stays valid on any console encoding.
        document = {"ok": False, "error": error, "warnings": list(shown or [])}
        print(json.dumps(document, indent=2), file=out or sys.stdout)
    return code


def _dispatch(args: argparse.Namespace, shown: list[str]) -> tuple[int, dict]:
    if args.command == "init":
        folder = init(args.path, force=args.force)
        print(f"useful-views project ready at {folder}")
        return EXIT_OK, {"ok": True, "project": str(folder)}
    if args.command == "example":
        folder = example(args.name, args.path)
        print(f'Example {args.name!r} ready at {folder}. Make its views with: useful-views -C "{folder}" sync')
        return EXIT_OK, {"ok": True, "project": str(folder)}
    if args.command == "new-recipe":
        folder = new_recipe(args.name)
        print(f"Created {folder}. Edit its recipe.toml and 01_first_step.py, then run: useful-views run {args.name}")
        return EXIT_OK, {"ok": True, "recipe": args.name, "folder": str(folder)}
    if args.command == "run":
        result = run(args.recipe)
        print(f"Ran recipes/{result.recipe}. Wrote {', '.join(result.outputs)}.")
        print("Updated datapackage.json and useful-views/README.md.")
        return EXIT_OK, {"ok": True, **result.to_dict()}
    if args.command == "check":
        findings = check()
        data = {"ok": not findings, "findings": [f.to_dict() for f in findings]}
        if not findings:
            if shown:
                print(f"No problems found ({len(shown)} warning{'s' if len(shown) > 1 else ''} above).")
            else:
                print("No problems found.")
            return EXIT_OK, data
        for finding in findings:
            first, *rest = finding.message.split("\n")
            print(f"{finding.kind}: {first}")
            for line in rest:
                print(f"    {line}" if line else "")
        for_sync = [f for f in findings if syncable(f)]
        others = [f for f in findings if not syncable(f) and f.kind not in ("stale-cookbook", "stray-file")]
        if for_sync and not others:
            then = ", then useful-views cookbook" if any(f.kind == "stale-cookbook" for f in findings) else ""
            print(f"\nTo make or update everything that is missing or changed, upstream first, run: "
                  f"useful-views sync{then}")
        elif for_sync:
            print("\nFix the other findings first; then useful-views sync makes or updates the rest, upstream first.")
        return EXIT_PROBLEMS, data
    if args.command == "sync":
        result = sync(all=args.all)
        if result.removed:
            print("Removed the records of deleted recipes: " + ", ".join(f"recipes/{n}" for n in result.removed) + ".")
        if result.dropped:
            makers = _makers()
            moved = [f"{v} (now made by recipes/{makers[v]})" for v in result.dropped if v in makers]
            gone = [v for v in result.dropped if v not in makers]
            if gone:
                print("Removed views no recipe declares any more: " + ", ".join(gone) + ".")
            if moved:
                print("Moved to another recipe: " + ", ".join(moved) + ".")
        if result.deleted:
            print("Deleted: " + ", ".join(result.deleted) + ".")
        if result.ran:
            print("Ran, in order: " + ", ".join(f"recipes/{n}" for n in result.ran) + ".")
        if result.skipped:
            print("Up to date after all, not run: " + ", ".join(f"recipes/{n}" for n in result.skipped)
                  + " (the views they read came out the same).")
        if not (result.removed or result.dropped or result.ran):
            print("Everything is in sync: nothing to remove or run.")
        else:
            print("Updated datapackage.json and useful-views/README.md.")
        return EXIT_OK, {"ok": True, **result.to_dict()}
    if args.command == "cookbook":
        result = cookbook(check=args.check)
        data = {"ok": result.up_to_date, **result.to_dict()}
        if args.check:
            if result.up_to_date:
                print("cookbook.json is up to date.")
                return EXIT_OK, data
            if result.findings:
                print("The recipe folders no longer match their records in datapackage.json, so cookbook.json "
                      "is out of date:")
                for finding in result.findings:
                    print(f"  {finding.kind}: {finding.message.splitlines()[0]}")
                if all(syncable(f) for f in result.findings):
                    print("Run useful-views sync, then useful-views cookbook, and commit the results.")
                else:
                    print("Fix what is listed (useful-views check shows each in full), then run useful-views sync "
                          "and useful-views cookbook, and commit the results.")
            else:
                print("cookbook.json is missing or out of date. Update it with: useful-views cookbook")
            return EXIT_PROBLEMS, data
        if result.written:
            print(f"Wrote cookbook.json: {len(result.recipes)} recipe(s), in run order.")
        else:
            print("cookbook.json is already up to date.")
        return EXIT_OK, data
    if args.command == "rules":
        print(describe_rules())
        return EXIT_OK, {"ok": True, "rules": [r._asdict() for r in RULES.values()]}
    raise AssertionError(args.command)


def _makers() -> dict[str, str]:
    """Each recorded view and the recipe that made it, after a sync."""
    from .project import find_root
    from .record import load_datapackage

    try:
        data = load_datapackage(find_root())
    except UsefulViewsError:
        return {}
    return {out: r["name"] for r in data["recipes"] for out in r["outputs"]}


if __name__ == "__main__":
    sys.exit(main())
