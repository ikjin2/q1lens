# Settings

Open VS Code settings and search for `Q1Lens`, or edit `settings.json`.

## Python

| Setting | Default | Purpose |
| --- | --- | --- |
| `q1lens.pythonEnvironment` | `auto` | `auto` uses a path you set and otherwise follows VS Code's selected Python. `vscode` always follows VS Code's selection. |
| `q1lens.pythonPath` | `python` | The Python executable for file analysis |
| `q1lens.pythonArgs` | `[]` | Arguments placed before the Q1Lens module, for a launcher such as `uv` with `["run", "python"]` |
| `q1lens.q1timeline.pythonPath` | `python` | A separate executable for Q1ASM analysis. It takes precedence over `q1lens.pythonPath`. |
| `q1lens.q1timeline.pythonArgs` | `[]` | Launcher arguments for Q1ASM analysis |

Notebook analysis always uses the notebook's kernel.

## Timeline

| Setting | Default | Purpose |
| --- | --- | --- |
| `q1lens.q1timeline.projectFile` | `q1timeline.yml` | The project file name or relative path to look for |
| `q1lens.q1timeline.updateMode` | `onSave` | When the preview refreshes: `onSave`, `onType` or `manual` |
| `q1lens.q1timeline.debounceMs` | `400` | The delay before an `onType` refresh, in ms |
| `q1lens.q1timeline.view.defaultMode` | `normal` | The view the timeline opens in: `normal` or `debug` |
| `q1lens.q1timeline.executionMode` | `project` | `project` follows `q1timeline.yml`. `analysis`, `concrete` and `simulation` override it. |
| `q1lens.q1timeline.maxSteps` | `10000` | The step limit per sequencer when `executionMode` overrides the project |
| `q1lens.q1timeline.hardwareReadiness` | `false` | Adds scope and bin-reuse hints |
| `q1lens.q1timeline.verification.autoRun` | `false` | Verifies saved Q1ASM files automatically |
| `q1lens.q1timeline.render.maxEventsBeforeSimplify` | `10000` | The event count above which the timeline warns and simplifies |

## Analyzer process

| Setting | Default | Purpose |
| --- | --- | --- |
| `q1lens.q1timeline.analyzer.timeoutMs` | `30000` | The longest one analysis may run, in ms |
| `q1lens.q1timeline.analyzer.extraArgs` | `[]` | Extra arguments for the analyzer |
| `q1lens.q1timeline.analyzer.env` | `{}` | Environment variables for the analyzer process |

## Qblox Scheduler

| Setting | Default | Purpose |
| --- | --- | --- |
| `q1lens.qbs.autoRefresh` | `false` | Analyzes again when project or schedule files change |
| `q1lens.qbs.outputDirOverride` | `null` | A different folder for generated files |
| `q1lens.revealOutputChannel` | `true` | Shows the Q1Lens output when you run an analysis |
| `q1lens.q1timelineCommand` | `null` | A `q1timeline` command to use in place of the built-in one |

## Older names

Settings that start with `qbsTimeline.`, `qbloxTimeline.` or `q1timeline.` are names from earlier versions. Use the `q1lens.` names for new configuration.
