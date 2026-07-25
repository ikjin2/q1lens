import * as vscode from "vscode";
import { registerCommands } from "./qbs/commands";
import { QbsTimelineTaskProvider } from "./qbs/taskProvider";
import { parseProjectConfigLite } from "./qbs/projectDiscovery";
import { registerSidebar } from "./qbs/sidebar";
import { registerQ1Timeline } from "./q1timeline/register";

export function activate(context: vscode.ExtensionContext): void {
  const q1timeline = registerQ1Timeline(context);
  registerCommands(context, { openQ1TimelineTarget: q1timeline.openTarget });
  registerSidebar(context);
  context.subscriptions.push(
    vscode.tasks.registerTaskProvider(
      "qbs-timeline",
      new QbsTimelineTaskProvider(async () => {
        const uris = await vscode.workspace.findFiles("**/qbstimeline.y{ml,aml}", "**/.qbs_timeline/**");
        return Promise.all(
          uris.map(async (uri) => {
            const project = vscode.workspace.asRelativePath(uri);
            try {
              const bytes = await vscode.workspace.fs.readFile(uri);
              return { project, outputDir: parseProjectConfigLite(Buffer.from(bytes).toString("utf8")).outputDir };
            } catch {
              return { project, outputDir: ".qbs_timeline" };
            }
          }),
        );
      }),
    ),
  );
}

export function deactivate(): void {
  // VSCode disposes registered subscriptions.
}
