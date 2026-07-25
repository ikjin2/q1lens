# Adaptive Array Demo

This q1timeline workspace is a deliberately dense simulation demo. It is not a
hardware recipe and it does not claim a real LINQ throughput number. The goal is
to put a larger adaptive experiment shape on screen than `three-peak-demo1`:
four drive lanes, four readout lanes, four trackers, two audit scopes, one
arbiter, one telemetry sidecar, and one fault probe.

The demo combines:

- trigger-aligned drive/readout windows
- acquisition-derived IQ feedback with two payload slots per readout
- tracker-to-arbiter register feedback
- arbiter-to-drive grant feedback
- dense telemetry feedback with intentional over-production
- one deliberate LINQ feedback latency violation
- branch regions, marker windows, and latched gain/frequency/phase updates

The shared trigger and LINQ channel assignments live in `params.json`, so the
same topology can be retuned without editing every sequencer source file.

For a browser-friendly explanation of what the demo shows, open
`demo-overview.html`.

## VS Code

From the repository root:

```powershell
code --extensionDevelopmentPath "$PWD\vscode-extension" "$PWD\examples\adaptive-array-demo\adaptive-array-demo.code-workspace"
```

Then run `Q1Lens: Open Timeline Preview` from the Command Palette.

## Analyze

From this example directory:

```powershell
python -m q1timeline analyze --project q1timeline.yml
```
