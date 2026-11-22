"""Closed-form assertions on normalized, pre-clipping baseband port outputs."""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from functools import cached_property
from itertools import accumulate
from math import gcd, lcm
from time import monotonic

import z3

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.expr import Expr
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.smt import Query, verify_domain
from q1timeline.analysis.values import Concrete
from q1timeline.execution.operands import resolve_register_name
from q1timeline.execution.validation import is_packed_feedback_register_form
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.output import Repeat, from_state
from q1timeline.output.modules import ModuleConfig
from q1timeline.q1asm import core_devices, core_rt
from q1timeline.q1asm.ast import numeric_defs, with_def_values
from q1timeline.simulation.signals import PHASE_TICKS_PER_TURN
from q1timeline.verification_annotations import with_assumed_parameters


def _real(value):
    return z3.RealVal(str(Fraction(value)))


class _Wave:
    """Exact statistics of stored samples, including zero outside memory."""

    def __init__(self, samples, deadline):
        self.samples = samples
        low, high = min(samples), max(samples)
        self.maximum = Fraction(max(abs(low), abs(high)))
        self.uniform = low if low == high else None
        self.tables = {}
        self.deadline = deadline

    @cached_property
    def prefix(self):
        return tuple(accumulate(map(Fraction, self.samples), initial=Fraction(0)))

    def lookup(self, index, *, prefix=False):
        index = z3.simplify(index)
        if self.uniform is not None:
            size = len(self.samples)
            if prefix:
                return _real(self.uniform) * z3.If(index < 0, 0, z3.If(index > size, size, index))
            return z3.If(z3.And(index >= 0, index < size), _real(self.uniform), 0)
        values = self.prefix if prefix else self.samples
        if z3.is_int_value(index):
            i = index.as_long()
            if 0 <= i < len(values):
                return _real(values[i])
            return _real(self.prefix[-1] if prefix and i >= len(values) else 0)
        if prefix not in self.tables:
            table = z3.K(z3.IntSort(), _real(0))
            for i, value in enumerate(values):
                if i % 256 == 0 and monotonic() >= self.deadline:
                    raise ValueError("Output verification timed out while indexing waveform memory.")
                if value:
                    table = z3.Store(table, i, _real(value))
            self.tables[prefix] = table
        entry = z3.Select(self.tables[prefix], index)
        return z3.If(index >= len(values), _real(self.prefix[-1]), entry) if prefix else entry


def _module(scenario):
    name = scenario.metadata.get(scenario.seq, {}).get("module")
    matches = [m for m in scenario.modules.values() if m.name == name or scenario.seq in m.channel_map]
    if len(matches) == 1:
        return matches[0]
    if matches or (name in scenario.modules):
        raise ValueError("Select an unambiguous module for this output assertion.")
    return ModuleConfig(scenario.seq, "QRM", {scenario.seq: {"out0": "I", "out1": "Q"}})


def _register_ranges_proven(state, domain):
    """Check issued operands, including values overwritten before the next RT capture."""
    instructions = {item.source: item for item in state.instructions_by_pc.values()}
    loops = {}
    varying_reads = state.metadata.get("loop_varying_operand_registers", {})
    setters = {}
    for event in state.events:
        if event.kind == "loop_block":
            for eid in event.meta["first_iteration_event_ids"]:
                loops.setdefault(eid, []).append(event.meta)
        elif event.kind == "latched_state_pending":
            setters[event.meta.get("q1_issue_event_id")] = event.id
    for issue in state.events:
        if issue.kind != "q1_issue" or issue.meta.get("loop_iteration_index", 0):
            continue
        item = instructions.get(issue.source)
        if not item or is_packed_feedback_register_form(item, state):
            continue
        ranges = core_rt.OPERAND_RANGES.get(item.op, core_devices.DEVICE_RANGES.get(item.op, ()))
        for index, role, low, high in ranges:
            if index >= len(item.args):
                continue
            register = resolve_register_name(item.args[index], state)
            if register is None or item.op == "set_mrk":  # Marker words decode to their low four bits.
                continue
            varying = [
                loop for loop in loops.get(issue.id, ())
                if register in varying_reads.get(loop["loop_id"], {}).get(item.pc, loop["uncertain_registers"])
            ]
            try:
                if varying:
                    # The compact loop records the per-iteration value of an affine latch write.
                    if len(varying) != 1:
                        return False
                    loop = varying[0]
                    ramp = loop["latched_affine"][setters[issue.id]][index]
                    count = Expr.from_value_json(loop.get("count_expr", loop["count"])).bounds(domain)[1]
                    first = ramp["intercept"]
                    lower, upper = sorted((first, first + (count - 1) * ramp["slope"]))
                else:
                    word = Expr.from_value_json(issue.meta["register_reads"][register])
                    if role in {"awg_gain", "awg_offset", "frequency"}:
                        word = word.signed()
                    lower, upper = word.bounds(domain)
            except (KeyError, ValueError):
                return False
            if lower < low or upper > high:
                return False
    return True


