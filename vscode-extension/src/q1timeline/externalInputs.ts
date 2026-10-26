import { isMap, isScalar, parseDocument } from "yaml";

export interface ExternalTrigger {
  id: string;
  address: number | string;
  targets: string[];
  times_ns: number[];
}

export interface ExternalInputs { triggers: ExternalTrigger[]; }

const identifier = /^[A-Za-z_][A-Za-z0-9_]*$/;
const nonemptyString = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;

export function validExternalTriggers(value: unknown): value is ExternalTrigger[] {
  if (!Array.isArray(value)) return false;
  const ids = new Set<string>();
  return value.every((trigger) => {
    if (!trigger || typeof trigger !== "object" || Array.isArray(trigger) ||
        Object.keys(trigger).some((key) => !["id", "address", "targets", "times_ns"].includes(key)) ||
        !nonemptyString(trigger.id) || trigger.id.length > 128 || ids.has(trigger.id) ||
        !(Number.isInteger(trigger.address) && trigger.address >= 1 && trigger.address <= 15 ||
          typeof trigger.address === "string" && identifier.test(trigger.address)) ||
        !Array.isArray(trigger.targets) || !trigger.targets.length || !trigger.targets.every(nonemptyString) ||
        new Set(trigger.targets).size !== trigger.targets.length ||
        !Array.isArray(trigger.times_ns) || !trigger.times_ns.every((time: unknown, index: number) =>
          Number.isSafeInteger(time) && Number(time) >= 0 && (index === 0 || Number(time) > trigger.times_ns[index - 1]))) return false;
    ids.add(trigger.id);
    return true;
  });
}

export function rawExternalInputs(value: { triggers: ExternalTrigger[] }): ExternalInputs {
  return { triggers: value.triggers.map(({ id, address, targets, times_ns }) => ({
    id, address, targets: [...targets], times_ns: [...times_ns],
  })) };
}

function projectDocument(text: string) {
  const document = parseDocument(text);
  if (document.errors.length || !isMap(document.contents)) {
    throw new Error(document.errors[0]?.message || "The project must be a YAML mapping.");
  }
  return document;
}

export function readProjectExternalInputs(text: string) {
  const document = projectDocument(text);
  const source = document.toJS().external_inputs;
  const inputs = source == null ? { triggers: [] } : source;
  const triggers = inputs?.triggers === undefined ? [] : inputs.triggers;
  if (!inputs || typeof inputs !== "object" || Array.isArray(inputs) ||
      Object.keys(inputs).some((key) => key !== "triggers") || !validExternalTriggers(triggers)) {
    throw new Error("Invalid project external_inputs: expected triggers with an address, targets and increasing times_ns.");
  }
  return { inputs: rawExternalInputs({ triggers }), baseline: JSON.stringify(source) };
}

export function writeProjectExternalInputs(text: string, inputs: ExternalInputs, baseline: string | undefined) {
  if (readProjectExternalInputs(text).baseline !== baseline) {
    throw new Error("Project external inputs changed on disk. Reset to project before saving again.");
  }
  const document = projectDocument(text);
  const previous = document.get("external_inputs", true);
  if (isScalar(previous) && previous.value === null) {
    const mapping = document.createNode({});
    mapping.commentBefore = previous.commentBefore;
    mapping.comment = previous.comment;
    document.set("external_inputs", mapping);
  }
  document.setIn(["external_inputs", "triggers"], rawExternalInputs(inputs).triggers);
  const output = document.toString();
  return text.includes("\r\n") ? output.replace(/\r?\n/g, "\r\n") : output;
}
