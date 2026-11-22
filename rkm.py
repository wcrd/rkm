#!/usr/bin/env python3
"""rkm — create, change and check the knowledge in rkm/.

rkm/ is an Open Knowledge Format v0.2 bundle of a repository's contributor and
agent knowledge (`rkm conventions` prints the rules). This tool is the only
way its frontmatter, file names and index change; bodies are edited by hand.

One file, Python 3.11+ standard library only. It lives in its own repository
and is installed from there with `uv tool install`; `python3 rkm.py` runs it
in place. Each repository has its own rkm/config.toml.

  rkm init [--name N] [--branch B] [--workspace PATH]
                                  start a bundle on _rkm (jj preferred, else git)
  rkm init --in-tree [--name N]   start a bundle in rkm/, committed with the code
  rkm migrate [--workspace PATH]  move an in-tree bundle to _rkm
  rkm path                        print the bundle's absolute directory
  rkm workspace add [--workspace PATH]  reattach existing knowledge history
  rkm workspace remove            remove a clean checkout, retaining history
  rkm config [--workspace-root PATH]  view or save the global knowledge workspace parent
  rkm commit -m MESSAGE | --summary TEXT [--kind KIND]
                                  commit knowledge using repository policy
  rkm new todo|bug|request|spec|decision|roadmap|process [<slug>] --title T
          [--description D] [--from ORIGIN] [--to LABEL]
                                   the slug defaults to one made from the title
  rkm adopt <path> <kind> [--description D] [--as ID] [--from ORIGIN]
                                   frontmatter for a file without it; a file
                                   outside rkm/ moves in, its links rewritten
  rkm close <id> [--note N]        state: done, closed stamped
  rkm reopen <id>
  rkm state <id> <state>           a state between open and done, e.g. asked
  rkm retype <id> <type>           the file stays where it is
  rkm status <id> draft|stable|deprecated
  rkm set <id> title|description|tags|to <value>
  rkm touch <id>                   restamp `generated` after rewriting the body
  rkm from <id> <origin>           record another place that asked for it
  rkm verify <id>                  human sign-off; refused for agents
  rkm mv <id> <new-id>             rename and rewrite every link to it
  rkm rm <id>                      delete; refused while anything links to it
  rkm ls [--type T] [--state S] [--status S] [--all] [--json]
                                  closed and deprecated are hidden without
                                  --all or a --state/--status asking for them
  rkm index                        regenerate rkm/index.md
  rkm check                        conformance; run it as a CI gate
  rkm conventions                  the full RKM rules and command examples
  rkm agents [--write]              print the short bootstrap or update AGENTS.md

An <id> is a concept id (`todos/regex-engine-swap`), a path
(`rkm/todos/regex-engine-swap.md`) or a bare slug when it is unique.

The repo is RKM_ROOT, else the nearest directory at or above the working
directory with an rkm/ bundle, else the jj or Git root. Branch mode, the
default, checks the bundle out under the global workspace root, else beside
the repository (`../<repo>-rkm`), and remembers its location locally.

rkm/config.toml, optional, holds what differs between repositories:

  name             project name in the default index intro (default: directory name)
  intro            index intro, replacing the default
  requires         the oldest rkm the bundle works with, e.g. "0.1"
  skip             repo paths that `mv` and `check` don't read
  text_extensions  further extensions whose links `mv` rewrites
  [types.<Name>]   add a type or change a default one: dir (where `rkm new`
                   puts it; none means adopt only), section (in the index),
                   tracked (carries `state`), states (its states, from open
                   to done; default open, done), template (new body), about
                   (its row in `rkm conventions`)

Writes are stamped with an actor: RKM_ACTOR, else `human:$USER`. An agent
must set RKM_ACTOR to `<agent>/<model>`, e.g. `claude/opus-5.5`.
"""
import argparse
import contextlib
import datetime
import getpass
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import unicodedata

if sys.version_info < (3, 11):
    sys.exit(f"rkm needs Python 3.11 or newer; this is {sys.version.split()[0]}")

import tomllib  # noqa: E402  (3.11+)

VERSION = "0.3.0"


def has_bundle(d):
    return any(os.path.isfile(os.path.join(d, "rkm", f)) for f in ("config.toml", "index.md", "log.md"))


def find_repo():
    if os.environ.get("RKM_ROOT"):
        return os.path.abspath(os.environ["RKM_ROOT"])
    d = os.getcwd()
    while True:
        if has_bundle(d):
            return d
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    # No bundle anywhere: where `rkm init` would start one.
    for cmd in (["jj", "--ignore-working-copy", "root"], ["git", "rev-parse", "--show-toplevel"]):
        try:
            return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return os.getcwd()


REPO = find_repo()


def git(*args, cwd=None, check=True):
    result = subprocess.run(["git", "-C", cwd or REPO, *args], capture_output=True, text=True)
    if check and result.returncode:
        raise Fail(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result


def jj(*args, cwd=None, check=True, snapshot=False):
    cmd = ["jj", "--no-pager", "--color", "never"]
    if not snapshot:
        cmd.append("--ignore-working-copy")
    result = subprocess.run(cmd + ["-R", cwd or REPO, *args], cwd=cwd or REPO,
                            capture_output=True, text=True)
    if check and result.returncode:
        raise Fail(result.stderr.strip() or result.stdout.strip() or f"jj {' '.join(args)} failed")
    return result


def repository_backend():
    """Prefer jj. Its Git directory also identifies the repo across workspaces."""
    try:
        result = jj("git", "root", check=False)
        if result.returncode == 0:
            return "jj", os.path.realpath(result.stdout.strip())
    except OSError:
        pass
    try:
        result = git("rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
        if result.returncode == 0:
            return "git", os.path.realpath(result.stdout.strip())
    except OSError:
        pass
    return "", ""


BACKEND, REPO_META = repository_backend()
STORAGE_FILE = os.path.join(REPO_META, "rkm-storage.json") if REPO_META else ""
try:
    with open(STORAGE_FILE, encoding="utf-8") as f:
        STORAGE = json.load(f)
except FileNotFoundError:
    STORAGE = {}
except (OSError, ValueError) as e:
    sys.exit(f"rkm: cannot read storage preference: {e}")
if not isinstance(STORAGE, dict) or (STORAGE and (
        STORAGE.get("backend") not in ("jj", "git") or
        not all(isinstance(STORAGE.get(k), str) and STORAGE[k]
                for k in ("branch", "workspace", "code_root")) or
        not all(os.path.isabs(STORAGE[k]) for k in ("workspace", "code_root")))):
    sys.exit("rkm: invalid storage preference in " + STORAGE_FILE)


BRANCH = STORAGE.get("branch", "")
STORE_ROOT = STORAGE.get("workspace", REPO)
if BRANCH and os.path.realpath(REPO) == os.path.realpath(STORE_ROOT):
    # Commands from the knowledge checkout use the code root recorded at setup.
    REPO = STORAGE["code_root"]
BUNDLE = os.path.join(STORE_ROOT, "rkm")
RESERVED = ("index.md", "log.md")

# The types a bundle may use, with the directory `rkm new` puts one in (none:
# adopt only), the index section that lists it, and whether it carries
# `state`. rkm/config.toml adds types or overrides these under [types.<Name>].
DEFAULT_TYPES = {
    "Spec": {"dir": "spec", "section": "Specs",
             "about": "How a feature behaves. Current unless its frontmatter says `status: draft`."},
    "Decision": {"dir": "decisions", "section": "Decisions",
                 "about": "Why the project is the way it is: one design call per file, numbered."},
    "Roadmap": {"dir": "roadmap", "section": "Roadmaps", "about": "Multi-phase plans."},
    "Bug": {"dir": "bugs", "section": "Open bugs", "tracked": True,
            "about": "Behaviour that is wrong, with a repro."},
    "Todo": {"dir": "todos", "section": "Open todos", "tracked": True,
             "about": "Planned work, one item per file."},
    "Request": {"dir": "requests", "section": "Open requests", "tracked": True,
                "states": ["open", "asked", "done"],
                "about": "What we want from another repo or team; `to` names who. "
                         "`asked` once it has been put to them."},
    "Process": {"dir": "process", "section": "Process", "about": "How to make a kind of change."},
    "Guide": {"section": "Guides"},
}
DEFAULT_TEMPLATES = {
    "Bug": "## Repro\n\n## Expected\n\n## Actual\n",
    "Todo": "## Why\n\n## What\n",
    "Request": "**Hit in:** where we ran into it.\n\n**Ask:** what we want changed.\n",
    # A decision says why something is the way it is, and what reopening it
    # would cost. Keep it short and link to the spec rather than restate it.
    "Decision": "**Decision.** One or two sentences.\n\n**Why.** The reasons, with links to the "
                "specs and code that carry them.\n\n**Not chosen.** The alternatives and why "
                "they lost.\n\n**Open.** Known gaps, inconsistencies, or follow-up work.\n",
}


CONFIG_KEYS = {"name", "intro", "requires", "skip", "text_extensions", "types", "commits", "push_policy"}
TYPE_KEYS = {"dir", "section", "tracked", "states", "template", "about"}
COMMIT_KEYS = {"template", "guidance", "pattern"}


def validate_commit_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("commits must be a table")
    unknown = set(policy) - COMMIT_KEYS
    if unknown:
        raise ValueError("unknown key(s) " + ", ".join("commits." + k for k in sorted(unknown)))
    for key, value in policy.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"commits.{key} must be a nonempty string")
    if "template" in policy:
        fields = []
        for _, field, spec, conversion in string.Formatter().parse(policy["template"]):
            if field is not None:
                if field not in ("kind", "summary") or spec or conversion:
                    raise ValueError("commits.template allows only {kind} and {summary}, without format modifiers")
                fields.append(field)
        if "summary" not in fields:
            raise ValueError("commits.template must contain {summary}")
    if "pattern" in policy:
        re.compile(policy["pattern"])


def load_config():
    path = os.path.join(BUNDLE, "config.toml")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"rkm: rkm/config.toml: {e}")
    # A misspelt key would otherwise be ignored without a word.
    unknown = set(config) - CONFIG_KEYS
    unknown |= {f"types.{t}.{k}" for t, v in config.get("types", {}).items() for k in set(v) - TYPE_KEYS}
    if unknown:
        sys.exit(f"rkm: rkm/config.toml: unknown key(s) {', '.join(sorted(unknown))}")
    try:
        for t, v in config.get("types", {}).items():
            st = v.get("states")
            if st is not None and not (isinstance(st, list) and len(st) >= 2 and st[0] == "open"
                                       and st[-1] == "done" and len(set(st)) == len(st)
                                       and all(isinstance(x, str) and re.fullmatch(r"[a-z][a-z0-9_-]*", x) for x in st)):
                raise ValueError(f"types.{t}.states must run from \"open\" to \"done\", "
                                 "each a distinct lower-case name")
        validate_commit_policy(config.get("commits", {}))
        if "push_policy" in config and (not isinstance(config["push_policy"], str)
                                       or not config["push_policy"].strip()
                                       or "\n" in config["push_policy"] or "\r" in config["push_policy"]):
            raise ValueError("push_policy must be a nonempty single-line string")
    except (ValueError, re.error) as e:
        sys.exit(f"rkm: rkm/config.toml: {e}")
    return config


