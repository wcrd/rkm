#!/usr/bin/env python3
"""rkm — create, change and check the knowledge in rkm/.

rkm/ is an Open Knowledge Format v0.2 bundle of a repository's contributor and
agent knowledge (`rkm conventions` prints the rules). This tool is the only
way its frontmatter, file names and index change; bodies are edited by hand.

One file, Python 3.11+ standard library only. It lives in its own repository
and is installed from there with `uv tool install`; `python3 rkm.py` runs it
in place. Each repository has its own rkm/config.toml.

  rkm init [--name N]              start rkm/ in this repository
  rkm new todo|bug|spec|decision|roadmap|process <slug> --title T --description D
  rkm adopt <path> <kind> --description D [--as ID]
                                   frontmatter for a file without it; a file
                                   outside rkm/ moves in, its links rewritten
  rkm close <id> [--note N]        state: done, closed stamped
  rkm reopen <id>
  rkm retype <id> <type>           the file stays where it is
  rkm status <id> draft|stable|deprecated
  rkm set <id> title|description|tags <value>
  rkm touch <id>                   restamp `generated` after rewriting the body
  rkm verify <id>                  human sign-off; refused for agents
  rkm mv <id> <new-id>             rename and rewrite every link to it
  rkm rm <id>                      delete; refused while anything links to it
  rkm ls [--type T] [--state S] [--status S] [--json]
  rkm index                        regenerate rkm/index.md
  rkm check                        conformance; run it as a CI gate
  rkm conventions                  the rules, as a section for AGENTS.md

An <id> is a concept id (`todos/regex-engine-swap`), a path
(`rkm/todos/regex-engine-swap.md`) or a bare slug when it is unique.

The repo is RKM_ROOT, else the nearest directory at or above the working
directory with an rkm/ bundle.

rkm/config.toml, optional, holds what differs between repositories:

  name             project name in the default index intro (default: directory name)
  intro            index intro, replacing the default
  requires         the oldest rkm the bundle works with, e.g. "0.1"
  skip             repo paths that `mv` and `check` don't read
  text_extensions  further extensions whose links `mv` rewrites
  [types.<Name>]   add a type or change a default one: dir (where `rkm new`
                   puts it; none means adopt only), section (in the index),
                   tracked (carries `state`), template (new body), about
                   (its row in `rkm conventions`)

Writes are stamped with an actor: RKM_ACTOR, else `human:$USER`. An agent
must set RKM_ACTOR to `<agent>/<model>`, e.g. `claude/opus-5.5`.
"""
import argparse
import datetime
import getpass
import json
import os
import re
import subprocess
import sys

if sys.version_info < (3, 11):
    sys.exit(f"rkm needs Python 3.11 or newer; this is {sys.version.split()[0]}")

import tomllib  # noqa: E402  (3.11+)

VERSION = "0.1.0"


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
    for cmd in (["jj", "root"], ["git", "rev-parse", "--show-toplevel"]):
        try:
            return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return os.getcwd()


REPO = find_repo()
BUNDLE = os.path.join(REPO, "rkm")
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
    "Process": {"dir": "process", "section": "Process", "about": "How to make a kind of change."},
    "Guide": {"section": "Guides"},
}
DEFAULT_TEMPLATES = {
    "Bug": "## Repro\n\n## Expected\n\n## Actual\n",
    "Todo": "## Why\n\n## What\n",
    # A decision says why something is the way it is, and what reopening it
    # would cost. Keep it short and link to the spec rather than restate it.
    "Decision": "**Decision.** One or two sentences.\n\n**Why.** The reasons, with links to the "
                "specs and code that carry them.\n\n**Not chosen.** The alternatives and why "
                "they lost.\n\n**Open.** Known gaps, inconsistencies, or follow-up work.\n",
}


CONFIG_KEYS = {"name", "intro", "requires", "skip", "text_extensions", "types"}
TYPE_KEYS = {"dir", "section", "tracked", "template", "about"}


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
    return config


CONFIG = load_config()


def version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


# A bundle names the oldest rkm it works with; an older rkm stops here rather
# than misreading it. A newer rkm always reads an older bundle.
if "requires" in CONFIG and version_key(VERSION) < version_key(str(CONFIG["requires"])):
    sys.exit(f"rkm: this bundle needs rkm {CONFIG['requires']} or newer; this is {VERSION}. "
             "Pull the rkm repository.")
