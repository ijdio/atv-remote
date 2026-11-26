"""Package layout: the headless web remote must not need the macOS menu bar stack."""

import subprocess
import sys


def test_web_import_does_not_load_menubar():
    code = (
        "import sys, atv_remote.web; "
        "print(any(m in sys.modules for m in ('rumps', 'AppKit', 'WebKit')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


def test_main_still_exported():
    import atv_remote

    assert "main" in atv_remote.__all__
    if sys.platform == "darwin":
        assert callable(atv_remote.main)
