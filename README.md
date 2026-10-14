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

## Optional separate knowledge history

Save a global parent directory for RKM knowledge workspaces once:

```bash
rkm config --workspace-root /your/chosen/rkm-workspaces
rkm config                       # show the preference and its config file
```

This is stored in `~/.config/rkm/config.toml`, or
`$XDG_CONFIG_HOME/rkm/config.toml` when set, separately from the uv tool
installation. Only RKM knowledge workspaces use it. Each repository/clone
gets a subdirectory named `<repo>-<identifier>` to avoid collisions between
same-named repositories. All code workspaces belonging to one repository
share its saved knowledge location. Changing or clearing the global setting
does not move any existing workspace. Clear it with
`rkm config --unset-workspace-root`.

The default remains an in-tree `rkm/` bundle. To keep knowledge out of the
code tree and its commit ancestry, opt into a separate `_rkm` history:

```bash
# Use the saved global parent.
rkm init --branch
rkm migrate                      # for an existing in-tree bundle

# Or override the global parent for this repository.
rkm init --branch --workspace ../widgets-knowledge

# Or move an existing in-tree bundle, including uncommitted notes.
rkm migrate --workspace ../widgets-knowledge
```

In a jj repository, rkm creates a native jj workspace based on `root()` and
an `_rkm` bookmark. Otherwise it creates a Git worktree on an orphan `_rkm`
branch. The workspace contains only the `rkm/` bundle. Use `--branch NAME`
to choose another bookmark/branch name. The workspace must be a new directory
outside the code checkout and repository metadata.
Without a global parent, `--workspace PATH` is required. Saving a global parent
does not automatically enable branch storage or migrate any repository.

The chosen absolute location, backend and branch are saved in
`rkm-storage.json` in the shared repository metadata (the Git directory,
located through jj when applicable). This preference is local to the clone,
shared by its code workspaces/worktrees, and never committed or pushed.
Commands from code subdirectories automatically use that bundle. Commands
from the knowledge workspace use the code checkout recorded during setup
for link checks and rewrites.

```bash
rkm path                        # absolute bundle path, for reading/editing bodies
rkm new todo investigate --title "Investigate" --description "Why."
rkm commit -m "Record investigation"
```

Initialization and migration record the first knowledge commit. Subsequent
commands leave changes for review; `rkm commit` records them in the knowledge
workspace, advances the jj bookmark, and starts a fresh working-copy change
(or makes a Git commit). It never commits the code checkout or pushes anything.
Push the knowledge bookmark/branch from its workspace using your usual VCS.

Migration copies and commits the bundle before removing its in-tree copy.
It refuses unresolved conflicts, an occupied destination, or an existing
knowledge branch that it would overwrite. Review and commit the `rkm/`
deletions in the code checkout yourself. Branch initialization and migration
also create or update `AGENTS.md` in that checkout with a section delimited by
`<!-- rkm:begin -->` and `<!-- rkm:end -->`. It tells agents to run `rkm path`,
read the bundle's index and relevant process/spec documents, and use the tool
for changes. The section is generated from `rkm conventions`; it contains no
machine-specific directory paths. Review and commit this file with the code.

Existing project instructions are preserved. An untouched section previously
pasted from `rkm conventions` is replaced; customized legacy instructions
are retained, with the new section explaining how their RKM references resolve.
Running `rkm init --branch` again refreshes the managed section without
duplicating it. Malformed or duplicate markers are refused before migration.
The default in-tree `rkm init` continues to print conventions for manual setup.

Older code branches retaining `rkm/` must reconcile and remove that copy;
rkm refuses to silently choose between two bundles. Existing code history
is not rewritten.

The knowledge is the project's current state across code branches. Distinguish
draft plans from implemented behaviour; keep essential checkout-specific
process instructions in `AGENTS.md`. This mode does not restore matching
knowledge when you check out an older code revision.

Relative links within the bundle remain ordinary Markdown links. Links between
code and knowledge retain the logical in-tree layout (`rkm/...` from code,
`../../README.md` from a spec), so existing references stay portable and
`rkm check`, `mv`, `rm` and `adopt` work across both locations. Ordinary
Markdown viewers may not resolve links crossing that boundary; use `rkm path`
to locate knowledge files. `new` and `mv` print physical paths in branch mode.

RKM commands sharing a bundle use an OS lock so concurrent log/index writes
do not overwrite each other. Coordinate manual body edits and VCS operations
in the shared workspace. Stop other edits while migrating.

Manage the knowledge checkout through the CLI:

```bash
rkm workspace remove             # clean checkout only; keep branch and location
rkm workspace add                # recreate at the saved location
rkm workspace add --workspace /another/location
```

Removal refuses uncommitted, untracked or ignored files. It retains the
knowledge history, local location preference and AGENTS.md declaration.
The next ordinary RKM operation automatically recreates an absent checkout
from the existing knowledge branch. A checkout switched to code history or
an occupied location is refused rather than overwritten.

For a new clone, fetch the knowledge branch/bookmark using your usual VCS.
The managed AGENTS.md block declares that branch; with a global workspace
parent configured, the first ordinary RKM operation attaches its workspace
automatically. Otherwise use `rkm workspace add --workspace <chosen-directory>`
or `rkm init --branch --workspace <chosen-directory>`. Each clone chooses its
own location. If the knowledge branch is unavailable, rkm asks you to fetch
it; it never creates an empty replacement for missing knowledge history.

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
