from __future__ import annotations

import re
from pathlib import Path

from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.ast import Arg, Instr, Program, SourceLocation


_LABEL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REGISTER_RE = re.compile(r"^R(?:0[xX][0-9A-Fa-f]+|0|[1-9]\d*)$", re.IGNORECASE)
_INTEGER_RE = re.compile(r"^-?(?:0[xX][0-9A-Fa-f]+|0|[1-9]\d*)$")
_PLACEHOLDER_RE = re.compile(r"^\{(?P<expression>.+)\}$")


def parse_q1asm(text: str, *, file: str = "<string>") -> Program:
    defs: dict[str, Arg] = {}
    labels: dict[str, int] = {}
    instructions: list[Instr] = []
    diagnostics: list[Diagnostic] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        raw = raw_line.rstrip("\r\n")
        if line_number == 1:
            raw = raw.removeprefix("\ufeff")
        code_without_comment = _strip_inline_comment(raw).rstrip()
        if not code_without_comment.strip():
            continue

        leading_spaces = len(code_without_comment) - len(code_without_comment.lstrip())
        code = code_without_comment.lstrip()
        if code.startswith((";", "//")):
            continue
        column = leading_spaces + 1
        if re.match(r"^@[A-Za-z_][A-Za-z0-9_]*\s*:", code):
            diagnostics.append(
                _syntax_error(
                    file=file,
                    line=line_number,
                    column=column,
                    raw=raw,
                    message="Label definitions must not use @; use label: for definitions and @label for references.",
                )
            )
            continue
        known_labels = set(labels)
        label = _extract_label(code, labels, len(instructions), file, line_number, raw, diagnostics)
        label_was_added = label is not None and label not in known_labels
        if label is not None:
            match = _LABEL_RE.match(code)
            assert match is not None
            after_label = code[match.end() :]
            spaces_after_label = len(after_label) - len(after_label.lstrip())
            code = after_label.lstrip()
            column += match.end() + spaces_after_label
            if code.startswith((";", "//")):
                continue
            if not code:
                continue
            if code.startswith(".DEF"):
                diagnostics.append(
                    _syntax_error(
                        file=file,
                        line=line_number,
                        column=column,
                        raw=raw,
                        message=".DEF directives may not appear after a label on the same line.",
                    )
                )
                continue

        if code.startswith(".DEF"):
            _parse_def_line(code, defs, file, line_number, column, raw, diagnostics)
            continue

        instruction = _parse_instruction_line(
            code,
            pc=len(instructions),
            label=label,
            file=file,
            line=line_number,
            column=column,
            raw=raw,
            diagnostics=diagnostics,
        )
        if instruction is not None:
            instructions.append(instruction)
        elif label_was_added:
            labels.pop(label, None)

    return Program(
        file=file,
        defs=defs,
        labels=labels,
        instructions=instructions,
        diagnostics=diagnostics,
    )


def parse_q1asm_file(path: str | Path) -> Program:
    file_path = Path(path)
    return parse_q1asm(file_path.read_text(encoding="utf-8"), file=str(file_path))


def _strip_inline_comment(raw: str) -> str:
    hash_comment = raw.split("#", 1)[0]
    stripped = hash_comment.lstrip()
    if stripped.startswith("//"):
        leading_spaces = len(hash_comment) - len(stripped)
        return hash_comment[:leading_spaces]
    return hash_comment


def _extract_label(
    code: str,
    labels: dict[str, int],
    pc: int,
    file: str,
    line: int,
    raw: str,
    diagnostics: list[Diagnostic],
) -> str | None:
    match = _LABEL_RE.match(code)
    if match is None:
        return None

    label = match.group(1)
    if label in labels:
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=1,
                raw=raw,
                message=f"Duplicate label: {label}",
                details={"label": label},
            )
        )
    else:
        labels[label] = pc
    return label


