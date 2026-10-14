"""Containment of canonical paths inside canonical roots (pure functions)."""

import posixpath

from muxroom.validation import is_within


def containing_root(canonical: str, roots: list[str]) -> str | None:
    """The most specific root containing `canonical`, or None."""
    canonical = posixpath.normpath(canonical)
    matches = [root for root in roots if is_within(canonical, root)]
    return max(matches, key=len) if matches else None


def is_contained(canonical: str, roots: list[str]) -> bool:
    return containing_root(canonical, roots) is not None


def describe_roots(roots: list[tuple[str, str | None]]) -> str:
    """Human-readable root list: configured value, plus where it resolves to."""
    if not roots:
        return "none configured"
    parts = []
    for configured, canonical in roots:
        if canonical is None:
            parts.append(f"{configured} (does not exist)")
        elif canonical != configured:
            parts.append(f"{configured} (→ {canonical})")
        else:
            parts.append(configured)
    return ", ".join(parts)


def parent_within(canonical: str, roots: list[str]) -> str | None:
    """Parent directory for navigation, or None if it would leave all roots."""
    if canonical == "/" or not is_contained(canonical, roots):
        return None
    parent = posixpath.dirname(canonical)
    return parent if is_contained(parent, roots) else None
