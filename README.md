# rkm

Repository knowledge management: the specs, decisions, roadmaps, todos, bugs
and process notes that contributors and agents work from, kept on a
repository's own `_rkm` branch, out of the code tree, as an `rkm/`
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

At the root of a jj or Git repository:

```bash
rkm config --workspace-root ~/rkm   # optional, once per machine
rkm init
```

`init` creates an orphan `_rkm` knowledge branch (a jj workspace and
bookmark, else a Git worktree) and starts the bundle on it: `rkm/index.md`,
`log.md` and `config.toml` with `name` and `requires`. The checkout goes under
the global workspace root when one is saved, else beside the repository in
`../<repo>-rkm`; `rkm path` prints where. `init` also writes a short bootstrap
section to `AGENTS.md` for you to review and commit with the code.
[Separate knowledge history](#separate-knowledge-history) has the details.

To keep knowledge in the code tree instead, committed with the code:

```bash
rkm init --in-tree
```

This creates `rkm/` in the checkout and prints the bootstrap section without
writing it. `rkm migrate` moves such a bundle to the knowledge branch later.

Use `rkm agents --write` to add or refresh the managed section; `rkm agents`
prints the marked block without editing the file. `rkm conventions` prints
the full RKM rules and command examples. Bring existing notes in with
`rkm adopt <path> <kind>`: a file outside `rkm/` moves in, and links to it
are rewritten.

When an item was asked for somewhere else, such as a Slack thread, a chat or
another repository, record it with `--from` on `new` or `adopt`: a link, or a
`prefix:name` label like `app:slack` or `repo:org/name`. Each later place that
asks for the same thing is added with `rkm from <id> <origin>`, so `from`
lists every requester in order and the log dates each one.

The other direction is a Request: what this repo wants from another repo or
team. `rkm new request --title "…" --to repo:org/name` files one, `rkm state
<id> asked` marks it once it has been put to them, and `rkm close` ends it
with a note saying whether it was delivered or declined.

## Separate knowledge history

Optionally, and recommended, save a global parent directory for RKM knowledge
workspaces once, so every project's knowledge sits side by side in one folder:

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

By default knowledge lives on a separate `_rkm` history, out of the code
tree and its commit ancestry:

```bash
rkm init                         # under the global parent, else ../widgets-rkm
rkm init --workspace ../widgets-knowledge   # a location of your choosing
rkm init --branch notes          # another bookmark/branch name

# Move an existing in-tree bundle, including uncommitted notes.
rkm migrate
rkm migrate --workspace ../widgets-knowledge
```

In a jj repository, rkm creates a native jj workspace based on `root()` and
an `_rkm` bookmark. Otherwise it creates a Git worktree on an orphan `_rkm`
branch. The workspace contains only the `rkm/` bundle. Use `--branch NAME`
to choose another bookmark/branch name. The workspace must be a new directory
outside the code checkout and repository metadata.
Without a global parent or `--workspace PATH`, the checkout goes in a sibling
of the main checkout that owns the repository metadata, named `<repo>-rkm`.
Saving a global parent does not migrate any repository or move an existing
checkout.

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
rkm commit --summary "record investigation"
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
for changes. This short bootstrap includes actor setup and directs agents to
`rkm conventions` for the full usage rules, keeping the command reference out
of each repository's instruction file. It contains no machine-specific paths.
Review and commit this file with the code.

The bootstrap makes external storage explicit and shows how to locate, search
and inspect the bundle using its actual backend (jj or Git). It warns that a
knowledge commit includes all pending bundle changes, including edits from
other sessions, and asks agents to close completed todos/bugs with a reference
to the implementing code commit/change. `rkm conventions` explains document
lifecycle commands separately from VCS status and gives the detailed workflow.

Existing project instructions are preserved. An untouched section previously
pasted from `rkm conventions` is replaced; customized legacy instructions
are retained, with the new section explaining how their RKM references resolve.
Running `rkm agents --write` or `rkm init` again refreshes the managed section without
duplicating it. Malformed or duplicate markers are refused before migration.
`rkm init --in-tree` prints the same bootstrap for manual setup;
`rkm agents --write` can install it without changing the storage mode.

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
The managed AGENTS.md block declares that branch; the first ordinary RKM
operation, or `rkm init`, attaches its workspace automatically, under the
global workspace parent or beside the clone. To choose another location, use
`rkm workspace add --workspace <chosen-directory>` or
`rkm init --workspace <chosen-directory>`. Each clone has its own location. If the knowledge branch is unavailable, rkm asks you to fetch
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

[types.Incident]                 # a new type, or a change to a default one
dir = "incidents"                # where `rkm new incident` puts it; none: adopt only
section = "Open incidents"       # its index section
tracked = true                   # carries state: open|done
states = ["open", "mitigated", "done"]  # optional; from open to done
template = "## Impact\n"         # body of a new one
about = "Outages and their follow-up."  # its row in `rkm conventions`
```

The default types are Spec, Decision, Roadmap, Bug, Todo, Request, Process
and Guide; `DEFAULT_TYPES` in `rkm.py` lists their settings. rkm acts only on
the ends of a type's `states`: `new` sets `open` and `close` sets `done`.
`rkm state <id> <state>` moves an item through any states between.

## Knowledge commit messages

Conventional Commits are the default recommendation: `docs(todo): ...`,
`docs(bug): ...`, `docs(spec): ...`, and `docs(rkm): ...` for mixed changes.
Recording a bug is a documentation change; `fix` describes fixing code.

```bash
rkm commit --summary "record parser cleanup task"  # infer kind from changed documents
rkm commit --summary "clarify grammar" --kind spec # choose the scope explicitly
rkm commit -m "TEAM-123 docs(spec): clarify grammar" # complete message, passed unchanged
```

Changes to the generated index/log do not affect inferred kinds. Mixed kinds,
configuration changes, or changes that cannot be classified use `rkm`.
The summary is a single line; complete `-m` messages may include bodies and
trailers. `--kind` is only used with `--summary`.

Set a repository policy in the bundle's `rkm/config.toml`, raising its top-level
`requires` to `"0.3"` when using this configuration:

```toml
[commits]
template = "docs(rkm): [{kind}] {summary}"
guidance = "Start the summary with an issue key, for example TEAM-123."
pattern = 'docs\(rkm\): \[[a-z][a-z0-9_-]*\] TEAM-[0-9]+ .+'
```

`template` defaults to `docs({kind}): {summary}`. It accepts only `{kind}`
and `{summary}`, without format modifiers, and must include `{summary}`.
`guidance` is shown by `rkm conventions` for users and agents. The optional
`pattern` is a Python regular expression matched against the entire message
with `re.fullmatch`; include multiline handling if your policy allows bodies.
Repository policy takes precedence over the default recommendation.

Generated and explicit messages are validated before RKM commits them.
Initialization and migration use the same policy with kind `rkm`; their first
commit can be overridden with `init -m MESSAGE` or
`migrate -m MESSAGE`. An existing knowledge branch is attached without a new
initialization commit. Direct jj/Git commits bypass RKM validation. Remote
rules still apply and may use different regex syntax or additional constraints.
For in-tree bundles, commit through your normal VCS using the policy shown
by `rkm conventions`.

## Knowledge push guidance

RKM never pushes automatically. Its default guidance is to push only when
explicitly authorized. Override the guidance with a top-level key in the
bundle's `rkm/config.toml` (before any table headers):

```toml
requires = "0.3"
push_policy = "Leave knowledge pushes to a human."
```

`push_policy` must be a nonempty single-line string. Both `rkm agents` and
`rkm conventions` show this policy; it is guidance for contributors and agents,
not enforcement of direct VCS commands. Refresh an existing managed block with
`rkm agents --write` after changing the policy.

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
