import assert from "node:assert/strict";
import Module = require("node:module");
import { openFileBelowAtLine, resolveGeneratedFile } from "../src/qbs/navigation";

describe("navigation", () => {
  it("resolves generated Q1ASM files under the output directory", () => {
    const resolved = resolveGeneratedFile({
      outputDir: "C:\\repo\\.qbs_timeline",
      relativeFile: "q1asm/cluster0_module4_seq0.q1asm",
    });

    assert.equal(resolved.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1asm/cluster0_module4_seq0.q1asm");
  });

  it("rejects paths that escape the output directory", () => {
    assert.throws(
      () => resolveGeneratedFile({ outputDir: "C:\\repo\\.qbs_timeline", relativeFile: "../schedule.py" }),
      /Generated file escapes output directory/,
    );
  });

  it("opens files in a below editor group", async () => {
    const commands: string[] = [];
    let shownOptions: any = undefined;
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return {
          Uri: { file: (fsPath: string) => ({ fsPath }) },
          ViewColumn: { Active: -1, Beside: 2 },
          Range: class {
            start: any;
            constructor(public startLine: number, public startColumn: number) {
              this.start = { line: startLine, character: startColumn };
            }
          },
          Selection: class {
            constructor(public start: any, public end: any) {}
          },
          workspace: {
            openTextDocument: async (uri: any) => ({ uri }),
          },
          window: {
            showTextDocument: async (_document: any, options: any) => {
              shownOptions = options;
              return {
                set selection(_value: any) {},
                revealRange: () => undefined,
              };
            },
          },
          commands: {
            executeCommand: async (command: string) => {
              commands.push(command);
            },
          },
          TextEditorRevealType: { InCenter: 1 },
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    try {
      await openFileBelowAtLine("C:\\repo\\q1asm\\seq0.q1asm", 3);
    } finally {
      (Module as any)._load = originalLoad;
    }

    assert.deepEqual(commands, ["workbench.action.newGroupBelow"]);
    assert.deepEqual(shownOptions, { viewColumn: -1, preview: false });
  });
});
