# Q1Lens

See how a Qblox schedule becomes pulses and Q1ASM without leaving VS Code.

Q1Lens is a cross-layer debugger for `qblox-scheduler` workflows. It connects
three views of the same program:

1. scheduled operations such as `Reset`, `X`, and `Measure`;
2. pulses and acquisitions grouped by port and clock;
3. the Q1ASM program executed by each sequencer.

This makes it easier to answer questions such as:

- When does this operation run?
- Which pulse or acquisition did it produce?
- Which sequencer received it?
- Which Q1ASM instructions implement it?

Q1Lens can also inspect standalone `.q1asm` files when no schedule project is
available.

Created by [Ik Kyeong Jin](https://github.com/ikjin2).

## Requirements

- Python 3.10 or newer
- VS Code 1.90 or newer
- A Python environment containing the dependencies used by your schedule

Q1Lens imports and runs your schedule code during analysis. Install the Python
package in the same environment as the schedule project.

## Install

### From a VS Code notebook

Install the Q1Lens VS Code extension, select your notebook's Python kernel, and
run a cell. **Q1Lens: Open Q1Lens from This Cell** uses that kernel's environment
automatically; no Python path setting is needed. If the environment needs
attention, choose **Show Setup** to see the diagnosis and select **pip** or
**uv pip**. Copy the command or insert it into a terminal, review and run it
yourself, then choose **Check Again** to continue. The extension does not install
packages or bootstrap pip. Windows uses PowerShell commands; Linux and macOS
use POSIX shell commands, always targeting the notebook's Python explicitly.
**Refresh** follows the original notebook's current kernel. Keep the notebook
open with its kernel running.

This works when the kernel and VS Code extension run on the same host. Separate
remote Jupyter servers are not supported. Analysis reruns the selected cells in
a separate process; existing notebook variables are not shared.

### With uv

Add Q1Lens as a development dependency:

```console
uv add --dev q1lens
uv run q1lens --help
```

Install
[Q1Lens from the VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=q1lens.q1lens),
then choose the project's environment with **Python: Select Interpreter**.
To keep using uv as an explicit launcher, you can instead add:

```json
{
  "q1lens.pythonPath": "uv",
  "q1lens.pythonArgs": ["run", "python"]
}
```

The extension will then run Q1Lens through the current uv project environment.

### With pip

Activate the environment used by your schedule project, then install Q1Lens:

```console
python -m pip install q1lens
python -m q1lens --help
```

Install the VS Code extension from the Marketplace and choose that environment
with **Python: Select Interpreter**. General file analysis and Refresh follow
the VS Code selection for the original analysis target. Existing explicit
Q1Lens Python settings remain manual overrides, for example:

```json
{
  "q1lens.pythonPath": ".venv/bin/python"
}
```

On Windows, use `.venv\\Scripts\\python.exe`.

Click the **Q1Lens Python** status item or run **Q1Lens: Show Python Environment**
to see the interpreter, its selection source and analysis target. Windows and
Linux use the same selection rules. Missing or unusable selections are explained
instead of silently falling back to a different Python.
**Follow VS Code Selection** switches this project to automatic selection while
keeping old Q1Lens launcher settings saved; `q1lens.pythonEnvironment: "auto"`
restores the normal manual-override precedence.

Run **Q1Lens: Check Python Environment** to inspect the selected Python,
package locations, and Q1ASM bridge. **Q1Lens: Select Python Environment**
selects a workspace virtual environment or interpreter for both timeline views.
Explicit Q1ASM Python settings take precedence over shared settings.

## Quick start

Create `qbstimeline.yml` in your schedule project:

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

Expose two functions from `schedule.py`:

```python
def build_schedule():
    ...


def build_compiler():
    ...
```

The compiler returned by `build_compiler()` must provide
`compile(schedule)`. Its result must expose `compiled_instructions` when Q1ASM
inspection is required.

Then:

1. Open the project folder in VS Code.
2. Open `qbstimeline.yml`.
3. Run **Q1Lens: Analyze and Open** from the Command Palette.
4. Select an operation to inspect its pulse and Q1ASM context.

Q1Lens writes generated files under `.qbs_timeline/`:

```text
.qbs_timeline/
|-- qbs_ir.json
|-- index.html
|-- q1asm/
|   `-- <sequencer>.q1asm
`-- q1timeline.yml
```

The same flow is available from the command line:

```console
uv run q1lens build --project examples/basic-transmon/qbstimeline.yml
```

`build` writes the IR, rendered HTML, and Q1ASM under the configured output
directory. Use `--out-dir` to override it relative to the project file, or
`--open` to open the HTML. The individual stages are also available:

```console
uv run q1lens analyze --project examples/basic-transmon/qbstimeline.yml --out examples/basic-transmon/.qbs_timeline/qbs_ir.json

uv run q1lens render --ir examples/basic-transmon/.qbs_timeline/qbs_ir.json --out examples/basic-transmon/.qbs_timeline/index.html
```

When using pip, replace `uv run q1lens` with `python -m q1lens`.

For automated checks, `analyze --strict` returns exit code 1 for warnings or
IR diagnostics. `diagnose --strict` returns 1 for findings or provenance
coverage gaps. Requested artifacts are still written; usage and runtime
failures return 2. Missing provenance is reported separately from scheduler
bug candidates.

## Standalone Q1ASM

Use **Q1Lens: Set Analysis Parameter...** or the timeline's **Parameters**
panel to try a temporary JSON parameter value. Edit, Apply, Cancel, and Reset
keep the project files unchanged. Overrides are scoped to the current project
and session; **Q1Lens: Clear Analysis Parameter Overrides** restores file values.
The CLI equivalent is `q1timeline analyze --project q1timeline.yml --set-param NAME=123`;
repeat `--set-param` for multiple parameters.

You can inspect Q1ASM without a schedule project:

1. Open a `.q1asm` file.
2. Run **Q1Lens: Open Timeline Preview**.
3. Use **Q1Lens: Select Q1ASM Files in Folder...** if only some sibling files
   should be included.

If Q1Lens finds a nearby `q1timeline.yml`, it uses that project. Otherwise, it
creates `.q1timeline/auto-generated.q1timeline.yml` next to the selected file
and includes the sibling `.q1asm` files in that folder.

Try the included example at
`examples/q1asm-standalone-demo/drive.q1asm`.

## Symbolic values and Q1ASM mapping

Use annotations when a scheduler value should retain its human-readable name:

```python
from q1lens import annotate, sym

T_TOTAL = sym.time("T_TOTAL", 40e-9)
AMP_X = sym.amp("AMP_X", 0.32)

sched.add(
    annotate(
        SquarePulse(
            duration=40e-9,
            amp=0.32,
            port="q0:mw",
            clock="q0.01",
        ),
        duration=T_TOTAL,
        amp=AMP_X,
    ),
    label="x180",
)
```

Exact schedule-to-Q1ASM line mapping uses compiler-provided
`qbstimeline_provenance`. When that data is absent, Q1Lens can infer mappings
for simple, unique `play` and `acquire` instructions. Ambiguous or optimized
lowerings remain visible without claiming an exact source mapping.

Optional native diagrams are disabled by default. Enable them in
`qbstimeline.yml` when the schedule supports the corresponding plotting
methods:

```yaml
artifacts:
  circuit_diagram: true
  analog_pulse_diagram: true
```

## Development

### Review and execution workflows

The following workflows are available in the Q1Lens 0.2.4 Python package and
VS Code extension. The Desktop workbench is a development app run from the
source checkout; this release does not include a Desktop installer.

Run a read-only project preflight before executing a schedule:

```console
q1lens doctor --project qbstimeline.yml
q1lens doctor --project qbstimeline.yml --json
```

Doctor checks configuration, source entrypoints or notebook prerequisites, and
the configured output location without importing or executing project code.
It returns `0` when the required checks pass, `1` for failed checks, and `2` for
usage or runtime errors.

To collect an existing diagnosis, pass `--bundle diagnostics.zip` to
`q1lens diagnose`, then run `q1lens verify-bundle --bundle diagnostics.zip`.
Bundles can include source files, IR, generated Q1ASM and environment details.
Inspect the contents before sharing. Manifest hashes detect corruption;
they do not authenticate the origin of a bundle.

In the timeline, **Save Comparison Baseline** retains a reference across analyses.
Changed blocks show before/after values, and the comparison status identifies
the active baseline. Provenance coverage buttons locate Compiler, Inferred and
Unmapped blocks, with their mapping evidence and Q1ASM preview. Available Q1ASM
Quick Fixes check that the saved source still matches the diagnostic.

For bounded register, loop and branch execution, configure a Q1ASM project:

```yaml
analysis:
  execution_mode: concrete
  max_steps: 10000
```

Use `--execution-mode concrete --max-steps 10000` to override those values on the
CLI. In VS Code, `q1lens.q1timeline.executionMode: project` follows the project
configuration; explicit execution modes use `q1lens.q1timeline.maxSteps`.
Concrete mode executes sequencers independently. Missing inputs produce
`blocked` and long-running programs produce `step_limit`, retaining a partial trace.

The Q1ASM timeline's Play button calculates one path for up to five seconds.
Pause retains the session, and Play resumes it. Unknown conditional branches
default to Continue; their controls identify this assumption. After a run, choose
Jump or Continue on a branch occurrence to recalculate with that choice. Executed
loops can be expanded to inspect every recorded iteration; collapsing them also
compresses the shared time axis. New playback results start with loops and long
waits folded. Selecting source instructions highlights their visible loop or
representative without unfolding it; expand explicitly to inspect hidden
iterations. Manual expansion survives view-mode changes.

Long waits in playback use display-only time compression, including when an
acquisition's end or feedback value is unknown. The duration labels and raw times
stay unchanged, and the ruler, cursor and lanes share the same compressed axis.
A compressed wait does not assert that the hardware is idle.

Playback assumes each `fb_pop_data` receipt is already available (additional wait
`0 ns`) with an unknown value. Click its Q1 block's `≈` marker to set a value and
additional wait for that occurrence, then Apply to recalculate. An empty value
remains unknown; `0` is an explicit assumed value. Earlier choices are retained,
and later choices are cleared when a receipt or branch changes. Assumed values
and their downstream effects stay marked as assumptions. Waiting delays Q1 and
subsequent packet issue times; it does not move already queued RT work.

This path uses analytical timing, with queue checks under the receipt assumptions;
it does not simulate shared Q1/RT clocks or feed queue backpressure into register
execution. Other unavailable external timing and the recorded-event limit retain
a partial trace. Source edits or Refresh reset the session. Project execution-mode
settings remain unchanged.

`set_cond 0,...` disables conditional execution and can pass through Play and
concrete execution. Active or unresolved `set_cond` needs trigger-counter inputs
at each RT instruction, so Play retains a partial trace there. Static analysis
marks conditional RT timing and affected outputs unknown rather than treating
them as unconditional execution; turning the condition off does not recover an
already unknown elapsed time.

Use `execution_mode: simulation` for shared Q1/RT timing, finite queues,
synchronization and configured feedback inputs. Optional numerical signal models
calculate waveform stages and acquisition results. The Hardware view exposes
register history, pending/applied parameters, queues, signals, bins and recorded
execution replay. See the [simulation reference](docs/simulation.md),
[concrete register example](examples/concrete-register-execution/README.md), and
[simulation examples](examples/simulation-verification/README.md).

TimelineIR includes a `critical_path` result. Add `--hardware-readiness` when
analyzing Q1ASM to include conservative hardware-readiness checks as well.
Incomplete or invalid inputs remain explicit. A completed execution or exact
critical path describes the selected analysis model, not a hardware measurement.

The [desktop workbench](desktop-app/README.md) provides a local Electron/Pixi
timeline using the same Python analysis. For reproducible checks, see
[continuous validation](docs/ci-validation.md) and the
[cursor-frequency verification guide](examples/cursor-frequency-demo/verification/README.md).

### Build and test

Python:

```console
git clone https://github.com/ikjin2/q1lens.git
cd q1lens
uv sync --extra dev
uv run pytest -q tests
uv build
```

VS Code extension:

```console
cd vscode-extension
npm ci
npm test
npm run test:extension
npm run package
```

## Project status and authorship

Q1Lens is an early-stage project. It was conceived and originally developed by
[Ik Kyeong Jin](https://github.com/ikjin2) as an independent side project while
employed by [Qblox B.V.](https://qblox.com/).

Q1Lens is independently owned and maintained by Ik Kyeong Jin. It is not an
official Qblox product and is not sponsored, endorsed, or maintained by
Qblox B.V.

OpenAI Codex was used as an AI-assisted development tool for implementation,
testing, review, and documentation. All technical decisions and released
changes are reviewed and accepted by the project maintainer.

## License

Q1Lens is licensed under the [Apache License 2.0](LICENSE). See
[NOTICE](NOTICE) for project attribution and history.
