(function instructionColorsFactory(root) {
  const families = {
    q1: {
      ops: "add and asl asr cmp illegal lsl lsr move muls16 muls32 muls32h muls32l mulu16 mulu32 mulu32h mulu32l nop not or sub sw_req test xor".split(" "),
      color: "#80b7ff", lightColor: "#245fa8", scope: "keyword.other.q1.q1asm",
    },
    flow: {
      ops: "ja jae jb jbe jg jge jl jle jlt jmp jno jns jnz jo js jz loop stop".split(" "),
      color: "#f4a261", lightColor: "#9a4c18", scope: "keyword.control.flow.q1asm",
    },
    latched: {
      ops: "reset_netzero reset_ph set_awg_gain set_awg_offs set_cond set_digital set_freq set_mrk set_ph set_ph_delta set_scope_en set_time_ref".split(" "),
      color: "#6ec6d9", lightColor: "#176b7e", scope: "storage.modifier.latched.q1asm",
    },
    rt: {
      ops: "acquire acquire_digital acquire_timetags acquire_ttl acquire_weighed acquire_weighted latch_rst play play_netzero play_pulse set_latch_en upd_param upd_thres wait".split(" "),
      color: "#8bcf9a", lightColor: "#27763c", scope: "entity.name.function.rt.q1asm",
    },
    sync: {
      ops: "wait_sync wait_trigger".split(" "),
      color: "#b9a7dc", lightColor: "#7251a3", scope: "constant.language.sync.q1asm",
    },
    feedback: {
      ops: "fb_acq_iq_id fb_acq_iq_shift fb_acq_tb_cfg fb_acq_tb_extra fb_acq_tb_id fb_acq_tb_mock fb_acq_tb_valid fb_cmd fb_com_cfg fb_com_data fb_com_extra fb_llp_tags_id fb_llp_ttls_id fb_pop_data fb_pull_data fb_tdc_tags_id fb_tdc_tdelta_id".split(" "),
      color: "#ff6fb3", lightColor: "#ad316c", scope: "string.other.feedback.q1asm",
    },
    directive: {
      ops: [".def"],
      color: "#dfc27d", lightColor: "#826219", scope: "keyword.control.directive.q1asm",
    },
  };
  const argumentStyles = {
    register: { color: "#a9c8e8", lightColor: "#315f8d", scope: "variable.other.register.q1asm" },
    number: { color: "#d6c7a1", lightColor: "#755c28", scope: "constant.numeric.q1asm" },
    symbol: { color: "#c2b5df", lightColor: "#6f548e", scope: "constant.other.symbol.q1asm" },
    label: { color: "#d9b398", lightColor: "#87522e", scope: "entity.name.label.q1asm" },
    placeholder: { color: "#b4cca6", lightColor: "#486b39", scope: "constant.other.placeholder.q1asm" },
  };
  const familyByCommand = new Map(Object.entries(families).flatMap(([family, spec]) => spec.ops.map((op) => [op, family])));

  function commandToken(raw) {
    const code = String(raw || "").split(/#|\/\//, 1)[0].replace(/^\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*/, "");
    const match = code.match(/^\s*(\.?[A-Za-z_][A-Za-z0-9_]*)\b/);
    return match ? match[1] : "";
  }

  function commandFamily(op) {
    return familyByCommand.get(String(op || "").toLowerCase()) || "unknown";
  }

  function commandAccentColor(op) {
    const family = commandFamily(op);
    return `var(--q1asm-${family}-color, ${families[family]?.color || "#94a3b8"})`;
  }

  function eventCommandToken(event) {
    if (["loop_block", "loop_iteration_preview", "branch_region"].includes(event && event.kind)) return "loop";
    const op = String((event && event.meta && event.meta.op) || "").trim();
    if (op) return op;
    const raw = commandToken(event && event.source && event.source.raw);
    if (raw) return raw;
    return ({ feedback_pop: "fb_pop_data", feedback_com: "fb_com_data", feedback_receive: "fb_pop_data",
      marker_state: "play", sync_wait: "wait_sync" })[event && event.kind] || String(event && event.kind || "");
  }

  function argumentAccentColor(kind) {
    return `var(--q1asm-arg-${kind}-color, ${argumentStyles[kind].color})`;
  }

  function sourceTokens(raw) {
    const text = String(raw || "");
    let codeEnd = text.length;
    let braceDepth = 0;
    for (let index = 0; index < text.length; index += 1) {
      if (text[index] === "#" || (braceDepth === 0 && text.startsWith("//", index))) {
        codeEnd = index;
        break;
      }
      if (text[index] === "{") braceDepth += 1;
      else if (text[index] === "}") braceDepth = Math.max(0, braceDepth - 1);
    }
    const code = text.slice(0, codeEnd);
    const tokens = [];
    const prefix = code.match(/^(\s*)(?:([A-Za-z_][A-Za-z0-9_]*)\s*:\s*)?/);
    if (prefix[2]) {
      tokens.push({ start: prefix[1].length, end: prefix[1].length + prefix[2].length, kind: "label" });
    }
    let offset = prefix[0].length;
    const command = code.slice(offset).match(/^\.?[A-Za-z_][A-Za-z0-9_]*\b/);
    if (!command) return tokens;
    tokens.push({ start: offset, end: offset + command[0].length, kind: "command", family: commandFamily(command[0]) });
    offset += command[0].length;
    if (command[0].toLowerCase() === ".def") {
      const definition = code.slice(offset).match(/^\s+([A-Za-z_][A-Za-z0-9_]*)\b/);
      if (definition) {
        offset += definition[0].length;
        tokens.push({ start: offset - definition[1].length, end: offset, kind: "symbol" });
      }
    }
    while (offset < code.length) {
      if (/[,\s]/.test(code[offset])) {
        offset += 1;
        continue;
      }
      const start = offset;
      braceDepth = 0;
      do {
        if (code[offset] === "{") braceDepth += 1;
        else if (code[offset] === "}") braceDepth = Math.max(0, braceDepth - 1);
        offset += 1;
      } while (offset < code.length && (braceDepth > 0 || !/[,\s]/.test(code[offset])));
      const token = code.slice(start, offset);
      let kind;
      if (/^\{.+\}$/.test(token) && token.slice(1, -1).trim()) kind = "placeholder";
      else if (/^R(?:0[xX][0-9A-Fa-f]+|0|[1-9]\d*)$/i.test(token)) kind = "register";
      else if (/^-?(?:0[xX][0-9A-Fa-f]+|0|[1-9]\d*)$/.test(token)) kind = "number";
      else if (/^@[A-Za-z_][A-Za-z0-9_]*$/.test(token)) kind = "label";
      else if (/^\$[A-Za-z_][A-Za-z0-9_]*$/.test(token)) kind = "symbol";
      if (kind) tokens.push({ start, end: offset, kind });
    }
    return tokens;
  }

  const api = { families, argumentStyles, commandToken, commandFamily, commandAccentColor, eventCommandToken, argumentAccentColor, sourceTokens };
  root.q1asmInstructionColors = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
