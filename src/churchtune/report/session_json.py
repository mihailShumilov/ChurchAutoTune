"""The report as machine-readable data (``report.json``, SPEC §10): no language, no pictures.

Curves are given in thirds of an octave (the averaged response of the grid in each third), which
keeps the file small and enough for another tool or a person with a spreadsheet.
"""

from dataclasses import asdict
from typing import Any

import numpy as np

from churchtune.analysis.bands import OCTAVES_HZ
from churchtune.analysis.points import Analysis
from churchtune.domain.arrays import FloatArray
from churchtune.report.findings import findings
from churchtune.report.model import Msg, ReportData

SCHEMA_VERSION = 1
THIRDS_HZ = (
    31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000,
)  # fmt: skip


def thirds_db(grid_hz: FloatArray, curve_db: FloatArray) -> list[float | None]:
    """The curve's mean in each third of an octave of ``THIRDS_HZ`` (None outside the grid)."""
    values: list[float | None] = []
    for centre in THIRDS_HZ:
        near = (grid_hz >= centre * 2 ** (-1 / 6)) & (grid_hz < centre * 2 ** (1 / 6))
        values.append(round(float(np.mean(curve_db[near])), 2) if near.any() else None)
    return values


def _msg(msg: Msg) -> dict[str, Any]:
    args = {k: _msg(v) if isinstance(v, Msg) else v for k, v in msg.args.items()}
    return {"key": msg.key, "args": args}


def _analysis(an: Analysis) -> dict[str, Any]:
    return {
        "points": list(an.point_names),
        "corrected_from_hz": round(an.f_low_hz, 1),
        "thirds_hz": list(THIRDS_HZ),
        "average_db": thirds_db(an.grid_hz, an.avg_db),
        "spread_db": thirds_db(an.grid_hz, an.spread_db),
        "per_point_db": {name: thirds_db(an.grid_hz, c) for name, c in an.norm_db.items()},
        "decay_s": {
            str(c): {"t20": an.rt60[c].t20.mean_s, "edt": an.rt60[c].edt.mean_s}
            for c in OCTAVES_HZ
            if c in an.rt60
        },
    }


def report_json(data: ReportData) -> dict[str, Any]:
    """Everything the report tells, as plain JSON values."""
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "venue": data.venue_name,
        "created": data.created,
        "tool_version": data.tool_version,
        "console_model": data.console_model,
        "roles": dict(data.roles),
        "variant": data.variant,
        "mic_count": data.mic_count,
        "calibration": data.calibration,
        "measurement": dict(data.sweep),
        "points": [asdict(p) for p in data.points],
        "advice": [_msg(m) for m in data.advice],
        "applied": data.applied_state,
        "changes": [
            {"path": c.path, "before": c.before_value, "after": c.after_value} for c in data.changes
        ],
        "undo_file": data.undo_file,
        "audit": [asdict(a) for a in data.audit],
        "baseline": None,
        "findings": [],
        "junction": None,
        "verify": None,
    }
    if data.analysis is not None:
        out["baseline"] = _analysis(data.analysis)
        out["findings"] = [_msg(m) for m in findings(data.analysis, data.target_db)]
    if data.alignment is not None:
        a = data.alignment
        out["junction"] = {
            "fc_hz": round(a.fc_hz, 1),
            "sub_minus_tops_db": round(a.sub_minus_tops_at_fc_db, 2),
            "delay_ms": round(a.delay_ms, 2),
            "polarity": a.polarity,
        }
    if data.verify is not None:
        v = data.verify
        out["verify"] = {
            "verdict": v.verdict.value,
            "points": list(v.points),
            "m_before_db": round(v.m_before_db, 2),
            "m_after_db": round(v.m_after_db, 2),
            "worst_point": v.worst_point,
            "worst_delta_db": round(v.worst_delta_db, 2),
            "band_delta_db": round(v.band_delta_db, 2),
            "band_point": v.band_point,
            "band_hz": round(v.band_hz, 1),
            "spread_delta_db": round(v.spread_delta_db, 2),
            "after": _analysis(data.verify_analysis) if data.verify_analysis else None,
        }
    return out
