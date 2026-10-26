import assert from "node:assert/strict";
import { parse } from "yaml";
import { readProjectExternalInputs, writeProjectExternalInputs } from "../src/q1timeline/externalInputs";
import { parseWebviewMessage } from "../src/q1timeline/webviewMessageValidation";

const trigger = { id: "shot_start", address: "TRIGGER_INDEX", targets: ["drive", "readout"], times_ns: [1000] };

describe("shared external inputs", () => {
  it("accepts shared inputs, an empty schedule, reset and save", () => {
    for (const triggers of [[], [trigger], [{ ...trigger, address: 15, times_ns: [] }]]) {
      const message = { type: "setExternalInputs", triggers };
      assert.deepEqual(parseWebviewMessage(message), { valid: true, ...message });
    }
    for (const type of ["saveExternalInputs", "resetExternalInputs"]) {
      assert.deepEqual(parseWebviewMessage({ type }), { valid: true, type });
      assert.equal(parseWebviewMessage({ type, extra: true }).valid, false);
    }
  });

  it("rejects ambiguous targets, addresses and event times", () => {
    for (const invalid of [
      { id: " " }, { address: 0 }, { address: 16 }, { address: "TRIGGER_INDEX+1" },
      { targets: [] }, { targets: ["drive", "drive"] }, { targets: [""] },
      { times_ns: [-1] }, { times_ns: [4, 4] }, { times_ns: [8, 4] },
      { times_ns: [1.5] }, { times_ns: [Number.MAX_SAFE_INTEGER + 1] }, { resolved_address: 1 },
    ]) assert.equal(parseWebviewMessage({ type: "setExternalInputs", triggers: [{ ...trigger, ...invalid }] }).valid, false);
    assert.equal(parseWebviewMessage({ type: "setExternalInputs", triggers: [trigger, trigger] }).valid, false);
  });

  it("preserves unrelated project data and comments, adding only external inputs", () => {
    const source = "# experiment\nversion: 1 # keep version\nsequencers: []\n";
    const { baseline } = readProjectExternalInputs(source);
    const output = writeProjectExternalInputs(source, { triggers: [trigger] }, baseline);
    assert.match(output, /# experiment/);
    assert.match(output, /version: 1 # keep version/);
    assert.deepEqual(parse(output), { version: 1, sequencers: [], external_inputs: { triggers: [trigger] } });
  });

  it("keeps unrelated concurrent edits and rejects concurrent external-input changes", () => {
    const source = "version: 1\nexternal_inputs:\n  # schedule\n  triggers: []\n";
    const { baseline } = readProjectExternalInputs(source);
    const output = writeProjectExternalInputs(source + "title: changed\n", { triggers: [trigger] }, baseline);
    assert.match(output, /# schedule/);
    assert.equal(parse(output).title, "changed");
    assert.throws(() => writeProjectExternalInputs(output, { triggers: [] }, baseline), /changed on disk/);
  });

  it("saves inputs over a null or blank YAML value while keeping its comments", () => {
    for (const value of ["null", ""]) {
      const source = `sequencers: []\nexternal_inputs: ${value} # input context\n`;
      const { baseline } = readProjectExternalInputs(source);
      const output = writeProjectExternalInputs(source, { triggers: [trigger] }, baseline);
      assert.match(output, /# input context/);
      assert.deepEqual(parse(output).external_inputs, { triggers: [trigger] });
    }
  });
});
