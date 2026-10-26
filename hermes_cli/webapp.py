"""Build and launch plumbing for the browser-hosted Desktop workspace.

The browser surface shares Hermes' hardened dashboard server. This module owns
only the separate renderer artifact and the one-way handoff into that server;
it never creates a second HTTP/WebSocket stack. The renderer is compiled by the
shared product builders (``apps/desktop/scripts/build-webapp.mjs``), whose
receipt inside ``dist-webapp`` decides freshness.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
import subprocess

# Serialize renderer publication with Dashboard builds in this checkout.
_LOCK_NAME = ".web_ui_build.lock"
_LOCK_WAIT_SECONDS = 30 * 60
# Written into the bundle when `hermes update` could not rebuild it, so the gateways serving it report
# the Webapp unavailable instead of serving it against the updated backend. Publishing a build
# replaces the whole folder, and the receipt's output hash covers this file, so a marked bundle is
# never reused as current.
STALE_MARKER = ".hermes-stale"


class WebappBuildError(RuntimeError):
    """The browser-hosted Desktop renderer could not be prepared."""


def webapp_dist_dir(project_root: Path) -> Path:
    """Where this install's Webapp bundle lives. ``HERMES_WEBAPP_DIST`` overrides the checkout
    location for deployments that cannot build into the install root (Nix: the store is
    read-only and carries no ``apps/`` tree) and instead point at a bundle built beside the
    backend, which therefore matches it. Both the ``hermes webapp`` build path and the
    gateway's mount resolve through this one function."""
    override = os.environ.get("HERMES_WEBAPP_DIST")
    if override:
        return Path(override)
    return project_root / "apps" / "desktop" / "dist-webapp"


def _prebuilt_dist() -> Path | None:
    """The deployment-provided bundle's directory, or None. Set for the whole process tree
    (the service environment and the CLI wrappers share it), so every lookup agrees."""
    override = os.environ.get("HERMES_WEBAPP_DIST")
    return Path(override) if override else None


def mark_webapp_stale(project_root: Path) -> bool:
    """Mark the built bundle in *project_root* out of date, after a rebuild that failed. False when
    no bundle is built: nothing serves one, and the build output folder stays the builder's. A
    deployment-provided bundle (``HERMES_WEBAPP_DIST``) is never marked: it is read-only and was
    built for exactly this backend, so there is no newer code it could be stale against."""
    if _prebuilt_dist() is not None:
        return False
    dist = webapp_dist_dir(project_root)
    if not (dist / "index.html").is_file():
        return False
    (dist / STALE_MARKER).write_text("hermes update could not rebuild this bundle\n", encoding="utf-8")
    return True


def webapp_bundle_stale(dist: Path) -> bool:
    """Whether the bundle in *dist* is marked out of date (:func:`mark_webapp_stale`)."""
    return (Path(dist) / STALE_MARKER).is_file()


def _clear_stale_mark(dist: Path) -> None:
    try:
        (dist / STALE_MARKER).unlink(missing_ok=True)
    except OSError as exc:
        raise WebappBuildError(f"Could not mark the Webapp renderer at {dist} current: {exc}") from exc


@contextmanager
def _exclusive_build_lock(path: Path):
    """Cross-platform exclusive lock for one renderer generation."""
    from pm.filesystem import lock_fd, unlock_fd

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
    except OSError as exc:
        raise WebappBuildError(f"Could not open Webapp build lock {path}: {exc}") from exc

    try:
        try:
            if not lock_fd(handle.fileno(), wait=False):
                print("→ Another Hermes Webapp build is running; waiting for it...")
                if not lock_fd(handle.fileno(), wait=True, timeout=_LOCK_WAIT_SECONDS):
                    raise WebappBuildError(f"Timed out waiting for another Webapp build ({path})")
        except OSError as exc:
            raise WebappBuildError(f"Could not lock Webapp build {path}: {exc}") from exc
        yield
    finally:
        unlock_fd(handle.fileno())
        handle.close()


def prepare_webapp_renderer(
    project_root: Path,
    *,
    force: bool = False,
    skip_build: bool = False,
    explicit: bool = False,
) -> Path:
    """Return a verified browser renderer, serializing concurrent builds.

    ``explicit`` (a requested build) may install dependencies even when lazy
    installs are disabled, like ``hermes desktop --build-only``.
    """
    from hermes_cli.source_build import build_source_webapp, source_build_env, source_product_current

    project_root = project_root.resolve()
    dist = webapp_dist_dir(project_root)
    if _prebuilt_dist() is not None:
        # A deployment-provided bundle is the product for this backend, so it is always
        # current; and its store directory can host neither the build lock nor a rebuild.
        # Like --skip-build, this path never writes the install root.
        if not (dist / "index.html").is_file():
            raise WebappBuildError(
                f"HERMES_WEBAPP_DIST points at {dist}, which has no Webapp renderer"
            )
        print(f"→ Using the deployment-provided Hermes Webapp renderer at {dist}")
        return dist
    if skip_build:
        # Reuse takes no build lock, like `hermes dashboard --skip-build`: the
        # lock lives in the install root, which a read-only install cannot write.
        if not (dist / "index.html").is_file():
            raise WebappBuildError(
                f"--skip-build was passed but no Webapp renderer exists at {dist}"
            )
        print(f"→ Reusing Hermes Webapp renderer at {dist} (--skip-build)")
        return dist
    with _exclusive_build_lock(project_root / _LOCK_NAME):
        if not force and source_product_current(project_root, "webapp", dist):
            _clear_stale_mark(dist)
            print(f"✓ Hermes Webapp renderer is up to date: {dist}")
            return dist
        try:
            build_source_webapp(project_root, env=source_build_env(explicit=explicit), explicit=explicit)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            raise WebappBuildError(f"Browser-hosted Desktop build failed: {exc}") from exc
        _clear_stale_mark(dist)
        print(f"✓ Hermes Webapp renderer built: {dist}")
        return dist
