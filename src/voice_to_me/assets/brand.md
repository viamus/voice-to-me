# Voice to Me identity

The application is displayed as **Voice to Me**. The repository is `voice-to-me`
and the Python package is `voice_to_me`; the existing `voiceui-*` asset filenames
remain stable. The original mark combines a speech bubble with four rounded
waveform bars: voice becomes a clear message. Use the square icon in the Windows
title bar and tray, and the wordmark in documentation. Keep both legible at small
sizes. The tagline is **YOUR VOICE. YOUR WORDS.** Use **WINDOWS** as a platform
label without repeating the application name.

| Role | Color |
| --- | --- |
| Pine / primary text and brand | `#123B42` |
| Coral / recording and primary action | `#EF896B` |
| Teal / ready and processing | `#167C80` |
| Canvas | `#F3F7F6` |
| Surface | `#FFFFFF` |
| Secondary text | `#49646A` |
| Success | `#16725D` |
| Error | `#B3383A` |

Use Windows Segoe UI, with bold labels and generous spacing. Buttons are large
enough for one-handed use, keyboard focus is visible, and every state has a
written label and symbol in addition to color. Avoid flashing and unnecessary
animation. The Dictation screen communicates recording, local transcription,
refinement and clipboard completion. Completed text is visible only in the
explicit History page; tray status and notifications contain no message text.

The interface, status messages, settings, and documentation use English.
Dictated content keeps its source language. Portuguese remains the default
speech language and can be changed in Settings. **Dictation**, **History** and
**Settings** share one window and a consistent navigation strip. The built-in
settings editor captures keyboard or mouse shortcuts and edits the writing style
alongside microphone, transcription profile and Codex options.
It does not ask the user to open an external text editor for these tasks.

The logo SVGs, PNG, and ICO are original local project assets. The vectors do not
depend on web fonts or a remote asset server. `voiceui.png` is the 256px icon;
`voiceui.ico` contains 16, 24, 32, 48, 64, 128, and 256px variants.

The tray has a separate `voiceui-{state}.png` for each state, with soft color
accents and recording, processing, completion, or error badges. Completion/error
notifications report status only and never contain dictated or refined text.

`voiceui-preview.png` shows the main interface and its navigation.
`settings-preview.png` illustrates the built-in shortcut capture and writing-style
editor. `history-preview.png` shows session history using synthetic messages.
These are representative interface illustrations rendered locally with Pillow.
They are not desktop screenshots and do not access a microphone or
clipboard. Rebuild the raster assets with Python 3.12:

```powershell
.venv\Scripts\python.exe src\voice_to_me\assets\build_assets.py
```