def _settings(scenario, seq):
    sequence = scenario.memories.get(seq, {})
    physical = scenario.simulation.get("physical", {}).get("sequencers", {}).get(seq, {})
    settings = sequence.get("output_settings", sequence.get("settings", {}))
    if not isinstance(settings, dict):
        raise ValueError(f"{seq}: Sequencer output settings must be a mapping.")
    return {**physical, **settings}


def _outputs(scenario, domain, module, inputs, max_steps, max_paths, deadline):
    from q1timeline.verification import _execution_problem, _validate_source

    states = []
    # A DEF range is local to the annotated sequencer. An assumed parameter is
    # shared wherever the project references it without a local DEF.
    for seq in dict.fromkeys([scenario.seq, *module.channel_map]):
        program = scenario.programs[seq]
        varying = set(domain) if seq == scenario.seq else scenario.free_inputs - program.defs.keys()
        params = {k: v for k, v in scenario.params.items() if k.lstrip("$") not in varying}
        if inputs is None:
            # Immediate-operand legality is checked even for writes overwritten
            # before their RT capture. These per-operand domains are intervals.
            for i in {0, 1}:
                endpoint = {key: domain[key][i] for key in varying}
                sample = with_def_values(program, endpoint) if seq == scenario.seq else program
                _validate_source(sample, {**params, **endpoint})
            if seq == scenario.seq:
                program = replace(program, defs={k: v for k, v in program.defs.items() if k not in varying})
        else:
            if seq == scenario.seq:
                program = with_def_values(program, inputs)
            params.update({name: inputs[name] for name in varying})
        external = scenario.receiver_inputs(scenario.case_params(inputs), seq=seq) if inputs is not None else None
        state = interpret_program(program, sequencer_id=seq, params=params, external=external,
                                  execution_mode="analysis" if inputs is None else "concrete",
                                  max_steps=max_steps, deadline=deadline)
        if inputs is not None:
            _validate_source(program, params)
            reason = _execution_problem(state)
            if reason:
                raise ValueError(f"{seq}: {reason}")
        else:
            reason = next((d.message for d in state.diagnostics if d.severity == "error" or
                           d.category in {"analysis_incomplete", "unresolved_branch"} or
                           (d.category == "unresolved_symbol" and
                            d.details.get("symbol", "").removeprefix("$") not in varying)), None)
            if reason or not any(e.kind == "stop" for e in state.events):
                raise ValueError(reason or "Output verification requires a reachable program end.")
            if not _register_ranges_proven(state, domain):
                # Use instruction-by-instruction execution when the compact trace cannot
                # certify every register read, including reads on later loop iterations.
                candidate = with_assumed_parameters(scenario.programs[seq], {k: domain[k] for k in varying})
                ranges = {k: domain[k] for k in varying & numeric_defs(candidate).keys()}
                valid = verify_domain(candidate, domain=ranges, query=Query("q1_time", ">=", 0),
                                      max_steps=max_steps, max_paths=max_paths, deadline=deadline, params=params)
                if valid.status != "proved":
                    raise ValueError(f"{seq}: {valid.reason or 'Could not establish valid instruction operands.'}")
        states.append(state)
    mode = scenario.alignment_mode if len(states) > 1 else "none"
    return _output_models(scenario, module, states, mode)


def _output_models(scenario, module, states, mode):
    outputs = {}
    for state in states:
        seq = state.sequencer_id
        if seq != scenario.seq and seq not in module.channel_map:
            continue
        state.metadata.update(scenario.metadata.get(seq, {}))
        state.metadata["hardware_parameters"] = scenario.hardware.get(seq, {})
        state.metadata["output_sequence"] = scenario.memories.get(seq, {})
        state.metadata["output_settings"] = _settings(scenario, seq)
        out = from_state(state, state.metadata["output_sequence"])
        if out.issues:
            raise ValueError(f"{seq}: {'; '.join(out.issues)}")
        if out.iir_a != (1.0,) or out.iir_b != (1.0,):
            raise ValueError("Port property verification does not support nontrivial IIR filters.")
        outputs[seq] = out
    alignment = align_timelines(states, mode=mode, anchor_kinds=scenario.alignment_anchor_kinds)
    if alignment.confidence != "exact" or any(not isinstance(o, Concrete) for o in alignment.sequencer_offsets.values()):
        raise ValueError("Port contributors require an exact RT alignment for this output assertion.")
    return states, outputs, {seq: o.value for seq, o in alignment.sequencer_offsets.items()}


