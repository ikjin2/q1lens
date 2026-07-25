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

### With uv

Add Q1Lens as a development dependency:

```console
uv add --dev q1lens
uv run q1lens --help
```

Install
[Q1Lens from the VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=q1lens.q1lens),
then add this to your VS Code settings:

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

Install the VS Code extension from the Marketplace. If `python` does not refer
to the correct environment, set `q1lens.pythonPath` to its interpreter:

```json
{
  "q1lens.pythonPath": ".venv/bin/python"
}
```

On Windows, use `.venv\\Scripts\\python.exe`.

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
uv run q1lens analyze --project examples/basic-transmon/qbstimeline.yml --out examples/basic-transmon/.qbs_timeline/qbs_ir.json

uv run q1lens render --ir examples/basic-transmon/.qbs_timeline/qbs_ir.json --out examples/basic-transmon/.qbs_timeline/index.html
```

When using pip, replace `uv run q1lens` with `python -m q1lens`.

## Standalone Q1ASM

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

Python:

```console
git clone https://github.com/ikjin2/q1lens.git
cd q1lens
uv sync --extra dev
uv run pytest -q
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

See [Migrating to Q1Lens](docs/migration-from-qbs-and-q1timeline.md) when
replacing the older separate QBS Timeline and q1timeline extensions.

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
