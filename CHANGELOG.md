# Changelog

## Unreleased

- **`rkm verify` is gone.** Nothing read its sign-off, and a later edit didn't
  clear it, so a document could look reviewed after an agent rewrote it. An
  existing `verified` field is kept as written and still passes `check`.

## 0.4.0 — 2026-11-22

Merging

- **rkm no longer writes `log.md`.** Every command inserted its entry at the
  top of the file, so two clones recording knowledge at once conflicted on
  every merge. Version control records each change instead. An existing
  `log.md` is left alone as history (and still checked); delete it if you
  don't want it. Without it, `init` starts a bundle with `index.md` and
  `config.toml`.
- `rkm close --note` keeps the note in `closed`, e.g.
  `closed: { by: …, at: …, note: "landed in a1b2c3" }`. `--note` is gone from
  `reopen`, `state`, `touch` and `rm`; put the context in the commit message.
- `rkm index` regenerates `index.md` even when it holds conflict markers, so
  running it resolves an index conflict after a merge. It refuses while
  another bundle file still has conflicts, rather than build an index that
  leaves out that document.
- `rkm check` reports conflict markers in any bundle file.

Upgrading

- Nothing in a bundle needs converting. Once everyone has upgraded, raise
  `requires` to `"0.4"` in `rkm/config.toml`, so an older rkm refuses the
  bundle instead of adding to `log.md` again, and delete `rkm/log.md` if you
  don't want a log that stops at the upgrade.
- Drop `--note` from scripts that pass it to `reopen`, `state`, `touch` or
  `rm`.

## 0.3.0 — 2026-10-17

Storage

- **Branch storage is the default.** Plain `rkm init` creates the orphan
  `_rkm` knowledge branch (a jj workspace, else a Git worktree) and writes
  the AGENTS.md section; `--branch NAME` still picks another name. In a new
  clone it attaches the branch AGENTS.md declares. The old in-tree `rkm/`
  folder is opt-in with `rkm init --in-tree`; outside a jj or Git repository
  plain `init` refuses and suggests it. `--branch` with no name is still
  accepted.
- With no global workspace root and no `--workspace`, `init`, `migrate`,
  `workspace add` and new-clone setup check knowledge out beside the
  repository, in `../<repo>-rkm`. The global root stays optional and
  recommended; `rkm config` no longer says `--workspace` is needed without
  it.
- `init` in an existing repository and `agents --write` report the exact
  AGENTS.md path and whether the managed section was updated or was already
  current.

Documents

- Request is a default type (`rkm/requests/`, "Open requests"): what this repo
  wants from another repo or team. `new --to repo:org/name` names who, `set
  <id> to` changes it, and its states are `open`, `asked`, `done`. A bundle
  that already defines Request keeps its settings, but its "Open requests"
  section moves after "Open todos": run `rkm index` once after upgrading, or
  `check` reports the index out of date.
- A type may list its own `states` in config, from `open` to `done`.
  `rkm state <id> <state>` moves an item through the states between; `check`
  and `adopt --state` accept the type's states.
- Items record where they were asked for in an optional `from` list: links or
  `prefix:name` labels such as `app:slack` or `repo:org/name`. `new` and
  `adopt` take `--from` for the first; `rkm from <id> <origin>` adds each later
  requester without restamping `generated`, and logs it as a **Request**.
  `check` validates the entries and refuses duplicates.
- `new` derives the slug from `--title` when none is given (lower-case ASCII,
  hyphenated, cut at a word boundary near 50 characters). A derived slug that
  is already taken is refused rather than numbered; pass one explicitly.
- `description` is optional for people: `new` and `adopt` accept no
  `--description`, `check` accepts its absence, and the index line drops its
  ` - `. Agents must still pass one.
- `ls` hides closed (`state: done`) and deprecated documents by default, in
  the table and in `--json`. `--all` (`-a`) shows everything, and
  `--state done` or `--status deprecated` still list those documents. On a
  terminal, a note on stderr counts what was hidden.

Commits and agents

- Recommend Conventional Commits for knowledge history. `commit --summary`
  generates a message with an inferred kind (or `--kind`); complete `-m`
  messages remain available.
- Repository `[commits]` configuration supplies a template, guidance and an
  optional validation pattern, including for initialization/migration commits.
  Initial messages can be overridden with `-m`. Policy is shown by `conventions`.
- Add repository `push_policy` guidance, shown in both agent instructions and
  conventions; the default is to push only when explicitly authorized.
- The agent bootstrap and `rkm conventions` say plainly to commit knowledge
  changes with `rkm commit` before finishing, in the session that made them.
  In-tree bootstraps say to commit `rkm/` with the code it describes.
- The short agent bootstrap explicitly directs todos, bugs, specs, decisions,
  roadmaps, process notes and contributor guides into RKM, and clarifies
  external, shared knowledge storage: backend-specific locate/search/status
  commands, the shared-commit warning, completion references, and checkout
  recovery.
- Expand conventions with document-lifecycle versus workspace-status guidance
  and the detailed shared-workspace workflow.
- Refresh existing AGENTS.md blocks with `rkm agents --write` after
  upgrading.

## 0.2.0 — 2026-10-13

- Opt-in separate knowledge history: `init --branch --workspace PATH` and
  `migrate --workspace PATH`, using a native jj workspace when available,
  otherwise a Git worktree on an orphan branch. The default name is `_rkm`.
- Remember the chosen location in shared local repository metadata, so code
  workspaces use one current-state bundle. The in-tree default is unchanged.
- `config --workspace-root PATH` saves a global RKM workspace parent in the
  user config directory. Each repository gets its own directory; an explicit
  `--workspace` overrides it and existing locations stay put.
- `path` locates the bundle; `commit -m MESSAGE` records knowledge separately.
- Serialize shared-bundle commands; preserve logical links across code and
  knowledge locations. Migration commits a copy before removing the original.
- Branch initialization and migration manage an RKM section in the code
  checkout's `AGENTS.md`, preserving other instructions and giving agents a
  portable way to discover the external knowledge bundle.
- Keep that section short, referring agents to `rkm conventions` for the full
  rules. `rkm agents` prints the managed bootstrap; `rkm agents --write`
  updates it while preserving project-specific instructions in either mode.
- `workspace add` and `workspace remove` manage knowledge checkouts, retaining
  history and preferences and refusing removal with uncommitted files. Missing
  checkouts are recreated from the existing branch on use; a new clone can
  discover the branch from AGENTS.md and use its configured global parent.

## 0.1.0 — 2026-10-12

First release. Needs Python 3.11+
(`tomllib`).

- rkm lives in this repository as one file, `rkm.py`, and installs with
  `uv tool install`, which also supplies Python 3.11+.
- Every repository-specific setting is in `rkm/config.toml`: project name,
  index intro, the types a bundle uses (directory, index section, tracked,
  template), skipped paths, extra text extensions, and `requires`, the oldest
  rkm that reads the bundle. Unknown keys are refused.
- `adopt` moves a file in from outside `rkm/`, rewriting links to it.
- `init` starts a bundle.
- `conventions` prints the rules as a section for `AGENTS.md`, with the
  bundle's own types.
- The repository is found from `RKM_ROOT` or the current directory upwards.
- `ls` sizes its columns to fit and prints a header in a terminal (none when
  piped). STATE shows a status only when it isn't `stable`.
