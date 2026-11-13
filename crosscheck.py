"""crosscheck.py OUT_DIR [WORKDIR]: check an armcat.py build against a plain `git grep` of each PX4 checkout.

For every tag in OUT_DIR/summary.json it greps the checkout armcat.py left in WORKDIR (default /tmp/armcat) for
armingCheckFailure / healthFailure / healthWarning under HealthAndArmingChecks, and prints every grep hit that
is not the line of a parsed report record (declarations, comments, strings, or calls the parser missed), so each
difference can be explained by eye. It then prints 5 evenly spaced report records of the page tag, with their
source lines, for spot checks. Writes OUT_DIR/crosscheck.json.

By MV2 of Munim, Inc.; made with AI agents. Apache-2.0.
"""

import json
import os
import re
import subprocess
import sys

CHECKS_DIR = "src/modules/commander/HealthAndArmingChecks"
PATTERN = "(armingCheckFailure|healthFailure|healthWarning)"
REPORT_KINDS = ("arming_check", "health_failure", "health_warning")


def safe(tag):
    return re.sub(r"[^\w.-]", "_", tag)


def grep(checkout):
    p = subprocess.run(["git", "-C", checkout, "grep", "-n", "-E", PATTERN, "--", CHECKS_DIR],
                       capture_output=True, text=True)
    hits = []
    for ln in p.stdout.splitlines():
        parts = ln.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            hits.append((parts[0], int(parts[1]), parts[2]))
    return p.returncode, hits


def main(out_dir, workdir="/tmp/armcat"):
    with open(os.path.join(out_dir, "summary.json"), encoding="utf-8") as f:
        summary = json.load(f)
    result = {}
    for tag in summary.get("ok_tags") or []:
        with open(os.path.join(out_dir, "catalog-%s.json" % safe(tag)), encoding="utf-8") as f:
            recs = json.load(f)["records"]
        rep = [r for r in recs if r["kind"] in REPORT_KINDS and r["path"].startswith(CHECKS_DIR + "/")]
        at = {(r["path"], r["line"]) for r in rep}
        code, hits = grep(os.path.join(workdir, safe(tag)))
        unmatched = [h for h in hits if (h[0], h[1]) not in at]
        hit_lines = {(h[0], h[1]) for h in hits}
        missing = sorted(x for x in at if x not in hit_lines)
        result[tag] = {"grep_exit": code, "grep_hits": len(hits), "reports_in_checks_dir": len(rep),
                       "report_lines": len(at), "hits_not_report_lines": len(unmatched),
                       "report_lines_without_hit": len(missing),
                       "unmatched": [{"path": h[0], "line": h[1], "text": h[2].strip()} for h in unmatched]}
        print("TAG %s grep_exit=%s grep_hits=%d reports_in_checks_dir=%d report_lines=%d hits_not_report_lines=%d "
              "report_lines_without_hit=%d" % (tag, code, len(hits), len(rep), len(at), len(unmatched), len(missing)),
              flush=True)
        for h in unmatched[:80]:
            print("  NOT-A-REPORT %s:%d: %s" % (h[0], h[1], h[2].strip()), flush=True)
        for x in missing[:20]:
            print("  REPORT-WITHOUT-HIT %s:%d" % x, flush=True)
    page = summary.get("page_tag")
    if page:
        with open(os.path.join(out_dir, "catalog-%s.json" % safe(page)), encoding="utf-8") as f:
            recs = json.load(f)["records"]
        rep = [r for r in recs if r["kind"] in REPORT_KINDS]
        picks = [rep[(i * len(rep)) // 5] for i in range(5)] if len(rep) >= 5 else rep
        for r in picks:
            print("SPOT %s %s:%d" % (page, r["path"], r["line"]), flush=True)
            print(json.dumps({k: r.get(k) for k in ("kind", "event_id", "log", "component", "modes", "message",
                                                    "params_doc", "params_near", "console", "url")},
                             ensure_ascii=False), flush=True)
            print(r.get("source") or "", flush=True)
            print("END-SPOT", flush=True)
        result["spot"] = [{"path": r["path"], "line": r["line"], "event_id": r.get("event_id")} for r in picks]
    with open(os.path.join(out_dir, "crosscheck.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
