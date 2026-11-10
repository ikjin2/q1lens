"""LINQ aliases and payload fields verified against Qblox Instruments 1.4.1."""
from __future__ import annotations


RAW_LINQ_OPERATIONS = {
    0x00: "fb_tdc_tags_id", 0x01: "fb_tdc_tdelta_id",
    0x10: "upd_thres", 0x11: "upd_thres", 0x12: "upd_thres", 0x13: "upd_thres",
    0x20: "fb_llp_tags_id", 0x21: "fb_llp_ttls_id",
    0x40: "fb_acq_tb_id", 0x41: "fb_acq_iq_id",
    0x42: "fb_acq_tb_cfg", 0x43: "fb_acq_tb_extra",
    0x5B: "fb_acq_tb_valid", 0x5C: "fb_acq_iq_shift", 0x5D: "fb_acq_tb_mock",
    0x60: "fb_com_cfg", 0x61: "fb_com_extra",
}
PACKED_LINQ_CONFIGS = {"fb_acq_tb_cfg", "fb_com_cfg"}
PACKED_LINQ_EXTRAS = {"fb_acq_tb_extra", "fb_com_extra"}
PACKED_LINQ_OPERATIONS = PACKED_LINQ_CONFIGS | PACKED_LINQ_EXTRAS | {"fb_acq_tb_mock"}
LINQ_CONFIGURATION_STREAMS = {
    "fb_com_cfg": "q1_register_or_immediate", "fb_com_extra": "q1_register_or_immediate",
    "fb_acq_tb_cfg": "thresholded_bits", "fb_acq_tb_extra": "thresholded_bits",
}
MOCK_LIMITATION = "fb_acq_tb_mock emission timing and fourth operand (duration versus count) require a target firmware vector."


class UnsupportedLinqCommand(ValueError):
    """The command has no verified simulation behavior."""


def linq_configuration_limitation(op: str, operands: list[int]) -> str | None:
    if op in PACKED_LINQ_CONFIGS:
        wc, shift, length = operands[:3]
        if wc not in {0, 1} or op == "fb_com_cfg" and wc:
            return "This common-data/write-combine mode requires a reference encoding vector."
        if wc and (not 1 <= length <= 4 or shift + 2 > length * 8):
            return "TB write-combine supports one word (1..4 bytes) with an in-bounds two-bit contribution."
        if wc and shift % 2:
            return "TB write-combine requires an even bit position for the two-bit contribution."
    elif op in PACKED_LINQ_EXTRAS and operands[0]:
        return "Enabled LINQ extra bytes require placement, padding and FIFO word-order vectors."
    return None


def raw_linq_operation(command_id: int | None) -> str | None:
    if command_id is not None and 0x100 <= command_id <= 0x1FF:
        return "fb_com_data"
    return RAW_LINQ_OPERATIONS.get(command_id)


def decode_linq_command(op: str, operands: list[int]) -> tuple[str, list[int]]:
    if op == "fb_cmd":
        command_id, payload, duration = operands
        op = raw_linq_operation(command_id)
        if op is None:
            raise UnsupportedLinqCommand(f"LINQ command 0x{command_id:x} has no verified behavior.")
        if op == "fb_com_data":
            return op, [command_id - 0x100, payload, duration]
        if op == "upd_thres":
            return op, [command_id - 0x10, payload, duration]
    else:
        payload, duration = operands

    if op in PACKED_LINQ_CONFIGS:
        mask = (0x3FF << 16) | (0x7F << 8) | 0x3
        fields = [payload & 0x3, (payload >> 16) & 0x3FF, (payload >> 8) & 0x7F]
    elif op in PACKED_LINQ_EXTRAS:
        mask = 0x1FFFF
        fields = [payload >> 16, payload & 0xFFFF]
    elif op == "fb_acq_tb_mock":
        mask = 0x111
        fields = [payload & 1, (payload >> 8) & 1, (payload >> 4) & 1]
    else:
        return op, [payload, duration]
    if payload & ~mask:
        raise ValueError(f"{op} packed payload 0x{payload:x} sets reserved bits outside 0x{mask:x}.")
    return op, [*fields, duration]
