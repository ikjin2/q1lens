"""Single-receiver inputs and device commands at concrete instruction boundaries."""
from __future__ import annotations

from dataclasses import dataclass, replace

from q1timeline.feedback_fifo import DEFAULT_FEEDBACK_QUEUE_CAPACITY, feedback_pop_limitation
from q1timeline.q1asm.linq_commands import (
    LINQ_CONFIGURATION_STREAMS, MOCK_LIMITATION, PACKED_LINQ_OPERATIONS, UnsupportedLinqCommand,
    decode_linq_command, linq_configuration_limitation,
)
from q1timeline.q1asm.trigger_counters import TriggerCounters, selected_addresses, trigger_counter_settings_valid


class CoreBlocked(ValueError):
    """The next issue needs external input or a verified device behavior."""


class FeedbackOverflow(ValueError):
    def __init__(self, time_ns: int, identifier: int, capacity: int):
        super().__init__(f"LINQ feedback queue overflow at Q1 {time_ns} ns (capacity {capacity}).")
        self.time_ns = time_ns
        self.identifier = identifier
        self.capacity = capacity


@dataclass(frozen=True)
class ExternalInputs:
    """Ordered receiver-side arrivals. None denotes an unspecified trigger schedule."""

    trigger_arrivals: tuple[tuple[int, int], ...] | None = None  # (RT time in ns, address)
    feedback_arrivals: tuple[tuple[int, int, int], ...] = ()  # (Q1 time in ns, ID, word)
    trigger_settings: tuple[tuple[str, int | bool], ...] = ()
    scheduled_addresses: frozenset[int] | None = None
    feedback_queue_capacity: int = DEFAULT_FEEDBACK_QUEUE_CAPACITY

    def __post_init__(self) -> None:
        if type(self.feedback_queue_capacity) is not int or self.feedback_queue_capacity < 1:
            raise ValueError("feedback_queue_capacity must be a positive integer")
        if not trigger_counter_settings_valid(dict(self.trigger_settings)):
            raise ValueError("Invalid trigger counter settings")
        for entries in (self.trigger_arrivals or (), self.feedback_arrivals):
            if any(type(row[0]) is not int or row[0] < 0 or i and entries[i - 1][0] > row[0]
                   for i, row in enumerate(entries)):
                raise ValueError("Receiver arrival times must be nonnegative and nondecreasing")
        if any(type(address) is not int or not 0 <= address <= 15 for _, address in self.trigger_arrivals or ()):
            raise ValueError("Trigger address must be in range 0..15")
        if self.scheduled_addresses is not None:
            if any(type(address) is not int or not 0 <= address <= 15 for address in self.scheduled_addresses):
                raise ValueError("Scheduled trigger addresses must be in range 0..15")
            if any(address not in self.scheduled_addresses for _, address in self.trigger_arrivals or ()):
                raise ValueError("Scheduled trigger addresses must cover every arrival")
        for _, identifier, word in self.feedback_arrivals:
            if type(identifier) is not int or not 0 <= identifier <= 255 or type(word) is not int or not 0 <= word <= 0xFFFFFFFF:
                raise ValueError("A LINQ arrival requires an 8-bit ID and a 32-bit word")

    def check_fifo(self, cursor: int, until_ns: int) -> None:
        first_excess = cursor + self.feedback_queue_capacity
        if first_excess < len(self.feedback_arrivals):
            time_ns, identifier, _ = self.feedback_arrivals[first_excess]
            if time_ns <= until_ns:
                raise FeedbackOverflow(time_ns, identifier, self.feedback_queue_capacity)


