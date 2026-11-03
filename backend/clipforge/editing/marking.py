"""Mandatory visible labels for newly rendered video, with a verified font snapshot."""
from contextlib import ExitStack, contextmanager
from fractions import Fraction
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time

from clipforge.compliance import VISIBLE_LABEL

POLICY = "clipforge-visible-mark-v1"
FILTER = "ass=filename=subtitles.ass:fontsdir=fonts"


def marking_layout(width, height):
    if any(type(x) is not int or x < 2 for x in (width, height)):
        raise ValueError("visible marking requires positive video dimensions")
    margin_x, margin_y = max(2, round(width * .05)), max(2, round(height * .06))
    available = width - 2 * margin_x - 4
    size = min(max(8, round(height * .027)), available * 20 // (6 * 23))
    if size < 1:
        raise ValueError("video is too small to contain the visible editing label")
    layout = {"width": width, "height": height, "margin_x": margin_x, "margin_y": margin_y,
              "brand_size": size, "available_width": available}
    validate_marking_layout(layout)
    return layout


def validate_marking_layout(layout):
    """Require room for the complete label, including outline and line height."""
    # Match the existing conservative 1.15-em-per-glyph width estimate. Reserve
    # a two-em line box and one-pixel outline on each side, inside both margins.
    if (layout["brand_size"] * len(VISIBLE_LABEL) * 23 > layout["available_width"] * 20
            or 2 * layout["margin_y"] + 2 * layout["brand_size"] + 2 > layout["height"]):
        raise ValueError("video is too small to contain the complete visible editing label")


def watermark_ass(profile, duration_frames, font_name):
    from clipforge.editing.subtitles import _timestamp, validate_font_name
    from clipforge.editing.edl import frame_rate
    validate_font_name(font_name)
    if type(duration_frames) is not int or duration_frames < 1:
        raise ValueError("visible marking requires a nonempty frame range")
    layout = marking_layout(profile["width"], profile["height"])
    end = Fraction(duration_frames * 100, 1) / frame_rate(profile["fps"])
    finish = _timestamp(-(-end.numerator // end.denominator), 100)
    return ("[Script Info]\nTitle: ClipForge visible editing label\nScriptType: v4.00+\n"
            f"PlayResX: {layout['width']}\nPlayResY: {layout['height']}\n"
            "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Brand,{font_name},{layout['brand_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
            f"0,0,0,0,100,100,0,0,1,1,0,9,{layout['margin_x']},{layout['margin_x']},{layout['margin_y']},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 1,0:00:00.00,{finish},Brand,ClipForge,0,0,0,,{VISIBLE_LABEL}\n")


def prepare_watermark(stack, profile, duration_frames, ffmpeg, deadline, font_file=None, font_name=None):
    from clipforge.editing import render
    family = font_name if font_name is not None else os.environ.get("SUBTITLE_FONT_NAME")
    if not family:
        raise ValueError("visible marking requires SUBTITLE_FONT_FILE and SUBTITLE_FONT_NAME")
    ass = watermark_ass(profile, duration_frames, family)
    state = render._ass_state(stack, ass, font_file, family, ffmpeg, deadline)
    state["report"].update(policy=POLICY, text=VISIBLE_LABEL, duration_frames=duration_frames,
                            fps=dict(profile["fps"]), layout=marking_layout(profile["width"], profile["height"]))
    return state


def font_fingerprint():
    """Cache identity changes with configured font bytes, not merely its path."""
    from clipforge.editing import render
    family, configured = os.environ.get("SUBTITLE_FONT_NAME"), os.environ.get("SUBTITLE_FONT_FILE")
    if not family or not configured:
        raise ValueError("visible marking requires SUBTITLE_FONT_FILE and SUBTITLE_FONT_NAME")
    with render.open_media_source(Path(configured).expanduser()) as incoming:
        data = incoming.read(render.MAX_FONT_BYTES + 1)
    if len(data) > render.MAX_FONT_BYTES:
        raise ValueError("visible marking font exceeds bounded size")
    render._font_info(data, family, {ord(x) for x in VISIBLE_LABEL})
    return {"policy": POLICY, "text": VISIBLE_LABEL, "font_name": family,
            "font_sha256": hashlib.sha256(data).hexdigest()}


def validate_render_marking(record, expected_font, frames):
    """Bind cached generated-media evidence to this request's font and range."""
    from clipforge.compliance import require_metadata
    try:
        visible = record["visible"]
        if (any(visible[key] != value for key, value in expected_font.items())
                or visible["all_output_frames"] is not True
                or visible["verified_font_selection"] is not True
                or type(visible["duration_frames"]) is not int or visible["duration_frames"] != frames
                or visible["fps"] != {"num": 30, "den": 1}):
            raise ValueError("visible marking font or frame range differs from request")
        layout = visible["layout"]
        profile = {"width": layout["width"], "height": layout["height"], "fps": visible["fps"]}
        if layout != marking_layout(profile["width"], profile["height"]):
            raise ValueError("visible marking layout differs from policy")
        ass = watermark_ass(profile, frames, visible["font_name"])
        if hashlib.sha256(ass.encode("utf-8")).hexdigest() != visible["ass_sha256"]:
            raise ValueError("visible marking ASS identity differs")
        require_metadata(record["metadata"]["observed"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("cached video lacks matching visible marking evidence") from error


class MarkingSession:
    filter = FILTER

    def __init__(self, state, folder, executable, deadline, font_handle, ass_handle):
        self.state, self.cwd, self.ffmpeg, self.deadline = state, folder, executable, deadline
        self.report = dict(state["report"])
        self.font_handle, self.ass_handle = font_handle, ass_handle

    def run(self, command, timeout=None):
        from clipforge.editing import render
        command = list(command)
        if self.filter not in " ".join(command):
            raise ValueError("visible marking filter is missing from render command")
        command[0] = self.ffmpeg
        if "-loglevel" in command:
            command[command.index("-loglevel") + 1] = "level+verbose"
        else:
            command[1:1] = ["-loglevel", "level+verbose"]
        if "-nostats" not in command:
            command.insert(1, "-nostats")
        budget = render._tool_budget(self.deadline)
        if timeout is not None:
            budget = min(budget, timeout)
        result = render._subtitle_tool(command, budget, cwd=self.cwd)
        render._validate_subtitle_logs(result[1], self.state)
        for handle, key in ((self.state["handle"], "font_sha256"),
                            (self.font_handle, "font_sha256"), (self.ass_handle, "ass_sha256")):
            if render._hash_handle(handle, self.deadline) != self.report[key]:
                raise ValueError("visible marking input changed during rendering")
        render._check_identity(self.state["path"], self.state["identity"])
        self.report.update(all_output_frames=True, verified_font_selection=True)
        return result


@contextmanager
def watermark_session(profile, duration_frames, ffmpeg, timeout=600, *, font_file=None, font_name=None):
    from clipforge.editing import render
    render._timeout(timeout)
    deadline = time.monotonic() + timeout
    executable = shutil.which(str(ffmpeg)) or str(Path(ffmpeg).resolve())
    with ExitStack() as stack:
        state = prepare_watermark(stack, profile, duration_frames, executable, deadline, font_file, font_name)
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="clipforge-mark-")))
        (folder / "fonts").mkdir()
        font = folder / "fonts" / state["filename"]
        font.write_bytes(state["data"])
        ass = folder / "subtitles.ass"
        ass.write_bytes(state["ass"].encode("utf-8"))
        font_handle = stack.enter_context(render.open_media_source(font))
        ass_handle = stack.enter_context(render.open_media_source(ass))
        yield MarkingSession(state, folder, executable, deadline, font_handle, ass_handle)
