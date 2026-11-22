"""Tests for rkm, each against a throwaway repo with an rkm/ bundle.

  python3 -m unittest discover -s tests
"""
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

RKM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rkm.py")
NOW = "2026-10-12T09:00:00Z"


class Repo:
    def __init__(self, root):
        self.root = root

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as f:
            return f.read()

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))

    def run(self, *args, actor="human:tester", agent=False, ok=True, script=RKM, root=True, cwd=None):
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "RKM_ACTOR", "RKM_ROOT")}
        env.update(RKM_NOW=NOW)
        if root:
            env["RKM_ROOT"] = self.root
        if actor:
            env["RKM_ACTOR"] = actor
        if agent:
            env["CLAUDECODE"] = "1"
        p = subprocess.run([sys.executable, "-I", script, *args], env=env, cwd=cwd,
                           capture_output=True, text=True)
        if ok and p.returncode != 0:
            raise AssertionError(f"rkm {' '.join(args)} failed:\n{p.stdout}{p.stderr}")
        if not ok and p.returncode == 0:
            raise AssertionError(f"rkm {' '.join(args)} should have failed:\n{p.stdout}")
        return p.stdout + p.stderr


class RkmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)
        self.repo.write("rkm/log.md", "# Update log\n")
        self.repo.run("index")

    def tearDown(self):
        self.tmp.cleanup()

    def new_todo(self, slug="a", title="Do A"):
        self.repo.run("new", "todo", slug, "--title", title, "--description", f"Why {slug}.")

    def test_new_writes_frontmatter_and_index_but_not_the_log(self):
        self.new_todo()
        text = self.repo.read("rkm/todos/a.md")
        self.assertTrue(text.startswith(
            '---\ntype: Todo\ntitle: "Do A"\ndescription: "Why a."\nstate: open\n'
            f"generated: {{ by: human:tester, at: {NOW} }}\n---\n# Do A\n"))
        self.assertIn("* [Do A](todos/a.md) - Why a.", self.repo.read("rkm/index.md"))
        self.assertEqual(self.repo.read("rkm/log.md"), "# Update log\n")
        self.assertIn("all conformant", self.repo.run("check"))

    def test_new_refuses_a_taken_slug(self):
        self.new_todo()
        self.repo.run("new", "bug", "a", "--title", "B", "--description", "d", ok=False)

    def test_close_and_reopen(self):
        self.new_todo()
        self.repo.run("close", "a", "--note", "landed in abc123")
        text = self.repo.read("rkm/todos/a.md")
        self.assertIn("state: done\n", text)
        self.assertIn(f'closed: {{ by: human:tester, at: {NOW}, note: "landed in abc123" }}', text)
        self.assertIn("all conformant", self.repo.run("check"))
        index = self.repo.read("rkm/index.md")
        self.assertIn("# Closed", index)
        self.assertIn("[Do A](todos/a.md) (todo)", index)
        self.repo.run("close", "a", ok=False)
        self.repo.run("reopen", "a")
        self.assertNotIn("closed:", self.repo.read("rkm/todos/a.md"))
        self.repo.run("close", "a")
        self.assertIn(f"closed: {{ by: human:tester, at: {NOW} }}\n", self.repo.read("rkm/todos/a.md"))
        self.repo.run("check")

    def test_index_resolves_its_own_conflicts_but_not_a_documents(self):
        self.new_todo()
        self.new_todo("b", "Do B")
        index = self.repo.read("rkm/index.md")
        self.repo.write("rkm/index.md", "<<<<<<< ours\n" + index + "=======\nother\n>>>>>>> theirs\n")
        out = self.repo.run("check", ok=False)
        self.assertIn("index.md: conflict markers; run `rkm index`", out)
        self.assertNotIn("out of date", out)
        a = self.repo.read("rkm/todos/a.md")
        self.repo.write("rkm/todos/a.md", a + "<<<<<<< Conflict 1 of 1\n%%%%%%% side\n>>>>>>> Conflict 1 of 1 ends\n")
        self.assertIn("resolve the conflicts in todos/a.md first", self.repo.run("index", ok=False))
        self.assertIn("todos/a.md: conflict markers; resolve them", self.repo.run("check", ok=False))
        self.repo.write("rkm/todos/a.md", a)
        self.repo.run("index")
        self.assertEqual(self.repo.read("rkm/index.md"), index)
        self.assertIn("all conformant", self.repo.run("check"))

    def test_retype_keeps_the_path(self):
        self.new_todo()
        self.repo.run("retype", "todos/a", "bug")
        self.assertIn("type: Bug\n", self.repo.read("rkm/todos/a.md"))
        self.assertIn("# Open bugs", self.repo.read("rkm/index.md"))
        self.repo.run("retype", "a", "spec")
        self.assertNotIn("state:", self.repo.read("rkm/todos/a.md"))
        self.repo.run("check")

    def test_unknown_keys_survive_a_write(self):
        self.repo.write("rkm/todos/k.md", '---\ntype: Todo\ntitle: "K"\ndescription: "d"\n'
                        "state: open\nowner:   someone   # kept as written\nsources:\n"
                        "  - id: s\n    resource: https://example.com\n"
                        "verified:\n  - { by: human:old, at: 2026-01-01T00:00Z }\n---\n# K\n")
        self.repo.run("close", "k")
        text = self.repo.read("rkm/todos/k.md")
        self.assertIn("owner:   someone   # kept as written\n", text)
        self.assertIn("sources:\n  - id: s\n    resource: https://example.com\n", text)
        # A sign-off from the removed `rkm verify` is kept as written.
        self.assertIn("verified:\n  - { by: human:old, at: 2026-01-01T00:00Z }\n", text)
        self.repo.run("check")

    def test_set_title_follows_the_heading(self):
        self.new_todo()
        self.repo.run("set", "a", "title", "Do A properly")
        text = self.repo.read("rkm/todos/a.md")
        self.assertIn('title: "Do A properly"', text)
        self.assertIn("\n# Do A properly\n", text)

    def test_mv_rewrites_links_everywhere(self):
        self.new_todo()
        self.repo.write("README.md", "See [the todo](rkm/todos/a.md#why) and `rkm/todos/a.md`.\n")
        self.repo.write("src/lib.rs", "// tracked in rkm/todos/a.md\n")
        self.repo.run("new", "spec", "s", "--title", "S", "--description", "d")
        with open(os.path.join(self.repo.root, "rkm/spec/s.md"), "a") as f:
            f.write("Plan: [../todos/a.md](../todos/a.md:3).\n")
        with open(os.path.join(self.repo.root, "rkm/todos/a.md"), "a") as f:
            f.write("Back to [the readme](../../README.md) and [S](../spec/s.md).\n")
        self.repo.run("mv", "a", "archive/old/b")
        self.assertFalse(self.repo.exists("rkm/todos/a.md"))
        self.assertIn("[the todo](rkm/archive/old/b.md#why) and `rkm/archive/old/b.md`",
                      self.repo.read("README.md"))
        self.assertIn("rkm/archive/old/b.md", self.repo.read("src/lib.rs"))
        self.assertIn("[../archive/old/b.md](../archive/old/b.md:3)", self.repo.read("rkm/spec/s.md"))
        moved = self.repo.read("rkm/archive/old/b.md")
        self.assertIn("[the readme](../../../README.md) and [S](../../spec/s.md)", moved)
        self.assertIn("all conformant", self.repo.run("check"))

    def test_check_finds_problems(self):
        self.new_todo()
        self.repo.write("rkm/todos/bad.md", '---\ntype: Todo\ntitle: "Bad"\ndescription: "TODO"\nstate: maybe\n---\n')
        self.repo.write("rkm/todos/done/old.md", '---\ntype: Todo\ntitle: "O"\ndescription: "d"\nstate: done\n---\n')
        self.repo.write("rkm/spec/x.md", '---\ntype: Widget\ntitle: "X"\ndescription: "d"\n---\n'
                        "[gone](../todos/gone.md)\n")
        out = self.repo.run("check", ok=False)
        for expected in ("todos/bad.md: no `description`", "todos/bad.md: state must be",
                         "todos/done/ is gone", "type 'Widget'", "broken link to ../todos/gone.md",
                         "index.md: out of date"):
            self.assertIn(expected, out)

    def test_agents_must_name_themselves(self):
        out = self.repo.run("new", "todo", "a", "--title", "A", "--description", "d",
                            actor=None, agent=True, ok=False)
        self.assertIn("set RKM_ACTOR", out)
        self.repo.run("new", "todo", "a", "--title", "A", "--description", "d",
                      actor="human:someone", agent=True, ok=False)
        self.repo.run("new", "todo", "a", "--title", "A", "--description", "d",
                      actor="claude/opus-5.5", agent=True)
        self.assertIn("by: claude/opus-5.5", self.repo.read("rkm/todos/a.md"))

    def test_description_is_optional_for_people_only(self):
        self.repo.run("new", "todo", "a", "--title", "A")
        text = self.repo.read("rkm/todos/a.md")
        self.assertNotIn("description:", text)
        self.assertIn("* [A](todos/a.md)\n", self.repo.read("rkm/index.md"))
        self.repo.run("new", "todo", "p", "--title", "P", actor="process:nightly")
        out = self.repo.run("new", "todo", "b", "--title", "B", actor="claude/opus-5.5",
                            agent=True, ok=False)
        self.assertIn("must pass --description", out)
        self.repo.run("new", "todo", "c", "--title", "C", actor="claude/opus-5.5", ok=False)
        self.assertFalse(self.repo.exists("rkm/todos/b.md"))
        self.repo.write("rkm/todos/bare.md", "# Bare\n")
        self.repo.run("adopt", "todos/bare", "todo", actor="claude/opus-5.5", agent=True, ok=False)
        self.repo.run("adopt", "todos/bare", "todo")
        self.assertIn("all conformant", self.repo.run("check"))

    def test_slug_derived_from_title(self):
        out = self.repo.run("new", "todo", "--title", "Replace the Régex engine (v2)!")
        self.assertIn("todos/replace-the-regex-engine-v2.md", out)
        self.repo.run("new", "spec", "--title", "A" * 20 + " " + "b" * 20 + " " + "c" * 20)
        self.assertTrue(self.repo.exists("rkm/spec/" + "a" * 20 + "-" + "b" * 20 + ".md"))
        out = self.repo.run("new", "bug", "--title", "Replace the regex engine v2", ok=False)
        self.assertIn("is taken by todos/replace-the-regex-engine-v2; is it the same item?", out)
        self.repo.run("new", "todo", "--title", "!!!", ok=False)
        self.repo.run("new", "decision", "--title", "05 Things")
        self.assertTrue(self.repo.exists("rkm/decisions/01-05-things.md"))
        self.repo.run("check")

    def test_from_records_each_place_that_asked(self):
        url = "https://team.slack.com/archives/C01/p17#x"
        self.repo.run("new", "todo", "a", "--title", "Do A", "--description", "d", "--from", url)
        text = self.repo.read("rkm/todos/a.md")
        self.assertIn(f"state: open\nfrom:\n  - {url}\ngenerated: ", text)
        self.repo.run("from", "a", "repo:org/other", actor="claude/opus-5.5", agent=True)
        self.assertIn("already lists", self.repo.run("from", "a", url))
        self.repo.run("touch", "a")
        text = self.repo.read("rkm/todos/a.md")
        self.assertIn(f"from:\n  - {url}\n  - repo:org/other\n", text)
        self.repo.run("from", "a", "not an origin", ok=False)
        self.repo.run("new", "todo", "b", "--title", "B", "--description", "d", "--from", "slack", ok=False)
        self.assertFalse(self.repo.exists("rkm/todos/b.md"))
        self.assertIn("all conformant", self.repo.run("check"))

    def test_from_written_by_hand_is_checked(self):
        self.new_todo()
        text = self.repo.read("rkm/todos/a.md").replace("state: open\n", "state: open\nfrom: app:slack\n")
        self.repo.write("rkm/todos/a.md", text)
        self.assertIn("all conformant", self.repo.run("check"))
        self.repo.run("from", "a", "app:linear")
        self.assertIn("from:\n  - app:slack\n  - app:linear\n", self.repo.read("rkm/todos/a.md"))
        self.repo.write("rkm/todos/a.md", text.replace("from: app:slack", "from: [app:slack, app:slack]"))
        self.assertIn("`from` lists an origin twice", self.repo.run("check", ok=False))
        self.repo.write("rkm/todos/a.md", text.replace("from: app:slack", "from: slack"))
        self.assertIn("`from` entries are links", self.repo.run("check", ok=False))

    def test_adopt_records_from(self):
        self.repo.write("notes/Ask.md", "# Ask\n")
        self.repo.run("adopt", "notes/Ask.md", "todo", "--description", "d", "--from", "app:slack")
        self.assertIn("from:\n  - app:slack\n", self.repo.read("rkm/todos/ask.md"))

    def test_request_is_put_to_someone_and_asked(self):
        self.repo.run("new", "request", "--title", "Comma links", "--description", "d",
                      "--to", "repo:org/okit")
        text = self.repo.read("rkm/requests/comma-links.md")
        self.assertIn("state: open\nto: repo:org/okit\ngenerated: ", text)
        self.assertIn("**Hit in:**", text)
        self.assertIn("# Open requests", self.repo.read("rkm/index.md"))
        self.repo.run("state", "comma-links", "asked")
        self.assertIn("state: asked\n", self.repo.read("rkm/requests/comma-links.md"))
        self.assertIn("requests/comma-links", self.repo.run("ls", "--state", "asked"))
        self.assertIn("all conformant", self.repo.run("check"))
        self.repo.run("state", "comma-links", "done", ok=False)
        self.repo.run("close", "comma-links", "--note", "delivered")
        self.repo.run("state", "comma-links", "asked", ok=False)
        self.repo.run("reopen", "comma-links")
        self.assertIn("state: open\n", self.repo.read("rkm/requests/comma-links.md"))
        self.repo.run("set", "comma-links", "to", "https://github.com/org/okit", ok=False)
        self.repo.run("set", "comma-links", "to", "team:platform")
        self.assertIn("to: team:platform\n", self.repo.read("rkm/requests/comma-links.md"))
        self.repo.run("new", "request", "--title", "X", "--description", "d", "--to", "okit", ok=False)

    def test_asked_belongs_to_requests_only(self):
        self.new_todo()
        self.repo.run("state", "a", "asked", ok=False)
        text = self.repo.read("rkm/todos/a.md")
        self.repo.write("rkm/todos/a.md", text.replace("state: open", "state: asked"))
        self.assertIn("state must be one of open, done", self.repo.run("check", ok=False))
        self.repo.write("rkm/todos/a.md", text)
        self.repo.run("new", "request", "r", "--title", "R", "--description", "d")
        self.repo.run("state", "r", "asked")
        self.repo.run("retype", "r", "todo")
        self.assertIn("state: open\n", self.repo.read("rkm/requests/r.md"))
        self.assertIn("all conformant", self.repo.run("check"))

    def test_config_states_run_from_open_to_done(self):
        self.repo.write("rkm/config.toml", '[types.Incident]\ndir = "incidents"\n'
                        'states = ["open", "mitigated", "done"]\n')
        self.repo.run("new", "incident", "i", "--title", "I", "--description", "d")
        self.assertIn("state: open\n", self.repo.read("rkm/incidents/i.md"))
        self.repo.run("state", "i", "mitigated")
        self.repo.run("close", "i")
        self.assertIn("all conformant", self.repo.run("check"))
        self.repo.write("rkm/config.toml", '[types.Incident]\nstates = ["new", "done"]\n')
        self.assertIn('must run from "open" to "done"', self.repo.run("check", ok=False))

    def test_adopt_gives_a_bare_file_frontmatter(self):
        self.repo.write("rkm/todos/bare.md", "# Bare todo\n\nBody.\n")
        self.assertIn("give it some with `rkm adopt`", self.repo.run("check", ok=False))
        self.repo.run("adopt", "rkm/todos/bare.md", "todo", "--description", "Arrived by merge.")
        text = self.repo.read("rkm/todos/bare.md")
        self.assertTrue(text.startswith('---\ntype: Todo\ntitle: "Bare todo"\n'
                                        'description: "Arrived by merge."\nstate: open\n---\n# Bare todo\n'))
        self.assertNotIn("generated:", text)
        self.repo.run("adopt", "todos/bare", "todo", "--description", "again", ok=False)
        self.assertIn("all conformant", self.repo.run("check"))

    def test_decisions_are_numbered_and_dated(self):
        self.repo.run("new", "decision", "closed-by-default", "--title", "Closed", "--description", "d")
        self.repo.run("new", "decision", "single-sub", "--title", "Single", "--description", "d")
        self.assertTrue(self.repo.exists("rkm/decisions/02-single-sub.md"))
        text = self.repo.read("rkm/decisions/01-closed-by-default.md")
        self.assertIn("decided: 2026-10-12\n", text)
        self.assertIn("**Not chosen.**", text)
        self.assertIn("# Decisions", self.repo.read("rkm/index.md"))
        self.repo.run("check")

    def test_rm_refuses_while_linked(self):
        self.new_todo()
        self.repo.write("README.md", "[a](rkm/todos/a.md)\n")
        self.assertIn("still linked from: README.md", self.repo.run("rm", "a", ok=False))
        self.repo.write("README.md", "nothing\n")
        self.repo.run("rm", "a")
        self.assertFalse(self.repo.exists("rkm/todos/a.md"))
        self.repo.run("check")

    def test_adopt_moves_a_file_in_from_outside(self):
        self.repo.write("notes/Testing.md", "# Testing\n\n[up](../README.md), [kit](../../kit/x.md)\n")
        self.repo.write("README.md", "See [notes/Testing.md](notes/Testing.md#run).\n")
        self.repo.run("adopt", "notes/Testing.md", "process", "--description", "How to test.")
        self.assertFalse(self.repo.exists("notes/Testing.md"))
        text = self.repo.read("rkm/process/testing.md")
        self.assertTrue(text.startswith('---\ntype: Process\ntitle: "Testing"\n'))
        self.assertIn("[up](../../README.md), [kit](../../../kit/x.md)", text)
        self.assertIn("See [rkm/process/testing.md](rkm/process/testing.md#run).", self.repo.read("README.md"))
        self.repo.write("old/Survey.md", "# Survey\n")
        self.repo.run("adopt", "old/Survey.md", "guide", "--as", "archive/survey",
                      "--status", "deprecated", "--description", "d")
        self.assertIn("status: deprecated\n", self.repo.read("rkm/archive/survey.md"))
        self.assertIn("all conformant", self.repo.run("check"))

    def test_config_adds_types_and_sets_the_intro(self):
        self.repo.write("rkm/config.toml", 'intro = "Knowledge for X."\n\n[types.Incident]\n'
                        'dir = "incidents"\nsection = "Open incidents"\ntracked = true\n'
                        'template = "## Want\\n"\n\n[types.Guide]\ndir = "guides"\n')
        self.repo.run("new", "incident", "r", "--title", "R", "--description", "d")
        self.repo.run("new", "guide", "g", "--title", "G", "--description", "d")
        text = self.repo.read("rkm/incidents/r.md")
        self.assertIn("state: open\n", text)
        self.assertIn("## Want\n", text)
        index = self.repo.read("rkm/index.md")
        self.assertIn("Knowledge for X.\n", index)
        self.assertLess(index.index("# Guides"), index.index("# Open incidents"))
        self.repo.run("close", "r")
        self.assertIn("all conformant", self.repo.run("check"))

    def test_ls_filters(self):
        self.new_todo("a")
        self.new_todo("b")
        self.repo.run("close", "b")
        out = self.repo.run("ls", "--state", "open")
        self.assertIn("todos/a", out)
        self.assertNotIn("todos/b", out)

    def test_ls_hides_closed_and_deprecated(self):
        self.new_todo("a")
        self.new_todo("b")
        self.repo.run("close", "b")
        self.repo.run("new", "spec", "old", "--title", "O", "--description", "d")
        self.repo.run("status", "old", "deprecated")
        out = self.repo.run("ls")
        self.assertIn("todos/a", out)
        self.assertNotIn("todos/b", out)
        self.assertNotIn("spec/old", out)
        self.assertEqual([d["id"] for d in json.loads(self.repo.run("ls", "--json"))],
                         ["todos/a"])
        out = self.repo.run("ls", "--all")
        self.assertIn("todos/b", out)
        self.assertIn("spec/old", out)
        self.assertEqual(len(json.loads(self.repo.run("ls", "-a", "--json"))), 3)
        # A filter that names the hidden value asks for it.
        self.assertEqual(self.repo.run("ls", "--state", "done").split()[0], "todos/b")
        self.assertEqual(self.repo.run("ls", "--status", "deprecated").split()[0], "spec/old")

    def test_ls_columns(self):
        self.new_todo("a")
        self.repo.run("new", "spec", "a-much-longer-spec", "--title", "S", "--description", "d",
                      "--draft")
        self.repo.run("new", "spec", "settled", "--title", "T", "--description", "d")
        rows = self.repo.run("ls").splitlines()
        self.assertEqual(len(rows), 3)  # piped: no header
        line = {r.split()[0]: r.split()[2:] for r in rows}
        title = {"todos/a": "Do A", "spec/a-much-longer-spec": "S", "spec/settled": "T"}
        starts = {r.rindex(title[r.split()[0]]) for r in rows}
        self.assertEqual(len(starts), 1)  # titles line up
        self.assertEqual(line["todos/a"][0], "open")
        self.assertEqual(line["spec/a-much-longer-spec"], ["draft", "S"])
        self.assertEqual(line["spec/settled"], ["T"])  # stable is not shown

    def test_ls_header_on_tty(self):
        import pty
        self.new_todo("a")
        self.new_todo("b")
        self.repo.run("close", "b")
        leader, follower = pty.openpty()
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "RKM_ACTOR")}
        env.update(RKM_ROOT=self.repo.root, RKM_NOW=NOW)
        subprocess.run([sys.executable, "-I", RKM, "ls"], env=env, stdout=follower,
                       stderr=follower, check=True)
        out = os.read(leader, 65536).decode()  # macOS drops it once the follower closes
        os.close(follower)
        os.close(leader)
        self.assertRegex(out.splitlines()[0], r"^ID +TYPE +STATE +TITLE")
        self.assertIn("todos/a", out.splitlines()[1])
        self.assertIn("1 closed or deprecated hidden; --all shows them", out)


