"""Export the sequential EDL prototype to a local Shotcut MLT XML project.

MLT integer frame out-points are inclusive: EDL [in, out) -> MLT in, out-1.
This module does not render, probe media, or establish Shotcut GUI compatibility.
"""

import argparse
from copy import deepcopy
import hashlib
from html import escape
import json
from math import gcd
from pathlib import Path
import xml.etree.ElementTree as ET

from clipforge.editing.edl import _local_ref, validate_edl
from clipforge.editing.subtitles import (
    _fit_font, subtitle_layout, validate_subtitles, validate_font_name,
)


def resolve_source_ref(ref, source_root, require_media=True):
    """Resolve local references against an explicit root; never allow a URL/UNC."""
    _local_ref(ref)
    root = Path(source_root)
    _local_ref(str(root))
    if not root.is_absolute():
        raise ValueError("source_root must be an absolute local directory")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("source_root must exist as a directory")
    source = Path(ref)
    # A drive-rooted Windows path cannot be interpreted as relative on POSIX.
    if len(ref) >= 3 and ref[1] == ":" and not source.is_absolute():
        raise ValueError("source absolute path is not supported on this operating system")
    relative = not source.is_absolute()
    resolved = (root / source if relative else source).resolve()
    if relative and not resolved.is_relative_to(root):
        raise ValueError("relative source ref escapes source_root")
    if require_media and not resolved.is_file():
        raise ValueError(f"source media file does not exist: {resolved}")
    return resolved.as_posix()


def _properties(element, values):
    for name, value in values.items():
        ET.SubElement(element, "property", {"name": name}).text = str(value)


def _text_filter(tractor, *, identifier, start, end, text, font_name, size, layout, brand=False):
    """Editable literal qtext with independent Shotcut Text: Simple style.

    Use qtext directly: dynamictext would expand #timecode#/metadata in ASR text.
    At the validated 96 DPI, multiples of four pixels map to integer point sizes
    that survive the control's point/pixel conversion. Rendering uses the font's
    line spacing; the geometry only reserves conservative space for each line.
    """
    if len(text.encode('utf-16-le')) // 2 > 256:
        raise ValueError(f'MLT editable text exceeds Shotcut\'s 256 UTF-16-unit limit: {identifier}')
    if size < 4:
        raise ValueError(f'MLT editable text requires a font size of at least 4 pixels: {identifier}')
    size = size // 4 * 4
    line_reserve = (size * 3 + 1) // 2
    lines = 1 if brand else layout['max_lines']
    height = min(layout['height'] - 2 * layout['margin_y'],
                 line_reserve * lines + 8)
    x = layout['margin_x']
    y = layout['margin_y'] if brand else layout['height'] - layout['margin_y'] - height
    width = layout['width'] - 2 * x
    align = 'right' if brand else 'center'
    effect = ET.SubElement(tractor, 'filter', {'id': identifier, 'in': str(start), 'out': str(end - 1)})
    _properties(effect, {'mlt_service': 'qtext', 'shotcut:filter': 'dynamicText',
                         'shotcut:name': 'AI辅助剪辑' if brand else 'ClipForge 字幕',
                         'argument': text, 'geometry': f'{x} {y} {width} {height}',
                         'family': font_name, 'size': size, 'weight': '400', 'style': 'normal',
                         'fgcolour': '0xffffffff', 'olcolour': '0x00000000',
                         'outline': '0', 'pad': '0', 'typewriter': '0',
                         'shotcut:usePointSize': '1', 'shotcut:pointSize': size * 3 // 4,
                         # MLT treats filter out=0 as unspecified. A first-frame
                         # one-frame event needs this guard to stop at frame 1.
                         'opacity': '0=1;1=0' if start == 0 and end == 1 else '1',
                         'bgcolour': '0x00000080', 'halign': align, 'valign': 'top' if brand else 'bottom',
                         'clipforge:cue_id': identifier})


def _text_filter_v1(tractor, *, identifier, start, end, text, font_name, size, layout, brand=False):
    """Reproduce the original rich-text export bytes for saved version 1 files."""
    line_height = (size * 3 + 1) // 2
    height = min(layout['height'] - 2 * layout['margin_y'],
                 line_height * (text.count('\n') + 1) + 8)
    x = layout['margin_x']
    y = layout['margin_y'] if brand else layout['height'] - layout['margin_y'] - height
    width = layout['width'] - 2 * x
    align = 'right' if brand else 'center'
    literal = escape(text, quote=True).replace('\n', '<br/>')
    html = ('<html><head><meta name="qrichtext" content="1" /></head><body>'
            f'<p align="{align}" style="margin:0px;line-height:{line_height}px;">'
            f'<font face="{escape(font_name, quote=True)}">'
            f'<span style="font-size:{size}px;color:#ffffff;">{literal}</span>'
            '</font></p></body></html>')
    effect = ET.SubElement(tractor, 'filter', {'id': identifier, 'in': str(start), 'out': str(end - 1)})
    _properties(effect, {'mlt_service': 'qtext', 'shotcut:filter': 'richText',
                         'shotcut:name': 'AI辅助剪辑' if brand else 'ClipForge 字幕',
                         'argument': text, 'html': html, 'geometry': f'{x} {y} {width} {height}',
                         'overflow-y': '0', 'pixel_ratio': '1',
                         'opacity': '0=1;1=0' if start == 0 and end == 1 else '1',
                         'bgcolour': '0x00000080', 'halign': align, 'valign': 'top',
                         'clipforge:cue_id': identifier})


