#!/usr/bin/env python3
"""armcat.py: a catalogue of PX4's health and arming check messages, read from PX4's own source.

Since v1.14, PX4 reports why a vehicle will not arm (and which parts of the system are unhealthy) through calls
such as reporter.armingCheckFailure(...) in src/modules/commander/HealthAndArmingChecks. Each call carries an
event ID, a log level, the flight modes it blocks, a short message, and often a /* EVENT */ comment whose
description names the parameters that control the check. Many checks also print a "Preflight Fail: ..." line
to the MAVLink console. PX4's documentation lists only a handful of these messages. This tool reads every call
straight from the source of each PX4 release and writes one record per call, so a message seen in
QGroundControl or a log can be traced to its check, its parameters and its exact source line, and so changes
between releases can be listed.

    python armcat.py build --out /out --tags v1.14.4 v1.15.4 v1.16.2 v1.17.0 --page-tag v1.17.0
    python armcat.py scan <checkout-root> [--tag NAME] [--sha SHA]    # print JSON lines for a local checkout

Only PX4's public git repository is fetched (shallow, sparse: src/modules/commander only). Standard library
only. The parser masks comments and string literals before matching, so commented-out calls are ignored.
"Parameters near the check" come from a simple scan of the code between the previous report call (or the
start of the function) and this one: they may include parameters that do not control this message.

By MV2 of Munim, Inc.; made with AI agents. Apache-2.0.
PX4 source excerpts in the output are PX4-Autopilot code (BSD-3-Clause, Copyright (c) PX4 Development Team).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time

__version__ = "0.1.0"

REPO_URL = "https://github.com/PX4/PX4-Autopilot.git"
BLOB_URL = "https://github.com/PX4/PX4-Autopilot/blob/{sha}/{path}#L{line}"
SCAN_DIR = "src/modules/commander"
CHECKS_DIR = "src/modules/commander/HealthAndArmingChecks"
KIND = {"armingCheckFailure": "arming_check", "healthFailure": "health_failure", "healthWarning": "health_warning"}
REPORT_KINDS = tuple(KIND.values())
KIND_LABEL = {"arming_check": "arming check", "health_failure": "health failure", "health_warning": "health warning",
              "event": "event", "console": "console text"}
SOURCE_LICENCE = "PX4-Autopilot source excerpts: BSD-3-Clause, Copyright (c) PX4 Development Team"
CHECKER_URL = "https://munimv2.github.io/px4-log-check/"
REPO_SELF = "https://github.com/munimtechnologies/px4-arming-checks"
CONSOLE_WINDOW = 800

RE_REPORT = re.compile(r"\b(\w+)\s*(?:\.|->)\s*(armingCheckFailure|healthFailure|healthWarning)\b\s*(<[^;{}()]*>)?\s*\(")
RE_SEND = re.compile(r"\bevents\s*::\s*send\b\s*(<[^;{}()]*>)?\s*\(")
RE_MAVLOG = re.compile(r"\bmavlink_log_(critical|warning|info|emergency|notice)\b\s*\(")
RE_FUNC = re.compile(r"^(?=[A-Za-z_])[^\n;{}]*?\b([A-Za-z_]\w*(?:::~?\w+)+)\s*\(", re.M)
RE_PARAM_DEF = re.compile(r"px4::params::(\w+)\s*>\s*\)\s*(\w+)")
RE_PARAM_USE = re.compile(r"\b(_param_\w+)")
RE_PARAM_FIND = re.compile(r"param_find\s*\(\s*\"(\w+)\"")
RE_LIT = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
RE_DOC_PARAM = re.compile(r"<param>\s*(\w+)\s*</param>")
RE_MENTION = re.compile(r"(?:\.|->)\s*(armingCheckFailure|healthFailure|healthWarning)\b")


# ---------------------------------------------------------------- lexing helpers

def _blank(out, text, a, b):
    for k in range(a, b):
        if text[k] != "\n":
            out[k] = " "


def mask(text, strings=True):
    """Same-length copy of C++ text with comments blanked and, if strings, string/char literal contents blanked."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            _blank(out, text, i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            _blank(out, text, i, j)
            i = j
        elif text[i] in "\"'":
            q = text[i]
            j = i + 1
            while j < n and text[j] != q and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            if strings:
                _blank(out, text, i + 1, min(j, n))
            i = j + 1
        else:
            i += 1
    return "".join(out)


