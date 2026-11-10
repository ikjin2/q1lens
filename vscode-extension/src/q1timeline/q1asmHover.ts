import type * as vscodeTypes from "vscode";

interface SyntaxColors {
  color: string;
  lightColor: string;
}

const { families, argumentStyles, sourceTokens } = require("../shared/timeline/instructionColors.js") as {
  families: Record<string, SyntaxColors>;
  argumentStyles: Record<string, SyntaxColors>;
  sourceTokens(raw: string): Array<{ start: number; end: number; kind: string; family?: string }>;
};

// Argument order follows the Qblox Q1ASM guide, with aliases and packed-register
// forms supported by q1timeline/q1asm/instruction_table.py.
// https://docs.qblox.com/en/2026.10.1/products/qblox_instruments/q1/index.html
const signatures: Record<string, readonly string[]> = {
  illegal: [""],
  stop: ["[status]"],
  nop: [""],
  jmp: ["address"],
  jz: ["address"],
  jnz: ["address"],
  jo: ["address"],
  jno: ["address"],
  js: ["address"],
  jns: ["address"],
  jg: ["address"],
  jge: ["a, b, address", "address"],
  jl: ["address"],
  jlt: ["a, b, address"],
  jle: ["address"],
  ja: ["address"],
  jae: ["address"],
  jb: ["address"],
  jbe: ["address"],
  loop: ["source, address"],
  move: ["source, destination"],
  not: ["source, destination"],
  add: ["a, b, destination"],
  sub: ["a, b, destination"],
  cmp: ["a, b"],
  mulu16: ["a, b, destination"],
  muls16: ["a, b, destination"],
  mulu32: ["a, b, dst_low, dst_high"],
  muls32: ["a, b, dst_low, dst_high"],
  mulu32l: ["a, b, dst_low"],
  mulu32h: ["a, b, dst_high"],
  muls32l: ["a, b, dst_low"],
  muls32h: ["a, b, dst_high"],
  and: ["a, b, destination"],
  test: ["a, b"],
  or: ["a, b, destination"],
  xor: ["a, b, destination"],
  asl: ["a, b, destination"],
  asr: ["a, b, destination"],
  lsl: ["a, b, destination"],
  lsr: ["a, b, destination"],
  sw_req: ["request"],
  set_mrk: ["mrk"],
  set_awg_gain: ["gain0, gain1"],
  set_awg_offs: ["offs0, offs1"],
  set_freq: ["nco_freq"],
  reset_ph: [""],
  set_ph: ["nco_po"],
  set_ph_delta: ["nco_delta_po"],
  set_cond: ["cond_en, mask, op, else_cnt"],
  set_digital: ["out_val, out_mask, fine_out_delay"],
  set_time_ref: [""],
  set_scope_en: ["scope_en"],
  reset_netzero: [""],
  fb_acq_tb_id: ["id, duration"],
  fb_acq_tb_cfg: ["wc, shift, len, duration", "config, duration"],
  fb_acq_iq_id: ["id, duration"],
  fb_acq_tb_valid: ["tb_valid, duration"],
  fb_acq_tb_extra: ["extra_vld, extra, duration", "config, duration"],
  fb_acq_tb_mock: ["mock_en, mock_vld, mock_data, duration", "config, duration"],
  fb_acq_iq_shift: ["iq_shift, duration"],
  fb_llp_tags_id: ["id, duration"],
  fb_llp_ttls_id: ["id, duration"],
  fb_tdc_tags_id: ["id, duration"],
  fb_tdc_tdelta_id: ["id, duration"],
  fb_com_data: ["id, value, duration"],
  fb_com_cfg: ["wc, shift, len, duration", "config, duration"],
  fb_com_extra: ["extra_vld, extra, duration", "config, duration"],
  fb_cmd: ["command, payload, duration"],
  fb_pop_data: ["id, destination"],
  fb_pull_data: ["id_destination, data_destination"],
  wait: ["duration"],
  wait_sync: ["duration"],
  wait_trigger: ["trig_addr, duration"],
  play: ["wave0, wave1, duration"],
  play_pulse: ["fine_out_delay, duration"],
  play_netzero: ["maxt, duration"],
  acquire: ["acq_idx, bin_idx, duration"],
  acquire_weighed: ["acq_idx, bin_idx, weight_idx0, weight_idx1, duration"],
  acquire_weighted: ["acq_idx, bin_idx, weight_idx0, weight_idx1, duration"],
  acquire_ttl: ["acq_idx, bin_idx, ttl_en, duration"],
  acquire_timetags: ["acq_idx, bin_idx, window_en, fine_acq_delay, duration"],
  acquire_digital: ["acq_idx, bin_idx, duration"],
  upd_thres: ["dio_thres_idx, value, duration"],
  upd_param: ["duration"],
  latch_rst: ["duration"],
  set_latch_en: ["latch_en, duration"],
  ".def": ["name, value"],
};

export class Q1asmHoverProvider implements vscodeTypes.HoverProvider {
  constructor(private readonly vscode: typeof vscodeTypes) {}

  provideHover(document: vscodeTypes.TextDocument, position: vscodeTypes.Position): vscodeTypes.Hover | undefined {
    const text = document.lineAt(position.line).text;
    const tokens = sourceTokens(text);
    const command = tokens.find((token) => token.kind === "command");
    const token = tokens.find((item) => item.start <= position.character && position.character < item.end);
    if (!token) {
      return undefined;
    }
    const op = command ? text.slice(command.start, command.end).toLowerCase() : "";
    const args = Object.hasOwn(signatures, op) ? signatures[op] : undefined;
    const declaration = token.kind === "label" && (!command || token.start < command.start);
    if (!args && !declaration) {
      return undefined;
    }
    let value: string;
    if (token.kind === "command") {
      const name = this.colored(op, families[token.family!]);
      value = args!.map((names) => `<code>${name}${names ? ` ${names}` : ""}</code>`).join("<br>");
    } else {
      const types: Record<string, string> = {
        number: "immediate",
        register: "register",
        label: "label (address)",
        symbol: "alias",
        placeholder: "parameter",
      };
      value = `<code>${this.colored(text.slice(token.start, token.end), argumentStyles[token.kind])} · ${types[token.kind]}</code>`;
    }
    const content = new this.vscode.MarkdownString(value);
    content.supportHtml = true;
    return new this.vscode.Hover(content, new this.vscode.Range(position.line, token.start, position.line, token.end));
  }

  private colored(text: string, colors: SyntaxColors): string {
    const kind = this.vscode.window.activeColorTheme.kind;
    const light = kind === this.vscode.ColorThemeKind.Light || kind === this.vscode.ColorThemeKind.HighContrastLight;
    const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `<span style="color:${light ? colors.lightColor : colors.color};">${escaped}</span>`;
  }
}

export function registerQ1asmHover(context: vscodeTypes.ExtensionContext, vscode: typeof vscodeTypes): void {
  context.subscriptions.push(vscode.languages.registerHoverProvider(
    { language: "q1asm" },
    new Q1asmHoverProvider(vscode),
  ));
}
