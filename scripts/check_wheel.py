"""Verify runtime resources and MIT licensing without installing or launching the app."""
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

REQUIRED = {
    "voice_to_me/codex_status.py",
    "voice_to_me/history.py",
    "voice_to_me/history_ui.py",
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
        names = set(package.namelist())
        missing = REQUIRED - names
        metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise SystemExit("Expected exactly one wheel METADATA file.")
        metadata_path = metadata_files[0]
        metadata = BytesParser().parsebytes(package.read(metadata_path))
        if metadata.get("License-Expression") != "MIT":
            raise SystemExit("Expected MIT license expression in wheel metadata.")
        if "LICENSE" not in metadata.get_all("License-File", []):
            raise SystemExit("Expected LICENSE declaration in wheel metadata.")
        license_path = metadata_path.removesuffix("METADATA") + "licenses/LICENSE"
        if license_path not in names or package.read(license_path) != Path("LICENSE").read_bytes():
            raise SystemExit("Wheel must include the project's complete LICENSE file.")
    if missing:
        raise SystemExit("Missing package resources: " + ", ".join(sorted(missing)))
    print(f"Wheel resources and MIT license verified: {wheels[-1].name}")


if __name__ == "__main__":
    main()
