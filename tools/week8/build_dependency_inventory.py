"""Reproducible, evidence-labelled dependency inventory; not a license clearance.

Run with the project's Python environment to capture installed package metadata,
then regenerate offline from that snapshot. Uses packaging to parse declarations;
does not import the project dependencies being inventoried.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


MANIFESTS = (
    "backend/requirements.txt",
    "backend/requirements-worker.txt",
    "backend/requirements-dev.txt",
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def manifest_requirements(root):
    """Read each physical manifest once; retain includes rather than count twice."""
    records, includes = [], []
    for relative in MANIFESTS:
        for number, original in enumerate((root / relative).read_text(encoding="utf-8-sig").splitlines(), 1):
            line = original.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-r "):
                includes.append({"source": relative, "line": number, "include": line[3:].strip()})
                continue
            req = Requirement(line)
            pins = [s.version for s in req.specifier if s.operator == "==" and "*" not in s.version]
            if len(pins) != 1 or len(list(req.specifier)) != 1 or req.url:
                raise ValueError(f"Expected one exact package pin: {relative}:{number}")
            records.append({"source": relative, "line": number, "requirement": line,
                            "name": canonicalize_name(req.name), "version": pins[0],
                            "extras": sorted(req.extras), "marker": str(req.marker) if req.marker else None})
    return records, includes


def active(req, environment, extras=()):
    return not req.marker or any(req.marker.evaluate({**environment, "extra": extra}) for extra in ("", *extras))


def license_evidence(dist):
    fields = dist.metadata
    raw = fields.get("License") or ""
    expression = fields.get("License-Expression") or None
    classifiers = sorted(x for x in fields.get_all("Classifier", []) if x.startswith("License ::"))
    # Preserve exact metadata labels without turning broad classifiers into SPDX IDs.
    label = expression or (raw.strip() if "\n" not in raw and len(raw) <= 160 else None)
    if not label and classifiers:
        label = " | ".join(classifiers)
    return {"declared_label": label, "license_expression": expression,
            "license_classifiers": classifiers, "legacy_license_field_sha256": sha256(raw.encode()) if raw else None,
            "legacy_license_field_characters": len(raw),
            "license_files_declared": sorted(fields.get_all("License-File", [])),
            "source": "installed distribution METADATA; no legal interpretation or bundled-code audit"}


def capture_metadata(records):
    environment = default_environment()
    queue = deque()
    for item in records:
        req = Requirement(item["requirement"])
        if active(req, environment):
            queue.append((canonicalize_name(req.name), tuple(sorted(req.extras))))
    seen, packages, edges, missing = set(), {}, [], set()
    while queue:
        name, extras = queue.popleft()
        key = (name, extras)
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.add(name)
            continue
        if name not in packages:
            packages[name] = {"name": canonicalize_name(dist.metadata["Name"]), "version": dist.version,
                              "license": license_evidence(dist),
                              "project_urls": sorted(dist.metadata.get_all("Project-URL", [])),
                              "requires_dist": sorted(dist.requires or [])}
        for raw in packages[name]["requires_dist"]:
            req = Requirement(raw)
            if not active(req, environment, extras):
                continue
            target = canonicalize_name(req.name)
            edge = {"from": name, "to": target, "requirement": raw, "enabled_by_extras": list(extras)}
            if edge not in edges:
                edges.append(edge)
            queue.append((target, tuple(sorted(req.extras))))
    return {"schema_version": "clipforge-installed-metadata-v1", "environment": environment,
            "collector_packaging_version": metadata.version("packaging"),
            "packages": packages, "missing_distributions": sorted(missing),
            "edges": sorted(edges, key=lambda x: (x["from"], x["to"], x["requirement"], x["enabled_by_extras"]))}


def license_signal(label):
    """Textual triage only. Composite expressions always need manual review."""
    if not label:
        return "missing-license-metadata"
    if re.search(r"\b(?:AND|OR|WITH)\b|\|", label):
        return "compound-or-multiple-labels-review"
    if re.search(r"(?:^|[^A-Z])(?:LGPL|MPL|EPL)(?:[^A-Z]|$)", label, re.I):
        return "contains-weak-copyleft-family-label-review"
    if re.search(r"(?:^|[^A-Z])(?:AGPL|GPL)(?:[^A-Z]|$)|General Public License", label, re.I):
        return "contains-copyleft-family-label-review"
    if re.search(r"SSPL|RSAL|BUSL|Business Source", label, re.I):
        return "contains-source-available-family-label-review"
    return "other-declared-label-review"


def python_components(records, snapshot):
    declared = {}
    for item in records:
        declared.setdefault((item["name"], item["version"]), []).append(item)
    result, covered = [], set()
    env = snapshot["environment"]
    for (name, version), locations in sorted(declared.items()):
        installed = snapshot["packages"].get(name)
        applicable = any(active(Requirement(x["requirement"]), env) for x in locations)
        match = bool(installed and installed["version"] == version)
        if match:
            covered.add(name)
        license_data = installed["license"] if match else {"declared_label": None, "source": "matching-version metadata unavailable"}
        result.append({"ecosystem": "pypi", "name": name, "version": version,
                       "role": "manifest-listed; direct-vs-transitive not inferred from a frozen requirements list",
                       "version_basis": "exact requirement pin", "declared_at": locations,
                       "applicable_to_captured_environment": applicable,
                       "installed_version_observed": installed["version"] if installed else None,
                       "metadata_matches_pinned_version": match, "license": license_data,
                       "license_review_signal": license_signal(license_data["declared_label"]),
                       "status": "metadata-only-not-cleared" if match else "matching-version-license-pending"})
    for name, installed in sorted(snapshot["packages"].items()):
        if name in covered:
            continue
        result.append({"ecosystem": "pypi", "name": name, "version": installed["version"],
                       "role": "observed-transitive-or-version-mismatch; reachable from active manifest requirements",
                       "version_basis": "installed environment only; not a repository lock",
                       "license": installed["license"],
                       "license_review_signal": license_signal(installed["license"]["declared_label"]),
                       "status": "metadata-only-not-cleared"})
    return result


def npm_components(lock):
    packages = lock["packages"]
    manifest = packages[""]
    declared = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {}),
                **manifest.get("optionalDependencies", {})}
    rows = []
    for location, value in sorted(packages.items()):
        if not location:
            continue
        name = value.get("name") or location.rsplit("node_modules/", 1)[-1]
        direct = location == "node_modules/" + name and name in declared
        label = value.get("license")
        rows.append({"ecosystem": "npm", "name": name, "version": value.get("version"),
                     "role": "direct" if direct else "transitive", "install_path": location,
                     "version_basis": "package-lock.json", "development_only": value.get("dev", False),
                     "optional": value.get("optional", False), "platform_os": value.get("os", []),
                     "platform_cpu": value.get("cpu", []), "resolved": value.get("resolved"),
                     "integrity": value.get("integrity"),
                     "license": {"declared_label": label, "source": "package-lock.json license field"},
                     "license_review_signal": license_signal(label),
                     "status": "metadata-only-not-cleared" if label else "license-pending"})
    return rows


def build(root, snapshot, baseline):
    records, includes = manifest_requirements(root)
    current_manifests = {p: sha256((root / p).read_bytes()) for p in MANIFESTS}
    if snapshot.get("manifest_sha256") != current_manifests:
        raise ValueError("Requirements changed since metadata capture; capture a fresh snapshot")
    lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8-sig"))
    components = python_components(records, snapshot) + npm_components(lock)
    inputs = list(MANIFESTS) + ["frontend/package-lock.json", "frontend/package.json", "backend/pyproject.toml",
                               "deploy/Dockerfile.api", "deploy/Dockerfile.worker", "compose.yaml",
                               "backend/clipforge/analysis/model.py"]
    return {"schema_version": "clipforge-dependency-inventory-v1", "status": "draft-inventory-not-license-clearance",
            "source_baseline_commit": baseline,
            "generator_sha256": sha256(Path(__file__).read_bytes()),
            "scope": {"npm": "all lockfile package locations including optional platform/dev entries; not proof of installation",
                      "python": "manifest pins plus reachable installed dependencies for captured markers/extras; not Linux/container SBOM",
                      "excluded": ["unrelated installed Python packages", "container OS packages and binaries", "model weights and fonts", "license text/notice and distribution obligations audit"]},
            "input_sha256": {p: sha256((root / p).read_bytes()) for p in inputs},
            "installed_snapshot_sha256": sha256((json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()),
            "python_environment": snapshot["environment"], "requirements_includes": includes,
            "python_dependency_edges": snapshot["edges"], "python_missing_distributions": snapshot["missing_distributions"],
            "summary": {"component_records": len(components), "by_ecosystem": dict(Counter(x["ecosystem"] for x in components)),
                        "by_review_signal": dict(Counter(x["license_review_signal"] for x in components)),
                        "by_status": dict(Counter(x["status"] for x in components))},
            "components": components,
            "manual_review_required": [
                "Inspect exact artifact license texts, copyright notices, bundled libraries and redistribution conditions.",
                "Generate a separate Linux container SBOM after image build, with immutable image digests and OS packages.",
                "Verify redis:7.4.11-alpine exact release terms; do not reuse an older Redis license assumption.",
                "Record FFmpeg/MLT/Shotcut actual version, build configuration and bundled-code license evidence.",
                "Verify fixed base/small model weights, Noto fonts and dataset permissions separately.",
                "Choose the project license explicitly with the project owner; this inventory does not grant a license.",
                "Review copied third-party code and create THIRD_PARTY_NOTICES where required."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--metadata-snapshot", type=Path, required=True)
    parser.add_argument("--capture-installed", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()
    records, _ = manifest_requirements(args.root)
    if args.capture_installed:
        snapshot = capture_metadata(records)
        snapshot["manifest_sha256"] = {p: sha256((args.root / p).read_bytes()) for p in MANIFESTS}
        dump(args.metadata_snapshot, snapshot)
    else:
        snapshot = json.loads(args.metadata_snapshot.read_text(encoding="utf-8"))
        if snapshot.get("schema_version") != "clipforge-installed-metadata-v1":
            raise ValueError("Unknown metadata snapshot schema")
    inventory = build(args.root, snapshot, args.baseline)
    dump(args.output, inventory)
    print(json.dumps(inventory["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