def version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def configure_bundle():
    global CONFIG, TYPE_DEFS, TYPES, KINDS, TRACKED, TEMPLATES, SECTIONS, SKIP_PATHS, TEXT_EXT
    CONFIG = load_config()
    # Stop before touching a bundle requiring a newer reader.
    if "requires" in CONFIG and version_key(VERSION) < version_key(str(CONFIG["requires"])):
        sys.exit(f"rkm: this bundle needs rkm {CONFIG['requires']} or newer; this is {VERSION}. "
                 "Pull the rkm repository.")
    TYPE_DEFS = {t: dict(v) for t, v in DEFAULT_TYPES.items()}
    for t, v in CONFIG.get("types", {}).items():
        TYPE_DEFS.setdefault(t, {}).update(v)
    TYPES = set(TYPE_DEFS)
    KINDS = {t.lower(): (t, v["dir"]) for t, v in TYPE_DEFS.items() if v.get("dir")}
    TRACKED = {t for t, v in TYPE_DEFS.items() if v.get("tracked") or v.get("states")}
    TEMPLATES = {**DEFAULT_TEMPLATES,
                 **{t: v["template"] for t, v in TYPE_DEFS.items() if "template" in v}}
    SECTIONS = [v.get("section", t) for t, v in TYPE_DEFS.items()] + ["Closed", "Deprecated", "Other"]
    SKIP_PATHS = {p.strip("/") for p in CONFIG.get("skip", [])}
    TEXT_EXT = {".md", ".html", ".rs", ".py", ".ts", ".js", ".mjs", ".swift", ".go", ".sh",
                ".toml", ".json", ".yml", ".yaml", ".txt", *CONFIG.get("text_extensions", [])}


configure_bundle()
STATES = ("open", "done")


def states_of(t):
    """A tracked type's states. rkm acts only on the ends: `new` sets open,
    `close` done; any between are labels `rkm state` moves an item through."""
    return tuple(TYPE_DEFS.get(t, {}).get("states", STATES))
STATUSES = ("draft", "stable", "deprecated")
KEY_ORDER = ["type", "title", "description", "state", "status", "decided", "tags",
             "to", "from", "generated", "closed", "verified", "stale_after", "sources"]
QUOTED = {"title", "description"}
WRITES = {"type", "title", "description", "state", "status", "decided", "tags",
          "to", "from", "generated", "closed", "verified"}
BLOCK_LISTS = {"from", "verified"}

DATE = re.compile(r"^\d{4}-\d\d-\d\d$")

# Repo files whose links `rkm mv` rewrites and `rkm check` follows.
SKIP_NAMES = {".git", ".jj", "target", "node_modules"}

ACTOR = re.compile(r"^(human|process|team):[\w.@-]+$|^[\w.-]+/[\w.-]+$")
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:\d\d)$")
BARE = re.compile(r"^[A-Za-z0-9][\w.+-]*$")
# Where an item was asked for: a link (https://…) or a label (app:slack,
# repo:org/name). Either is a lower-case prefix, a colon and no spaces.
ORIGIN = re.compile(r"^[a-z][a-z0-9+.-]*:\S+$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Git and jj conflict markers at the start of a line.
CONFLICT = re.compile(r"^(<<<<<<< |>>>>>>> |%%%%%%% )", re.M)


class Fail(Exception):
    pass


def global_config_path():
    root = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(root, "rkm", "config.toml")


def global_config():
    path = global_config_path()
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise Fail(f"{path}: {e}") from e
    if set(config) - {"workspace_root"}:
        raise Fail(f"{path}: unknown global configuration key(s): "
                   + ", ".join(sorted(set(config) - {"workspace_root"})))
    if "workspace_root" in config and (not isinstance(config["workspace_root"], str)
                                       or not config["workspace_root"]
                                       or not os.path.isabs(os.path.expanduser(config["workspace_root"]))):
        raise Fail(f"{path}: workspace_root must be an absolute directory path")
    return config


def preferred_workspace():
    """Under the global workspace root if set, else beside the repository."""
    if os.path.basename(REPO_META) == ".git":
        owner = os.path.dirname(REPO_META)
    elif os.sep + ".jj" + os.sep in REPO_META:
        owner = REPO_META.split(os.sep + ".jj" + os.sep, 1)[0]
    else:
        owner = REPO_META
    name = re.sub(r"[^\w.-]", "-", os.path.basename(owner).removesuffix(".git")).strip(".-") or "repo"
    root = global_config().get("workspace_root")
    if not root:
        # Code workspaces share the checkout beside the repository that owns them.
        return os.path.join(os.path.dirname(owner), f"{name}-rkm")
    # Stable across code workspaces, distinct for same-named repos and clones.
    suffix = hashlib.sha256(os.fsencode(REPO_META)).hexdigest()[:12]
    return os.path.join(os.path.realpath(os.path.expanduser(root)), f"{name}-{suffix}")


def cmd_config(a):
    config = global_config()
    if a.workspace_root is not None or a.unset_workspace_root:
        actor()
        if a.workspace_root is not None:
            if not a.workspace_root.strip():
                raise Fail("workspace root cannot be empty")
            root = os.path.realpath(os.path.expanduser(a.workspace_root))
            if os.path.exists(root) and not os.path.isdir(root):
                raise Fail(f"workspace root is not a directory: {root}")
            config["workspace_root"] = root
        else:
            config.pop("workspace_root", None)
        path = global_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = ""
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path),
                                             prefix=".config-", delete=False) as f:
                temp = f.name
                if "workspace_root" in config:
                    f.write("workspace_root = " + json.dumps(config["workspace_root"], ensure_ascii=False) + "\n")
            os.replace(temp, path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
    print(f"Global configuration: {global_config_path()}")
    if "workspace_root" in config:
        print("workspace_root = " + json.dumps(config["workspace_root"], ensure_ascii=False))
    else:
        print("workspace_root is unset; knowledge is checked out beside each repository (../<repo>-rkm)")


@contextlib.contextmanager
def storage_lock():
    """Serialize commands sharing the bundle; OS locks release after a crash."""
    with open(os.path.join(REPO_META, "rkm.lock"), "a+b") as f:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            if not f.read(1):
                f.write(b"\0")
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def validate_storage():
    if not os.path.isdir(STORE_ROOT):
        raise Fail(f"knowledge workspace is missing: {STORE_ROOT}; restore it at its saved location")
    backend = STORAGE["backend"]
    if backend == "jj":
        meta = jj("git", "root", cwd=STORE_ROOT).stdout.strip()
        branch_rev = jj("log", "--no-graph", "-r", f"bookmarks(exact:{json.dumps(BRANCH)})",
                        "-T", 'commit_id ++ "\\n"', cwd=STORE_ROOT).stdout.splitlines()
        # The workspace may have unpublished children of the bookmark, but must
        # stay on this history rather than being switched to a code revision.
        valid = len(branch_rev) == 1 and jj(
            "log", "--no-graph", "-r", f"{branch_rev[0]}::@", "-T", 'commit_id ++ "\\n"',
            cwd=STORE_ROOT).stdout.strip()
    else:
        meta = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=STORE_ROOT).stdout.strip()
        valid = git("symbolic-ref", "--quiet", "HEAD", cwd=STORE_ROOT, check=False).stdout.strip() == f"refs/heads/{BRANCH}"
    if os.path.realpath(meta) != REPO_META or not valid:
        raise Fail(f"{STORE_ROOT} is not the {BRANCH} knowledge workspace for this repository")
    if has_bundle(REPO):
        raise Fail("both the code checkout and knowledge workspace contain rkm/; "
                   "reconcile and remove the old in-tree bundle before continuing")
    if not has_bundle(STORE_ROOT):
        raise Fail(f"no rkm/ bundle in the knowledge workspace: {STORE_ROOT}")


def restore_workspace(workspace=None):
    """Reattach existing knowledge history; never create an empty replacement."""
    global STORE_ROOT, BUNDLE
    backend = STORAGE["backend"]
    revision = knowledge_revision(backend, BRANCH)
    if not revision:
        raise Fail(f"knowledge branch {BRANCH} is unavailable; fetch it before recreating its workspace")
    validate_knowledge_revision(backend, revision)
    target = os.path.realpath(os.path.expanduser(workspace)) if workspace else STORE_ROOT
    if os.path.lexists(target):
        raise Fail(f"workspace location already exists: {target}")
    if os.path.commonpath([target, os.path.realpath(REPO)]) == os.path.realpath(REPO) or (
            os.path.commonpath([target, REPO_META]) == REPO_META):
        raise Fail("the knowledge workspace must be outside the code checkout and repository metadata")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if backend == "jj":
        registered = jj("workspace", "root", "--name", BRANCH, check=False)
        names = jj("workspace", "list", "-T", 'name ++ "\\n"').stdout.splitlines()
        if BRANCH in names:
            if registered.returncode == 0:
                old = os.path.realpath(registered.stdout.strip())
                if old != STORE_ROOT or os.path.exists(old):
                    raise Fail("knowledge workspace is registered elsewhere; reconcile its location before recreating it")
            jj("workspace", "forget", BRANCH)
        jj("workspace", "add", "--name", BRANCH, "-r", revision,
           "--sparse-patterns", "full", target, snapshot=True)
    else:
        # Git can remove the registration of a worktree whose directory is gone.
        git("worktree", "remove", STORE_ROOT, check=False)
        local = git("rev-parse", "--verify", f"refs/heads/{BRANCH}", check=False).returncode == 0
        git("worktree", "add", *([] if local else ["-b", BRANCH]), target,
            BRANCH if local else revision)
    save_storage(backend, BRANCH, target)
    configure_bundle()
    validate_storage()


def declared_branch():
    """Portable opt-in declaration in the generated AGENTS.md block."""
    content = agents_content()
    if AGENTS_BEGIN not in content:
        return ""
    block = content[content.index(AGENTS_BEGIN):content.index(AGENTS_END)]
    match = re.search(r"^<!-- rkm:branch (.+) -->$", block, re.M)
    if not match:
        return ""
    try:
        branch = json.loads(match.group(1))
    except ValueError as e:
        raise Fail("invalid RKM branch declaration in AGENTS.md") from e
    if not isinstance(branch, str) or not branch:
        raise Fail("invalid RKM branch declaration in AGENTS.md")
    return branch


