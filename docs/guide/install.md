# Install

You need Python 3.10 or newer and VS Code 1.90 or newer.

## 1. Install the Python package

Activate the Python environment your project uses, then install `q1lens` into it.

```bash
python -m pip install q1lens
```

With uv:

```bash
uv pip install q1lens
```

The package installs the Z3 solver, which program verification uses.

## 2. Install the VS Code extension

Install Q1Lens from the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=q1lens.q1lens) or from [Open VSX](https://open-vsx.org/extension/q1lens/q1lens).

## 3. Tell the extension which Python to use

The extension runs the Python package for every analysis, so it must use the environment where you installed `q1lens`.

1. Find the interpreter path. Run this in your project's Python session or notebook:

    ```python
    import sys
    print(sys.executable)
    ```

2. In the Command Palette, run **Q1Lens: Select Python Environment**.
3. Choose **Enter Python path…** and paste the path. The picker also offers a `.venv` or `venv` folder in the workspace, **Select Interpreter in VS Code…**, **Python on PATH** and **Browse for Python executable…**.

Q1Lens checks the environment after you choose. Run **Q1Lens: Show Python Environment** at any time to see the selected interpreter, the installed `q1lens` version and whether the environment is **Ready** or **Needs attention**. The same panel opens when you click the **Q1Lens** Python item in the status bar.

## How the interpreter is chosen

| You have | Q1Lens uses |
| --- | --- |
| Chosen an interpreter with **Select Python Environment**, or set `q1lens.pythonPath` or `q1lens.pythonArgs` yourself | That interpreter |
| Set nothing | The interpreter selected in the VS Code Python extension for that file |
| Set `q1lens.pythonEnvironment` to `vscode` | The VS Code selection, with your saved path kept for later |
| A Jupyter notebook | The notebook's kernel, always |

To launch through a wrapper, put the launcher in `q1lens.pythonPath` and its arguments in `q1lens.pythonArgs`. For example, `uv` with `["run", "python"]` runs `uv run python -m q1lens …`.

## Keep both versions the same

The Python package and the extension are released together with the same version number. When they differ, Q1Lens warns you once and names both versions. Update whichever is older.

If a check fails, see [Troubleshooting](../reference/troubleshooting.md).
