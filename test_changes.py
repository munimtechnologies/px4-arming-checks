"""Synthetic tests for changes.py (no network, no PX4 checkout). By MV2 of Munim, Inc.; made with AI agents. Apache-2.0."""

import json
import os
import shutil
import tempfile
import unittest

import changes

P = "src/modules/commander/HealthAndArmingChecks/checks/"
SHA_A = "a" * 40
SHA_B = "b" * 40


def rec(kind, eid, msg, name, line, **extra):
    r = {"kind": kind, "event_id": eid, "message": msg, "message_raw": None, "path": P + name, "line": line}
    r.update(extra)
    return r


OLD = [
    rec("arming_check", "check_a", "Alpha", "aCheck.cpp", 10),
    rec("health_failure", "check_b", "Beta | old", "bCheck.cpp", 20),
    rec("arming_check", "check_c", "Gone", "cCheck.cpp", 5),
    rec("arming_check", None, "No id", "bCheck.cpp", 30),
    rec("console", None, "Preflight Fail: Alpha", "aCheck.cpp", 12),
]
NEW = [
    rec("arming_check", "check_a", "Alpha", "aCheck.cpp", 11),
    rec("health_failure", "check_b", "Beta <new>", "bCheck.cpp", 22),
    rec("health_warning", "check_d", "New\none", "dCheck.cpp", 7, url="https://example.org/d#L7"),
]
TAGS = ["v1.0.0", "v1.1.0-rc1"]
BY_TAG = {"v1.0.0": OLD, "v1.1.0-rc1": NEW}
SHAS = {"v1.0.0": SHA_A, "v1.1.0-rc1": SHA_B}


class TestChanges(unittest.TestCase):
    def setUp(self):
        self.steps = changes.changes(TAGS, BY_TAG, SHAS)

    def test_counts(self):
        self.assertEqual(len(self.steps), 1)
        s = self.steps[0]
        self.assertEqual((s["from"], s["to"], s["reports_from"], s["reports_to"]), ("v1.0.0", "v1.1.0-rc1", 4, 3))
        self.assertEqual(s["counts"], {"added": 1, "removed": 2, "message_changed": 1})

    def test_missing_tag_skipped(self):
        steps = changes.changes(["v1.0.0", "missing", "v1.1.0-rc1"], BY_TAG, SHAS)
        self.assertEqual(steps, self.steps)

    def test_removed_rows(self):
        rows = self.steps[0]["removed"]
        self.assertEqual([(r["check"], r["event_id"], r["old"], r["new"]) for r in rows],
                         [("bCheck", None, "No id", None), ("cCheck", "check_c", "Gone", None)])
        self.assertEqual(rows[1]["old_url"],
                         "https://github.com/PX4/PX4-Autopilot/blob/%s/%scCheck.cpp#L5" % (SHA_A, P))
        self.assertIsNone(rows[1]["new_url"])

    def test_added_and_reworded_rows(self):
        add = self.steps[0]["added"]
        self.assertEqual(len(add), 1)
        self.assertEqual((add[0]["event_id"], add[0]["kind"], add[0]["new"], add[0]["old"]),
                         ("check_d", "health_warning", "New one", None))
        self.assertEqual(add[0]["new_url"], "https://example.org/d#L7")
        rw = self.steps[0]["message_changed"]
        self.assertEqual([(r["event_id"], r["old"], r["new"]) for r in rw], [("check_b", "Beta | old", "Beta <new>")])
        self.assertEqual(rw[0]["new_url"],
                         "https://github.com/PX4/PX4-Autopilot/blob/%s/%sbCheck.cpp#L22" % (SHA_B, P))
        self.assertEqual(rw[0]["old_url"],
                         "https://github.com/PX4/PX4-Autopilot/blob/%s/%sbCheck.cpp#L20" % (SHA_A, P))

    def test_markdown(self):
        md = changes.render_md(self.steps, TAGS)
        self.assertIn("# What changed in PX4's health and arming check messages (v1.0.0 to v1.1.0-rc1)", md)
        self.assertIn("| v1.0.0 | v1.1.0-rc1 | 4 | 3 | 1 | 2 | 1 |", md)
        self.assertIn("## v1.0.0 to v1.1.0-rc1", md)
        self.assertIn("### Removed (2)", md)
        self.assertIn("Beta \\| old", md)
        self.assertIn("Beta &lt;new&gt;", md)
        self.assertNotIn("Beta <new>", md)
        self.assertIn("| bCheck | (none) | No id | [v1.0.0](", md)
        self.assertIn("| dCheck | `check_d` | New one | [v1.1.0-rc1](https://example.org/d#L7) |", md)
        self.assertIn("Made with AI agents", md)

    def test_html(self):
        h = changes.render_html(self.steps, TAGS)
        self.assertIn('<h2 id="v1.0.0-v1.1.0-rc1">', h)
        self.assertIn("Beta &lt;new&gt;", h)
        self.assertNotIn("Beta <new>", h)
        self.assertIn('<a href="https://example.org/d#L7">v1.1.0-rc1</a>', h)
        self.assertIn("<h3>Reworded (1)</h3>", h)
        self.assertIn("PX4 log check", h)
        self.assertIn("Made with AI agents", h)

    def test_empty_section(self):
        steps = changes.changes(["v1.0.0", "v1.0.1"], {"v1.0.0": OLD, "v1.0.1": OLD}, SHAS)
        self.assertEqual(steps[0]["counts"], {"added": 0, "removed": 0, "message_changed": 0})
        self.assertIn("None.", changes.render_md(steps, ["v1.0.0", "v1.0.1"]))


class TestMain(unittest.TestCase):
    def test_main_reads_build_folder(self):
        src, dst = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            with open(os.path.join(src, "summary.json"), "w", encoding="utf-8") as f:
                json.dump({"ok_tags": TAGS}, f)
            for tag in TAGS:
                with open(os.path.join(src, "catalog-%s.json" % changes.safe(tag)), "w", encoding="utf-8") as f:
                    json.dump({"tag": tag, "sha": SHAS[tag], "records": BY_TAG[tag]}, f)
            self.assertEqual(changes.main([src, "--dest", dst]), 0)
            for name in ("changes.json", "changes.md", "changes.html"):
                self.assertTrue(os.path.isfile(os.path.join(dst, name)), name)
            with open(os.path.join(dst, "changes.json"), encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["tags"], TAGS)
            self.assertEqual(data["steps"][0]["counts"], {"added": 1, "removed": 2, "message_changed": 1})
        finally:
            shutil.rmtree(src)
            shutil.rmtree(dst)

    def test_safe(self):
        self.assertEqual(changes.safe("v1.18.0-rc1"), "v1.18.0-rc1")
        self.assertEqual(changes.safe("release/1.17"), "release_1.17")


if __name__ == "__main__":
    unittest.main()
