"""Tests for GRBL response parser."""
import pytest
from app.grbl.parser import (
    is_end_of_program,
    is_welcome_message,
    parse_grbl_version,
    parse_status_report,
    prepare_gcode_line,
    strip_gcode_comment,
)


class TestParseStatusReport:
    def test_idle_with_mpos(self):
        r = parse_status_report("<Idle|MPos:1.000,2.500,0.000|FS:0,0>")
        assert r is not None
        assert r.state == "Idle"
        assert r.sub_state is None
        assert r.mpos_x == pytest.approx(1.0)
        assert r.mpos_y == pytest.approx(2.5)
        assert r.feed_rate == 0.0

    def test_run_with_feed(self):
        r = parse_status_report("<Run|MPos:10.000,5.000,0.000|FS:1000,0>")
        assert r is not None
        assert r.state == "Run"
        assert r.feed_rate == pytest.approx(1000.0)

    def test_hold_with_substate(self):
        r = parse_status_report("<Hold:0|MPos:5.000,2.000,0.000|FS:0,0>")
        assert r is not None
        assert r.state == "Hold"
        assert r.sub_state == "0"

    def test_alarm_with_substate(self):
        r = parse_status_report("<Alarm:1|MPos:0.000,0.000,0.000>")
        assert r is not None
        assert r.state == "Alarm"
        assert r.sub_state == "1"

    def test_jog_state(self):
        r = parse_status_report("<Jog|MPos:3.000,0.000,0.000|FS:500,0>")
        assert r is not None
        assert r.state == "Jog"

    def test_wpos_instead_of_mpos(self):
        r = parse_status_report("<Idle|WPos:1.000,2.000,0.000|FS:0,0>")
        assert r is not None
        assert r.wpos_x == pytest.approx(1.0)
        assert r.wpos_y == pytest.approx(2.0)
        assert r.mpos_x is None

    def test_grbl_09_format(self):
        r = parse_status_report("<Idle,MPos:1.000,2.000,0.000,WPos:1.000,2.000,0.000>")
        assert r is not None
        assert r.state == "Idle"
        assert r.mpos_x == pytest.approx(1.0)
        assert r.mpos_y == pytest.approx(2.0)

    def test_negative_positions(self):
        r = parse_status_report("<Idle|MPos:-10.000,-5.500,0.000|FS:0,0>")
        assert r is not None
        assert r.mpos_x == pytest.approx(-10.0)
        assert r.mpos_y == pytest.approx(-5.5)

    def test_returns_none_for_ok(self):
        assert parse_status_report("ok") is None

    def test_returns_none_for_empty(self):
        assert parse_status_report("") is None

    def test_returns_none_for_error(self):
        assert parse_status_report("error:1") is None

    def test_returns_none_for_plain_text(self):
        assert parse_status_report("Grbl 1.1f ['$' for help]") is None

    def test_with_overrides_field(self):
        # Extra fields should not break parsing
        r = parse_status_report("<Idle|MPos:0.000,0.000,0.000|FS:0,0|Ov:100,100,100>")
        assert r is not None
        assert r.state == "Idle"


class TestWelcomeMessage:
    def test_grbl_11(self):
        assert is_welcome_message("Grbl 1.1f ['$' for help]")

    def test_grbl_09(self):
        assert is_welcome_message("Grbl 0.9j ['$' for help]")

    def test_lowercase(self):
        assert is_welcome_message("grbl 1.1f")

    def test_not_welcome(self):
        assert not is_welcome_message("ok")
        assert not is_welcome_message("")
        assert not is_welcome_message("<Idle|MPos:0,0,0>")

    def test_parse_version(self):
        assert parse_grbl_version("Grbl 1.1f ['$' for help]") == "1.1f"
        assert parse_grbl_version("Grbl 0.9j ['$' for help]") == "0.9j"
        assert parse_grbl_version("ok") is None


class TestGcodeStripping:
    def test_semicolon_comment(self):
        assert strip_gcode_comment("G0 X10 ; move to start") == "G0 X10"

    def test_parenthetical_comment(self):
        assert strip_gcode_comment("G0 X10 (rapid move)") == "G0 X10"

    def test_full_comment_line(self):
        assert strip_gcode_comment("; this is all comment") == ""

    def test_full_paren_line(self):
        assert strip_gcode_comment("(full comment)") == ""

    def test_no_comment(self):
        assert strip_gcode_comment("G1 X10 Y20 F500") == "G1 X10 Y20 F500"

    def test_preserves_whitespace_trim(self):
        assert strip_gcode_comment("  G0 X0  ") == "G0 X0"

    def test_multiple_parens(self):
        assert strip_gcode_comment("G1 (move) X10 (fast)") == "G1  X10"

    def test_prepare_line_returns_none_for_blank(self):
        assert prepare_gcode_line("") is None
        assert prepare_gcode_line("   ") is None
        assert prepare_gcode_line("; comment only") is None
        assert prepare_gcode_line("(comment)") is None

    def test_prepare_line_strips_comment(self):
        result = prepare_gcode_line("G0 X10 ; comment")
        assert result == "G0 X10"

    def test_prepare_line_passes_through(self):
        assert prepare_gcode_line("G1 X5 F100") == "G1 X5 F100"


class TestEndOfProgram:
    def test_m2(self):
        assert is_end_of_program("M2")
        assert is_end_of_program("M02")

    def test_m30(self):
        assert is_end_of_program("M30")

    def test_percent(self):
        assert is_end_of_program("%")

    def test_m2_with_comment(self):
        assert is_end_of_program("M2 ; end of program")

    def test_lowercase(self):
        assert is_end_of_program("m2")

    def test_not_end(self):
        assert not is_end_of_program("G0 X0 Y0")
        assert not is_end_of_program("M3")
        assert not is_end_of_program("")
