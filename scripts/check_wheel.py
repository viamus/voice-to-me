"""Verify required runtime resources without installing or launching the app."""
from pathlib import Path
from zipfile import ZipFile

REQUIRED = {
    "voice_to_me/codex_status.py",
    "voice_to_me/paste.py",
    "voice_to_me/sounds.py",
    "voice_to_me/defaults/config.toml",
    "voice_to_me/defaults/writing-profile.md",
    "voice_to_me/assets/voiceui.ico",
    "voice_to_me/assets/voiceui.png",
    "voice_to_me/assets/recording-start.wav",
    "voice_to_me/assets/recording-stop.wav",
    "voice_to_me/assets/result-ready.wav",
}


def main() -> None:
    wheels = sorted(Path("dist").glob("voice_to_me-*.whl"), key=lambda path: path.stat().st_mtime)
    if not wheels:
        raise SystemExit("Build the wheel first with uv build --wheel.")
    with ZipFile(wheels[-1]) as package:
        missing = REQUIRED - set(package.namelist())
    if missing:
        raise SystemExit("Missing package resources: " + ", ".join(sorted(missing)))
    print(f"Wheel resources verified: {wheels[-1].name}")


if __name__ == "__main__":
    main()
