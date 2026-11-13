"""Synthetic tests for armcat.py (no network, no PX4 checkout). By MV2 of Munim, Inc.; made with AI agents. Apache-2.0.

The C++ below is written for these tests in the style of PX4's check files; it is not copied from PX4.
"""

import json
import os
import shutil
import tempfile
import unittest

import armcat

SAMPLE = "\n".join([
    '#include "fooCheck.hpp"',                                                                   # 1
    "",                                                                                          # 2
    "using namespace time_literals;",                                                            # 3
    "",                                                                                          # 4
    "void FooChecks::checkAndReport(const Context &context, Report &reporter)",                  # 5
    "{",                                                                                         # 6
    "\tif (_param_com_foo.get() > 0 && !ready) {",                                               # 7
    "\t\t/* EVENT",                                                                              # 8
    "\t\t * @description",                                                                       # 9
    "\t\t * The foo sensor is not ready.",                                                       # 10
    "\t\t *",                                                                                    # 11
    '\t\t * <profile name="dev">',                                                               # 12
    "\t\t * Can be configured with <param>COM_FOO</param>.",                                     # 13
    "\t\t * </profile>",                                                                         # 14
    "\t\t */",                                                                                   # 15
    "\t\treporter.armingCheckFailure<uint8_t>(NavModes::All, health_component_t::battery,",      # 16
    '\t\t\t\t\t\t     events::ID("check_foo_not_ready"),',                                       # 17
    '\t\t\t\t\t\t     events::Log::Error, "Foo {1}: not ready (see docs)", instance);',          # 18
    "",                                                                                          # 19
    "\t\tif (reporter.mavlink_log_pub()) {",                                                     # 20
    '\t\t\tmavlink_log_critical(reporter.mavlink_log_pub(), "Preflight Fail: Foo not ready");',  # 21
    "\t\t}",                                                                                     # 22
    "\t}",                                                                                       # 23
    "",                                                                                          # 24
    '\t// reporter.healthFailure(NavModes::All, x, y, z, "in a comment");',                      # 25
    '\tconst char *s = "reporter.healthFailure(";',                                              # 26
    "",                                                                                          # 27
    "\tif (_param_com_bar.get() == 2) {",                                                        # 28
    "\t\t/* EVENT",                                                                              # 29
    "\t\t */",                                                                                   # 30
    '\t\treporter.healthFailure(required_modes, health_component_t::gps, events::ID("check_bar_" "lost"),',  # 31
    '\t\t\t\t       log_level, "Bar lost, "',                                                    # 32
    '\t\t\t\t       "check wiring");',                                                           # 33
    "\t}",                                                                                       # 34
    "",                                                                                          # 35
    '\tevents::send<float>(events::ID("foo_event"), {events::Log::Warning, events::LogInternal::Info}, "Foo event {1:.1}", x);',  # 36
    "}",                                                                                         # 37
    "",                                                                                          # 38
    "void FooChecks::other(Report &reporter)",                                                   # 39
    "{",                                                                                         # 40
    '\treporter.healthWarning(NavModes::None, health_component_t::system, events::ID("check_baz"), events::Log::Warning, "Baz, \\"quoted\\"");',  # 41
    '\tmavlink_log_critical(&_mavlink_log_pub, "Arming denied: baz");',                          # 42
    "}",                                                                                         # 43
    "",
])

HPP = "\n".join([
    "class FooChecks : public HealthAndArmingCheckBase",
    "{",
    "\tDEFINE_PARAMETERS_CUSTOM_PARENT(HealthAndArmingCheckBase,",
    "\t\t\t\t\t(ParamInt<px4::params::COM_FOO>) _param_com_foo,",
    "\t\t\t\t\t(ParamFloat<px4::params::COM_BAR>) _param_com_bar",
    "\t\t\t\t       );",
    "};",
    "",
])

PATH = "src/modules/commander/HealthAndArmingChecks/checks/fooCheck.cpp"


def scan():
    return armcat.scan_text(SAMPLE, PATH, armcat.param_map_from(HPP))


def reports(recs):
    return [r for r in recs if r["kind"] in armcat.REPORT_KINDS]