def cmd_workspace(a):
    if not BRANCH:
        if a.action == "remove":
            raise Fail("no branch storage is configured")
        branch = declared_branch() or "_rkm"
        if not knowledge_revision(BACKEND, branch):
            raise Fail(f"knowledge branch {branch} is unavailable; fetch it or use `rkm init`")
        enable_branch(argparse.Namespace(branch=branch, workspace=a.workspace, name=None))
        return
    if a.action == "add":
        if os.path.isdir(STORE_ROOT):
            if a.workspace and os.path.realpath(os.path.expanduser(a.workspace)) != STORE_ROOT:
                raise Fail("remove the existing knowledge workspace before choosing another location")
            validate_storage()
        else:
            restore_workspace(a.workspace)
        update_agents()
        print(f"Knowledge workspace: {STORE_ROOT}")
        return
    actor()
    if not os.path.isdir(STORE_ROOT):
        print("Knowledge workspace is already absent; its location preference is retained")
        return
    validate_storage()
    backend = STORAGE["backend"]
    if backend == "jj":
        changes = jj("diff", "--summary", "--from", f"bookmarks(exact:{json.dumps(BRANCH)})",
                     "--to", "@", cwd=STORE_ROOT, snapshot=True).stdout.strip()
        recorded = set(jj("file", "list", "-r", "@", cwd=STORE_ROOT).stdout.splitlines())
        extra = []
        for d, dirs, files in os.walk(STORE_ROOT):
            if d == STORE_ROOT:
                dirs[:] = [n for n in dirs if n not in (".git", ".jj")]
                files = [n for n in files if n not in (".git", ".jj")]
            for name in files + [n for n in dirs if os.path.islink(os.path.join(d, n))]:
                rel = os.path.relpath(os.path.join(d, name), STORE_ROOT)
                if rel not in recorded:
                    extra.append(rel)
        if changes or extra:
            raise Fail("knowledge workspace has uncommitted or untracked files; commit or preserve them before removal")
        jj("workspace", "forget", BRANCH)
        shutil.rmtree(STORE_ROOT)
    else:
        if git("status", "--porcelain", "--untracked-files=all", "--ignored", cwd=STORE_ROOT).stdout.strip():
            raise Fail("knowledge workspace has uncommitted or untracked files; commit or preserve them before removal")
        git("worktree", "remove", STORE_ROOT)
    print("Removed the knowledge workspace; its history and saved location are retained.\n"
          "The next RKM command recreates it, or use `rkm workspace add`.")


