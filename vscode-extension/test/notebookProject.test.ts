import assert from "node:assert/strict";
import { join, normalize } from "node:path";
import {
  createManagedNotebookTimelineProject,
  inferNotebookTimelineVariables,
} from "../src/qbs/notebookProject";

function normalized(filePath: string): string {
  return normalize(filePath).replace(/\\/g, "/");
}

describe("notebook project UX", () => {
  it("creates a managed qbstimeline project and selected notebook snapshot", () => {
    const project = createManagedNotebookTimelineProject({
      notebookPath: "C:\\repo\\experiments\\demo.ipynb",
      selectedCellIndex: 1,
      scheduleVariable: "two_tone_sched",
      compilerVariable: "hw_agent",
      cells: [
        {
          kind: "code",
          text: "hw_agent = build_compiler()\n",
          metadata: { tags: ["qbstimeline-setup"] },
        },
        {
          kind: "code",
          text: "two_tone_sched = Schedule('demo')\n",
          metadata: { tags: ["old-tag"] },
        },
        {
          kind: "markdown",
          text: "# Notes\n",
          metadata: { tags: ["qbstimeline-schedule"] },
        },
      ],
    });

    assert.equal(
      normalized(project.projectFile),
      normalized(join("C:\\repo\\experiments", ".qbs_timeline", "notebook", "qbstimeline.yml")),
    );
    assert.equal(
      normalized(project.snapshotFile),
      normalized(join("C:\\repo\\experiments", ".qbs_timeline", "notebook", "selected.ipynb")),
    );
    assert.match(project.projectYaml, /schedule:\n  notebook: selected\.ipynb/);
    assert.match(project.projectYaml, /source:\n  notebook: "C:\/repo\/experiments\/demo\.ipynb"/);
    assert.match(project.projectYaml, /schedule_variable: two_tone_sched/);
    assert.match(project.projectYaml, /compiler_variable: hw_agent/);
    assert.match(project.projectYaml, /outputs:\n  dir: \.\./);

    const snapshot = JSON.parse(project.snapshotJson);
    assert.deepEqual(snapshot.cells[0].metadata.tags, ["qbstimeline-setup"]);
    assert.deepEqual(snapshot.cells[1].metadata.tags, ["old-tag", "qbstimeline-schedule"]);
    assert.deepEqual(snapshot.cells[2].metadata.tags, []);
  });

  it("infers notebook variable defaults from selected and setup cells", () => {
    const inferred = inferNotebookTimelineVariables(
      [
        { kind: "code", text: "hw_agent = build_compiler()\n", metadata: {} },
        { kind: "code", text: "two_tone_sched = Schedule('demo')\n", metadata: {} },
      ],
      1,
    );

    assert.equal(inferred.scheduleVariable, "two_tone_sched");
    assert.equal(inferred.compilerVariable, "hw_agent");
  });
});
