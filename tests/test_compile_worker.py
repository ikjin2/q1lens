from __future__ import annotations

import json
from collections import UserDict
from pathlib import Path
from types import SimpleNamespace

from qbstimeline import annotate, sym
from qbstimeline import compile_worker
from qbstimeline.compile_worker import (
    Q1ASMProgram,
    _extract_operations,
    _extract_schedule_structure,
    _merge_object_source_trace,
    _valid_q1asm_provenance_rows,
    _write_q1asm_files,
    analyze_project,
    extract_q1asm_programs,
)
from qbstimeline.project import ProjectConfig, load_project_config
from qbstimeline.source_tracing import SourceTrace, SourceTraceLocation


class FakeSequencerSettings:
    def __init__(self, program: str) -> None:
        self.sequence = {"program": program, "waveforms": {}}


class FakeCompiledSchedule(dict):
    @property
    def compiled_instructions(self):
        return self["compiled_instructions"]

    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()


def test_extract_q1asm_programs_supports_dict_and_settings_objects() -> None:
    compiled_instructions = {
        "cluster0": {
            "cluster0_module2": {
                "sequencers": {
                    "seq0": {"sequence": {"program": "wait_sync 4\nstop\n"}},
                    "seq1": FakeSequencerSettings("play 0,1,4\nstop\n"),
                }
            }
        }
    }

    programs = extract_q1asm_programs(compiled_instructions)

    assert [program.sequencer_id for program in programs] == [
        "cluster0_cluster0_module2_seq0",
        "cluster0_cluster0_module2_seq1",
    ]
    assert programs[0].relative_file == Path("q1asm/cluster0_cluster0_module2_seq0.q1asm")
    assert programs[1].program == "play 0,1,4\nstop\n"


def test_extract_q1asm_programs_disambiguates_normalized_sequencer_ids() -> None:
    compiled_instructions = {
        "cluster0": {
            "mod-a": {"sequencers": {"seq0": {"sequence": {"program": "play 0,0,4\nstop\n"}}}},
            "mod_a": {"sequencers": {"seq0": {"sequence": {"program": "play 1,1,4\nstop\n"}}}},
        }
    }

    programs = extract_q1asm_programs(compiled_instructions)

    assert [program.sequencer_id for program in programs] == [
        "cluster0_mod_a_seq0",
        "cluster0_mod_a_seq0_2",
    ]
    assert [program.relative_file for program in programs] == [
        Path("q1asm/cluster0_mod_a_seq0.q1asm"),
        Path("q1asm/cluster0_mod_a_seq0_2.q1asm"),
    ]


def test_valid_q1asm_provenance_rows_filters_operand_mappings_outside_row_range() -> None:
    rows = _valid_q1asm_provenance_rows(
        [
            {
                "source_id": "pulse:x0:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "x0",
                "sequencer_id": "cluster0_module2_seq0",
                "q1asm_line_start": 1,
                "q1asm_line_end": 1,
                "operand_mappings": [
                    {
                        "line": 1,
                        "instruction": "play",
                        "operand_index": 2,
                        "role": "duration",
                        "numeric_value": 20,
                        "unit": "ns",
                    },
                    {
                        "line": 99,
                        "line_end": 100,
                        "instruction": "play",
                        "operand_index": 2,
                        "role": "duration",
                        "numeric_value": 20,
                        "unit": "ns",
                    },
                ],
            }
        ],
        [
            Q1ASMProgram(
                sequencer_id="cluster0_module2_seq0",
                relative_file=Path("q1asm/cluster0_module2_seq0.q1asm"),
                program="play 0,1,20\nstop\n",
                path=("cluster0", "module2", "seq0"),
            )
        ],
    )

    assert rows[0]["operand_mappings"] == [
        {
            "line": 1,
            "instruction": "play",
            "operand_index": 2,
            "role": "duration",
            "numeric_value": 20,
            "unit": "ns",
        }
    ]


def test_valid_q1asm_provenance_rows_rejects_ambiguous_collision_base_id() -> None:
    rows = _valid_q1asm_provenance_rows(
        [
            {
                "source_id": "pulse:second:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "second",
                "sequencer_id": "cluster0_mod_a_seq0",
                "q1asm_line_start": 1,
                "q1asm_line_end": 1,
            }
        ],
        [
            Q1ASMProgram(
                sequencer_id="cluster0_mod_a_seq0",
                relative_file=Path("q1asm/cluster0_mod_a_seq0.q1asm"),
                program="play 0,0,4\nstop\n",
                path=("cluster0", "mod-a", "seq0"),
            ),
            Q1ASMProgram(
                sequencer_id="cluster0_mod_a_seq0_2",
                relative_file=Path("q1asm/cluster0_mod_a_seq0_2.q1asm"),
                program="play 1,1,4\nstop\n",
                path=("cluster0", "mod_a", "seq0"),
            ),
        ],
    )

    assert rows == []


def test_valid_q1asm_provenance_rows_keeps_natural_numeric_suffix_sequencer() -> None:
    rows = _valid_q1asm_provenance_rows(
        [
            {
                "source_id": "pulse:first:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "first",
                "sequencer_id": "cluster0_module2_seq0",
                "q1asm_line_start": 1,
                "q1asm_line_end": 1,
            }
        ],
        [
            Q1ASMProgram(
                sequencer_id="cluster0_module2_seq0",
                relative_file=Path("q1asm/cluster0_module2_seq0.q1asm"),
                program="play 0,0,4\nstop\n",
                path=("cluster0", "module2", "seq0"),
            ),
            Q1ASMProgram(
                sequencer_id="cluster0_module2_seq0_2",
                relative_file=Path("q1asm/cluster0_module2_seq0_2.q1asm"),
                program="play 1,1,4\nstop\n",
                path=("cluster0", "module2", "seq0_2"),
            ),
        ],
    )

    assert rows[0]["source_id"] == "pulse:first:pulse:0"


def test_write_q1asm_files_removes_stale_generated_programs(tmp_path: Path) -> None:
    stale = tmp_path / "q1asm" / "old_seq.q1asm"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")

    _write_q1asm_files(
        tmp_path,
        [
            Q1ASMProgram(
                sequencer_id="new_seq",
                relative_file=Path("q1asm") / "new_seq.q1asm",
                program="stop\n",
                path=("new_seq",),
            )
        ],
    )

    assert not stale.exists()
    assert (tmp_path / "q1asm" / "new_seq.q1asm").read_text(encoding="utf-8") == "stop\n"


def test_analyze_project_loads_entrypoints_and_writes_q1asm(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
class FakeTableData:
    def to_dict(self, orient):
        assert orient == "records"
        return [
            {
                "operation": "X(q0)",
                "port": "q0:mw",
                "clock": "q0.01",
                "abs_time": 20e-9,
                "duration": 40e-9,
                "is_acquisition": False,
            }
        ]

class FakeTimingTable:
    data = FakeTableData()

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        return FakeTimingTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"op0": {"operation_id": "x0", "abs_time": 0.0}},
            "operations": {
                "x0": {
                    "name": "X(q0)",
                    "pulse_info": [
                        {
                            "name": "DRAGPulse",
                            "port": "q0:mw",
                            "clock": "q0.01",
                            "t0": 0.0,
                            "duration": 40e-9,
                            "amp": 0.32,
                        }
                    ],
                }
            },
            "compiled_instructions": {
                "cluster0": {
                    "module2": {
                        "sequencers": {
                            "seq0": {"sequence": {"program": "wait_sync 4\\nplay 0,1,4\\nstop\\n"}}
                        }
                    }
                }
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:op0:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "op0",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 2,
                    "q1asm_line_end": 2,
                    "operand_mappings": [
                        {
                            "line": 2,
                            "instruction": "play",
                            "operand_index": 2,
                            "role": "trigger_duration",
                            "numeric_value": 4,
                            "unit": "ns",
                        }
                    ],
                }
            ],
        })

