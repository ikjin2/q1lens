import type * as vscodeTypes from "vscode";
import { readMergedSetting } from "../settings";

export interface QbsTimelineSettings {
  pythonPath: string;
  pythonArgs: string[];
  autoRefresh: boolean;
  outputDirOverride: string | null;
  revealOutputChannel: boolean;
  q1timelineCommand: string | null;
}

export function readSettings(): QbsTimelineSettings {
  const vscode = require("vscode") as typeof vscodeTypes;
  const q1lensConfig = vscode.workspace.getConfiguration("q1lens");
  const qbloxConfig = vscode.workspace.getConfiguration("qbloxTimeline");
  const qbsConfig = vscode.workspace.getConfiguration("qbsTimeline");
  const settings = {
    q1lens: {
      pythonPath: q1lensConfig.get<string | undefined>("pythonPath"),
      pythonArgs: q1lensConfig.get<string[] | undefined>("pythonArgs"),
      revealOutputChannel: q1lensConfig.get<boolean | undefined>("revealOutputChannel"),
      q1timelineCommand: q1lensConfig.get<string | null | undefined>("q1timelineCommand"),
      qbs: {
        autoRefresh: q1lensConfig.get<boolean | undefined>("qbs.autoRefresh"),
        outputDirOverride: q1lensConfig.get<string | null | undefined>("qbs.outputDirOverride"),
      },
    },
    qbloxTimeline: {
      pythonPath: qbloxConfig.get<string | undefined>("pythonPath"),
      pythonArgs: qbloxConfig.get<string[] | undefined>("pythonArgs"),
      qbs: {
        autoRefresh: qbloxConfig.get<boolean | undefined>("qbs.autoRefresh"),
        outputDirOverride: qbloxConfig.get<string | null | undefined>("qbs.outputDirOverride"),
      },
    },
    qbsTimeline: {
      pythonPath: qbsConfig.get<string | undefined>("pythonPath"),
      pythonArgs: qbsConfig.get<string[] | undefined>("pythonArgs"),
      autoRefresh: qbsConfig.get<boolean | undefined>("autoRefresh"),
      outputDirOverride: qbsConfig.get<string | null | undefined>("outputDirOverride"),
      revealOutputChannel: qbsConfig.get<boolean | undefined>("revealOutputChannel"),
      q1timelineCommand: qbsConfig.get<string | null | undefined>("q1timelineCommand"),
    },
  };
  return {
    pythonPath: readMergedSetting(settings, ["pythonPath"], "python"),
    pythonArgs: readMergedSetting(settings, ["pythonArgs"], []),
    autoRefresh: readMergedSetting(settings, ["qbs", "autoRefresh"], false),
    outputDirOverride: readMergedSetting(settings, ["qbs", "outputDirOverride"], null),
    revealOutputChannel: readMergedSetting(settings, ["revealOutputChannel"], true),
    q1timelineCommand: readMergedSetting(settings, ["q1timelineCommand"], null),
  };
}
