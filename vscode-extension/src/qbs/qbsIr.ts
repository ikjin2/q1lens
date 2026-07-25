export interface QbsOperation {
  id: string;
  label: string;
  abs_time: number;
  duration: number;
  operation_id?: string;
  parent_control_flow_id?: string;
  depth?: number;
}

export interface QbsControlFlowBlock {
  id: string;
  kind: "loop" | string;
  label: string;
  abs_time: number;
  duration: number;
  preview_abs_time?: number;
  preview_duration?: number;
  duration_kind?: string;
  preview_kind?: string;
  operation_id?: string;
  schedulable_id?: string;
  parent_control_flow_id?: string;
  depth?: number;
  repetitions?: number;
  body_operation_count?: number;
  iteration?: QbsIterationMetadata;
}

export interface QbsIterationMetadata {
  kind?: string;
  variable?: string;
  variables?: string[];
  source?: string;
  count?: number;
}

export interface QbsSymbolicValue {
  id: string;
  label: string;
  value: number;
  unit?: string | null;
  kind?: string;
}

export interface QbsSymbolicBlock {
  id: string;
  operation_id?: string;
  schedulable_id?: string;
  lane: string;
  kind: "pulse" | "acquisition" | string;
  label: string;
  display_label?: string;
  display_subtitle?: string;
  abs_time: number;
  duration: number;
  duration_value_id?: string;
  parameters?: Record<string, unknown>;
}

export interface QbsQ1asmProgram {
  sequencer: string;
  sequencer_id?: string;
  file: string;
  path?: string[];
  text?: string;
}

export interface QbsProvenanceMapping {
  sequencer?: string;
  sequencer_id?: string;
  line?: number;
  instruction?: string;
  instruction_roles?: string[];
  q1asm_line_start?: number;
  q1asm_line_end?: number;
  operation_id?: string;
  schedulable_id?: string;
  source_id?: string;
  source_kind?: string;
  symbolic_value_id?: string;
  confidence?: string;
  inference_reason?: string;
  operand_mappings?: Array<{
    instruction?: string;
    line?: number;
    source_value_id?: string;
    source_expression?: string;
    [key: string]: unknown;
  }>;
  expression?: string;
}

export interface Q1TimelineDiagnostic {
  category?: string;
  message?: string;
  severity?: string;
  source?: {
    file?: string;
    line?: number;
    column?: number;
    raw?: string;
  };
  [key: string]: unknown;
}

export interface QbsIrDiagnostic {
  code: string;
  path: string;
  message: string;
  severity: "error" | "warning" | "information" | "hint";
}

export interface QbsNotebookSourceLocation {
  file: string;
  cell_index: number;
  cell_id?: string;
  cell_line?: number;
}

export interface QbsSourceLocation {
  kind?: "python" | "notebook" | string;
  file?: string;
  line?: number;
  column?: number;
  label?: string;
  notebook?: QbsNotebookSourceLocation;
  generated_file?: string;
  generated_line?: number;
}

export interface QbsSourceMap {
  primary?: QbsSourceLocation;
  schedulables?: Record<string, QbsSourceLocation>;
}

