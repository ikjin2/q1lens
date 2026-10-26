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

## Start from a Jupyter notebook

1. Select your Python kernel in VS Code and run a cell to start it.
2. Run **Q1Lens: Open Q1Lens from This Cell** from the notebook.
3. If the environment needs attention, choose **Show Setup** to see the Python
   path and diagnosis: a missing Q1Lens package or an import failure.
4. Select **pip** or **uv pip**, then **Copy Command** or **Insert into Terminal**.
   The suggested command explicitly targets the notebook's Python and uses the
   extension's package version. Review or edit it and press Enter yourself.
5. Choose **Check Again** after installing. Q1Lens checks the original notebook's
   current kernel and continues the analysis when it is ready.

Q1Lens never runs the installer or bootstraps pip. If pip is missing, setup
explains this and offers uv pip; uv must already be available on your PATH.
You can also manage Q1Lens through your project's existing dependency workflow
and then check again. The suggested commands do not modify project dependency
or lock files.

Windows commands use PowerShell; Linux and macOS commands use POSIX sh syntax.
**Insert into Terminal** opens a new terminal using that shell and fills in the
command without executing it. If you copy a command, use the shell indicated
in the setup screen. A changed notebook kernel updates the setup before a
command can be copied or inserted.

Notebook analysis uses the selected kernel's Python automatically; no
`q1lens.pythonPath` setting is needed. **Refresh** checks the original notebook's
current kernel again, even if another editor is active. Keep that notebook open
with its kernel running.

This supports kernels on the same host as the VS Code extension, including a
shared Remote SSH/WSL host. A separate remote Jupyter server is not supported.
Analysis reruns the selected setup and schedule cells in a separate process;
it uses the kernel's installed packages, not its live variables.

## Python for standalone files

For Python schedule projects and standalone Q1ASM files, Q1Lens uses the
Microsoft Python extension's selected interpreter for the analysis target's
project. Select it with **Python: Select Interpreter**. This works on Windows,
Linux and macOS, including an extension running in a Remote SSH or WSL workspace.

Explicit Q1Lens Python paths and launcher arguments remain manual overrides,
including legacy settings and Q1ASM-specific settings. Notebook workflows
continue to use their selected kernel. **Follow VS Code Selection** in the
environment screen switches this project to VS Code without deleting saved
launchers. Set `q1lens.pythonEnvironment` back to `auto` to use those settings again.

The **Q1Lens Python** status item shows the environment and whether it came from
VS Code, a manual setting, or a notebook kernel. Click it, or run **Q1Lens: Show
Python Environment**, to see the executable, selection source and analysis target.
**Refresh** resolves that same target again; another active editor does not
redirect the environment. View changes and playback use the analyzed environment.

If no usable interpreter is selected, Q1Lens explains how to choose one. It
does not silently fall back to Python on PATH. Missing packages and import errors
use the same user-managed **Show Setup** / **Check Again** flow as notebooks.

## Install the Python package

With uv:

```console
uv add --dev q1lens
uv run q1lens --help
```

Select the project's virtual environment with **Python: Select Interpreter**.
Alternatively, explicitly configure the extension to launch through uv:

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

Select that environment with **Python: Select Interpreter**. A manual
`q1lens.pythonPath` is optional, for example an absolute path to `.venv/bin/python`
on macOS or Linux, or `.venv\\Scripts\\python.exe` on Windows.

Run **Q1Lens: Check Python Environment**, also available in the sidebar, to
inspect the selected paths and their sources, Python versions, package locations,
and Q1ASM bridge availability.
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
VS Code restarts. These controls require the matching Q1Lens 0.2.0 Python package.

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

Normal view shows RT lanes with underflow flags; Debug view also shows Q1 lanes.
Click an RT block to expand its latched parameters and the registers read by that
command. Highlights mark known value changes: registers are compared with the
previous RT command's issue snapshot, and parameters with the previous applied
state. Unknown values and first appearances are not highlighted as changes.
Loop controls and branch choices remain available; the sequencer's execution
assumptions menu exposes branch and feedback choices when Q1 lanes are hidden.

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