@dataclass(frozen=True)
class DeviceState:
    latch_enabled: bool = False
    counts: tuple[tuple[int, int], ...] = ()
    unknown_counts: bool = False
    last_counter_ns: int = 0
    counter_cursor: int = 0
    trigger_wait_cursor: int = 0
    feedback_cursor: int = 0
    thresholds: tuple[tuple[int, int], ...] = ()
    linq_config: tuple[tuple[str, tuple[int, ...]], ...] = ()
    time_ref_ns: int | None = None

    def advance_counters(self, inputs: ExternalInputs, time_ns: int) -> DeviceState:
        arrivals = inputs.trigger_arrivals
        cursor = self.counter_cursor
        counts = dict(self.counts)
        while arrivals and cursor < len(arrivals) and arrivals[cursor][0] <= time_ns:
            address = arrivals[cursor][1]
            if self.latch_enabled:
                counts[address] = counts.get(address, 0) + 1
            cursor += 1
        partial = arrivals is None or inputs.scheduled_addresses is not None
        unknown = self.unknown_counts or (partial and self.latch_enabled and time_ns > self.last_counter_ns)
        return replace(self, counts=tuple(counts.items()), counter_cursor=cursor,
                       unknown_counts=unknown, last_counter_ns=time_ns)

    def condition_taken(self, mask: int, operator: int, inputs: ExternalInputs) -> bool:
        counters = TriggerCounters(dict(inputs.trigger_settings), dict(self.counts), self.latch_enabled)
        unknown = set(selected_addresses(mask)) - (inputs.scheduled_addresses or frozenset())
        possible = dict.fromkeys(unknown) if self.unknown_counts else {}
        decision = counters.decide(mask, operator, possible)
        if decision is None:
            raise CoreBlocked("set_cond requires receiver trigger inputs for the selected addresses")
        return decision

    def await_trigger(self, inputs: ExternalInputs, address: int, start: int) -> tuple[DeviceState, int]:
        arrivals = inputs.trigger_arrivals or ()
        for index in range(self.trigger_wait_cursor, len(arrivals)):
            arrival, channel = arrivals[index]
            if arrival >= start and channel == address:
                # The next packet dequeues strictly after this arrival, even for a zero wait.
                return replace(self, trigger_wait_cursor=index + 1), arrival
        raise CoreBlocked(f"wait_trigger requires an unused arrival for address {address} at or after {start} ns")

    def receive(self, inputs: ExternalInputs, identifier: int | None, now_ns: int) -> tuple[DeviceState, int, tuple[int, ...]]:
        if identifier is not None:
            if not 0 <= identifier <= 65535:
                raise ValueError("fb_pop_data selector must be in range 0..65535")
            limitation = feedback_pop_limitation(identifier)
            if limitation:
                raise CoreBlocked(limitation)
        cursor = self.feedback_cursor
        observed = now_ns
        for index in range(cursor, len(inputs.feedback_arrivals)):
            arrival, channel, word = inputs.feedback_arrivals[index]
            if arrival > observed:
                # A waiting selective pop discards nonmatches before the next arrival batch.
                cursor, observed = index, arrival
                inputs.check_fifo(cursor, observed)
            if identifier is None or channel == identifier:
                words = (channel, word) if identifier is None else (word,)
                return replace(self, feedback_cursor=index + 1), arrival, words
        raise CoreBlocked("LINQ receive requires a matching feedback FIFO entry")

    def apply(self, op: str, args: tuple[int, ...]) -> DeviceState:
        if op == "set_latch_en":
            return replace(self, latch_enabled=bool(args[0]))
        if op == "latch_rst":
            return replace(self, counts=(), unknown_counts=False)
        if op == "upd_thres":
            thresholds = dict(self.thresholds)
            thresholds[args[0]] = args[1]
            return replace(self, thresholds=tuple(thresholds.items()))
        if op.startswith("fb_") and op != "fb_com_data":
            config = dict(self.linq_config)
            config[op] = args[:-1]
            return replace(self, linq_config=tuple(config.items()))
        return self