export interface QbsIr {
  schedule?: { name?: string };
  status?: string;
  operations: QbsOperation[];
  control_flow_blocks?: QbsControlFlowBlock[];
  symbolic_values: QbsSymbolicValue[];
  symbolic_pulses: QbsSymbolicBlock[];
  q1asm_programs: QbsQ1asmProgram[];
  q1asm_by_sequencer?: Record<string, string>;
  q1asm_provenance: QbsProvenanceMapping[];
  q1timeline_ir?: Record<string, unknown>;
  q1timeline_diagnostics?: Q1TimelineDiagnostic[];
  ir_diagnostics?: QbsIrDiagnostic[];
  source_map?: QbsSourceMap;
  project?: { root?: string; low_level_q1timeline?: boolean };
  warnings?: string[];
  capabilities?: Record<string, boolean>;
  artifacts?: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireArray(value: Record<string, unknown>, key: string): unknown[] {
  const arrayValue = value[key];
  if (!Array.isArray(arrayValue)) {
    throw new Error(`QBS IR must contain ${key}[]`);
  }
  return arrayValue;
}

function readString(value: Record<string, unknown>, key: string, fallback = ""): string {
  return typeof value[key] === "string" ? value[key] : fallback;
}

function readNumber(value: Record<string, unknown>, key: string): number {
  const raw = value[key];
  if (typeof raw !== "number" || Number.isNaN(raw)) {
    throw new Error(`QBS IR field ${key} must be a number`);
  }
  return raw;
}

function parseOperation(value: unknown): QbsOperation {
  if (!isRecord(value)) {
    throw new Error("QBS IR operations[] entries must be objects");
  }

  return {
    id: readString(value, "id"),
    label: readString(value, "label"),
    abs_time: readNumber(value, "abs_time"),
    duration: readNumber(value, "duration"),
    ...(typeof value.operation_id === "string" ? { operation_id: value.operation_id } : {}),
    ...(typeof value.parent_control_flow_id === "string" ? { parent_control_flow_id: value.parent_control_flow_id } : {}),
    ...(typeof value.depth === "number" && Number.isFinite(value.depth) ? { depth: value.depth } : {}),
  };
}

function parseControlFlowBlock(value: unknown): QbsControlFlowBlock {
  if (!isRecord(value)) {
    throw new Error("QBS IR control_flow_blocks[] entries must be objects");
  }

  return {
    id: readString(value, "id"),
    kind: readString(value, "kind", "loop"),
    label: readString(value, "label"),
    abs_time: readNumber(value, "abs_time"),
    duration: readNumber(value, "duration"),
    ...(typeof value.preview_abs_time === "number" && Number.isFinite(value.preview_abs_time)
      ? { preview_abs_time: value.preview_abs_time }
      : {}),
    ...(typeof value.preview_duration === "number" && Number.isFinite(value.preview_duration)
      ? { preview_duration: value.preview_duration }
      : {}),
    ...(typeof value.duration_kind === "string" ? { duration_kind: value.duration_kind } : {}),
    ...(typeof value.preview_kind === "string" ? { preview_kind: value.preview_kind } : {}),
    ...parseIteration(value.iteration),
    ...(typeof value.operation_id === "string" ? { operation_id: value.operation_id } : {}),
    ...(typeof value.schedulable_id === "string" ? { schedulable_id: value.schedulable_id } : {}),
    ...(typeof value.parent_control_flow_id === "string" ? { parent_control_flow_id: value.parent_control_flow_id } : {}),
    ...(typeof value.depth === "number" && Number.isFinite(value.depth) ? { depth: value.depth } : {}),
    ...(typeof value.repetitions === "number" && Number.isFinite(value.repetitions) ? { repetitions: value.repetitions } : {}),
    ...(typeof value.body_operation_count === "number" && Number.isFinite(value.body_operation_count)
      ? { body_operation_count: value.body_operation_count }
      : {}),
  };
}

function parseIteration(value: unknown): { iteration?: QbsIterationMetadata } {
  if (!isRecord(value)) {
    return {};
  }
  const iteration: QbsIterationMetadata = {};
  if (typeof value.kind === "string") {
    iteration.kind = value.kind;
  }
  if (typeof value.variable === "string") {
    iteration.variable = value.variable;
  }
  if (typeof value.source === "string") {
    iteration.source = value.source;
  }
  if (typeof value.count === "number" && Number.isFinite(value.count)) {
    iteration.count = value.count;
  }
  if (Array.isArray(value.variables) && value.variables.every((item) => typeof item === "string")) {
    iteration.variables = value.variables;
  }
  return Object.keys(iteration).length ? { iteration } : {};
}

function parseQ1asmProgram(value: unknown): QbsQ1asmProgram {
  if (!isRecord(value)) {
    throw new Error("QBS IR q1asm_programs[] entries must be objects");
  }

  const sequencer = readString(value, "sequencer", readString(value, "sequencer_id"));
  if (!sequencer) {
    throw new Error("QBS IR q1asm_programs[] entries must contain sequencer_id");
  }

  return {
    sequencer,
    ...(typeof value.sequencer_id === "string" ? { sequencer_id: value.sequencer_id } : {}),
    file: readString(value, "file"),
    ...(Array.isArray(value.path) ? { path: value.path.filter((part): part is string => typeof part === "string") } : {}),
    ...(typeof value.text === "string" ? { text: value.text } : {}),
  };
}

function parseProvenanceMapping(value: unknown): QbsProvenanceMapping {
  if (!isRecord(value)) {
    throw new Error("QBS IR q1asm_provenance[] entries must be objects");
  }

  const operandMappings = Array.isArray(value.operand_mappings)
    ? (value.operand_mappings.filter(isRecord) as QbsProvenanceMapping["operand_mappings"])
    : [];
  const firstOperand = operandMappings?.find((mapping) => typeof mapping.line === "number" && mapping.instruction !== "range");
  const sequencer = readString(value, "sequencer", readString(value, "sequencer_id"));
  const operationId = readString(value, "operation_id", readString(value, "schedulable_id"));
  const symbolicOperand = operandMappings?.find((mapping) => typeof mapping.source_value_id === "string");
  const instructionRoles = Array.isArray(value.instruction_roles)
    ? value.instruction_roles.filter((role): role is string => typeof role === "string" && role.length > 0)
    : [];
  const topLevelLine = typeof value.line === "number" && Number.isFinite(value.line) ? value.line : undefined;
  const topLevelInstruction = typeof value.instruction === "string" ? value.instruction : undefined;
  const topLevelSymbolicValueId = typeof value.symbolic_value_id === "string" ? value.symbolic_value_id : undefined;
  const topLevelExpression = typeof value.expression === "string" ? value.expression : undefined;

  return {
    ...(sequencer ? { sequencer } : {}),
    ...(typeof value.sequencer_id === "string" ? { sequencer_id: value.sequencer_id } : {}),
    ...(typeof firstOperand?.line === "number" || typeof topLevelLine === "number"
      ? { line: firstOperand?.line ?? topLevelLine }
      : {}),
    ...(typeof firstOperand?.instruction === "string" || topLevelInstruction
      ? { instruction: firstOperand?.instruction ?? topLevelInstruction }
      : {}),
    ...(instructionRoles.length ? { instruction_roles: instructionRoles } : {}),
    ...(typeof value.q1asm_line_start === "number" ? { q1asm_line_start: value.q1asm_line_start } : {}),
    ...(typeof value.q1asm_line_end === "number" ? { q1asm_line_end: value.q1asm_line_end } : {}),
    ...(operationId ? { operation_id: operationId } : {}),
    ...(typeof value.schedulable_id === "string" ? { schedulable_id: value.schedulable_id } : {}),
    ...(typeof value.source_id === "string" ? { source_id: value.source_id } : {}),
    ...(typeof value.source_kind === "string" ? { source_kind: value.source_kind } : {}),
    ...(typeof symbolicOperand?.source_value_id === "string" || topLevelSymbolicValueId
      ? { symbolic_value_id: symbolicOperand?.source_value_id ?? topLevelSymbolicValueId }
      : {}),
    ...(typeof symbolicOperand?.source_expression === "string" || topLevelExpression
      ? { expression: symbolicOperand?.source_expression ?? topLevelExpression }
      : {}),
    ...(typeof value.confidence === "string" ? { confidence: value.confidence } : {}),
    ...(typeof value.inference_reason === "string" ? { inference_reason: value.inference_reason } : {}),
    ...(operandMappings ? { operand_mappings: operandMappings } : {}),
  };
}

function parseSourceLocation(value: unknown): QbsSourceLocation | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const location: QbsSourceLocation = {};
  if (typeof value.kind === "string") {
    location.kind = value.kind;
  }
  if (typeof value.file === "string") {
    location.file = value.file;
  }
  if (typeof value.line === "number" && Number.isFinite(value.line)) {
    location.line = value.line;
  }
  if (typeof value.column === "number" && Number.isFinite(value.column)) {
    location.column = value.column;
  }
  if (typeof value.label === "string") {
    location.label = value.label;
  }
  if (isRecord(value.notebook)) {
    const notebook: Partial<QbsNotebookSourceLocation> = {};
    if (typeof value.notebook.file === "string") {
      notebook.file = value.notebook.file;
    }
    if (typeof value.notebook.cell_index === "number" && Number.isFinite(value.notebook.cell_index)) {
      notebook.cell_index = value.notebook.cell_index;
    }
    if (typeof value.notebook.cell_id === "string") {
      notebook.cell_id = value.notebook.cell_id;
    }
    if (typeof value.notebook.cell_line === "number" && Number.isFinite(value.notebook.cell_line)) {
      notebook.cell_line = value.notebook.cell_line;
    }
    if (typeof notebook.file === "string" && typeof notebook.cell_index === "number") {
      location.notebook = notebook as QbsNotebookSourceLocation;
    }
  }
  if (typeof value.generated_file === "string") {
    location.generated_file = value.generated_file;
  }
  if (typeof value.generated_line === "number" && Number.isFinite(value.generated_line)) {
    location.generated_line = value.generated_line;
  }
  return Object.keys(location).length ? location : undefined;
}