def _parse_def_line(
    code: str,
    defs: dict[str, Arg],
    file: str,
    line: int,
    column: int,
    raw: str,
    diagnostics: list[Diagnostic],
) -> None:
    parts = code.split(None, 2)
    if len(parts) != 3 or parts[0] != ".DEF" or not _NAME_RE.match(parts[1]):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw,
                message="Malformed .DEF directive.",
            )
        )
        return

    if parts[1] in defs:
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw,
                message=f"Duplicate .DEF name: {parts[1]}",
                details={"name": parts[1]},
            )
        )
        return

    value = parts[2].strip()
    if not value or _has_invalid_operand_whitespace(value):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw,
                message="whitespace-separated .DEF values are not valid; define a single operand value.",
            )
        )
        return

    parsed_value = _parse_arg(
        value,
        file=file,
        line=line,
        column=column,
        raw_source=raw,
        diagnostics=diagnostics,
    )
    if parsed_value is None:
        return
    if parsed_value.kind == "reg" and not _is_valid_register_arg(parsed_value):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw,
                message=f".DEF register values must use an uppercase register R0..R63, got {parsed_value.raw}.",
                details={"name": parts[1], "operand": parsed_value.raw},
            )
        )
        return
    if parsed_value.kind == "symbol":
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw,
                message=".DEF values must be integer, register, label, or template operands.",
                details={"name": parts[1], "operand": parsed_value.raw},
            )
        )
        return
    defs[parts[1]] = parsed_value


def _parse_instruction_line(
    code: str,
    *,
    pc: int,
    label: str | None,
    file: str,
    line: int,
    column: int,
    raw: str,
    diagnostics: list[Diagnostic],
) -> Instr | None:
    if code.startswith("/*"):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw,
                message="C-style block comments are not supported in Q1ASM; use # comments.",
            )
        )
        return None

    semicolon_index = code.find(";")
    if semicolon_index != -1:
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column + semicolon_index,
                raw=raw,
                message="Semicolon comments or statement separators are not supported in Q1ASM.",
            )
        )
        return None

    parts = ["stop", ""] if code.startswith("stop,") else code.split(None, 1)
    op = parts[0]
    if op != op.lower():
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw,
                message=f"Instruction names are case-sensitive and must be lowercase: {op}.",
                details={"op": op},
            )
        )
        return None
    arg_text = parts[1].strip() if len(parts) > 1 else ""
    args: list[Arg] = []

    if arg_text:
        raw_args = _split_argument_text(arg_text)
        if op == "stop":
            raw_args = [arg for arg in raw_args if arg.strip()]
        else:
            while raw_args and not raw_args[-1].strip():
                raw_args.pop()
        if any(not arg.strip() for arg in raw_args):
            diagnostics.append(
                _syntax_error(
                    file=file,
                    line=line,
                    column=column,
                    raw=raw,
                    message="Malformed comma-separated argument list.",
                )
            )
            return None
        if any(_has_invalid_operand_whitespace(arg) for arg in raw_args):
            diagnostics.append(
                _syntax_error(
                    file=file,
                    line=line,
                    column=column,
                    raw=raw,
                    message="whitespace-separated operands are not valid; separate operands with commas.",
                )
            )
            return None
        for arg in raw_args:
            parsed_arg = _parse_arg(
                arg.strip(),
                file=file,
                line=line,
                column=column,
                raw_source=raw,
                diagnostics=diagnostics,
            )
            if parsed_arg is None:
                return None
            args.append(parsed_arg)
    if op == "stop" and len(args) == 1 and args[0].kind == "imm" and args[0].value == 0:
        args = []

    return Instr(
        pc=pc,
        label=label,
        op=op,
        args=args,
        source=SourceLocation(file=file, line=line, column=column, raw=raw),
    )


