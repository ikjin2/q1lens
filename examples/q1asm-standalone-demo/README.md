# Q1ASM Standalone VSCode Demo

This folder is a manual smoke demo for the standalone Q1ASM VSCode workflow.
It intentionally does not include `q1timeline.yml`.

## What This Exercises

- Opening a `.q1asm` file directly.
- Auto-generating `.q1timeline/auto-generated.q1timeline.yml`.
- Including sibling `.q1asm` files in the generated project.
- Inferring placeholder params into `.q1timeline/auto-generated.params.json`.
- Selecting a smaller folder subset with `Q1Lens: Select Q1ASM Files in Folder...`.

## Manual VSCode Test

1. Open this repository in VSCode with the Q1Lens extension installed or running
   in an Extension Development Host.
2. Open `examples/q1asm-standalone-demo/drive.q1asm`.
3. Click inside the `drive.q1asm` editor so it is the active editor, then run
   `Q1Lens: Open Timeline Preview`. You can also right-click `drive.q1asm` in
   Explorer and choose `Q1Lens: Open Timeline Preview`.
4. Confirm the timeline opens.
5. Open the `Q1ASM Timeline` output channel and look for lines like:

```text
Auto-generated q1timeline fallback includes 2 Q1ASM file(s): drive.q1asm, readout_feedback.q1asm
Auto-generated q1timeline fallback params: auto-generated.params.json
Created auto-generated q1timeline fallback project: ...
```

6. Confirm these files were created:

```text
examples/q1asm-standalone-demo/.q1timeline/auto-generated.q1timeline.yml
examples/q1asm-standalone-demo/.q1timeline/auto-generated.params.json
```

7. Run `Q1Lens: Select Q1ASM Files in Folder...`.
8. Select only `drive.q1asm`.
9. Confirm `.q1timeline/auto-generated.q1timeline.yml` is rewritten with only
   the selected file.

## Cleanup

Generated files are disposable:

```powershell
Remove-Item -Recurse -Force examples\q1asm-standalone-demo\.q1timeline
```
