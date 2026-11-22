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

Install [Q1Lens from the VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=q1lens.q1lens) or [Open VSX](https://open-vsx.org/extension/q1lens/q1lens).

## Python environment

Find your Python executable by running this in your project's Python session or notebook:

```python
import sys
print(sys.executable)
```

In the Command Palette, run **Q1Lens: Select Python Environment** → **Enter Python path…** and paste that path.

Run **Q1Lens: Show Python Environment** to confirm the selection.

## Quick start

Open the [verification demo workspace](examples/verification-demo/verification.code-workspace) from your Q1Lens checkout. In that workspace, run **Q1Lens: Select Python Environment** and choose the interpreter where you installed `q1lens`.
Open `queue.q1asm`, run **Q1Lens: Verify Program**, then choose **Open counterexample timeline**. The queue stays supplied for `N=1..37`. At `N=38`, the timeline selects the first underflow at **1188 ns**.
