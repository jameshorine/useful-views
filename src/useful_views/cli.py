"""The useful-views command: a thin wrapper over the Python functions."""

from __future__ import annotations

import argparse
import sys
import warnings

from . import check, init, new_recipe, run, sync
from .errors import UsefulViewsError, UsefulViewsWarning

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_FAILED = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="useful-views",
        description="Turn raw data into documented derived tables (useful views).",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    p = commands.add_parser("init", help="create the project structure (safe to run twice)")
    p.add_argument("path", nargs="?", default=".", help="project folder (default: the current folder)")
    p = commands.add_parser("new-recipe", help="create recipes/<name>/ from a template")
    p.add_argument("name")
    p = commands.add_parser("run", help="run one recipe and record what it produced")
    p.add_argument("recipe")
    commands.add_parser("check", help="report the state of the project")
    p = commands.add_parser(
        "sync", help="make the views match the recipe folders: run what is missing or changed, remove the rest"
    )
    p.add_argument("--all", action="store_true", help="run every recipe again, upstream first")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    with warnings.catch_warnings():
        original = warnings.showwarning

        def show(message, category, filename, lineno, file=None, line=None):
            if issubclass(category, UsefulViewsWarning):
                print(f"warning: {message}", file=sys.stderr)
            else:
                original(message, category, filename, lineno, file, line)

        warnings.simplefilter("always", UsefulViewsWarning)
        warnings.showwarning = show
        try:
            return _dispatch(args)
        except UsefulViewsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FAILED
        except Exception as exc:  # an unexpected failure: still readable, still exit 3
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_FAILED


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init":
        folder = init(args.path)
        print(f"useful-views project ready at {folder}")
        return EXIT_OK
    if args.command == "new-recipe":
        folder = new_recipe(args.name)
        print(f"Created {folder}. Edit its recipe.toml and 01_first_step.py, then run: useful-views run {args.name}")
        return EXIT_OK
    if args.command == "run":
        result = run(args.recipe)
        print(f"Ran recipes/{result.recipe}. Wrote {', '.join(result.outputs)}.")
        print("Updated datapackage.json and useful-views/README.md.")
        return EXIT_OK
    if args.command == "check":
        findings = check()
        if not findings:
            print("No problems found.")
            return EXIT_OK
        for finding in findings:
            print(f"{finding.kind}: {finding.message}")
        return EXIT_PROBLEMS
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
        return EXIT_OK
    raise AssertionError(args.command)


if __name__ == "__main__":
    sys.exit(main())
