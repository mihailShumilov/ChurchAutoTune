"""Operator-facing texts for coded reasons, warnings and notes.

Transitional (docs/adr/0003): the expert CLI and the Markdown report keep the Russian texts of
the original tool as literals here. In M3 they move to the Fluent catalogues and this module
turns into key lookups.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from churchtune.design.crossover import CrossoverNote, NoteCode
from churchtune.design.proposal import (
    DesignWarning,
    Proposal,
    Reason,
    ReasonCode,
    SkipCode,
    TargetSpec,
)
from churchtune.domain.changes import Change
from churchtune.domain.eq import Band

LEGACY_CODE = "legacy"
Params = Mapping[str, float | int | str | bool | None]


class ValueFormats(Protocol):
    """How the console shows values; supplied by the driver."""

    def freq(self, freq_hz: float) -> str:
        """A frequency as the console shows it (``479.8``, ``1k97``)."""
        ...

    def q(self, q: float) -> str:
        """A Q value as the console shows it."""
        ...

    def band(self, band: Band) -> str:
        """A whole band as the console shows it."""
        ...


def target_description(target: TargetSpec) -> str:
    """One line describing the target curve."""
    lift = f"низ +{target.bass_lift_db:.0f} дБ ниже ~100 Гц, " if target.bass_lift_db else ""
    return f"{lift}ровно до {target.tilt_from_hz:.0f} Гц, выше {target.tilt_db_per_oct:+.1f} дБ/окт"


def _num(params: Params, key: str) -> float:
    value = params.get(key)
    return float(value) if isinstance(value, int | float) else float("nan")


def _points(params: Params) -> str:
    return f"{params.get('n')}/{params.get('n_points')}"


def _greedy_reason(code: ReasonCode, p: Params, fmt: ValueFormats) -> str:
    freq, width = fmt.freq(_num(p, "freq_hz")), f"ширина ≈{_num(p, 'width_oct'):.1f} окт"
    if code is ReasonCode.RELAX:
        return (
            f"Ослабить существующий вырез {_num(p, 'old_gain_db'):+.2f} -> "
            f"{_num(p, 'new_gain_db'):+.2f} дБ @ {freq} Гц: на его месте провал "
            f"{_num(p, 'dev_db'):+.1f} дБ в {_points(p)} точках — текущий вырез избыточен."
        )
    if code is ReasonCode.CUT:
        text = (
            f"Вырез {_num(p, 'gain_db'):+.2f} дБ @ {freq} Гц, Q {fmt.q(_num(p, 'q'))}: "
            f"усреднённая АЧХ на {_num(p, 'dev_db'):+.1f} дБ выше цели, пик в {_points(p)} "
            f"точках ({width})."
        )
        if p.get("mode_hint"):
            text += (
                " Похоже на комнатную моду/гул — вырез уменьшит бубнение, но полностью мода "
                "EQ не лечится."
            )
    else:
        text = (
            f"Подъём {_num(p, 'gain_db'):+.2f} дБ (лимит +{_num(p, 'max_boost_db'):.0f}) @ "
            f"{freq} Гц, Q {fmt.q(_num(p, 'q'))}: широкий провал {_num(p, 'dev_db'):+.1f} дБ в "
            f"{_points(p)} точках, разброс {_num(p, 'spread_db'):.1f} дБ ({width})."
        )
        if p.get("replaced_freq_hz") is not None:
            text += (
                f" Заменяет прежний вырез ({p.get('replaced_type')} "
                f"{fmt.freq(_num(p, 'replaced_freq_hz'))} Гц {_num(p, 'replaced_gain_db'):+.2f} "
                f"дБ Q {fmt.q(_num(p, 'replaced_q'))}), который углублял этот провал."
            )
    if p.get("approximate"):
        text += " ОРИЕНТИРОВОЧНО: выше ~10 кГц измерительный микрофон без калибровки неточен."
    return text


def _optimized_reason(p: Params, fmt: ValueFormats) -> str:
    gain = _num(p, "gain_db")
    text = (
        f"{'Подъём' if gain > 0 else 'Вырез'} {gain:+.2f} дБ @ {fmt.freq(_num(p, 'freq_hz'))} Гц "
        f"Q {fmt.q(_num(p, 'q'))}: в зоне ±1/3 окт среднее {_num(p, 'before_db'):+.1f} -> "
        f"{_num(p, 'after_db'):+.1f} дБ относительно цели; проблема в {_points(p)} точках."
    )
    if p.get("mode_zone"):
        text += " Зона мод/саба — разброс между точками большой, вырез компромиссный."
    if p.get("approximate"):
        text += " ОРИЕНТИРОВОЧНО (микрофон без калибровки выше 10 кГц)."
    return text


def _skip_reason(code: SkipCode, p: Params, fmt: ValueFormats) -> str:
    if code is SkipCode.LOCAL_PEAK:
        return f"пик только в {_points(p)} точках — локальная проблема, не трогаем"
    if code is SkipCode.NO_FREE_BAND:
        why = "вырезы приоритетнее" if p.get("cuts_first") else "приоритет у более сильных проблем"
        return f"нет свободных полос ({why})"
    if code is SkipCode.NARROW_DIP:
        return (
            f"узкий провал ({_num(p, 'width_oct'):.2f} окт) — интерференция/нуль, "
            "EQ его не исправит"
        )
    if code is SkipCode.LOCAL_DIP:
        return f"провал только в {_points(p)} точках"
    if code is SkipCode.LARGE_SPREAD:
        return f"большой разброс между точками ({_num(p, 'spread_db'):.1f} дБ) — зависит от позиции"
    if code is SkipCode.NEAR_ROLLOFF:
        return "рядом с естественным спадом колонок по НЧ — не поднимаем"
    if code is SkipCode.TOO_SMALL:
        return (
            f"расчётная коррекция {_num(p, 'gain_db'):+.2f} дБ — меньше "
            f"{_num(p, 'min_effect_db')} дБ, не нужна"
        )
    return (
        f"оптимизатор хотел {_num(p, 'gain_db'):+.2f} дБ @ {fmt.freq(_num(p, 'freq_hz'))} Гц "
        f"Q {fmt.q(_num(p, 'q'))}, но проблема только в {_points(p)} точках "
        f"(нужно {p.get('need')}) — полоса оставлена как была"
    )


_FIXED_REASONS: Mapping[ReasonCode, str] = {
    ReasonCode.LOCKED_CUT_FILTER: "фильтр среза — заблокирован, не меняется",
    ReasonCode.UNCHANGED: "без изменений",
    ReasonCode.UNCHANGED_INACTIVE: "без изменений (полоса не активна, 0 дБ)",
    ReasonCode.UNCHANGED_EXISTING: (
        "без изменений (существующая настройка, проблем рядом не найдено)"
    ),
}


def reason_text(reason: Reason, fmt: ValueFormats) -> str:
    """The sentence for a band reason or a skip reason."""
    code, p = reason.code, reason.params
    if isinstance(code, SkipCode):
        return str(p["text"]) if "text" in p else _skip_reason(code, p, fmt)
    if code in _FIXED_REASONS:
        return _FIXED_REASONS[code]
    if code is ReasonCode.LEGACY:
        return str(p.get("text", ""))
    if code is ReasonCode.NOTCH_KEPT:
        return (
            f"узкий вырез (вероятно, против обратной связи): {_num(p, 'old_gain_db'):+.2f} -> "
            f"{_num(p, 'new_gain_db'):+.2f} дБ, не меньше половины глубины. Проверьте вокальные "
            "микрофоны на звон."
        )
    if code is ReasonCode.NOT_NEEDED:
        return (
            f"полоса больше не нужна (в зоне {fmt.freq(_num(p, 'freq_hz'))} Гц отклонение уже "
            f"{_num(p, 'after_db'):+.1f} дБ)"
        )
    if code is ReasonCode.OPTIMIZED:
        return _optimized_reason(p, fmt)
    return _greedy_reason(code, p, fmt)


_DESIGN_WARNINGS: Mapping[DesignWarning, str] = {
    DesignWarning.EQ_CHANGED_SINCE_MEASUREMENT: (
        "EQ на пульте сейчас отличается от EQ во время замера — расчёт сделан от состояния "
        "во время замера; apply покажет все отличия."
    ),
    DesignWarning.EQ_WAS_OFF: (
        "Во время замера EQ был ВЫКЛЮЧЕН; предложение включает его (eq_on=true) вместе с "
        "заблокированными полосами — учтено в прогнозе."
    ),
}


def design_warning_text(warning: DesignWarning) -> str:
    """The sentence for a proposal warning."""
    return _DESIGN_WARNINGS[warning]


def proposal_note(proposal: Proposal) -> str:
    """The closing note of a proposal: what the prediction is worth."""
    if proposal.method.value == "optimize":
        return (
            "Совместная оптимизация полос (метод optimize). Прогноз — модель; истина — verify. "
            "max_dev = наибольшее ПРЕВЫШЕНИЕ над целью (провалы EQ не заполняет)."
        )
    return (
        "Модель EQ пульта приблизительная (RBJ-биквады). Прогноз — ориентир; истину покажет verify."
    )


def crossover_note_text(note: CrossoverNote, fmt: ValueFormats) -> str:
    """The line for one entry of a crossover plan."""
    p = note.params
    slope, fc = p.get("slope"), _num(p, "fc_hz")
    head = f"Mtx{note.matrix} band {note.band.band}: {fmt.band(note.band)}"
    texts = {
        NoteCode.FAKE_LOW_PASS_REMOVED: (
            f"{head} -> 0 дБ (заменяется настоящим ФНЧ {slope} {fc:.0f} Гц)"
        ),
        NoteCode.BOOST_ABOVE_SUB_REMOVED: f"{head} -> 0 дБ (подъём выше среза саба не нужен)",
        NoteCode.BOOST_IN_ZONE_REMOVED: (f"{head} -> 0 дБ (подъём в зоне кроссовера портит стык)"),
        NoteCode.SUB_LOW_PASS: f"{head} -> {slope} {fmt.freq(fc)} Гц (ФНЧ саба)",
        NoteCode.TOP_HIGH_PASS: f"{head} -> {slope} {fmt.freq(fc)} Гц (ФВЧ сателлитов)",
        NoteCode.FAKE_HIGH_PASS_REMOVED: f"{head} -> 0 дБ (имитация ФВЧ больше не нужна)",
        NoteCode.MORE_LOWS_TO_TOPS: (
            f"!! Mtx{note.matrix}: новый ФВЧ пропускает в сателлиты БОЛЬШЕ низа, чем старый "
            f"({_num(p, 'old_db'):.0f} -> {_num(p, 'new_db'):.0f} дБ на {_num(p, 'at_hz'):.0f} Гц)"
            " — проверьте, что колонки это выдержат"
        ),
    }
    return texts[note.code]


def quality_warning_text(code: str, params: Params) -> str:
    """The sentence for a coded quality warning of a measured point."""
    p = params
    if code == LEGACY_CODE:
        return str(p.get("text", ""))
    if code == "clipping":
        return (
            f"КЛИППИНГ: {p.get('count')} сэмплов >= {_num(p, 'threshold_dbfs')} dBFS — "
            "уменьшите уровень (--level) или gain входа микрофона"
        )
    if code == "hot":
        return f"пик {_num(p, 'peak_dbfs'):.1f} dBFS — близко к клиппингу"
    if code == "quiet":
        return (
            f"слишком тихо: пик {_num(p, 'peak_dbfs'):.1f} dBFS — поднимите gain входа "
            "микрофона / проверьте +48V"
        )
    if code == "low-snr":
        return (
            f"низкий SNR {_num(p, 'snr_db'):.1f} дБ (шум зала {_num(p, 'noise_dbfs'):.1f} dBFS) "
            "— тишина в зале, громче свип"
        )
    if code == "low-pnr":
        tail = "; замер ненадёжен" if p.get("unreliable") else ""
        return (
            f"PNR импульса {_num(p, 'pnr_db'):.1f} дБ < {_num(p, 'needed_db'):.0f} — "
            f"T30 будет недоступен{tail}"
        )
    if code == "xrun":
        return f"аудио xrun/статус: {p.get('flags')} — повторите замер"
    return f"{code}: {dict(p)}"


def format_changes(changes: Sequence[Change], title: str = "Изменения") -> str:
    """A diff table of planned writes: parameter, was, will be."""
    if not changes:
        return f"{title}: нет (пульт уже в целевом состоянии)"
    width = max(len(c.path) for c in changes)
    lines = [
        f"{title} ({len(changes)}):",
        f"  {'параметр':<{width}}  {'было':>12}  ->  {'станет':<12}",
    ]
    lines += [f"  {c.path:<{width}}  {c.old_display:>12}  ->  {c.new_display:<12}" for c in changes]
    return "\n".join(lines)