class _Proof:
    def __init__(self, outputs, offsets, domain, deadline):
        self.outputs = outputs
        self.offsets = offsets
        self.deadline = deadline
        self.symbols = {name: z3.Int(name) for name in domain}
        self.solver = z3.Solver()
        for name, (low, high) in domain.items():
            self.solver.add(self.symbols[name] >= low, self.symbols[name] <= high)
        self.waves = {seq: {idx: _Wave(data, deadline) for idx, data in out.waveforms.items()} for seq, out in outputs.items()}
        ends = [self.translate(out.waveform_end, self.symbols) + offsets[seq] for seq, out in outputs.items()]
        self.end = ends[0]
        for end in ends[1:]:
            self.end = z3.If(self.end >= end, self.end, end)
        self.end = z3.simplify(self.end)
        for seq in outputs:
            self._validate(seq, self.nodes(seq), self.symbols, z3.BoolVal(True))

    def solve(self, condition):
        remaining = int((self.deadline - monotonic()) * 1000)
        if remaining <= 0:
            raise ValueError("Output verification timed out.")
        self.solver.set(timeout=remaining)
        self.solver.push()
        try:
            self.solver.add(condition)
            result = self.solver.check()
            if result == z3.unknown:
                raise ValueError(f"Output solver: {self.solver.reason_unknown()}.")
            return self.solver.model() if result == z3.sat else None
        finally:
            self.solver.pop()

    def minimum(self, condition, variable, lower, model):
        """Retain a feasible upper bound while proving the integer prefix below it empty."""
        upper = model.eval(variable, model_completion=True).as_long()
        while lower < upper:
            mid = (lower + upper) // 2
            candidate = self.solve(z3.And(condition, variable <= mid))
            if candidate is None:
                lower = mid + 1
            else:
                model = candidate
                upper = model.eval(variable, model_completion=True).as_long()
        return upper, model

    @staticmethod
    def translate(expr, scope):
        missing = expr.free_symbols() - scope.keys()
        if missing:
            raise ValueError(f"Missing an @assume range for {', '.join(sorted(missing))}.")
        return expr.to_z3(scope)

    def nodes(self, seq):
        out = self.outputs[seq]
        nodes = [(node, None) for node in out.segments]
        if out.tail is not None:
            nodes.append((out.tail, self.end - self.offsets[seq]))
        return nodes

    def _validate(self, seq, nodes, scope, guard):
        for node, _ in nodes:
            if isinstance(node, Repeat):
                fields = (node.start, node.period, node.count)
            else:
                fields = (node.start, node.end, node.sample_offset, *node.gain, *node.offset, node.frequency, node.phase)
            constraints = []
            if not isinstance(node, Repeat):
                for wave_id in node.waveforms:
                    if isinstance(wave_id, Expr):
                        value = self.translate(wave_id, scope)
                        constraints.extend(wave_id.constraints_z3(scope))
                        constraints.append(z3.Or(*[value == i for i in self.waves[seq]]))
            for field in fields:
                self.translate(field, scope)
                constraints.extend(field.constraints_z3(scope))
            if constraints and self.solve(z3.And(guard, z3.Not(z3.And(*constraints)))) is not None:
                raise ValueError("The assumed inputs include a value outside the valid instruction domain.")
            if isinstance(node, Repeat):
                index = z3.FreshInt("iteration")
                count = self.translate(node.count, scope)
                self._validate(seq, [(n, None) for n in node.body], {**scope, node.iteration: index},
                               z3.And(guard, index >= 0, index < count))

    def interval_peak(self, seq, path, nodes, domain):
        result = Fraction(0)
        for node, _ in nodes:
            if isinstance(node, Repeat):
                _, high = node.count.bounds(domain)
                value = self.interval_peak(seq, path, [(n, None) for n in node.body],
                                           {**domain, node.iteration: (0, max(0, high - 1))})
            else:
                def bound(p):
                    key = node.waveforms[p]
                    if isinstance(key, Expr):
                        maximum = max(w.maximum for w in self.waves[seq].values())
                    else:
                        maximum = self.waves[seq][key].maximum if key is not None else 0
                    gain = max(map(abs, node.gain[p].bounds(domain)))
                    bias = max(map(abs, node.offset[p].bounds(domain)))
                    out = self.outputs[seq]
                    return ((gain * maximum * abs(Fraction(out.static_gain[p])) + bias) / Fraction(32768)
                            + abs(Fraction(out.static_offset[p])))

                rotated = any(v.bounds(domain) != (0, 0) for v in (node.frequency, node.phase))
                value = bound(0) + bound(1) if rotated else bound(path)
            result = max(result, value)
        return result

    def component(self, seq, path, node, scope, index, length=None):
        wave_id = node.waveforms[path]
        def read(waveform):
            return waveform.lookup(index) if length is None else (
                waveform.lookup(index + length, prefix=True) - waveform.lookup(index, prefix=True))
        sample = _real(0)
        if isinstance(wave_id, Expr):
            key = self.translate(wave_id, scope)
            for i, wave in self.waves[seq].items():
                sample = z3.If(key == i, read(wave), sample)
        elif wave_id is not None:
            sample = read(self.waves[seq][wave_id])
        gain, bias = (self.translate(pair[path], scope) for pair in (node.gain, node.offset))
        out = self.outputs[seq]
        amplitude = gain * _real(out.static_gain[path]) / _real(32768)
        offset = bias / _real(32768) + _real(out.static_offset[path])
        return amplitude * sample + offset * (1 if length is None else length)

    def contribution(self, seq, path, nodes, scope, time):
        total = _real(0)
        for node, end_override in nodes:
            start = self.translate(node.start, scope)
            if isinstance(node, Repeat):
                count, period = (self.translate(v, scope) for v in (node.count, node.period))
                index = z3.FreshInt("iteration") if time is None else (time - start) / period
                body = self.contribution(seq, path, [(n, None) for n in node.body], {**scope, node.iteration: index},
                                         None if time is None else (time - start) % period)
                if time is None:
                    # Sum an affine per-iteration area without enumerating the count.
                    first, second = (z3.substitute(body, (index, z3.IntVal(i))) for i in (0, 1))
                    delta = second - first
                    residual = z3.simplify(body - first - index * delta, som=True)
                    if not z3.is_true(z3.simplify(residual == 0)):
                        if self.solve(z3.And(index >= 0, index < count, residual != 0)) is not None:
                            raise ValueError("The repeated area has no proven affine recurrence.")
                    total += first * count + delta * count * (count - 1) / 2
                else:
                    total += z3.If(z3.And(time >= start, time < start + count * period), body, 0)
                continue
            end = self.translate(node.end, scope) if end_override is None else end_override
            if time is None and z3.is_true(z3.simplify(end == start)):
                continue
            offset = self.translate(node.sample_offset, scope)
            phase = [self.translate(v, scope) for v in (node.frequency, node.phase)]
            rotated = not all(z3.is_true(z3.simplify(v == 0)) for v in phase)
            if time is None:
                if rotated:
                    raise ValueError("An NCO envelope bound does not determine signed area.")
                total += z3.If(end > start, self.component(seq, path, node, scope, offset, end - start), 0)
            else:
                index = offset + time - start
                sample = self.component(seq, path, node, scope, index)
                if rotated:
                    sample = self.rotate_bound(sample, self.component(seq, 1 - path, node, scope, index))
                # A sequencer holds its last captured settings indefinitely.
                active = time >= start if end_override is not None else z3.And(time >= start, time < end)
                total += z3.If(active, sample, 0)
        return z3.simplify(total)

    def rotate_bound(self, value, other):
        component = z3.FreshReal("rotated_component")
        self.solver.add(component * component <= value * value + other * other)
        return component

    def steady(self, routes, port_bias):
        """Possible port values after every waveform has drained."""
        total = _real(port_bias)
        for seq, path in routes:
            out = self.outputs[seq]
            tail = out.tail
            levels = [self.translate(v, self.symbols) / _real(32768) + _real(out.static_offset[p])
                      for p, v in enumerate(tail.offset)]
            rotated = any(not z3.is_true(z3.simplify(self.translate(v, self.symbols) == 0))
                          for v in (tail.frequency, tail.phase))
            total += self.rotate_bound(*levels) if rotated else levels[path]
        return z3.simplify(total, som=True)

    def port_start(self, routes):
        return min((self.offsets[seq] for seq, _ in routes), default=0)

    def port_formula(self, routes, port_bias, time):
        total = _real(port_bias) * (self.end - self.port_start(routes) if time is None else 1)
        for seq, path in routes:
            local_time = None if time is None else time - self.offsets[seq]
            total += self.contribution(seq, path, self.nodes(seq), self.symbols, local_time)
        return z3.simplify(total, som=True)


