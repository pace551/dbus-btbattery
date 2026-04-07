import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import utils
import battery


class _MockBattery(battery.Battery):
    def test_connection(self): return True
    def get_settings(self): return True
    def refresh_data(self): return True


def _make_battery():
    b = _MockBattery("/bttest", 0, "test")
    b.max_battery_charge_current = 70.0
    b.max_battery_discharge_current = 90.0
    b.soc = 50
    return b


def _raise(*args, **kwargs):
    raise ValueError("injected error")


def test_discharge_cv_returns_discharge_current_on_error(monkeypatch):
    b = _make_battery()
    monkeypatch.setattr(utils, "calcStepRelationship", _raise)
    monkeypatch.setattr(utils, "calcLinearRelationship", _raise)
    result = b.calcMaxDischargeCurrentReferringToCellVoltage()
    assert result == 90.0, f"Expected 90.0 (discharge), got {result}"


def test_discharge_soc_returns_discharge_current_on_error(monkeypatch):
    b = _make_battery()
    monkeypatch.setattr(utils, "calcStepRelationship", _raise)
    monkeypatch.setattr(utils, "calcLinearRelationship", _raise)
    result = b.calcMaxDischargeCurrentReferringToSoc()
    assert result == 90.0, f"Expected 90.0 (discharge), got {result}"
