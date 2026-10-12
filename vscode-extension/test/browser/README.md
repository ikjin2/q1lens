# Hardware view browser regression

These tests mount the actual `src/q1timeline/media/hardwareState.js`,
`hardwareView.js` and `hardwareView.css` in Chromium. The loopback server reads
those files directly; a regression test compares the served bytes and SHA-256
hashes to the source files. No generated asset copy or production test hook is
used. This checks the DOM and public view callbacks; the separate extension-host
suite checks VS Code command, file-watching and editor integration.

From the repository root, install the Python package with `python -m pip install
'.[dev]'`. Then, from `vscode-extension`:

```sh
npm ci
npx playwright install chromium
npm run test:browser
```

On the Linux CI runner use `npx playwright install --with-deps chromium`.
The lockfile pins Playwright and its associated browser build. Optional variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `Q1LENS_BROWSER_PYTHON` | `python` | Python executable; must import the package and PyYAML |
| `Q1LENS_BROWSER_OUTPUT` | `<repo>/output/ci-baseline-validation/browser` | Absolute evidence directory recommended |
| `Q1LENS_BROWSER_PORT` | `4173` | Test-only HTTP server bound to `127.0.0.1` |

Every invocation creates a new `fixtures-*` directory. The server invokes the
public CLI for all five `examples/simulation-verification` projects. All ordinary
fixtures must exit 0 with completed execution. `04-underflow` must exit exactly 2
and report only `rt_queue_underflow` as an error, at 24 ns. Missing/invalid JSON,
timeouts and startup errors fail setup; an older IR cannot satisfy these checks.
Noise seeds are declared in the projects and checked by the tests.

The server also runs both profiles of
`examples/cursor-frequency-demo/verification/verify.py` in its new fixture
directory. Each profile generates two fresh CLI outputs and checks deterministic
replay, independent controller arithmetic, modeled DAC samples and unchanged
original source hashes. The original continuous programs intentionally finish
`blocked` awaiting an unsupplied third input. Their IQ inputs are synthetic; they
are neither measurements nor evidence of hardware agreement. Browser assertions
check readable register values, source locations, Module → Sequencer navigation,
one-shot frequency/gain latency, scenario bin provenance and rewind boundaries.

## Prior check mapping

The five functional tests in `hardware.spec.js` retain the 74 checks from the
earlier local `output/playwright/simulation/verify-*.js` experiments. Those local
scripts are not runtime dependencies and need not exist in a clean checkout.

| Earlier script | Checks | Durable test and coverage |
| --- | ---: | --- |
| `verify-digital.js` | 22 | Digital test: register readiness, writer occurrence IDs and navigation, source callback, hex/all-64 controls, queue stall, pending/applied gain, flags, slider, keyboard tabs, time basis, narrow viewport |
| `verify-communication.js` | 12 | Communication test: wait/arrival/readiness, receive provenance, sender navigation, arithmetic, trigger boundary |
| `verify-signals.js` | 28 | Physical test: five stages, I/Q polylines, finite SVG, four filter startup samples, result completion, threshold, loopback provenance, rewind and controller feedback |
| `verify-underflow.js` | 5 | Underflow test: overview warning, status/time, partial stall and empty queue |
| `verify-supplied.js` | 7 | Supplied-input test: ADC I/Q, synthetic provenance, completion boundary, threshold and bin history |

Additional tests enforce fresh fixtures/source assets and the two selected cursor
profiles. Every test fails on browser exceptions, error console messages, failed
requests or HTTP errors. Controls use accessible roles/names; stable production
`data-*` attributes identify numeric output cells. Screenshots on failure and
retained traces help diagnose layout, source and timing failures. There is no
pixel-perfect screenshot baseline or claim of complete visual accessibility.

`extended-simulation.spec.js` adds seven input-fault cases and a compact bin-wrap
case. Every server start generates fresh IR for the seven adjacent
`examples/simulation-verification/faults` projects and runs the independent
`verify_boundaries.py --profile bin-wrap-preview` oracle. The manifest records
these under `extended_cases`. Expected error exits require the precise underflow
or overflow diagnostic. Browser checks cover wait/resume, register latency,
duplicate-input identity, blocked trigger waits, queue backpressure, error causes,
source callbacks, and bin result replacement/rewind. The wrap fixture runs all
four original sequencers for three shots with an explicit four-bin override.
The 1,001-shot PRNG and 513-shot ring profiles run in the separate CLI campaign;
their large traces are not loaded by browser setup.

## Cursor performance budget

`performance-fixture.js` creates a deterministic synthetic mix of **10,000 total
events**, divided across 1, 6 or 24 sequencers. It includes Q1 issue, register
writes, pending/applied settings, queue, acquisition and communication records.
Each of Registers, Parameters, Bins and Simulation receives 20 warmup cursor
changes followed by 100 fixed-order measured changes. The sorted 95th value
(nearest-rank p95) must be at most **100 ms** in every view; there are no retries.

Timing starts after the IR has reached the browser. Each measured operation
dispatches a synchronous DOM change and forces layout. It excludes transport,
input scheduling, animation-frame scheduling and GPU paint. Fast synthetic
updates and shared CI host load affect the result: this is a local rendering
regression budget, not an end-to-end latency or hardware-performance guarantee.
Mount time and DOM node count are recorded without separate pass thresholds.

## Evidence

The output directory contains `manifest.json` (fresh run, CLI exits, IR hashes,
asset hashes), `fixtures-*/` (CLI logs and all freshly generated IR plus cursor
oracle evidence), `results.json`, an HTML `report/`, and `test-results/` (failure
screenshots, traces and context). Each test attaches its browser-error list;
performance tests attach `cursor-performance` JSON with p50/p95/max for every
view, browser identity and measurement limits. Fixture logs remain available if
setup fails before Playwright can run a test. CI uploads this directory even on
failure. A local pass does not establish a remote GitHub Actions pass.
