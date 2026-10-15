# Q1Lens development workspace

## Source of truth

The user designated the integrated source as the development baseline on 2026-10-15.
Use it for subsequent product development, tests, builds, and local installation.

These are existing local Git worktrees sharing one repository, not independent
source copies to keep synchronized manually:

| Role | Local path | Branch / recorded revision |
| --- | --- | --- |
| Canonical development | `${HOME}/.codex/worktrees/latch-register-integration/q1lens` | `codex/latch-register-integration`; integration baseline `b64f1ab5d2b83c99e8ac308049b8db9f14e6030b` |
| Released 0.2.1 source | `${HOME}/.codex/worktrees/timeline-navigation-20261014/q1lens` | `agent/timeline-navigation-20261014`; `6a0492f5ba3a76c9408039e7ab4711b552eeaf0a` |
| Preserved previous workspace | `${HOME}/Documents/GitHub/q1lens` | `agent/cli-diagnostics-workflow`; previous work and local research/equipment material |

These paths describe this Windows machine. The integration baseline is historical;
development HEAD advances with later commits. Do not reset to that baseline.
The older `.worktrees/public-release` checkout is not the 0.2.1 release source.
Do not infer the newest source from a folder name, package version, or tool cwd.

## Before making changes

- Verify the target checkout with `git rev-parse --show-toplevel`,
  `git branch --show-current`, and `git status --short`.
- If a tool starts in the preserved or release checkout, use the canonical
  development path explicitly for product work, unless the user requests work
  on a specific checkout. Read that checkout's applicable instructions as well.
- Preserve the previous workspace's tracked changes, untracked files, research,
  equipment data, and generated output. Do not reset, clean, mass-stage, or copy
  its contents wholesale into the integrated source.
- The previous workspace also holds the shared Git repository in `.git`.
  Do not move or delete that workspace or its `.git`; the linked worktrees depend
  on it.
- Keep this canonical worktree available; it is the active development workspace.

## Development and release flow

Develop and verify in the canonical workspace. Build both the VS Code extension
and Python package from the same source revision when installing an updated
integration, and verify that the installed artifacts match that source.
Installing a local build does not publish a release.
As of 2026-10-15, the installed extension and Python package were verified against
the integrated baseline, even though their version remains 0.2.1.

For a requested public release, prepare the exact verified development commit in
a separate release worktree and build from that commit. Keep this guide's release
mapping current when the release checkout or published revision changes.

The accepted integration retains the current Hardware UI, removes duplicated
latched setters from RT rows, and displays Compare both alternatives in separate
Jump/Continue rows. Critical Path UI and the old separate register-inspector
badges/flags remain deferred. Local research and equipment material remain
separate from product changes; do not silently include them in a release.