function parseSourceMap(value: unknown): QbsSourceMap | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const schedulables: Record<string, QbsSourceLocation> = {};
  if (isRecord(value.schedulables)) {
    for (const [key, rawLocation] of Object.entries(value.schedulables)) {
      const location = parseSourceLocation(rawLocation);
      if (location) {
        schedulables[key] = location;
      }
    }
  }
  const primary = parseSourceLocation(value.primary);
  return {
    ...(primary ? { primary } : {}),
    ...(Object.keys(schedulables).length ? { schedulables } : {}),
  };
}

function parseIrDiagnostic(value: unknown): QbsIrDiagnostic | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  if (typeof value.code !== "string" || typeof value.path !== "string" || typeof value.message !== "string") {
    return undefined;
  }
  const severity = typeof value.severity === "string" ? value.severity : "warning";
  return {
    code: value.code,
    path: value.path,
    message: value.message,
    severity: severity === "error" || severity === "information" || severity === "hint" ? severity : "warning",
  };
}

export function parseQbsIrText(text: string): QbsIr {
  const parsed: unknown = JSON.parse(text);
  if (!isRecord(parsed)) {
    throw new Error("QBS IR root must be an object");
  }

  const operations = requireArray(parsed, "operations").map(parseOperation);
  if (operations.some((operation) => operation.id.length === 0)) {
    throw new Error("QBS IR operations[] entries must contain id");
  }

  return {
    schedule: isRecord(parsed.schedule) ? { name: readString(parsed.schedule, "name", "") } : undefined,
    status: typeof parsed.status === "string" ? parsed.status : undefined,
    operations,
    control_flow_blocks: Array.isArray(parsed.control_flow_blocks)
      ? parsed.control_flow_blocks.map(parseControlFlowBlock)
      : [],
    symbolic_values: Array.isArray(parsed.symbolic_values) ? (parsed.symbolic_values as QbsSymbolicValue[]) : [],
    symbolic_pulses: Array.isArray(parsed.symbolic_pulses) ? (parsed.symbolic_pulses as QbsSymbolicBlock[]) : [],
    q1asm_programs: Array.isArray(parsed.q1asm_programs) ? parsed.q1asm_programs.map(parseQ1asmProgram) : [],
    q1asm_by_sequencer: isRecord(parsed.q1asm_by_sequencer)
      ? (parsed.q1asm_by_sequencer as Record<string, string>)
      : {},
    q1asm_provenance: Array.isArray(parsed.q1asm_provenance)
      ? parsed.q1asm_provenance.map(parseProvenanceMapping)
      : [],
    q1timeline_ir: isRecord(parsed.q1timeline_ir) ? (parsed.q1timeline_ir as Record<string, unknown>) : undefined,
    q1timeline_diagnostics: Array.isArray(parsed.q1timeline_diagnostics)
      ? (parsed.q1timeline_diagnostics.filter(isRecord) as Q1TimelineDiagnostic[])
      : [],
    ir_diagnostics: Array.isArray(parsed.ir_diagnostics)
      ? parsed.ir_diagnostics.flatMap((diagnostic) => {
        const parsedDiagnostic = parseIrDiagnostic(diagnostic);
        return parsedDiagnostic ? [parsedDiagnostic] : [];
      })
      : [],
    source_map: parseSourceMap(parsed.source_map),
    project: isRecord(parsed.project) ? (parsed.project as QbsIr["project"]) : undefined,
    warnings: Array.isArray(parsed.warnings) ? (parsed.warnings as string[]) : [],
    capabilities: isRecord(parsed.capabilities) ? (parsed.capabilities as Record<string, boolean>) : {},
    artifacts: isRecord(parsed.artifacts) ? (parsed.artifacts as Record<string, unknown>) : {},
  };
}

export function getScheduleTitle(ir: QbsIr): string {
  return ir.schedule?.name || "Q1Lens";
}

export function getOperationById(ir: QbsIr, id: string): QbsOperation {
  const operation = ir.operations.find((candidate) => candidate.id === id || candidate.operation_id === id);
  if (!operation) {
    throw new Error(`Operation not found: ${id}`);
  }
  return operation;
}
