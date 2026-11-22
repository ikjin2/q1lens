# Quick start

This walkthrough finds a real-time queue underflow, opens the failing run on the timeline and shows you the first empty dequeue. It takes about five minutes.

## Before you start

- [Install](install.md) the Python package and the extension.
- Clone the [Q1Lens repository](https://github.com/ikjin2/q1lens) to get the example files.

## Find the underflow

1. In VS Code, open `examples/verification-demo/verification.code-workspace` from your clone.
2. Run **Q1Lens: Select Python Environment** and choose the interpreter where you installed `q1lens`.
3. Open [`queue.q1asm`](../../examples/verification-demo/queue.q1asm):

    ```asm
    # The pulse train drains the prefilled RT queue faster than Q1 replenishes it.
    .DEF N 20
    move $N,R0
    wait 56
    pulse: sub R0,1,R0
           play 0,1,4
           jnz @pulse
    stop
    ```

    Each pass of the loop plays for 4 ns, but Q1 needs longer than that to issue the next `play`. The RT core drains the queue faster than Q1 fills it. How many pulses fit before the queue is empty?

4. Run **Q1Lens: Verify Program**. Q1Lens varies `N` and reports the boundary:

    > N=1..37 safe. First RT underflow at 1188 ns for N=38

5. Choose **Open counterexample timeline**. The timeline opens the run with `N=38` and selects the dequeue at **1188 ns**, where the RT core finds the queue empty.

## Look around

- Click any block on the timeline. The line under the timeline describes the event and the editor jumps to its source line.
- Run **Q1Lens: Open Timeline Preview** on the same file to see the program with its own value, `N=20`.
- Click **Z3 Auto: Off** in the status bar to verify on every save. The result then appears above the `.DEF N 20` line.

## Try your own file

You do not need a project file to start. Create `hello.q1asm`:

```asm
wait_sync 4
wait 64
play 0,1,40
stop
```

Run **Q1Lens: Open Timeline Preview**. Q1Lens builds a temporary project for the folder and shows the timeline. Add a [project file](projects.md) when you need several sequencers, parameters or waveforms.

## Next

- [Read the timeline](timeline.md) explains what each row and marker means.
- The [tutorial examples](../../examples/q1asm-tutorials/README.md) are 48 small programs, each built around one behavior.
- The [verification demo](../../examples/verification-demo/README.md) continues with assertions, feedback, alignment and before/after comparison.
