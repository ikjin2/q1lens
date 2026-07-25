from __future__ import annotations

from pathlib import Path

from qbstimeline.render.gallery import render_gallery_index, write_gallery_index


def test_render_gallery_index_links_examples_and_summarizes_statuses() -> None:
    html = render_gallery_index(
        [
            {
                "id": "rabi",
                "title": "Rabi",
                "category": "timedomain",
                "href": "rabi/index.html",
                "status": "ok",
                "operation_count": 3,
                "timing_row_count": 3,
                "q1asm_program_count": 2,
                "source": "src/qblox_scheduler/schedules/timedomain_schedules.py::rabi_sched",
            },
            {
                "id": "nv-dark-esr",
                "title": "NV dark ESR",
                "category": "spectroscopy",
                "href": "nv-dark-esr/index.html",
                "status": "error",
                "error": "unsupported mock setup",
                "operation_count": 0,
                "timing_row_count": 0,
                "q1asm_program_count": 0,
                "source": "src/qblox_scheduler/schedules/spectroscopy_schedules.py::nv_dark_esr_sched",
            },
        ],
        title="Scheduler Gallery",
    )

    assert "<!doctype html>" in html
    assert "Scheduler Gallery" in html
    assert "2 examples" in html
    assert "1 compiled" in html
    assert "1 failed" in html
    assert 'href="rabi/index.html"' in html
    assert "src/qblox_scheduler/schedules/timedomain_schedules.py::rabi_sched" in html
    assert "unsupported mock setup" in html


def test_write_gallery_index_creates_parent_directory(tmp_path: Path) -> None:
    out = tmp_path / "gallery" / "index.html"

    write_gallery_index([], out, title="Empty Gallery")

    html = out.read_text(encoding="utf-8")
    assert "Empty Gallery" in html
    assert "No examples generated." in html