def build_schedule():
    return {"name": "demo"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=True,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["schedule"]["name"] == "demo"
    assert result.ir["status"] == "ok"
    assert result.ir["operations"][0]["id"] == "op0"
    assert result.ir["timing_table"][0]["port"] == "q0:mw"
    assert result.ir["capabilities"] == {
        "operations": True,
        "symbolic_pulses": True,
        "q1asm": True,
        "artifacts": False,
    }
    assert result.ir["symbolic_pulses"][0]["id"] == "pulse:op0:pulse:0"
    assert result.ir["symbolic_pulses"][0]["lane"] == "q0:mw / q0.01"
    assert result.ir["symbolic_values"] == []
    assert result.ir["q1asm_provenance"][0]["source_id"] == "pulse:op0:pulse:0"
    assert (
        result.ir["q1asm_provenance"][0]["operand_mappings"][0]["role"]
        == "trigger_duration"
    )
    assert result.ir["warnings"] == []
    assert result.ir["artifacts"] == {}
    assert result.ir["q1asm_programs"][0]["sequencer_id"] == "cluster0_module2_seq0"
    assert result.ir["q1asm_by_sequencer"]["cluster0_module2_seq0"].startswith("wait_sync 4")
    assert (tmp_path / ".qbs_timeline" / "q1asm" / "cluster0_module2_seq0.q1asm").read_text(
        encoding="utf-8"
    ) == "wait_sync 4\nplay 0,1,4\nstop\n"
    assert (tmp_path / ".qbs_timeline" / "q1timeline.yml").is_file()


def test_analyze_project_appends_ir_validation_warnings(tmp_path: Path, monkeypatch) -> None:
    class FakeDiagnostic:
        def to_warning(self) -> str:
            return "fake_invariant at operations[0]: broken invariant"

        def to_ir(self) -> dict[str, str]:
            return {
                "code": "fake_invariant",
                "path": "operations[0]",
                "message": "broken invariant",
                "severity": "warning",
            }

    monkeypatch.setattr(
        compile_worker,
        "validate_qbs_ir",
        lambda ir: [FakeDiagnostic()],
        raising=False,
    )
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
class FakeCompiler:
    def compile(self, schedule):
        return {
            "name": "validation demo",
            "schedulables": {},
            "operations": {},
            "compiled_instructions": {},
        }

def build_schedule():
    return {"name": "validation demo"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=True,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["ir_diagnostics"] == [
        {
            "code": "fake_invariant",
            "path": "operations[0]",
            "message": "broken invariant",
            "severity": "warning",
        }
    ]
    assert result.ir["warnings"] == [
        "IR invariant fake_invariant at operations[0]: broken invariant"
    ]


def test_analyze_project_removes_q1timeline_project_when_no_q1asm_programs(
    tmp_path: Path,
) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
class FakeCompiler:
    def compile(self, schedule):
        return {
            "name": "no q1asm",
            "schedulables": {"op0": {"operation_id": "idle", "abs_time": 0.0}},
            "operations": {"idle": {"name": "IdlePulse", "duration": 20e-9}},
            "compiled_instructions": {},
        }

def build_schedule():
    return {"name": "no q1asm"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    output_dir = tmp_path / ".qbs_timeline"
    output_dir.mkdir()
    stale_project = output_dir / "q1timeline.yml"
    stale_project.write_text("sequencers: []\n", encoding="utf-8")
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=output_dir,
        low_level_q1timeline=True,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.q1asm_programs == []
    assert result.ir["capabilities"]["q1asm"] is False
    assert not stale_project.exists()


def test_analyze_project_infers_q1asm_provenance_without_sidecar(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import annotate, sym

T_TOTAL = sym.time("T_TOTAL", 40e-9)

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class TableData:
            def to_dict(self, orient):
                assert orient == "records"
                return [
                    {"operation": "X(q0)", "abs_time": index * 20e-9, "duration": 20e-9}
                    for index in range(3)
                ]

        class StyledTable:
            data = TableData()

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"x180": {"operation_id": "x_q0", "abs_time": 20e-9}},
            "operations": {
                "x_q0": annotate(
                    {
                        "name": "X(q0)",
                        "duration": 40e-9,
                        "pulse_info": [
                            {
                                "name": "DRAGPulse",
                                "port": "q0:mw",
                                "clock": "q0.01",
                                "t0": 0.0,
                                "duration": 40e-9,
                                "amp": 0.32,
                            }
                        ],
                    },
                    duration=T_TOTAL,
                )
            },
            "compiled_instructions": {
                "cluster0": {
                    "module2": {
                        "sequencers": {
                            "seq0": {"sequence": {"program": "wait_sync 4\\nwait 20\\nset_awg_gain 1,0\\nplay 0,1,40\\nstop\\n"}}
                        }
                    }
                }
            },
        })

def build_schedule():
    return {"name": "demo"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["q1asm_provenance"] == [
        {
            "source_id": "pulse:x180:pulse:0",
            "source_kind": "pulse",
            "operation_id": "x_q0",
            "schedulable_id": "x180",
            "sequencer_id": "cluster0_module2_seq0",
            "q1asm_line_start": 3,
            "q1asm_line_end": 4,
            "instruction_roles": ["set_awg_gain", "play"],
            "operand_mappings": [
                {
                    "line": 4,
                    "instruction": "play",
                    "operand_index": 2,
                    "role": "duration",
                    "numeric_value": 40,
                    "unit": "ns",
                    "source_value_id": "value:t_total",
                }
            ],
            "confidence": "inferred",
            "inference_reason": "unique play event matched pulse time and duration",
        }
    ]


def test_analyze_project_adds_operand_mapping_to_range_only_sidecar(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import annotate, sym

T_TOTAL = sym.time("T_TOTAL", 40e-9)

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"x180": {"operation_id": "x_q0", "abs_time": 20e-9}},
            "operations": {
                "x_q0": annotate(
                    {
                        "name": "X(q0)",
                        "pulse_info": [{"name": "SquarePulse", "t0": 0.0, "duration": 40e-9}],
                    },
                    duration=T_TOTAL,
                )
            },
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "wait 20\\nplay 0,1,40\\nstop\\n"}}}}}
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:x180:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "x180",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 2,
                    "q1asm_line_end": 2,
                }
            ],
        })

def build_schedule():
    return {"name": "range-only-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["q1asm_provenance"][0]["operand_mappings"] == [
        {
            "line": 2,
            "instruction": "play",
            "operand_index": 2,
            "role": "duration",
            "numeric_value": 40,
            "unit": "ns",
            "source_value_id": "value:t_total",
        }
    ]


def test_analyze_project_adds_operand_mapping_to_broad_sidecar_range(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import annotate, sym

T_TOTAL = sym.time("T_TOTAL", 40e-9)

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"x180": {"operation_id": "x_q0", "abs_time": 20e-9}},
            "operations": {
                "x_q0": annotate(
                    {
                        "name": "X(q0)",
                        "pulse_info": [{"name": "SquarePulse", "t0": 0.0, "duration": 40e-9}],
                    },
                    duration=T_TOTAL,
                )
            },
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "wait 20\\nset_awg_gain 1,0\\nplay 0,1,40\\nstop\\n"}}}}}
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:x180:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "x180",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 1,
                    "q1asm_line_end": 3,
                    "instruction_roles": ["wait", "set_awg_gain", "play"],
                }
            ],
        })

def build_schedule():
    return {"name": "broad-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["q1asm_provenance"][0]["operand_mappings"] == [
        {
            "line": 3,
            "instruction": "play",
            "operand_index": 2,
            "role": "duration",
            "numeric_value": 40,
            "unit": "ns",
            "source_value_id": "value:t_total",
        }
    ]


def test_analyze_project_enriches_sidecar_on_one_of_two_identical_sequencers(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import annotate, sym

T_TOTAL = sym.time("T_TOTAL", 40e-9)

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"x180": {"operation_id": "x_q0", "abs_time": 20e-9}},
            "operations": {
                "x_q0": annotate(
                    {
                        "name": "X(q0)",
                        "pulse_info": [{"name": "SquarePulse", "t0": 0.0, "duration": 40e-9}],
                    },
                    duration=T_TOTAL,
                )
            },
            "compiled_instructions": {
                "cluster0": {
                    "module2": {
                        "sequencers": {
                            "seq0": {"sequence": {"program": "wait 20\\nplay 0,1,40\\nstop\\n"}},
                            "seq1": {"sequence": {"program": "wait 20\\nplay 0,1,40\\nstop\\n"}},
                        }
                    }
                }
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:x180:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "x180",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 2,
                    "q1asm_line_end": 2,
                    "instruction_roles": ["play"],
                }
            ],
        })

def build_schedule():
    return {"name": "same-pulse-two-sequencers"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["q1asm_provenance"][0]["operand_mappings"] == [
        {
            "line": 2,
            "instruction": "play",
            "operand_index": 2,
            "role": "duration",
            "numeric_value": 40,
            "unit": "ns",
            "source_value_id": "value:t_total",
        }
    ]


def test_analyze_project_adds_operand_mapping_to_event_only_sidecar(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import annotate, sym

T_TOTAL = sym.time("T_TOTAL", 40e-9)

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"x180": {"operation_id": "x_q0", "abs_time": 20e-9}},
            "operations": {
                "x_q0": annotate(
                    {
                        "name": "X(q0)",
                        "pulse_info": [{"name": "SquarePulse", "t0": 0.0, "duration": 40e-9}],
                    },
                    duration=T_TOTAL,
                )
            },
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "wait 20\\nset_awg_gain 1,0\\nplay 0,1,40\\nstop\\n"}}}}}
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:x180:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "x180",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 3,
                    "q1asm_line_end": 3,
                }
            ],
        })