TYPE_DEFS = {t: dict(v) for t, v in DEFAULT_TYPES.items()}
for t, v in CONFIG.get("types", {}).items():
    TYPE_DEFS.setdefault(t, {}).update(v)
TYPES = set(TYPE_DEFS)
# `rkm new` kinds: type name and the directory a new file goes in.
KINDS = {t.lower(): (t, v["dir"]) for t, v in TYPE_DEFS.items() if v.get("dir")}
TRACKED = {t for t, v in TYPE_DEFS.items() if v.get("tracked")}  # carry `state`
TEMPLATES = {**DEFAULT_TEMPLATES,
             **{t: v["template"] for t, v in TYPE_DEFS.items() if "template" in v}}
STATES = ("open", "done")
STATUSES = ("draft", "stable", "deprecated")
KEY_ORDER = ["type", "title", "description", "state", "status", "decided", "tags",
             "generated", "closed", "verified", "stale_after", "sources"]
QUOTED = {"title", "description"}
WRITES = {"type", "title", "description", "state", "status", "decided", "tags",
          "generated", "closed", "verified"}

DATE = re.compile(r"^\d{4}-\d\d-\d\d$")

# Repo files whose links `rkm mv` rewrites and `rkm check` follows.
SKIP_NAMES = {".git", ".jj", "target", "node_modules"}
SKIP_PATHS = {p.strip("/") for p in CONFIG.get("skip", [])}
TEXT_EXT = {".md", ".html", ".rs", ".py", ".ts", ".js", ".mjs", ".swift", ".go", ".sh",
            ".toml", ".json", ".yml", ".yaml", ".txt", *CONFIG.get("text_extensions", [])}

