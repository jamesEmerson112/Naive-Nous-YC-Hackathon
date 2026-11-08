"""The code_patch format check (APIs §2.10)."""

from romulus_contracts.codepatch import patch_paths, validate_code_patch

MODIFY = """\
--- a/control/gripper.py
+++ b/control/gripper.py
@@ -1,3 +1,3 @@
 def close():
-    return 0
+    return 1
 # end
"""

NEW_FILE = """\
--- /dev/null
+++ b/tests/test_new.py
@@ -0,0 +1,2 @@
+def test_x():
+    assert True
"""

DELETE_FILE = """\
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""

GIT_STYLE = """\
diff --git a/control/gripper.py b/control/gripper.py
index 1111111..2222222 100644
--- a/control/gripper.py\t2026-11-07 22:01:00
+++ b/control/gripper.py\t2026-11-07 22:02:00
@@ -1,2 +1,2 @@
-a
+b
 c
\\ No newline at end of file
"""


def test_valid_patches_have_no_problems():
    for patch in (MODIFY, NEW_FILE, DELETE_FILE, GIT_STYLE, MODIFY + NEW_FILE):
        assert validate_code_patch(patch) == [], patch


def test_patch_paths():
    assert patch_paths(MODIFY) == ["control/gripper.py"]
    assert patch_paths(NEW_FILE) == ["tests/test_new.py"]
    assert patch_paths(DELETE_FILE) == ["old.py"]
    assert patch_paths(GIT_STYLE) == ["control/gripper.py"]
    assert patch_paths(MODIFY + NEW_FILE + MODIFY) == ["control/gripper.py", "tests/test_new.py"]


def test_rename_reports_both_paths():
    patch = "--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert patch_paths(patch) == ["new.py", "old.py"]


def test_removed_line_that_looks_like_a_header_is_not_a_header():
    # A removed line "-- x" shows up as "--- x" inside a hunk.
    patch = "--- a/f.md\n+++ b/f.md\n@@ -1,2 +1,2 @@\n--- x\n+++ y\n keep\n"
    assert patch_paths(patch) == ["f.md"]
    assert validate_code_patch(patch) == []


def test_empty_and_non_diff():
    assert validate_code_patch("") == ["code_patch is empty"]
    assert validate_code_patch("   \n") == ["code_patch is empty"]
    assert validate_code_patch("just text\n")[0].startswith("no '--- a/<path>'")
    assert patch_paths("just text") == []


def test_missing_prefixes():
    patch = "--- control/gripper.py\n+++ control/gripper.py\n@@ -1 +1 @@\n-a\n+b\n"
    problems = validate_code_patch(patch)
    assert len(problems) == 2 and "should start with 'a/'" in problems[0]
    assert patch_paths(patch) == []


def test_paths_must_stay_inside_device_root():
    for bad in ("a/../secrets.txt", "a//etc/passwd", "a/"):
        patch = f"--- {bad}\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        assert any("must stay inside device/" in p for p in validate_code_patch(patch)), bad
    patch = "--- a/../x\n+++ b/../x\n@@ -1 +1 @@\n-a\n+b\n"
    assert patch_paths(patch) == []


def test_paths_starting_with_device_are_flagged():
    patch = "--- a/device/control/x.py\n+++ b/device/control/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    problems = validate_code_patch(patch)
    assert len(problems) == 2 and "relative to device/ itself" in problems[0]


def test_both_sides_dev_null():
    patch = "--- /dev/null\n+++ /dev/null\n"
    assert any("both sides" in p for p in validate_code_patch(patch))


def test_truncated_hunk_is_reported():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,5 +1,5 @@\n-a\n+b\n"
    assert validate_code_patch(patch) == ["hunk at line 3 is shorter than its header says"]


def test_no_newline_marker_inside_hunk():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n"
    assert validate_code_patch(patch) == []


def test_hunk_cut_short_by_garbage_line():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n-a\n+b\nGARBAGE\n"
    assert validate_code_patch(patch) == ["hunk at line 3 is shorter than its header says"]
