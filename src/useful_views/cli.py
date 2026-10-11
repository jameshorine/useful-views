"""The useful-views command: a thin wrapper over the Python functions."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import warnings

from . import __version__, check, example, init, new_recipe, run, sync
from .errors import UsefulViewsError, UsefulViewsWarning
from .rulebook import RULES, describe_rules

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2
EXIT_FAILED = 3

EXIT_INTERRUPTED = 130

EXIT_CODES = """exit codes:
  0    success
  1    check found problems
  2    usage error (unknown command or option, or a bad -C path)
  3    refused or failed (the message says what to fix, and in which file)
  130  interrupted (Ctrl-C)"""


class UsageError(Exception):
    """The command line itself is wrong."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # raise instead of exiting, so --json can still answer
        raise UsageError(f"{self.prog}: {message}")

SYNCABLE = {"never-run", "out-of-date", "missing-view", "missing-recipe-folder"}


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
            "README.md, .gitignore and datapackage.json. Never overwrites a file.")
    p.add_argument("path", nargs="?", default=".", help="project folder (default: the current folder)")

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
    wants_json = "--json" in argv
    try:
        args = _parser().parse_args(argv)
    except UsageError as exc:
        return _fail(wants_json, str(exc), {"type": "UsageError", "message": str(exc)}, code=EXIT_USAGE)
    start = os.getcwd()
    try:
        if args.project:
            try:
                os.chdir(os.path.expanduser(args.project))
            except OSError as exc:
                message = f"cannot use -C {args.project}: {exc.strerror or exc}"
                return _fail(args.json, message, {"type": "UsageError", "message": message}, code=EXIT_USAGE)
        return _main(args)
    finally:
        os.chdir(start)


def _main(args: argparse.Namespace) -> int:
    with warnings.catch_warnings():
        original = warnings.showwarning

        def show(message, category, filename, lineno, file=None, line=None):
            if issubclass(category, UsefulViewsWarning):
                print(f"warning: {message}", file=sys.stderr)
            else:
                original(message, category, filename, lineno, file, line)

        warnings.simplefilter("always", UsefulViewsWarning)
        warnings.showwarning = show
        out = sys.stdout
        try:
            if args.json:
                # Step output and progress go to standard error; standard
                # output carries only the JSON.
                with contextlib.redirect_stdout(sys.stderr):
                    code, data = _dispatch(args)
                print(json.dumps(data, indent=2), file=out)
                return code
            code, _ = _dispatch(args)
            return code
        except UsefulViewsError as exc:
            return _fail(args.json, str(exc), exc.to_dict(), out)
        except KeyboardInterrupt:
            message = "interrupted"
            return _fail(args.json, message, {"type": "Interrupted", "message": message}, out,
                         code=EXIT_INTERRUPTED)
        except Exception as exc:  # an unexpected failure: still readable, still exit 3
            message = f"{type(exc).__name__}: {exc}"
            return _fail(args.json, message, {"type": type(exc).__name__, "message": str(exc), "recipe": None}, out)


def _fail(as_json: bool, message: str, error: dict, out=None, *, code: int = EXIT_FAILED) -> int:
    print(f"error: {message}", file=sys.stderr)
    if as_json:
        # ASCII-escaped, so the JSON stays valid on any console encoding.
        print(json.dumps({"ok": False, "error": error}, indent=2), file=out or sys.stdout)
    return code


def _dispatch(args: argparse.Namespace) -> tuple[int, dict]:
    if args.command == "init":
        folder = init(args.path)
        print(f"useful-views project ready at {folder}")
        return EXIT_OK, {"ok": True, "project": str(folder)}
    if args.command == "example":
        folder = example(args.name, args.path)
        print(f"Example {args.name!r} ready at {folder}. Make its views with: cd {folder} && useful-views sync")
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
            print("No problems found.")
            return EXIT_OK, data
        for finding in findings:
            first, *rest = finding.message.split("\n")
            print(f"{finding.kind}: {first}")
            for line in rest:
                print(f"    {line}" if line else "")
        if any(f.kind in SYNCABLE for f in findings):
            print("\nTo make or update everything that is missing or changed, upstream first, run useful-views sync.")
        return EXIT_PROBLEMS, data
    if args.command == "sync":
        result = sync(all=args.all)
        if result.removed:
            print("Removed the records of deleted recipes: " + ", ".join(f"recipes/{n}" for n in result.removed) + ".")
        if result.dropped:
            print("Removed views no recipe declares any more: " + ", ".join(result.dropped) + ".")
        if result.deleted:
            print("Deleted: " + ", ".join(result.deleted) + ".")
        if result.ran:
            print("Ran, in order: " + ", ".join(f"recipes/{n}" for n in result.ran) + ".")
        if not (result.removed or result.dropped or result.ran):
            print("Everything is in sync: nothing to remove or run.")
        else:
            print("Updated datapackage.json and useful-views/README.md.")
        return EXIT_OK, {"ok": True, **result.to_dict()}
    if args.command == "rules":
        print(describe_rules())
        return EXIT_OK, {"ok": True, "rules": [r._asdict() for r in RULES.values()]}
    raise AssertionError(args.command)


if __name__ == "__main__":
    sys.exit(main())
