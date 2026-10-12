# Q1Lens Desktop

A standalone Electron + PixiJS workspace for Q1Lens. The existing Python engine
analyzes schedules and Q1ASM; a locally bundled PixiJS renderer displays their
timelines. No VS Code installation or CDN connection is needed at runtime.

## Run from this checkout

Requires Node.js 22.12+ and Python 3.10+ with PyYAML. A QBS project also needs its
usual scheduler dependencies in the selected Python environment.

```powershell
cd desktop-app
npm install
npm start
```

The included Three Peak project is analyzed on startup. `npm start` builds the
renderer before opening Electron. Python modules are loaded from this checkout's
`src` directory; this first version is a development app, not an installer.

## Open your data

- **Open project** accepts `q1timeline.yml`, `qbstimeline.yml`, or an exported
  `timeline_ir.json`. JSON imports do not require Python.
- **Reanalyze** refreshes the current project after you edit its source files.
- **Demo** returns to the included eight-sequencer example.
- The **Python button** in the bottom right selects an interpreter. Automatic
  selection checks `.venv` beside the project, then the repository `.venv`, then
  `python` on PATH. Manual selection lasts for the app session.

QBS projects are compiled into Q1ASM timelines. This version displays the
low-level timeline; the extension's high-level schedule and notebook views are
not yet included. Opening a QBS project executes its schedule code in the selected
Python environment, just like the existing CLI.

Analysis artifacts are written to the operating system's temporary directory,
under `q1lens-desktop-*`, rather than the project's outputs folder. These are
retained so generated Q1ASM source references remain valid. Failed opens preserve
the previous project and timeline.

## Explore the timeline

| Control | Action |
| --- | --- |
| Scroll | Zoom around the pointer |
| Drag | Pan through time and lanes |
| Shift + scroll | Scroll lanes vertically |
| Click an event or route | Inspect timing, confidence, and source |
| Click / drag the time ruler | Seek and pause |
| Playback slider | Seek through the complete sequence |
| Space | Play / pause when the timeline has focus |
| F / Fit | Fit the complete time range |
| Home / Reset | Fit the view and return to its start |
| Speed selector | 0.25×, 0.5×, 1×, or 2× playback |

**Overview** combines each sequencer into a compact row. **Flow** emphasizes
feedback paths. **Debug** expands instruction issue, queue, and slack lanes.
The timeline can scroll vertically when there are more lanes than fit on screen.

Playback is a visual inspection aid: at 1×, the displayed full time extent takes
15 seconds to traverse. Nanosecond labels come from analysis, not wall-clock
hardware execution. The app does not control hardware.

## Development checks

```powershell
npm run build
npm test
npm run verify
cd ..
python -m pytest tests/test_pixi_standalone_demo.py -q
```

Backend tests exercise real Q1ASM analysis, QBS compilation, invalid input, and
JSON loading without Python. `npm run verify` opens an actual Electron window,
checks playback, zoom/pan, mode changes, native-picker cancellation, invalid-input
recovery, QBS opening/reanalysis, and compact window layout. Native file dialogs
return controlled paths during this check. Screenshots go to `output/playwright`.

The standalone browser demo and Electron app share
`demo-pixi-standalone/pixi-standalone-demo.js`; keep both entry points working when
changing the renderer.