ACTOR = re.compile(r"^(human|process|team):[\w.@-]+$|^[\w.-]+/[\w.-]+$")
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:\d\d)$")
BARE = re.compile(r"^[A-Za-z0-9][\w.+-]*$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Fail(Exception):
    pass


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
    if key not in QUOTED and (ACTOR.match(v) or TIMESTAMP.match(v) or BARE.match(v)):
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
                if k == "verified" and isinstance(v, list):
                    out.append("verified:")
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


def log(kind, doc, note=None):
    log_entry(f"* **{kind}**: [{doc.get('title') or doc.id}]({doc.rel})"
              + (f": {note}" if note else "") + f" ({actor()})")


def log_entry(entry):
    path = os.path.join(BUNDLE, "log.md")
    lines = open(path, encoding="utf-8").read().splitlines() if os.path.exists(path) else ["# Update log", ""]
    day = now()[:10]
    heads = [i for i, l in enumerate(lines) if l.startswith("## ")]
    if heads and lines[heads[0]] == f"## {day}":
        lines.insert(heads[0] + 2, entry)
    else:
        at = heads[0] if heads else len(lines)
        lines[at:at] = [f"## {day}", "", entry, ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")


# --- index ------------------------------------------------------------------

def section_of(d):
    t, status = d.get("type"), d.get("status")
    if status == "deprecated":
        return "Deprecated"
    if t in TRACKED and d.get("state") == "done":
        return "Closed"
    return TYPE_DEFS.get(t, {}).get("section", "Other")


SECTIONS = [v.get("section", t) for t, v in TYPE_DEFS.items()] + ["Closed", "Deprecated", "Other"]
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
            lines.append(f"* [{d.get('title') or d.id}]({d.rel}){mark} - {d.get('description', '')}")
        lines.append("")
    return "\n".join(lines)


def write_index():
    with open(os.path.join(BUNDLE, "index.md"), "w", encoding="utf-8") as f:
        f.write(render_index(load_all()))


# --- links ------------------------------------------------------------------

LINK = re.compile(r"(?P<pre>\]\(<?)(?P<t>[^)\s>]+)|(?P<hpre>(?:href|src)=\")(?P<ht>[^\"\s]+)\"")
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
TARGET = re.compile(r"^(?P<p>[^#]*?)(?P<line>:\d+(?:-\d+)?)?(?P<frag>#.*)?$")


def repo_files():
    """Tracked text files, repo-relative. Falls back to a walk outside jj."""
    try:
        out = subprocess.run(["jj", "file", "list"], cwd=REPO, check=True,
                             capture_output=True, text=True).stdout
        files = out.splitlines()
    except (OSError, subprocess.CalledProcessError):
        files = list(walk_files())
    for f in files:
        if os.path.splitext(f)[1] in TEXT_EXT and not any(
                f == p or f.startswith(p + "/") for p in SKIP_PATHS):
            yield f


def walk_files():
    for d, dirs, files in os.walk(REPO):
        here = os.path.relpath(d, REPO)
        dirs[:] = [x for x in dirs if x not in SKIP_NAMES
                   and os.path.normpath(os.path.join(here, x)) not in SKIP_PATHS]
        for f in files:
            yield os.path.relpath(os.path.join(d, f), REPO)


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

def cmd_new(a):
    if a.kind not in KINDS:
        raise Fail(f"kind must be one of {', '.join(KINDS)}")
    if not SLUG.match(a.slug):
        raise Fail("slug is lower-case letters, digits, - and _")
    t, d = KINDS[a.kind]
    if t == "Decision" and not re.match(r"^\d\d-", a.slug):
        taken = [int(f[:2]) for f in os.listdir(os.path.join(BUNDLE, d))
                 if re.match(r"^\d\d-.*\.md$", f)] if os.path.isdir(os.path.join(BUNDLE, d)) else []
        a.slug = f"{max(taken, default=0) + 1:02d}-{a.slug}"
    path = os.path.join(BUNDLE, d, a.slug + ".md")
    if os.path.exists(path):
        raise Fail(f"{os.path.relpath(path, REPO)} exists")
    hits = [p for p in concept_paths() if os.path.basename(p) == a.slug + ".md"]
    if hits:
        raise Fail(f"slug {a.slug} is taken by {os.path.relpath(hits[0], BUNDLE)[:-3]}")
    generated = stamp()  # refuses a bad actor before anything is written
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = f"# {a.title}\n\n" + TEMPLATES.get(t, "")
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\ntype: " + t + "\n---\n" + body)
    doc = Doc(path)
    doc.set("title", a.title)
    doc.set("description", a.description)
    if t in TRACKED:
        doc.set("state", "open")
    if a.draft:
        doc.set("status", "draft")
    if t == "Decision":
        doc.set("decided", now()[:10])
    doc.set("generated", generated)
    doc.save()
    log("Creation", doc)
    write_index()
    print(os.path.relpath(path, REPO))


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
        raise Fail(f"{os.path.relpath(path, REPO)} exists")
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
    actor()  # refuses a bad actor before anything is written
    if t == "Decision" and not (a.decided and DATE.match(a.decided)):
        raise Fail("a Decision needs --decided YYYY-MM-DD")
    if outside:
        move_file(src, os.path.relpath(path, REPO))
        text = open(path, encoding="utf-8").read()
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\ntype: " + t + "\n---\n" + text)
    doc = Doc(path)
    doc.set("title", title)
    doc.set("description", a.description)
    if t in TRACKED:
        doc.set("state", a.state)
    if t == "Decision":
        doc.set("decided", a.decided)
    if a.status:
        doc.set("status", a.status)
    doc.save()
    log("Adoption", doc, f"from {src}" if outside else None)
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
    doc.set("closed", stamp())
    doc.save()
    log("Closure", doc, a.note)
    write_index()


def cmd_reopen(a):
    doc = resolve(a.id)
    tracked(doc)
    if doc.get("state") != "done":
        raise Fail(f"{doc.id} is not done")
    doc.set("state", "open")
    doc.set("closed", None)
    doc.save()
    log("Reopening", doc, a.note)
    write_index()


def cmd_retype(a):
    doc = resolve(a.id)
    new = KINDS[a.kind][0] if a.kind in KINDS else a.kind
    if new not in TYPES:
        raise Fail(f"type must be one of {', '.join(sorted(TYPES))}")
    old = doc.get("type")
    doc.set("type", new)
    if new in TRACKED and old not in TRACKED:
        doc.set("state", "open")
    if new not in TRACKED:
        doc.set("state", None)
        doc.set("closed", None)
    doc.save()
    log("Update", doc, f"{old} → {new}")
    write_index()


def cmd_status(a):
    doc = resolve(a.id)
    doc.set("status", None if a.status == "stable" else a.status)
    doc.save()
    log("Deprecation" if a.status == "deprecated" else "Update", doc, f"status {a.status}")
    write_index()


def cmd_set(a):
    doc = resolve(a.id)
    if a.field == "tags":
        value = [t.strip() for t in a.value.split(",") if t.strip()]
        doc.set("tags", value or None)
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
    log("Update", doc, a.note)


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
    log("Verification", doc)


def move_file(old, new):
    """Move repo-relative `old` to `new` and rewrite every link to it."""
    # Compute every rewrite before writing any, so a failure changes nothing.
    edits = {}
    for f in repo_files():
        path = os.path.join(REPO, f)
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
    dest = os.path.join(REPO, new)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.rename(os.path.join(REPO, old), dest)
    return new


def cmd_mv(a):
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
    new = move_file(os.path.relpath(doc.path, REPO), os.path.relpath(dest, REPO))
    moved = Doc(dest)
    log("Update", moved, f"moved from {doc.id}")
    write_index()
    print(new)


def inbound(target):
    """Repo files with a link that resolves to `target` (repo-relative)."""
    hits = []
    for f in repo_files():
        if f in (target, "rkm/index.md", "rkm/log.md") or not f.endswith((".md", ".html")):
            continue
        try:
            content = open(os.path.join(REPO, f), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for _, t, tm, _ in link_targets(content):
            if os.path.normpath(os.path.join(os.path.dirname(f), tm.group("p"))) == target:
                hits.append(f)
                break
    return hits


def cmd_rm(a):
    doc = resolve(a.id)
    rel = os.path.relpath(doc.path, REPO)
    hits = inbound(rel)
    if hits:
        raise Fail(f"{doc.id} is still linked from: " + ", ".join(hits))
    log("Deletion", doc, a.note)
    os.remove(doc.path)
    write_index()


def cmd_ls(a):
    rows = []
    for d in load_all():
        if a.type and d.get("type", "").lower() != a.type.lower():
            continue
        if a.state and d.get("state") != a.state:
            continue
        if a.status and (d.get("status") or "stable") != a.status:
            continue
        rows.append(d)
    if a.json:
        print(json.dumps([{"id": d.id, **{k: v for k, v in d.values.items()}} for d in rows],
                         ensure_ascii=False, indent=1))
        return
    for d in rows:
        flag = d.get("state") or d.get("status") or ""
        print(f"{d.id:55} {d.get('type', ''):9} {flag:10} {d.get('title', '')}")


def cmd_index(a):
    write_index()


def problems():
    out = []
    docs = []
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
            if not isinstance(v, str) or not v.strip() or v == "TODO":
                bad(f"no `{k}`")
        if t in TRACKED:
            if d.get("state") not in STATES:
                bad(f"state must be {' or '.join(STATES)}")
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
            content = open(os.path.join(REPO, f), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for _, t, tm, _ in link_targets(content):
            target = os.path.normpath(os.path.join(os.path.dirname(f), tm.group("p")))
            if (target == "rkm" or target.startswith("rkm/")) and not os.path.exists(os.path.join(REPO, target)):
                out.append(f"{f}: broken link to {t}")
    index = os.path.join(BUNDLE, "index.md")
    if not os.path.exists(index) or open(index, encoding="utf-8").read() != render_index(docs):
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

# `rkm conventions` prints this for a repository's AGENTS.md, with the
# bundle's own types in the table. It is the canonical statement of the
# rules; `check` enforces them.
CONVENTIONS = """\
## Where knowledge lives

Everything a contributor or agent works from is in `rkm/` (repository
knowledge management), listed in [rkm/index.md](rkm/index.md):

| Directory | `type` | What goes there |
|-----------|--------|-----------------|
{rows}
| `rkm/archive/` | any, `status: deprecated` | History. Don't treat it as current. |

`rkm/` is an [Open Knowledge Format v0.2]({okf})
bundle: every file opens with frontmatter (`type`, `title`, `description`,
`state` for todos and bugs, and who wrote and reviewed it), `rkm/index.md` lists
them, and `rkm/log.md` records each change. `rkm/config.toml` says which types
the bundle uses beyond rkm's defaults, which trees rkm leaves alone, and the
oldest rkm it works with (`requires`).

`rkm` is a single Python 3.11+ script kept in its own repository, not in this
one; install it with `uv tool install <rkm repository>` and update it with
`uv tool upgrade rkm`. `rkm --version` says which you have.

**Change it only through `rkm`.** Don't create, rename, move or delete
files in `rkm/`, and don't edit their frontmatter, `index.md` or `log.md`, by
hand. Edit a file's body by hand. Set `RKM_ACTOR` to yourself first; an agent
that doesn't is refused.

```bash
export RKM_ACTOR=claude/opus-5.5          # <agent>/<model>
rkm new todo regex-engine-swap --title "Replace regexlite" --description "One sentence."
rkm new bug  lsp-crash-on-empty  --title "…" --description "…"   # Repro / Expected / Actual
rkm new decision closed-by-default --title "…" --description "…"  # numbered, dated
rkm close regex-engine-swap --note "landed in feat/regex"
rkm retype regex-engine-swap bug  # the file stays where it is
rkm touch <id>                    # after rewriting a body: stamps `generated`
rkm set <id> description "…"      # title, description or tags
rkm status <id> draft             # draft | stable | deprecated
rkm mv <id> <new-id>              # renames and rewrites every link to it
rkm rm <id>                       # refused while anything links to it
rkm adopt <path> <kind> --description "…"  # a file that arrived without frontmatter
rkm adopt notes/x.md process --description "…"   # moves it in from outside rkm/, rewriting links
rkm ls --type bug --state open
rkm check                         # conformance: frontmatter, links, index
```

- **A file never moves when its state changes.** In OKF a concept's path is
  its identity. Closing a todo sets `state: done`; it stays in `rkm/todos/`.
  Turning a todo into a bug changes `type`, not the directory.
- **`status` is not progress.** It is OKF's lifecycle of the document
  (`draft`, `stable`, `deprecated`); a todo's progress is `state`.
- **`verify` is a human sign-off.** rkm refuses it from an agent. A file only
  an agent wrote, with no `verified`, is unreviewed; say so when you rely on it.
- Link between files with relative links, not `/`-rooted ones, so they work on
  GitHub and in the editor.
"""


def cmd_conventions(a):
    rows = "\n".join(f"| `rkm/{v['dir']}/` | `{t}` | {v.get('about', '')} |"
                     for t, v in TYPE_DEFS.items() if v.get("dir"))
    print(CONVENTIONS.replace("{rows}", rows).replace("{okf}", OKF), end="")


def cmd_init(a):
    if has_bundle(REPO):
        raise Fail(f"{REPO} already has an rkm/ bundle")
    who = actor()  # refuses a bad actor before anything is written
    os.makedirs(BUNDLE, exist_ok=True)
    minor = ".".join(VERSION.split(".")[:2])
    with open(os.path.join(BUNDLE, "config.toml"), "w", encoding="utf-8") as f:
        f.write("# Read by rkm; `rkm --help` lists the keys.\n\n"
                f"name = {json.dumps(a.name or os.path.basename(REPO))}\n"
                f'requires = "{minor}"\n')
    log_entry(f"* **Initialization**: Created rkm/ with rkm {VERSION} ({who})")
    write_index()
    print(f"Created rkm/ in {REPO}. Add this section to AGENTS.md "
          "(`rkm conventions` prints it again):\n")
    cmd_conventions(a)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rkm", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"rkm {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("kind")
    p.add_argument("slug")
    p.add_argument("--title", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--draft", action="store_true")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("adopt")
    p.add_argument("path")
    p.add_argument("kind")
    p.add_argument("--title")
    p.add_argument("--description", required=True)
    p.add_argument("--state", choices=STATES, default="open")
    p.add_argument("--decided", help="for a Decision: YYYY-MM-DD")
    p.add_argument("--as", dest="as_", metavar="ID", help="where a file from outside rkm/ goes")
    p.add_argument("--status", choices=("draft", "deprecated"))
    p.set_defaults(fn=cmd_adopt)
    for name, fn in (("close", cmd_close), ("reopen", cmd_reopen), ("touch", cmd_touch)):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--note")
        p.set_defaults(fn=fn)
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
    p.add_argument("field", choices=("title", "description", "tags"))
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
    p.add_argument("--note")
    p.set_defaults(fn=cmd_rm)
    p = sub.add_parser("ls")
    p.add_argument("--type")
    p.add_argument("--state", choices=STATES)
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_ls)
    sub.add_parser("index").set_defaults(fn=cmd_index)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("conventions").set_defaults(fn=cmd_conventions)
    p = sub.add_parser("init")
    p.add_argument("--name", help="project name for the index intro (default: the directory name)")
    p.set_defaults(fn=cmd_init)
    a = ap.parse_args(argv)
    try:
        if a.fn is not cmd_init and not has_bundle(REPO):
            raise Fail("no rkm/ bundle here or above; `rkm init` starts one")
        a.fn(a)
    except Fail as e:
        print(f"rkm: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
