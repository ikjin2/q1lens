# Q1Lens development workspace

## Source of truth

Use `${HOME}/Documents/GitHub/q1lens` on branch `main` for product development,
tests, builds, and local installation. Resolve `${HOME}` to the current user's
home directory. This is the single development workspace designated by the user.

The `origin` remote is the public repository:
`https://github.com/ikjin2/q1lens.git`. Track and compare against `origin/main`.

Before changes, verify `git rev-parse --show-toplevel`, `git branch --show-current`,
and `git status --short`. Do not choose a different source copy based on its
folder name, package version, or the tool's starting directory.

## Development and release flow

Develop in this checkout. Do not merge the full ancestry of archived branches or
restore an older workspace over it. Port individual changes only when requested
and review them against the current `main`.

For a public release, build the exact verified development commit in a temporary
release worktree. Build the Python package and VS Code extension from that same
revision and verify the installed artifacts. Installing a local build does not
publish a release. Release versions are recorded by tags; release worktrees are
not separate development baselines and can be closed after required artifacts
and verification reports have been retained.

Publish only the intended branch and tags, never all local refs. Keep `.git` in
place; do not move or delete repository metadata as a way to change workspaces.

## Local material

Research manuscripts, experiment records, and equipment notes are archived
outside this checkout. They are reference material, not an alternative product
source. Do not copy them into product changes or include them in a release
without an explicit request.

Keep generated output, local installation artifacts, and dependency caches
untracked. Stage specific source files for a change rather than an entire folder.

## Accepted product baseline

Retain the current Hardware UI, show latched settings where RT instructions
apply them without duplicating setters in RT rows, and display Compare both
alternatives in separate Jump/Continue rows. Critical Path UI and the old separate
register-inspector badges/flags remain deferred.
