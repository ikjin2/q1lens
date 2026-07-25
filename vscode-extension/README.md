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

## Inspect standalone Q1ASM

1. Open a `.q1asm` file.
2. Run **Q1Lens: Open Timeline Preview**.
3. Use **Q1Lens: Select Q1ASM Files in Folder...** to choose a subset.

Q1Lens uses a nearby `q1timeline.yml` when one exists. Otherwise, it creates a
project under `.q1timeline/` and includes the sibling `.q1asm` files.

## Main commands

- **Q1Lens: Analyze and Open**
- **Q1Lens: Refresh**
- **Q1Lens: Open QBS IR**
- **Q1Lens: Open Rendered HTML**
- **Q1Lens: Open Q1ASM Folder**
- **Q1Lens: Open Q1ASM Timeline**
- **Q1Lens: Open Timeline Preview**

## Project status

Q1Lens is an early-stage independent project. It was conceived and originally
developed by [Ik Kyeong Jin](https://github.com/ikjin2) while employed by
[Qblox B.V.](https://qblox.com/). It is not an official Qblox product and is
not sponsored, endorsed, or maintained by Qblox B.V.

Q1Lens is licensed under the Apache License 2.0. OpenAI Codex was used as an
AI-assisted development tool; all released changes are reviewed and accepted
by the project maintainer.
