# Three Peak Demo 1

This workspace models a three-peak demo where QCM0 emits three independently
drifting Gaussian peaks on Ch1, Ch2, and Ch3, and emits the sum of those three
peaks on Ch0. QRM0 tracks Ch1 and Ch2. QRM1 tracks Ch3.

Each shot starts from `wait_trigger 1, $TRIGGER_WAIT`, so the external trigger
period controls the demo speed. The Q1ASM wait budget includes that trigger
wait, so keep the trigger period at or above `T_TOTAL`.

The peak drift is generated inside Q1ASM with independent xorshift32 seeds.
The demo uses independent bounded drift personalities: Ch1 updates frequently
with smaller jittery steps, Ch2 wanders more slowly with larger steps, and Ch3
drifts steadily with small regular moves. At the time-window edges the offsets
reflect back inward instead of wrapping, so the peaks stay separate without
sudden cross-window jumps.

The PC is not part of the real-time tracking loop. It should poll acquisition
results roughly once per second. Edge bins provide averaged height samples.
Position is encoded as a dynamic acquisition bin: the bin `avg_cnt` histogram
tracks the current `MEAS_DELAY` estimate.

## VS Code

From the repository root:

```powershell
.\scripts\open-vscode-demo.ps1 -Example three-peak-demo1
```

Or open the workspace manually with the development extension:

```powershell
code --extensionDevelopmentPath "$PWD\vscode-extension" "$PWD\examples\three-peak-demo1\three-peak-demo1.code-workspace"
```

Then run `Q1Timeline: Open Timeline Preview` from the Command Palette.
