# Acquisition window prototype

Open `readout.q1asm` in VS Code and run **Q1Lens: Open Preview** to use the
extension's existing timeline and Hardware UI. The same project also opens with
**Open project** in Q1Lens Desktop (`cd desktop-app` and `npm start`).

The existing RT rows show instruction waits. One compact **Acquisition** row per
acquiring sequencer shows cyan brackets and mode icons. Select either an Acquire command
or its integration window to inspect the same source instruction.

| Sequencer | Setting | What to inspect |
| --- | --- | --- |
| `readout` | 1000 ns | First acquisition ends after 600 ns when the next Acquire starts. Different acquisition and bin indices share one ACQ row. |
| `parallel` | 800 ns | Independent windows overlap in time with the other sequencer. |
| `unknown` | Not supplied | Each acquisition shows a 4 ns integration assumed from the RT instruction duration. |

`sequencers[].hardware_parameters.integration_length_acq` is an explicit saved
setup value in nanoseconds. In this prototype, enter the same value used for the
QCoDeS setter; the app does not connect to a live instrument or inspect notebook
setter calls. Edit the YAML and use **Reanalyze** to try another length.
The saved value takes precedence over the displayed assumption.

From `desktop-app`, run `npm run build` followed by
`node scripts/verify-acquisition.cjs` to check the real Python-to-Electron flow.
Screenshots and verification evidence are saved under `output/playwright`.

This prototype covers ordinary QRM `acquire` in analytical/concrete timelines.
Weighted or unresolved acquisition windows stay unknown. Numerical simulation
continues to use its existing `simulation.physical` settings.

Open `scope/readout.q1asm` for simultaneous scope and integration. The nested
project saves `sequencer_index`, `module_type`, and the module's QCoDeS scope
selection/trigger settings. Scope appears only for the explicitly selected
sequencer and paths triggered by `sequencer`; it uses a second compact track
within the same Acquisition row. Scope capture is independent of integration.

Acquisition interruption follows the [Qblox integrator description](https://docs.qblox.com/en/2026.10.1/products/architecture/sequencers/readout.html#integrator):
a new `acquire` or `acquire_weighed` ends the previous integration, stores its
result, and starts the next integration. An interrupted window is not itself an
acquisition buffer error.