def match_paren(m, i):
    """Index of the bracket closing the one at m[i], or -1."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = []
    for k in range(i, len(m)):
        c = m[k]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if not stack or stack[-1] != c:
                return -1
            stack.pop()
            if not stack:
                return k
    return -1


def split_args(text, m, a, b):
    """Split text[a:b] at top-level commas (bracket depth taken from the masked copy m)."""
    out, depth, start = [], 0, a
    for k in range(a, b):
        c = m[k]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(text[start:k].strip())
            start = k + 1
    last = text[start:b].strip()
    if last or out:
        out.append(last)
    return out


def unescape(s):
    return re.sub(r"\\(.)", lambda mm: {"n": "\n", "t": "\t"}.get(mm.group(1), mm.group(1)), s)


def literal(s):
    """The value of an argument made only of (adjacent) string literals, else None."""
    if s is None:
        return None
    s = s.strip()
    lits = RE_LIT.findall(s)
    rest = RE_LIT.sub("", s).strip()
    if not lits or rest:
        return None
    return "".join(unescape(x) for x in lits)


def event_id(s):
    mm = re.match(r"events\s*::\s*ID\s*\((.*)\)\s*$", (s or "").strip(), re.S)
    return literal(mm.group(1)) if mm else None


def log_level(s):
    s = (s or "").strip()
    if s.startswith("{"):
        mm = re.search(r"events\s*::\s*Log\s*::\s*(\w+)", s)
        return mm.group(1) if mm else None
    mm = re.fullmatch(r"events\s*::\s*Log\s*::\s*(\w+)", s)
    return mm.group(1) if mm else None


def component(s):
    mm = re.fullmatch(r"health_component_t\s*::\s*(\w+)", (s or "").strip())
    return mm.group(1) if mm else None


def modes(s):
    found = re.findall(r"NavModes\s*::\s*(\w+)", s or "")
    return found or None


def lineno(text, pos):
    return text.count("\n", 0, pos) + 1


def line_start(text, pos):
    return text.rfind("\n", 0, pos) + 1


def line_end(text, pos):
    j = text.find("\n", pos)
    return len(text) if j < 0 else j


def _paras(lines):
    paras, cur = [], []
    for s in list(lines) + [""]:
        if s:
            cur.append(s)
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    txt = "\n\n".join(paras)
    txt = RE_DOC_PARAM.sub(lambda mm: mm.group(1), txt)
    txt = re.sub(r"</?profile\b[^>]*>", "", txt)
    txt = txt.replace("\\>", ">").replace("\\<", "<")
    return re.sub(r"[ \t]+", " ", txt).strip()


def parse_event(body):
    """Parse the inside of a /* EVENT ... */ comment."""
    lines = []
    for ln in body.split("\n"):
        ln = ln.strip()
        if ln.startswith("*"):
            ln = ln[1:]
            if ln.startswith(" "):
                ln = ln[1:]
        lines.append(ln.rstrip())
    lines[0] = re.sub(r"^\s*EVENT\b", "", lines[0]).strip()
    params = []
    for p in RE_DOC_PARAM.findall(body):
        if p not in params:
            params.append(p)
    summary, desc, args, cur = [], [], {}, "summary"
    for ln in lines:
        s = ln.strip()
        if s.startswith("@description"):
            cur = "desc"
            rest = s[len("@description"):].strip()
            if rest:
                desc.append(rest)
            continue
        mm = re.match(r"@arg(\d+)\s+(\w+)", s)
        if mm:
            args[mm.group(1)] = mm.group(2)
            continue
        if s.startswith("@"):
            continue
        if re.match(r"</?profile\b[^>]*>$", s):
            continue
        (desc if cur == "desc" else summary).append(s)
    return {"summary": _paras(summary), "description": _paras(desc), "params_doc": params, "args_doc": args}


def event_comment(text, start):
    """(comment start, parsed) for a /* EVENT */ comment directly before position start, else None."""
    before = text[:start].rstrip()
    if not before.endswith("*/"):
        return None
    a = before.rfind("/*")
    if a < 0:
        return None
    body = before[a + 2:-2]
    if not re.match(r"\s*EVENT\b", body):
        return None
    return a, parse_event(body)


def param_map_from(text):
    """{'_param_com_foo': 'COM_FOO'} from DEFINE_PARAMETERS entries."""
    return {ident: name for name, ident in RE_PARAM_DEF.findall(mask(text))}


def _params_in(m_slice, text_slice, pmap):
    out = []
    for ident in RE_PARAM_USE.findall(m_slice):
        name = pmap.get(ident, ident)
        if name not in out:
            out.append(name)
    for name in RE_PARAM_FIND.findall(text_slice):
        if name not in out:
            out.append(name)
    return out


def _enclosing(funcs, pos):
    best = (0, None)
    for start, name in funcs:
        if start < pos:
            best = (start, name)
        else:
            break
    return best


# ---------------------------------------------------------------- scanning

def scan_text(text, path, pmap=None):
    """All report calls, events::send calls and mavlink_log_* console texts in one C++ file."""
    pmap = pmap or {}
    m = mask(text)
    nc = mask(text, strings=False)
    funcs = [(mm.start(), mm.group(1)) for mm in RE_FUNC.finditer(m)]
    mavlogs = []
    for mm in RE_MAVLOG.finditer(m):
        o = mm.end() - 1
        c = match_paren(m, o)
        args = split_args(nc, m, o + 1, c) if c > 0 else []
        mavlogs.append({"start": mm.start(), "level": mm.group(1), "args": args,
                        "text": literal(args[1]) if len(args) >= 2 else None,
                        "text_raw": args[1] if len(args) >= 2 else None, "attached": None})
    reports = list(RE_REPORT.finditer(m))
    records = []
    prev_close = 0
    for idx, mm in enumerate(reports):
        o = mm.end() - 1
        c = match_paren(m, o)
        fstart, fname = _enclosing(funcs, mm.start())
        rec = {"kind": KIND[mm.group(2)], "method": mm.group(2), "object": mm.group(1),
               "template": ((mm.group(3) or "")[1:-1].strip() or None), "path": path,
               "line": lineno(text, mm.start()), "function": fname, "event_id": None, "message": None,
               "message_raw": None, "log": None, "log_raw": None, "component": None, "modes": None,
               "modes_raw": None, "extra_args": [], "summary": "", "description": "", "params_doc": [],
               "args_doc": {}, "params_near": [], "console": [], "source": None, "parsed": False}
        if c < 0:
            rec["source"] = text[line_start(text, mm.start()):line_end(text, mm.start())]
            records.append(rec)
            continue
        args = split_args(nc, m, o + 1, c)
        if len(args) >= 5:
            rec["modes_raw"], rec["modes"] = args[0], modes(args[0])
            rec["component"] = component(args[1])
            rec["event_id"] = event_id(args[2])
            rec["log_raw"], rec["log"] = args[3], log_level(args[3])
            rec["message_raw"], rec["message"] = args[4], literal(args[4])
            rec["extra_args"] = args[5:]
        rec["parsed"] = bool(rec["event_id"] and rec["message"] is not None)
        ec = event_comment(text, mm.start())
        src_a = line_start(text, mm.start())
        if ec:
            src_a = line_start(text, ec[0])
            for k in ("summary", "description", "params_doc", "args_doc"):
                rec[k] = ec[1][k]
        rec["source"] = text[src_a:line_end(text, c)]
        w0 = max(prev_close, fstart)
        rec["params_near"] = _params_in(m[w0:c + 1], nc[w0:c + 1], pmap)
        limit = min(reports[idx + 1].start() if idx + 1 < len(reports) else len(text), c + CONSOLE_WINDOW)
        for ml in mavlogs:
            if c < ml["start"] < limit and ml["args"] and mm.group(1) in ml["args"][0]:
                ml["attached"] = rec["event_id"]
                rec["console"].append({"level": ml["level"], "text": ml["text"], "text_raw": ml["text_raw"]})
        prev_close = c
        records.append(rec)
    for mm in RE_SEND.finditer(m):
        o = mm.end() - 1
        c = match_paren(m, o)
        if c < 0:
            continue
        args = split_args(nc, m, o + 1, c)
        rec = {"kind": "event", "method": "events::send", "template": ((mm.group(1) or "")[1:-1].strip() or None),
               "path": path, "line": lineno(text, mm.start()), "function": _enclosing(funcs, mm.start())[1],
               "event_id": event_id(args[0]) if args else None,
               "log": log_level(args[1]) if len(args) > 1 else None, "log_raw": args[1] if len(args) > 1 else None,
               "message": literal(args[2]) if len(args) > 2 else None,
               "message_raw": args[2] if len(args) > 2 else None, "extra_args": args[3:],
               "summary": "", "description": "", "params_doc": [], "args_doc": {}}
        ec = event_comment(text, mm.start())
        src_a = line_start(text, mm.start())
        if ec:
            src_a = line_start(text, ec[0])
            for k in ("summary", "description", "params_doc", "args_doc"):
                rec[k] = ec[1][k]
        rec["source"] = text[src_a:line_end(text, c)]
        rec["parsed"] = bool(rec["event_id"] and rec["message"] is not None)
        records.append(rec)
    for ml in mavlogs:
        records.append({"kind": "console", "method": "mavlink_log_" + ml["level"], "level": ml["level"],
                        "path": path, "line": lineno(text, ml["start"]),
                        "function": _enclosing(funcs, ml["start"])[1], "message": ml["text"],
                        "message_raw": ml["text_raw"], "attached_event_id": ml["attached"],
                        "source": text[line_start(text, ml["start"]):line_end(text, ml["start"])],
                        "parsed": ml["text"] is not None})
    records.sort(key=lambda r: (r["line"], r["kind"]))
    return records


def _files(root, rel_dir):
    base = os.path.join(root, *rel_dir.split("/"))
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith((".cpp", ".hpp", ".h", ".c")):
                full = os.path.join(dirpath, name)
                out.append((os.path.relpath(full, root).replace(os.sep, "/"), full))
    return out


def scan_tree(root, rel_dir=SCAN_DIR):
    files = _files(root, rel_dir)
    texts = {}
    for rel, full in files:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            texts[rel] = f.read()
    pmap = {}
    for rel, text in texts.items():
        pmap.update(param_map_from(text))
    records, stats = [], {"files": len(texts), "sha256": {}, "mentions_in_checks": 0}
    for rel, text in texts.items():
        stats["sha256"][rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if rel.startswith(CHECKS_DIR + "/"):
            stats["mentions_in_checks"] += len(RE_MENTION.findall(text))
        records.extend(scan_text(text, rel, pmap))
    return records, stats


# ---------------------------------------------------------------- comparing releases

def key(r):
    return r.get("event_id") or "%s|%s" % (r["path"].rsplit("/", 1)[-1], r.get("message") or r.get("message_raw"))


def _index(recs):
    idx = {}
    for r in recs:
        if r["kind"] in REPORT_KINDS:
            idx.setdefault(key(r), set()).add(r.get("message") or r.get("message_raw") or "")
    return {k: sorted(v) for k, v in idx.items()}


def diff(by_tag, tags):
    out = []
    tags = [t for t in tags if t in by_tag]
    for t0, t1 in zip(tags, tags[1:]):
        a, b = _index(by_tag[t0]), _index(by_tag[t1])
        out.append({"from": t0, "to": t1, "added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
                    "message_changed": sorted(k for k in set(a) & set(b) if a[k] != b[k])})
    return out


def presence(by_tag, tags):
    pres = {}
    for t in tags:
        for k in _index(by_tag.get(t, [])):
            pres.setdefault(k, []).append(t)
    return pres


# ---------------------------------------------------------------- rendering

def _check_name(path):
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _page_groups(records):
    groups = {}
    for r in records:
        if r["kind"] in REPORT_KINDS:
            groups.setdefault(_check_name(r["path"]), []).append(r)
    return [(name, sorted(groups[name], key=lambda r: r["line"])) for name in sorted(groups, key=str.lower)]


def _params(r):
    doc = list(r.get("params_doc") or [])
    near = [p for p in (r.get("params_near") or []) if p not in doc and not p.startswith("_")]
    return doc, near


def render_md(records, tag, sha, pres):
    groups = _page_groups(records)
    n = sum(len(g) for _, g in groups)
    out = ["# PX4 health and arming check messages (%s)" % tag, "",
           "Every health and arming check report in PX4 %s (commit `%s`), read from PX4's own source by "
           "[armcat.py](%s) %s: %d reports in %d check files. Descriptions are PX4's own text from its EVENT "
           "comments. 'Named by PX4' parameters come from those descriptions; 'near the check' parameters are "
           "found by a simple code scan and may not control the message." % (tag, sha[:10], REPO_SELF, __version__,
                                                                           n, len(groups)), ""]
    for name, recs in groups:
        out += ["## %s" % name, ""]
        for r in recs:
            out += _md_record(r, sha, pres)
    out += ["---", "", SOURCE_LICENCE + ". This page: CC BY 4.0, MV2, Munim, Inc. (mv2@munimtech.com). "
            "Made with AI agents working in MV2's name.", ""]
    return "\n".join(out)


def _md_record(r, sha, pres):
    title = r["message"] if r["message"] is not None else (r["message_raw"] or "(message not a literal)")
    bits = ["event ID `%s`" % (r["event_id"] or "?"), KIND_LABEL[r["kind"]]]
    if r.get("log") or r.get("log_raw"):
        bits.append("log level %s" % (r.get("log") or "`%s`" % r["log_raw"]))
    if r.get("component"):
        bits.append("component %s" % r["component"])
    bits.append("modes %s" % ("|".join(r["modes"]) if r.get("modes") else "`%s`" % (r.get("modes_raw") or "?")))
    lines = ["### %s" % title.replace("\n", " "), "", "- " + "; ".join(bits)]
    for c in r.get("console") or []:
        lines.append("- Console (%s): %s" % (c["level"], c["text"] if c["text"] is not None else "`%s`" % c["text_raw"]))
    doc, near = _params(r)
    if doc or near:
        lines.append("- Parameters: %s" % "; ".join(
            x for x in ("named by PX4: " + ", ".join(doc) if doc else "",
                        "near the check: " + ", ".join(near) if near else "") if x))
    if r.get("description"):
        lines.append("- PX4's description: " + r["description"].replace("\n\n", " "))
    lines.append("- In: %s" % ", ".join(pres.get(key(r), [])))
    lines.append("- Source: [%s line %d](%s)" % (r["path"].rsplit("/", 1)[-1], r["line"],
                                                 BLOB_URL.format(sha=sha, path=r["path"], line=r["line"])))
    lines.append("")
    return lines


def render_html(records, tag, sha, pres):
    e = html.escape
    groups = _page_groups(records)
    n = sum(len(g) for _, g in groups)
    used = {}
    parts = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             "<title>PX4 health and arming check messages (%s): every message, its parameters and source</title>"
             % e(tag),
             '<meta name="description" content="Every PX4 %s health and arming check message (Preflight Fail, '
             'arming denied), with the parameters that control it and its source line, read from PX4\'s source.">'
             % e(tag),
             "<style>body{font-family:system-ui,sans-serif;max-width:60rem;margin:auto;padding:1rem;line-height:1.45}"
             "section{border-top:1px solid #ddd;padding:.4rem 0}code{background:#f4f4f4;padding:0 .2rem}"
             "h3{margin:.6rem 0 .2rem;font-size:1.05rem}ul{margin:.2rem 0}</style></head><body>",
             "<h1>PX4 health and arming check messages (%s)</h1>" % e(tag),
             "<p>Every health and arming check report in PX4 %s (commit <code>%s</code>), read from PX4's own source "
             'by <a href="%s">armcat.py</a> %s: %d reports in %d check files. Descriptions are PX4\'s own text from '
             "its EVENT comments. Parameters &lsquo;named by PX4&rsquo; come from those descriptions; parameters "
             "&lsquo;near the check&rsquo; are found by a simple code scan and may not control the message. Use "
             "your browser's find (Ctrl+F) with the text QGroundControl shows.</p>"
             % (e(tag), e(sha[:10]), e(REPO_SELF), e(__version__), n, len(groups)),
             '<p>To check a flight log (vibration, GPS, battery, failsafes) in your browser, without uploading it: '
             '<a href="%s">PX4 log check</a>.</p>' % e(CHECKER_URL)]
    for name, recs in groups:
        parts.append("<h2 id=\"%s\">%s</h2>" % (e(name), e(name)))
        for r in recs:
            anchor = r["event_id"] or "%s-%d" % (name, r["line"])
            used[anchor] = used.get(anchor, 0) + 1
            if used[anchor] > 1:
                anchor = "%s-%d" % (anchor, used[anchor])
            title = r["message"] if r["message"] is not None else (r["message_raw"] or "(message not a literal)")
            items = ["event ID <code>%s</code>, %s" % (e(r["event_id"] or "?"), e(KIND_LABEL[r["kind"]]))]
            if r.get("log") or r.get("log_raw"):
                items[0] += ", log level %s" % (e(r["log"]) if r.get("log") else "<code>%s</code>" % e(r["log_raw"]))
            if r.get("component"):
                items[0] += ", component %s" % e(r["component"])
            items[0] += ", modes %s" % ("|".join(e(x) for x in r["modes"]) if r.get("modes")
                                        else "<code>%s</code>" % e(r.get("modes_raw") or "?"))
            for c in r.get("console") or []:
                items.append("console (%s): %s" % (e(c["level"]), e(c["text"]) if c["text"] is not None
                                                   else "<code>%s</code>" % e(c["text_raw"] or "")))
            doc, near = _params(r)
            if doc:
                items.append("parameters named by PX4: " + ", ".join("<code>%s</code>" % e(p) for p in doc))
            if near:
                items.append("parameters near the check: " + ", ".join("<code>%s</code>" % e(p) for p in near))
            if r.get("description"):
                items.append("PX4's description: " + e(r["description"]).replace("\n\n", "<br>"))
            items.append("in: " + e(", ".join(pres.get(key(r), []))))
            items.append('source: <a href="%s">%s line %d</a>' % (
                e(BLOB_URL.format(sha=sha, path=r["path"], line=r["line"])), e(r["path"].rsplit("/", 1)[-1]),
                r["line"]))
            parts.append('<section id="%s"><h3>%s</h3><ul>%s</ul></section>' % (
                e(anchor), e(title.replace("\n", " ")), "".join("<li>%s</li>" % x for x in items)))
    parts.append("<hr><p><small>%s. This page: CC BY 4.0. MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents "
                 "working in MV2's name; generated by armcat.py (Apache-2.0).</small></p></body></html>"
                 % e(SOURCE_LICENCE))
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------- fetching and building

def fetch(tag, dest, log):
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    cmds = [["git", "clone", "--quiet", "--depth", "1", "--branch", tag, "--filter=blob:none", "--sparse",
             REPO_URL, dest], ["git", "-C", dest, "sparse-checkout", "set", SCAN_DIR]]
    for cmd in cmds:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
            code, err = p.returncode, p.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            code, err = "error", str(exc)
        log.append({"tag": tag, "cmd": " ".join(cmd), "exit": code, "stderr": (err or "")[-2000:]})
        if code != 0:
            return None
    p = subprocess.run(["git", "-C", dest, "rev-parse", "HEAD"], capture_output=True, text=True)
    return p.stdout.strip() or None


def _write(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, indent=1, ensure_ascii=False)
            f.write("\n")


def build(out, tags, page_tag=None, workdir="/tmp/armcat"):
    os.makedirs(out, exist_ok=True)
    log, by_tag, shas, stats = [], {}, {}, {}
    for tag in tags:
        dest = os.path.join(workdir, re.sub(r"[^\w.-]", "_", tag))
        sha = fetch(tag, dest, log)
        if not sha:
            stats[tag] = {"ok": False}
            continue
        recs, st = scan_tree(dest)
        for r in recs:
            r["tag"], r["sha"] = tag, sha
            r["url"] = BLOB_URL.format(sha=sha, path=r["path"], line=r["line"])
        by_tag[tag], shas[tag] = recs, sha
        rep = [r for r in recs if r["kind"] in REPORT_KINDS]
        rep_checks = [r for r in rep if r["path"].startswith(CHECKS_DIR + "/")]
        st.update({"ok": True, "sha": sha, "records": len(recs),
                   "by_kind": {k: sum(1 for r in recs if r["kind"] == k) for k in REPORT_KINDS + ("event", "console")},
                   "reports_parsed": sum(1 for r in rep if r["parsed"]),
                   "reports_in_checks_dir": len(rep_checks),
                   "distinct_event_ids": len({r["event_id"] for r in rep if r["event_id"]}),
                   "with_description": sum(1 for r in rep if r["description"]),
                   "with_params_doc": sum(1 for r in rep if r["params_doc"]),
                   "with_console": sum(1 for r in rep if r["console"]),
                   "unparsed": [{"path": r["path"], "line": r["line"], "source": r["source"]}
                                for r in rep if not r["parsed"]][:40]})
        stats[tag] = st
        _write(os.path.join(out, "catalog-%s.json" % re.sub(r"[^\w.-]", "_", tag)),
               {"tool": "armcat.py", "version": __version__, "tag": tag, "sha": sha, "licence": SOURCE_LICENCE,
                "records": recs})
    ok_tags = [t for t in tags if t in by_tag]
    pres = presence(by_tag, ok_tags)
    d = diff(by_tag, ok_tags)
    _write(os.path.join(out, "diff.json"), d)
    _write(os.path.join(out, "presence.json"), pres)
    with open(os.path.join(out, "records.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for t in ok_tags:
            for r in by_tag[t]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    page_tag = page_tag if page_tag in by_tag else (ok_tags[-1] if ok_tags else None)
    if page_tag:
        _write(os.path.join(out, "catalog.md"), render_md(by_tag[page_tag], page_tag, shas[page_tag], pres))
        _write(os.path.join(out, "index.html"), render_html(by_tag[page_tag], page_tag, shas[page_tag], pres))
    summary = {"tool": "armcat.py", "version": __version__, "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "tags": tags, "ok_tags": ok_tags, "page_tag": page_tag, "stats": stats, "fetch_log": log,
               "diff_counts": [{"from": x["from"], "to": x["to"], "added": len(x["added"]), "removed": len(x["removed"]),
                                "message_changed": len(x["message_changed"])} for x in d]}
    _write(os.path.join(out, "summary.json"), summary)
    short = {t: {k: v for k, v in s.items() if k not in ("sha256", "unparsed")} for t, s in stats.items()}
    print(json.dumps({"ok_tags": ok_tags, "stats": short, "diff_counts": summary["diff_counts"]}, indent=1), flush=True)
    for t in ok_tags:
        for u in stats[t]["unparsed"][:10]:
            print("UNPARSED %s %s:%d\n%s" % (t, u["path"], u["line"], u["source"]), flush=True)
    return 0 if ok_tags else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", required=True)
    b.add_argument("--tags", nargs="+", required=True)
    b.add_argument("--page-tag")
    s = sub.add_parser("scan")
    s.add_argument("root")
    s.add_argument("--tag", default="local")
    s.add_argument("--sha", default="")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        return build(a.out, a.tags, a.page_tag)
    recs, _ = scan_tree(a.root)
    for r in recs:
        r["tag"], r["sha"] = a.tag, a.sha
        print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
