# Command line and CI

Everything the extension shows comes from the `q1lens` command, so you can run the same checks in a terminal, a script or a CI job. If the command is not on your path, use `python -m q1lens`.

## Verify

```sh
q1lens verify program.q1asm
q1lens verify program.q1asm --project q1timeline.yml --sequencer drive
q1lens verify q1timeline.yml --alignment
```

The first form checks the RT queue boundaries and the `@assert` comments in one file. The second uses a project's settings for that file. The third checks [alignment](verification.md#alignment-between-sequencers) across a project.

```text
counterexample
queue.q1asm:2  counterexample  N=1..37 safe. First RT underflow at 1188 ns for N=38 (buffered RT startup).
  N=38  queue.q1asm:5
```

| Exit code | Status | Meaning |
| --- | --- | --- |
| 0 | `proved` | Every check holds |
| 1 | `counterexample` | At least one check has a reproduced failure |
| 2 | `unknown` | A check could not be settled |

Use the exit code as the gate in CI.

| Option | Purpose |
| --- | --- |
| `--json` | Print a machine-readable report with one entry per check |
| `--include-replay` | Add the failing run's timeline to the JSON |
| `--startup-delay-ns N` | Model a fixed RT start time |
| `--max-steps`, `--max-cases`, `--max-paths`, `--timeout-ms` | Limit the work |

## Compare two versions

```sh
q1lens equiv before.q1asm after.q1asm
```

This compares the RT commands of two programs over their shared `@assume` ranges. It uses the same exit codes and the same `--json` and `--include-replay` options as `verify`.

## Compare compiled output

```sh
q1lens compare-output before/q1timeline.yml after/q1timeline.yml --port cluster0/module2.out0 --start-ns 0 --end-ns 40
```

This compares the sampled signal at one module port over a time window. It exits with 0 when the samples are equal, 1 when they differ and 2 when the comparison is incomplete. With `--json`, `first_difference` gives the time and both values. Use `--params inputs.json` when the two projects have different default parameter values.

## Analyze and render a timeline

```sh
q1lens q1timeline analyze --project q1timeline.yml --out timeline.json
q1lens q1timeline render --ir timeline.json --out timeline.html --mode debug
```

`analyze` writes the timeline as JSON and prints the diagnostic counts. `render` turns it into a standalone HTML page you can open in a browser or attach to a report.

To analyze one file without a project, give the file instead of `--project`.

| `analyze` option | Purpose |
| --- | --- |
| `--execution-mode {analysis,concrete,simulation}` | Override the project's [execution mode](execution-modes.md) |
| `--max-steps N` | Limit the instructions executed per sequencer |
| `--params FILE`, `--set-param NAME=VALUE` | Override parameter values |
| `--strict` | Exit with an error when there are warnings |
| `--hardware-readiness` | Add scope and bin-reuse hints |
| `--no-render` | Do not render HTML |

`analyze` exits with 2 when the analysis finds an error. It still writes the timeline and the diagnostics.

To keep a rendered timeline up to date while you edit:

```sh
q1lens q1timeline watch --project q1timeline.yml --out-dir out
```

## Prove one property

`q1lens q1timeline prove` asks one question about one file over a range of one DEF.

```sh
q1lens q1timeline prove sum.q1asm --range X=0..1013 --assert "R2 < 1024"
q1lens q1timeline prove branches.q1asm --range X=0..1014 --reach limit
q1lens q1timeline prove queue.q1asm --range N=1..37 --underflow-at 4
```

| Question | Exit 0 | Exit 1 |
| --- | --- | --- |
| `--assert EXPR` | Proved | A counterexample exists |
| `--reach LABEL`, `--path BRANCH=OUTCOME,...` | Reachable | Unreachable |
| `--underflow-at STARTUP_NS` | No underflow | Underflow found |

Exit code 2 always means unknown. Add `--replay-ir FILE` to save the witness run, then render it. More examples are in [Run with DEF values](../def-register-sweep.md#prove-a-property-or-find-a-reachable-path).

## Qblox Scheduler projects

```sh
q1lens build --project qbstimeline.yml
q1lens doctor --project qbstimeline.yml
```

| Command | Purpose |
| --- | --- |
| `build` | Compile the schedule and render the overview page. `--open` opens it. |
| `analyze` | Compile the schedule and write the result as JSON |
| `render` | Render a compiled schedule to HTML |
| `doctor` | Check a project without running your code |
| `diagnose` | Write a diagnostics report, and optionally a bundle to share |
| `verify-bundle` | Check a diagnostics bundle |

See [Qblox Scheduler and notebooks](scheduler-notebook.md) for the project file.

## Help

Every command prints its options with `--help`, for example `q1lens verify --help`.