def _subtitle_filters(tractor, plan, font_name, text_style_version):
    layout = subtitle_layout(plan)
    # Reserve the policy's full line count even before an editor reflows a cue.
    layout['max_lines'] = plan['policy']['max_lines']
    text_filter = _text_filter_v1 if text_style_version == 1 else _text_filter
    for index, cue in enumerate(plan['cues']):
        size = (_fit_font(cue['text'], layout['subtitle_size'], layout['available_width'])
                if layout['portrait'] else layout['subtitle_size'])
        text_filter(tractor, identifier=f'subtitle{index}', start=cue['timeline_in_frame'],
                     end=cue['timeline_out_frame'], text=cue['text'], font_name=font_name,
                     size=size, layout=layout)
    text_filter(tractor, identifier='clipforge_brand', start=0, end=plan['duration_frames'],
                 text=plan['brand']['text'], font_name=font_name, size=layout['brand_size'],
                 layout=layout, brand=True)


def export_mlt(edl, source_root, require_media=True, *, subtitle_plan=None, analyses=None,
               font_name="Noto Sans CJK SC", text_style_version=2):
    """Return UTF-8-compatible XML; source metadata is declared, not re-probed."""
    if type(text_style_version) is not int or text_style_version not in (1, 2):
        raise ValueError('MLT text_style_version must be integer 1 or 2')
    validate_edl(edl)
    audio_sources = {source['id'] for source in edl['sources'] if source['audio_stream_index'] is not None}
    if any(clip['source_id'] in audio_sources and clip['source_out_frame'] - clip['source_in_frame'] == 1
           for clip in edl['clips']):
        raise ValueError('MLT cannot reliably preserve audio in a single-frame clip; use the FFmpeg EDL MP4 renderer')
    if subtitle_plan is not None:
        validate_subtitles(subtitle_plan, edl, analyses)
        validate_font_name(font_name)
    elif analyses is not None:
        raise ValueError('analyses require a subtitle plan')
    resources = {source["id"]: resolve_source_ref(source["ref"], source_root, require_media)
                 for source in edl["sources"]}
    if require_media:
        for source in edl["sources"]:
            with Path(resources[source["id"]]).open("rb") as media:
                digest = hashlib.file_digest(media, "sha256").hexdigest()
            if digest != source["sha256"]:
                raise ValueError(f"source media sha256 does not match EDL: {source['id']}")
    duration = edl["duration_frames"]
    profile = edl["profile"]
    title = f"Shotcut project (ClipForge EDL {edl['schema_version']})"
    root = ET.Element("mlt", {"LC_NUMERIC": "C", "producer": "main_bin", "title": title})
    divisor = gcd(profile["width"], profile["height"])
    ET.SubElement(root, "profile", {
        "description": "ClipForge explicit CFR profile", "width": str(profile["width"]),
        "height": str(profile["height"]), "progressive": "1",
        "sample_aspect_num": "1", "sample_aspect_den": "1",
        "display_aspect_num": str(profile["width"] // divisor),
        "display_aspect_den": str(profile["height"] // divisor),
        "frame_rate_num": str(profile["fps"]["num"]),
        "frame_rate_den": str(profile["fps"]["den"]), "colorspace": "709"})
    # Keep this bin before the render graph as in the inspected Shotcut sample.
    main_bin = ET.SubElement(root, "playlist", {"id": "main_bin"})
    _properties(main_bin, {"xml_retain": "1", "shotcut:colorTransfer": ""})
    black = ET.SubElement(root, "producer", {"id": "black", "in": "0", "out": str(duration - 1)})
    _properties(black, {"length": duration, "eof": "pause", "resource": "0", "aspect_ratio": "1",
                        "mlt_service": "color", "mlt_image_format": "rgba", "set.test_audio": "0"})
    background = ET.SubElement(root, "playlist", {"id": "background"})
    ET.SubElement(background, "entry", {"producer": "black", "in": "0", "out": str(duration - 1)})
    chain_ids = {}
    clip_chains = {}
    crops = {item['source_id']: item for item in edl.get('framing', {}).get('sources', [])}
    for index, source in enumerate(edl["sources"]):
        chain_id = f"chain{index}"
        chain_ids[source["id"]] = chain_id
        chain = ET.SubElement(root, "chain", {"id": chain_id, "in": "0",
                                              "out": str(source["total_frames"] - 1)})
        _properties(chain, {"length": source["total_frames"], "eof": "pause",
                            "resource": resources[source["id"]], "mlt_service": "avformat",
                            "video_index": source["video_stream_index"],
                            "audio_index": -1 if source["audio_stream_index"] is None else source["audio_stream_index"],
                            "seekable": "1", "ignore_points": "0", "shotcut:caption": Path(resources[source["id"]]).name})
        if source['id'] in crops:
            crop = crops[source['id']]
            effect = ET.SubElement(chain, 'filter', {'id': f'crop{index}', 'in': '0',
                                                    'out': str(source['total_frames'] - 1)})
            # The loader's normalizing crop performs the operation before resize.
            # This parameter setter must be inactive, with native-pixel units.
            _properties(effect, {'mlt_service': 'crop', 'active': '0', 'center': '0', 'use_profile': '0',
                                 'left': crop['x'], 'top': crop['y'],
                                 'right': source['width'] - crop['x'] - crop['width'],
                                 'bottom': source['height'] - crop['y'] - crop['height'],
                                 'clipforge:framing': 'center_crop'})
        if source['id'] in audio_sources:
            # A shared avformat decoder can lose audio at backward/repeated seeks.
            # Independent contexts reference the same bytes, without media copies.
            members = [clip for clip in edl['clips'] if clip['source_id'] == source['id']]
            for number, clip in enumerate(members):
                if number == 0:
                    clip_chains[clip['id']] = chain_id
                    continue
                clone_id = f'{chain_id}_clip{number}'
                clone = deepcopy(chain)
                clone.set('id', clone_id)
                for effect in clone.findall('filter'):
                    effect.set('id', f"{effect.get('id')}_clip{number}")
                root.append(clone)
                clip_chains[clip['id']] = clone_id
    video = ET.SubElement(root, "playlist", {"id": "playlist0"})
    _properties(video, {"shotcut:video": "1", "shotcut:name": "V1"})
    for clip in edl["clips"]:
        ET.SubElement(video, "entry", {"producer": clip_chains.get(clip['id'], chain_ids[clip["source_id"]]),
                                        "in": str(clip["source_in_frame"]),
                                        "out": str(clip["source_out_frame"] - 1)})
    tractor = ET.SubElement(root, "tractor", {"id": "tractor0", "in": "0", "out": str(duration - 1),
                                            "title": title})
    _properties(tractor, {"shotcut": "1", "shotcut:projectAudioChannels": "2",
                          "shotcut:projectFolder": "0", "shotcut:processingMode": "Native8Cpu",
                          "shotcut:colorTransfer": ""})
    ET.SubElement(tractor, "track", {"producer": "background"})
    ET.SubElement(tractor, "track", {"producer": "playlist0"})
    mix = ET.SubElement(tractor, "transition", {"id": "transition0"})
    _properties(mix, {"a_track": "0", "b_track": "1", "mlt_service": "mix",
                      "always_active": "1", "sum": "1"})
    if subtitle_plan is not None:
        _subtitle_filters(tractor, subtitle_plan, font_name, text_style_version)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("edl", type=Path)
    parser.add_argument("--source-root", required=True, type=Path,
                        help="absolute local directory for relative source references")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument('--subtitles', type=Path, help='saved canonical subtitles.json, not an SRT/ASS file')
    parser.add_argument('--analysis', action='append', default=[], metavar='SOURCE_ID=PATH',
                        help='original analysis JSON for every source; repeat for multiple sources')
    parser.add_argument('--font-name', default='Noto Sans CJK SC', help='font family installed in Shotcut')
    args = parser.parse_args()
    try:
        data = json.loads(args.edl.read_text(encoding="utf-8-sig"))
        analyses = None
        if args.subtitles:
            analyses = {}
            for argument in args.analysis:
                identifier, separator, filename = argument.partition('=')
                if not separator or not identifier or not filename or identifier in analyses:
                    raise ValueError('--analysis requires unique SOURCE_ID=PATH pairs')
                analyses[identifier] = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
            plan = json.loads(args.subtitles.read_text(encoding='utf-8-sig'))
        else:
            if args.analysis:
                raise ValueError('--analysis requires --subtitles')
            plan = None
        content = export_mlt(data, args.source_root, subtitle_plan=plan, analyses=analyses,
                             font_name=args.font_name)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(content)
    except (OSError, ValueError) as error:
        parser.exit(1, f"INVALID: {error}\n")
    print(f"MLT: {args.output} ({len(data['clips'])} clips; {data['duration_frames']} frames)")


if __name__ == "__main__":
    main()