def _counterexample(module, port, ns, observed, states, concrete, offsets, inputs, area, include_replay, method):
    from q1timeline.verification import _focus

    subjects = [s for s in states if port in module.channel_map.get(s.sequencer_id, {})]
    if not subjects:  # A module DC-only port has no driving packet.
        subjects = [s for s in states if s.sequencer_id in concrete]
    events = []
    for s in subjects:
        output = concrete[s.sequencer_id]
        local = ns - offsets[s.sequencer_id]
        if local < 0:
            continue
        active = next((n for n in output.segments if n.start.evaluate({}) <= local < n.end.evaluate({})),
                      output.tail)
        events.extend((s, e) for e in s.events if e.id == active.source_event)
    if not events:
        events = [(s, e) for s in subjects for e in s.events if e.kind == "q1_issue"]
    if not events:
        raise ValueError("Concrete replay produced no source event.")
    state, event = max(events, key=lambda pair: pair[1].t0.value + offsets[pair[0].sequencer_id])
    witness = _focus(states, state, event, inputs, False)
    mode = state.metadata["alignment"]["mode"]
    if include_replay:
        witness["timeline"] = timeline_ir_from_states(states, project={"alignment_mode": mode, "virtual": True},
                                                      modules={module.name: module})
    if mode != "none":
        witness["time_basis"] = "aligned"
    witness.update(port=port, value=observed, time_ns=ns)
    if not area:
        witness["earliest_sample"] = True
    finding = "area" if area else "earliest violating sample"
    return {"status": "counterexample", "counterexample": witness, "method": method,
            "message": f"{module.name}.{port} {finding} = {observed:g} at {ns} ns."}