def build_schedule():
    return {"name": "event-only-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["q1asm_provenance"][0]["operand_mappings"] == [
        {
            "line": 3,
            "instruction": "play",
            "operand_index": 2,
            "role": "duration",
            "numeric_value": 40,
            "unit": "ns",
            "source_value_id": "value:t_total",
        }
    ]


def test_analyze_project_expands_event_only_sidecar_for_split_duration_operand(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import annotate, sym

T_RAMP = sym.time("T_RAMP", 400e-9)

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"ramp": {"operation_id": "ramp_op", "abs_time": 20e-9}},
            "operations": {
                "ramp_op": annotate(
                    {
                        "name": "Ramp(q0)",
                        "pulse_info": [{"name": "RampPulse", "t0": 0.0, "duration": 400e-9}],
                    },
                    duration=T_RAMP,
                )
            },
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "wait 20\\nset_awg_gain 1,0 # setting gain for RampPulse\\nplay 0,1,4 # play RampPulse (400 ns)\\nwait 396\\nstop\\n"}}}}}
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:ramp:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "ramp",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 3,
                    "q1asm_line_end": 3,
                }
            ],
        })

def build_schedule():
    return {"name": "event-only-split-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)
    row = result.ir["q1asm_provenance"][0]

    assert (row["q1asm_line_start"], row["q1asm_line_end"]) == (2, 4)
    assert row["instruction_roles"] == ["set_awg_gain", "play", "wait"]
    assert row["operand_mappings"] == [
        {
            "line": 3,
            "line_end": 4,
            "instruction": "play",
            "operand_index": 2,
            "role": "duration_range",
            "numeric_value": 400,
            "unit": "ns",
            "source_value_id": "value:t_ramp",
        }
    ]


def test_analyze_project_adds_missing_duration_operand_to_partial_sidecar(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import annotate, sym

T_RAMP = sym.time("T_RAMP", 400e-9)

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"ramp": {"operation_id": "ramp_op", "abs_time": 20e-9}},
            "operations": {
                "ramp_op": annotate(
                    {
                        "name": "Ramp(q0)",
                        "pulse_info": [{"name": "RampPulse", "t0": 0.0, "duration": 400e-9}],
                    },
                    duration=T_RAMP,
                )
            },
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "wait 20\\nset_awg_gain 1,0 # setting gain for RampPulse\\nplay 0,1,4 # play RampPulse (400 ns)\\nwait 396\\nstop\\n"}}}}}
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:ramp:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "ramp",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 2,
                    "q1asm_line_end": 4,
                    "instruction_roles": ["set_awg_gain", "play", "wait"],
                    "operand_mappings": [
                        {
                            "line": 2,
                            "instruction": "set_awg_gain",
                            "operand_index": 0,
                            "role": "amplitude",
                            "numeric_value": 1,
                            "unit": "awg",
                        }
                    ],
                }
            ],
        })

def build_schedule():
    return {"name": "partial-sidecar-duration"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)
    mappings = result.ir["q1asm_provenance"][0]["operand_mappings"]

    assert mappings[0]["role"] == "amplitude"
    assert mappings[1] == {
        "line": 3,
        "line_end": 4,
        "instruction": "play",
        "operand_index": 2,
        "role": "duration_range",
        "numeric_value": 400,
        "unit": "ns",
        "source_value_id": "value:t_ramp",
    }


def test_analyze_project_adds_missing_amplitude_operand_to_sidecar(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import annotate, sym

AMP_X = sym.amp("AMP_X", 0.32)

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"x180": {"operation_id": "x_q0", "abs_time": 20e-9}},
            "operations": {
                "x_q0": annotate(
                    {
                        "name": "X(q0)",
                        "pulse_info": [{"name": "DRAGPulse", "t0": 0.0, "duration": 40e-9, "amp": 0.32}],
                    },
                    amp=AMP_X,
                )
            },
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "wait 20\\nset_awg_gain 3277,0 # setting gain for DRAGPulse\\nplay 0,1,40 # play DRAGPulse\\nstop\\n"}}}}}
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:x180:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "x180",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 2,
                    "q1asm_line_end": 3,
                    "instruction_roles": ["set_awg_gain", "play"],
                }
            ],
        })

def build_schedule():
    return {"name": "amplitude-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["q1asm_provenance"][0]["operand_mappings"] == [
        {
            "line": 2,
            "instruction": "set_awg_gain",
            "operand_index": 0,
            "role": "amplitude",
            "numeric_value": 0.32,
            "unit": None,
            "source_value_id": "value:amp_x",
        }
    ]


def test_analyze_project_adds_missing_offset_operand_to_sidecar(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from qbstimeline import SymbolicValue, annotate

OFFSET0 = SymbolicValue(id="value:offset0", label="OFFSET0", value=0.1, unit=None, kind="offset")

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"offset": {"operation_id": "offset_op", "abs_time": 0.0}},
            "operations": {
                "offset_op": annotate(
                    {
                        "name": "Offset",
                        "pulse_info": [{"name": "VoltageOffset", "t0": 0.0, "duration": 300e-9, "offset_path_0": 0.1}],
                    },
                    offset_path_0=OFFSET0,
                )
            },
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "set_awg_offs 123,0 # setting offset for VoltageOffset\\nupd_param 4 # VoltageOffset\\nwait 296\\nstop\\n"}}}}}
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:offset:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "offset",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 1,
                    "q1asm_line_end": 3,
                    "instruction_roles": ["set_awg_offs", "upd_param", "wait"],
                }
            ],
        })

def build_schedule():
    return {"name": "offset-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["q1asm_provenance"][0]["operand_mappings"] == [
        {
            "line": 1,
            "instruction": "set_awg_offs",
            "operand_index": 0,
            "role": "offset",
            "numeric_value": 0.1,
            "unit": None,
            "source_value_id": "value:offset0",
        }
    ]


def test_analyze_project_infers_unmapped_blocks_when_sidecar_is_partial(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {
                "x0": {"operation_id": "x0_op", "abs_time": 0.0},
                "x1": {"operation_id": "x1_op", "abs_time": 20e-9},
            },
            "operations": {
                "x0_op": {
                    "name": "X0",
                    "pulse_info": [{"name": "SquarePulse", "port": "q0:mw", "clock": "q0.01", "t0": 0.0, "duration": 20e-9}],
                },
                "x1_op": {
                    "name": "X1",
                    "pulse_info": [{"name": "SquarePulse", "port": "q0:mw", "clock": "q0.01", "t0": 0.0, "duration": 20e-9}],
                },
            },
            "compiled_instructions": {
                "cluster0": {
                    "module2": {
                        "sequencers": {
                            "seq0": {
                                "sequence": {
                                    "program": "set_awg_gain 1,0\\nplay 0,1,20\\nset_awg_gain 2,0\\nplay 2,3,20\\nstop\\n"
                                }
                            }
                        }
                    }
                }
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:x0:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "x0",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 1,
                    "q1asm_line_end": 2,
                    "operand_mappings": [{"line": 2, "instruction": "play", "operand_index": 2, "role": "duration", "numeric_value": 20, "unit": "ns"}],
                }
            ],
        })

def build_schedule():
    return {"name": "partial-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert [row["source_id"] for row in result.ir["q1asm_provenance"]] == [
        "pulse:x0:pulse:0",
        "pulse:x1:pulse:0",
    ]
    assert result.ir["q1asm_provenance"][1]["confidence"] == "inferred"
    assert result.ir["q1asm_provenance"][1]["q1asm_line_start"] == 3
    assert result.ir["q1asm_provenance"][1]["q1asm_line_end"] == 4


def test_analyze_project_ignores_stale_sidecar_provenance_for_inference(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"x0": {"operation_id": "x0_op", "abs_time": 0.0}},
            "operations": {
                "x0_op": {
                    "name": "X0",
                    "pulse_info": [{"name": "SquarePulse", "port": "q0:mw", "clock": "q0.01", "t0": 0.0, "duration": 20e-9}],
                },
            },
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "play 0,1,20\\nstop\\n"}}}}}
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:x0:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "x0",
                    "sequencer_id": "old_seq",
                    "q1asm_line_start": 99,
                    "q1asm_line_end": 99,
                }
            ],
        })