class TestLexing(unittest.TestCase):
    def test_mask_keeps_length_and_newlines(self):
        m = armcat.mask(SAMPLE)
        self.assertEqual(len(m), len(SAMPLE))
        self.assertEqual(m.count("\n"), SAMPLE.count("\n"))
        self.assertNotIn("in a comment", m)
        self.assertNotIn("Foo not ready", m)
        nc = armcat.mask(SAMPLE, strings=False)
        self.assertIn("Foo not ready", nc)
        self.assertNotIn("in a comment", nc)

    def test_char_literal_and_escape(self):
        t = "a = '\"'; b = \"x\\\"y\"; c = f(1);"
        m = armcat.mask(t)
        self.assertEqual(len(m), len(t))
        self.assertIn("c = f(1);", m)
        self.assertNotIn("x", m.split("b =")[1].split(";")[0])

    def test_match_paren_and_split(self):
        t = 'f(a, g(b, c), "x, (y", {d, e})'
        m = armcat.mask(t)
        c = armcat.match_paren(m, 1)
        self.assertEqual(c, len(t) - 1)
        self.assertEqual(armcat.split_args(t, m, 2, c), ["a", "g(b, c)", '"x, (y"', "{d, e}"])

    def test_literal(self):
        self.assertEqual(armcat.literal('"a" "b"'), "ab")
        self.assertEqual(armcat.literal('"a\\"b"'), 'a"b')
        self.assertIsNone(armcat.literal("msg"))
        self.assertIsNone(armcat.literal('"a" + x'))
        self.assertEqual(armcat.event_id('events::ID("x_" "y")'), "x_y")
        self.assertEqual(armcat.log_level("events::Log::Error"), "Error")
        self.assertEqual(armcat.log_level("{events::Log::Warning, events::LogInternal::Info}"), "Warning")
        self.assertIsNone(armcat.log_level("log_level"))

    def test_param_map(self):
        self.assertEqual(armcat.param_map_from(HPP), {"_param_com_foo": "COM_FOO", "_param_com_bar": "COM_BAR"})


class TestScan(unittest.TestCase):
    def test_three_reports_comment_and_string_ignored(self):
        rs = reports(scan())
        self.assertEqual([r["line"] for r in rs], [16, 31, 41])
        self.assertEqual([r["kind"] for r in rs], ["arming_check", "health_failure", "health_warning"])
        self.assertTrue(all(r["parsed"] for r in rs))

    def test_first_report(self):
        r = reports(scan())[0]
        self.assertEqual(r["event_id"], "check_foo_not_ready")
        self.assertEqual(r["template"], "uint8_t")
        self.assertEqual(r["modes"], ["All"])
        self.assertEqual(r["component"], "battery")
        self.assertEqual(r["log"], "Error")
        self.assertEqual(r["message"], "Foo {1}: not ready (see docs)")
        self.assertEqual(r["extra_args"], ["instance"])
        self.assertEqual(r["function"], "FooChecks::checkAndReport")
        self.assertEqual(r["description"], "The foo sensor is not ready.\n\nCan be configured with COM_FOO.")
        self.assertEqual(r["params_doc"], ["COM_FOO"])
        self.assertEqual(r["params_near"], ["COM_FOO"])
        self.assertEqual(r["console"], [{"level": "critical", "text": "Preflight Fail: Foo not ready",
                                         "text_raw": '"Preflight Fail: Foo not ready"'}])
        self.assertTrue(r["source"].startswith("\t\t/* EVENT"))
        self.assertTrue(r["source"].endswith("instance);"))
        self.assertEqual(r["source"].count("\n"), 10)

    def test_second_report(self):
        r = reports(scan())[1]
        self.assertEqual(r["event_id"], "check_bar_lost")
        self.assertIsNone(r["modes"])
        self.assertEqual(r["modes_raw"], "required_modes")
        self.assertIsNone(r["log"])
        self.assertEqual(r["log_raw"], "log_level")
        self.assertEqual(r["message"], "Bar lost, check wiring")
        self.assertEqual(r["params_near"], ["COM_BAR"])
        self.assertEqual(r["description"], "")
        self.assertEqual(r["console"], [])

    def test_third_report_and_unattached_console(self):
        recs = scan()
        r = reports(recs)[2]
        self.assertEqual(r["message"], 'Baz, "quoted"')
        self.assertEqual(r["modes"], ["None"])
        self.assertEqual(r["function"], "FooChecks::other")
        self.assertEqual(r["params_near"], [])
        self.assertEqual(r["console"], [])
        cons = [x for x in recs if x["kind"] == "console"]
        self.assertEqual([(x["line"], x["message"], x["attached_event_id"]) for x in cons],
                         [(21, "Preflight Fail: Foo not ready", "check_foo_not_ready"),
                          (42, "Arming denied: baz", None)])

    def test_events_send(self):
        ev = [x for x in scan() if x["kind"] == "event"]
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["line"], ev[0]["event_id"], ev[0]["log"], ev[0]["message"], ev[0]["template"]),
                         (36, "foo_event", "Warning", "Foo event {1:.1}", "float"))
        self.assertEqual(ev[0]["extra_args"], ["x"])


