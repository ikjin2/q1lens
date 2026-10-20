import assert from "node:assert/strict";
import { readMergedSetting } from "../src/settings";
import { q1timelineConfigValue } from "../src/q1timeline/settings";
import { explicitConfigValue, readSharedPythonSettings } from "../src/qbs/settingsCore";

function config(values: Record<string, unknown>, defaults: Record<string, unknown> = {}) {
  return {
    get<T>(key: string, fallback: T): T {
      if (Object.prototype.hasOwnProperty.call(values, key)) {
        return values[key] as T;
      }
      return Object.prototype.hasOwnProperty.call(defaults, key) ? (defaults[key] as T) : fallback;
    },
    inspect<T>(key: string) {
      return { defaultValue: defaults[key] as T | undefined, workspaceValue: values[key] as T | undefined };
    },
  };
}

const pythonDefaults = { pythonPath: "python", pythonArgs: [] };
const q1lensDefaults = { ...pythonDefaults, "q1timeline.pythonPath": "python", "q1timeline.pythonArgs": [] };

describe("merged settings", () => {
  it("prefers q1lens settings over qbloxTimeline and legacy settings", () => {
    const value = readMergedSetting(
      {
        q1lens: { pythonPath: "py-q1lens" },
        qbloxTimeline: { pythonPath: "py-new" },
        qbsTimeline: { pythonPath: "py-old" },
      },
      ["pythonPath"],
      "python",
    );

    assert.equal(value, "py-q1lens");
  });

  it("falls back to qbloxTimeline settings before legacy settings", () => {
    const value = readMergedSetting(
      {
        qbloxTimeline: { pythonPath: "py-new" },
        qbsTimeline: { pythonPath: "py-old" },
      },
      ["pythonPath"],
      "python",
    );

    assert.equal(value, "py-new");
  });

  it("falls back to q1timeline legacy settings", () => {
    const value = readMergedSetting(
      {
        qbloxTimeline: {},
        q1timeline: { projectFile: "custom.q1timeline.yml" },
      },
      ["q1timeline", "projectFile"],
      "q1timeline.yml",
    );

    assert.equal(value, "custom.q1timeline.yml");
  });
});

describe("q1timeline settings bridge", () => {
  it("preserves legacy q1timeline Python path before the shared fallback", () => {
    const value = q1timelineConfigValue(
      config({ pythonPath: "py-qblox" }),
      config({ pythonPath: "py-legacy" }),
      "pythonPath",
      "python",
    );

    assert.equal(value, "py-legacy");
  });

  it("falls back to shared qbloxTimeline Python path when no q1timeline setting exists", () => {
    const value = q1timelineConfigValue(
      config({ pythonPath: "py-qblox" }),
      config({}),
      "pythonPath",
      "python",
    );

    assert.equal(value, "py-qblox");
  });

  it("prefers explicit qbloxTimeline q1timeline overrides over legacy q1timeline settings", () => {
    const value = q1timelineConfigValue(
      config({ "q1timeline.pythonPath": "py-integrated", pythonPath: "py-qblox" }),
      config({ pythonPath: "py-legacy" }),
      "pythonPath",
      "python",
    );

    assert.equal(value, "py-integrated");
  });

  it("prefers explicit q1lens q1timeline overrides over qbloxTimeline settings", () => {
    const value = q1timelineConfigValue(
      config({ "q1timeline.pythonPath": "py-qblox-integrated", pythonPath: "py-qblox" }),
      config({ pythonPath: "py-legacy" }),
      "pythonPath",
      "python",
      config({ "q1timeline.pythonPath": "py-q1lens-integrated", pythonPath: "py-q1lens" }),
    );

    assert.equal(value, "py-q1lens-integrated");
  });

  it("uses shared Python settings despite registered Q1ASM and legacy defaults", () => {
    const primary = config({ pythonPath: "py-shared", pythonArgs: ["-3.12"] }, q1lensDefaults);
    const qblox = config({}, pythonDefaults);
    const legacy = config({}, pythonDefaults);

    assert.equal(q1timelineConfigValue(qblox, legacy, "pythonPath", "python", primary), "py-shared");
    assert.deepEqual(q1timelineConfigValue(qblox, legacy, "pythonArgs", [], primary), ["-3.12"]);
  });

  it("honors an explicit Q1ASM override even when it equals its registered default", () => {
    const primary = config({
      pythonPath: "py-shared",
      pythonArgs: ["-3.12"],
      "q1timeline.pythonPath": "python",
      "q1timeline.pythonArgs": [],
    }, q1lensDefaults);

    assert.equal(q1timelineConfigValue(config({}), config({}), "pythonPath", "python", primary), "python");
    assert.deepEqual(q1timelineConfigValue(config({}), config({}), "pythonArgs", [], primary), []);
  });

  it("preserves explicit legacy overrides ahead of shared settings despite registered defaults", () => {
    const primary = config({ pythonPath: "py-shared", pythonArgs: ["-3.12"] }, q1lensDefaults);
    const legacy = config({ pythonPath: "py-legacy", pythonArgs: ["-3.11"] }, pythonDefaults);

    assert.equal(q1timelineConfigValue(config({}, pythonDefaults), legacy, "pythonPath", "python", primary), "py-legacy");
    assert.deepEqual(q1timelineConfigValue(config({}, pythonDefaults), legacy, "pythonArgs", [], primary), ["-3.11"]);
  });

  it("honors legacy refresh mode despite registered primary defaults", () => {
    assert.equal(q1timelineConfigValue(
      config({}),
      config({ updateMode: "manual" }, { updateMode: "onSave" }),
      "updateMode",
      "onSave",
      config({}, { "q1timeline.updateMode": "onSave" }),
    ), "manual");
  });

  it("honors a qblox project filename despite registered primary defaults", () => {
    assert.equal(q1timelineConfigValue(
      config({ "q1timeline.projectFile": "experiment.yml" }, { "q1timeline.projectFile": "q1timeline.yml" }),
      config({ projectFile: "legacy.yml" }, { projectFile: "q1timeline.yml" }),
      "projectFile",
      "q1timeline.yml",
      config({}, { "q1timeline.projectFile": "q1timeline.yml" }),
    ), "experiment.yml");
  });

  it("prefers an explicit primary refresh mode even when it equals the default", () => {
    assert.equal(q1timelineConfigValue(
      config({ "q1timeline.updateMode": "onType" }),
      config({ updateMode: "manual" }),
      "updateMode",
      "onSave",
      config({ "q1timeline.updateMode": "onSave" }, { "q1timeline.updateMode": "onSave" }),
    ), "onSave");
  });
});

