# Contributing to chaos

Thanks for looking at this. A few things up front, then how to actually get
a change in.

## Authorized use, no exceptions

This is a pentest tool — it can trigger real scans, credential
brute-forcing, and traffic capture against real hosts. Contributions that
weaken the authorized-use framing (removing the README warnings, defaulting
`CHAOS_PENTEST_TOKEN` behavior to something less safe, adding features whose
only real use is unauthorized access) will be closed without much
discussion. Contributions that make authorized use *safer or clearer*
(better scope controls, audit logging, clearer error messages) are very
welcome.

## What this project is

An Arduino App for the **Arduino UNO Q / Ventuno Q**. (Some comments/logs
still say "jessy" — that's just the reference deployment's hostname, not a
generic term; if you're running this on your own board, ignore it.) If you
don't have this exact board, you can still read/review code and improve
`pentest.py` and
`flipper_bridge.py` — both are standalone Python modules with no dependency
on the Arduino app framework, so you can run and test them directly:

```bash
cd python
python3 -c "import pentest; print(pentest.valid_target('192.168.1.0/24'))"
```

## Setting up

1. You need an Arduino UNO Q / Ventuno Q board with `arduino-app-cli`
   installed, or just work on the standalone modules above.
2. `arduino-app-cli app start ~/ArduinoApps/chaos` to run it for real.
3. `arduino-app-cli app logs ~/ArduinoApps/chaos --follow` to watch it.

## Good first contributions

- **Add a new pentest tool wrapper.** Look at any function in `pentest.py`
  (e.g. `nmap_scan`) as the template: validate input with `valid_target`/
  `valid_url`, build a fixed or constrained argv list (never pass raw user
  input as a flag), call `start_job(...)`. Wire it into `main.py`'s
  `on_pentest_run` dispatcher and add a `tool-card` to `assets/index.html`.
- **Improve the dashboard UI** in `assets/` — plain HTML/CSS/JS, no build
  step, no framework.
- **Add IPv6 support** to `pentest.valid_target`/`valid_url` — currently
  IPv4/hostname only, noted as a known gap.
- **Persist scan/job history** with the `dbstorage_sqlstore` Brick instead
  of the current in-memory lists.

## Code style

- No comments explaining *what* code does — name things clearly instead.
  Comments are for *why*, when it's non-obvious (see existing code for the
  tone).
- Every subprocess call goes through `pentest.start_job` — no ad hoc
  `subprocess.run` calls elsewhere, so job tracking/limits/output-capping
  stay consistent.
- Validate at the boundary (`valid_target`/`valid_url`), trust it everywhere
  after that.

## Pull requests

Small and focused beats large and sweeping. Explain the *why* in the PR
description — the diff usually explains the *what* on its own.
