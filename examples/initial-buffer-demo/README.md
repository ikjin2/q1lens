# Initial RT buffer demo

Three independent sequencers show how the initial-buffer assumption changes
the diagnosis of Q1 work after an RT command. Every sequence first applies a
marker at analytical RT time 0, performs a compact Q1-only loop, then issues
another RT update. No waveform memory or connected instrument is needed.

| Sequencer | Q1 work | Expected result |
| --- | --- | --- |
| `assumed` | About 0.5 ms | Covered by the assumed 1 ms reserve; no underflow warning |
| `tentative` | About 1.5 ms | Information: an initial buffer up to 2 ms may cover the work |
| `exhausted` | About 2.5 ms | Underflow error: both timing reserves are exhausted |

The reserve covers cumulative gaps in Q1's RT-command supply. It is not renewed
for each command, and does not insert a 1 ms delay before the first RT command.
The 1 ms and 2 ms values are unmeasured model assumptions. The ordinary FIFO
capacity remains 32 commands. The marker changes illustrate the analytical
trace; this project is not an instrument-control or hardware-validation script.

## Open in VS Code

1. Open `initial-buffer-demo.code-workspace` with the Q1Lens extension installed.
2. Open any of the three `.q1asm` files.
3. Run **Q1Lens: Open Timeline Preview** from the Command Palette.
4. Keep **Debug** mode to see Q1 work. **Instruction** view makes the compact
   loops and the final RT updates easier to inspect next to one another.
5. Find **Initial buffer ≈ 1 ms · up to 2 ms probably OK** in the timeline
   controls. Open the diagnostics to compare the informational estimate for
   `tentative` with the error for `exhausted`.

The informational notices about omitted loop previews are expected. The buffer
estimate is the diagnostic named `initial_rt_buffer_estimate`.

Keep execution mode set to **project** (the workspace does this). The project
uses compact analytical loops; running every loop iteration in concrete or
simulation mode would hit the normal instruction limit and demonstrate a
different feature.
Use **Q1Lens: Refresh Timeline Preview** to rerun this compact analysis.

The workspace selects `python` for Q1Lens so a separate instrument environment
does not override the demo. That Python must have the current Q1Lens package
available.

## Reproduce the analysis

From the repository root:

```powershell
python -m q1lens q1timeline analyze --project examples/initial-buffer-demo/q1timeline.yml --out output/initial-buffer-demo/timeline_ir.json --diagnostics output/initial-buffer-demo/diagnostics.json
```

The intentional underflow is an error, so this analytical demonstration exits
with code 2 while still writing its timeline and diagnostics. Generated outputs
stay outside the example source directory.
