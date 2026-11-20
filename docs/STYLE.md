# Writing the docs

How the useful-views docs are written. The model is
[dplyr's documentation](https://dplyr.tidyverse.org/): short pages, an
example first, and real output. This file is not published.

## Voice

- Open each page with one or two sentences: what it covers and when you need
  it. Then show an example.
- Show, then tell. Give the code and its real output, then one to three
  sentences on what to notice.
- Write one idea per sentence. Keep most sentences under 20 words.
- Use "you", the present tense and the active voice.
- Use a list for anything with three or more parts. Don't chain clauses with
  colons or semicolons.
- Name a task or a thing in each heading: "Write a recipe", "Paths".
- Explain why in one short paragraph at most, after the how. Leave it out if
  the reader doesn't need it to act.
- Keep the owner's own informal lines, such as "R users please reach out!".

Avoid these:

- filler openers: "So,", "That is,", "Note that", "Simply", "just";
- "X, not Y" refrains;
- em-dash asides;
- rhetorical triplets;
- a closing paragraph that restates the page.

## One home for each fact

Explain each topic once, on its home page. Elsewhere, link to it in a
sentence. The README, `SKILL.md` and the project templates may summarise in
a line or two, then link.

| Topic | Home |
|---|---|
| Installing, environments, notebooks | `installation.md` |
| The walkthrough, the words, who writes what | `quickstart.qmd` (Get started) |
| Project layout, what to commit, `.gitignore` | `concepts/projects.md` |
| `recipe.toml`, TOML quoting, paths | `concepts/recipes.md` |
| Step names, how a step runs, "Steps are code" | `concepts/steps.md` |
| `sources.toml` | `concepts/sources.md` |
| What `run`, `sync` and `check` do; what to run after a change | `concepts/running.md` |
| Stale views and what is caught today | `concepts/stale-views.md` |
| File hash and data hash, hands on | `guides/fingerprints.qmd` |
| `datapackage.json` and the catalog | `concepts/records.md` |
| `cookbook.json` and its CI job | `concepts/cookbook.md` |
| Using it from Python, `root()` | `guides/python.qmd` |
| `AGENTS.md`, `--json` output | `guides/ai-tools.md` |
| How it compares with other tools | `guides/compare.md` |
| Commands, options, exit codes | `reference/cli.md` (generated) |
| Rules | `reference/rules.md` (generated) |
| Check findings | `reference/check-findings.md` (generated) |
| Environment variables, signals, the lock | `reference/environment.md` |
| The data hash specification | `reference/data-hash.md` |

Planned work lives in GitHub issues, not in the docs. Link the issue.

## Length

| Page | Words of prose |
|---|---|
| README.md | about 600 |
| Get started | about 1,200 |
| Other guide pages | about 800 |
| Reference pages | as long as the facts need, in tables |

## Examples

- Use the pwt example wherever you can.
- Show real output. Pages with output are `.qmd` files. Quarto runs their
  code when the site is built, so the output is always current.
- Start each `.qmd` page with the hidden setup cell from an existing page.
  It reads the example project from `tests/data` (offline) and works in a
  fresh temporary folder.
- Show a command in an `sh` block. Put a cell with `#| echo: false` after it
  that runs the same command with `sh()`. `sh()` runs `useful-views`, `cd`
  and `cat`, and fails if a command exits with an unexpected code: pass
  `expect=1` for `check` with findings, or `expect=3` for a refusal.
- Never print a `Path`: it shows the temporary folder. `sh()` replaces that
  folder with `/path/to`.
- When the prose says what the output shows, add a hidden cell
  (`#| include: false`) that asserts it, so the build fails if it stops
  being true.
- `tests/test_docs_pages.py` runs every `.qmd` page's cells, so a broken
  example fails the tests before it fails the docs build.
- Write a docstring example as a doctest (`>>>` and the output) inside a
  ```` ```python ```` fence. Without the fence, great-docs splits the output
  from its prompt. `tests/test_docstring_examples.py` runs each one in a
  synced copy of the pwt example.
- Keep the README's Usage section a real run, with its real output.

## Reference pages

Reference pages hold tables and definitions, with no story. Each public
function's docstring:

- starts with a summary of 10 words or fewer, which is its line in the
  index;
- gives its command-line form;
- keeps Args, Returns and Raises short;
- ends with an `Examples:` section.
