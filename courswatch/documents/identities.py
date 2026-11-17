"""Stable attachment-to-catalog bindings; preserve ambiguous legacy histories."""
from __future__ import annotations

import hashlib
import json
import re

from courswatch.documents.versions import (PdfArchiveError, _atomic_json, _ensure_child_dir,
                                 _load_catalog, _safe_catalog_key, _safe_course_dir)


def _sensor_key(value, module_key):
    _safe_catalog_key(value)
    prefix = module_key + ":attachment-"
    if not value.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{12}", value[len(prefix):]):
        raise PdfArchiveError("Identité de pièce jointe incompatible avec le module")
    return value


def _binding(path, course_id, module_key):
    if path.is_symlink():
        raise PdfArchiveError("Lien symbolique de correspondance interdit")
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PdfArchiveError("Correspondance de pièces jointes illisible") from error
    if (not isinstance(value, dict) or set(value) != {"version", "course", "key", "primary"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["course"] != course_id or value["key"] != module_key):
        raise PdfArchiveError("Correspondance de pièces jointes incompatible")
    _sensor_key(value["primary"], module_key)
    return value


def attachment_archive_keys(store, course_id, module_key, pdfs):
    """Return ``(sensor -> archive key, warning)`` under the collector lock.

    A module's legacy catalog is assigned only to its durably bound attachment.
    If legacy bytes cannot identify exactly one current attachment, all current
    attachments use their own sensor catalogs and the old catalog stays frozen.
    No PDF or existing catalog is rewritten by this helper.
    """
    filename = _safe_catalog_key(module_key)
    if not isinstance(pdfs, dict):
        raise PdfArchiveError("Lot de pièces jointes invalide")
    for sensor, result in pdfs.items():
        _sensor_key(sensor, module_key)
        if (not isinstance(result, dict)
                or not isinstance(result.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", result["sha256"])):
            raise PdfArchiveError("SHA256 de pièce jointe invalide")
        if "bytes" in result and (not isinstance(result["bytes"], bytes)
                                  or hashlib.sha256(result["bytes"]).hexdigest() != result["sha256"]):
            raise PdfArchiveError("SHA256 incompatible avec les octets de la pièce jointe")
    _, course = _safe_course_dir(store, course_id)
    bindings = _ensure_child_dir(course, "bindings", course)
    path = bindings / filename
    if path.resolve(strict=False).parent != bindings:
        raise PdfArchiveError("Chemin de correspondance invalide")
    existing = _binding(path, course_id, module_key)
    catalog_dir = course / "catalog"
    if catalog_dir.is_symlink():
        raise PdfArchiveError("Lien symbolique de catalogue interdit")
    if catalog_dir.exists() and not catalog_dir.is_dir():
        raise PdfArchiveError("Dossier de catalogues invalide")
    catalog_path = catalog_dir / filename
    if catalog_path.resolve(strict=False).parent != catalog_dir.resolve(strict=False):
        raise PdfArchiveError("Chemin de catalogue invalide")
    if catalog_path.is_symlink():
        raise PdfArchiveError("Lien symbolique de catalogue interdit")
    if existing is not None:
        primary = existing["primary"]
        return {key: module_key if key == primary else key for key in sorted(pdfs)}, None
    legacy = _load_catalog(catalog_path, course_id, module_key)
    if legacy is not None:
        matches = [sensor for sensor, result in pdfs.items() if result["sha256"] == legacy["current_sha"]]
        if len(matches) != 1:
            return {key: key for key in sorted(pdfs)}, "legacy_unresolved"
        primary = matches[0]
    elif pdfs:
        primary = min(pdfs)
    else:
        return {}, None
    value = {"version": 1, "course": course_id, "key": module_key, "primary": primary}
    _atomic_json(path, value)
    return {key: module_key if key == primary else key for key in sorted(pdfs)}, None