def build_schedule():
    return {"name": "stale-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["q1asm_provenance"][0]["source_id"] == "pulse:x0:pulse:0"
    assert result.ir["q1asm_provenance"][0]["sequencer_id"] == "cluster0_module2_seq0"
    assert result.ir["q1asm_provenance"][0]["confidence"] == "inferred"


def test_analyze_project_reserves_sidecar_q1asm_ranges_for_inference(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {
                "x0": {"operation_id": "x0_op", "abs_time": 0.0},
                "x1": {"operation_id": "x1_op", "abs_time": 0.0},
            },
            "operations": {
                "x0_op": {
                    "name": "X0",
                    "pulse_info": [{"name": "SquarePulse", "port": "q0:mw", "clock": "q0.01", "t0": 0.0, "duration": 20e-9}],
                },
                "x1_op": {
                    "name": "X1",
                    "pulse_info": [{"name": "SquarePulse", "port": "q1:mw", "clock": "q1.01", "t0": 0.0, "duration": 20e-9}],
                },
            },
            "compiled_instructions": {
                "cluster0": {
                    "module2": {"sequencers": {"seq0": {"sequence": {"program": "play 0,1,20\\nstop\\n"}}}},
                    "module4": {"sequencers": {"seq0": {"sequence": {"program": "play 0,1,20\\nstop\\n"}}}},
                }
            },
            "qbstimeline_provenance": [
                {
                    "source_id": "pulse:x0:pulse:0",
                    "source_kind": "pulse",
                    "schedulable_id": "x0",
                    "sequencer_id": "cluster0_module2_seq0",
                    "q1asm_line_start": 1,
                    "q1asm_line_end": 1,
                }
            ],
        })

def build_schedule():
    return {"name": "reserved-sidecar"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    inferred = next(row for row in result.ir["q1asm_provenance"] if row["source_id"] == "pulse:x1:pulse:0")
    assert inferred["sequencer_id"] == "cluster0_module4_seq0"
    assert inferred["confidence"] == "inferred"


def test_analyze_project_uses_sidecar_acquisition_context_for_readout_pulses(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {
                "measure_q0": {"operation_id": "measure_q0_op", "abs_time": 100e-9},
                "measure_q1": {"operation_id": "measure_q1_op", "abs_time": 100e-9},
            },
            "operations": {
                "measure_q0_op": {
                    "name": "Measure q0",
                    "pulse_info": [{"name": "SquarePulse", "port": "q0:res", "clock": "q0.ro", "t0": 0.0, "duration": 20e-9}],
                    "acquisition_info": [{"protocol": "SSBIntegrationComplex", "port": "q0:res", "clock": "q0.ro", "t0": 20e-9, "duration": 240e-9, "acq_channel": 0}],
                },
                "measure_q1_op": {
                    "name": "Measure q1",
                    "pulse_info": [{"name": "SquarePulse", "port": "q1:res", "clock": "q1.ro", "t0": 0.0, "duration": 20e-9}],
                    "acquisition_info": [{"protocol": "SSBIntegrationComplex", "port": "q1:res", "clock": "q1.ro", "t0": 20e-9, "duration": 240e-9, "acq_channel": 1}],
                },
            },
            "compiled_instructions": {
                "cluster0": {
                    "module6": {
                        "sequencers": {
                            "seq0": {"sequence": {"program": "wait 100\\nplay 0,0,20\\nacquire 0,0,240\\nstop\\n"}},
                            "seq1": {"sequence": {"program": "wait 100\\nplay 0,0,20\\nacquire 1,0,240\\nstop\\n"}},
                        }
                    }
                }
            },
            "qbstimeline_provenance": [
                {"source_id": "acq:measure_q0:acquisition:0", "source_kind": "acquisition", "schedulable_id": "measure_q0", "sequencer_id": "cluster0_module6_seq0", "q1asm_line_start": 3, "q1asm_line_end": 3},
                {"source_id": "acq:measure_q1:acquisition:0", "source_kind": "acquisition", "schedulable_id": "measure_q1", "sequencer_id": "cluster0_module6_seq1", "q1asm_line_start": 3, "q1asm_line_end": 3},
            ],
        })

def build_schedule():
    return {"name": "sidecar-acq-context"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)
    provenance_by_source = {row["source_id"]: row for row in result.ir["q1asm_provenance"]}

    assert provenance_by_source["pulse:measure_q0:pulse:0"]["sequencer_id"] == "cluster0_module6_seq0"
    assert provenance_by_source["pulse:measure_q1:pulse:0"]["sequencer_id"] == "cluster0_module6_seq1"


def test_analyze_project_removes_q1timeline_project_when_disabled(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
class FakeCompiler:
    def compile(self, schedule):
        return {
            "name": schedule["name"],
            "schedulables": {},
            "operations": {},
            "compiled_instructions": {
                "cluster0": {"module2": {"sequencers": {"seq0": {"sequence": {"program": "stop\\n"}}}}}
            },
        }

def build_schedule():
    return {"name": "disable-q1timeline"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    output_dir = tmp_path / ".qbs_timeline"
    output_dir.mkdir()
    stale_project = output_dir / "q1timeline.yml"
    stale_project.write_text("stale: true\n", encoding="utf-8")
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=output_dir,
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    analyze_project(config)

    assert not stale_project.exists()


def test_analyze_project_loads_schedule_sibling_imports(tmp_path: Path) -> None:
    helper_file = tmp_path / "demo_compiler_adapter.py"
    helper_file.write_text(
        """
class DemoCompiler:
    def compile(self, schedule):
        return {
            "name": schedule["name"],
            "schedulables": {"idle": {"operation_id": "idle_op", "abs_time": 0.0}},
            "operations": {"idle_op": {"name": "Idle", "duration": 4e-9}},
            "compiled_instructions": {},
        }
""".lstrip(),
        encoding="utf-8",
    )
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from demo_compiler_adapter import DemoCompiler

def build_schedule():
    return {"name": "sibling-import"}

def build_compiler():
    return DemoCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["schedule"]["name"] == "sibling-import"


def test_analyze_project_emits_source_map_for_literal_schedule_add_labels(
    tmp_path: Path,
) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_text = """
class Schedule:
    name = "demo"

    def add(self, operation, *, label, abs_time):
        return None

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule.name,
            "schedulables": {"measure": {"operation_id": "measure_q0", "abs_time": 60e-9}},
            "operations": {"measure_q0": {"name": "Measure(q0)", "duration": 160e-9}},
            "compiled_instructions": {},
        })

def build_schedule():
    schedule = Schedule()
    schedule.add(object(), label="measure", abs_time=60e-9)
    return schedule

def build_compiler():
    return FakeCompiler()
""".lstrip()
    schedule_file.write_text(schedule_text, encoding="utf-8")
    expected_line = schedule_text.splitlines().index(
        '    schedule.add(object(), label="measure", abs_time=60e-9)'
    ) + 1
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["source_map"]["schedulables"]["measure"] == {
        "file": "schedule.py",
        "line": expected_line,
        "column": 4,
        "label": "measure",
    }


def test_analyze_project_promotes_runtime_source_trace_to_source_map_for_dynamic_labels(
    tmp_path: Path,
) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_text = """
class Schedule:
    name = "demo"

    def __init__(self):
        self.schedulables = {}

    def add(self, operation, *, label, abs_time):
        self.schedulables[label] = {"operation_id": "measure_q0", "abs_time": abs_time}
        return label

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule.name,
            "schedulables": schedule.schedulables,
            "operations": {"measure_q0": {"name": "Measure(q0)", "duration": 160e-9}},
            "compiled_instructions": {},
        })

def build_schedule():
    schedule = Schedule()
    label = "measure"
    schedule.add(object(), label=label, abs_time=60e-9)
    return schedule

def build_compiler():
    return FakeCompiler()
""".lstrip()
    schedule_file.write_text(schedule_text, encoding="utf-8")
    expected_line = schedule_text.splitlines().index(
        "    schedule.add(object(), label=label, abs_time=60e-9)"
    ) + 1
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["operations"][0]["source"]["line"] == expected_line
    assert result.ir["source_map"]["schedulables"]["measure"]["line"] == expected_line


def test_analyze_project_runtime_source_trace_overrides_unrelated_add_label(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_text = """
from types import SimpleNamespace

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        return FakeCompiledSchedule({
            "name": schedule.name,
            "schedulables": schedule.schedulables,
            "operations": schedule.operations,
            "_qbstimeline_source_trace": schedule._qbstimeline_source_trace,
            "compiled_instructions": {},
        })

class FakeSchedule:
    def __init__(self):
        self.name = "runtime-source"
        self.schedulables = {}
        self.operations = {}
        self._qbstimeline_source_trace = {}

    def add(self, operation, *, label, abs_time):
        self.schedulables[label] = {"operation_id": operation.name, "abs_time": abs_time}
        self.operations[operation.name] = {"name": operation.name, "duration": 20e-9}
        self._qbstimeline_source_trace[label] = {"file": __file__, "line": 999, "column": 8}
        return operation

class Tracker:
    def add(self, operation, *, label, abs_time):
        return operation

def build_schedule():
    schedule = FakeSchedule()
    schedule.add(SimpleNamespace(name="measure_q0"), label="measure", abs_time=0.0)
    Tracker().add(SimpleNamespace(name="not_schedule"), label="measure", abs_time=0.0)
    return schedule

def build_compiler():
    return FakeCompiler()
""".lstrip()
    real_add_line = schedule_text.splitlines().index(
        '    schedule.add(SimpleNamespace(name="measure_q0"), label="measure", abs_time=0.0)'
    ) + 1
    schedule_text = schedule_text.replace('"line": 999', f'"line": {real_add_line}')
    schedule_file.write_text(schedule_text, encoding="utf-8")
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["source_map"]["schedulables"]["measure"]["line"] == real_add_line


def test_extract_operations_unwraps_scheduler_userdict_objects() -> None:
    compiled_schedule = SimpleNamespace(
        schedulables={
            "schedulable0": UserDict({"operation_id": "operation0", "abs_time": 4e-9})
        },
        operations={
            "operation0": UserDict({"name": "DRAGPulse(q0)", "duration": 20e-9})
        },
    )

    operations = _extract_operations(compiled_schedule)

    assert operations == [
        {
            "id": "schedulable0",
            "operation_id": "operation0",
            "label": "DRAGPulse(q0)",
            "abs_time": 4e-9,
            "duration": 20e-9,
        }
    ]


def test_extract_operations_uses_zero_for_missing_operation_duration() -> None:
    compiled_schedule = SimpleNamespace(
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 0.0}},
        operations={"loop_operation": {"name": "LoopOperation", "duration": None}},
    )

    operations = _extract_operations(compiled_schedule)

    assert operations == [
        {
            "id": "loop",
            "operation_id": "loop_operation",
            "label": "LoopOperation",
            "abs_time": 0.0,
            "duration": 0.0,
        }
    ]


def test_extract_operations_prefers_operation_duration_attribute_before_unwrapping_data() -> None:
    class LoopOperationLike:
        duration = 925e-6
        data = {"name": "LoopOperation", "control_flow_info": {"body": "nested schedule"}}

    compiled_schedule = SimpleNamespace(
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 0.0}},
        operations={"loop_operation": LoopOperationLike()},
    )

    operations = _extract_operations(compiled_schedule)

    assert operations == [
        {
            "id": "loop",
            "operation_id": "loop_operation",
            "label": "LoopOperation",
            "abs_time": 0.0,
            "duration": 925e-6,
        }
    ]


