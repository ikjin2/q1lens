import { join } from "node:path";
import { QbsIr, QbsProvenanceMapping, QbsSymbolicBlock } from "../qbs/qbsIr";
import { resolveGeneratedFile } from "../qbs/navigation";
import { Q1TimelineOpenTarget } from "../q1timeline/api";

export interface Q1TimelineSelection {
  operationId?: string;
  blockId?: string;
  sequencer?: string;
  file?: string;
  line?: number;
}

function findBlock(ir: QbsIr, blockId: string | undefined): QbsSymbolicBlock | undefined {
  return blockId ? ir.symbolic_pulses.find((block) => block.id === blockId) : undefined;
}

function targetLine(mapping: QbsProvenanceMapping): number {
  const timedOperand = mapping.operand_mappings?.find(
    (operand) =>
      typeof operand.line === "number" &&
      isTimedOperandInstruction(operand.instruction),
  );
  if (typeof timedOperand?.line === "number") {
    return timedOperand.line;
  }
  return mapping.q1asm_line_start ?? mapping.line ?? 1;
}

function isTimedOperandInstruction(instruction: string | undefined): boolean {
  const normalized = String(instruction || "").toLowerCase();
  return ["play", "acquire", "wait", "upd_param"].includes(normalized) || normalized.startsWith("acquire_");
}

function findProgramFile(ir: QbsIr, sequencer: string): string {
  const program = ir.q1asm_programs.find((candidate) => candidate.sequencer === sequencer || candidate.sequencer_id === sequencer);
  if (!program) {
    throw new Error(`No Q1ASM program found for sequencer ${sequencer}`);
  }
  return program.file;
}

function sequencerMatches(mapping: QbsProvenanceMapping, sequencer: string | undefined): boolean {
  if (!sequencer) {
    return false;
  }
  return mapping.sequencer === sequencer || (mapping as any).sequencer_id === sequencer;
}

function preferredMapping(
  mappings: QbsProvenanceMapping[],
  selection: Q1TimelineSelection,
): QbsProvenanceMapping | undefined {
  return mappings.find((candidate) => sequencerMatches(candidate, selection.sequencer)) ?? mappings[0];
}

function findMapping(ir: QbsIr, selection: Q1TimelineSelection): QbsProvenanceMapping | undefined {
  if (selection.sequencer && typeof selection.line === "number" && !selection.blockId) {
    return { sequencer: selection.sequencer, line: selection.line, instruction: "", operation_id: selection.operationId };
  }
  const block = findBlock(ir, selection.blockId);
  const exactSourceIds = [selection.blockId, block?.id].filter((value): value is string => typeof value === "string" && value.length > 0);
  const exactSourceMatches = ir.q1asm_provenance.filter((candidate) => exactSourceIds.includes(candidate.source_id ?? ""));
  if (exactSourceMatches.length) {
    return preferredMapping(exactSourceMatches, selection);
  }
  const exactSchedulableIds = [block?.schedulable_id].filter((value): value is string => typeof value === "string" && value.length > 0);
  const exactSchedulableMatches = ir.q1asm_provenance.filter((candidate) => exactSchedulableIds.includes(candidate.schedulable_id ?? ""));
  if (exactSchedulableMatches.length) {
    return preferredMapping(exactSchedulableMatches, selection);
  }
  const relatedBlocks = selection.operationId
    ? ir.symbolic_pulses.filter((candidate) => candidate.operation_id === selection.operationId)
    : [];
  const candidates = [
    selection.blockId,
    selection.operationId,
    block?.id,
    block?.operation_id,
    block?.schedulable_id,
    ...relatedBlocks.flatMap((candidate) => [candidate.id, candidate.operation_id, candidate.schedulable_id]),
  ].filter((value): value is string => typeof value === "string" && value.length > 0);
  const matches = ir.q1asm_provenance.filter((candidate) => (
    candidates.includes(candidate.source_id ?? "") ||
    candidates.includes(candidate.operation_id ?? "") ||
    candidates.includes(candidate.schedulable_id ?? "")
  ));
  return preferredMapping(matches, selection);
}

export function resolveQbsSelectionToQ1TimelineTarget(input: {
  ir: QbsIr;
  selection: Q1TimelineSelection;
  outputDir: string;
}): Q1TimelineOpenTarget {
  const block = findBlock(input.ir, input.selection.blockId);
  const mapping = findMapping(input.ir, input.selection);
  const sequencer = mapping?.sequencer ?? input.selection.sequencer ?? input.ir.q1asm_programs[0]?.sequencer;
  if (!sequencer) {
    throw new Error("No Q1ASM program found for q1timeline target");
  }
  const relativeFile = input.selection.file ?? findProgramFile(input.ir, sequencer);
  return {
    projectFile: join(input.outputDir, "q1timeline.yml"),
    q1asmFile: resolveGeneratedFile({ outputDir: input.outputDir, relativeFile }),
    sequencer,
    line: mapping ? targetLine(mapping) : input.selection.line ?? 1,
    operationId: input.selection.operationId ?? block?.operation_id ?? mapping?.operation_id ?? mapping?.schedulable_id,
    blockId: input.selection.blockId,
    symbolicValueId: mapping?.symbolic_value_id,
  };
}
