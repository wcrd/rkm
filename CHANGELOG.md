# Changelog

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