def test_extract_schedule_structure_emits_loop_block_and_first_iteration_body() -> None:
    loop_body = SimpleNamespace(
        schedulables={"body0": {"operation_id": "body_pulse", "abs_time": 5e-9}},
        operations={"body_pulse": {"name": "X(q0)", "duration": 20e-9}},
    )

    class LoopOperationLike:
        duration = 120e-9
        body = loop_body
        data = {
            "name": "LoopOperation",
            "control_flow_info": {
                "body": loop_body,
                "repetitions": 3,
                "t0": 0.0,
            },
        }

    compiled_schedule = SimpleNamespace(
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 10e-9}},
        operations={"loop_operation": LoopOperationLike()},
    )

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert structure.control_flow_blocks == [
        {
            "id": "control-flow:loop",
            "kind": "loop",
            "label": "Loop x3",
            "abs_time": 10e-9,
            "duration": 120e-9,
            "preview_abs_time": 10e-9,
            "preview_duration": 25e-9,
            "preview_kind": "first_iteration",
            "operation_id": "loop_operation",
            "schedulable_id": "loop",
            "duration_kind": "expanded",
            "repetitions": 3,
            "body_operation_count": 1,
        }
    ]
    assert structure.operations == [
        {
            "id": "loop",
            "operation_id": "loop_operation",
            "label": "LoopOperation",
            "abs_time": 10e-9,
            "duration": 120e-9,
        },
        {
            "id": "loop/body0",
            "operation_id": "body_pulse",
            "label": "X(q0)",
            "abs_time": 15e-9,
            "duration": 20e-9,
            "parent_control_flow_id": "control-flow:loop",
            "depth": 1,
        },
    ]


def test_operation_body_without_control_flow_metadata_is_nested_without_loop_block() -> None:
    pulse_compensation_body = SimpleNamespace(
        schedulables={"measure": {"operation_id": "measure_op", "abs_time": 0.0}},
        operations={"measure_op": {"name": "Measure cs0", "duration": 800e-9}},
    )

    class PulseCompensationLike:
        duration = 0.0
        body = pulse_compensation_body
        data = {"name": "PulseCompensation"}

    compiled_schedule = SimpleNamespace(
        schedulables={"pc": {"operation_id": "pulse_compensation", "abs_time": 0.0}},
        operations={"pulse_compensation": PulseCompensationLike()},
    )

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert structure.control_flow_blocks == []
    assert structure.operations == [
        {
            "id": "pc",
            "operation_id": "pulse_compensation",
            "label": "PulseCompensation",
            "abs_time": 0.0,
            "duration": 0.0,
        },
        {
            "id": "pc/measure",
            "operation_id": "measure_op",
            "label": "Measure cs0",
            "abs_time": 0.0,
            "duration": 800e-9,
        },
    ]


def test_extract_schedule_structure_flattens_nested_schedule_wrappers_without_operation_rows() -> None:
    nested_schedule = SimpleNamespace(
        schedulables={"drive": {"operation_id": "drive_op", "abs_time": 8e-9}},
        operations={"drive_op": {"name": "SquarePulse", "duration": 20e-9}},
    )
    compiled_schedule = SimpleNamespace(
        schedulables={"nested": {"operation_id": "nested_schedule", "abs_time": 5e-9}},
        operations={"nested_schedule": nested_schedule},
    )

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert structure.operations == [
        {
            "id": "nested/drive",
            "operation_id": "drive_op",
            "label": "SquarePulse",
            "abs_time": 13e-9,
            "duration": 20e-9,
        }
    ]


def test_large_repeated_nested_operations_are_compacted_as_manual_sweep() -> None:
    nested_body = SimpleNamespace(
        schedulables={"measure": {"operation_id": "measure_op", "abs_time": 0.0}},
        operations={"measure_op": {"name": "Measure cs0", "duration": 800e-9}},
    )

    class PulseCompensationLike:
        duration = 0.0
        body = nested_body
        data = {"name": "PulseCompensation"}

    schedulables = {f"pc{index}": {"operation_id": f"pulse_compensation_{index}", "abs_time": 0.0} for index in range(60)}
    operations = {f"pulse_compensation_{index}": PulseCompensationLike() for index in range(60)}
    compiled_schedule = SimpleNamespace(schedulables=schedulables, operations=operations)

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert [block["label"] for block in structure.control_flow_blocks] == ["Sweep x60"]
    assert structure.control_flow_blocks[0]["kind"] == "sweep"
    assert [operation["id"] for operation in structure.operations] == ["pc0", "pc0/measure"]
    assert structure.operations[1]["parent_control_flow_id"] == "control-flow:pc0"


def test_repeated_nested_schedule_uses_source_order_timing_and_brackets() -> None:
    nested_body = SimpleNamespace(
        schedulables={
            "square0": {"operation_id": "square0_op"},
            "ramp": {"operation_id": "ramp_op"},
            "square1": {"operation_id": "square1_op"},
        },
        operations={
            "square0_op": {"name": "SquarePulse", "duration": 300e-9},
            "ramp_op": {"name": "RampPulse", "duration": 400e-9},
            "square1_op": {"name": "SquarePulse", "duration": 100e-9},
        },
    )

    def pulse_sequence() -> SimpleNamespace:
        return SimpleNamespace(name="pulse_sequence", schedulables=nested_body.schedulables, operations=nested_body.operations)

    compiled_schedule = SimpleNamespace(
        repetitions=1_000_000,
        schedulables={
            f"point{index}": {"operation_id": f"pulse_sequence_{index}"}
            for index in range(10)
        },
        operations={f"pulse_sequence_{index}": pulse_sequence() for index in range(10)},
    )

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert [block["label"] for block in structure.control_flow_blocks] == ["Loop x1000000", "Sweep x10"]
    assert [block["duration"] for block in structure.control_flow_blocks] == [8e-6, 8e-6]
    assert [block["preview_duration"] for block in structure.control_flow_blocks] == [800e-9, 800e-9]
    assert [block["duration_kind"] for block in structure.control_flow_blocks] == ["expanded", "expanded"]
    assert [block["preview_kind"] for block in structure.control_flow_blocks] == ["first_iteration", "first_iteration"]
    assert structure.control_flow_blocks[1]["parent_control_flow_id"] == "control-flow:__schedule_repetition"
    assert [operation["label"] for operation in structure.operations[-3:]] == [
        "SquarePulse",
        "RampPulse",
        "SquarePulse",
    ]
    assert [operation["abs_time"] for operation in structure.operations[-3:]] == [0.0, 300e-9, 700e-9]
    assert structure.operations[-3]["parent_control_flow_id"] == "control-flow:point0"
    assert structure.operations[-3]["depth"] == 2


