# Qblox Scheduler and notebooks

If you write experiments as Qblox Scheduler schedules, you do not write Q1ASM yourself: the compiler generates it. Q1Lens can compile your schedule, extract the generated Q1ASM for each sequencer and open it on the timeline.

Q1Lens runs your code in the Python environment you selected. That environment needs `q1lens` and your scheduler packages.

## From a notebook

1. Open a saved notebook, select its Python kernel and run a cell once.
2. On the cell that builds your schedule, choose **Open Q1Lens from This Cell** in the cell toolbar.
3. Confirm the name of the schedule variable and of the compiler variable. Q1Lens suggests names it finds in your cells, such as `schedule` and `compiler`.

Q1Lens then compiles the schedule and opens the result.

### Mark the cells that matter

Q1Lens does not read variables from the running kernel. It runs the needed cells again in a separate process, so the result does not depend on the order in which you ran cells by hand. Tell it which cells to run from the cell's context menu:

| Command | Marks |
| --- | --- |
| **Mark Cell as Q1Lens Setup** | A cell that must run first: imports, the hardware configuration, the compiler. Mark as many as you need. |
| **Mark Cell as Q1Lens Schedule** | The one cell that builds the schedule |

Notebook analysis always uses the notebook's kernel, not the interpreter chosen for files. The kernel must run on your machine, and the workspace must be trusted.

## From a Python file

Create `qbstimeline.yml` next to your schedule code:

```yaml
schedule:
  file: schedule.py
  entrypoint: build_schedule
  compiler: build_compiler
```

| Key | Meaning |
| --- | --- |
| `schedule.file` | The Python file |
| `schedule.entrypoint` | The function that returns the schedule. Default `build_schedule`. |
| `schedule.compiler` | The function that returns the compiler. Default `build_compiler`. |
| `outputs.dir` | Where generated files go. Default `.qbs_timeline`. |
| `low_level.module_types` | The module type for a module the compiler does not identify, for example `{cluster0/cluster0_module2: QCM}` |

Open the **Q1Lens** view in the activity bar and select **Analyze and Open**.

## The Q1Lens view

| Action | What it does |
| --- | --- |
| **Analyze and Open** | Compiles the project and opens the result |
| **Refresh** | Runs the last analysis again |
| **Open Q1ASM Timeline** | Opens the generated Q1ASM on the [timeline](timeline.md) |
| **Open Current Folder Q1ASM** | Opens the Q1ASM files in the current folder on the timeline |
| **Open Rendered HTML** | Opens the schedule overview in your browser |
| **Open QBS IR** | Opens the compiled schedule as JSON |
| **Open Q1ASM Folder** | Shows the generated Q1ASM files in your file manager |

The view also has a **Python** row that checks the environment.

Set `q1lens.qbs.autoRefresh` to analyze again whenever the project or schedule files change.

## What gets generated

The output folder holds the compiled schedule (`qbs_ir.json`), an overview page (`index.html`) and a `q1asm` folder with the Q1ASM that the compiler produced for each sequencer. Q1Lens also keeps each sequencer's waveforms and settings in a sequence file and generates a `q1timeline.yml` that ties them together. Everything in the other chapters works on these generated files.

To compare the output of two compiles, keep each compile's generated folder and use [`q1lens compare-output`](cli.md#compare-compiled-output).

## As a task

To run the analysis as a VS Code task, use the `qbs-timeline` task type:

```json
{
  "type": "qbs-timeline",
  "command": "analyzeAndRender",
  "project": "qbstimeline.yml"
}
```

`command` is `analyze`, `render` or `analyzeAndRender`.
