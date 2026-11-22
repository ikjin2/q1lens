# Write Q1ASM

Q1Lens treats files ending in `.q1asm` as Q1ASM and helps while you type.

## Highlighting

The editor colors comments (`#` or `//`), labels (`name:` and `@name`), `.DEF` directives, registers (`R0`–`R63`), numbers, `$alias` references and `{PLACEHOLDER}` parameters. Instructions are colored by family: Q1 arithmetic, flow control, latched settings, real-time, synchronization and feedback.

Toggle a line comment with the usual VS Code shortcut. It inserts `#`.

## Hover

Hover an instruction to see its signature, for example `play wave0, wave1, duration`. Hover an operand to see how Q1Lens reads it: immediate, register, label, alias or parameter.

## Diagnostics

Q1Lens checks the whole source before it executes anything, then reports what execution finds. Problems appear as underlines in the editor and in the **Problems** panel. They include:

| Kind | Examples |
| --- | --- |
| Source errors | Syntax error, unknown instruction, wrong number or type of arguments, value out of range, undefined label, unresolved symbol |
| Timing | RT underflow (error) and possible RT underflow (warning) |
| Feedback | A receive before the estimated arrival, unbalanced sends and receives |
| Hints | Unused alias, unreachable code, a register overwritten before it is read, settings never applied before `stop` |

A confirmed underflow is an error. A possible one is a warning. A valid timeline still opens when other lines have source errors, with the affected commands marked.

Some diagnostics offer a **Quick Fix** (the light bulb, or `Ctrl+.`). Fixes apply to saved files.

### When diagnostics refresh

By default Q1Lens analyzes when you save. Change this with `q1lens.q1timeline.updateMode`:

| Value | Behavior |
| --- | --- |
| `onSave` | Analyze when you save (default) |
| `onType` | Analyze as you type, after `q1lens.q1timeline.debounceMs` (default 400 ms) |
| `manual` | Analyze only when you run **Q1Lens: Refresh Timeline Preview** |

## Verification results in the source

Turn on automatic verification with **Z3 Auto: Off** in the status bar. On each save, a result appears above every numeric `.DEF` and every `@assert` comment:

- **✓ Proved**: the check holds for the whole stated range.
- **✗ Counterexample**: Q1Lens found and replayed a failing input. Click it to open that run on the timeline.
- **? Unknown**: the model or the budget could not settle it. Click it to run verification again.

These results are CodeLens items. If you have turned CodeLens off for Q1ASM, turn it on to see them. [Verify a program](verification.md) explains how to write the checks.

## Editor commands

Right-click in a Q1ASM editor for these commands:

| Command | What it does |
| --- | --- |
| **Q1Lens: Open Timeline Preview** | Opens the timeline for this file |
| **Q1Lens: Reveal Current Line in Timeline** | Selects the timeline event for the line under the cursor |
| **Q1Lens: View Selection in Timeline** | Zooms the timeline to the selected lines |
| **Q1Lens: Select Q1ASM Files in Folder...** | Chooses which files in the folder belong on the timeline |
| **Q1Lens: Verify Program** | Verifies this file |
| **Q1Lens: Compare RT Traces Across Inputs** | Compares this file with another version |