def test_distinct_abs_time_repeated_nested_schedules_are_not_compacted() -> None:
    nested_body = SimpleNamespace(
        schedulables={"measure": {"operation_id": "measure_op", "abs_time": 0.0}},
        operations={"measure_op": {"name": "Measure cs0", "duration": 100e-9}},
    )

    def pulse_sequence() -> SimpleNamespace:
        return SimpleNamespace(name="pulse_sequence", schedulables=nested_body.schedulables, operations=nested_body.operations)

    compiled_schedule = SimpleNamespace(
        schedulables={
            "point0": {"operation_id": "pulse_sequence_0", "abs_time": 0.0},
            "point1": {"operation_id": "pulse_sequence_1", "abs_time": 1e-6},
        },
        operations={"pulse_sequence_0": pulse_sequence(), "pulse_sequence_1": pulse_sequence()},
    )

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert structure.control_flow_blocks == []
    assert [operation["id"] for operation in structure.operations] == ["point0/measure", "point1/measure"]
    assert [operation["abs_time"] for operation in structure.operations] == [0.0, 1e-6]


def test_extract_schedule_structure_reads_experiment_wrapped_schedule() -> None:
    loop_body = SimpleNamespace(
        schedulables={"body0": {"operation_id": "body_pulse", "abs_time": 5e-9}},
        operations={"body_pulse": {"name": "X(q0)", "duration": 20e-9}},
    )

    class LoopOperationLike:
        duration = 60e-9
        body = loop_body
        data = {
            "name": "LoopOperation",
            "control_flow_info": {
                "body": loop_body,
                "repetitions": 3,
                "t0": 0.0,
            },
        }

    nested_schedule = SimpleNamespace(
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 0.0}},
        operations={"loop_operation": LoopOperationLike()},
    )

    class ExperimentWrappedSchedule:
        _experiments = [
            {
                "steps": [
                    {
                        "schedule_info": {
                            "schedule": nested_schedule,
                        }
                    }
                ]
            }
        ]

        @property
        def schedulables(self):
            raise RuntimeError("unavailable on schedule with untimed operations")

        @property
        def operations(self):
            raise RuntimeError("unavailable on schedule with untimed operations")

    structure = compile_worker._extract_schedule_structure(ExperimentWrappedSchedule())

    assert [block["label"] for block in structure.control_flow_blocks] == ["Loop x3"]
    assert [operation["id"] for operation in structure.operations] == [
        "experiment0/loop",
        "experiment0/loop/body0",
    ]


def test_extract_schedule_structure_marks_experiment_frequency_domain_as_sweep() -> None:
    class Domain:
        def __init__(self, dtype: str, num: int) -> None:
            self.dtype = dtype
            self.num = num

    sweep_body = SimpleNamespace(
        schedulables={"drive": {"operation_id": "drive_pulse", "abs_time": 4e-9}},
        operations={"drive_pulse": {"name": "SquarePulse", "duration": 20e-9}},
    )

    class ExperimentWrappedSchedule:
        _experiments = [
            {
                "steps": [
                    {
                        "loop_info": {
                            "domains": {"freq": Domain("frequency", 200)},
                            "steps": [
                                {
                                    "schedule_info": {
                                        "schedule": sweep_body,
                                    }
                                }
                            ],
                        }
                    }
                ]
            }
        ]

        @property
        def schedulables(self):
            raise RuntimeError("unavailable on schedule with untimed operations")

        @property
        def operations(self):
            raise RuntimeError("unavailable on schedule with untimed operations")

    structure = compile_worker._extract_schedule_structure(ExperimentWrappedSchedule())

    assert structure.control_flow_blocks[0]["kind"] == "sweep"
    assert structure.control_flow_blocks[0]["label"] == "Sweep x200"
    assert structure.control_flow_blocks[0]["iteration"] == {
        "kind": "domain",
        "variable": "freq",
        "count": 200,
    }
    assert structure.operations[1]["id"] == "experiment0/step0/drive"
    assert structure.operations[1]["parent_control_flow_id"] == "control-flow:experiment0/step0"


def test_extract_schedule_structure_reads_dict_domain_metadata() -> None:
    sweep_body = SimpleNamespace(
        schedulables={"drive": {"operation_id": "drive_pulse", "abs_time": 0.0}},
        operations={"drive_pulse": {"name": "SquarePulse", "duration": 20e-9}},
    )

    class SweepOperationLike:
        duration = 2e-6
        body = sweep_body
        data = {
            "name": "SweepOperation",
            "control_flow_info": {
                "body": sweep_body,
                "domain": {"freq": {"dtype": "frequency", "num": 100}},
                "t0": 0.0,
            },
        }

    compiled_schedule = SimpleNamespace(
        schedulables={"sweep": {"operation_id": "sweep_operation", "abs_time": 0.0}},
        operations={"sweep_operation": SweepOperationLike()},
    )

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert structure.control_flow_blocks[0]["kind"] == "sweep"
    assert structure.control_flow_blocks[0]["label"] == "Sweep x100"
    assert structure.control_flow_blocks[0]["repetitions"] == 100
    assert structure.control_flow_blocks[0]["iteration"] == {
        "kind": "domain",
        "variable": "freq",
        "count": 100,
    }


def test_domain_iteration_omits_generated_qblox_variable_names() -> None:
    iteration = compile_worker._domain_iteration(
        {
            "Vare50d1570402b488d856129dbb3026738": {
                "dtype": "number",
                "num": 100,
            }
        },
        100,
    )

    assert iteration == {"kind": "domain", "count": 100}


def test_extract_schedule_structure_compacts_nested_sweep_body_once() -> None:
    class UnknownDurationLoopOperation:
        @property
        def duration(self):
            raise TypeError("body duration is unresolved")

    class Domain:
        def __init__(self, dtype: str, num: int) -> None:
            self.dtype = dtype
            self.num = num

    sweep_body = SimpleNamespace(
        schedulables={
            "set_freq": {"operation_id": "set_frequency", "abs_time": 0.0},
            "drive": {"operation_id": "drive_pulse", "abs_time": 12e-9},
        },
        operations={
            "set_frequency": {"name": "SetClockFrequency", "duration": 0.0},
            "drive_pulse": {"name": "SquarePulse", "duration": 20e-9},
        },
    )

    class SweepOperationLike(UnknownDurationLoopOperation):
        body = sweep_body
        data = {
            "name": "LoopOperation",
            "control_flow_info": {
                "body": sweep_body,
                "domain": {"freq": Domain("frequency", 100)},
                "repetitions": 100,
                "t0": 2e-9,
            },
        }

    outer_body = SimpleNamespace(
        schedulables={"sweep": {"operation_id": "sweep_operation", "abs_time": 5e-9}},
        operations={"sweep_operation": SweepOperationLike()},
    )

    class LoopOperationLike(UnknownDurationLoopOperation):
        body = outer_body
        data = {
            "name": "LoopOperation",
            "control_flow_info": {
                "body": outer_body,
                "domain": {"rep": Domain("number", 400)},
                "repetitions": 400,
                "t0": 0.0,
            },
        }

    compiled_schedule = SimpleNamespace(
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 10e-9}},
        operations={"loop_operation": LoopOperationLike()},
    )

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert [block["label"] for block in structure.control_flow_blocks] == ["Loop x400", "Sweep x100"]
    assert [block["kind"] for block in structure.control_flow_blocks] == ["loop", "sweep"]
    assert structure.control_flow_blocks[1]["parent_control_flow_id"] == "control-flow:loop"
    assert structure.control_flow_blocks[1]["depth"] == 1
    assert [operation["id"] for operation in structure.operations] == [
        "loop",
        "loop/sweep",
        "loop/sweep/set_freq",
        "loop/sweep/drive",
    ]
    assert structure.operations[2]["parent_control_flow_id"] == "control-flow:loop/sweep"
    assert structure.operations[3]["parent_control_flow_id"] == "control-flow:loop/sweep"