def knowledge_revision(backend, branch):
    """Find one existing local or fetched remote branch, without fetching."""
    if backend == "jj":
        for revset in (f"bookmarks(exact:{json.dumps(branch)})",
                       f"remote_bookmarks(exact:{json.dumps(branch)})"):
            ids = jj("log", "--no-graph", "-r", revset, "-T", 'commit_id ++ "\\n"').stdout.splitlines()
            if len(set(ids)) > 1:
                raise Fail(f"{branch} has multiple targets; resolve it before enabling branch storage")
            if ids:
                return ids[0]
    else:
        result = git("rev-parse", "--verify", f"refs/heads/{branch}", check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        refs = git("for-each-ref", "--format=%(refname)", "refs/remotes/").stdout.splitlines()
        refs = [r for r in refs if r.split("/", 3)[-1] == branch]
        if len(refs) > 1:
            raise Fail(f"multiple remotes have {branch}; create a local branch from the intended remote first")
        if refs:
            return refs[0]
    return ""


def validate_knowledge_revision(backend, revision):
    if backend == "jj":
        files = jj("file", "list", "-r", revision).stdout.splitlines()
        roots = jj("log", "--no-graph", "-r", f"roots(::({revision}) ~ root())",
                   "-T", 'commit_id ++ "\\n"').stdout.splitlines()
        for root in roots:
            if any(not f.startswith("rkm/") for f in jj("file", "list", "-r", root).stdout.splitlines()):
                raise Fail("knowledge history shares code ancestry; use a separate root history")
    else:
        files = git("ls-tree", "-r", "--name-only", revision).stdout.splitlines()
        for root in git("rev-list", "--max-parents=0", revision).stdout.splitlines():
            if git("ls-tree", "--name-only", root).stdout.splitlines() != ["rkm"]:
                raise Fail("knowledge history shares code ancestry; use an orphan branch")
    if not files or any(not f.startswith("rkm/") for f in files) or not any(
            f in files for f in ("rkm/config.toml", "rkm/index.md", "rkm/log.md")):
        raise Fail("the requested knowledge branch must contain only an rkm/ bundle")


def save_storage(backend, branch, workspace):
    global STORAGE, BRANCH, STORE_ROOT, BUNDLE
    preference = {"backend": backend, "branch": branch, "workspace": workspace, "code_root": REPO}
    temp = STORAGE_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(preference, f, indent=2)
        f.write("\n")
    os.replace(temp, STORAGE_FILE)
    STORAGE, BRANCH, STORE_ROOT = preference, branch, workspace
    BUNDLE = os.path.join(workspace, "rkm")


def commit_message(summary=None, kind="rkm", message=None):
    policy = CONFIG.get("commits", {})
    if message is None:
        if not summary or not summary.strip() or "\n" in summary or "\r" in summary:
            raise Fail("commit summary must be a nonempty single line")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", kind):
            raise Fail("commit kind must be a lower-case name")
        message = policy.get("template", "docs({kind}): {summary}").format(kind=kind, summary=summary.strip())
    if not message or not message.splitlines()[0].strip():
        raise Fail("commit message must have a nonempty subject")
    if "pattern" in policy and not re.fullmatch(policy["pattern"], message):
        raise Fail("commit message does not match commits.pattern; "
                   + (policy.get("guidance") or "check the repository's commit policy in rkm/config.toml"))
    return message


def pending_commit_kind():
    if STORAGE["backend"] == "jj":
        lines = jj("diff", "--summary", "--from", f"bookmarks(exact:{json.dumps(BRANCH)})",
                   "--to", "@", cwd=STORE_ROOT, snapshot=True).stdout.splitlines()
        paths = [line[2:] for line in lines]
    else:
        paths = git("diff", "--name-only", "-z", "HEAD", "--", "rkm", cwd=STORE_ROOT).stdout.split("\0")
        paths += git("ls-files", "--others", "--exclude-standard", "-z", "--", "rkm",
                     cwd=STORE_ROOT).stdout.split("\0")
    kinds = set()
    for path in paths:
        if not path.startswith("rkm/") or path in ("rkm/index.md", "rkm/log.md"):
            continue
        if not path.endswith(".md"):
            kinds.add("rkm")
            continue
        try:
            kinds.add(Doc(os.path.join(STORE_ROOT, path)).get("type").lower())
        except (Fail, OSError, AttributeError):
            # Deleted documents can still be classified by their configured directory.
            matches = {t.lower() for t, v in TYPE_DEFS.items() if v.get("dir")
                       and path.startswith("rkm/" + v["dir"].strip("/") + "/")}
            kinds.update(matches or {"rkm"})
    return next(iter(kinds)) if len(kinds) == 1 else "rkm"


def commit_knowledge(message, backend, branch, workspace):
    message = commit_message(message=message)
    if backend == "jj":
        jj("describe", "-m", message, cwd=workspace, snapshot=True)
        jj("bookmark", "set", branch, "-r", "@", cwd=workspace, snapshot=True)
        jj("new", cwd=workspace, snapshot=True)
    else:
        git("add", "--", "rkm", cwd=workspace)
        git("commit", "-m", message, cwd=workspace)


def enable_branch(a, migrate=False):
    """Create/attach knowledge storage. Copy and commit before removing source."""
    global STORE_ROOT, BUNDLE
    if not BACKEND:
        raise Fail("branch storage needs a jj or Git repository")
    branch = a.branch or "_rkm"
    # Use Git's ref grammar, also for jj bookmarks, so they can be exported.
    git_dir_args = ["--git-dir", REPO_META, "check-ref-format", f"refs/heads/{branch}"]
    result = subprocess.run(["git", *git_dir_args], capture_output=True, text=True)
    if result.returncode or branch.startswith("-"):
        raise Fail(f"invalid branch name: {branch}")
    if BRANCH:
        if migrate:
            raise Fail("branch storage is already enabled")
        if branch != BRANCH or (os.path.isdir(STORE_ROOT) and a.workspace
                                and os.path.realpath(os.path.expanduser(a.workspace)) != STORE_ROOT):
            raise Fail("branch storage is already configured with a different branch or workspace")
        if not os.path.isdir(STORE_ROOT):
            restore_workspace(a.workspace)
        validate_storage()
        changed = update_agents()
        print(f"Using {BRANCH} knowledge workspace at {STORE_ROOT}")
        state = "updated" if changed else "already up to date"
        print(f"Managed RKM section in {os.path.join(REPO, 'AGENTS.md')}: {state}.")
        return
    chosen_workspace = a.workspace or preferred_workspace()
    actor()  # authorize before workspace creation or copying
    agents_content()  # reject malformed managed sections before migrating data
    workspace = os.path.realpath(os.path.expanduser(chosen_workspace))
    if os.path.commonpath([workspace, os.path.realpath(REPO)]) == os.path.realpath(REPO) or (
            os.path.commonpath([workspace, REPO_META]) == REPO_META):
        raise Fail("the knowledge workspace must be outside the code checkout and repository metadata")
    if os.path.lexists(workspace):
        raise Fail(f"workspace location already exists: {workspace}; choose a new directory "
                   "with --workspace PATH")
    if has_bundle(REPO) != migrate:
        raise Fail("use `rkm migrate` for an existing in-tree bundle" if not migrate
                   else "no in-tree rkm/ bundle to migrate")
    source = os.path.join(REPO, "rkm")
    revision = knowledge_revision(BACKEND, branch)
    if revision and migrate:
        raise Fail(f"{branch} already exists; refusing to replace existing knowledge")
    if revision:
        validate_knowledge_revision(BACKEND, revision)
    seed_message = None if revision else commit_message(
        "migrate repository knowledge" if migrate else "initialize repository knowledge",
        message=getattr(a, "message", None))
    if migrate:
        if os.path.islink(source):
            raise Fail("migration requires a real rkm/ directory, not a symbolic link")
        if BACKEND == "jj":
            result = jj("resolve", "--list", "rkm", check=False)
            if result.returncode and "No conflicts found" not in result.stderr:
                raise Fail(result.stderr.strip())
            conflicts = result.stdout.strip()
        else:
            conflicts = git("ls-files", "--unmerged", "--", "rkm").stdout.strip()
        if conflicts:
            raise Fail("resolve rkm/ conflicts before migrating")
        for d, _, files in os.walk(source):
            for name in files:
                path = os.path.join(d, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        if CONFLICT.search(f.read()):
                            raise Fail(f"resolve conflict markers in {path} before migrating")
                except UnicodeDecodeError:
                    pass
    if BACKEND == "git" and not revision:
        git("var", "GIT_AUTHOR_IDENT")
        git("var", "GIT_COMMITTER_IDENT")
    previous_root, previous_bundle = STORE_ROOT, BUNDLE
    created = False
    try:
        os.makedirs(os.path.dirname(workspace), exist_ok=True)
        if BACKEND == "jj":
            jj("workspace", "add", "--name", branch, "-r", revision or "root()",
               "--sparse-patterns", "full", workspace, snapshot=True)
        elif revision:
            local = git("rev-parse", "--verify", f"refs/heads/{branch}", check=False).returncode == 0
            git("worktree", "add", *([] if local else ["-b", branch]), workspace,
                branch if local else revision)
        else:
            git("worktree", "add", "--orphan", "-b", branch, workspace)
        created = True
        STORE_ROOT, BUNDLE = workspace, os.path.join(workspace, "rkm")
        if migrate:
            shutil.copytree(source, BUNDLE, symlinks=True)
        elif not revision:
            initialize_bundle(a)
        configure_bundle()
        if not revision:
            commit_knowledge(seed_message, BACKEND, branch, workspace)
        elif BACKEND == "jj":
            jj("bookmark", "set", branch, "-r", revision, cwd=workspace)
        # Snapshot settings or ignore rules can omit files without failing a
        # jj commit. Never remove the source unless every copied file is recorded.
        if BACKEND == "jj":
            recorded = set(jj("file", "list", "-r", f"bookmarks(exact:{json.dumps(branch)})",
                              cwd=workspace).stdout.splitlines())
        else:
            recorded = set(git("ls-tree", "-r", "--name-only", branch, cwd=workspace).stdout.splitlines())
        expected = set()
        for d, dirs, files in os.walk(BUNDLE):
            for name in files + [n for n in dirs if os.path.islink(os.path.join(d, n))]:
                expected.add(bundle_rel(os.path.join(d, name)))
        if expected - recorded:
            raise Fail("knowledge commit omitted files (check VCS ignore/snapshot settings): "
                       + ", ".join(sorted(expected - recorded)))
        save_storage(BACKEND, branch, workspace)
    except BaseException:
        STORE_ROOT, BUNDLE = previous_root, previous_bundle
        # Only remove a newly created checkout; the source has not been touched.
        if created and not os.path.exists(STORAGE_FILE):
            if BACKEND == "jj":
                jj("workspace", "forget", branch, check=False)
                shutil.rmtree(workspace)
            else:
                git("worktree", "remove", "--force", workspace, check=False)
        raise
    update_agents()
    if migrate:
        shutil.rmtree(source)
        print("Migrated rkm/. Review and commit its removal in the code checkout.")
    print(f"Knowledge lives in {workspace} on {branch} ({BACKEND}).\n"
          "`rkm path` prints the bundle location; `rkm commit -m MESSAGE` records knowledge changes.\n"
          "Updated the RKM section in AGENTS.md. Review and commit it in the code checkout.\n"
          "Push knowledge separately when ready.")


# --- frontmatter ------------------------------------------------------------
# The subset of YAML rkm writes: `key: scalar`, JSON-quoted strings, flow
# mappings `{ k: v, ... }`, flow lists `[a, b]` and block lists of either.
# Keys rkm does not know are kept byte for byte.

def split_flow(s):
    parts, depth, cur, quote = [], 0, "", False
    for i, ch in enumerate(s):
        if quote:
            cur += ch
            if ch == '"' and s[i - 1] != "\\":
                quote = False
        elif ch == '"':
            quote, cur = True, cur + ch
        elif ch in "{[":
            depth, cur = depth + 1, cur + ch
        elif ch in "}]":
            depth, cur = depth - 1, cur + ch
        elif ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def scalar(s):
    s = s.strip()
    if s.startswith('"'):
        return json.loads(s)
    if s.startswith("{"):
        if not s.endswith("}"):
            raise ValueError(f"unclosed mapping: {s}")
        out = {}
        for part in split_flow(s[1:-1]):
            k, sep, v = part.partition(":")
            if not sep:
                raise ValueError(f"not `key: value`: {part}")
            out[k.strip()] = scalar(v)
        return out
    if s.startswith("["):
        if not s.endswith("]"):
            raise ValueError(f"unclosed list: {s}")
        return [scalar(p) for p in split_flow(s[1:-1])]
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def encode(key, v):
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k}: {encode(k, x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(encode(key, x) for x in v) + "]"
    if key not in QUOTED and (ACTOR.match(v) or TIMESTAMP.match(v) or BARE.match(v)
                              or key in ("to", "from") and ORIGIN.match(v)):
        return v
    return json.dumps(v, ensure_ascii=False)


class Doc:
    def __init__(self, path):
        self.path = path
        self.rel = os.path.relpath(path, BUNDLE)
        self.id = self.rel[:-3]
        text = open(path, encoding="utf-8").read()
        if not text.startswith("---\n"):
            raise Fail(f"{self.rel}: no frontmatter; give it some with `rkm adopt`")
        end = text.find("\n---\n", 3)
        if end < 0:
            raise Fail(f"{self.rel}: frontmatter is not closed")
        self.body = text[end + 5:]
        self.raw, self.values, self.dirty = {}, {}, set()
        key = None
        for line in text[4:end].splitlines():
            m = re.match(r"^([A-Za-z_][\w-]*):(?: (.*))?$", line)
            if m:
                key = m.group(1)
                if key in self.raw:
                    raise Fail(f"{self.rel}: `{key}` appears twice")
                self.raw[key] = [line]
            elif key and (line.startswith(" ") or not line.strip()):
                self.raw[key].append(line)
            else:
                raise Fail(f"{self.rel}: cannot read frontmatter line: {line!r}")
        for k, lines in self.raw.items():
            try:
                self.values[k] = self._decode(lines)
            except ValueError as e:
                # Keys rkm writes must parse; any other (OKF `sources`, or a
                # producer's own) is kept byte for byte.
                if k in WRITES:
                    raise Fail(f"{self.rel}: `{k}`: {e}")

    @staticmethod
    def _decode(lines):
        first = lines[0].partition(":")[2].strip()
        rest = [l for l in lines[1:] if l.strip()]
        if first:
            if rest:
                raise ValueError("value continues onto further lines")
            return scalar(first)
        if all(l.strip().startswith("- ") for l in rest):
            return [scalar(l.strip()[2:]) for l in rest]
        raise ValueError("unsupported value")

    def get(self, k, default=None):
        return self.values.get(k, default)

    def set(self, k, v):
        if v is None:
            self.values.pop(k, None)
            self.raw.pop(k, None)
        else:
            self.values[k] = v
        self.dirty.add(k)

    def text(self):
        keys = sorted(set(self.raw) | set(self.values),
                      key=lambda k: (KEY_ORDER.index(k) if k in KEY_ORDER else len(KEY_ORDER),
                                     list(self.raw).index(k) if k in self.raw else 0))
        out = ["---"]
        for k in keys:
            if k not in self.values and k not in self.raw:
                continue
            if k in self.dirty or k not in self.raw:
                v = self.values[k]
                if k in BLOCK_LISTS and isinstance(v, list):
                    out.append(f"{k}:")
                    out += [f"  - {encode(k, x)}" for x in v]
                else:
                    out.append(f"{k}: {encode(k, v)}")
            else:
                out += self.raw[k]
        out += ["---", ""]
        return "\n".join(out) + self.body

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(self.text())


# --- bundle -----------------------------------------------------------------

def concept_paths():
    for d, dirs, files in os.walk(BUNDLE):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for f in sorted(files):
            if f.endswith(".md") and f not in RESERVED:
                yield os.path.join(d, f)


def load_all():
    """Every concept that parses; `rkm check` reports the ones that don't."""
    docs = []
    for p in concept_paths():
        try:
            docs.append(Doc(p))
        except Fail:
            pass
    return docs


def resolve(ident):
    s = ident
    if s.startswith("rkm/"):
        s = s[4:]
    if s.endswith(".md"):
        s = s[:-3]
    path = os.path.join(BUNDLE, s + ".md")
    if "/" in s:
        if os.path.exists(path):
            return Doc(path)
        raise Fail(f"no concept {ident}")
    hits = [p for p in concept_paths() if os.path.basename(p) == s + ".md"]
    if len(hits) == 1:
        return Doc(hits[0])
    if not hits:
        raise Fail(f"no concept {ident}")
    raise Fail(f"{ident} is ambiguous: " + ", ".join(os.path.relpath(h, BUNDLE)[:-3] for h in hits))


def now():
    if os.environ.get("RKM_NOW"):
        return os.environ["RKM_NOW"]
    t = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return t.isoformat().replace("+00:00", "Z")


def is_agent():
    return bool(os.environ.get("CLAUDECODE"))


def actor():
    a = os.environ.get("RKM_ACTOR")
    if not a:
        if is_agent():
            raise Fail("set RKM_ACTOR to <agent>/<model>, e.g. RKM_ACTOR=claude/opus-5.5")
        a = "human:" + getpass.getuser()
    if not ACTOR.match(a):
        raise Fail(f"RKM_ACTOR {a!r} is not human:<id>, process:<id> or <agent>/<model>")
    if is_agent() and a.startswith("human:"):
        raise Fail("an agent cannot write as a human: set RKM_ACTOR to <agent>/<model>")
    return a


def stamp():
    return {"by": actor(), "at": now()}


def origins(doc):
    """`from` as a list; a hand-written single value counts as one entry."""
    v = doc.get("from")
    return [] if v is None else v if isinstance(v, list) else [v]


def target(label):
    """`to`: who a request is put to, as a label, never a link; links go in the body."""
    if label is not None and (not ORIGIN.match(label) or "://" in label):
        raise Fail(f"to {label!r} is not a prefix:name label, e.g. repo:org/name or team:platform")
    return label


def origin(ref):
    if ref is not None and not ORIGIN.match(ref):
        raise Fail(f"from {ref!r} is not a link or a prefix:name label, e.g. app:slack or repo:org/name")
    return ref


def describe(description):
    """The description to write, or None. People may leave it out; an agent
    writes one, since the index line is what lets the next reader choose."""
    d = (description or "").strip() or None
    who = actor()  # also refuses a bad actor
    if d is None and (is_agent() or not who.startswith(("human:", "process:", "team:"))):
        raise Fail("an agent must pass --description: one sentence for the index")
    return d


# --- index ------------------------------------------------------------------

def section_of(d):
    t, status = d.get("type"), d.get("status")
    if status == "deprecated":
        return "Deprecated"
    if t in TRACKED and d.get("state") == "done":
        return "Closed"
    return TYPE_DEFS.get(t, {}).get("section", "Other")


OKF = "https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md"


def intro():
    if "intro" in CONFIG:
        return CONFIG["intro"].strip("\n").split("\n")
    name = CONFIG.get("name") or os.path.basename(REPO)
    return [f"Contributor and agent knowledge for {name}, as an [Open Knowledge Format]({OKF})",
            "bundle. Changes go through `rkm`; `rkm conventions` gives the rules."]


def render_index(docs):
    lines = ["---", 'okf_version: "0.2"', "---", "",
             "<!-- Generated by rkm. Do not edit; run `rkm index`. -->", "",
             *intro(), ""]
    by = {}
    for d in docs:
        by.setdefault(section_of(d), []).append(d)
    for name in dict.fromkeys(SECTIONS):
        if name not in by:
            continue
        lines += [f"# {name}", ""]
        for d in sorted(by[name], key=lambda d: d.id):
            mark = " (draft)" if d.get("status") == "draft" else ""
            if name == "Closed":
                mark = f" ({d.get('type').lower()})"
            desc = d.get("description")
            lines.append(f"* [{d.get('title') or d.id}]({d.rel}){mark}" + (f" - {desc}" if desc else ""))
        lines.append("")
    return "\n".join(lines)


def write_index():
    with open(os.path.join(BUNDLE, "index.md"), "w", encoding="utf-8") as f:
        f.write(render_index(load_all()))


# --- links ------------------------------------------------------------------

LINK = re.compile(r"(?P<pre>\]\(<?)(?P<t>[^)\s>]+)|(?P<hpre>(?:href|src)=\")(?P<ht>[^\"\s]+)\"")
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
TARGET = re.compile(r"^(?P<p>[^#]*?)(?P<line>:\d+(?:-\d+)?)?(?P<frag>#.*)?$")


def repo_path(rel):
    """Resolve the logical code-root layout, even with an external bundle."""
    rel = os.path.normpath(rel)
    if rel == "rkm" or rel.startswith("rkm" + os.sep):
        return os.path.join(STORE_ROOT, rel)
    return os.path.join(REPO, rel)


def bundle_rel(path):
    return os.path.join("rkm", os.path.relpath(path, BUNDLE))


def repo_files():
    """Code and knowledge text files, in one logical repo-relative layout."""
    try:
        out = subprocess.run(["jj", "--ignore-working-copy", "file", "list"], cwd=REPO, check=True,
                             capture_output=True, text=True).stdout
        files = out.splitlines()
    except (OSError, subprocess.CalledProcessError):
        files = list(walk_files())
    if BRANCH:
        files = [f for f in files if f != "rkm" and not f.startswith("rkm/")]
        files.extend(walk_files(BUNDLE, STORE_ROOT))
    for f in dict.fromkeys(files):
        if os.path.splitext(f)[1] in TEXT_EXT and not any(
                f == p or f.startswith(p + "/") for p in SKIP_PATHS):
            yield f


def walk_files(root=None, relative_to=None):
    root, relative_to = root or REPO, relative_to or REPO
    for d, dirs, files in os.walk(root):
        here = os.path.relpath(d, relative_to)
        dirs[:] = [x for x in dirs if x not in SKIP_NAMES
                   and os.path.normpath(os.path.join(here, x)) not in SKIP_PATHS]
        for f in files:
            yield os.path.relpath(os.path.join(d, f), relative_to)


def link_targets(content):
    for m in LINK.finditer(content):
        key = "t" if m.group("t") else "ht"
        t = m.group(key)
        if SCHEME.match(t) or t.startswith(("#", "/")):
            continue
        tm = TARGET.match(t)
        if tm and tm.group("p"):
            yield m.span(key), t, tm, (m.start() if key == "t" else None)


def rewrite_links(content, f_old, f_new, old, new):
    """Point links at `old` (repo-relative) to `new`; re-relativize every
    relative link when the file itself moved from f_old to f_new."""
    out, pos = [], 0
    for (start, end), t, tm, text_end in link_targets(content):
        p = tm.group("p")
        target = os.path.normpath(os.path.join(os.path.dirname(f_old), p))
        moved = target == old
        if not moved and f_old == f_new:
            continue
        dest = new if moved else target
        rel = os.path.relpath(dest, os.path.dirname(f_new) or ".")
        if p.endswith("/"):
            rel += "/"
        before = content[pos:start]
        # Link text that spells the old target, `[../x.md](../x.md)`, follows it.
        lb = content.rfind("[", pos, text_end) if text_end is not None else -1
        if lb >= 0 and content[lb + 1:text_end].strip("`") in (t, p):
            label = content[lb + 1:text_end].replace(t if t in content[lb + 1:text_end] else p, rel, 1)
            before = content[pos:lb + 1] + label + content[text_end:start]
        out.append(before + rel + (tm.group("line") or "") + (tm.group("frag") or ""))
        pos = end
    out.append(content[pos:])
    text = "".join(out)
    # Repo-root mentions in prose and comments: `rkm/todos/x.md`.
    return re.sub(r"(?<![\w./-])" + re.escape(old) + r"(?![\w-])", new, text)


# --- commands ---------------------------------------------------------------

def slugify(title):
    """`Replace regexlite (v2)` -> `replace-regexlite-v2`, cut at a word
    boundary so the path stays short."""
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > 50:
        s = s[:51].rsplit("-", 1)[0] if "-" in s[:51] else s[:50]
    return s


def cmd_new(a):
    if a.kind not in KINDS:
        raise Fail(f"kind must be one of {', '.join(KINDS)}")
    derived = not a.slug
    if derived:
        a.slug = slugify(a.title)
        if not a.slug:
            raise Fail("no slug can be derived from the title; pass one")
    if not SLUG.match(a.slug):
        raise Fail("slug is lower-case letters, digits, - and _")
    t, d = KINDS[a.kind]
    if t == "Decision" and (derived or not re.match(r"^\d\d-", a.slug)):
        taken = [int(f[:2]) for f in os.listdir(os.path.join(BUNDLE, d))
                 if re.match(r"^\d\d-.*\.md$", f)] if os.path.isdir(os.path.join(BUNDLE, d)) else []
        a.slug = f"{max(taken, default=0) + 1:02d}-{a.slug}"
    path = os.path.join(BUNDLE, d, a.slug + ".md")
    # A derived slug that is taken is refused, not numbered: the same title
    # is usually the same item, and a slug is a path, which is permanent.
    hint = "; is it the same item? If not, pass a slug" if derived else ""
    if os.path.exists(path):
        raise Fail(f"{bundle_rel(path)} exists{hint}")
    hits = [p for p in concept_paths() if os.path.basename(p) == a.slug + ".md"]
    if hits:
        raise Fail(f"slug {a.slug} is taken by {os.path.relpath(hits[0], BUNDLE)[:-3]}{hint}")
    generated = stamp()  # refuses a bad actor before anything is written
    description = describe(a.description)
    source = origin(a.from_)
    to = target(a.to)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = f"# {a.title}\n\n" + TEMPLATES.get(t, "")
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\ntype: " + t + "\n---\n" + body)
    doc = Doc(path)
    doc.set("title", a.title)
    doc.set("description", description)
    if t in TRACKED:
        doc.set("state", "open")
    if a.draft:
        doc.set("status", "draft")
    if t == "Decision":
        doc.set("decided", now()[:10])
    doc.set("to", to)
    if source:
        doc.set("from", [source])
    doc.set("generated", generated)
    doc.save()
    write_index()
    print(path if BRANCH else bundle_rel(path))


def cmd_adopt(a):
    """Give a file that arrived without frontmatter (a merge, a hand-made file)
    its frontmatter. `generated` is left unset: rkm does not know who wrote it."""
    if a.kind not in KINDS and a.kind not in {k.lower() for k in TYPES}:
        raise Fail(f"kind must be one of {', '.join(sorted(set(KINDS) | {k.lower() for k in TYPES}))}")
    t = KINDS[a.kind][0] if a.kind in KINDS else next(k for k in TYPES if k.lower() == a.kind)
    src = os.path.normpath(a.path)
    outside = os.path.exists(os.path.join(REPO, src)) and not src.startswith("rkm/")
    if outside:
        # A file from elsewhere in the repo moves in: to --as, else to its
        # type's directory under its own name.
        default = os.path.join(TYPE_DEFS[t].get("dir", ""),
                               re.sub(r"[^a-z0-9_-]+", "-", os.path.basename(src)[:-3].lower()))
        rel = a.as_ or default
        rel = rel[4:] if rel.startswith("rkm/") else rel
        if not SLUG.match(os.path.basename(rel.removesuffix(".md"))):
            raise Fail(f"bad destination {rel}; pass --as")
        if not src.endswith(".md"):
            raise Fail(f"{src} is not Markdown")
    else:
        if a.as_:
            raise Fail("--as is for a file outside rkm/")
        rel = a.path[4:] if a.path.startswith("rkm/") else a.path
    path = os.path.join(BUNDLE, rel if rel.endswith(".md") else rel + ".md")
    if outside and os.path.exists(path):
        raise Fail(f"{bundle_rel(path)} exists")
    if not outside and not os.path.exists(path):
        raise Fail(f"no file rkm/{rel}")
    if os.path.basename(path) in RESERVED:
        raise Fail(f"{os.path.basename(path)} is reserved")
    text = open(os.path.join(REPO, src) if outside else path, encoding="utf-8").read()
    if text.startswith("---\n"):
        raise Fail(f"{src if outside else 'rkm/' + rel} already has frontmatter; use rkm set")
    h1 = re.search(r"^# (.+)$", text, re.M)
    title = a.title or (h1.group(1).strip() if h1 else None)
    if not title:
        raise Fail("no `# ` heading to take the title from; pass --title")
    description = describe(a.description)  # refuses a bad actor before anything is written
    source = origin(a.from_)
    if t == "Decision" and not (a.decided and DATE.match(a.decided)):
        raise Fail("a Decision needs --decided YYYY-MM-DD")
    if t in TRACKED and a.state not in states_of(t):
        raise Fail(f"a {t}'s state is one of {', '.join(states_of(t))}")
    if outside:
        move_file(src, bundle_rel(path))
        text = open(path, encoding="utf-8").read()
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\ntype: " + t + "\n---\n" + text)
    doc = Doc(path)
    doc.set("title", title)
    doc.set("description", description)
    if t in TRACKED:
        doc.set("state", a.state)
    if t == "Decision":
        doc.set("decided", a.decided)
    if a.status:
        doc.set("status", a.status)
    if source:
        doc.set("from", [source])
    doc.save()
    write_index()


def tracked(doc):
    if doc.get("type") not in TRACKED:
        raise Fail(f"{doc.id} is a {doc.get('type')}; only {' and '.join(sorted(TRACKED))} have a state")


def cmd_close(a):
    doc = resolve(a.id)
    tracked(doc)
    if doc.get("state") == "done":
        raise Fail(f"{doc.id} is already done")
    doc.set("state", "done")
    doc.set("closed", stamp() | ({"note": a.note} if a.note else {}))
    doc.save()
    write_index()


def cmd_reopen(a):
    actor()  # refuses a bad actor before anything is written
    doc = resolve(a.id)
    tracked(doc)
    if doc.get("state") != "done":
        raise Fail(f"{doc.id} is not done")
    doc.set("state", "open")
    doc.set("closed", None)
    doc.save()
    write_index()


def cmd_state(a):
    actor()  # refuses a bad actor before anything is written
    doc = resolve(a.id)
    tracked(doc)
    allowed = states_of(doc.get("type"))
    if a.state == "done":
        raise Fail("use rkm close, which stamps `closed`")
    if a.state not in allowed:
        raise Fail(f"a {doc.get('type')}'s state is one of {', '.join(allowed)}")
    if doc.get("state") == "done":
        raise Fail(f"{doc.id} is done; rkm reopen it first")
    if doc.get("state") == a.state:
        raise Fail(f"{doc.id} is already {a.state}")
    doc.set("state", a.state)
    doc.save()
    write_index()


def cmd_retype(a):
    actor()  # refuses a bad actor before anything is written
    doc = resolve(a.id)
    new = KINDS[a.kind][0] if a.kind in KINDS else a.kind
    if new not in TYPES:
        raise Fail(f"type must be one of {', '.join(sorted(TYPES))}")
    old = doc.get("type")
    doc.set("type", new)
    if new in TRACKED and (old not in TRACKED or doc.get("state") not in states_of(new)):
        doc.set("state", "open")
    if new not in TRACKED:
        doc.set("state", None)
        doc.set("closed", None)
    doc.save()
    write_index()


def cmd_status(a):
    actor()  # refuses a bad actor before anything is written
    doc = resolve(a.id)
    doc.set("status", None if a.status == "stable" else a.status)
    doc.save()
    write_index()


def cmd_set(a):
    doc = resolve(a.id)
    if a.field == "tags":
        value = [t.strip() for t in a.value.split(",") if t.strip()]
        doc.set("tags", value or None)
    elif a.field == "to":
        doc.set("to", target(a.value.strip()) or None)
    else:
        if not a.value.strip():
            raise Fail(f"{a.field} cannot be empty")
        if a.field == "title":
            old = doc.get("title")
            first = re.match(r"^\s*# (.+)\n", doc.body)
            if old and first and first.group(1).strip() == old:
                doc.body = doc.body.replace(first.group(0), first.group(0).replace(old, a.value, 1), 1)
        doc.set(a.field, a.value)
    doc.save()
    write_index()


def cmd_touch(a):
    doc = resolve(a.id)
    doc.set("generated", stamp())
    doc.save()


def cmd_from(a):
    """Record one more place that asked for an item. The body is unchanged,
    so `generated` is not restamped; the commit dates the request."""
    origin(a.origin)
    actor()  # refuses a bad actor before anything is written
    doc = resolve(a.id)
    seen = origins(doc)
    if a.origin in seen:
        print(f"{doc.id} already lists {a.origin}")
        return
    doc.set("from", seen + [a.origin])
    doc.save()


def cmd_verify(a):
    if is_agent():
        raise Fail(f"verification is a human sign-off; ask the user to run `rkm verify`")
    who = actor()
    if not who.startswith("human:"):
        raise Fail("only a human:<id> actor can verify")
    doc = resolve(a.id)
    v = doc.get("verified") or []
    if isinstance(v, dict):
        v = [v]
    v = [x for x in v if x.get("by") != who] + [{"by": who, "at": now()}]
    doc.set("verified", v)
    doc.save()


def move_file(old, new):
    """Move repo-relative `old` to `new` and rewrite every link to it."""
    # Compute every rewrite before writing any, so a failure changes nothing.
    edits = {}
    for f in dict.fromkeys([old, *repo_files()]):
        path = repo_path(f)
        try:
            content = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        f_new = new if f == old else f
        text = rewrite_links(content, f, f_new, old, new)
        if text != content:
            edits[path] = text
    for path, text in edits.items():
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    dest = repo_path(new)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(repo_path(old), dest)
    return new


def cmd_mv(a):
    actor()  # refuses a bad actor before anything is written
    doc = resolve(a.id)
    dest_id = a.new[:-3] if a.new.endswith(".md") else a.new
    if dest_id.startswith("rkm/"):
        dest_id = dest_id[4:]
    if "/" not in dest_id:
        dest_id = os.path.join(os.path.dirname(doc.id), dest_id)
    if not SLUG.match(os.path.basename(dest_id)) or os.path.basename(dest_id) + ".md" in RESERVED:
        raise Fail(f"bad destination {a.new}")
    dest = os.path.join(BUNDLE, dest_id + ".md")
    if os.path.exists(dest):
        raise Fail(f"{dest_id} exists")
    new = move_file(bundle_rel(doc.path), bundle_rel(dest))
    write_index()
    print(dest if BRANCH else new)


def inbound(target):
    """Repo files with a link that resolves to `target` (repo-relative)."""
    hits = []
    for f in repo_files():
        if f in (target, "rkm/index.md", "rkm/log.md") or not f.endswith((".md", ".html")):
            continue
        try:
            content = open(repo_path(f), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for _, t, tm, _ in link_targets(content):
            if os.path.normpath(os.path.join(os.path.dirname(f), tm.group("p"))) == target:
                hits.append(f)
                break
    return hits


def cmd_rm(a):
    actor()  # refuses a bad actor before anything is written
    doc = resolve(a.id)
    rel = bundle_rel(doc.path)
    hits = inbound(rel)
    if hits:
        raise Fail(f"{doc.id} is still linked from: " + ", ".join(hits))
    os.remove(doc.path)
    write_index()


def cmd_ls(a):
    # Closed and deprecated documents are history, so they are hidden unless
    # --all is given or a --state or --status filter asks for them by name.
    rows, hidden = [], 0
    for d in load_all():
        if a.type and d.get("type", "").lower() != a.type.lower():
            continue
        if a.state and d.get("state") != a.state:
            continue
        if a.status and (d.get("status") or "stable") != a.status:
            continue
        section = section_of(d)
        if not a.all and (section == "Closed" and not a.state
                          or section == "Deprecated" and not a.status):
            hidden += 1
            continue
        rows.append(d)
    if a.json:
        print(json.dumps([{"id": d.id, **{k: v for k, v in d.values.items()}} for d in rows],
                         ensure_ascii=False, indent=1))
        return
    # STATE is the tracked state plus any status but the default `stable`,
    # so drafts and deprecations stand out. The header is for a person
    # reading a terminal; piped output stays rows only, for grep and awk.
    table = [(d.id, d.get("type", ""),
              " ".join(f for f in (d.get("state"), d.get("status")) if f and f != "stable"),
              d.get("title", "")) for d in rows]
    if rows and sys.stdout.isatty():
        table.insert(0, ("ID", "TYPE", "STATE", "TITLE"))
    widths = [max((len(r[i]) for r in table), default=0) for i in range(3)]
    for r in table:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)), r[3], sep="  ")
    if hidden and sys.stdout.isatty():
        print(f"{hidden} closed or deprecated hidden; --all shows them", file=sys.stderr)


def conflicted():
    """Bundle files, relative to it, that hold conflict markers."""
    for d, dirs, files in os.walk(BUNDLE):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for f in sorted(files):
            path = os.path.join(d, f)
            try:
                with open(path, encoding="utf-8") as fh:
                    if CONFLICT.search(fh.read()):
                        yield os.path.relpath(path, BUNDLE)
            except (UnicodeDecodeError, OSError):
                pass


def cmd_index(a):
    # index.md is generated, so regenerating it resolves its conflicts; the
    # files it is generated from must be resolved first, or it would drop them.
    sources = [f for f in conflicted() if f not in RESERVED]
    if sources:
        raise Fail("resolve the conflicts in " + ", ".join(sources) + " first; the index is built from them")
    write_index()


def problems():
    out = []
    docs = []
    stuck = list(conflicted())
    for f in stuck:
        out.append(f"{f}: conflict markers; " + ("run `rkm index`" if f == "index.md" else "resolve them"))
    for p in concept_paths():
        rel = os.path.relpath(p, BUNDLE)
        try:
            d = Doc(p)
        except Fail as e:
            out.append(str(e))
            continue
        docs.append(d)
        t = d.get("type")
        def bad(msg):
            out.append(f"{rel}: {msg}")
        if not t:
            bad("no `type`")
        elif t not in TYPES:
            bad(f"type {t!r} is not one of {', '.join(sorted(TYPES))}")
        for k in ("title", "description"):
            v = d.get(k)
            if k == "description" and "description" not in d.values:
                continue  # optional; agents are made to write one
            if not isinstance(v, str) or not v.strip() or v == "TODO":
                bad(f"no `{k}`")
        if t in TRACKED:
            if d.get("state") not in states_of(t):
                bad(f"state must be one of {', '.join(states_of(t))}")
        elif "state" in d.values:
            bad(f"a {t} has no `state`")
        if t == "Decision":
            if not DATE.match(str(d.get("decided", ""))):
                bad("a Decision needs `decided: YYYY-MM-DD`")
        elif "decided" in d.values:
            bad(f"a {t} has no `decided`")
        if "closed" in d.values and d.get("state") != "done":
            bad("`closed` on an item that is not done")
        if d.get("status") is not None and d.get("status") not in STATUSES:
            bad(f"status must be one of {', '.join(STATUSES)}")
        for k in ("generated", "closed"):
            if k in d.values:
                v = d.get(k)
                if not isinstance(v, dict) or not ACTOR.match(str(v.get("by", ""))) \
                        or not TIMESTAMP.match(str(v.get("at", ""))):
                    bad(f"`{k}` must be {{ by: <actor>, at: <ISO 8601 with offset> }}")
                elif k == "closed" and not isinstance(v.get("note", ""), str):
                    bad("`closed` note must be text")
        if "to" in d.values:
            v = d.get("to")
            if not isinstance(v, str) or not ORIGIN.match(v) or "://" in v:
                bad("`to` is one prefix:name label, e.g. repo:org/name; links go in the body")
        if "from" in d.values:
            f = origins(d)
            if not f or not all(isinstance(x, str) and ORIGIN.match(x) for x in f):
                bad("`from` entries are links or prefix:name labels, e.g. app:slack")
            elif len(set(f)) != len(f):
                bad("`from` lists an origin twice")
        v = d.get("verified")
        if v is not None:
            for x in v if isinstance(v, list) else [v]:
                if not isinstance(x, dict) or not str(x.get("by", "")).startswith("human:") \
                        or not TIMESTAMP.match(str(x.get("at", ""))):
                    bad("`verified` entries are { by: human:<id>, at: <ISO 8601 with offset> }")
        if rel.startswith("todos/done/"):
            bad("todos/done/ is gone; a closed item keeps its path and has state: done")
    # Links into the bundle must resolve, from anywhere in the repo.
    for f in repo_files():
        # The log is history: an entry may name a file since deleted.
        if not f.endswith((".md", ".html")) or f == "rkm/log.md":
            continue
        try:
            content = open(repo_path(f), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for _, t, tm, _ in link_targets(content):
            target = os.path.normpath(os.path.join(os.path.dirname(f), tm.group("p")))
            if (target == "rkm" or target.startswith("rkm/")) and not os.path.exists(repo_path(target)):
                out.append(f"{f}: broken link to {t}")
    index = os.path.join(BUNDLE, "index.md")
    if "index.md" not in stuck and (not os.path.exists(index)
                                    or open(index, encoding="utf-8").read() != render_index(docs)):
        out.append(f"index.md: out of date; run `rkm index`")
    logp = os.path.join(BUNDLE, "log.md")
    if os.path.exists(logp):
        for line in open(logp, encoding="utf-8"):
            if line.startswith("## ") and not re.match(r"^## \d{4}-\d\d-\d\d$", line.rstrip()):
                out.append(f"log.md: date heading {line.strip()!r} is not YYYY-MM-DD")
    return out


def cmd_check(a):
    found = problems()
    for p in found:
        print(p)
    if found:
        raise Fail(f"{len(found)} problem(s) in rkm/")
    print(f"rkm: {sum(1 for _ in concept_paths())} concepts, all conformant")


# --- distribution -----------------------------------------------------------

# `rkm conventions` is the canonical full reference, with this bundle's
# types in the table. AGENTS.md directs agents here; `check` enforces the rules.
CONVENTIONS = """\
## Where knowledge lives

Everything a contributor or agent works from is in `rkm/` (repository
knowledge management), listed in [rkm/index.md](rkm/index.md):

| Directory | `type` | What goes there |
|-----------|--------|-----------------|
{rows}
| `rkm/archive/` | any, `status: deprecated` | History. Don't treat it as current. |

`rkm/` is an [Open Knowledge Format v0.2]({okf})
bundle: every file opens with frontmatter (`type`, `title`, usually `description`,
`state` for todos and bugs, and who wrote and reviewed it), `rkm/index.md` lists
them, and version control records each change. rkm no longer writes `rkm/log.md`;
an older one is kept as history. `rkm/config.toml` says which types
the bundle uses beyond rkm's defaults, which trees rkm leaves alone, and the
oldest rkm it works with (`requires`).

`rkm` is a single Python 3.11+ script kept in its own repository, not in this
one; install it with `uv tool install <rkm repository>` and update it with
`uv tool upgrade rkm`. `rkm --version` says which you have.

**Change it only through `rkm`.** Don't create, rename, move or delete
files in `rkm/`, and don't edit their frontmatter or `index.md` by
hand. Edit a file's body by hand. Set `RKM_ACTOR` to yourself first; an agent
that doesn't is refused.

```bash
export RKM_ACTOR=claude/opus-5.5          # <agent>/<model>
rkm new todo --title "Regex engine swap" --description "One sentence."  # todos/regex-engine-swap
rkm new bug  lsp-crash-on-empty  --title "…" --description "…"   # Repro / Expected / Actual
rkm new decision closed-by-default --title "…" --description "…"  # numbered, dated
rkm close regex-engine-swap --note "landed in feat/regex"  # the note is kept in `closed`
rkm retype regex-engine-swap bug  # the file stays where it is
rkm touch <id>                    # after rewriting a body: stamps `generated`
rkm new todo --title "…" --description "…" --from https://…  # asked for elsewhere
rkm from <id> repo:org/other      # another place asked for the same thing
rkm new request --title "…" --description "…" --to repo:org/name  # an ask of another repo
rkm state <id> asked              # once the request has been put to them
rkm set <id> description "…"      # title, description or tags
rkm status <id> draft             # draft | stable | deprecated
rkm mv <id> <new-id>              # renames and rewrites every link to it
rkm rm <id>                       # refused while anything links to it
rkm adopt <path> <kind> --description "…"  # a file that arrived without frontmatter
rkm adopt notes/x.md process --description "…"   # moves it in from outside rkm/, rewriting links
rkm ls --type bug                 # current only; --all adds closed and deprecated
rkm index                         # regenerates index.md, resolving its conflicts
rkm check                         # conformance: frontmatter, links, index
```

- **A file never moves when its state changes.** In OKF a concept's path is
  its identity. Closing a todo sets `state: done`; it stays in `rkm/todos/`.
  Turning a todo into a bug changes `type`, not the directory.
- **`status` is not progress.** It is OKF's lifecycle of the document
  (`draft`, `stable`, `deprecated`); a todo's progress is `state`.
- **`verify` is a human sign-off.** rkm refuses it from an agent. A file only
  an agent wrote, with no `verified`, is unreviewed; say so when you rely on it.
- **`from` says where an item was asked for**, when that was outside this
  repo: a link to the thread, issue or chat if there is one, else a label
  such as `app:slack` or `repo:org/name`. It lists the first place, then each
  later one added with `rkm from`; `touch` keeps it.
- **A request is what we want from someone else**; `to` names who, as a
  label. Its state is `open` until it has been put to them, then `asked`,
  then `done` on `rkm close`, with a note saying whether it was delivered or
  declined. Links to their issue, todo or PR go in the body.
- **After merging knowledge from another clone**, run `rkm index`: `index.md`
  is generated, so regenerating it resolves its conflicts. A conflict in a
  document is real; resolve it by hand first, and `rkm index` refuses until
  you do. `rkm check` reports any conflict markers left.
- **An agent always writes a `description`.** It is the index line the next
  reader chooses by. A person may leave it out; rkm refuses that from an agent.
- Link between files with relative links, not `/`-rooted ones, so they work on
  GitHub and in the editor.
"""


def conventions_text(branch_mode=None):
    rows = "\n".join(f"| `rkm/{v['dir']}/` | `{t}` | {v.get('about', '')} |"
                     for t, v in TYPE_DEFS.items() if v.get("dir"))
    text = CONVENTIONS.replace("{rows}", rows).replace("{okf}", OKF)
    branch_mode = bool(BRANCH) if branch_mode is None else branch_mode
    if branch_mode:
        text = text.replace(
            "Everything a contributor or agent works from is in `rkm/` (repository\n"
            "knowledge management), listed in [rkm/index.md](rkm/index.md):",
            f"Repository knowledge lives on `{BRANCH}` in a separate {STORAGE['backend']} workspace.\n"
            "Run `rkm path` from this code checkout to locate the bundle, then read its\n"
            "`index.md` and the relevant specs, decisions and process notes before working.\n"
            "RKM paths elsewhere in this file also use this logical layout; locate their\n"
            "files through `rkm path`, rather than assuming a directory in the code checkout.\n"
            "The saved location is local to this clone and shared by its code\n"
            "workspaces. `rkm/` paths below are logical identifiers; `rkm` resolves them.\n"
            "If the knowledge checkout is absent, ordinary RKM commands recreate it from\n"
            "the existing branch at the saved location. In a new clone, fetch the knowledge\n"
            "branch; the declaration in AGENTS.md lets RKM set up the checkout under your\n"
            "global workspace parent, or beside the repository when none is set. To choose\n"
            f"another location, run `rkm init --branch {BRANCH} --workspace <chosen-directory>`.\n"
            "Keep essential checkout-specific instructions in AGENTS.md.\n")
        text = text.replace(
            "- Link between files with relative links, not `/`-rooted ones, so they work on\n"
            "  GitHub and in the editor.",
            "- Relative links within the bundle work in the editor and on the knowledge branch.\n"
            "  Links between code and knowledge keep the logical code-root layout: `rkm check`\n"
            "  and `rkm mv` resolve them, but ordinary Markdown viewers may not.\n"
            "- Edit bodies at the location printed by `rkm path`. RKM commands serialize their\n"
            "  writes; coordinate simultaneous manual edits to the same file.\n"
            "- **Commit knowledge changes before you finish** with `rkm commit --summary TEXT`\n"
            "  (or `-m MESSAGE`); they are not recorded until you do. Push the knowledge\n"
            "  bookmark/branch separately; it is never merged into the code history.")
    policy = CONFIG.get("commits", {})
    text += ("\n## Knowledge commit messages\n\n"
             "Prefer Conventional Commits for knowledge changes, for example\n"
             "`docs(todo): add parser cleanup tasks`, `docs(bug): record invalid filter behaviour`,\n"
             "or `docs(spec): define edge templates`. Recording a bug is a documentation\n"
             "change; use `fix` for a code fix. Mixed knowledge changes use scope `rkm`.\n\n"
             "Repository templates and guidance take precedence over these defaults.\n\n"
             "Use `rkm commit --summary TEXT` to apply the configured template; RKM infers\n"
             "the kind from changed documents, or accepts `--kind KIND`. A complete\n"
             "`rkm commit -m MESSAGE` is preserved verbatim. In-tree bundles use your usual\n"
             "VCS commit command, following the same policy.\n\n"
             "Template: `" + policy.get("template", "docs({kind}): {summary}") + "`\n")
    if policy.get("guidance"):
        text += "\nRepository guidance: " + policy["guidance"] + "\n"
    if policy.get("pattern"):
        text += "\nValidation pattern (Python full match of the entire message): `" + policy["pattern"] + "`\n"
    text += "\nRKM validates its own commits, including initialization and migration. Direct\n"
    text += "jj/Git commits bypass this validation; remote branch rules still apply.\n"
    text += ("\n## Knowledge workflow\n\n"
             "With `B=$(rkm path)`, read `$B/index.md` and search the bundle with\n"
             '`rg "search term" "$B"`. Review pending changes with `' + knowledge_status_command() + "`.\n"
             "`rkm status` sets document lifecycle (draft/stable/deprecated); it does\n"
             "not report workspace changes.\n\n"
             "When a code change finishes a todo or fixes a bug, close the knowledge item\n"
             'in the same session with `rkm close <id> --note "<commit-or-change-id>"`.\n')
    if branch_mode:
        text += ("\nCommit your knowledge changes in the session that made them, before you\n"
                 "finish; don't leave them pending for someone else to find.\n"
                 "`rkm commit` records everything pending in the bundle, including other sessions'\n"
                 "edits. Review the whole bundle first; coordinate unrelated pending edits\n"
                 "with their authors rather than committing them accidentally. Command locking\n"
                 "serializes RKM writes; it does not isolate sessions' changes. Knowledge\n"
                 "commits are separate from code commits and are never merged into them.\n\n"
                 "Missing configured checkouts are recreated from existing history. New clones\n"
                 "also need the fetched knowledge branch, checked out under the global workspace\n"
                 "parent or beside the repository. Missing history or an occupied location\n"
                 "requires action; RKM does not create an empty replacement or overwrite\n"
                 "another checkout.\n")
    text += "\nPush policy: " + knowledge_push_policy() + "\n"
    text += "This is repository guidance; RKM does not push automatically or enforce external pushes.\n"
    return text


def cmd_conventions(a):
    print(conventions_text(), end="")


AGENTS_BEGIN = "<!-- rkm:begin -->"
AGENTS_END = "<!-- rkm:end -->"


def knowledge_status_command():
    backend = STORAGE.get("backend") if BRANCH else BACKEND
    return 'jj -R "$B/.." status' if backend == "jj" else 'git -C "$B/.." status'


def knowledge_push_policy():
    return CONFIG.get("push_policy", "Push only when explicitly authorized.").strip()


def agents_bootstrap():
    text = "## Repository knowledge (RKM)\n\n"
    if BRANCH:
        text += ("Specs, decisions, todos, bugs, roadmaps, process notes and contributor guides\n"
                 f"are **not in this checkout**. They live on `{BRANCH}` in a separate checkout\n"
                 "shared by this clone's code workspaces.\n")
    else:
        text += ("Store todos, bugs, specs, decisions, roadmaps, process notes and contributor\n"
                 "guides in this checkout's `rkm/` bundle.\n")
    text += ("RKM paths elsewhere in this file, such as `rkm/spec/x.md`, refer to that bundle.\n\n"
             "```bash\n"
             'export RKM_ACTOR="agent/model"   # replace with your agent/model identity\n'
             'B=$(rkm path)                    # locate the bundle\n'
             'rg "search term" "$B"            # search knowledge\n'
             + knowledge_status_command() + "           # review pending edits\n"
             "rkm conventions                  # read and follow the full rules\n"
             "```\n\n"
             "Read `$B/index.md` and relevant specs, decisions and process notes before working.\n"
             "Create, rename, retype, close and delete through `rkm`; edit bodies by hand.\n")
    if BRANCH:
        text += ("**Commit knowledge changes before you finish** with `rkm commit --summary\n"
                 "TEXT`; they are not recorded until you do. It commits everything pending in\n"
                 "the bundle, including other sessions' edits, so review its status first.\n"
                 "Knowledge commits stay separate from code commits and are never merged\n"
                 "into them.\n")
    else:
        text += "Commit `rkm/` changes with your usual VCS, alongside the code they describe.\n"
    text += ('When code finishes a todo or fixes a bug, run `rkm close <id> --note\n'
             '"<commit-or-change-id>"` in the same session.\n'
             "Push policy: " + knowledge_push_policy() + "\n")
    if BRANCH:
        text += ("Missing configured checkouts are recreated from existing history. A new clone\n"
                 "needs the fetched knowledge branch; unavailable history or an occupied location\n"
                 "requires action.\n")
    text += "Keep repository-specific rules outside this managed section.\n"
    return text


def agents_block():
    declaration = "\n<!-- rkm:branch " + json.dumps(BRANCH) + " -->" if BRANCH else ""
    return AGENTS_BEGIN + declaration + "\n" + agents_bootstrap().rstrip("\n") + "\n" + AGENTS_END


def cmd_agents(a):
    if a.write:
        changed = update_agents()
        state = "updated" if changed else "already up to date"
        print(f"Managed RKM section in {os.path.join(REPO, 'AGENTS.md')}: {state}.")
    else:
        print(agents_block())


def agents_content():
    path = os.path.join(REPO, "AGENTS.md")
    if os.path.islink(path):
        raise Fail("AGENTS.md is a symbolic link; use a regular file before enabling branch storage")
    try:
        with open(path, encoding="utf-8", newline="") as f:
            content = f.read()
    except FileNotFoundError:
        return ""
    if (content.count(AGENTS_BEGIN), content.count(AGENTS_END)) not in ((0, 0), (1, 1)) or (
            AGENTS_BEGIN in content and content.index(AGENTS_BEGIN) > content.index(AGENTS_END)):
        raise Fail("AGENTS.md has malformed or duplicate RKM markers; repair them before enabling branch storage")
    return content


def update_agents():
    """Own only the marked RKM block; preserve existing project instructions."""
    actor()
    path = os.path.join(REPO, "AGENTS.md")
    content = agents_content()
    newline = "\r\n" if "\r\n" in content else "\n"
    block = agents_block().replace("\n", newline)
    if AGENTS_BEGIN in content:
        start = content.index(AGENTS_BEGIN)
        end = content.index(AGENTS_END) + len(AGENTS_END)
        updated = content[:start] + block + content[end:]
    else:
        # Recognize an untouched section pasted from older `rkm conventions`.
        # Customized legacy prose remains intact; the new section explains
        # that all its RKM references use the external logical layout.
        full_legacy = conventions_text(False)
        candidates = (full_legacy, full_legacy.split("\n## Knowledge commit messages\n", 1)[0])
        legacy = next((s.replace("\n", newline) for s in candidates if s.replace("\n", newline) in content), None)
        if legacy is not None:
            updated = content.replace(legacy, block + newline, 1)
        else:
            separator = "" if not content or content.endswith(newline * 2) else (
                newline if content.endswith(newline) else newline * 2)
            updated = content + separator + block + newline
    if updated == content:
        return False
    temp = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=REPO,
                                         prefix=".rkm-agents-", delete=False) as f:
            temp = f.name
            f.write(updated)
        if os.path.exists(path):
            os.chmod(temp, os.stat(path).st_mode & 0o7777)
        else:
            os.chmod(temp, 0o644)
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
    return True


def initialize_bundle(a):
    actor()  # refuses a bad actor before anything is written
    os.makedirs(BUNDLE, exist_ok=True)
    minor = ".".join(VERSION.split(".")[:2])
    with open(os.path.join(BUNDLE, "config.toml"), "w", encoding="utf-8") as f:
        f.write("# Read by rkm; `rkm --help` lists the keys.\n\n"
                f"name = {json.dumps(a.name or os.path.basename(REPO))}\n"
                f'requires = "{minor}"\n')
    configure_bundle()
    write_index()


def cmd_init(a):
    if not a.in_tree:
        if not BACKEND:
            raise Fail("branch storage needs a jj or Git repository; "
                       "`rkm init --in-tree` keeps knowledge in rkm/ instead")
        # Keep a configured branch; a new clone attaches the one AGENTS.md declares.
        a.branch = a.branch or BRANCH or declared_branch() or None
        enable_branch(a)
        return
    for flag, value in (("--branch", a.branch), ("--workspace", a.workspace), ("--message", a.message)):
        if value is not None:
            raise Fail(f"{flag} is for branch storage; drop --in-tree")
    if BRANCH or has_bundle(REPO):
        raise Fail(f"{REPO} already has an rkm/ bundle")
    initialize_bundle(a)
    print(f"Created rkm/ in {REPO}. Add this section to AGENTS.md "
          "(`rkm conventions` gives the full rules):\n")
    print(agents_block())


def cmd_migrate(a):
    enable_branch(a, migrate=True)


def cmd_path(a):
    print(BUNDLE)


def cmd_commit(a):
    if not BRANCH:
        raise Fail("`rkm commit` is for branch storage; commit an in-tree bundle with your usual VCS")
    actor()
    if a.message is not None and a.kind is not None:
        raise Fail("--kind is for --summary; a complete -m message is used unchanged")
    message = commit_message(message=a.message) if a.message is not None else None
    if STORAGE["backend"] == "jj":
        files = jj("file", "list", cwd=STORE_ROOT, snapshot=True).stdout.splitlines()
    else:
        files = git("ls-files", cwd=STORE_ROOT).stdout.splitlines()
    if any(not f.startswith("rkm/") for f in files):
        raise Fail("knowledge workspace contains tracked files outside rkm/; refusing to commit")
    if message is None:
        message = commit_message(a.summary, kind=a.kind or pending_commit_kind())
    commit_knowledge(message, STORAGE["backend"], BRANCH, STORE_ROOT)
    print(f"Recorded knowledge on {BRANCH}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rkm", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"rkm {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("kind")
    p.add_argument("slug", nargs="?", help="default: derived from the title")
    p.add_argument("--title", required=True)
    p.add_argument("--description", help="required from an agent")
    p.add_argument("--draft", action="store_true")
    p.add_argument("--from", dest="from_", metavar="ORIGIN",
                   help="where it was asked for: a link, or a label like app:slack")
    p.add_argument("--to", metavar="LABEL", help="for a request: who it is put to, e.g. repo:org/name")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("adopt")
    p.add_argument("path")
    p.add_argument("kind")
    p.add_argument("--title")
    p.add_argument("--description", help="required from an agent")
    p.add_argument("--state", default="open")
    p.add_argument("--decided", help="for a Decision: YYYY-MM-DD")
    p.add_argument("--as", dest="as_", metavar="ID", help="where a file from outside rkm/ goes")
    p.add_argument("--status", choices=("draft", "deprecated"))
    p.add_argument("--from", dest="from_", metavar="ORIGIN",
                   help="where it was asked for: a link, or a label like app:slack")
    p.set_defaults(fn=cmd_adopt)
    p = sub.add_parser("close")
    p.add_argument("id")
    p.add_argument("--note", help="kept in `closed`, e.g. the commit or change that did it")
    p.set_defaults(fn=cmd_close)
    for name, fn in (("reopen", cmd_reopen), ("touch", cmd_touch)):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.set_defaults(fn=fn)
    p = sub.add_parser("state", help="move an item to a state between open and done")
    p.add_argument("id")
    p.add_argument("state")
    p.set_defaults(fn=cmd_state)
    p = sub.add_parser("from", help="record another place that asked for an item")
    p.add_argument("id")
    p.add_argument("origin", help="a link, or a label like app:slack or repo:org/name")
    p.set_defaults(fn=cmd_from)
    p = sub.add_parser("retype")
    p.add_argument("id")
    p.add_argument("kind")
    p.set_defaults(fn=cmd_retype)
    p = sub.add_parser("status")
    p.add_argument("id")
    p.add_argument("status", choices=STATUSES)
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("set")
    p.add_argument("id")
    p.add_argument("field", choices=("title", "description", "tags", "to"))
    p.add_argument("value")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("verify")
    p.add_argument("id")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("mv")
    p.add_argument("id")
    p.add_argument("new")
    p.set_defaults(fn=cmd_mv)
    p = sub.add_parser("rm")
    p.add_argument("id")
    p.set_defaults(fn=cmd_rm)
    p = sub.add_parser("ls")
    p.add_argument("--type")
    p.add_argument("--state")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("-a", "--all", action="store_true",
                   help="include closed and deprecated documents")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_ls)
    sub.add_parser("index").set_defaults(fn=cmd_index)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("conventions").set_defaults(fn=cmd_conventions)
    p = sub.add_parser("agents", help="print or write the short managed AGENTS.md section")
    p.add_argument("--write", action="store_true", help="update AGENTS.md, preserving other instructions")
    p.set_defaults(fn=cmd_agents)
    p = sub.add_parser("init")
    p.add_argument("--name", help="project name for the index intro (default: the directory name)")
    p.add_argument("--branch", nargs="?", const="_rkm", help="knowledge bookmark/branch (default: _rkm)")
    p.add_argument("--workspace", help="knowledge workspace location (default: under the global RKM "
                   "parent, else ../<repo>-rkm)")
    p.add_argument("-m", "--message", help="complete message for the initial knowledge commit")
    p.add_argument("--in-tree", action="store_true", help="keep knowledge in rkm/ in this checkout instead")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("migrate")
    p.add_argument("--branch", default="_rkm", help="knowledge bookmark/branch (default: _rkm)")
    p.add_argument("--workspace", help="knowledge workspace location (default: under the global RKM "
                   "parent, else ../<repo>-rkm)")
    p.add_argument("-m", "--message", help="complete message for the migration commit")
    p.set_defaults(fn=cmd_migrate)
    sub.add_parser("path").set_defaults(fn=cmd_path)
    p = sub.add_parser("commit")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("-m", "--message", help="complete message, used unchanged")
    group.add_argument("--summary", help="summary to render with the configured commit template")
    p.add_argument("--kind", help="scope for --summary (default: inferred, or rkm for mixed changes)")
    p.set_defaults(fn=cmd_commit)
    p = sub.add_parser("config", help="view or save global RKM preferences")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--workspace-root", help="global parent directory for RKM knowledge workspaces "
                       "(default: beside each repository)")
    group.add_argument("--unset-workspace-root", action="store_true", help="clear the global location preference")
    p.set_defaults(fn=cmd_config)
    p = sub.add_parser("workspace", help="remove or reattach the knowledge workspace")
    workspace_sub = p.add_subparsers(dest="action", required=True)
    p = workspace_sub.add_parser("add")
    p.add_argument("--workspace", help="override the saved or default knowledge location")
    p.set_defaults(fn=cmd_workspace)
    workspace_sub.add_parser("remove").set_defaults(fn=cmd_workspace)
    a = ap.parse_args(argv)
    try:
        declaration = declared_branch() if (not BRANCH and not has_bundle(REPO)
                                            and a.fn not in (cmd_init, cmd_migrate, cmd_config, cmd_workspace)) else ""
        lock = storage_lock() if a.fn is not cmd_config and REPO_META and (BRANCH or declaration
                                               or a.fn in (cmd_migrate, cmd_workspace) or
                                               (a.fn is cmd_init and not a.in_tree)) else contextlib.nullcontext()
        with lock:
            # Another process may have enabled branch mode while we waited.
            if a.fn is not cmd_config and STORAGE_FILE and not BRANCH and os.path.exists(STORAGE_FILE):
                raise Fail("storage mode changed while this command was waiting; run it again")
            if declaration:
                if not knowledge_revision(BACKEND, declaration):
                    raise Fail(f"knowledge branch {declaration} is unavailable; fetch it before using RKM")
                with contextlib.redirect_stdout(sys.stderr):
                    enable_branch(argparse.Namespace(branch=declaration, workspace=None, name=None))
            if BRANCH and a.fn not in (cmd_init, cmd_migrate, cmd_config, cmd_workspace):
                if not os.path.isdir(STORE_ROOT):
                    restore_workspace()
                validate_storage()
                configure_bundle()
            if a.fn not in (cmd_init, cmd_migrate, cmd_config, cmd_workspace) and not has_bundle(STORE_ROOT):
                raise Fail("no rkm/ bundle here or above; `rkm init` starts one")
            a.fn(a)
    except (Fail, OSError) as e:
        print(f"rkm: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
