"""Project-specific export identification; not a legal-compliance certification.

These tags describe editing provenance, not AI generation. They are an internal
ClipForge contract, not an implementation of any external metadata standard.
"""

MARKING_VERSION = "1"
VISIBLE_LABEL = "AI辅助剪辑"
EXPORT_TAGS = {
    "clipforge_editing": "AI-assisted-editing",
    "clipforge_tool": "ClipForge",
    "clipforge_marking_version": MARKING_VERSION,
}


def metadata_arguments():
    """Return separate argv values; callers also need use_metadata_tags for MP4."""
    return [part for key, value in EXPORT_TAGS.items() for part in ("-metadata", f"{key}={value}")]


def inspect_metadata(tags):
    """Check exact reserved values without claiming a visible label is present."""
    if not isinstance(tags, dict):
        tags = {}
    missing = [key for key in EXPORT_TAGS if key not in tags]
    mismatched = [key for key, value in EXPORT_TAGS.items() if key in tags and tags[key] != value]
    return {
        "contract": "clipforge-export-marking-v1",
        "status": "pass" if not missing and not mismatched else "fail",
        "expected": dict(EXPORT_TAGS),
        "observed": {key: tags[key] for key in EXPORT_TAGS if key in tags},
        "missing": missing,
        "mismatched": mismatched,
    }


def require_metadata(tags):
    """New exports fail closed; historical saved exports are not reclassified."""
    result = inspect_metadata(tags)
    if result["status"] != "pass":
        raise ValueError("rendered MP4 lacks valid ClipForge AI-assisted-editing metadata")
    return result