def test_extract_schedule_structure_finds_sweep_inside_nested_schedule_body() -> None:
    class Domain:
        def __init__(self, dtype: str, num: int) -> None:
            self.dtype = dtype
            self.num = num

    sweep_body = SimpleNamespace(
        schedulables={"drive": {"operation_id": "drive_pulse", "abs_time": 4e-9}},
        operations={"drive_pulse": {"name": "SquarePulse", "duration": 20e-9}},
    )

    class SweepOperationLike:
        duration = 20e-9
        body = sweep_body
        data = {
            "name": "LoopOperation",
            "control_flow_info": {
                "body": sweep_body,
                "domain": {"freq": Domain("frequency", 100)},
                "repetitions": 100,
                "t0": 0.0,
            },
        }

    nested_schedule = SimpleNamespace(
        schedulables={"sweep": {"operation_id": "sweep_operation", "abs_time": 0.0}},
        operations={"sweep_operation": SweepOperationLike()},
    )
    outer_body = SimpleNamespace(
        schedulables={"nested": {"operation_id": "nested_schedule", "abs_time": 5e-9}},
        operations={"nested_schedule": nested_schedule},
    )

    class LoopOperationLike:
        duration = 0.0
        body = outer_body
        data = {
            "name": "LoopOperation",
            "control_flow_info": {
                "body": outer_body,
                "domain": {"rep": Domain("number", 400)},
                "repetitions": 400,
                "t0": 0.0,
            },
        }

    compiled_schedule = SimpleNamespace(
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 10e-9}},
        operations={"loop_operation": LoopOperationLike()},
    )

    structure = compile_worker._extract_schedule_structure(compiled_schedule)

    assert [block["label"] for block in structure.control_flow_blocks] == ["Loop x400", "Sweep x100"]
    assert [block["kind"] for block in structure.control_flow_blocks] == ["loop", "sweep"]
    assert structure.control_flow_blocks[0]["iteration"] == {
        "kind": "domain",
        "variable": "rep",
        "count": 400,
    }
    assert structure.control_flow_blocks[1]["parent_control_flow_id"] == "control-flow:loop"
    assert "loop/nested/sweep/drive" in [operation["id"] for operation in structure.operations]


def test_manual_sweep_records_iteration_variable_from_enclosing_for(tmp_path: Path) -> None:
    source_file = tmp_path / "notebook_cells.py"
    source_file.write_text(
        "def build():\n"
        "    amp_points = [0.1, 0.2]\n"
        "    for amp in amp_points:\n"
        "        schedule.add(pulse_sequence)\n",
        encoding="utf-8",
    )
    pulse_sequence = SimpleNamespace(
        schedulables={"drive": {"operation_id": "drive_pulse", "abs_time": 0.0}},
        operations={"drive_pulse": {"name": "SquarePulse", "duration": 20e-9}},
        name="pulse_sequence",
    )
    compiled_schedule = SimpleNamespace(
        schedulables={
            "point0": {"operation_id": "pulse_sequence", "abs_time": 0.0},
            "point1": {"operation_id": "pulse_sequence", "abs_time": 0.0},
        },
        operations={"pulse_sequence": pulse_sequence},
    )
    source_trace = SourceTrace(
        locations_by_schedulable_id={
            "point0": SourceTraceLocation(file=str(source_file), line=4, column=8),
            "point1": SourceTraceLocation(file=str(source_file), line=4, column=8),
        }
    )

    structure = compile_worker._extract_schedule_structure(
        compiled_schedule,
        source_trace=source_trace,
    )

    assert structure.control_flow_blocks[0]["iteration"] == {
        "kind": "manual_sweep",
        "variable": "amp",
        "source": "amp_points",
        "count": 2,
    }


def test_manual_sweep_omits_iteration_for_nested_for_source(tmp_path: Path) -> None:
    source_file = tmp_path / "notebook_cells.py"
    source_file.write_text(
        "def build():\n"
        "    for amp in amp_points:\n"
        "        for freq in freq_points:\n"
        "            schedule.add(pulse_sequence)\n",
        encoding="utf-8",
    )
    pulse_sequence = SimpleNamespace(
        schedulables={"drive": {"operation_id": "drive_pulse", "abs_time": 0.0}},
        operations={"drive_pulse": {"name": "SquarePulse", "duration": 20e-9}},
        name="pulse_sequence",
    )
    compiled_schedule = SimpleNamespace(
        schedulables={
            "point0": {"operation_id": "pulse_sequence", "abs_time": 0.0},
            "point1": {"operation_id": "pulse_sequence", "abs_time": 0.0},
        },
        operations={"pulse_sequence": pulse_sequence},
    )
    source_trace = SourceTrace(
        locations_by_schedulable_id={
            "point0": SourceTraceLocation(file=str(source_file), line=4, column=12),
            "point1": SourceTraceLocation(file=str(source_file), line=4, column=12),
        }
    )

    structure = compile_worker._extract_schedule_structure(
        compiled_schedule,
        source_trace=source_trace,
    )

    assert "iteration" not in structure.control_flow_blocks[0]


def test_extract_schedule_structure_emits_operation_source_locations() -> None:
    source_trace = SourceTrace(
        locations_by_schedulable_id={
            "measure": SourceTraceLocation(
                file="notebook_cells.py",
                line=8,
                column=0,
                label="measure",
            )
        }
    )
    compiled_schedule = SimpleNamespace(
        schedulables={"measure": {"operation_id": "measure_op", "abs_time": 0.0}},
        operations={"measure_op": {"name": "Measure q0", "duration": 0.0}},
    )

    structure = compile_worker._extract_schedule_structure(
        compiled_schedule,
        source_trace=source_trace,
    )

    assert structure.operations[0]["source"]["file"] == "notebook_cells.py"
    assert structure.operations[0]["source"]["line"] == 8


def test_extract_schedule_structure_emits_schedule_repetition_source_location() -> None:
    compiled_schedule = SimpleNamespace(
        repetitions=3,
        schedulables={"measure": {"operation_id": "measure_op", "abs_time": 0.0}},
        operations={"measure_op": {"name": "Measure q0", "duration": 100e-9}},
    )
    source_trace = SourceTrace(
        locations_by_schedule_id={
            id(compiled_schedule): SourceTraceLocation(
                file="notebook_cells.py",
                line=12,
                column=4,
                label="schedule repetitions",
            )
        }
    )

    structure = compile_worker._extract_schedule_structure(
        compiled_schedule,
        source_trace=source_trace,
    )

    assert structure.control_flow_blocks[0]["id"] == "control-flow:__schedule_repetition"
    assert structure.control_flow_blocks[0]["source"] == {
        "file": "notebook_cells.py",
        "line": 12,
        "column": 4,
        "label": "schedule repetitions",
    }
    assert structure.control_flow_blocks[0]["iteration"] == {
        "kind": "schedule_repetition",
        "variable": "repetitions",
        "count": 3,
    }


def test_analyze_project_maps_generated_probe_lines_to_notebook_cells(tmp_path: Path) -> None:
    notebook = tmp_path / "source.ipynb"
    notebook.write_text("{}", encoding="utf-8")
    notebook_cells = tmp_path / "notebook_cells.py"
    notebook_cells.write_text(
        "# %% qbstimeline notebook cell 3\n"
        "sched = Schedule('demo')\n"
        "sched.add(Measure('q0'))\n",
        encoding="utf-8",
    )
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from pathlib import Path
from types import SimpleNamespace

class FakeCompiler:
    def compile(self, schedule):
        return schedule

def build_schedule():
    return SimpleNamespace(
        name='demo',
        schedulables={'measure': {'operation_id': 'measure_op', 'abs_time': 0.0}},
        operations={'measure_op': {'name': 'Measure q0', 'duration': 0.0}},
        _qbstimeline_source_trace={
            'measure': {'file': str(Path('notebook_cells.py')), 'line': 3, 'column': 0, 'label': 'measure'}
        },
    )

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    project = tmp_path / "qbstimeline.yml"
    project.write_text(
        f"""
schedule:
  file: schedule.py
source:
  notebook: {notebook}
outputs:
  dir: .qbs_timeline
""".lstrip(),
        encoding="utf-8",
    )

    result = analyze_project(load_project_config(project))
    location = result.ir["source_map"]["schedulables"]["measure"]

    assert result.ir["source_map"]["primary"]["kind"] == "notebook"
    assert location["kind"] == "notebook"
    assert location["notebook"]["cell_index"] == 2
    assert location["notebook"]["cell_line"] == 2


