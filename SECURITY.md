# Security

skillpantry installs other people's instructions into your AI agents, so a bad catalog entry is a
security problem, not just a bug.

## Report

- **A catalog entry is unsafe** (hidden instructions, data exfiltration, a script that does more than it
  says, a pinned commit that changed meaning): open a private report via
  **Security → Report a vulnerability** on GitHub, or an issue if it is already public.
- **A bug in skillpantry itself** that writes outside the target, leaks a secret into a config file, or
  runs code from a source repository: report it privately the same way.

Please include the entry id, the pinned commit and the file and line you are worried about.

## What happens next

The entry is set to `excluded` or pinned to a safe commit in a patch release, usually within a few days.
Users see the change on `pantryctl update`; `skillpantry status --all` shows installs that differ from
the catalog.

## What the audit is and is not

`skillpantry audit` is pattern matching that points a reviewer at lines worth reading. It is not a
sandbox and not a guarantee — the human review described in [CONTRIBUTING.md](CONTRIBUTING.md) is.
