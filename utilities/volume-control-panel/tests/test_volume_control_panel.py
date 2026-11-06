from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "volume-control-panel.bat"


def commands() -> list[str]:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    return [
        line.strip() for line in lines if line.strip() and not line.startswith("::")
    ]


def test_opens_only_the_sound_control_panel() -> None:
    starts = [line for line in commands() if line.lower().startswith("start ")]
    assert len(starts) == 1
    assert starts[0].startswith('start "" mmsys.cpl')


def test_reports_a_failed_launch_with_status_one() -> None:
    script = commands()
    assert "@echo off" in script
    assert "exit /b 1" in script
    assert script.index("if errorlevel 1 (") < script.index("exit /b 1")
