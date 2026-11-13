# px4-arming-checks

A catalogue of PX4's health and arming check messages ("Preflight Fail: ...", "Arming denied", the items in
QGroundControl's arming check report), read straight from PX4's own source for each release.

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

```
python armcat.py build --out out --tags v1.14.4 v1.15.4 v1.16.2 v1.17.0 v1.18.0-rc1 main --page-tag v1.17.0
python armcat.py scan path/to/PX4-Autopilot          # JSON lines for a local checkout
python -m unittest -v test_armcat                    # synthetic tests, no network
```

Standard library only. `build` makes a shallow, sparse clone of PX4-Autopilot (`src/modules/commander` only)
for each tag.

**Status (13 Nov 2026):** version 0.1.0 passes its synthetic tests. The first build over PX4 v1.14.4 to
v1.18.0-rc1 is queued; the catalogue and the searchable page are published here once its output has been checked
(every report call parsed, counts checked against a plain text search).

**Limits.** The parser masks comments and strings and matches calls by name; it does not compile the code.
"Parameters near the check" come from a simple scan and may include parameters that do not control that message;
the parameters PX4 names in its own descriptions are listed separately. A message whose text is built at runtime
is shown as the source expression.

## Licence

Code: Apache-2.0. Generated catalogue text: CC BY 4.0. Source excerpts in the output are PX4-Autopilot code,
BSD-3-Clause, Copyright (c) PX4 Development Team.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name.
