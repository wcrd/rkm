# rkm

Repository knowledge management: the specs, decisions, roadmaps, todos, bugs
and process notes that contributors and agents work from, kept in a
repository's `rkm/` directory as an
[Open Knowledge Format v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundle, and the `rkm` tool that owns its structure.

`rkm` is one file, `rkm.py`, of Python 3.11+ standard library. Read it before
you trust it with your files; that is why it is one file.

## Install

With [uv](https://docs.astral.sh/uv/), from a clone of this repository:

```bash
uv tool install ~/code/rkm
```

uv installs a snapshot of rkm with a Python it is happy with (3.11 or newer,
fetched if need be) and puts `rkm` on PATH. The snapshot is separate from the
clone: editing or checking out other commits in the clone changes nothing
until you reinstall. `python3 rkm.py …` runs the clone in place without
installing.

## Updating

Pull this repository, then reinstall:

```bash
uv tool install --reinstall ~/code/rkm
```

Every repository uses the new version at once; `rkm --version` says which you
have. (Installed from a git URL instead of a clone, `uv tool upgrade rkm` does
both steps.)

A bundle's `rkm/config.toml` names the oldest rkm it works with:

```toml
requires = "0.1"
```

An older rkm refuses to touch that bundle and says to pull. A newer one always
works. Raise `requires` in a repository when it starts relying on something a
newer rkm added.

## Starting a bundle

At the root of a repository:

```bash
rkm init
```

`init` creates `rkm/` (`index.md`, `log.md`, `config.toml` with `name` and
`requires`) and prints a "Where knowledge lives" section for `AGENTS.md`.
Paste it in; `rkm conventions` prints it again. Bring existing notes in with
`rkm adopt <path> <kind>`: a file outside `rkm/` moves in, and links to it
are rewritten.

## Configuration

`rkm/config.toml` holds what differs between repositories. Every key is
optional; an unknown key is an error, so a typo doesn't pass silently.

```toml
name = "widgets"                 # project name in the default index intro
intro = """
Replaces the default index intro.
"""
requires = "0.1"                 # the oldest rkm that reads this bundle
skip = ["vendor", "tests/goldens"]   # trees `mv` and `check` don't read
text_extensions = [".okd"]       # further files whose links `mv` rewrites

[types.Request]                  # a new type, or a change to a default one
dir = "requests"                 # where `rkm new request` puts it; none: adopt only
section = "Open requests"        # its index section
tracked = true                   # carries state: open|done
template = "## Want\n"           # body of a new one
about = "Asks from users."       # its row in `rkm conventions`
```

The default types are Spec, Decision, Roadmap, Bug, Todo, Process and Guide;
`DEFAULT_TYPES` in `rkm.py` lists their settings.

## Developing rkm

```bash
python3 -I -m unittest discover -s tests
uv tool install --reinstall --editable .   # `rkm` follows this checkout as you edit
```

Go back to a plain install (without `--editable`) when you're done, so a
half-finished edit isn't what every repository runs.

Run the tests on 3.11 as well as a current Python. rkm refuses to start on
anything older (macOS's own `python3` is 3.9).

To release: bump `VERSION` in `rkm.py` (the package version reads it) and add a `CHANGELOG.md` entry. Bump the
minor version when a bundle could come to rely on the change, so repositories
can raise `requires` to it.
