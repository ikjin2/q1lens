# Input faults and queue backpressure

Run from the repository root with q1lens installed in the selected Python:

```console
python examples/simulation-verification/faults/verify.py
python examples/simulation-verification/faults/verify.py --case 03-feedback-duplicate --out-dir output/my-fault-run
```

The default evidence directory is `output/extended-simulation-validation/faults`. Each invocation retains a fresh `run-*/CASE/` directory with two public-CLI JSON traces, logs, and exact commands. `report.json` records checked observations, replay equality, source hashes, and trace paths. `--case` may be supplied for several different cases. A failed verification returns exit code 1 and leaves `passed: false`; it never reuses a previous trace.

The verifier runs the CLI from the repository root and prepends this checkout's `src` to `PYTHONPATH`, matching the boundary verifier. The report and recorded commands identify that core source path; an older installed wheel is not selected in its place.

Each named directory contains the complete source program and YAML scenario. It can also be opened directly in Q1Lens or reproduced using:

```console
python -m q1lens q1timeline analyze --project examples/simulation-verification/faults/03-feedback-duplicate/q1timeline.yml --out output/duplicate.json --format vscode-json --summary-only
```

All times below are local nanoseconds. Startup is explicitly 200 ns; it is a model parameter, not a measured device latency.

| Case | Deliberate condition | Checked result |
| --- | --- | --- |
| `01-feedback-missing` | No feedback while Q1 waits after enqueuing one 1000 ns RT wait | RT runs 200–1200 ns, then underflows at 1200; status `error`, feedback stall remains pending, no register value is invented |
| `02-feedback-delayed` | Feedback ID 5/value 7 arrives at 400 ns | Q1 waits from 4 to 400 ns, R0 becomes readable at 408, status `completed` |
| `03-feedback-duplicate` | ID 5 receives values 7, 7, 9 at the same 400 ns timestamp | FIFO R0/R1/R2 = 7/7/9 at 408/416/424; identical values remain separate input occurrences and writer origins |
| `04-trigger-missing` | `wait_trigger 1,4` never receives a trigger | RT is pending from 200 ns; status `blocked`, no completed stop or fabricated trigger |
| `05-queue-full` | Six 40 ns RT waits with queue capacity 2 | Four Q1 stalls, depth at most 2, every packet enqueued/dequeued/executed once in order, RT completes at 440 ns |
| `06-feedback-too-late` | Feedback would arrive at 1600 ns, beyond the RT wait deadline | Underflow at 1200 ns; the failed sequencer does not publish the future input or register write |
| `07-feedback-overflow` | Feedback capacity 2 and three inputs (11, 22, 33) at 400 ns | Error at 400 ns; the first two arrivals retain their values, input indices and depths; the third is not accepted and Q1 publishes no register write |

The public analyzer exits 2 for the three expected error cases. Missing-trigger exits 0 because it has a warning, **but its execution is `blocked`, not completed**. The verifier checks execution status and pending events in addition to process exit codes. Same-time input events precede Q1 work in this model, so all three overflow inputs are considered before Q1 can pop an entry.

These are regression checks of q1lens's numerical/event-driven model. They do not establish behavior or timing equivalence on physical hardware. The scenarios use no measured input or external simulator. The original cursor demo and the pre-existing five simulation verification examples are unchanged.
