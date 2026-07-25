import { randomBytes } from "node:crypto";
import { isAbsolute, normalize, resolve } from "node:path";
import * as vscode from "vscode";
import { getScheduleTitle, QbsIr } from "../qbsIr";
import { TimelineSourceContext, WebviewToExtensionMessage } from "./messages";
import { buildWebviewHtml } from "./html";
import { OutputPaths } from "../projectDiscovery";

export interface TimelinePanelHandlers {
  onRefresh: () => Promise<void>;
  onOpenQ1Timeline: (message: Extract<WebviewToExtensionMessage, { type: "openQ1Timeline" }>) => Promise<void>;
  onOpenQ1AsmSource: (message: Extract<WebviewToExtensionMessage, { type: "openQ1AsmSource" }>) => Promise<void>;
  onOpenScheduleSource: (message: Extract<WebviewToExtensionMessage, { type: "openScheduleSource" }>) => Promise<void>;
  onOpenIr: () => Promise<void>;
  onOpenProjectFile: () => Promise<void>;
  onOpenScheduleFile: () => Promise<void>;
  onOpenNotebookFile: () => Promise<void>;
}

export class TimelinePanel {
  private panel: vscode.WebviewPanel | undefined;
  private pendingIr: QbsIr | undefined;
  private pendingSourceContext: TimelineSourceContext | undefined;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly handlers: TimelinePanelHandlers,
  ) {}

  show(ir: QbsIr, paths?: OutputPaths): void {
    if (!this.panel) {
      this.panel = vscode.window.createWebviewPanel(
        "qbsTimeline",
        `Q1Lens: ${getScheduleTitle(ir)}`,
        vscode.ViewColumn.Beside,
        {
          enableScripts: true,
          localResourceRoots: [
            vscode.Uri.joinPath(this.extensionUri, "out", "src", "shared", "timeline"),
            vscode.Uri.joinPath(this.extensionUri, "out", "src", "qbs", "webview", "assets"),
          ],
        },
      );
      this.panel.onDidDispose(() => {
        this.panel = undefined;
      });
      this.panel.webview.onDidReceiveMessage((message: WebviewToExtensionMessage) => {
        void this.receiveMessage(message);
      });
    }

    const sharedCssUri = this.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "out", "src", "shared", "timeline", "renderer.css"),
    );
    const sharedScriptUri = this.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "out", "src", "shared", "timeline", "renderer.js"),
    );
    const cssUri = this.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "out", "src", "qbs", "webview", "assets", "timeline.css"),
    );
    const modelScriptUri = this.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "out", "src", "qbs", "webview", "assets", "timelineModel.js"),
    );
    const scriptUri = this.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "out", "src", "qbs", "webview", "assets", "timeline.js"),
    );
    const nonce = randomBytes(16).toString("base64");
    this.panel.title = `Q1Lens: ${getScheduleTitle(ir)}`;
    this.pendingIr = ir;
    this.pendingSourceContext = paths ? buildSourceContext(paths, ir) : undefined;
    this.panel.webview.html = buildWebviewHtml({
      title: getScheduleTitle(ir),
      sharedCssUri: sharedCssUri.toString(),
      cssUri: cssUri.toString(),
      cspSource: this.panel.webview.cspSource,
      sharedScriptUri: sharedScriptUri.toString(),
      modelScriptUri: modelScriptUri.toString(),
      scriptUri: scriptUri.toString(),
      nonce,
    });
    this.panel.reveal(vscode.ViewColumn.Beside);
  }

  private async receiveMessage(message: WebviewToExtensionMessage): Promise<void> {
    if (message.type === "ready") {
      if (this.pendingIr && this.panel) {
        void this.panel.webview.postMessage({ type: "render", ir: this.pendingIr, sourceContext: this.pendingSourceContext });
      }
    } else if (message.type === "refresh") {
      await this.handlers.onRefresh();
    } else if (message.type === "openQ1Timeline") {
      await this.handlers.onOpenQ1Timeline(message);
    } else if (message.type === "openQ1AsmSource") {
      await this.handlers.onOpenQ1AsmSource(message);
    } else if (message.type === "openScheduleSource") {
      await this.handlers.onOpenScheduleSource(message);
    } else if (message.type === "openIr") {
      await this.handlers.onOpenIr();
    } else if (message.type === "openProjectFile") {
      await this.handlers.onOpenProjectFile();
    } else if (message.type === "openScheduleFile") {
      await this.handlers.onOpenScheduleFile();
    } else if (message.type === "openNotebookFile") {
      await this.handlers.onOpenNotebookFile();
    }
  }
}

function resolveFromProject(projectDir: string, value: string): string {
  return isAbsolute(value) ? normalize(value) : resolve(projectDir, value);
}

function buildSourceContext(paths: OutputPaths, ir: QbsIr): TimelineSourceContext {
  const primary = ir.source_map?.primary;
  const primaryNotebook = primary?.kind === "notebook" && primary.file
    ? resolveFromProject(paths.projectDir, primary.file)
    : undefined;
  return {
    projectFile: paths.projectFile,
    ...(paths.schedulePath ? { scheduleFile: paths.schedulePath } : {}),
    ...(paths.sourceNotebookPath || paths.scheduleNotebookPath || primaryNotebook
      ? { sourceNotebook: paths.sourceNotebookPath ?? paths.scheduleNotebookPath ?? primaryNotebook }
      : {}),
    outputDir: paths.outputDir,
  };
}
