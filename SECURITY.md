# Security Policy

## What this software is

This repository holds laboratory instrument control software. It runs as a
desktop application on a measurement PC and drives sources, magnets, lock-in
amplifiers, cameras and a cryostat over GPIB, serial and a local network. It
does not listen for network connections, has no user accounts, and stores no
credentials.

That shapes what a security problem looks like here. The realistic risk is
physical rather than informational: a fault that drives an instrument outside
its safe limits can damage a sample, an instrument or the magnet. Faults of
that kind are treated with the same seriousness as a conventional
vulnerability, and are the most useful thing to report.

Examples of what is in scope:

- A path that lets a source exceed its compliance, current or field limit, or
  that leaves an output energised after a stop, an error or a window close.
- A failure that is reported as success, so an unsafe condition goes unnoticed.
- Loss or silent corruption of measurement data during acquisition or export.
- Anything that executes untrusted input, such as a file the program reads or
  a reply from an instrument.

Out of scope: the frozen release packages under `Other-files/`, which are
archives of past builds rather than maintained code; vendor drivers and
runtimes, which belong to their suppliers; and anything requiring physical
access to the rack, which already implies control of the instruments.

## Supported versions

Only the current `main` branch is maintained. Fixes land there and are not
backported.

| Version | Supported |
| --- | --- |
| `main` | Yes |
| V3 Windows build package | No, archived build |
| V1 frozen baseline | No, archived build |

## Reporting

Please report privately rather than opening a public issue, so a fix can be in
place before the problem is described publicly.

Use GitHub's private vulnerability reporting, under the repository's Security
tab, or write to hengniu@nus.edu.sg with `SECURITY` in the subject.

Helpful to include: what the program did, what it should have done, which page
and instruments were involved, and the version or commit. A test that
reproduces the fault without hardware attached is the most useful thing you
can send, since the test suites run entirely offline.

## What to expect

This is a research project with a single maintainer, so responses are best
effort rather than a guaranteed timeline. Expect an acknowledgement within a
week or so. If a report is accepted, you will hear when a fix lands, and you
will be credited unless you prefer otherwise. If it is declined, you will be
told why.

Anything affecting instrument or sample safety is prioritised over everything
else, and will be described in `Other-files/README.md` when it is fixed so
that anyone running the software knows to update.
