# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Report them privately
through GitHub instead:
[Security → Report a vulnerability](https://github.com/ijdio/atv-remote/security/advisories/new).

Include what you found, how to reproduce it, and what an attacker could do with it.
You'll get a reply as soon as the maintainer can. This is a hobby project, so
there's no fixed timeline.

## Supported versions

Only the latest commit on `main` is supported.

## What's in scope

The app runs a local HTTP server that can control your Apple TV, so the most
relevant issues are:

- Ways for a web page, another user, or another machine to send commands through
  the local server. It listens on `127.0.0.1` only, checks the `Host` header, and
  requires a custom header on every POST.
- Leaks of the pairing credentials stored in `~/.pyatv.conf`

Bugs in [pyatv](https://github.com/postlund/pyatv) itself should be reported to
that project.
