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

## Timeline diagnostics

Errors highlight the affected command. When the Q1 lane is hidden, its errors
appear on the sequencer lane. Warnings remain in the Diagnostics list, where
event links open the affected instruction and source.

## Python environment

Find your Python executable by running this in your project's Python session or notebook:

```python
import sys
print(sys.executable)
```

In the Command Palette, run **Q1Lens: Select Python Environment** → **Enter Python path…** and paste that path.

Run **Q1Lens: Show Python Environment** to confirm the selection.

## Time compression

Time compression uses one icon-and-duration tab on the ruler for each interval.
Click it, or focus it and press Enter/Space, to expand or compress that interval
with a short animation. Subtle bands align the interval across lanes. Dense tabs
keep the icon and expose the full duration in the tooltip. The display transition
respects reduced-motion preferences and does not change execution timing.

Folded spans use the width needed by their tabs. The compact timeline stays on
the left and leaves unused space on the right. Time scale view fits the available
width without a horizontal scrollbar; zoom and pan still use real nanoseconds.
RT command names remain visible when folded; short commands reserve readable
label space while instruction timing and acquisition lengths stay unchanged.

Known acquisition windows can be compressed too, while their command and end
boundaries remain visible. A whole window's tab shows its acquisition duration;
the bracket keeps only its mode icon until expanded. The tooltip distinguishes
the full measurement length from the compressed interior. Commands inside a
window split the fold into smaller intervals with their actual durations.

## Acquisition timing

Save the QCoDeS integration setting alongside each sequencer in `q1timeline.yml`:

```yaml
sequencers:
  - id: readout
    file: readout.q1asm
    hardware_parameters:
      integration_length_acq: 1000
```

In Time scale view, sequencers with acquisition commands have one compact
**Acquisition** child row. Cyan brackets show elapsed acquisition time; icons
distinguish integration (Σ), weighted integration (sliders), and scope (waveform).
Its integration windows are separate from the RT instruction waits. A new Acquire
can end the previous integration early; select the window to inspect configured
and effective lengths and navigate to the original instruction. Instruction view
keeps the command sequence without an extra ACQ row.

The current implementation resolves ordinary QRM `acquire` from the saved setting
in nanoseconds. Missing settings, weighted acquisitions and unresolved paths show
unknown integration length. Settings are saved inputs; Q1Lens does not query a
live QCoDeS instrument. Numerical simulation keeps its existing physical settings.

For QRM/QRM-RF scope capture, save the actual hardware sequencer number and the
module's scope settings in the same sequencer entry:

```yaml
    module_type: QRM
    sequencer_index: 0
    hardware_parameters:
      integration_length_acq: 1000
      scope_acq_sequencer_select: 0
      scope_acq_trigger_mode_path0: sequencer
      scope_acq_trigger_mode_path1: sequencer
```

Only a matching selected sequencer with a `sequencer` trigger path gets a scope
bracket. Its 16384 ns capture appears below integration within the same compact
row. Level-triggered scope timing cannot be inferred from Q1ASM. Unknown hardware
types retain an unknown scope length. Open
`examples/acquisition-window-prototype/scope/readout.q1asm` for a complete example.