describe("shared Python settings", () => {
  it("does not let registered defaults hide explicit legacy settings", () => {
    assert.deepEqual(readSharedPythonSettings(
      config({}, pythonDefaults),
      config({}, pythonDefaults),
      config({ pythonPath: "py-legacy", pythonArgs: ["-3.11"] }, pythonDefaults),
    ), { pythonPath: "py-legacy", pythonArgs: ["-3.11"] });
  });

  it("prefers explicit primary settings, including empty launcher arguments", () => {
    assert.deepEqual(readSharedPythonSettings(
      config({ pythonPath: "python", pythonArgs: [] }, pythonDefaults),
      config({ pythonPath: "py-qblox", pythonArgs: ["-3.12"] }, pythonDefaults),
      config({ pythonPath: "py-legacy", pythonArgs: ["-3.11"] }, pythonDefaults),
    ), { pythonPath: "python", pythonArgs: [] });
  });

  it("prefers explicit qblox settings to qbs legacy settings", () => {
    assert.deepEqual(readSharedPythonSettings(
      config({}, pythonDefaults),
      config({ pythonPath: "py-qblox", pythonArgs: ["-3.12"] }, pythonDefaults),
      config({ pythonPath: "py-legacy", pythonArgs: ["-3.11"] }, pythonDefaults),
    ), { pythonPath: "py-qblox", pythonArgs: ["-3.12"] });
  });

  it("uses registered defaults when nothing is configured", () => {
    assert.deepEqual(readSharedPythonSettings(
      config({}, pythonDefaults), config({}, pythonDefaults), config({}, pythonDefaults),
    ), { pythonPath: "python", pythonArgs: [] });
  });

  it("recognizes user and workspace folder values as explicit", () => {
    for (const scope of ["globalValue", "workspaceFolderValue", "globalLanguageValue", "workspaceLanguageValue", "workspaceFolderLanguageValue"]) {
      const scopedConfig = {
        get<T>(_key: string, _fallback: T): T { return "py-scoped" as T; },
        inspect<T>(_key: string) { return { [scope]: "py-scoped" as T }; },
      };
      assert.equal(explicitConfigValue(scopedConfig, "pythonPath"), "py-scoped", scope);
    }
  });
});
