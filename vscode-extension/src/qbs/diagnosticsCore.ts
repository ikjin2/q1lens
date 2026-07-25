import { Q1TimelineDiagnostic, QbsIr, QbsOperation, QbsSymbolicBlock } from "./qbsIr";

export type QbsDiagnosticSeverity = "error" | "warning" | "information" | "hint";
export type QbsDiagnosticCode = "QBST001" | "QBST002" | "QBST003" | "QBST004" | "QBST005" | "QBST006" | "QBST007" | string;

export interface QbsDiagnostic {
  code: QbsDiagnosticCode;
  severity: QbsDiagnosticSeverity;
  message: string;
  file?: string;
  line?: number;
  operationId?: string;
  blockId?: string;
  sequencer?: string;
  source?: "qbsTimeline" | "q1timeline" | string;
}

export interface DiagnosticContext {
  outputDir: string;
  existingFiles: Set<string>;
}

interface SpanNs {
  start: number;
  end: number;
}

const QBS_TO_Q1TIMELINE_NS = 1e9;
const CROSS_LAYER_TOLERANCE_NS = 0.001;
const IGNORED_CROSS_LAYER_EVENT_KINDS = new Set([
  "branch_region",
  "loop_block",
  "loop_iteration_preview",
  "q1_issue",
  "queue_depth",
  "slack",
  "stop",
  "underflow_warning",
  "unknown_region",
]);

