import type * as vscodeTypes from "vscode";
import { readMergedSetting } from "../settings";
import { readSharedPythonSettings } from "./settingsCore";

export interface QbsTimelineSettings {
  pythonPath: string;
  pythonArgs: string[];
  autoRefresh: boolean;
  outputDirOverride: string | null;
  revealOutputChannel: boolean;
  q1timelineCommand: string | null;
}

export function readSettings(resource?: vscodeTypes.Uri): QbsTimelineSettings {
  const vscode = require("vscode") as typeof vscodeTypes;
  const q1lensConfig = vscode.workspace.getConfiguration("q1lens", resource);
  const qbloxConfig = vscode.workspace.getConfiguration("qbloxTimeline", resource);
  const qbsConfig = vscode.workspace.getConfiguration("qbsTimeline", resource);
  const settings = {
    q1lens: {
      revealOutputChannel: q1lensConfig.get<boolean | undefined>("revealOutputChannel"),
      q1timelineCommand: q1lensConfig.get<string | null | undefined>("q1timelineCommand"),
      qbs: {
        autoRefresh: q1lensConfig.get<boolean | undefined>("qbs.autoRefresh"),
        outputDirOverride: q1lensConfig.get<string | null | undefined>("qbs.outputDirOverride"),
      },
    },
    qbloxTimeline: {
      qbs: {
        autoRefresh: qbloxConfig.get<boolean | undefined>("qbs.autoRefresh"),
        outputDirOverride: qbloxConfig.get<string | null | undefined>("qbs.outputDirOverride"),
      },
    },
    qbsTimeline: {
      autoRefresh: qbsConfig.get<boolean | undefined>("autoRefresh"),
      outputDirOverride: qbsConfig.get<string | null | undefined>("outputDirOverride"),
      revealOutputChannel: qbsConfig.get<boolean | undefined>("revealOutputChannel"),
      q1timelineCommand: qbsConfig.get<string | null | undefined>("q1timelineCommand"),
    },
  };
  return {
    ...readSharedPythonSettings(q1lensConfig, qbloxConfig, qbsConfig),
    autoRefresh: readMergedSetting(settings, ["qbs", "autoRefresh"], false),
    outputDirOverride: readMergedSetting(settings, ["qbs", "outputDirOverride"], null),
    revealOutputChannel: readMergedSetting(settings, ["revealOutputChannel"], true),
    q1timelineCommand: readMergedSetting(settings, ["q1timelineCommand"], null),
  };
}
