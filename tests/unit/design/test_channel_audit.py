"""The channel audit: advice only, grouped for a volunteer."""

from dataclasses import replace

from churchtune.design.channel_audit import AuditCode, audit_channels, is_voice
from churchtune.domain.eq import Band, EqType
from churchtune.domain.snapshot import Headamp, InputKind, InputRef
from tests.fixtures.topologies import TOPOLOGIES, load

HALL = load(TOPOLOGIES / "hall-2026-10-02.toml").snapshot()


def codes(findings):
    return [f.code for f in findings]


def test_the_reference_hall_shows_shared_cuts_and_a_probable_feedback_notch():
    findings = audit_channels(HALL)
    shared = [f for f in findings if f.code is AuditCode.SHARED_CUT]
    assert shared[0].freq_hz is not None
    assert 200 <= shared[0].freq_hz <= 320
    assert len(shared[0].channels) >= 5
    assert len(shared) <= 3
    assert AuditCode.POSSIBLE_FEEDBACK_NOTCH in codes(findings)
    assert AuditCode.VOICE_HPF_HIGH not in codes(findings)  # up to 226 Hz is normal for speech
    assert len(findings) <= 6


def test_a_shared_cut_near_a_system_cut_is_called_room_compensation():
    findings = audit_channels(HALL, system_cuts_hz=(300.0,))
    assert codes(findings).count(AuditCode.SHARED_CUT_ROOM) == 1


def test_a_voice_hpf_above_250_hz_is_reported():
    ch = replace(HALL.channels[1], hpf_on=True, hpf_hz=320.0, eq_on=False)
    findings = audit_channels(replace(HALL, channels={1: ch}))
    assert codes(findings) == [AuditCode.VOICE_HPF_HIGH]


def test_voices_are_found_by_name_in_several_languages():
    channel = HALL.channels[1]
    for name in (
        "Vocal 1",
        "Pastor",
        "Радио 2",
        "Голос",
        "Voz",
        "Headset",
        "BV 2",
        "Хор",
        "MC",
        "Bass Vox",
        "Бас вокал",
    ):
        assert is_voice(replace(channel, name=name))
    for name in ("PIANO", "Lead Gtr", "Radio Bass", "радио гитара", "Leader", "Keynote clip"):
        assert not is_voice(replace(channel, name=name))


def test_a_voice_without_high_pass_and_a_big_boost():
    ch = HALL.channels[1]
    boost = Band(2, EqType.PEQ, 3000.0, 8.0, 1.0)
    changed = replace(ch, hpf_on=False, eq_on=True, eq=(*ch.eq[:1], boost, *ch.eq[2:]))
    snapshot = replace(HALL, channels={1: changed})
    findings = audit_channels(snapshot)
    assert codes(findings)[0] is AuditCode.VOICE_NO_HPF
    big = next(f for f in findings if f.code is AuditCode.BIG_BOOST)
    assert big.channels == (1,)
    assert big.gain_db == 8.0


def test_unused_channels_are_not_audited():
    ch = replace(HALL.channels[1], on=False, name="", hpf_on=False)
    assert audit_channels(replace(HALL, channels={1: ch})) == ()
    off = replace(HALL.channels[1], source=InputRef(InputKind.OFF), hpf_on=False)
    assert audit_channels(replace(HALL, channels={1: off})) == ()


def test_phantom_on_a_dynamic_microphone_is_a_hint_when_the_driver_knows_the_preamp():
    ch = replace(HALL.channels[1], name="SM58 lead", hpf_on=True, hpf_hz=100.0, eq_on=False)
    snapshot = replace(HALL, channels={1: ch})
    with_phantom = audit_channels(snapshot, lambda _src: Headamp(0, 30.0, phantom=True))
    assert codes(with_phantom) == [AuditCode.PHANTOM_ON_DYNAMIC]
    assert audit_channels(snapshot, lambda _src: Headamp(0, 30.0, phantom=False)) == ()
    assert audit_channels(snapshot) == ()
