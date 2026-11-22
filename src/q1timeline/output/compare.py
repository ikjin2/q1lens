"""Compare two fixed-input project outputs on the same 1 ns port sample grid."""
from __future__ import annotations

from dataclasses import replace
from functools import partial
from pathlib import Path

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.api import analyze_project_states
from q1timeline.output import from_state
from q1timeline.project import load_project_config


def _port_sampler(config, selector, params):
    module_name, _, port = selector.rpartition(".")
    matches = [module for module in config.modules.values()
               if (not module_name or module.name == module_name) and port in module.ports]
    if not matches:
        raise ValueError(f"No configured output port {selector!r} in {config.root}")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous port {port!r}. Select a module: "
                         + ", ".join(f"{module.name}.{port}" for module in matches))
    module, = matches
    routes = {seq for seq, ports in module.channel_map.items() if port in ports}
    execution = "simulation" if config.analysis.execution_mode == "simulation" else "concrete"
    config = replace(config, params=params, analysis=replace(config.analysis, execution_mode=execution))
    states = analyze_project_states(config)
    sidecars = {str(seq.sequence_json) for seq in config.sequencers if seq.id in routes and seq.sequence_json}
    for diagnostic in config.diagnostics:
        if diagnostic.severity == "error" or diagnostic.details.get("file") in sidecars:
            raise ValueError(diagnostic.message)
    mode = config.alignment_for_execution(execution)
    align_timelines(states, mode=mode, anchor_kinds=config.alignment_anchor_kinds)
    basis = mode
    if mode == "first_anchor":
        basis += ":" + ",".join(config.alignment_anchor_kinds)
    offsets = {}
    outputs = {}
    for state in states:
        seq = state.sequencer_id
        if seq not in routes:
            continue
        anchor = state.metadata["alignment"]
        if anchor.get("fallback") or anchor["unaligned"] or anchor["confidence"] != "exact":
            if len(routes) > 1 or execution == "simulation":
                raise ValueError(f"{config.root}: missing exact {mode} alignment for {seq}")
            # As in Hardware, a single unresolved anchor can still show the local output.
            basis = "local"
            offsets[seq] = 0
        else:
            offsets[seq] = anchor["offset"]
        model = from_state(state, state.metadata.get("output_sequence", {}))
        if model.issues:
            raise ValueError(f"{config.root}, {seq}: " + "; ".join(model.issues))
        outputs[seq] = model.bind({name: params[name] for name in model.parameter_names if name in params})
    return module, basis, execution, partial(module.samples, outputs, port, time_offsets=offsets)


def compare_output(before: Path, after: Path, *, port: str, start_ns: int, end_ns: int, params=None) -> dict:
    """Compare normalized, clipped port samples on [start_ns, end_ns)."""
    if end_ns <= start_ns:
        raise ValueError("The sample window requires --start-ns < --end-ns.")
    configs = [load_project_config(path) for path in (before, after)]
    if params is None:
        left, right = (config.params for config in configs)
        conflicts = sorted(name for name in left.keys() & right.keys() if left[name] != right[name])
        if conflicts:
            raise ValueError("Conflicting project inputs: " + ", ".join(conflicts) + ". Supply shared --params.")
        params = {**left, **right}
    (left_module, left_basis, left_mode, left_samples), (right_module, right_basis, right_mode, right_samples) = (
        _port_sampler(config, port, params) for config in configs
    )
    if left_module.name != right_module.name or left_module.module_type != right_module.module_type:
        raise ValueError(f"Port {port!r} resolves to different hardware: "
                         f"{left_module.name} ({left_module.module_type}), {right_module.name} ({right_module.module_type}).")
    if left_basis != right_basis or left_mode != right_mode:
        raise ValueError(f"Projects use different time bases: {left_mode}/{left_basis}, {right_mode}/{right_basis}.")
    if start_ns < 0 and left_basis in {"local", "none"}:
        raise ValueError("Negative sample times require an aligned port.")
    qualified = f"{left_module.name}.{port.rpartition('.')[2]}"
    result = {"port": qualified, "module_type": left_module.module_type, "execution_mode": left_mode,
              "start_ns": start_ns, "end_ns": end_ns, "time_basis": left_basis, "inputs": params}
    # Keep allocations proportional to one block, even for long schedules.
    for start in range(start_ns, end_ns, 16384):
        end = min(end_ns, start + 16384)
        for time, (left, right) in enumerate(zip(left_samples(start, end), right_samples(start, end)), start):
            if left != right:
                return {**result, "status": "counterexample",
                        "first_difference": {"time_ns": time, "before": left, "after": right},
                        "message": f"{qualified} first differs at {time} ns: before {left}, after {right}."}
    return {**result, "status": "proved",
            "message": f"{qualified} matches at all {end_ns - start_ns} samples in [{start_ns}, {end_ns}) ns."}