def _rec(eid, msg, path="a/x.cpp", kind="arming_check"):
    return {"kind": kind, "event_id": eid, "message": msg, "message_raw": None, "path": path}


class TestCompare(unittest.TestCase):
    def test_diff_and_presence(self):
        by_tag = {"a": [_rec("x", "X"), _rec("y", "Y")], "b": [_rec("y", "Y2"), _rec("z", "Z"), _rec(None, "N")],
                  "c": [_rec("z", "Z")]}
        d = armcat.diff(by_tag, ["a", "b", "missing", "c"])
        self.assertEqual(d[0], {"from": "a", "to": "b", "added": ["x.cpp|N", "z"], "removed": ["x"],
                                "message_changed": ["y"]})
        self.assertEqual(d[1], {"from": "b", "to": "c", "added": [], "removed": ["x.cpp|N", "y"],
                                "message_changed": []})
        p = armcat.presence(by_tag, ["a", "b", "c"])
        self.assertEqual(p["z"], ["b", "c"])
        self.assertEqual(p["x"], ["a"])


class TestRender(unittest.TestCase):
    def test_pages(self):
        recs = scan()
        pres = {armcat.key(r): ["v9.9.9"] for r in reports(recs)}
        md = armcat.render_md(recs, "v9.9.9", "0123456789abcdef", pres)
        self.assertIn("### Foo {1}: not ready (see docs)", md)
        self.assertIn("named by PX4: COM_FOO", md)
        self.assertIn("near the check: COM_BAR", md)
        self.assertIn("3 reports in 1 check files", md)
        self.assertIn("blob/0123456789abcdef/" + PATH + "#L16", md)
        self.assertIn("Made with AI agents", md)
        h = armcat.render_html(recs, "v9.9.9", "0123456789abcdef", pres)
        self.assertIn('<section id="check_foo_not_ready">', h)
        self.assertIn("Baz, &quot;quoted&quot;", h)
        self.assertNotIn('Baz, "quoted"', h)
        self.assertIn("Preflight Fail: Foo not ready", h)


class TestTree(unittest.TestCase):
    def test_scan_tree(self):
        d = tempfile.mkdtemp()
        try:
            base = os.path.join(d, *armcat.CHECKS_DIR.split("/"), "checks")
            os.makedirs(base)
            with open(os.path.join(base, "fooCheck.cpp"), "w", encoding="utf-8", newline="\n") as f:
                f.write(SAMPLE)
            with open(os.path.join(base, "fooCheck.hpp"), "w", encoding="utf-8", newline="\n") as f:
                f.write(HPP)
            recs, st = armcat.scan_tree(d)
            self.assertEqual(st["files"], 2)
            self.assertEqual(st["mentions_in_checks"], 5)  # 3 calls + 1 in a comment + 1 in a string
            rs = reports(recs)
            self.assertEqual(len(rs), 3)
            self.assertEqual(rs[0]["path"], PATH)
            self.assertEqual(rs[1]["params_near"], ["COM_BAR"])
            json.dumps(recs)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