def source(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


VERSION = re.search(r'^VERSION = "([^"]+)"$', source(RKM), re.M).group(1)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)
        self.repo.write("rkm/log.md", "# Update log\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_preamble_names_the_project(self):
        self.repo.write("rkm/config.toml", 'name = "widgets"\n')
        self.repo.run("index")
        index = self.repo.read("rkm/index.md")
        self.assertIn("Contributor and agent knowledge for widgets, as an", index)
        self.assertIn("<!-- Generated by rkm. Do not edit; run `rkm index`. -->", index)

    def test_requires_is_the_oldest_rkm_that_reads_the_bundle(self):
        self.repo.write("rkm/config.toml", 'requires = "99.0"\n')
        out = self.repo.run("index", ok=False)
        self.assertIn(f"this bundle needs rkm 99.0 or newer; this is {VERSION}", out)
        self.repo.write("rkm/config.toml", 'requires = "0.0"\n')
        self.repo.run("index")

    def test_unknown_config_key_is_refused(self):
        self.repo.write("rkm/config.toml", 'nmae = "typo"\n')
        self.assertIn("unknown key(s) nmae", self.repo.run("index", ok=False))
        self.repo.write("rkm/config.toml", '[types.Todo]\ndri = "x"\n')
        self.assertIn("unknown key(s) types.Todo.dri", self.repo.run("index", ok=False))

    def test_commit_policy_rejects_invalid_templates_and_patterns(self):
        for config, expected in (
                ('[commits]\ntemplate = "{summary.__class__}"\n', "only {kind} and {summary}"),
                ('[commits]\ntemplate = "fixed"\n', "must contain {summary}"),
                ('[commits]\npattern = "["\n', "unterminated"),
                ('[commits]\nmessage = "typo"\n', "commits.message")):
            with self.subTest(config=config):
                self.repo.write("rkm/config.toml", config)
                self.assertIn(expected, self.repo.run("conventions", ok=False))

    def test_push_policy_requires_a_nonempty_single_line(self):
        for value in ('""', '42', '"line one\\nline two"'):
            with self.subTest(value=value):
                self.repo.write("rkm/config.toml", "push_policy = " + value + "\n")
                self.assertIn("push_policy", self.repo.run("agents", ok=False))

    def test_skip_and_text_extensions(self):
        self.repo.write("rkm/config.toml", 'skip = ["vendor"]\ntext_extensions = [".okd"]\n')
        self.repo.run("index")
        self.repo.run("new", "todo", "a", "--title", "A", "--description", "d")
        self.repo.write("vendor/notes.md", "[gone](../rkm/todos/gone.md) and rkm/todos/a.md\n")
        self.repo.write("model.okd", "// see rkm/todos/a.md\n")
        self.assertIn("all conformant", self.repo.run("check"))
        self.repo.run("mv", "a", "b")
        self.assertIn("rkm/todos/b.md", self.repo.read("model.okd"))
        self.assertIn("rkm/todos/a.md", self.repo.read("vendor/notes.md"))

    def test_finds_the_repo_from_a_subdirectory(self):
        self.repo.run("index")
        os.makedirs(os.path.join(self.repo.root, "src/deep"))
        self.repo.run("new", "todo", "a", "--title", "A", "--description", "d",
                      root=False, cwd=os.path.join(self.repo.root, "src/deep"))
        self.assertTrue(self.repo.exists("rkm/todos/a.md"))

    def test_no_bundle_is_an_error(self):
        out = self.repo.run("ls", root=False, cwd=tempfile.gettempdir(), ok=False)
        self.assertIn("`rkm init` starts one", out)


class InitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(os.path.join(self.tmp.name, "widgets"))
        os.makedirs(self.repo.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_starts_a_bundle(self):
        out = self.repo.run("init", "--in-tree")
        self.assertIn("## Repository knowledge (RKM)", out)
        self.assertIn("rkm conventions", out)
        self.assertIn(self.repo.run("agents"), out)
        minor = ".".join(VERSION.split(".")[:2])
        self.assertIn(f'name = "widgets"\nrequires = "{minor}"\n', self.repo.read("rkm/config.toml"))
        self.assertFalse(self.repo.exists("rkm/log.md"))
        self.assertFalse(self.repo.exists("scripts"))
        self.assertIn("0 concepts, all conformant", self.repo.run("check"))
        self.assertIn("already has an rkm/ bundle", self.repo.run("init", "--in-tree", ok=False))

    def test_default_init_needs_a_repository_and_suggests_in_tree(self):
        out = self.repo.run("init", ok=False)
        self.assertIn("jj or Git repository", out)
        self.assertIn("rkm init --in-tree", out)
        self.assertIn("drop --in-tree", self.repo.run("init", "--in-tree", "--branch", ok=False))

    def test_init_refuses_an_unnamed_agent(self):
        self.repo.run("init", "--in-tree", actor=None, agent=True, ok=False)
        self.assertFalse(self.repo.exists("rkm"))

    def test_agents_print_and_write_for_an_in_tree_bundle(self):
        self.repo.run("init", "--in-tree")
        out = self.repo.run("agents")
        self.assertIn("<!-- rkm:begin -->", out)
        self.assertIn("rkm conventions", out)
        self.assertNotIn("rkm:branch", out)
        self.assertIn("this checkout's `rkm/` bundle", out)
        self.assertNotIn("**not in this checkout**", out)
        self.assertNotIn("everything pending in the bundle", out)
        self.assertIn("Commit `rkm/` changes with your usual VCS", out)
        self.assertFalse(self.repo.exists("AGENTS.md"))
        self.repo.write("AGENTS.md", "# Project rules\nUse nextest.\n")
        self.repo.run("agents", "--write")
        content = self.repo.read("AGENTS.md")
        self.assertTrue(content.startswith("# Project rules\nUse nextest.\n"))
        self.assertIn(out.strip(), content)
        self.repo.run("agents", "--write")
        self.assertEqual(content, self.repo.read("AGENTS.md"))

    def test_conventions_list_the_bundle_types(self):
        self.repo.run("init", "--in-tree")
        self.repo.write("rkm/config.toml", '[types.Request]\ndir = "requests"\nabout = "Asks from users."\n')
        out = self.repo.run("conventions")
        self.assertIn("**Change it only through `rkm`.**", out)
        self.assertIn("| `rkm/requests/` | `Request` | Asks from users. |", out)
        self.assertIn("| `rkm/todos/` | `Todo` | Planned work, one item per file. |", out)
        self.assertNotIn("{", out.split("```")[0])


class SiteTest(unittest.TestCase):
    def test_landing_page_lists_every_command(self):
        page = html.unescape(source(os.path.join(os.path.dirname(RKM), "site", "index.html")))
        usage = subprocess.run([sys.executable, RKM, "--help"], capture_output=True, text=True, check=True).stdout
        commands = re.search(r"\{([a-z,]+)\}", usage).group(1).split(",")
        commands.remove("workspace")
        commands += ["workspace add", "workspace remove", "init --in-tree"]
        reference = page.split('<section id="reference">', 1)[1].split("</section>", 1)[0]
        missing = [c for c in commands if f"<code>rkm {c}" not in reference]
        self.assertEqual(missing, [], "add these to the reference in site/index.html")


if __name__ == "__main__":
    unittest.main()
