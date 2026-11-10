import { isMap, isScalar, parseDocument } from "yaml";

export interface ExternalTrigger {
  id: string;
  address: number | string;
  targets: string[];
  times_ns: number[];
}

export interface ExternalFeedback {
  target: string;
  arrivals: { time_ns: number; id: number; value: number }[];
}

export interface ExternalInputs { triggers: ExternalTrigger[]; feedback?: ExternalFeedback[]; }

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

export function validExternalFeedback(value: unknown): value is ExternalFeedback[] {
  if (!Array.isArray(value)) return false;
  const targets = new Set<string>();
  return value.every((receiver) => {
    if (!receiver || typeof receiver !== "object" || Array.isArray(receiver) ||
        Object.keys(receiver).some((key) => !["target", "arrivals"].includes(key)) ||
        !nonemptyString(receiver.target) || targets.has(receiver.target) || !Array.isArray(receiver.arrivals) ||
        !receiver.arrivals.every((entry: any) => entry && typeof entry === "object" && !Array.isArray(entry) &&
          Object.keys(entry).every((key) => ["time_ns", "id", "value"].includes(key)) &&
          Number.isSafeInteger(entry.time_ns) && entry.time_ns >= 0 &&
          Number.isInteger(entry.id) && entry.id >= 1 && entry.id <= 255 &&
          Number.isInteger(entry.value) && entry.value >= -(2 ** 31) && entry.value <= 2 ** 32 - 1)) return false;
    targets.add(receiver.target);
    return true;
  });
}

export function rawExternalInputs(value: ExternalInputs): ExternalInputs {
  return {
    triggers: value.triggers.map(({ id, address, targets, times_ns }) => ({id, address, targets: [...targets], times_ns: [...times_ns]})),
    ...(value.feedback?.length ? {feedback: value.feedback.map(({target, arrivals}) => ({
      target, arrivals: arrivals.map(({time_ns, id, value}) => ({time_ns, id, value})),
    }))} : {}),
  };
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
  const feedback = inputs?.feedback === undefined ? [] : inputs.feedback;
  if (!inputs || typeof inputs !== "object" || Array.isArray(inputs) ||
      Object.keys(inputs).some((key) => !["triggers", "feedback"].includes(key)) ||
      !validExternalTriggers(triggers) || !validExternalFeedback(feedback)) {
    throw new Error("Invalid project external_inputs: check trigger sources and LINQ receiver schedules.");
  }
  return { inputs: rawExternalInputs({ triggers, feedback }), baseline: JSON.stringify(source) };
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
  const raw = rawExternalInputs(inputs);
  document.setIn(["external_inputs", "triggers"], raw.triggers);
  if (raw.feedback) document.setIn(["external_inputs", "feedback"], raw.feedback);
  else document.deleteIn(["external_inputs", "feedback"]);
  const output = document.toString();
  return text.includes("\r\n") ? output.replace(/\r?\n/g, "\r\n") : output;
}
