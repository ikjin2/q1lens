# Continuous validation

`.github/workflows/validation.yml` runs on pushes, pull requests and manual dispatch. It uses read-only repository permissions, requires no repository secrets and never publishes packages or installs an extension into a user's VS Code profile. A newer run on the same ref cancels an older run.

## Jobs and evidence

| Job | Environment | Checks | Uploaded evidence |
| --- | --- | --- | --- |
| Python | Windows, Python 3.10 and 3.12 | All regular `tests/`; cursor-frequency project baseline | pytest log, JUnit XML with failures/skips, project baseline report |
| Independent wheel | Ubuntu 24.04, Python 3.10 | Build a wheel, install it in a separate clean environment, run the five API/CLI simulation fixtures | wheel, installation/provenance report, scenario results |
| Extension unit | Windows, Node 24, Python 3.12 | Compile and run the extension unit suite, including real Python environment probes | Mocha log and xUnit XML |
| Extension host | Windows, Node 24, Python 3.12, VS Code 1.90.0 and stable | Run the registered extension commands and file/settings lifecycle tests | host log and fixture evidence |
| Browser DOM | Ubuntu 24.04, Node 24, Python 3.12, Chromium | Generate fresh CLI IR and exercise the hardware view in a real browser | browser report, failing screenshots/traces and fixture-generation evidence |

Evidence under `output/ci/` uploads even when a test step fails, with 14-day retention. Artifact names distinguish Python and VS Code matrix versions. A test failure fails its job; no job uses `continue-on-error`.

The Python command explicitly names `tests/` rather than relying on repository-wide pytest discovery. This prevents copied test trees or local experimental archives from becoming accidental test inputs. `-ra` and JUnit XML retain skip reasons. Investigate any unexpected skip reason.

The complete Python and extension suites run on Windows, including Windows path behavior. The Ubuntu jobs test the portable installed-wheel and browser paths; they do not establish that the entire repository suite is Linux-compatible.

## Dependencies and versions

Python jobs install `.[dev]`: PyYAML plus build, pytest and the Python 3.10 tomli fallback. The core simulation and wheel jobs do not require q1simulator, Qblox drivers, a scheduler installation or hardware. The wheel verifier checks that q1simulator is absent and that package imports resolve to the isolated installation.

Node jobs use `npm ci` and the committed lockfile. Node 24 satisfies the current `@vscode/vsce` and transitive package engine requirements. Browser dependencies are installed by `npx playwright install --with-deps chromium` on the disposable runner. Python 3.12 is the current project validation version; Python 3.10 and VS Code 1.90.0 exercise the declared compatibility floors. The stable VS Code job deliberately follows current stable releases.

The extension unit job also installs the Python package and its runtime dependencies. Its environment-probe tests invoke a real Python interpreter and import the CLI, so Node dependencies alone are insufficient on a clean runner.

The GitHub actions are pinned to full commit SHAs. The initial pins were resolved from the official [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1), [setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0), [setup-node v7.0.0](https://github.com/actions/setup-node/releases/tag/v7.0.0) and [upload-artifact v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1) releases. When updating a pin, verify the official release and update its adjacent version comment. These runner actions and the project's Node runtime are separate version choices.

## Local equivalents

Use a clean Python environment, from the repository root:

```powershell
python -m pip install '.[dev]'
python -m pytest -q -ra tests --junitxml=output/ci/python/junit.xml
python examples/cursor-frequency-demo/verification/verify.py --out-dir output/ci/cursor
python -m build --wheel
python tools/verify_simulation_wheel.py --wheel 'dist/q1lens-*.whl' --out output/ci/wheel
```

The wheel argument is a quoted pattern resolved by the verifier, so the command behaves consistently in PowerShell and Bash. The verifier must select one wheel; remove ambiguity by passing an exact wheel path when testing a directory with multiple versions.

Extension and host tests, from `vscode-extension`:

```powershell
npm ci
npm test
$env:Q1LENS_VSCODE_TEST_VERSION = '1.90.0'
npm run test:extension
$env:Q1LENS_VSCODE_TEST_VERSION = 'stable'
npm run test:extension
Remove-Item Env:Q1LENS_VSCODE_TEST_VERSION
```

Each host run creates a fresh VS Code user-data profile and a copied workspace containing the required examples. The copied workspace receives the selected Python executable in its own settings; the developer's original workspace settings are preserved. Lifecycle fixtures live under that copied workspace's `host-fixtures` directory so VS Code's workspace file watchers actually observe edits. Reusing a profile can carry execution-mode settings from a previous run, while putting fixtures outside the workspace bypasses the file-watching behavior these tests are meant to verify. `Q1LENS_EXTENSION_HOST_OUTPUT` selects the directory for the copied workspace, fresh profile and host evidence.

Browser checks, from `vscode-extension`:

```powershell
npx playwright install chromium
npm run test:browser
```

On Linux, use `npx playwright install --with-deps chromium` to install required system libraries as in CI. `Q1LENS_BROWSER_PYTHON` can select the Python executable, and `Q1LENS_BROWSER_OUTPUT` can select the evidence directory. A DOM test result is distinct from extension-host command execution and from numerical model validation; all three checks serve different purposes.

Validate workflow syntax locally with:

```powershell
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 .github/workflows/validation.yml
```

Local passes and workflow syntax validation do not prove a hosted GitHub Actions run passed. The first remote run must be inspected after these files are pushed; creating this workflow does not push or dispatch it.
