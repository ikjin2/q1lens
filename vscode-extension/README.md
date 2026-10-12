# Q1Lens

See how a Qblox schedule becomes pulses and Q1ASM without leaving VS Code.

Q1Lens connects three views of the same program:

1. scheduled operations;
2. pulses and acquisitions grouped by port and clock;
3. Q1ASM programs grouped by sequencer.

Select an operation to keep its schedule, pulse, and Q1ASM context together.
You can also open standalone `.q1asm` files without a schedule project.

Created by [Ik Kyeong Jin](https://github.com/ikjin2).

## Requirements

- Python 3.10 or newer
- The Q1Lens Python package installed in the same environment as your schedule

Q1Lens imports and runs schedule code during analysis, so a separate
Q1Lens-only environment will not see your project dependencies.

## Install the Python package

With uv:

```console
uv add --dev q1lens
uv run q1lens --help
```

Configure the extension to use uv:

```json
{
  "q1lens.pythonPath": "uv",
  "q1lens.pythonArgs": ["run", "python"]
}
```

Or install it in an activated virtual environment with pip:

```console
python -m pip install q1lens
python -m q1lens --help
```

If `python` does not point to that environment, set `q1lens.pythonPath` to the
interpreter, for example `.venv/bin/python` on macOS or Linux, or
`.venv\\Scripts\\python.exe` on Windows.

Run **Q1Lens: Check Python Environment**, also available in the sidebar, to
inspect Python versions, package locations, and Q1ASM bridge availability.
**Q1Lens: Select Python Environment** lets you select a workspace `.venv`/`venv`,
browse for an interpreter, or enter its path. Use the environment that runs
your schedule. Selecting an existing configured command preserves its launcher
arguments; browsing or entering a path resets them.

Selection saves shared and Q1ASM Python settings in the workspace, or in user
settings when no folder is open. Explicit Q1ASM overrides take precedence over
shared settings. The check imports Q1Lens without executing a schedule; it
checks schedule dependencies when you analyze the project. A failed timeline
offers an environment check and a retry.

## Use a schedule project

Create `qbstimeline.yml`:

```yaml
schedule:
  file: schedule.py
  entrypoint: build_schedule
  compiler: build_compiler

outputs:
  dir: .qbs_timeline

low_level:
  q1timeline: true
```

Your `schedule.py` must expose `build_schedule()` and `build_compiler()`. Then:

1. Open the project folder in VS Code.
2. Open `qbstimeline.yml`.
3. Run **Q1Lens: Analyze and Open**.
4. Select an operation to inspect its pulse and Q1ASM context.

Generated analysis files are kept in `.qbs_timeline/`.

**Refresh** reanalyzes the currently displayed project and preserves its view.
Changing the automatic-refresh setting takes effect without restarting VS Code.
Opening analysis artifacts before a first analysis offers **Analyze and Open**.

## Inspect standalone Q1ASM

1. Open a `.q1asm` file.
2. Run **Q1Lens: Open Timeline Preview**.
3. Use **Q1Lens: Select Q1ASM Files in Folder...** to choose a subset.

Q1Lens uses a nearby `q1timeline.yml` when one exists. Otherwise, it creates a
project under `.q1timeline/` and includes the sibling `.q1asm` files.

The collapsible **Parameters** panel shows the effective analysis values and
temporary override count. Use Edit and Apply (or Enter) to try a JSON value,
Cancel to discard input, and Reset to restore an individual file value.
Invalid input or failed analysis keeps the previous result and shows the error.

**Q1Lens: Set Analysis Parameter...** and **Q1Lens: Clear Analysis Parameter
Overrides** provide the same session-only workflow from the Command Palette.
Overrides do not modify project files and reset when the project changes or
VS Code restarts. These controls require the Python CLI from the same checkout,
including its `--set-param` support.

## Compare runs and inspect execution

**Q1Lens: Save Timeline Comparison Baseline** stores the current successful analysis in
`.q1timeline/comparison-baseline.json`. Later runs show added, changed, shifted,
and removed events against that baseline, with before/after details in the
inspector. The comparison badge identifies the active baseline. Use **Q1Lens:
Clear Timeline Comparison Baseline** to return to comparison with the previous run.

The Hardware view follows the cursor through register writes, simulated signal
stages, acquisitions, and feedback. Execution replay can prepare a bounded
concrete or simulated trace. These views show the declared software model;
they do not represent measurements from connected hardware.

`q1lens.q1timeline.executionMode` defaults to `project`, which respects the
project configuration. Choose `analysis`, `concrete`, or `simulation` to override
it, and set `q1lens.q1timeline.maxSteps` to bound that override.

In the schedule view, the Provenance Coverage legend selects matching pulses
and exposes Compiler, Inferred, or Unmapped evidence with linked Q1ASM context.

## Diagnostic quick fixes

Set `q1lens.q1timeline.hardwareReadiness` to `true` to enable conservative
end-state, scope/acquisition, and concrete acquisition-bin checks. Validated
analyzer suggestions appear as Quick Fixes on file-backed `.q1asm` documents.
Fixes are offered only for the matching saved instruction; dirty documents,
stale instructions, and invalid or cross-file edits are ignored. Applying a
Quick Fix remains an explicit user action.

## Main commands

- **Q1Lens: Check Python Environment**
- **Q1Lens: Select Python Environment**
- **Q1Lens: Analyze and Open**
- **Q1Lens: Refresh**
- **Q1Lens: Open QBS IR**
- **Q1Lens: Open Rendered HTML**
- **Q1Lens: Open Q1ASM Folder**
- **Q1Lens: Open Q1ASM Timeline**
- **Q1Lens: Open Timeline Preview**
- **Q1Lens: Save Timeline Comparison Baseline**
- **Q1Lens: Clear Timeline Comparison Baseline**
- **Q1Lens: Set Analysis Parameter...**
- **Q1Lens: Clear Analysis Parameter Overrides**

## Project status

Q1Lens is an early-stage independent project. It was conceived and originally
developed by [Ik Kyeong Jin](https://github.com/ikjin2) while employed by
[Qblox B.V.](https://qblox.com/). It is not an official Qblox product and is
not sponsored, endorsed, or maintained by Qblox B.V.

Q1Lens is licensed under the Apache License 2.0. OpenAI Codex was used as an
AI-assisted development tool; all released changes are reviewed and accepted
by the project maintainer.
