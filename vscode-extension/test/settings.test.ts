import assert from "node:assert/strict";
import { readMergedSetting } from "../src/settings";
import { q1timelineConfigValue } from "../src/q1timeline/settings";

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
  function config(values: Record<string, unknown>) {
    return {
      get<T>(key: string, fallback: T): T {
        return Object.prototype.hasOwnProperty.call(values, key) ? (values[key] as T) : fallback;
      },
    };
  }

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
});
