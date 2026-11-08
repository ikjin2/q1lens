# Q1Lens

![Q1Lens demo](https://github.com/ikjin2/q1lens/releases/download/v0.2.7/q1lens-demo.gif)

## Install

Python 3.10+. Activate your project's Python environment, then install with either command.

pip:
```bash
python -m pip install q1lens
```

uv pip:
```bash
uv pip install q1lens
```

## VS Code

Install [Q1Lens from the VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=q1lens.q1lens).

## Python environment

Find your Python executable by running this in your project's Python session or notebook:

```python
import sys
print(sys.executable)
```

In the Command Palette, run **Q1Lens: Select Python Environment** → **Enter Python path…** and paste that path.

Run **Q1Lens: Show Python Environment** to confirm the selection.

## Timeline recovery

Invalid Q1ASM lines are excluded from the timeline, and the remaining instructions
retain their normal stop, branch, and loop behavior. Errors keep their original
source locations. A notice explains that timing and hardware state reflect the
remaining lines. This also applies to playback and simulation; runtime limits and
unavailable inputs can still stop execution. CLI analysis still returns a nonzero
status for source errors, and `--q1asm-strict` retains strict validation.
