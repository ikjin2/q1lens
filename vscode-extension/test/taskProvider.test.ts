import assert from "node:assert/strict";
import { buildTaskDefinitions, buildTaskExecution, resolveTaskDefinition } from "../src/qbs/taskProvider";

describe("taskProvider", () => {
  it("builds analyze, render, and combined tasks for each project", () => {
    const tasks = buildTaskDefinitions(["examples/basic-transmon/qbstimeline.yml"]);

    assert.deepEqual(tasks.map((task) => task.label), [
      "Q1Lens: Analyze examples/basic-transmon",
      "Q1Lens: Render examples/basic-transmon",
      "Q1Lens: Analyze and Render examples/basic-transmon",
    ]);
  });

  it("passes required Q1Lens CLI arguments for analyze and render tasks", () => {
    const tasks = buildTaskDefinitions(["examples/basic-transmon/qbstimeline.yml"]);
    const settings = { pythonPath: "python", pythonArgs: [] };

    assert.deepEqual(buildTaskExecution(tasks[0], settings), {
      command: "python",
      args: [
        "-m",
        "q1lens",
        "analyze",
        "--project",
        "examples/basic-transmon/qbstimeline.yml",
        "--out",
        "examples/basic-transmon/.qbs_timeline/qbs_ir.json",
      ],
    });

    assert.deepEqual(buildTaskExecution(tasks[1], settings), {
      command: "python",
      args: [
        "-m",
        "q1lens",
        "render",
        "--ir",
        "examples/basic-transmon/.qbs_timeline/qbs_ir.json",
        "--out",
        "examples/basic-transmon/.qbs_timeline/index.html",
      ],
    });
  });

  it("runs analyze before render for combined tasks", () => {
    const task = buildTaskDefinitions(["examples/basic-transmon/qbstimeline.yml"])[2];
    const execution = buildTaskExecution(task, { pythonPath: "python", pythonArgs: ["-X", "utf8"] });

    assert.equal(execution.command, "python");
    assert.equal(execution.args[0], "-X");
    assert.equal(execution.args[1], "utf8");
    assert.equal(execution.args[2], "-c");
    assert.match(execution.args[3], /"analyze"/);
    assert.match(execution.args[3], /"render"/);
    assert.match(execution.args[3], /examples\/basic-transmon\/\.qbs_timeline\/qbs_ir\.json/);
  });

  it("resolves user-authored task definitions into runnable artifact paths", () => {
    const definition = resolveTaskDefinition({
      type: "qbs-timeline",
      command: "render",
      project: "examples/two-qubit-entangling/qbstimeline.yml",
    });

    assert.deepEqual(definition, {
      type: "qbs-timeline",
      command: "render",
      project: "examples/two-qubit-entangling/qbstimeline.yml",
      label: "Q1Lens: Render examples/two-qubit-entangling",
      outputDir: ".qbs_timeline",
      irPath: "examples/two-qubit-entangling/.qbs_timeline/qbs_ir.json",
      htmlPath: "examples/two-qubit-entangling/.qbs_timeline/index.html",
    });
  });

  it("applies the configured output directory override when resolving tasks", () => {
    const definition = resolveTaskDefinition(
      {
        type: "qbs-timeline",
        command: "analyze",
        project: "examples/basic-transmon/qbstimeline.yml",
      },
      "build/qbs",
    );

    assert.equal(definition?.irPath, "examples/basic-transmon/build/qbs/qbs_ir.json");
    assert.equal(definition?.htmlPath, "examples/basic-transmon/build/qbs/index.html");
  });
});
