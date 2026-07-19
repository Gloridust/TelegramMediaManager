"""File-manager engine ops, focusing on path confinement (security-critical)."""

import os
import shutil
import tempfile

from app.core.downloader import DownloadEngine


def _engine(root):
    eng = DownloadEngine.__new__(DownloadEngine)
    eng.root_path = root
    eng.current_dir = root
    return eng


def test_list_delete_rename_confined():
    base = tempfile.mkdtemp()
    try:
        root = os.path.join(base, "root")
        os.makedirs(os.path.join(root, "sub"), exist_ok=True)
        with open(os.path.join(root, "a.mp4"), "wb") as f:
            f.write(b"x" * 100)
        with open(os.path.join(root, "sub", "b.txt"), "w") as f:
            f.write("hi")
        # a secret OUTSIDE the root — must never be reachable
        outside = os.path.join(base, "secret.txt")
        with open(outside, "w") as f:
            f.write("nope")

        eng = _engine(root)

        # list_entries: folders + files with sizes
        target, folders, files = eng.list_entries(root)
        assert folders == ["sub"], folders
        assert files == [("a.mp4", 100)], files

        # resolve_path confinement
        assert eng.resolve_path(os.path.join(root, "a.mp4"), "file") is not None
        assert eng.resolve_path(outside, "file") is None
        assert eng.resolve_path(os.path.join(root, "sub"), "dir") is not None
        assert eng.resolve_path(os.path.join(root, "a.mp4"), "dir") is None  # not a dir

        # delete refuses the root itself and anything outside it
        assert eng.delete_path(root) is False
        assert eng.delete_path(outside) is False
        assert os.path.exists(outside)  # untouched

        # delete a real file / folder inside the root
        assert eng.delete_path(os.path.join(root, "a.mp4")) is True
        assert not os.path.exists(os.path.join(root, "a.mp4"))
        assert eng.delete_path(os.path.join(root, "sub")) is True
        assert not os.path.exists(os.path.join(root, "sub"))

        # rename: valid, reject traversal, reject collision
        with open(os.path.join(root, "c.txt"), "w") as f:
            f.write("c")
        with open(os.path.join(root, "d.txt"), "w") as f:
            f.write("d")
        assert eng.rename_path(os.path.join(root, "c.txt"), "..") is None       # traversal
        assert eng.rename_path(os.path.join(root, "c.txt"), "d.txt") is None    # collision
        new = eng.rename_path(os.path.join(root, "c.txt"), "renamed.txt")
        assert new and os.path.basename(new) == "renamed.txt"
        assert os.path.exists(os.path.join(root, "renamed.txt"))
    finally:
        shutil.rmtree(base, ignore_errors=True)
