import assert from "node:assert/strict";
import * as vscode from "vscode";

const { families, argumentStyles } = require("../../src/shared/timeline/instructionColors.js");

function colored(value: string, style: { color: string; lightColor: string }): string {
  const light = [vscode.ColorThemeKind.Light, vscode.ColorThemeKind.HighContrastLight].includes(vscode.window.activeColorTheme.kind);
  return `<span style="color:${light ? style.lightColor : style.color};">${value}</span>`;
}

describe("Q1ASM instruction hover in VS Code", function () {
  this.timeout(30000);

  it("shows only argument names on commands in an unsaved Q1ASM document", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const document = await vscode.workspace.openTextDocument({
      language: "q1asm",
      content: "start: play 0,1,4 # play\nacquire_weighed 0,0,0,1,4\nstop\n",
    });
    const hover = (line: number, character: number) => vscode.commands.executeCommand<vscode.Hover[]>(
      "vscode.executeHoverProvider", document.uri, new vscode.Position(line, character),
    );
    const contents = (items: vscode.Hover[]) => items.flatMap((item) => item.contents)
      .map((item) => typeof item === "string" ? item : item.value);

    const command = (await hover(0, 8))[0];
    assert.deepEqual(contents([command]), [`<code>${colored("play", families.rt)} wave0, wave1, duration</code>`]);
    assert.equal((command.contents[0] as vscode.MarkdownString).supportHtml, true);
    assert.deepEqual(command.range, new vscode.Range(0, 7, 0, 11));
    assert.deepEqual(contents(await hover(1, 4)), [`<code>${colored("acquire_weighed", families.rt)} acq_idx, bin_idx, weight_idx0, weight_idx1, duration</code>`]);
    assert.deepEqual(contents(await hover(2, 1)), [`<code>${colored("stop", families.flow)} [status]</code>`]);
    assert.deepEqual(contents(await hover(0, 12)), [`<code>${colored("0", argumentStyles.number)} · immediate</code>`]);
    assert.deepEqual(contents(await hover(0, 0)), [`<code>${colored("start", argumentStyles.label)} · label (address)</code>`]);
    for (const character of [5, 11, 13, 19, 21]) {
      assert.deepEqual(await hover(0, character), [], `No instruction hover at column ${character}`);
    }
  });

  it("identifies standalone label declarations without treating their names as instructions", async () => {
    const document = await vscode.workspace.openTextDocument({ language: "q1asm", content: "Check_conv:\n  play :\n" });
    for (const [line, start, name] of [[0, 0, "Check_conv"], [1, 2, "play"]] as const) {
      const result = await vscode.commands.executeCommand<vscode.Hover[]>(
        "vscode.executeHoverProvider", document.uri, new vscode.Position(line, start + 1),
      );
      assert.equal((result[0].contents[0] as vscode.MarkdownString).value, `<code>${colored(name, argumentStyles.label)} · label (address)</code>`);
      assert.deepEqual(result[0].range, new vscode.Range(line, start, line, start + name.length));
    }
  });

  it("shows a register type and range through the registered provider", async () => {
    const document = await vscode.workspace.openTextDocument({ language: "q1asm", content: "move 4, R0 # R1" });
    const result = await vscode.commands.executeCommand<vscode.Hover[]>(
      "vscode.executeHoverProvider", document.uri, new vscode.Position(0, 9),
    );
    assert.equal((result[0].contents[0] as vscode.MarkdownString).value, `<code>${colored("R0", argumentStyles.register)} · register</code>`);
    assert.deepEqual(result[0].range, new vscode.Range(0, 8, 0, 10));
  });

  it("ignores command names in other languages", async () => {
    const document = await vscode.workspace.openTextDocument({ language: "plaintext", content: "play 0,1,4" });
    const hovers = await vscode.commands.executeCommand<vscode.Hover[]>(
      "vscode.executeHoverProvider", document.uri, new vscode.Position(0, 1),
    );
    assert.deepEqual(hovers, []);
  });
});