def _parse_arg(
    raw: str,
    *,
    file: str,
    line: int,
    column: int,
    raw_source: str,
    diagnostics: list[Diagnostic],
) -> Arg | None:
    if raw.startswith("+"):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw_source,
                message=f"Immediate integer literals may not use leading '+': {raw}.",
                details={"literal": raw},
            )
        )
        return None
    if raw.lower().lstrip("+-").startswith("0b"):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw_source,
                message=f"Binary integer literals are not accepted by q1asm source: {raw}.",
                details={"literal": raw},
            )
        )
        return None
    if _has_ambiguous_leading_zero_integer(raw):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw_source,
                message=f"Leading-zero decimal integer literals are not supported: {raw}.",
                details={"literal": raw},
            )
        )
        return None
    if _INTEGER_RE.match(raw):
        return Arg(kind="imm", value=_parse_int(raw), raw=raw)
    if _looks_like_malformed_integer(raw):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw_source,
                message=f"Malformed integer literal: {raw}.",
                details={"literal": raw},
            )
        )
        return None
    if _has_ambiguous_leading_zero_register(raw):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw_source,
                message=f"Leading-zero register literals are not supported: {raw}.",
                details={"literal": raw},
            )
        )
        return None
    if _REGISTER_RE.match(raw):
        return Arg(kind="reg", value=f"R{_parse_register_index(raw)}", raw=raw)
    if _looks_like_malformed_register(raw):
        diagnostics.append(
            _syntax_error(
                file=file,
                line=line,
                column=column,
                raw=raw_source,
                message=f"Malformed register literal: {raw}.",
                details={"literal": raw},
            )
        )
        return None
    if raw.startswith("@") and _NAME_RE.match(raw[1:]):
        return Arg(kind="label", value=raw[1:], raw=raw)
    placeholder = _placeholder_expression(raw)
    if placeholder is not None:
        return Arg(kind="placeholder", value=placeholder, raw=raw)
    return Arg(kind="symbol", value=raw, raw=raw)


def _split_argument_text(arg_text: str) -> list[str]:
    args: list[str] = []
    start = 0
    brace_depth = 0
    for index, char in enumerate(arg_text):
        if char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == "," and brace_depth == 0:
            args.append(arg_text[start:index])
            start = index + 1
    args.append(arg_text[start:])
    return args


def _has_invalid_operand_whitespace(raw: str) -> bool:
    stripped = raw.strip()
    if _placeholder_expression(stripped) is not None:
        return False
    return bool(re.search(r"\s", stripped))


def _placeholder_expression(raw: str) -> str | None:
    match = _PLACEHOLDER_RE.match(raw)
    if match is None:
        return None
    expression = match.group("expression").strip()
    return expression or None


def _parse_int(raw: str) -> int:
    lowered = raw.lower()
    if lowered.startswith(("0x", "-0x")):
        return int(raw, 0)
    return int(raw, 10)


def _parse_register_index(raw: str) -> int:
    text = raw[1:]
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text, 10)


def _has_ambiguous_leading_zero_integer(raw: str) -> bool:
    text = raw[1:] if raw.startswith("-") else raw
    return len(text) > 1 and text.startswith("0") and not text.lower().startswith("0x")


def _has_ambiguous_leading_zero_register(raw: str) -> bool:
    if len(raw) < 3 or raw[0] not in {"R", "r"}:
        return False
    text = raw[1:]
    return len(text) > 1 and text.startswith("0") and not text.lower().startswith("0x")


def _is_valid_register_arg(arg: Arg) -> bool:
    if arg.kind != "reg" or not isinstance(arg.value, str):
        return False
    raw = arg.raw
    if not raw.startswith("R") or not _REGISTER_RE.match(raw):
        return False
    index = _parse_register_index(raw)
    return 0 <= index <= 63


def _looks_like_malformed_integer(raw: str) -> bool:
    if len(raw) > 2 and raw[0] in {"+", "-"} and raw[1] in {"+", "-"} and raw[2].isdigit():
        return True
    text = raw[1:] if raw.startswith("-") else raw
    if not text:
        return False
    return text[0].isdigit()


def _looks_like_malformed_register(raw: str) -> bool:
    if len(raw) < 2 or raw[0] not in {"R", "r"}:
        return False
    return raw[1].isdigit() or raw[1] in {"+", "-"}


def _syntax_error(
    *,
    file: str,
    line: int,
    column: int,
    raw: str,
    message: str,
    details: dict[str, str] | None = None,
) -> Diagnostic:
    return Diagnostic(
        severity="error",
        category="syntax_error",
        message=message,
        source=SourceLocation(file=file, line=line, column=column, raw=raw),
        details=details or {},
    )
