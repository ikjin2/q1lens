import * as path from "node:path";
import type * as vscodeTypes from "vscode";
import { Q1TimelineApi, Q1TimelineOpenTarget } from "./api";

export interface Q1TimelineControllerState {
  projectFile?: string;
  pendingTarget?: Q1TimelineOpenTarget;
  setExplicitTarget(target: Q1TimelineOpenTarget): void;
}

export function q1timelineProjectOutputDir(projectFile: string): string {
  return path.join(path.dirname(projectFile), ".q1timeline");
}

export function createQ1TimelineControllerState(): Q1TimelineControllerState {
  return {
    projectFile: undefined,
    pendingTarget: undefined,
    setExplicitTarget(target) {
      this.projectFile = target.projectFile;
      this.pendingTarget = target;
    },
  };
}

export function registerQ1Timeline(context: vscodeTypes.ExtensionContext): Q1TimelineApi {
  const vscode = require("vscode") as typeof vscodeTypes;
  const { TimelineController } = require("./controller") as typeof import("./controller");
  const controller = new TimelineController(context);

  context.subscriptions.push(
    vscode.commands.registerCommand("q1timeline.openPreview", (sourceUri?: vscodeTypes.Uri) => controller.openPreview({ sourceUri })),
    vscode.commands.registerCommand("q1timeline.refreshPreview", () => controller.refreshPreview()),
    vscode.commands.registerCommand("q1timeline.selectProjectFile", () => controller.selectProjectFile()),
    vscode.commands.registerCommand("q1timeline.selectQ1asmFilesInFolder", (sourceUri?: vscodeTypes.Uri) => controller.selectQ1asmFilesInFolder(sourceUri)),
    vscode.commands.registerCommand("q1timeline.openQ1asmFilesInFolder", (sourceUri?: vscodeTypes.Uri) => controller.openQ1asmFilesInFolder(sourceUri)),
    vscode.commands.registerCommand("q1timeline.revealCurrentLineInTimeline", () => controller.revealCurrentLineInTimeline()),
  );

  return {
    openTarget: (target) => controller.openTarget(target),
    openPreview: () => controller.openPreview(),
    refresh: () => controller.refreshPreview(),
    selectProjectFile: () => controller.selectProjectFile(),
    openQ1asmFilesInFolder: (sourceUri) => controller.openQ1asmFilesInFolder(sourceUri),
    revealCurrentLine: async () => {
      controller.revealCurrentLineInTimeline();
    },
  };
}
