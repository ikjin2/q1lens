# Commands

Run a command from the Command Palette (`Ctrl+Shift+P`). Type `Q1Lens` to list them all.

## Python environment

| Command | What it does |
| --- | --- |
| **Q1Lens: Select Python Environment** | Chooses the interpreter Q1Lens runs |
| **Q1Lens: Show Python Environment** | Shows the selected interpreter, the installed version and its state |
| **Q1Lens: Check Python Environment** | Checks the environment again and writes the details to the **Q1Lens Python Environment** output |

## Timeline

| Command | What it does |
| --- | --- |
| **Q1Lens: Open Timeline Preview** | Opens the timeline for the current Q1ASM file |
| **Q1Lens: Refresh Timeline Preview** | Analyzes again |
| **Q1Lens: Reveal Current Line in Timeline** | Selects the event for the line under the cursor |
| **Q1Lens: View Selection in Timeline** | Zooms to the selected source lines |
| **Q1Lens: Open Decision Lanes** | Records one run of the current Q1ASM file and shows its [feedback-dependent branches](../guide/decision-lanes.md) |
| **Q1Lens: Select Project File** | Chooses which `q1timeline.yml` to use |
| **Q1Lens: Select Q1ASM Files in Folder...** | Chooses the files to show when there is no project file |
| **Q1Lens: Set Analysis Parameter...** | Overrides one parameter for this session |
| **Q1Lens: Clear Analysis Parameter Overrides** | Removes every override |
| **Q1Lens: Save Timeline Comparison Baseline** | Keeps the current run as the reference for change marks |
| **Q1Lens: Clear Timeline Comparison Baseline** | Compares with the previous run again |

## Verification

| Command | What it does |
| --- | --- |
| **Q1Lens: Verify Program** | Checks queue boundaries and assertions in the current file |
| **Q1Lens: Toggle Automatic Z3 Verification** | Turns verification on save on or off |
| **Q1Lens: Verify Sequencer Alignment** | Checks alignment across measurement outcomes in a project |
| **Q1Lens: Compare RT Traces Across Inputs** | Compares the current file with another version |

## Qblox Scheduler and notebooks

| Command | What it does |
| --- | --- |
| **Q1Lens: Analyze and Open** | Compiles the scheduler project and opens the result |
| **Q1Lens: Refresh** | Runs the last analysis again |
| **Q1Lens: Open Q1ASM Timeline** | Opens the generated Q1ASM on the timeline |
| **Q1Lens: Open Current Folder Q1ASM** | Opens the Q1ASM files in the current folder |
| **Q1Lens: Open Rendered HTML** | Opens the schedule overview in a browser |
| **Q1Lens: Open QBS IR** | Opens the compiled schedule as JSON |
| **Q1Lens: Open Q1ASM Folder** | Shows the generated Q1ASM files in the file manager |
| **Q1Lens: Open Q1Lens from This Cell** | Compiles the schedule built in a notebook cell |
| **Q1Lens: Mark Cell as Q1Lens Setup** | Marks a notebook cell to run before the schedule cell |
| **Q1Lens: Mark Cell as Q1Lens Schedule** | Marks the notebook cell that builds the schedule |
