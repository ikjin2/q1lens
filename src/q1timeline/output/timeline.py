"""Serialize compact per-sequencer output and a bounded display window for each routed port."""
from __future__ import annotations


def output_section(states, modules):
    from q1timeline.output import from_state

    routed = {seq for module in modules.values() for seq in module.channel_map}
    lanes = {}
    models = {}
    by_id = {state.sequencer_id: state for state in states}
    for seq, state in by_id.items():
        if "output_sequence" not in state.metadata and seq not in routed:
            continue
        model = from_state(state, state.metadata.get("output_sequence", {}))
        lanes[seq] = model.to_json()
        if not model.issues:
            models[seq] = model

    ports = []
    for name, module in modules.items():
        for port in module.ports:
            routes = {seq: paths[port] for seq, paths in module.channel_map.items() if port in paths}
            lane = {"module": name, "port": port, "routes": routes, "offset": module.output_offsets.get(port, 0.0),
                    "kind": "rf" if module.rf else "baseband"}
            ports.append(lane)
            anchors = {seq: by_id[seq].metadata.get("alignment", {}) for seq in routes}
            offsets = {seq: a["offset"] for seq, a in anchors.items()
                       if a.get("confidence") == "exact" and not a.get("unaligned") and type(a.get("offset")) is int}
            lane["aligned"] = offsets.keys() == routes.keys()
            if module.rf:
                continue  # The complex envelope is drawn from the overview; it has no scalar preview.
            if routes.keys() - models.keys():
                lane["preview_issue"] = "A routed sequencer has unresolved output."
                continue
            if len(routes) > 1 and offsets.keys() != routes.keys():
                lane["preview_issue"] = "Exact alignment is required to mix this port's sequencers."
                continue
            basis = "aligned" if offsets.keys() == routes.keys() else "local"
            if basis == "local":
                offsets = dict.fromkeys(routes, 0)
            try:
                # Ring-down can extend past waveform memory. Keep previews bounded on every path.
                start = min(offsets.values(), default=0)
                if routes and all("simulation" in by_id[seq].metadata for seq in routes):
                    # A long simulation prefill would fill the entire preview with idle samples.
                    first = min((offsets[seq] + p.rt_t0.value for seq in routes
                                 for p in by_id[seq].rt_packets[:1] if p.rt_t0 is not None), default=start)
                    if first >= start + 256:
                        start = first
                end = start + 256
                if routes and not any(models[seq].filtered for seq in routes):
                    end = min(end, max(offsets[seq] + models[seq].waveform_end.evaluate(models[seq].params) for seq in routes))
                lane["preview"] = {"t0": start, "dt_ns": 1, "time_basis": basis,
                                   "values": module.samples(models, port, start, end, time_offsets=offsets)}
            except (KeyError, ValueError) as exc:
                lane["preview_issue"] = (f"Missing output parameter {exc.args[0]}" if isinstance(exc, KeyError)
                                         else str(exc))
    section = {"sequencers": lanes, "ports": ports}
    section["modules"] = [
        {"name": name, "type": module.module_type, **({"cluster": module.cluster} if module.cluster else {}),
         **({"lo_frequency_hz": dict(module.lo_frequency_hz)} if module.lo_frequency_hz else {})}
        for name, module in modules.items()
    ]
    _attach_rack(section, by_id, lanes)
    return section


def _attach_rack(section, by_id, lanes):
    """Add what the outputs rack draws: parameter values, a shared time frame and a full-extent overview."""
    from q1timeline.output import from_state
    from q1timeline.output.window import Scene, embedded_waveforms

    base = {}
    project = {}
    for seq, lane in lanes.items():
        state = by_id[seq]
        sequence = state.metadata.get("output_sequence", {})
        concrete = from_state(state, sequence)
        base[seq] = concrete
        symbolic = state.metadata.get("output_symbolic")
        if symbolic is None or concrete.issues:
            continue
        # Parameters given by the project are resolved during analysis. A second pass that leaves
        # them open keeps their names in the formulas; it is used only when it reproduces the timing.
        open_state, values = symbolic
        candidate = from_state(open_state, sequence)
        given = {name: value for name, value in values.items() if type(value) is int}
        try:
            if candidate.issues or not candidate.parameter_names:
                continue
            bound = candidate.bind({k: v for k, v in given.items() if k in candidate.parameter_names})
            if (bound.duration.evaluate(bound.params) != concrete.duration.evaluate(concrete.params)
                    or bound.waveform_end.evaluate(bound.params) != concrete.waveform_end.evaluate(concrete.params)):
                continue
        except (KeyError, ValueError):
            continue
        base[seq] = candidate
        lane["symbolic"] = candidate.to_json()
        project.update({k: v for k, v in given.items() if k in candidate.parameter_names})
    offsets = {}
    for seq in base:
        anchor = by_id[seq].metadata.get("alignment", {})
        exact = anchor.get("confidence") == "exact" and not anchor.get("unaligned") and type(anchor.get("offset")) is int
        offsets[seq] = anchor["offset"] if exact else 0
    for seq, memory in embedded_waveforms({seq: model for seq, model in base.items() if not model.issues}).items():
        lanes[seq]["waveforms"] = memory
    section["time_offsets"] = offsets
    try:
        scene = Scene(base, section["ports"], offsets, project)
        extent = scene.extent()
        section["parameters"] = scene.parameters
        section["extent"] = extent
        section["overview"] = scene.window(extent["t0"], extent["t1"])
    except (KeyError, ValueError, OverflowError) as exc:
        section["overview_issue"] = (f"Missing output parameter {exc.args[0]}" if isinstance(exc, KeyError) else str(exc))