# Documented operand ranges for the expanded device operations. The final
# duration is checked separately, after selecting a raw/packed LINQ command.
_ACQUISITION = ((0, "acquisition_index", 0, 31), (1, "bin_index", 0, 16777215))
_CFG = ((0, "write_combine_flag", 0, 3), (1, "bit_position", 0, 1023), (2, "payload_length", 0, 127))
_EXTRA = ((0, "enable_flag", 0, 1), (1, "extra_payload_bytes", 0, 65535))
DEVICE_RANGES = {
    "play_pulse": ((0, "fine_output_delay", 0, 2047),),
    "play_netzero": ((0, "maximum_compensation_length", 0, 0xFFFFFFFF),),
    "set_latch_en": ((0, "enable_flag", 0, 1),), "wait_trigger": ((0, "trigger_index", 0, 15),),
    "acquire_digital": _ACQUISITION, "acquire_ttl": (*_ACQUISITION, (2, "input_index", 0, 1)),
    "acquire_timetags": (*_ACQUISITION, (2, "input_index", 0, 1), (3, "tag_index", 0, 2047)),
    "upd_thres": ((0, "threshold_index", 0, 3), (1, "threshold_value", 0, 0xFFFFFFFF)),
    "fb_com_data": ((0, "feedback_channel", 0, 255), (1, "feedback_source", 0, 0xFFFFFFFF)),
    "fb_cmd": ((0, "feedback_channel", 0, 511), (1, "feedback_source", 0, 0xFFFFFFFF)),
    "fb_acq_tb_valid": ((0, "feedback_acquisition_channel", 0, 1),),
    "fb_acq_iq_shift": ((0, "shift_count", 0, 63),), "fb_com_cfg": _CFG, "fb_acq_tb_cfg": _CFG,
    "fb_com_extra": _EXTRA, "fb_acq_tb_extra": _EXTRA,
    "fb_acq_tb_mock": ((0, "enable_flag", 0, 1), (1, "valid_bit", 0, 1), (2, "mock_data", 0, 1)),
    "fb_acq_tb_id": ((0, "feedback_acquisition_channel", 0, 255),),
    "fb_acq_iq_id": ((0, "feedback_acquisition_channel", 0, 255),),
    **{op: ((0, "feedback_channel", 0, 255),) for op in
       ("fb_llp_tags_id", "fb_llp_ttls_id", "fb_tdc_tags_id", "fb_tdc_tdelta_id")},
}
DEVICE_RT_OPS = frozenset(DEVICE_RANGES) | {"latch_rst"}
DEVICE_SETTERS = {"reset_netzero": "netzero_reset", "set_time_ref": "time_ref"}


def _check_args(op: str, args: tuple[int, ...]) -> None:
    for index, role, low, high in DEVICE_RANGES.get(op, ()):
        if not low <= args[index] <= high:
            raise ValueError(f"{op} {role} {args[index]} outside {low}..{high}")


def check_device_issue(op: str, args: tuple[int, ...]) -> None:
    # Packed alias fields are decoded at RT only when the packet executes.
    if op not in PACKED_LINQ_OPERATIONS or len(args) != 2:
        _check_args(op, args)


def normalize_command(op: str, args: tuple[int, ...]) -> tuple[str, tuple[int, ...]]:
    if op == "fb_cmd" or op in PACKED_LINQ_OPERATIONS and len(args) == 2:
        try:
            op, decoded = decode_linq_command(op, list(args))
        except UnsupportedLinqCommand as exc:
            raise CoreBlocked(str(exc)) from exc
        args = tuple(decoded)
        _check_args(op, args)
    if op == "play_pulse":
        raise CoreBlocked("Execution behavior for play_pulse is unverified")
    if op == "fb_acq_tb_mock":
        raise CoreBlocked(MOCK_LIMITATION)
    if op in LINQ_CONFIGURATION_STREAMS:
        limitation = linq_configuration_limitation(op, list(args))
        if limitation:
            raise CoreBlocked(limitation)
    return op, args
