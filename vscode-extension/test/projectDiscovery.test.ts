import assert from "node:assert/strict";
import { join, normalize } from "node:path";
import {
  chooseProjectFile,
  deriveOutputPaths,
  isProjectFile,
  parseProjectConfigLite,
} from "../src/qbs/projectDiscovery";

describe("projectDiscovery", () => {
  it("accepts qbstimeline.yml and qbstimeline.yaml", () => {
    assert.equal(isProjectFile("C:\\repo\\qbstimeline.yml"), true);
    assert.equal(isProjectFile("/repo/qbstimeline.yaml"), true);
    assert.equal(isProjectFile("/repo/schedule.py"), false);
  });

  it("prefers the active project file", async () => {
    const picked = await chooseProjectFile({
      activeFile: "/repo/examples/basic-transmon/qbstimeline.yml",
      discoveredFiles: ["/repo/examples/two-qubit-entangling/qbstimeline.yml"],
      choose: async () => {
        throw new Error("quick pick should not be used");
      },
    });

    assert.equal(picked, "/repo/examples/basic-transmon/qbstimeline.yml");
  });

  it("uses quick pick when multiple project files exist", async () => {
    const picked = await chooseProjectFile({
      activeFile: "/repo/README.md",
      discoveredFiles: ["/repo/a/qbstimeline.yml", "/repo/b/qbstimeline.yml"],
      choose: async (items) => items[1].path,
    });

    assert.equal(picked, "/repo/b/qbstimeline.yml");
  });

  it("parses output and schedule settings from YAML", () => {
    const config = parseProjectConfigLite(`
schedule:
  file: schedule.py
outputs:
  dir: .qbs_timeline
`);

    assert.equal(config.scheduleFile, "schedule.py");
    assert.equal(config.outputDir, ".qbs_timeline");
  });

  it("parses generated schedule source notebook metadata", () => {
    const config = parseProjectConfigLite(`
schedule:
  file: schedule.py
source:
  notebook: examples/050_qubit_spectroscopy.ipynb
outputs:
  dir: .qbs_timeline
`);

    assert.equal(config.scheduleFile, "schedule.py");
    assert.equal(config.sourceNotebook, "examples/050_qubit_spectroscopy.ipynb");
  });

  it("parses direct notebook schedule metadata", () => {
    const config = parseProjectConfigLite(`
schedule:
  notebook: experiments/tuneup.ipynb
  setup_tags:
    - qbstimeline-setup
  schedule_tag: qbstimeline-schedule
  schedule_variable: two_tone_sched
  compiler_variable: hw_agent
outputs:
  dir: .qbs_timeline
`);

    assert.equal(config.scheduleFile, undefined);
    assert.equal(config.scheduleNotebook, "experiments/tuneup.ipynb");
    assert.deepEqual(config.setupTags, ["qbstimeline-setup"]);
    assert.equal(config.scheduleTag, "qbstimeline-schedule");
    assert.equal(config.scheduleVariable, "two_tone_sched");
    assert.equal(config.compilerVariable, "hw_agent");
  });

  it("derives generated artifact paths relative to the project file", () => {
    const paths = deriveOutputPaths({
      projectFile: "C:\\repo\\examples\\two-qubit-entangling\\qbstimeline.yml",
      scheduleFile: "schedule.py",
      outputDir: ".qbs_timeline",
      overrideOutputDir: null,
    });

    assert.equal(
      normalize(paths.irPath),
      normalize("C:\\repo\\examples\\two-qubit-entangling\\.qbs_timeline\\qbs_ir.json"),
    );
    assert.equal(
      normalize(paths.htmlPath),
      normalize("C:\\repo\\examples\\two-qubit-entangling\\.qbs_timeline\\index.html"),
    );
    assert.equal(
      normalize(paths.q1asmDir),
      normalize("C:\\repo\\examples\\two-qubit-entangling\\.qbs_timeline\\q1asm"),
    );
    assert.equal(
      normalize(paths.schedulePath ?? ""),
      normalize("C:\\repo\\examples\\two-qubit-entangling\\schedule.py"),
    );
  });

  it("uses output directory override relative to the project file", () => {
    const paths = deriveOutputPaths({
      projectFile: join("C:\\repo", "qbstimeline.yml"),
      scheduleFile: "schedule.py",
      outputDir: ".qbs_timeline",
      overrideOutputDir: "tmp\\qbs-out",
    });

    assert.equal(normalize(paths.irPath), normalize("C:\\repo\\tmp\\qbs-out\\qbs_ir.json"));
  });

  it("derives notebook paths relative to the project file", () => {
    const paths = deriveOutputPaths({
      projectFile: join("C:\\repo", "qbstimeline.yml"),
      scheduleFile: "schedule.py",
      scheduleNotebook: "experiments\\tuneup.ipynb",
      sourceNotebook: "examples\\source.ipynb",
      outputDir: ".qbs_timeline",
      overrideOutputDir: null,
    });

    assert.equal(normalize(paths.schedulePath ?? ""), normalize("C:\\repo\\schedule.py"));
    assert.equal(normalize(paths.scheduleNotebookPath ?? ""), normalize("C:\\repo\\experiments\\tuneup.ipynb"));
    assert.equal(normalize(paths.sourceNotebookPath ?? ""), normalize("C:\\repo\\examples\\source.ipynb"));
  });

  it("preserves absolute schedule and output paths", () => {
    const paths = deriveOutputPaths({
      projectFile: join("C:\\repo", "qbstimeline.yml"),
      scheduleFile: "C:\\shared\\schedule.py",
      outputDir: "C:\\tmp\\qbs-out",
      overrideOutputDir: null,
    });

    assert.equal(normalize(paths.schedulePath ?? ""), normalize("C:\\shared\\schedule.py"));
    assert.equal(normalize(paths.outputDir), normalize("C:\\tmp\\qbs-out"));
  });
});
