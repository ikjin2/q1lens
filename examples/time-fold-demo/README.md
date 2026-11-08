# Time-fold demo

Open `readout.q1asm` in VS Code and run **Q1Lens: Open Timeline Preview**.
The adjacent `q1timeline.yml` supplies the saved QCoDeS parameter
`integration_length_acq: 1000` for the Acquisition lane.

1. Click the **8 µs** tab above the timeline to expand the first quiet interval.
2. Click it again to compress the interval. The layout animates in both directions.
3. Try the **4 µs** tab independently, or focus either tab and press Enter or Space.
4. The three **1 µs** tabs fold the integration windows too. A folded bracket keeps
   its acquisition icon; expanding restores the duration beside the icon.

Folded intervals stay as short as their tab labels. The compact timeline leaves
space on the right and fits the preview width without horizontal scrolling.

The three cyan integration brackets each span **1000 ns**. Folding only changes
the displayed time scale; acquisition times and the total duration stay unchanged.
Each interval keeps 16 ns of context at both ends. Quiet-interval tabs represent
exactly 8000 ns and 4000 ns. Acquisition tabs show the full 1000 ns measurement;
their tooltips identify the 964 ns compressed interior, after the 4 ns RT command
and the context at both ends.
