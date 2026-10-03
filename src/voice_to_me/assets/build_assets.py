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


def settings_preview() -> Image.Image:
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

    preview = Image.new("RGB", (1500, 1080), PINE)
    draw = ImageDraw.Draw(preview)
    preview_mark = mark.resize((70, 70), Image.Resampling.LANCZOS)
    preview.paste(preview_mark, (54, 33), preview_mark)
    text(draw, (142, 38), "Voice to Me", 41, "#FFFFFF", True)
    text(draw, (1300, 58), "WINDOWS", 20, "#CBDEDD")

    # A local, representative UI illustration rather than a desktop screenshot.
    draw.rounded_rectangle((53, 132, 744, 988), radius=23, fill="#08272C")
    draw.rounded_rectangle((47, 126, 738, 980), radius=20, fill=CANVAS)
    draw.rounded_rectangle((47, 126, 738, 315), radius=20, fill="#0B3037")
    draw.rectangle((47, 275, 738, 315), fill="#0B3037")
    draw.ellipse((690, 145, 710, 165), fill="#527177")
    window_mark = mark.resize((71, 71), Image.Resampling.LANCZOS)
    preview.paste(window_mark, (81, 188), window_mark)
    text(draw, (170, 184), "Voice to Me", 35, "#FFFFFF", True)
    text(draw, (174, 237), "YOUR VOICE. YOUR WORDS.", 15, "#CBDEDD")
    text(draw, (82, 340), "Write a message with your voice", 28, INK, True)
    text(draw, (82, 388), "Record. Refine. Paste anywhere when you're ready.", 19, MUTED)
    draw.rounded_rectangle(
        (82, 445, 701, 612), radius=15, fill="#FFFFFF", outline="#D5E2DF", width=2
    )
    draw.ellipse((108, 477, 130, 499), fill=CORAL)
    text(draw, (154, 465), "Recording your voice", 29, INK, True)
    text(draw, (108, 526), "Speak at your own pace. Press again", 20, MUTED)
    text(draw, (108, 556), "to stop recording.", 20, MUTED)
    pill(draw, (82, 638, 701, 718), "Finish recording", fill=CORAL, color=PINE, size=26)
    text(draw, (128, 733), "Toggle: Ctrl + Alt + Space  ·  Alt + R in this window", 17, MUTED)
    pill(draw, (82, 774, 380, 829), "Cancel", size=19)
    pill(draw, (401, 774, 701, 829), "Try again", color="#91A4A0", size=19)
    draw.line((82, 853, 701, 853), fill="#D5E2DF", width=2)
    pill(draw, (82, 872, 701, 925), "Settings", size=18)
    text(draw, (82, 939), "You choose where to paste and send.", 17, MUTED)

    text(draw, (800, 191), "Your voice,", 52, "#FFFFFF", True)
    text(draw, (800, 253), "ready to paste.", 52, "#FFFFFF", True)
    text(draw, (803, 337), "Speak naturally. Keep your own tone.", 22, "#CBDEDD")
    text(draw, (803, 373), "Paste anywhere when you're ready.", 22, "#CBDEDD")

    for y, number, label, detail, color in (
        (441, "01", "Ready to record", "A shortcut or a large button.", "#167C80"),
        (
            571,
            "02",
            "Loading the speech model",
            "Local model. Automatic GPU acceleration.",
            "#167C80",
        ),
        (
            701,
            "03",
            "Transcribing & refining",
            "Local Whisper, then your writing profile.",
            "#167C80",
        ),
        (
            831,
            "✓",
            "Copied to your clipboard",
            "Only the final text. Paste it yourself.",
            "#16725D",
        ),
    ):
        draw.rounded_rectangle((799, y, 1440, y + 112), radius=16, fill="#EAF3F0")
        draw.rounded_rectangle((820, y + 26, 885, y + 91), radius=15, fill=color)
        if number == "✓":
            draw.line([(834, y + 59), (846, y + 71), (870, y + 45)], fill="#FFFFFF", width=5)
        else:
            text(draw, (834, y + 41), number, 27, "#FFFFFF", True)
        text(draw, (906, y + 27), label, 25, INK, True)
        text(draw, (907, y + 71), detail, 18, MUTED)

    text(
        draw,
        (56, 1022),
        "Original local assets · Segoe UI · Accessible toggle controls",
        18,
        "#CBDEDD",
    )
    text(draw, (970, 1022), "Interface illustration / no device capture", 17, "#CBDEDD")
    preview.save(ROOT / "voiceui-preview.png")
    settings_preview().save(ROOT / "settings-preview.png")


if __name__ == "__main__":
    build()
