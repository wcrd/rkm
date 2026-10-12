# Changelog

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
