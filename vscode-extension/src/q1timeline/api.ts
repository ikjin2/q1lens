import type * as vscode from "vscode";

export interface Q1TimelineOpenTarget {
  projectFile: string;
  q1asmFile?: string;
  sequencer?: string;
  line?: number;
  operationId?: string;
  blockId?: string;
  symbolicValueId?: string;
  viewColumn?: vscode.ViewColumn;
  preserveFocus?: boolean;
}

export interface Q1TimelineApi {
  openTarget(target: Q1TimelineOpenTarget): Promise<void>;
  openPreview(): Promise<void>;
  refresh(): Promise<void>;
  selectProjectFile(): Promise<void>;
  openQ1asmFilesInFolder(sourceUri?: vscode.Uri): Promise<void>;
  revealCurrentLine(): Promise<void>;
}
