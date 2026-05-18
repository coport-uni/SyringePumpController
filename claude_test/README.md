# claude_test/ — Bench scripts and one-off diagnostics

This directory holds debug, exploratory, and diagnostic scripts per [CommonClaude/CLAUDE.md §3](https://github.com/coport-uni/CommonClaude/blob/main/CLAUDE.md). Scripts here are NOT part of CI/CD; production-quality tests live in [`tests/`](../tests/).

When adding a new file, append a row to the table below describing what it does and the key finding(s) that came out of running it.

| File | Purpose | Findings |
|---|---|---|
| [valve_toggle.py](valve_toggle.py) | Toggle the SY-01B valve between two distribution ports (default: 1 ↔ 3), verifying each move by polling `?6`. Runs `diagnose()` first; plunger never moves. | 2026-05-18 HIL on `/dev/ttyUSB1` (firmware 8.33): 20/20 moves verified, ~907 ms per port-to-port transition. Confirmed [LearnedPatterns.md](../LearnedPatterns.md) E5 (Q.busy unreliable post-init) and E6 (firmware treats MCC-4 as 4-way distribution; `?6` returns digits). |
