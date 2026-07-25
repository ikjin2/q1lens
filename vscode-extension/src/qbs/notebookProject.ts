import { dirname, join, normalize } from "node:path";

export const QBS_NOTEBOOK_SETUP_TAG = "qbstimeline-setup";
export const QBS_NOTEBOOK_SCHEDULE_TAG = "qbstimeline-schedule";

export interface NotebookTimelineCell {
  kind: "code" | "markdown";
  text: string;
  metadata?: Record<string, unknown>;
}

export interface ManagedNotebookTimelineProjectInput {
  notebookPath: string;
  selectedCellIndex: number;
  scheduleVariable: string;
  compilerVariable: string;
  cells: NotebookTimelineCell[];
}

export interface ManagedNotebookTimelineProject {
  projectDir: string;
  projectFile: string;
  snapshotFile: string;
  projectYaml: string;
  snapshotJson: string;
}

export interface NotebookTimelineVariableDefaults {
  scheduleVariable: string;
  compilerVariable: string;
}

export function createManagedNotebookTimelineProject(
  input: ManagedNotebookTimelineProjectInput,
): ManagedNotebookTimelineProject {
  const selectedCell = input.cells[input.selectedCellIndex];
  if (!selectedCell || selectedCell.kind !== "code") {
    throw new Error("Open Q1Lens from This Cell requires a code cell.");
  }
  const notebookDir = dirname(normalize(input.notebookPath));
  const projectDir = join(notebookDir, ".qbs_timeline", "notebook");
  const projectFile = join(projectDir, "qbstimeline.yml");
  const snapshotFile = join(projectDir, "selected.ipynb");
  return {
    projectDir,
    projectFile,
    snapshotFile,
    projectYaml: managedProjectYaml({
      sourceNotebook: input.notebookPath,
      scheduleVariable: input.scheduleVariable,
      compilerVariable: input.compilerVariable,
    }),
    snapshotJson: selectedNotebookSnapshotJson(input),
  };
}

export function inferNotebookTimelineVariables(
  cells: NotebookTimelineCell[],
  selectedCellIndex: number,
): NotebookTimelineVariableDefaults {
  const selectedAssignments = assignedNames(cells[selectedCellIndex]?.text ?? "");
  const priorAssignments = cells
    .slice(0, Math.max(0, selectedCellIndex + 1))
    .flatMap((cell) => assignedNames(cell.text));
  return {
    scheduleVariable: pickName(selectedAssignments, /sched|schedule/i, "schedule"),
    compilerVariable: pickName(priorAssignments, /compiler|hw_agent|hardware_agent|compile/i, "compiler"),
  };
}

export function withNotebookCellTag(
  metadata: Record<string, unknown> | undefined,
  tag: string,
): Record<string, unknown> {
  const next = { ...(metadata ?? {}) };
  const tags = metadataTags(next);
  if (!tags.includes(tag)) {
    tags.push(tag);
  }
  next.tags = tags;
  return next;
}

export function withoutNotebookCellTag(
  metadata: Record<string, unknown> | undefined,
  tag: string,
): Record<string, unknown> {
  const next = { ...(metadata ?? {}) };
  next.tags = metadataTags(next).filter((candidate) => candidate !== tag);
  return next;
}

function selectedNotebookSnapshotJson(input: ManagedNotebookTimelineProjectInput): string {
  const snapshot = {
    cells: input.cells.map((cell, index) => {
      const metadataWithoutSchedule = withoutNotebookCellTag(cell.metadata, QBS_NOTEBOOK_SCHEDULE_TAG);
      const metadata = index === input.selectedCellIndex
        ? withNotebookCellTag(metadataWithoutSchedule, QBS_NOTEBOOK_SCHEDULE_TAG)
        : metadataWithoutSchedule;
      return {
        cell_type: cell.kind === "markdown" ? "markdown" : "code",
        source: sourceLines(cell.text),
        metadata,
      };
    }),
    metadata: {
      qbstimeline: {
        source_notebook: toYamlPath(input.notebookPath),
        selected_cell_index: input.selectedCellIndex,
      },
    },
    nbformat: 4,
    nbformat_minor: 5,
  };
  return `${JSON.stringify(snapshot, null, 2)}\n`;
}

function managedProjectYaml(input: {
  sourceNotebook: string;
  scheduleVariable: string;
  compilerVariable: string;
}): string {
  return [
    "schedule:",
    "  notebook: selected.ipynb",
    "  setup_tags:",
    `    - ${QBS_NOTEBOOK_SETUP_TAG}`,
    `  schedule_tag: ${QBS_NOTEBOOK_SCHEDULE_TAG}`,
    `  schedule_variable: ${yamlVariable(input.scheduleVariable)}`,
    `  compiler_variable: ${yamlVariable(input.compilerVariable)}`,
    "source:",
    `  notebook: ${JSON.stringify(toYamlPath(input.sourceNotebook))}`,
    "outputs:",
    "  dir: ..",
    "low_level:",
    "  q1timeline: true",
    "",
  ].join("\n");
}

function yamlVariable(value: string): string {
  const trimmed = value.trim();
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(trimmed) ? trimmed : JSON.stringify(trimmed);
}

function toYamlPath(filePath: string): string {
  return normalize(filePath).replace(/\\/g, "/");
}

function sourceLines(text: string): string[] {
  return text.match(/[^\n]*\n|[^\n]+/g) ?? [];
}

function metadataTags(metadata: Record<string, unknown>): string[] {
  return Array.isArray(metadata.tags)
    ? metadata.tags.filter((tag): tag is string => typeof tag === "string")
    : [];
}

function assignedNames(text: string): string[] {
  const names: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=/);
    if (match) {
      names.push(match[1]);
    }
  }
  return names;
}

function pickName(names: string[], preferredPattern: RegExp, fallback: string): string {
  return names.find((name) => preferredPattern.test(name)) ?? names[0] ?? fallback;
}
