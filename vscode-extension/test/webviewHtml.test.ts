import assert from "node:assert/strict";
import { buildWebviewHtml } from "../src/qbs/webview/html";

describe("webview html", () => {
  it("contains CSP, asset links, nonce, and root element", () => {
    const html = buildWebviewHtml({
      title: "two-qubit entangling demo",
      sharedCssUri: "vscode-resource://shared-renderer.css",
      cssUri: "vscode-resource://timeline.css",
      cspSource: "vscode-resource://webview-csp",
      sharedScriptUri: "vscode-resource://shared-renderer.js",
      modelScriptUri: "vscode-resource://timelineModel.js",
      scriptUri: "vscode-resource://timeline.js",
      nonce: "abc123",
    });

    assert.match(html, /Content-Security-Policy/);
    assert.match(html, /style-src vscode-resource:\/\/webview-csp/);
    assert.match(html, /vscode-resource:\/\/timeline.css/);
    assert.ok(html.indexOf("vscode-resource://shared-renderer.css") < html.indexOf("vscode-resource://timeline.css"));
    assert.ok(html.indexOf("vscode-resource://shared-renderer.js") < html.indexOf("vscode-resource://timelineModel.js"));
    assert.match(html, /vscode-resource:\/\/timelineModel.js/);
    assert.doesNotMatch(html, /q1asmPreviewModel\.js/);
    assert.match(html, /nonce="abc123"/);
    assert.match(html, /id="qbs-root"/);
    assert.match(html, /two-qubit entangling demo/);
  });

  it("escapes the title", () => {
    const html = buildWebviewHtml({
      title: "<script>alert(1)</script>",
      sharedCssUri: "vscode-resource://shared-renderer.css",
      cssUri: "vscode-resource://timeline.css",
      cspSource: "vscode-resource://webview-csp",
      sharedScriptUri: "vscode-resource://shared-renderer.js",
      modelScriptUri: "vscode-resource://timelineModel.js",
      scriptUri: "vscode-resource://timeline.js",
      nonce: "abc123",
    });

    assert.doesNotMatch(html, /<script>alert/);
    assert.match(html, /&lt;script&gt;alert/);
  });
});
