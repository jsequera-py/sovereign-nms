# Working rules for Claude Code in this repo

Authoritative context: `HANDOFF.md` (state, findings, open questions),
`ROADMAP.md` (phases), `working-method.md` (how work flows).

## Never

- **Never run `git add`, `git commit`, `git push` or `git tag`.** Stop at
  `git diff`. Committing is a separate reviewed step run by Jesus. A scope
  check that runs after the commit cannot fail, which defeats its purpose.
- Never edit `sim/topology.yaml` to make discovery score better. It is the
  answer key, and editing it deletes the only honest measurement here.
- Never batch two changes whose effects cannot be verified separately.
- Never assert a result you did not observe. Quote the command output.

## Always

- One step, one change. A read-only inspection may accompany it.
- Report scope from git itself — `git status --short`, `git diff --stat` —
  never from your own summary of what you changed.
- `grep` here is aliased to ugrep: a pattern starting with `-` needs `--`
  before it.
- `psql ... -P pager=off`, always. The pager otherwise locks the terminal.
- After editing anything under `server/`, say so explicitly.
  `nms-api.service` holds those modules in memory, and a test run before the
  service restart proves nothing.
- After any change to `collector/` or `sim/`, the scorer must be re-run with
  `reset_data.sh` and a fresh poll — never against whatever is already in
  the database. Baseline: 88.9% recall / 100% precision.
