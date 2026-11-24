# Troubleshooting

## The Python environment needs attention

Run **Q1Lens: Check Python Environment** and read the **Q1Lens Python Environment** output. It lists the command Q1Lens ran, the Python version and whether it found the `q1lens` package.

- **`q1lens` is not installed in that interpreter.** Install it there, or run **Q1Lens: Select Python Environment** and choose the interpreter that has it. See [Install](../guide/install.md).
- **The interpreter is older than Python 3.10.** Choose a newer one.
- **The wrong interpreter is used.** A path you set earlier takes precedence over VS Code's selection. Open **Q1Lens: Show Python Environment** and choose **Follow VS Code Selection**, or select the interpreter again.

## Version mismatch

The warning names the Python package version and the extension version. Update the older one so both match:

```bash
python -m pip install --upgrade q1lens
```

## The timeline does not open or is out of date

- Save the file. With the default `onSave` update mode, the preview follows the saved source.
- Select **Refresh** in the timeline toolbar.
- Open the **Q1ASM Timeline** output for the analyzer's messages.
- A large program can exceed the time limit. Raise `q1lens.q1timeline.analyzer.timeoutMs`.

## "No q1timeline.yml found"

This is a notice. Q1Lens made a temporary project for the folder. Add a [project file](../guide/projects.md) when you need parameters, several sequencers or waveforms.

## No verification results above the source

- Automatic verification is off by default. Click **Z3 Auto: Off** in the status bar.
- Results are CodeLens items. Turn on **Editor: Code Lens** for Q1ASM.
- Verification reads saved files. Save the source and the project files.
- "Verification inputs changed" means the file changed after the result was computed. Run verification again.

## A check returns Unknown

Unknown means Q1Lens could not conclude either way. Common causes:

- The program uses an instruction or an input the verifier does not model, such as a feedback receive with no supplied word.
- A loop or a set of paths exceeded the budget. On the command line, raise `--max-steps`, `--max-paths`, `--max-cases` or `--timeout-ms`.
- A waveform assertion has no waveform data. Give the sequencer a `sequence_json`.

## A notebook does not open

- Select a Python kernel for the notebook and run one cell, then try again.
- Save the notebook.
- The kernel must run on your machine, and the workspace must be trusted.
- Install `q1lens` in the kernel's environment. The message shows the command to use.

## A timeline row stops early

Execution stops where the model has nothing to go on. The diagnostic at that point says why: a missing trigger or feedback word, an unsupported instruction, or the step or time limit. Supply the [external input](../guide/feedback-and-inputs.md#external-inputs), or raise the limit.

## Decision lanes does not open or stops early

- **"The Q1Lens Python package in the selected environment does not include Decision lanes."** The extension is newer than the package in that interpreter. Update the package there, then select **Refresh**.
- **The panel says Partial.** The run stopped where the model had nothing to go on, and the panel shows why. A missing trigger needs an [external input](../guide/feedback-and-inputs.md#external-inputs). A long run that reads many feedback words reaches the limit on recorded events.
- **No input decision.** No branch in the run compares a value that came from a feedback word. Check that the run reaches the `fb_pop_data` you expect.
- The panel shows the saved file. Save your edits, or select **Refresh** after changing a file outside VS Code.
- Messages from the Python worker are in the **Q1Lens Decision Lanes** output.

## Report a problem

Open an issue at [github.com/ikjin2/q1lens/issues](https://github.com/ikjin2/q1lens/issues). Include the Q1Lens version, the output from **Q1Lens: Check Python Environment** and a small Q1ASM file that shows the problem.
