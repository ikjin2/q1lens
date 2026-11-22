export interface WebviewHtmlInput {
  title: string;
  sharedCssUri?: string;
  cssUri: string;
  cspSource: string;
  sharedScriptUri?: string;
  instructionColorsScriptUri: string;
  modelScriptUri: string;
  scriptUri: string;
  nonce: string;
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    };
    return map[char];
  });
}

export function buildWebviewHtml(input: WebviewHtmlInput): string {
  const title = escapeHtml(input.title);
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src ${input.cspSource}; script-src 'nonce-${input.nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  ${input.sharedCssUri ? `<link rel="stylesheet" href="${input.sharedCssUri}">` : ""}
  <link rel="stylesheet" href="${input.cssUri}">
  <title>${title}</title>
</head>
<body>
  <header class="topbar">
    <h1>${title}</h1>
    <button id="refresh-button" class="icon-button" type="button" aria-label="Refresh timeline" title="Refresh timeline"><svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M12.5 8a4.5 4.5 0 1 1-1.3-3.2M12.5 2.3v2.8H9.7"/></svg></button>
    <div id="qbs-tools" class="topbar-tools"></div>
    <div id="qbs-status" class="topbar-status"></div>
  </header>
  <main id="qbs-root" class="timeline-shell" aria-live="polite"></main>
  <script nonce="${input.nonce}" src="${input.instructionColorsScriptUri}"></script>
  ${input.sharedScriptUri ? `<script nonce="${input.nonce}" src="${input.sharedScriptUri}"></script>` : ""}
  <script nonce="${input.nonce}" src="${input.modelScriptUri}"></script>
  <script nonce="${input.nonce}" src="${input.scriptUri}"></script>
</body>
</html>`;
}
