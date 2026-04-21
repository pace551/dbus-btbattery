import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils import mapRange
import utils


def test_mapRange_normal():
    assert mapRange(5, 0, 10, 0, 100) == 50.0


def test_mapRange_equal_bounds_returns_outMin_zero():
    # Without the guard this raises ZeroDivisionError
    assert mapRange(5, 3, 3, 0, 100) == 0


def test_mapRange_equal_bounds_returns_outMin_nonzero():
    assert mapRange(5, 3, 3, 40, 100) == 40


def test_BT_CONNECT_TIMEOUT_is_positive_int():
    assert isinstance(utils.BT_CONNECT_TIMEOUT, int)
    assert utils.BT_CONNECT_TIMEOUT > 0


def test_BT_WATCHDOG_TIMEOUT_is_non_negative_int():
    assert isinstance(utils.BT_WATCHDOG_TIMEOUT, int)
    assert utils.BT_WATCHDOG_TIMEOUT >= 0


def test_BT_ADAPTER_env_var_overrides_config():
    """service/run resolves BT_ADAPTER_MAC to an hciN name and exports it
    via env. The Python process must use the env value, not the config
    value, so both halves of the service stay in sync."""
    import importlib
    old_env = os.environ.get("BT_ADAPTER")
    try:
        os.environ["BT_ADAPTER"] = "hci7"
        # Reimport utils so BT_ADAPTER is re-evaluated with the env set.
        importlib.reload(utils)
        assert utils.BT_ADAPTER == "hci7"
    finally:
        if old_env is None:
            os.environ.pop("BT_ADAPTER", None)
        else:
            os.environ["BT_ADAPTER"] = old_env
        importlib.reload(utils)


def test_BT_ADAPTER_empty_env_falls_back_to_config():
    """An empty BT_ADAPTER env var must not mask the config value — shell
    exports the env even when the MAC didn't resolve, and we want the
    config fallback path to still work."""
    import importlib
    old_env = os.environ.get("BT_ADAPTER")
    try:
        os.environ["BT_ADAPTER"] = ""
        importlib.reload(utils)
        # Falls back to config value (which defaults to '' in default_config.ini).
        assert utils.BT_ADAPTER == ""
    finally:
        if old_env is None:
            os.environ.pop("BT_ADAPTER", None)
        else:
            os.environ["BT_ADAPTER"] = old_env
        importlib.reload(utils)


def test_BT_ADAPTER_MAC_exposed_from_config():
    """BT_ADAPTER_MAC is exposed on utils for diagnostics — runtime
    resolution happens in service/run, but utils should reflect what
    was configured for logging/debugging."""
    assert hasattr(utils, "BT_ADAPTER_MAC")
    assert isinstance(utils.BT_ADAPTER_MAC, str)
