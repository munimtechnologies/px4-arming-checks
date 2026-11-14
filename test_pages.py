"""Synthetic tests for pages.py (no network, no PX4 checkout). By MV2 of Munim, Inc.; made with AI agents. Apache-2.0."""

import hashlib
import json
import os
import shutil
import tempfile
import unittest

import pages

P = "src/modules/commander/HealthAndArmingChecks/checks/"
TAGS = ["v1.0.0", "v1.1.0", "main"]
SHAS = {"v1.0.0": "a" * 40, "v1.1.0": "b" * 40, "main": "c" * 40}
BASE = "https://munimv2.github.io/px4-arming-checks/"


def rec(kind, eid, msg, name, line, **extra):
    r = {"kind": kind, "event_id": eid, "message": msg, "message_raw": None, "path": P + name, "line": line}
    r.update(extra)
    return r


def alpha(msg, line):
    return rec("arming_check", "check_a", msg, "aCheck.cpp", line, function="ACheck::checkAndReport", log="Error",
               component="accel", modes=["All"], params_doc=["COM_A"], params_near=["COM_A", "SYS_B", "_param_x"],
               console=[{"level": "critical", "text": "Preflight Fail: Alpha low", "text_raw": "x"}],
               summary="Alpha is low", description="Para one.\n\nPara two <b>", source="line1\nline2")


LONG_SRC = "\n".join("s%d" % i for i in range(50))
BY_TAG = {
    "v1.0.0": [alpha("Alpha {1} low", 10), rec("health_failure", "check_b", "Beta", "bCheck.cpp", 20)],
    "v1.1.0": [alpha("Alpha {1} too low", 11), rec("arming_check", None, "No id <script>", "bCheck.cpp", 30)],
    "main": [alpha("Alpha {1} too low", 40), alpha("Alpha {1} too low", 12),
             rec("arming_check", None, "No id <script>", "bCheck.cpp", 31),
             rec("health_warning", "Check.Upper", None, "dCheck.cpp", 5, message_raw="buf", source=LONG_SRC),
             rec("console", None, "Preflight Fail: Alpha low", "aCheck.cpp", 13)],
}
C_KEY = "bCheck.cpp|No id <script>"
C_SLUG = "bcheck-" + hashlib.sha1(C_KEY.encode("utf-8")).hexdigest()[:8]


class TestCollect(unittest.TestCase):
    def setUp(self):
        self.items = pages.collect(TAGS, BY_TAG)
        self.by = {it["key"]: it for it in self.items}

    def test_items_and_order(self):
        self.assertEqual([it["key"] for it in self.items], ["check_a", "check_b", C_KEY, "Check.Upper"])
        self.assertEqual([it["slug"] for it in self.items], ["check_a", "check_b", C_SLUG, "check-upper"])
        self.assertEqual([it["title"] for it in self.items], ["Alpha {1} too low", "Beta", "No id <script>", "buf"])

    def test_releases(self):
        a = self.by["check_a"]
        self.assertEqual(a["tags"], TAGS)
        self.assertEqual(a["latest_tag"], "main")
        self.assertEqual(a["per_tag"]["main"]["calls"], 2)
        self.assertEqual(a["rec"]["line"], 12)
        self.assertEqual(self.by["check_b"]["tags"], ["v1.0.0"])
        self.assertEqual(self.by[C_KEY]["tags"], ["v1.1.0", "main"])

    def test_missing_tag_skipped(self):
        items = pages.collect(["v1.0.0", "missing", "v1.1.0", "main"], BY_TAG)
        self.assertEqual([(it["slug"], it["tags"]) for it in items], [(it["slug"], it["tags"]) for it in self.items])

    def test_slug_collision(self):
        self.assertEqual(pages.slug_for("x", {"event_id": "a.b"}, {"a-b"}), "a-b-2")


