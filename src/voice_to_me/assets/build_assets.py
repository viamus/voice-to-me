"""Rebuild original Voice to Me assets locally with Pillow; no screen capture."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
PINE = "#123B42"
CORAL = "#EF896B"
CANVAS = "#F3F7F6"
INK = "#163D45"
MUTED = "#49646A"
TEAL = "#167C80"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = Path("C:/Windows/Fonts") / ("segoeuib.ttf" if bold else "segoeui.ttf")
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default(size)


def icon(size: int, bubble_color: str = CORAL) -> Image.Image:
    scale = 4
    side = size * scale
    image = Image.new("RGBA", (side, side))
    draw = ImageDraw.Draw(image)
    factor = side / 256

    def box(*coordinates: int) -> tuple[int, ...]:
        return tuple(round(c * factor) for c in coordinates)

    draw.rounded_rectangle(box(0, 0, 256, 256), radius=58 * factor, fill=PINE)
    draw.ellipse(box(40, 32, 216, 208), fill=bubble_color)
    draw.polygon([box(164, 179), box(207, 216), box(199, 157)], fill=bubble_color)
    for x, top, bottom in ((80, 104, 136), (112, 85, 155), (144, 66, 174), (176, 94, 146)):
        draw.rounded_rectangle(box(x - 7, top - 7, x + 7, bottom + 7), radius=7 * factor, fill=PINE)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def text(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    value: str,
    size: int,
    color: str = INK,
    bold: bool = False,
) -> None:
    draw.text(position, value, font=font(size, bold), fill=color)


def pill(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    label: str,
    *,
    fill: str = "#FFFFFF",
    color: str = INK,
    size: int = 18,
) -> None:
    draw.rounded_rectangle(bounds, radius=12, fill=fill)
    length = draw.textlength(label, font=font(size, True))
    left, top, right, bottom = bounds
    text(
        draw,
        (round((left + right - length) / 2), round((top + bottom - size) / 2 - 3)),
        label,
        size,
        color,
        True,
    )


def wrapped_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    value: str,
    width: int,
    *,
    size: int = 18,
    color: str = MUTED,
    spacing: int = 9,
) -> int:
    """Draw readable paragraphs without truncating the local settings mockup."""
    x, y = position
    for paragraph in value.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if line and draw.textlength(candidate, font=font(size)) > width:
                text(draw, (x, y), line, size, color)
                y += size + spacing
                line = word
            else:
                line = candidate
        if line:
            text(draw, (x, y), line, size, color)
        y += size + spacing
    return y


def _settings_contents() -> Image.Image:
    """Show one embedded Settings page; the preview never captures a device."""
    image = Image.new("RGB", (940, 1771), CANVAS)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((48, 38, 902, 1713), radius=24, fill="#D5E2DF")
    draw.rounded_rectangle((40, 30, 894, 1705), radius=24, fill=CANVAS)
    draw.rounded_rectangle((40, 30, 894, 190), radius=24, fill=PINE)
    draw.rectangle((40, 150, 894, 190), fill=PINE)
    mark = icon(76)
    image.paste(mark, (77, 72), mark)
    text(draw, (174, 65), "Voice to Me", 38, "#FFFFFF", True)
    text(draw, (177, 120), "YOUR VOICE. YOUR WORDS.", 17, "#CBDEDD")
    text(draw, (835, 47), "×", 28, "#CBDEDD")
    pill(draw, (75, 213, 198, 261), "← Back", size=18)
    text(draw, (219, 211), "Settings", 31, INK, True)
    text(draw, (77, 278), "Your shortcuts stay paused while you edit.", 18, MUTED)

    def section(top: int, bottom: int, label: str) -> None:
        draw.rounded_rectangle(
            (74, top, 859, bottom), radius=13, fill="#FFFFFF", outline="#D5E2DF", width=2
        )
        text(draw, (96, top + 17), label, 21, INK, True)

    def field(y: int, label: str, value: str, *, dropdown: bool = False) -> None:
        text(draw, (98, y + 9), label, 18, INK)
        draw.rounded_rectangle(
            (338, y, 835, y + 46), radius=7, fill="#F8FAF9", outline="#D5E2DF", width=2
        )
        text(draw, (352, y + 8), value, 18, INK)
        if dropdown:
            draw.polygon([(800, y + 18), (812, y + 18), (806, y + 26)], fill=MUTED)

    section(322, 527, "Recording shortcut")
    draw.rounded_rectangle(
        (97, 375, 835, 420), radius=7, fill="#F8FAF9", outline="#D5E2DF", width=2
    )
    text(draw, (112, 384), "Mouse X1", 21, INK, True)
    pill(draw, (97, 432, 330, 480), "Capture shortcut", fill=CORAL, color=PINE, size=18)
    pill(draw, (343, 432, 447, 480), "Clear", size=18)
    text(draw, (98, 492), "Choose a key, combination, or any mouse button.", 17, MUTED)

    section(545, 750, "Paste shortcut (Ctrl + V)")
    draw.rounded_rectangle(
        (97, 598, 835, 643), radius=7, fill="#F8FAF9", outline="#D5E2DF", width=2
    )
    text(draw, (112, 607), "Mouse X2", 21, INK, True)
    pill(draw, (97, 655, 365, 703), "Capture paste shortcut", fill=CORAL, color=PINE, size=18)
    pill(draw, (378, 655, 482, 703), "Clear", size=18)
    text(draw, (98, 715), "Release your shortcut in the app where you want to paste.", 17, MUTED)

    section(768, 1049, "Writing style")
    draw.rounded_rectangle(
        (97, 821, 835, 1006), radius=7, fill="#F8FAF9", outline="#D5E2DF", width=2
    )
    wrapped_text(
        draw,
        (113, 833),
        "Preserve my meaning, tone, names, numbers, and facts. Remove "
        "hesitations and accidental repetition.\n\n"
        "Use clear punctuation and a natural, direct voice. Keep questions "
        "as questions. Return only the polished text.",
        696,
        size=19,
        color=INK,
        spacing=10,
    )
    text(draw, (98, 1020), "Edit your instructions here whenever you need.", 17, MUTED)

    section(1067, 1344, "Audio & transcription")
    field(1118, "Microphone", "Default input", dropdown=True)
    field(1177, "Dictation language", "pt")
    field(1236, "Transcription profile", "Balanced", dropdown=True)
    pill(draw, (338, 1294, 529, 1333), "Download model", size=17)
    text(draw, (552, 1302), "GPU acceleration: Ready", 17, TEAL, True)

    section(1362, 1611, "Codex")
    draw.rounded_rectangle((98, 1418, 119, 1439), radius=3, fill=TEAL)
    draw.line([(102, 1428), (107, 1434), (115, 1423)], fill="#FFFFFF", width=3)
    text(draw, (133, 1413), "Refine with Codex CLI", 20, INK)
    text(draw, (98, 1452), "Codex CLI: Ready", 17, TEAL, True)
    field(1487, "Model (optional)", "")
    wrapped_text(draw, (98, 1546),
                 "Uses your writing style. Leave the model blank to use the default.",
                 716, size=17)
    draw.line((76, 1631, 858, 1631), fill="#D5E2DF", width=2)
    pill(draw, (499, 1646, 628, 1689), "Cancel", size=18)
    pill(draw, (644, 1646, 858, 1689), "Save & Apply", fill=CORAL, color=PINE, size=18)
    text(
        draw,
        (43, 1732),
        "Same Voice to Me window · Local illustration · No device capture",
        17,
        MUTED,
    )
    return image


def preview_shell(page: str, bottom: int = 997) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (940, bottom + 76), CANVAS)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((48, 38, 902, bottom + 8), radius=24, fill="#D5E2DF")
    draw.rounded_rectangle((40, 30, 894, bottom), radius=24, fill=CANVAS)
    draw.rounded_rectangle((40, 30, 894, 144), radius=24, fill=PINE)
    draw.rectangle((40, 116, 894, 144), fill=PINE)
    mark = icon(64)
    image.paste(mark, (77, 55), mark)
    text(draw, (160, 53), "Voice to Me", 34, "#FFFFFF", True)
    text(draw, (163, 99), "YOUR VOICE. YOUR WORDS.", 14, "#CBDEDD")
    text(draw, (836, 47), "×", 27, "#CBDEDD")
    for index, label in enumerate(("Dictation", "History", "Settings")):
        left = 76 + index * 263
        selected = label.lower() == page
        pill(draw, (left, 165, left + 250, 215), label,
             fill=PINE if selected else "#FFFFFF", color="#FFFFFF" if selected else INK)
    text(draw, (43, bottom + 32),
         "Same Voice to Me window · Local illustration · No device capture", 17, MUTED)
    return image, draw


def preview_footer(draw: ImageDraw.ImageDraw, y: int = 910) -> None:
    draw.line((76, y, 858, y), fill="#D5E2DF", width=2)
    pill(draw, (76, y + 19, 235, y + 63), "Minimize", size=18)
    pill(draw, (757, y + 19, 859, y + 63), "Quit", size=18)


def settings_preview() -> Image.Image:
    image, _draw = preview_shell("settings", bottom=1735)
    # Show the full scrollable Settings content below the shared navigation.
    content = _settings_contents().crop((74, 205, 860, 1696))
    image.paste(content, (74, 232))
    return image


def dictation_preview() -> Image.Image:
    image, draw = preview_shell("dictation")
    text(draw, (76, 241), "Dictation", 31, INK, True)
    text(draw, (77, 290), "Speak naturally. Your text is copied when ready.", 19, MUTED)
    draw.rounded_rectangle((75, 335, 859, 503), radius=12, fill="#FFFFFF", outline="#D5E2DF", width=2)
    text(draw, (98, 353), "02", 28, TEAL, True)
    text(draw, (157, 352), "Transcribing", 28, INK, True)
    text(draw, (99, 407), "Turning your recording into text locally.", 20, MUTED)
    text(draw, (99, 438), "4 seconds elapsed", 18, MUTED)
    draw.rounded_rectangle((99, 479, 835, 484), radius=2, fill="#D5E2DF")
    draw.rounded_rectangle((269, 479, 449, 484), radius=2, fill=TEAL)
    pill(draw, (76, 530, 859, 606), "Transcribing…", fill=CORAL, color="#617572", size=26)
    text(draw, (203, 627), "Record: Mouse X1  ·  in this window: Alt + R", 18, MUTED)
    text(draw, (391, 656), "Paste: Mouse X2", 18, MUTED)
    pill(draw, (76, 708, 458, 762), "Cancel", size=20)
    pill(draw, (475, 708, 859, 762), "Try again", color="#91A4A0", size=20)
    text(draw, (77, 802), "Choose where to paste and send.", 18, MUTED)
    text(draw, (77, 833), "Find your recent messages in History.", 18, MUTED)
    preview_footer(draw)
    return image


def history_preview() -> Image.Image:
    image, draw = preview_shell("history")
    text(draw, (76, 241), "History", 31, INK, True)
    text(draw, (77, 290), "Last 30 messages in this session. Cleared when you quit.", 18, MUTED)
    draw.rounded_rectangle((76, 333, 859, 523), radius=6, fill="#FFFFFF", outline="#D5E2DF", width=2)
    draw.rectangle((78, 335, 857, 376), fill="#EAF1EE")
    for x, label in ((97, "Time"), (229, "Mode"), (348, "Message")):
        text(draw, (x, 344), label, 18, INK, True)
    for index, (stamp, mode, snippet) in enumerate((
        ("12:04:36", "Refined", "Please review the proposal before Friday."),
        ("12:02:09", "Local", "Move tomorrow's meeting to 10 am."),
        ("11:58:42", "Refined", "Thank you for sending the updated plan."),
    )):
        y = 377 + index * 47
        if index == 0:
            draw.rectangle((78, y, 857, y + 47), fill=TEAL)
        color = "#FFFFFF" if index == 0 else INK
        text(draw, (97, y + 12), stamp, 17, color)
        text(draw, (229, y + 12), mode, 17, color)
        text(draw, (348, y + 12), snippet, 17, color)
    text(draw, (77, 553), "Refined with Codex CLI · Oct 03, 12:04:36 · 3.2 s total", 17, MUTED)
    draw.rounded_rectangle((76, 590, 859, 777), radius=6, fill="#FFFFFF", outline="#D5E2DF", width=2)
    wrapped_text(draw, (97, 613),
                 "Please review the proposal before Friday.\n\n"
                 "I would like to confirm the next steps before our meeting.",
                 718, size=21, color=INK, spacing=11)
    pill(draw, (76, 804, 247, 858), "Copy text", size=19)
    pill(draw, (659, 804, 859, 858), "Clear history", size=19)
    text(draw, (77, 875), "Copied to clipboard. Paste whenever you're ready.", 17, MUTED)
    preview_footer(draw, y=911)
    return image


def build() -> None:
    mark = icon(256)
    mark.save(ROOT / "voiceui.png")
    mark.save(ROOT / "voiceui.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    for state, color in {
        "ready": "#86CEC8",
        "recording": CORAL,
        "transcribing": "#96C7E8",
        "refining": "#B9BEE9",
        "copied": "#86CEC8",
        "error": "#F0ADB5",
    }.items():
        state_image = icon(256, color)
        if state != "ready":
            state_draw = ImageDraw.Draw(state_image)
            badge_color = {"recording": CORAL, "copied": "#16725D", "error": "#B3383A"}.get(
                state, TEAL
            )
            state_draw.ellipse((165, 165, 250, 250), fill=badge_color, outline=PINE, width=7)
            if state == "recording":
                state_draw.ellipse((193, 193, 222, 222), fill=PINE)
            elif state == "copied":
                state_draw.line([(186, 207), (201, 220), (228, 191)], fill="#FFFFFF", width=8)
            elif state == "error":
                state_draw.line([(192, 192), (223, 223)], fill="#FFFFFF", width=8)
                state_draw.line([(192, 223), (223, 192)], fill="#FFFFFF", width=8)
            else:
                for x in (187, 202, 217):
                    state_draw.ellipse((x, 203, x + 9, 212), fill="#FFFFFF")
        state_image.save(ROOT / f"voiceui-{state}.png")
    wordmark = Image.new("RGB", (740, 256), PINE)
    wordmark_mark = mark.resize((196, 196), Image.Resampling.LANCZOS)
    wordmark.paste(wordmark_mark, (30, 30), wordmark_mark)
    draw = ImageDraw.Draw(wordmark)
    text(draw, (250, 55), "Voice to Me", 72, "#FFFFFF", True)
    text(draw, (254, 150), "YOUR VOICE. YOUR WORDS.", 22, "#CBDEDD")
    wordmark.save(ROOT / "voiceui-wordmark.png")

    dictation_preview().save(ROOT / "voiceui-preview.png")
    settings_preview().save(ROOT / "settings-preview.png")
    history_preview().save(ROOT / "history-preview.png")


if __name__ == "__main__":
    build()
