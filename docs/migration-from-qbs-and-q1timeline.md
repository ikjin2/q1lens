# Migrating To Q1Lens

Q1Lens replaces the separate QBS Timeline and q1timeline VSCode
extensions for active development.

## Install

1. Build or download `q1lens-0.2.0.vsix`.
2. Disable or uninstall `q1timeline.q1asm-live-timeline-debugger`.
3. Install `q1lens-0.2.0.vsix`.
4. Reload VSCode.

## Commands

Legacy command IDs remain available:

- `qbsTimeline.analyzeAndOpen`
- `qbsTimeline.refresh`
- `qbsTimeline.openQ1Timeline`
- `q1timeline.openPreview`
- `q1timeline.refreshPreview`

New command titles are grouped under `Q1Lens`.

## Python CLIs

The VSCode extension uses the Q1Lens Python CLI while preserving the existing aliases:

- `python -m q1lens`
- `python -m qbstimeline`
- `q1timeline`
