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
            lane = {"module": name, "port": port, "routes": routes, "offset": module.output_offsets.get(port, 0.0)}
            ports.append(lane)
            if routes.keys() - models.keys():
                lane["preview_issue"] = "A routed sequencer has unresolved output."
                continue
            anchors = {seq: by_id[seq].metadata.get("alignment", {}) for seq in routes}
            offsets = {seq: a["offset"] for seq, a in anchors.items()
                       if a.get("confidence") == "exact" and not a.get("unaligned") and type(a.get("offset")) is int}
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
    return {"sequencers": lanes, "ports": ports}