def test_analyze_project_executes_direct_notebook_schedule(tmp_path: Path) -> None:
    notebook = tmp_path / "experiment.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": ["raise RuntimeError('untagged cell must not run')\n"],
                        "metadata": {},
                    },
                    {
                        "cell_type": "code",
                        "source": [
                            "from types import SimpleNamespace\n"
                            "class FakeCompiler:\n"
                            "    def compile(self, schedule):\n"
                            "        schedule.compiled_instructions = {}\n"
                            "        return schedule\n"
                        ],
                        "metadata": {"tags": ["qbstimeline-setup"]},
                    },
                    {
                        "cell_type": "code",
                        "source": [
                            "two_tone_sched = SimpleNamespace(\n"
                            "    name='notebook-demo',\n"
                            "    schedulables={'measure': {'operation_id': 'measure_op', 'abs_time': 0.0}},\n"
                            "    operations={'measure_op': {'name': 'Measure q0', 'duration': 0.0}},\n"
                            ")\n"
                            "hw_agent = FakeCompiler()\n"
                        ],
                        "metadata": {"tags": ["qbstimeline-schedule"]},
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    project = tmp_path / "qbstimeline.yml"
    project.write_text(
        """
schedule:
  notebook: experiment.ipynb
  setup_tags:
    - qbstimeline-setup
  schedule_tag: qbstimeline-schedule
  schedule_variable: two_tone_sched
  compiler_variable: hw_agent
outputs:
  dir: .qbs_timeline
low_level:
  q1timeline: false
""".lstrip(),
        encoding="utf-8",
    )

    result = analyze_project(load_project_config(project))

    assert result.ir["schedule"]["name"] == "notebook-demo"
    assert result.ir["operations"][0]["id"] == "measure"
    assert result.ir["source_map"]["primary"]["kind"] == "notebook"


def test_analyze_project_maps_direct_notebook_adds_to_cells(tmp_path: Path) -> None:
    notebook = tmp_path / "experiment.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": ["raise RuntimeError('untagged cell must not run')\n"],
                        "metadata": {},
                    },
                    {
                        "cell_type": "code",
                        "source": [
                            "from types import SimpleNamespace\n"
                            "class FakeSchedule:\n"
                            "    name = 'notebook-demo'\n"
                            "    def __init__(self):\n"
                            "        self.schedulables = {}\n"
                            "        self.operations = {'measure_op': {'name': 'Measure q0', 'duration': 0.0}}\n"
                            "    def add(self, operation, *, label):\n"
                            "        self.schedulables[label] = {'operation_id': operation.name, 'abs_time': 0.0}\n"
                            "        return label\n"
                            "class FakeCompiler:\n"
                            "    def compile(self, schedule):\n"
                            "        return schedule\n"
                            "sched = FakeSchedule()\n"
                            "sched.add(SimpleNamespace(name='measure_op'), label='measure')\n"
                            "hw_agent = FakeCompiler()\n"
                        ],
                        "metadata": {"tags": ["qbstimeline-schedule"]},
                        "id": "schedule-cell",
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    project = tmp_path / "qbstimeline.yml"
    project.write_text(
        """
schedule:
  notebook: experiment.ipynb
  schedule_tag: qbstimeline-schedule
  schedule_variable: sched
  compiler_variable: hw_agent
outputs:
  dir: .qbs_timeline
""".lstrip(),
        encoding="utf-8",
    )

    result = analyze_project(load_project_config(project))
    location = result.ir["source_map"]["schedulables"]["measure"]

    assert location["kind"] == "notebook"
    assert location["notebook"]["cell_index"] == 1
    assert location["notebook"]["cell_id"] == "schedule-cell"
    assert location["notebook"]["cell_line"] == 14


def test_extract_timing_table_returns_empty_when_schedule_is_not_compiled() -> None:
    class UncompiledSchedule:
        @property
        def timing_table(self):
            raise ValueError("Absolute time has not been determined yet. Please compile your schedule.")

    assert compile_worker._extract_timing_table(UncompiledSchedule()) == []


def test_analyze_project_emits_control_flow_blocks(tmp_path: Path) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from types import SimpleNamespace

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        loop_body = SimpleNamespace(
            schedulables={"body0": {"operation_id": "body_pulse", "abs_time": 5e-9}},
            operations={"body_pulse": {"name": "X(q0)", "duration": 20e-9}},
        )

        class LoopOperationLike:
            duration = 120e-9
            body = loop_body
            data = {
                "name": "LoopOperation",
                "control_flow_info": {
                    "body": loop_body,
                    "repetitions": 3,
                    "t0": 0.0,
                },
            }

        return FakeCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {"loop": {"operation_id": "loop_operation", "abs_time": 10e-9}},
            "operations": {"loop_operation": LoopOperationLike()},
            "compiled_instructions": {},
        })

def build_schedule():
    return {"name": "demo"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["control_flow_blocks"] == [
        {
            "id": "control-flow:loop",
            "kind": "loop",
            "label": "Loop x3",
            "abs_time": 10e-9,
            "duration": 120e-9,
            "preview_abs_time": 10e-9,
            "preview_duration": 25e-9,
            "preview_kind": "first_iteration",
            "operation_id": "loop_operation",
            "schedulable_id": "loop",
            "duration_kind": "expanded",
            "repetitions": 3,
            "body_operation_count": 1,
        }
    ]
    assert result.ir["operations"][1]["parent_control_flow_id"] == "control-flow:loop"


def test_analyze_project_keeps_source_control_flow_compact_when_compiler_expands(
    tmp_path: Path,
) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from types import SimpleNamespace

class FakeCompiledSchedule(dict):
    @property
    def timing_table(self):
        class StyledTable:
            data = None

        return StyledTable()

class FakeCompiler:
    def compile(self, schedule):
        expanded_schedulables = {
            f"iter{index}": {"operation_id": "body_pulse", "abs_time": index * 20e-9}
            for index in range(3)
        }
        return FakeCompiledSchedule({
            "name": schedule.name,
            "schedulables": expanded_schedulables,
            "operations": {"body_pulse": {"name": "X(q0)", "duration": 20e-9}},
            "compiled_instructions": {},
        })

def build_schedule():
    loop_body = SimpleNamespace(
        schedulables={"body0": {"operation_id": "body_pulse", "abs_time": 5e-9}},
        operations={"body_pulse": {"name": "X(q0)", "duration": 20e-9}},
    )

    class LoopOperationLike:
        duration = 60e-9
        body = loop_body
        data = {
            "name": "LoopOperation",
            "control_flow_info": {
                "body": loop_body,
                "repetitions": 3,
                "t0": 0.0,
            },
        }

    return SimpleNamespace(
        name="compact-source",
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 10e-9}},
        operations={"loop_operation": LoopOperationLike()},
    )

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert [operation["id"] for operation in result.ir["operations"]] == ["loop", "loop/body0"]
    assert result.ir["control_flow_blocks"][0]["label"] == "Loop x3"
    assert result.ir["timing_table"] == []


def test_analyze_project_falls_back_to_source_preview_when_compile_fails(
    tmp_path: Path,
) -> None:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(
        """
from types import SimpleNamespace

class FakeCompiler:
    def compile(self, schedule):
        raise RuntimeError("Can not compile a schedule with untimed sections")

def build_schedule():
    return SimpleNamespace(
        name="source-preview",
        schedulables={"op0": {"operation_id": "x0", "abs_time": 0.0}},
        operations={"x0": {"name": "X(q0)", "duration": 20e-9}},
    )

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    config = ProjectConfig(
        root=tmp_path,
        project_file=tmp_path / "qbstimeline.yml",
        schedule_file=schedule_file,
        schedule_entrypoint="build_schedule",
        compiler_entrypoint="build_compiler",
        output_dir=tmp_path / ".qbs_timeline",
        low_level_q1timeline=False,
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )

    result = analyze_project(config)

    assert result.ir["operations"][0]["label"] == "X(q0)"
    assert result.ir["q1asm_programs"] == []
    assert result.ir["warnings"] == [
        "compile failed; rendered compact source preview only: Can not compile a schedule with untimed sections"
    ]


def test_extract_operations_unwraps_annotated_operations() -> None:
    class Operation:
        __slots__ = ("name", "duration")

        def __init__(self) -> None:
            self.name = "X(q0)"
            self.duration = 40e-9

    operation = annotate(Operation(), duration=sym.time("T_TOTAL", 40e-9))
    compiled_schedule = SimpleNamespace(
        schedulables={"x180": {"operation_id": "x_q0", "abs_time": 20e-9}},
        operations={"x_q0": operation},
    )

    operations = _extract_operations(compiled_schedule)

    assert operations == [
        {
            "id": "x180",
            "operation_id": "x_q0",
            "label": "X(q0)",
            "abs_time": 20e-9,
            "duration": 40e-9,
        }
    ]


def test_operation_dict_source_trace_attaches_to_operation_rows() -> None:
    trace = SourceTrace()
    schedule = SimpleNamespace(
        schedulables={"s0": {"operation_id": "op0", "abs_time": 0.0}},
        operation_dict={"op0": {"name": "X(q0)", "duration": 40e-9}},
        _qbstimeline_source_trace={
            "op0": {"file": "schedule.py", "line": 7, "column": 2},
        },
    )

    _merge_object_source_trace(trace, schedule)
    structure = _extract_schedule_structure(schedule, source_trace=trace)

    assert structure.operations[0]["source"] == {
        "file": "schedule.py",
        "line": 7,
        "column": 2,
    }
