import { QbsIr } from "../qbsIr";

export interface TimelineSourceContext {
  projectFile: string;
  scheduleFile?: string;
  sourceNotebook?: string;
  outputDir: string;
}

export type ExtensionToWebviewMessage =
  | { type: "render"; ir: QbsIr; sourceContext?: TimelineSourceContext; selectedOperationId?: string; selectedSequencer?: string }
  | { type: "stale"; reason: string };

export type WebviewToExtensionMessage =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "openQ1Timeline"; operationId?: string; blockId?: string; sequencer?: string; file?: string; line?: number }
  | { type: "openQ1AsmSource"; sequencer?: string; line?: number }
  | { type: "openScheduleSource"; schedulableId?: string; operationId?: string; blockId?: string }
  | { type: "openIr" }
  | { type: "openProjectFile" }
  | { type: "openScheduleFile" }
  | { type: "openNotebookFile" }
  | { type: "selectOperation"; operationId: string }
  | { type: "selectSequencer"; sequencer: string };
