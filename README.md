# px4-arming-checks

A catalogue of PX4's health and arming check messages ("Preflight Fail: ...", "Arming denied", the items in
QGroundControl's arming check report), read straight from PX4's own source for each release, and a list of what
changed between releases.

Since v1.14, PX4 reports why a vehicle will not arm through calls such as `reporter.armingCheckFailure(...)` in
`src/modules/commander/HealthAndArmingChecks`. Each call carries an event ID, a log level, the flight modes it
blocks and a short message. Many also have an `EVENT` comment whose description names the parameters that
control the check, and many print a "Preflight Fail: ..." line to the MAVLink console. PX4's preflight-check
documentation lists only a handful of these messages and points to QGroundControl's in-app report. There is no
public, searchable list that ties each message to its parameters and its source line, or that shows what changed
between releases.

`armcat.py` builds that list:

- one record per report call: message, event ID, arming check / health failure / health warning, log level,
  health component, flight modes, PX4's own description, the parameters PX4 names in it, parameters used in the
  code just before the call, the console text printed with it, and the exact source lines with a link to them at
  the release's commit;
- the same for `events::send` calls and `mavlink_log_*` console texts in `src/modules/commander`;
- which releases contain each message, and what was added, removed or reworded between releases.

`changes.py` turns a build into release-to-release pages (`changes.md`, `changes.html`, `changes.json`): for each
pair of consecutive releases, every report that was added, removed or reworded, with its event ID, check file,
message text and source link. `crosscheck.py` compares a build with a plain `git grep` of each checkout and prints
every difference, plus five records with their source lines for spot checks.

`pages.py` turns a build into one static page per message (`m/<event-id>.html`), so a search for the exact text
QGroundControl shows can land on it. Each page has the message, the console text PX4 prints with it, PX4's own
description, the parameters, every release with its message text and source link (or "not present"), the source
lines in the newest release that has it, and links to the other messages from the same check. It also writes
`messages.html` (every message by check file), `sitemap.xml` and `pages.json`, and adds one link to
`messages.html` to an existing `index.html`.

```
python armcat.py build --out out --tags v1.14.4 v1.15.4 v1.16.2 v1.17.0 v1.18.0-rc1 main --page-tag v1.17.0
python crosscheck.py out                             # uses the checkouts build left in /tmp/armcat
python changes.py out                                # writes changes.md / changes.html / changes.json
python pages.py out --dest site                      # one page per message + messages.html + sitemap.xml
python armcat.py scan path/to/PX4-Autopilot          # JSON lines for a local checkout
python -m unittest -v test_armcat test_changes test_pages   # synthetic tests, no network
```

Standard library only. `build` makes a shallow, sparse clone of PX4-Autopilot (`src/modules/commander` only)
for each tag.

**Status (14 Nov 2026):** armcat.py 0.1.0, changes.py 0.1.0 and pages.py 0.1.0 pass their synthetic tests. The
first build over PX4 v1.14.4 to v1.18.0-rc1 and main is queued; the catalogue, the change list and the per-message
pages are published here once the output has been checked (every report call parsed, every `git grep` hit
explained).

**Limits.** The parser masks comments and strings and matches calls by name; it does not compile the code.
"Parameters near the check" come from a simple scan and may include parameters that do not control that message;
the parameters PX4 names in its own descriptions are listed separately. A message whose text is built at runtime
is shown as the source expression. Reports are matched across releases by event ID, so a renamed event ID shows
as one removal and one addition.

## Licence

Code: Apache-2.0. Generated catalogue text and pages: CC BY 4.0. Source excerpts in the output are
PX4-Autopilot code, BSD-3-Clause, Copyright (c) PX4 Development Team.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
