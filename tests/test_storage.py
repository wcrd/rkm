"""Integration tests for optional knowledge history, using real jj and Git repos."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_rkm import NOW, RKM


class BranchStorageTest(unittest.TestCase):
    backend = "git"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(os.path.join(self.tmp.name, "widgets"))
        self.workspace = os.path.realpath(os.path.join(self.tmp.name, "chosen-knowledge"))
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("RKM_ROOT", "CLAUDECODE", "RKM_ACTOR", "JJ_CONFIG")}
        config = os.path.join(self.tmp.name, "jj.toml")
        with open(config, "w") as f:
            f.write('[user]\nname = "Tester"\nemail = "tester@example.com"\n')
        self.env.update(RKM_ACTOR="human:tester", RKM_NOW=NOW,
                        JJ_CONFIG=config, XDG_CONFIG_HOME=os.path.join(self.tmp.name, "config"))
        self.command("git", "init", "-b", "main", self.root, cwd=self.tmp.name)
        self.command("git", "config", "user.name", "Tester")
        self.command("git", "config", "user.email", "tester@example.com")
        self.write("README.md", "# Code\n")
        self.command("git", "add", "README.md")
        self.command("git", "commit", "-m", "Initial code")
        if self.backend == "jj":
            self.command("jj", "git", "init", "--colocate")

    def command(self, *args, cwd=None, ok=True):
        p = subprocess.run(args, cwd=cwd or self.root, env=self.env,
                           capture_output=True, text=True)
        if ok and p.returncode:
            self.fail(f"{args} failed:\n{p.stdout}{p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"{args} unexpectedly succeeded")
        return p.stdout + p.stderr

    def run_rkm(self, *args, cwd=None, ok=True):
        return self.command(sys.executable, "-I", RKM, *args, cwd=cwd, ok=ok)

    def write(self, rel, text, root=None):
        path = os.path.join(root or self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def read(self, rel, root=None):
        with open(os.path.join(root or self.root, rel)) as f:
            return f.read()

    def new(self, slug, cwd=None):
        return self.run_rkm("new", "todo", slug, "--title", slug, "--description", "d", cwd=cwd)

    def enable(self):
        return self.run_rkm("init", "--branch", "--workspace", self.workspace)

    def test_requires_a_location_and_is_opt_in(self):
        self.assertIn("--workspace PATH", self.run_rkm("init", "--branch", ok=False))
        self.assertFalse(os.path.exists(self.workspace))
        self.run_rkm("init")
        self.assertTrue(os.path.exists(os.path.join(self.root, "rkm/config.toml")))
        self.assertEqual(self.run_rkm("path").strip(), os.path.join(self.root, "rkm"))
        self.assertIn("migrate", self.enable_failure())

    def enable_failure(self):
        return self.run_rkm("init", "--branch", "--workspace", self.workspace, ok=False)

    def test_init_and_commit_keep_code_history_separate(self):
        before = self.command("git", "rev-parse", "HEAD").strip()
        self.assertIn(f"({self.backend})", self.enable())
        self.assertFalse(os.path.exists(os.path.join(self.root, "rkm")))
        self.assertEqual(self.run_rkm("path").strip(), os.path.join(self.workspace, "rkm"))
        self.assertIn(os.path.join(self.workspace, "rkm/todos/a.md"), self.new("a"))
        self.run_rkm("check")
        self.run_rkm("close", "a")
        self.run_rkm("commit", "-m", "Record A")
        self.assertEqual(before, self.command("git", "rev-parse", "HEAD").strip())
        self.assertNotIn("rkm", self.command("git", "ls-tree", "--name-only", "HEAD"))
        if self.backend == "jj":
            roots = self.command("jj", "--ignore-working-copy", "log", "--no-graph", "-r",
                                 "roots(::_rkm ~ root())", "-T", 'commit_id ++ "\\n"').splitlines()
            self.assertEqual(len(roots), 1)
            self.assertNotIn("README.md", self.command("jj", "file", "list", "-r", "_rkm",
                                                       cwd=self.workspace))
        else:
            self.command("git", "merge-base", "HEAD", "_rkm", ok=False)
            self.assertEqual(self.command("git", "ls-tree", "--name-only", "_rkm").strip(), "rkm")
        self.assertEqual(self.run_rkm("path", cwd=self.workspace).strip(),
                         os.path.join(self.workspace, "rkm"))
        conventions = self.run_rkm("conventions")
        self.assertIn("separate", conventions)
        self.assertIn("rkm path", conventions)
        self.assertNotIn(self.workspace, conventions)  # no clone-specific paths in AGENTS.md

    def test_migration_preserves_uncommitted_notes_and_links(self):
        self.run_rkm("init")
        self.new("a")
        self.write("README.md", "[A](rkm/todos/a.md)\n")
        self.write("notes/process.md", "# Process\n[code](../README.md)\n")
        original = self.read("rkm/todos/a.md")
        self.run_rkm("migrate", "--workspace", self.workspace)
        self.assertFalse(os.path.exists(os.path.join(self.root, "rkm")))
        self.assertEqual(original, self.read("rkm/todos/a.md", self.workspace))
        self.run_rkm("check")
        self.assertIn("still linked from: README.md", self.run_rkm("rm", "a", ok=False))
        self.run_rkm("mv", "a", "archive/b")
        self.assertIn("rkm/archive/b.md", self.read("README.md"))
        self.run_rkm("adopt", "notes/process.md", "process", "--description", "d")
        self.assertFalse(os.path.exists(os.path.join(self.root, "notes/process.md")))
        self.assertIn("../../README.md", self.read("rkm/process/process.md", self.workspace))
        self.run_rkm("check")

    def test_saved_preference_is_shared_with_another_code_workspace(self):
        self.enable()
        other = os.path.join(self.tmp.name, "other-code")
        if self.backend == "jj":
            self.command("jj", "workspace", "add", "-r", "@", other)
        else:
            self.command("git", "worktree", "add", "-b", "feature", other)
        os.makedirs(os.path.join(other, "nested"))
        nested = os.path.join(other, "nested")
        self.assertEqual(self.run_rkm("path", cwd=nested).strip(), os.path.join(self.workspace, "rkm"))
        self.new("other", cwd=nested)
        self.assertIn("todos/other", self.run_rkm("ls"))
        self.assertFalse(os.path.exists(os.path.join(other, "rkm")))

    def test_refuses_occupied_location_nested_location_and_code_branch(self):
        os.mkdir(self.workspace)
        self.assertIn("already exists", self.enable_failure())
        os.rmdir(self.workspace)
        out = self.run_rkm("init", "--branch", "--workspace", os.path.join(self.root, "nested"), ok=False)
        self.assertIn("outside", out)
        out = self.run_rkm("init", "--branch", "main", "--workspace", self.workspace, ok=False)
        self.assertIn("code ancestry", out)
        self.assertFalse(os.path.exists(self.workspace))

    def test_missing_workspace_is_recreated_from_committed_knowledge(self):
        self.enable()
        self.new("a")
        self.run_rkm("commit", "-m", "Save A")
        os.rename(self.workspace, self.workspace + "-moved")
        self.assertIn("todos/a", self.run_rkm("ls"))
        self.assertTrue(os.path.exists(self.workspace))
        self.assertFalse(os.path.exists(os.path.join(self.root, "rkm")))

    def test_migration_refuses_conflict_markers(self):
        self.run_rkm("init")
        self.write("rkm/spec/conflicted.md", "<<<<<<< Conflict 1\n=======\n>>>>>>> Conflict 1 ends\n")
        out = self.run_rkm("migrate", "--workspace", self.workspace, ok=False)
        self.assertIn("resolve", out)
        self.assertTrue(os.path.exists(os.path.join(self.root, "rkm/spec/conflicted.md")))
        self.assertFalse(os.path.exists(self.workspace))

    def test_concurrent_commands_keep_all_notes_and_log_entries(self):
        self.enable()
        processes = [subprocess.Popen([sys.executable, "-I", RKM, "new", "todo", f"item-{i}",
                                      "--title", f"Item {i}", "--description", "d"],
                                     cwd=self.root, env=self.env, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for i in range(6)]
        for p in processes:
            out, err = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 0, out + err)
        self.run_rkm("check")
        log = self.read("rkm/log.md", self.workspace)
        for i in range(6):
            self.assertIn(f"[Item {i}](todos/item-{i}.md)", log)
        self.assertEqual(len(json.loads(self.run_rkm("ls", "--json"))), 6)

    def test_custom_branch_name_and_project_name(self):
        self.run_rkm("init", "--branch", "knowledge/rkm", "--workspace", self.workspace,
                     "--name", "Custom project")
        self.assertIn("Custom project", self.read("rkm/index.md", self.workspace))
        self.new("a")
        self.run_rkm("check")
        self.run_rkm("commit", "-m", "Custom knowledge")

    def test_fetched_knowledge_is_attached_without_reinitializing_it(self):
        self.enable()
        self.new("a")
        self.run_rkm("commit", "-m", "A")
        if self.backend == "jj":
            self.command("jj", "git", "export")
        clone = os.path.realpath(os.path.join(self.tmp.name, "clone"))
        destination = os.path.realpath(os.path.join(self.tmp.name, "clone-knowledge"))
        self.command("git", "clone", "--branch", "main", self.root, clone)
        self.command("git", "config", "user.name", "Tester", cwd=clone)
        self.command("git", "config", "user.email", "tester@example.com", cwd=clone)
        if self.backend == "jj":
            self.command("jj", "git", "init", "--colocate", cwd=clone)
            self.command("jj", "bookmark", "forget", "_rkm", cwd=clone)
        self.run_rkm("init", "--branch", "--workspace", destination, cwd=clone)
        self.assertIn("todos/a", self.run_rkm("ls", cwd=clone))
        self.assertEqual(self.read("rkm/log.md", self.workspace), self.read("rkm/log.md", destination))
        self.run_rkm("check", cwd=clone)

    def test_old_in_tree_copy_is_not_silently_ignored(self):
        self.enable()
        self.write("rkm/log.md", "# Old notes\n")
        self.assertIn("both", self.run_rkm("ls", ok=False))
        self.assertEqual(self.read("rkm/log.md"), "# Old notes\n")

    def test_branch_init_creates_agents_and_refreshes_only_its_block(self):
        self.enable()
        agents = self.read("AGENTS.md")
        self.assertIn("<!-- rkm:begin -->", agents)
        self.assertIn("rkm path", agents)
        self.assertIn("process notes before working", agents)
        self.assertNotIn(self.workspace, agents)
        self.write("AGENTS.md", "# Existing instructions\n\nUse nextest.\n\n" + agents + "\n## Extra\nKeep this.\n")
        self.run_rkm("init", "--branch")
        updated = self.read("AGENTS.md")
        self.assertTrue(updated.startswith("# Existing instructions\n\nUse nextest.\n\n"))
        self.assertTrue(updated.endswith("\n## Extra\nKeep this.\n"))
        self.assertEqual(updated.count("<!-- rkm:begin -->"), 1)
        self.run_rkm("init", "--branch")
        self.assertEqual(self.read("AGENTS.md"), updated)

    def test_migration_replaces_standard_legacy_conventions(self):
        self.run_rkm("init")
        self.write("AGENTS.md", "# Project\n\n" + self.run_rkm("conventions") + "\n## Tests\nUse nextest.\n")
        self.run_rkm("migrate", "--workspace", self.workspace)
        agents = self.read("AGENTS.md")
        self.assertIn("# Project\n", agents)
        self.assertIn("## Tests\nUse nextest.\n", agents)
        self.assertNotIn("Everything a contributor or agent works from is in", agents)
        self.assertEqual(agents.count("**Change it only through `rkm`.**"), 1)

    def test_custom_legacy_instructions_are_preserved(self):
        original = "# Project\n\n## Where knowledge lives\nCustom instructions.\n\n## Tests\nUse nextest.\n"
        self.write("AGENTS.md", original)
        self.enable()
        self.assertTrue(self.read("AGENTS.md").startswith(original))
        self.assertIn("RKM paths elsewhere in this file", self.read("AGENTS.md"))

    def test_malformed_agents_markers_refuse_migration_without_moving_notes(self):
        self.run_rkm("init")
        self.write("AGENTS.md", "# Project\n<!-- rkm:begin -->\nUnfinished.\n")
        out = self.run_rkm("migrate", "--workspace", self.workspace, ok=False)
        self.assertIn("malformed", out)
        self.assertTrue(os.path.exists(os.path.join(self.root, "rkm")))
        self.assertFalse(os.path.exists(self.workspace))

    def test_workspace_remove_refuses_dirty_notes_then_recreates_on_use(self):
        self.enable()
        self.new("a")
        self.assertIn("uncommitted", self.run_rkm("workspace", "remove", ok=False))
        self.assertTrue(os.path.exists(self.workspace))
        self.run_rkm("commit", "-m", "Save A")
        self.run_rkm("workspace", "remove")
        self.assertFalse(os.path.exists(self.workspace))
        self.assertIn("todos/a", self.run_rkm("ls"))
        self.assertTrue(os.path.exists(self.workspace))
        self.run_rkm("check")

    def test_workspace_add_can_choose_a_new_location_after_removal(self):
        self.enable()
        self.run_rkm("workspace", "remove")
        destination = self.workspace + "-new"
        self.run_rkm("workspace", "add", "--workspace", destination)
        self.assertEqual(self.run_rkm("path").strip(), os.path.join(destination, "rkm"))
        self.assertFalse(os.path.exists(self.workspace))

    def test_workspace_remove_preserves_ignored_local_files(self):
        self.enable()
        self.write("rkm/.gitignore", "local.md\n", root=self.workspace)
        self.run_rkm("commit", "-m", "Ignore local scratch")
        self.write("rkm/local.md", "Uncommitted local knowledge.\n", root=self.workspace)
        self.assertIn("uncommitted", self.run_rkm("workspace", "remove", ok=False))
        self.assertEqual(self.read("rkm/local.md", self.workspace), "Uncommitted local knowledge.\n")

    def test_missing_history_does_not_create_an_empty_replacement(self):
        self.enable()
        self.run_rkm("workspace", "remove")
        if self.backend == "jj":
            self.command("jj", "bookmark", "delete", "_rkm")
        else:
            self.command("git", "branch", "-D", "_rkm")
        self.assertIn("fetch", self.run_rkm("ls", ok=False))
        self.assertFalse(os.path.exists(self.workspace))

    def test_new_clone_discovers_agents_and_bootstraps_existing_history(self):
        self.enable()
        self.new("a")
        self.run_rkm("commit", "-m", "Save knowledge")
        if self.backend == "jj":
            self.command("jj", "commit", "-m", "Advertise RKM")
            self.command("jj", "bookmark", "set", "main", "-r", "@-")
            self.command("jj", "git", "export")
        else:
            self.command("git", "add", "AGENTS.md")
            self.command("git", "commit", "-m", "Advertise RKM")
        clone = os.path.realpath(os.path.join(self.tmp.name, "auto-clone"))
        self.command("git", "clone", "--branch", "main", self.root, clone)
        if self.backend == "jj":
            self.command("jj", "git", "init", "--colocate", cwd=clone)
        parent = os.path.realpath(os.path.join(self.tmp.name, "global-knowledge"))
        self.run_rkm("config", "--workspace-root", parent, cwd=clone)
        self.assertIn("todos/a", self.run_rkm("ls", cwd=clone))
        self.assertTrue(self.run_rkm("path", cwd=clone).strip().startswith(parent + os.sep))
        self.assertFalse(os.path.exists(os.path.join(clone, "rkm")))

    def test_global_parent_is_saved_and_used_only_for_knowledge(self):
        parent = os.path.realpath(os.path.join(self.tmp.name, "all-knowledge"))
        config_path = os.path.join(self.env["XDG_CONFIG_HOME"], "rkm/config.toml")
        out = self.run_rkm("config", "--workspace-root", parent)
        self.assertIn(config_path, out)
        with open(config_path) as f:
            self.assertIn(parent, f.read())
        self.assertIn(parent, self.run_rkm("config", cwd=self.tmp.name))
        self.run_rkm("init", "--branch")
        bundle = self.run_rkm("path").strip()
        self.assertEqual(os.path.dirname(os.path.dirname(bundle)), parent)
        self.assertTrue(os.path.basename(os.path.dirname(bundle)).startswith("widgets-"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "rkm")))
        self.new("global")
        self.run_rkm("check")
        self.run_rkm("config", "--workspace-root", parent + "-new")
        self.assertEqual(self.run_rkm("path").strip(), bundle)
        self.run_rkm("config", "--unset-workspace-root")
        self.assertIn("unset", self.run_rkm("config"))
        self.assertEqual(self.run_rkm("path").strip(), bundle)

    def test_explicit_workspace_overrides_global_parent(self):
        parent = os.path.join(self.tmp.name, "global-parent")
        self.run_rkm("config", "--workspace-root", parent)
        self.run_rkm("init")
        self.new("a")
        self.run_rkm("migrate", "--workspace", self.workspace)
        self.assertEqual(self.run_rkm("path").strip(), os.path.join(self.workspace, "rkm"))
        self.assertFalse(os.path.exists(parent))

    def test_global_parent_separates_same_named_repositories(self):
        parent = os.path.realpath(os.path.join(self.tmp.name, "all-knowledge"))
        self.run_rkm("config", "--workspace-root", parent)
        self.run_rkm("init")
        self.new("a")
        self.run_rkm("migrate")
        first = self.run_rkm("path").strip()
        second = os.path.realpath(os.path.join(self.tmp.name, "other/widgets"))
        self.command("git", "init", "-b", "main", second)
        self.command("git", "config", "user.name", "Tester", cwd=second)
        self.command("git", "config", "user.email", "tester@example.com", cwd=second)
        if self.backend == "jj":
            self.command("jj", "git", "init", "--colocate", cwd=second)
        self.run_rkm("init", "--branch", cwd=second)
        self.assertNotEqual(first, self.run_rkm("path", cwd=second).strip())
        self.assertNotIn("todos/a", self.run_rkm("ls", cwd=second))
        self.assertEqual(self.run_rkm("path").strip(), first)

    def test_failure_recording_migration_keeps_original_notes(self):
        self.run_rkm("init")
        self.new("a")
        original = self.read("rkm/todos/a.md")
        if self.backend == "git":
            hook = os.path.join(self.root, ".git/hooks/pre-commit")
            with open(hook, "w") as f:
                f.write("#!/bin/sh\nexit 1\n")
            os.chmod(hook, 0o755)
        else:
            self.write("rkm/ignored.md", "# Important\n")
            self.write("rkm/.gitignore", "ignored.md\n")
        out = self.run_rkm("migrate", "--workspace", self.workspace, ok=False)
        self.assertIn("rkm:", out)
        self.assertEqual(self.read("rkm/todos/a.md"), original)
        self.assertFalse(os.path.exists(self.workspace))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".git/rkm-storage.json")))


@unittest.skipUnless(shutil.which("jj"), "jj is not installed")
class JjBranchStorageTest(BranchStorageTest):
    backend = "jj"

    def test_non_colocated_jj_repository(self):
        root = os.path.realpath(os.path.join(self.tmp.name, "native-jj"))
        self.command("jj", "git", "init", "--no-colocate", root)
        self.root = root
        self.enable()
        self.new("native")
        self.run_rkm("commit", "-m", "Native knowledge")
        self.run_rkm("check")
        self.assertFalse(os.path.exists(os.path.join(root, ".git")))
        self.assertIn("todos/native", self.run_rkm("ls"))


if __name__ == "__main__":
    unittest.main()