function endTime(item: { abs_time: number; duration: number }): number {
  return item.abs_time + item.duration;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function findOperation(ir: QbsIr, block: QbsSymbolicBlock): QbsOperation | undefined {
  return ir.operations.find((operation) => operation.id === block.operation_id || operation.operation_id === block.operation_id);
}

function q1asmText(ir: QbsIr, sequencer: string): string {
  const program = q1asmProgram(ir, sequencer);
  for (const candidate of [sequencer, program?.sequencer, program?.sequencer_id]) {
    if (candidate && ir.q1asm_by_sequencer?.[candidate]) {
      return ir.q1asm_by_sequencer[candidate];
    }
  }
  return program?.text ?? "";
}

function q1asmProgram(ir: QbsIr, sequencer: string): QbsIr["q1asm_programs"][number] | undefined {
  return ir.q1asm_programs.find((program) => program.sequencer === sequencer || program.sequencer_id === sequencer);
}

function q1asmLines(text: string): string[] {
  return text.split(/\r?\n/).filter((line, index, array) => index < array.length - 1 || line.length > 0);
}

function q1asmOpcode(line: string): string {
  return line.trimStart().split(/[,\s#]+/, 1)[0] ?? "";
}

function q1asmOpcodeMatches(line: string, expected: string): boolean {
  const opcode = q1asmOpcode(line);
  return opcode === expected || (expected === "acquire" && opcode.startsWith("acquire_"));
}

function provenanceLine(mapping: QbsIr["q1asm_provenance"][number]): number | undefined {
  if (typeof mapping.line === "number") {
    return mapping.line;
  }
  return typeof mapping.q1asm_line_start === "number" ? mapping.q1asm_line_start : undefined;
}

function provenanceLineRange(mapping: QbsIr["q1asm_provenance"][number]): { start: number; end: number } | undefined {
  if (typeof mapping.q1asm_line_start === "number") {
    const end = typeof mapping.q1asm_line_end === "number" ? mapping.q1asm_line_end : mapping.q1asm_line_start;
    return { start: mapping.q1asm_line_start, end };
  }
  if (typeof mapping.line === "number") {
    return { start: mapping.line, end: mapping.line };
  }
  return undefined;
}

function provenanceInstructions(mapping: QbsIr["q1asm_provenance"][number]): string[] {
  const instructions: string[] = [];
  if (mapping.instruction) {
    instructions.push(mapping.instruction);
  }
  for (const role of mapping.instruction_roles ?? []) {
    if (typeof role === "string" && role.length > 0 && !instructions.includes(role)) {
      instructions.push(role);
    }
  }
  return instructions;
}

function q1timelineSeverity(severity: string | undefined): QbsDiagnosticSeverity {
  if (severity === "error" || severity === "warning" || severity === "hint") {
    return severity;
  }
  return "information";
}

function q1timelineDiagnosticCode(diagnostic: Q1TimelineDiagnostic): string {
  return typeof diagnostic.category === "string" && diagnostic.category.length > 0
    ? diagnostic.category
    : "q1timeline";
}

function q1timelineDiagnosticMessage(diagnostic: Q1TimelineDiagnostic): string {
  if (typeof diagnostic.message === "string" && diagnostic.message.length > 0) {
    return diagnostic.message;
  }
  return q1timelineDiagnosticCode(diagnostic);
}

function sequencerAliases(ir: QbsIr, sequencer: string): Set<string> {
  const aliases = new Set([sequencer]);
  const program = q1asmProgram(ir, sequencer);
  if (program?.sequencer) {
    aliases.add(program.sequencer);
  }
  if (program?.sequencer_id) {
    aliases.add(program.sequencer_id);
  }
  return aliases;
}

function provenanceMatchesBlock(mapping: QbsIr["q1asm_provenance"][number], block: QbsSymbolicBlock): boolean {
  if (mapping.source_id) {
    return mapping.source_id === block.id;
  }
  const candidates = new Set(
    [block.id, block.operation_id, block.schedulable_id].filter((value): value is string => typeof value === "string" && value.length > 0),
  );
  return Boolean(
    (mapping.operation_id && candidates.has(mapping.operation_id))
    || (mapping.schedulable_id && candidates.has(mapping.schedulable_id)),
  );
}

function concreteTimelineValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (isRecord(value) && typeof value.value === "number" && Number.isFinite(value.value)) {
    return value.value;
  }
  return undefined;
}

function timelineEvents(ir: QbsIr): Record<string, unknown>[] {
  const events = isRecord(ir.q1timeline_ir) ? ir.q1timeline_ir.events : undefined;
  return Array.isArray(events) ? events.filter(isRecord) : [];
}

function timelineEventLine(event: Record<string, unknown>): number | undefined {
  const source = event.source;
  if (!isRecord(source) || typeof source.line !== "number" || !Number.isFinite(source.line)) {
    return undefined;
  }
  return source.line;
}

function timelineEventSequencer(event: Record<string, unknown>): string | undefined {
  return typeof event.sequencer_id === "string"
    ? event.sequencer_id
    : (typeof event.sequencer === "string" ? event.sequencer : undefined);
}

function timelineEventRaw(event: Record<string, unknown>): string {
  const source = event.source;
  return isRecord(source) && typeof source.raw === "string" ? source.raw : "";
}

function timelineEventKind(event: Record<string, unknown>): string {
  return typeof event.kind === "string" ? event.kind : "";
}

function timelineEventSpan(event: Record<string, unknown>): SpanNs | undefined {
  const start = concreteTimelineValue(event.t0);
  const end = concreteTimelineValue(event.t1);
  if (start === undefined || end === undefined || end < start) {
    return undefined;
  }
  return { start, end };
}

function timelineEventMatchesInstructions(event: Record<string, unknown>, instructions: string[]): boolean {
  const kind = timelineEventKind(event);
  if (IGNORED_CROSS_LAYER_EVENT_KINDS.has(kind)) {
    return false;
  }
  if (!instructions.length) {
    return true;
  }
  const raw = timelineEventRaw(event);
  return instructions.some((instruction) => {
    if (kind === instruction || (instruction === "acquire" && kind.startsWith("acquire"))) {
      return true;
    }
    return raw.length > 0 && q1asmOpcodeMatches(raw, instruction);
  });
}

function q1timelineSpanForMapping(ir: QbsIr, mapping: QbsIr["q1asm_provenance"][number]): SpanNs | undefined {
  if (!mapping.sequencer) {
    return undefined;
  }
  const lineRange = provenanceLineRange(mapping);
  if (!lineRange) {
    return undefined;
  }
  const aliases = sequencerAliases(ir, mapping.sequencer);
  const instructions = provenanceInstructions(mapping);
  const spans = timelineEvents(ir).flatMap((event) => {
    const sequencer = timelineEventSequencer(event);
    const line = timelineEventLine(event);
    const span = timelineEventSpan(event);
    if (
      !sequencer
      || !aliases.has(sequencer)
      || line === undefined
      || line < lineRange.start
      || line > lineRange.end
      || !span
      || !timelineEventMatchesInstructions(event, instructions)
    ) {
      return [];
    }
    return [span];
  });
  if (!spans.length) {
    return undefined;
  }
  return {
    start: Math.min(...spans.map((span) => span.start)),
    end: Math.max(...spans.map((span) => span.end)),
  };
}

function blockSpanNs(block: QbsSymbolicBlock): SpanNs {
  const start = block.abs_time * QBS_TO_Q1TIMELINE_NS;
  return {
    start,
    end: start + block.duration * QBS_TO_Q1TIMELINE_NS,
  };
}

function spanDuration(span: SpanNs): number {
  return span.end - span.start;
}

function spanDrifts(left: SpanNs, right: SpanNs): boolean {
  return (
    Math.abs(left.start - right.start) > CROSS_LAYER_TOLERANCE_NS
    || Math.abs(left.end - right.end) > CROSS_LAYER_TOLERANCE_NS
    || Math.abs(spanDuration(left) - spanDuration(right)) > CROSS_LAYER_TOLERANCE_NS
  );
}

function formatNs(value: number): string {
  const rounded = Math.round(value);
  if (Math.abs(value - rounded) < 0.000001) {
    return String(rounded);
  }
  return String(Number(value.toFixed(6)));
}

function spanLabel(span: SpanNs): string {
  return `${formatNs(span.start)}-${formatNs(span.end)} ns`;
}

function crossLayerDiagnostics(ir: QbsIr): QbsDiagnostic[] {
  if (!isRecord(ir.q1timeline_ir)) {
    return [];
  }
  const diagnostics: QbsDiagnostic[] = [];
  for (const block of ir.symbolic_pulses) {
    const mappings = ir.q1asm_provenance.filter((mapping) => provenanceMatchesBlock(mapping, block));
    for (const mapping of mappings) {
      const q1Span = q1timelineSpanForMapping(ir, mapping);
      if (!q1Span) {
        continue;
      }
      const qbsSpan = blockSpanNs(block);
      if (!spanDrifts(qbsSpan, q1Span)) {
        continue;
      }
      diagnostics.push({
        code: "QBST007",
        severity: "warning",
        message: (
          `Symbolic block ${block.id} spans QBS ${spanLabel(qbsSpan)}, `
          + `q1timeline ${spanLabel(q1Span)}.`
        ),
        blockId: block.id,
        operationId: block.operation_id,
        sequencer: mapping.sequencer,
        source: "qbsTimeline",
      });
      break;
    }
  }
  return diagnostics;
}

export function computeDiagnostics(ir: QbsIr, context: DiagnosticContext): QbsDiagnostic[] {
  const diagnostics: QbsDiagnostic[] = [];
  const symbolicValueIds = new Set(ir.symbolic_values.map((value) => value.id));

  for (const diagnostic of ir.ir_diagnostics ?? []) {
    diagnostics.push({
      code: `QBST-IR-${diagnostic.code}`,
      severity: diagnostic.severity,
      message: `${diagnostic.path}: ${diagnostic.message}`,
      source: "qbsTimeline",
    });
  }

  for (const warning of ir.warnings ?? []) {
    diagnostics.push({
      code: "QBST006",
      severity: "warning",
      message: warning,
    });
  }

  for (const block of ir.symbolic_pulses) {
    const operation = findOperation(ir, block);
    if (operation && (block.abs_time < operation.abs_time || endTime(block) > endTime(operation))) {
      diagnostics.push({
        code: "QBST001",
        severity: "error",
        message: `Symbolic block ${block.id} extends outside operation ${operation.id}.`,
        operationId: operation.id,
        blockId: block.id,
      });
    }

    if (block.duration_value_id && !symbolicValueIds.has(block.duration_value_id)) {
      diagnostics.push({
        code: "QBST004",
        severity: "warning",
        message: `Symbolic block ${block.id} references missing symbolic value ${block.duration_value_id}.`,
        blockId: block.id,
      });
    }
  }

  for (const mapping of ir.q1asm_provenance) {
    const lineRange = provenanceLineRange(mapping);
    const line = provenanceLine(mapping);
    const instructions = provenanceInstructions(mapping);
    if (!mapping.sequencer || !lineRange || typeof line !== "number" || !instructions.length) {
      continue;
    }

    const program = q1asmProgram(ir, mapping.sequencer);
    const lines = q1asmLines(q1asmText(ir, mapping.sequencer));

    if (lineRange.start < 1 || lineRange.end < lineRange.start || lineRange.end > lines.length) {
      diagnostics.push({
        code: "QBST002",
        severity: "error",
        message: `Provenance line ${lineRange.start} is outside Q1ASM program ${mapping.sequencer}.`,
        file: program?.file,
        line: lineRange.start,
        sequencer: mapping.sequencer,
      });
      continue;
    }

    const rangeLines = lines.slice(lineRange.start - 1, lineRange.end);
    for (const instruction of instructions) {
      if (!rangeLines.some((candidate) => q1asmOpcodeMatches(candidate, instruction))) {
        diagnostics.push({
          code: "QBST003",
          severity: "error",
          message: `Provenance expects ${instruction} in lines ${lineRange.start}-${lineRange.end}.`,
          file: program?.file,
          line: lineRange.start,
          sequencer: mapping.sequencer,
        });
      }
    }
  }

  for (const program of ir.q1asm_programs) {
    if (!context.existingFiles.has(program.file)) {
      diagnostics.push({
        code: "QBST005",
        severity: "warning",
        message: `Generated Q1ASM file is missing: ${program.file}.`,
        file: program.file,
        sequencer: program.sequencer,
      });
    }
  }

  diagnostics.push(...crossLayerDiagnostics(ir));

  for (const diagnostic of ir.q1timeline_diagnostics ?? []) {
    diagnostics.push({
      code: q1timelineDiagnosticCode(diagnostic),
      severity: q1timelineSeverity(diagnostic.severity),
      message: q1timelineDiagnosticMessage(diagnostic),
      file: diagnostic.source?.file,
      line: diagnostic.source?.line,
      source: "q1timeline",
    });
  }

  return diagnostics;
}