class TestRender(unittest.TestCase):
    def setUp(self):
        self.items = pages.collect(TAGS, BY_TAG)
        self.page = {it["key"]: pages.render_page(it, self.items, TAGS, SHAS) for it in self.items}

    def test_page_alpha(self):
        h = self.page["check_a"]
        self.assertIn("<h1>Alpha {1} too low</h1>", h)
        self.assertIn("Parts in braces", h)
        self.assertIn("<code>Preflight Fail: Alpha low</code> (critical)", h)
        self.assertIn("Named by PX4 in its description: <code>COM_A</code>", h)
        self.assertIn("<code>SYS_B</code>", h)
        self.assertNotIn("_param_x", h)
        self.assertIn("<p>Alpha is low</p>", h)
        self.assertIn("<p>Para two &lt;b&gt;</p>", h)
        self.assertIn('<link rel="canonical" href="%sm/check_a.html">' % BASE, h)
        self.assertIn("aCheck.cpp line 12 (2 calls)", h)
        self.assertIn('<a href="https://github.com/PX4/PX4-Autopilot/blob/%s/%saCheck.cpp#L10">aCheck.cpp line 10</a>'
                      % ("a" * 40, P), h)
        self.assertIn("<td>v1.0.0</td><td>Alpha {1} low</td>", h)
        self.assertIn("health component accel", h)
        self.assertIn("flight modes All", h)
        self.assertNotIn("not present", h)
        self.assertNotIn("Other messages", h)
        self.assertIn("Made with AI agents", h)
        self.assertIn("PX4 log check", h)

    def test_page_beta_and_siblings(self):
        h = self.page["check_b"]
        self.assertEqual(h.count("not present"), 2)
        self.assertNotIn("Parts in braces", h)
        self.assertNotIn("Console text", h)
        self.assertIn('<a href="%s.html">No id &lt;script&gt;</a>' % C_SLUG, h)

    def test_page_escapes(self):
        h = self.page[C_KEY]
        self.assertIn("<h1>No id &lt;script&gt;</h1>", h)
        self.assertNotIn("No id <script>", h)

    def test_long_source_truncated(self):
        h = self.page["Check.Upper"]
        self.assertIn("<h1>buf</h1>", h)
        self.assertIn("s39</code></pre>", h)
        self.assertNotIn("s40", h)
        self.assertIn("(10 more lines at the source link above.)", h)

    def test_meta_description(self):
        d = pages._meta_description(self.items[0], TAGS)
        self.assertEqual(d, 'PX4 arming check "Alpha {1} too low" (event ID check_a): PX4\'s description, the parameters '
                            "(COM_A) and its source line in each release (v1.0.0 to main).")

    def test_index_and_sitemap(self):
        idx = pages.render_index(self.items, TAGS)
        self.assertIn('href="m/check_a.html"', idx)
        self.assertIn('<h2 id="bCheck">', idx)
        self.assertIn("4 messages from 3 check files", idx)
        self.assertIn("(health failure; in v1.0.0)", idx)
        sm = pages.render_sitemap(self.items, BASE, "2026-11-14")
        self.assertEqual(sm.count("<url>"), 7)
        self.assertIn("<loc>%smessages.html</loc><lastmod>2026-11-14</lastmod>" % BASE, sm)
        self.assertIn("<loc>%sm/%s.html</loc>" % (BASE, C_SLUG), sm)


class TestMain(unittest.TestCase):
    def test_main_builds_site(self):
        src, dst = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            with open(os.path.join(src, "summary.json"), "w", encoding="utf-8") as f:
                json.dump({"ok_tags": TAGS, "finished": "2026-11-14T01:00:00Z"}, f)
            for tag in TAGS:
                with open(os.path.join(src, "catalog-%s.json" % tag), "w", encoding="utf-8") as f:
                    json.dump({"tag": tag, "sha": SHAS[tag], "records": BY_TAG[tag]}, f)
            with open(os.path.join(dst, "index.html"), "w", encoding="utf-8") as f:
                f.write("<html><body><h1>x</h1>\n<h2>a</h2></body></html>")
            self.assertEqual(pages.main([src, "--dest", dst]), 0)
            self.assertEqual(pages.main([src, "--dest", dst]), 0)
            for name in ("m/check_a.html", "m/check_b.html", "m/%s.html" % C_SLUG, "m/check-upper.html",
                         "messages.html", "sitemap.xml", "pages.json"):
                self.assertTrue(os.path.isfile(os.path.join(dst, name)), name)
            with open(os.path.join(dst, "index.html"), encoding="utf-8") as f:
                idx = f.read()
            self.assertEqual(idx.count(pages.LINK_MARK), 1)
            self.assertLess(idx.index("messages.html"), idx.index("<h2>a</h2>"))
            with open(os.path.join(dst, "sitemap.xml"), encoding="utf-8") as f:
                self.assertIn("<lastmod>2026-11-14</lastmod>", f.read())
            with open(os.path.join(dst, "pages.json"), encoding="utf-8") as f:
                meta = json.load(f)
            self.assertEqual(len(meta["pages"]), 4)
            self.assertEqual(meta["pages"][0]["tags"], TAGS)
        finally:
            shutil.rmtree(src)
            shutil.rmtree(dst)

    def test_empty_build(self):
        src, dst = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            with open(os.path.join(src, "summary.json"), "w", encoding="utf-8") as f:
                json.dump({"ok_tags": []}, f)
            self.assertEqual(pages.main([src, "--dest", dst]), 1)
        finally:
            shutil.rmtree(src)
            shutil.rmtree(dst)


if __name__ == "__main__":
    unittest.main()
