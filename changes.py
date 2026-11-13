#!/usr/bin/env python3
"""changes.py: what changed in PX4's health and arming check messages between releases, from an armcat.py build.

    python changes.py OUT_DIR [--dest DIR]

Reads summary.json and catalog-<tag>.json written by `armcat.py build` in OUT_DIR and writes changes.json,
changes.md and changes.html to DIR (default OUT_DIR). For each pair of consecutive releases it lists every
report (arming check, health failure, health warning) that was added, removed or reworded, with its event ID,
its check file, its message text and a link to its source line at the release's commit.

A report is matched across releases by its PX4 event ID (or, when it has none, by file name and message), the
same key armcat.py uses for diff.json, so a check whose event ID was renamed shows as one removal and one addition.

By MV2 of Munim, Inc.; made with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

import armcat

__version__ = "0.1.0"

SECTIONS = (("added", "Added"), ("removed", "Removed"), ("message_changed", "Reworded"))


def safe(tag):
    """The file-name form of a tag, as armcat.py writes catalog-<tag>.json."""
    return re.sub(r"[^\w.-]", "_", tag)


def load(out_dir):
    """(tags, {tag: records}, {tag: commit sha}) from an armcat.py build folder."""
    with open(os.path.join(out_dir, "summary.json"), encoding="utf-8") as f:
        summary = json.load(f)
    tags = list(summary.get("ok_tags") or [])
    by_tag, shas = {}, {}
    for tag in tags:
        with open(os.path.join(out_dir, "catalog-%s.json" % safe(tag)), encoding="utf-8") as f:
            cat = json.load(f)
        by_tag[tag] = cat["records"]
        shas[tag] = cat.get("sha") or ""
    return tags, by_tag, shas


def first_by_key(recs):
    """{key: the report record with that key that comes first by path and line}."""
    out = {}
    for r in sorted(recs, key=lambda x: (x.get("path") or "", x.get("line") or 0)):
        if r.get("kind") in armcat.REPORT_KINDS:
            out.setdefault(armcat.key(r), r)
    return out


def _joined(texts):
    if texts is None:
        return None
    return " / ".join(t.replace("\n", " ") for t in texts)


def _url(r, sha):
    if r is None:
        return None
    if r.get("url"):
        return r["url"]
    return armcat.BLOB_URL.format(sha=sha, path=r["path"], line=r["line"])


def _row(k, ra, rb, ta, tb, sha_a, sha_b):
    r = rb if rb is not None else ra
    return {"key": k, "event_id": r.get("event_id"), "kind": r.get("kind"), "check": armcat._check_name(r["path"]),
            "old": _joined(ta), "new": _joined(tb), "old_url": _url(ra, sha_a), "new_url": _url(rb, sha_b)}


def changes(tags, by_tag, shas):
    """One step per pair of consecutive tags, with added / removed / message_changed rows."""
    tags = [t for t in tags if t in by_tag]
    steps = []
    for d in armcat.diff(by_tag, tags):
        t0, t1 = d["from"], d["to"]
        fa, fb = first_by_key(by_tag[t0]), first_by_key(by_tag[t1])
        ia, ib = armcat._index(by_tag[t0]), armcat._index(by_tag[t1])
        sa, sb = shas.get(t0, ""), shas.get(t1, "")
        step = {"from": t0, "to": t1, "from_sha": sa, "to_sha": sb,
                "reports_from": len(ia), "reports_to": len(ib)}
        for name, _ in SECTIONS:
            rows = [_row(k, fa.get(k), fb.get(k), ia.get(k), ib.get(k), sa, sb) for k in d[name]]
            step[name] = sorted(rows, key=lambda x: (x["check"].lower(), x["key"]))
        step["counts"] = {name: len(step[name]) for name, _ in SECTIONS}
        steps.append(step)
    return steps


# ---------------------------------------------------------------- rendering

def _md(s):
    return (s or "").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def _eid_md(row):
    return "`%s`" % row["event_id"] if row["event_id"] else "(none)"


def _title(tags):
    return "What changed in PX4's health and arming check messages (%s to %s)" % (tags[0], tags[-1]) if tags else \
        "What changed in PX4's health and arming check messages"


INTRO = ("Built by changes.py %s from the output of armcat.py, which reads every health and arming check report "
         "(arming check, health failure, health warning) from PX4's own source. For each pair of consecutive "
         "releases it lists the reports that were added, removed, or whose message text changed. Reports are "
         "matched by PX4 event ID, so a check whose event ID was renamed shows as one removal and one addition. "
         "Counts are distinct event IDs.")
FOOT = ("PX4-Autopilot source is BSD-3-Clause, Copyright (c) PX4 Development Team. This page: CC BY 4.0, "
        "MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.")


def render_md(steps, tags):
    out = ["# " + _title(tags), "", INTRO % __version__, "",
           "| From | To | Reports (from) | Reports (to) | Added | Removed | Reworded |", "|---|---|---|---|---|---|---|"]
    for s in steps:
        c = s["counts"]
        out.append("| %s | %s | %d | %d | %d | %d | %d |" % (_md(s["from"]), _md(s["to"]), s["reports_from"],
                                                            s["reports_to"], c["added"], c["removed"],
                                                            c["message_changed"]))
    out.append("")
    for s in steps:
        out += ["## %s to %s" % (s["from"], s["to"]), ""]
        for name, label in SECTIONS:
            rows = s[name]
            out += ["### %s (%d)" % (label, len(rows)), ""]
            if not rows:
                out += ["None.", ""]
                continue
            if name == "message_changed":
                out += ["| Check | Event ID | Before (%s) | After (%s) | Source |" % (_md(s["from"]), _md(s["to"])),
                        "|---|---|---|---|---|"]
                for r in rows:
                    out.append("| %s | %s | %s | %s | [%s](%s) |" % (_md(r["check"]), _eid_md(r), _md(r["old"]),
                                                                     _md(r["new"]), _md(s["to"]), r["new_url"]))
            else:
                tag, text, url = ((s["to"], "new", "new_url") if name == "added" else (s["from"], "old", "old_url"))
                out += ["| Check | Event ID | Message | Source |", "|---|---|---|---|"]
                for r in rows:
                    out.append("| %s | %s | %s | [%s](%s) |" % (_md(r["check"]), _eid_md(r), _md(r[text]), _md(tag),
                                                                r[url]))
            out.append("")
    out += ["---", "", FOOT, ""]
    return "\n".join(out)


def render_html(steps, tags):
    e = html.escape
    title = _title(tags)
    parts = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             "<title>%s</title>" % e(title),
             '<meta name="description" content="PX4 arming and health check messages added, removed or reworded '
             'between releases, read from PX4\'s source, with links to each source line.">',
             "<style>body{font-family:system-ui,sans-serif;max-width:68rem;margin:auto;padding:1rem;line-height:1.45}"
             "table{border-collapse:collapse;margin:.4rem 0 1rem}td,th{border:1px solid #ddd;padding:.2rem .4rem;"
             "vertical-align:top;text-align:left}code{background:#f4f4f4;padding:0 .2rem}</style></head><body>",
             "<h1>%s</h1>" % e(title), "<p>%s</p>" % e(INTRO % __version__),
             '<p>The full catalogue, with parameters and PX4\'s descriptions: <a href="index.html">PX4 health and '
             'arming check messages</a>. To check a flight log in your browser without uploading it: '
             '<a href="%s">PX4 log check</a>.</p>' % e(armcat.CHECKER_URL),
             "<table><tr><th>From</th><th>To</th><th>Reports (from)</th><th>Reports (to)</th><th>Added</th>"
             "<th>Removed</th><th>Reworded</th></tr>"]
    for s in steps:
        c = s["counts"]
        parts.append('<tr><td>%s</td><td><a href="#%s">%s</a></td><td>%d</td><td>%d</td><td>%d</td><td>%d</td>'
                     "<td>%d</td></tr>" % (e(s["from"]), e(safe(s["from"]) + "-" + safe(s["to"])), e(s["to"]),
                                           s["reports_from"], s["reports_to"], c["added"], c["removed"],
                                           c["message_changed"]))
    parts.append("</table>")

    def eid(r):
        return "<code>%s</code>" % e(r["event_id"]) if r["event_id"] else "(none)"

    for s in steps:
        parts.append('<h2 id="%s">%s to %s</h2>' % (e(safe(s["from"]) + "-" + safe(s["to"])), e(s["from"]),
                                                     e(s["to"])))
        for name, label in SECTIONS:
            rows = s[name]
            parts.append("<h3>%s (%d)</h3>" % (e(label), len(rows)))
            if not rows:
                parts.append("<p>None.</p>")
                continue
            if name == "message_changed":
                parts.append("<table><tr><th>Check</th><th>Event ID</th><th>Before (%s)</th><th>After (%s)</th>"
                             "<th>Source</th></tr>" % (e(s["from"]), e(s["to"])))
                for r in rows:
                    parts.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><a href="%s">%s</a></td></tr>'
                                 % (e(r["check"]), eid(r), e(r["old"] or ""), e(r["new"] or ""), e(r["new_url"]),
                                    e(s["to"])))
            else:
                tag, text, url = ((s["to"], "new", "new_url") if name == "added" else (s["from"], "old", "old_url"))
                parts.append("<table><tr><th>Check</th><th>Event ID</th><th>Message</th><th>Source</th></tr>")
                for r in rows:
                    parts.append('<tr><td>%s</td><td>%s</td><td>%s</td><td><a href="%s">%s</a></td></tr>'
                                 % (e(r["check"]), eid(r), e(r[text] or ""), e(r[url]), e(tag)))
            parts.append("</table>")
    parts.append("<hr><p><small>%s Generated by changes.py (Apache-2.0).</small></p></body></html>" % e(FOOT))
    return "\n".join(parts) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out_dir")
    ap.add_argument("--dest")
    a = ap.parse_args(argv)
    dest = a.dest or a.out_dir
    os.makedirs(dest, exist_ok=True)
    tags, by_tag, shas = load(a.out_dir)
    steps = changes(tags, by_tag, shas)
    with open(os.path.join(dest, "changes.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"tool": "changes.py", "version": __version__, "tags": tags, "steps": steps}, f, indent=1,
                  ensure_ascii=False)
        f.write("\n")
    for name, text in (("changes.md", render_md(steps, tags)), ("changes.html", render_html(steps, tags))):
        with open(os.path.join(dest, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print(json.dumps([{"from": s["from"], "to": s["to"], "reports_from": s["reports_from"],
                       "reports_to": s["reports_to"], **s["counts"]} for s in steps], indent=1), flush=True)
    return 0 if steps else 1


if __name__ == "__main__":
    sys.exit(main())