def _check_fir(scenario, domain, query, module, ports, max_steps, max_paths, deadline, include_replay):
    from q1timeline.verification import _execution_problem

    if query.kind == "net_zero" or any(low != high for low, high in domain.values()):
        raise ValueError("FIR verification requires fixed inputs and a peak or no_clip assertion.")
    inputs = {name: low for name, (low, _) in domain.items()}
    if scenario.scheduled_inputs:
        states = scenario.simulate(inputs, max_steps, deadline)
        for state in states:
            if reason := _execution_problem(state):
                raise ValueError(f"{state.sequencer_id}: {reason}")
        states, outputs, offsets = _output_models(scenario, module, states, scenario.simulation_alignment_mode)
        method = "FIR + Simulation"
    else:
        states, outputs, offsets = _outputs(scenario, domain, module, inputs, max_steps, max_paths, deadline)
        method = "FIR + Concrete"
    starts = {port: min((offsets[s] for s, paths in module.channel_map.items() if port in paths), default=0)
              for port in ports}
    # Once all waveform memory and FIR histories have drained, only held offsets
    # remain. A rotated nonzero offset repeats on the integer-ns grid.
    steady = max(starts.values())
    period = 1
    for seq, paths in module.channel_map.items():
        if not any(port in paths for port in ports):
            continue
        output = outputs[seq]
        steady = max(steady, offsets[seq] + output.waveform_end.evaluate({}) + len(output.fir) - 1)
        if any(offset.evaluate({}) / 32768 + static for offset, static in zip(output.tail.offset, output.static_offset)):
            frequency = output.tail.frequency.evaluate({})
            period = lcm(period, PHASE_TICKS_PER_TURN // gcd(frequency, PHASE_TICKS_PER_TURN))
    end = steady + period
    for block in range(min(starts.values()), end, 256):
        stop = min(block + 256, end)
        winner = None
        for port in ports:
            start = max(block, starts[port])
            if start >= stop:
                continue
            samples = module.samples(outputs, port, start, stop, clip=False, time_offsets=offsets)
            for ns, sample in enumerate(samples, start):
                if abs(sample) > query.limit:
                    candidate = (ns, port, sample)
                    if winner is None or candidate < winner:
                        winner = candidate
                    break
        if monotonic() >= deadline:
            raise ValueError("Output verification timed out while sampling the FIR output.")
        if winner:
            ns, port, observed = winner
            return _counterexample(module, port, ns, observed, states, outputs, offsets, inputs, False,
                                   include_replay, method)
    return {"status": "proved", "method": method,
            "message": f"{query.kind} holds on {', '.join(ports)} for the fixed inputs, including the FIR tail."}


def check_output(scenario, domain, query, *, max_steps, max_paths, deadline, include_replay):
    try:
        module = _module(scenario)
        ports = [query.port] if query.port else module.ports
        if not ports or any(p not in module.ports for p in ports):
            raise ValueError("The assertion requires a configured output port.")
        if any(_settings(scenario, seq).get("fir", [1]) != [1] for seq in (scenario.seq, *module.channel_map)):
            return _check_fir(scenario, domain, query, module, ports, max_steps, max_paths, deadline, include_replay)
        if scenario.output_proof is None:
            _, outputs, offsets = _outputs(scenario, domain, module, None, max_steps, max_paths, deadline)
            scenario.output_proof = _Proof(outputs, offsets, domain, deadline)
        proof = scenario.output_proof
        methods = set()
        area = query.kind == "net_zero"
        time = None if area else z3.FreshInt("sample_ns")
        winner = None
        for port in sorted(ports):
            routes = [(seq, int(mapping[port] == "Q")) for seq, mapping in module.channel_map.items() if port in mapping]
            bias = module.output_offsets.get(port, 0)
            start = proof.port_start(routes)
            if winner is not None and start >= winner[0]:
                continue
            bound = abs(Fraction(bias)) + sum(proof.interval_peak(seq, path, proof.nodes(seq), domain)
                                                for seq, path in routes)
            if bound <= (0 if area else query.limit):
                methods.add("interval")
                continue
            if area:
                persistent = proof.steady(routes, bias)
                settled = z3.simplify(persistent == 0)
                if not z3.is_true(settled) and proof.solve(z3.Not(settled)) is not None:
                    raise ValueError(f"{module.name}.{port} can have a persistent output after waveform drain. "
                                     "A convergent signed area could not be established.")
            value = proof.port_formula(routes, bias, time)
            violation = value != 0 if area else z3.And(time >= start, z3.Abs(value) > _real(query.limit))
            if winner is not None:
                # A tie belongs to the earlier port in sorted order.
                violation = z3.And(violation, time < winner[0])
            if area and z3.is_false(z3.simplify(violation)):
                methods.add("closed form")
                continue
            model = proof.solve(violation)
            if model is None:
                methods.add("Z3")
                continue
            if area:
                winner = (None, port, routes, violation, model)
                break
            ns, model = proof.minimum(violation, time, start, model)
            winner = (ns, port, routes, z3.And(violation, time == ns), model)
        if winner is None:
            method = " + ".join(sorted(methods))
            return {"status": "proved", "method": method, "message": f"{query.kind} holds on {', '.join(ports)} ({method})."}

        ns, port, routes, constraint, model = winner
        inputs = {}
        for name in sorted(proof.symbols):
            var = proof.symbols[name]
            inputs[name], model = proof.minimum(constraint, var, domain[name][0], model)
            constraint = z3.And(constraint, var == inputs[name])
        states, concrete, offsets = _outputs(scenario, domain, module, inputs, max_steps, max_paths, deadline)
        if offsets != proof.offsets:
            raise ValueError("Concrete replay did not reproduce the analyzed clock alignment.")
        if area:
            replay = _Proof(concrete, offsets, {}, deadline)
            ns = max(replay.port_start(routes), replay.end.as_long() - 1)
            observed = replay.port_formula(routes, module.output_offsets.get(port, 0), None)
            if not z3.is_rational_value(observed) or observed.as_fraction() == 0:
                raise ValueError("Concrete replay did not reproduce the area violation.")
            observed = float(observed.as_fraction())
        else:
            observed, = module.samples(concrete, port, ns, ns + 1, clip=False, time_offsets=offsets)
            if abs(Fraction(observed)) <= query.limit:
                raise ValueError("The envelope exceeds the limit but the bound NCO path did not reproduce a violation.")
        return _counterexample(module, port, ns, observed, states, concrete, offsets, inputs, area,
                               include_replay, "Z3 + Concrete")
    except (ValueError, TimeoutError, KeyError) as exc:
        return {"status": "unknown", "message": str(exc)}
