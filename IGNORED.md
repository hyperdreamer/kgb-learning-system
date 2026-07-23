# IGNORED.md — Deferred Decisions

This file contains only intentional deferrals. Completed work belongs in Git history.

## Intentional behavior

### Projection failures do not block sentence work

`main_window.py` deliberately catches word/phrase projection link or sync failures while loading or saving sentence cards. A sentence database remains usable when its derived W/P projection fails. Failures are logged, but must not change this non-blocking contract.

### AI parser resilience for create actions

`parse_sense_assignment()` accepts a malformed or non-integer `sense_id` for AI `action=create` as no preferred sense. Reuse actions still validate referenced IDs. This remains intentional compatibility with imperfect model output.

### Toolbar guards tolerate construction order

Toolbar font styling uses guarded widget access because controls are created incrementally. A missing control skips only its own styling update; no runtime fault has been demonstrated.

## Audit environment limitations

### Optional PyQt WebEngine coverage

`tests/test_graphics.py:385` is skipped when `PyQt6-WebEngine` is not installed.
The application deliberately supports its proxy-safe Qt text renderer without
WebEngine, so this package is not a required dependency. Before releasing a
change to the WebEngine-specific rendering path, run this test in an environment
with `PyQt6-WebEngine` installed.

### Headless multimedia diagnostic

The headless test run emits two Qt FFmpeg messages stating that media could not
be opened. All related tests pass; this is an environment/media-fixture
diagnostic, not a failing application check. Validate real audio playback on a
workstation with the intended FFmpeg/media backend before releasing a TTS or
multimedia change.

### Chromium extension runtime validation

No Chromium-family executable is available in the current audit environment.
The extension's JavaScript syntax, Manifest V3 JSON, and static match-pattern
checks pass; before release, load `browser_extension/` unpacked in Chromium and
exercise both the default and a custom loopback endpoint.
