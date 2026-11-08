"""The code_patch format check (APIs §2.10) and the fail-closed safety rule (K17)."""

import pytest

from romulus_contracts.codepatch import (
    PatchSummary,
    patch_paths,
    summarize_code_patch,
    touches_safety,
    validate_code_patch,
)

SAFETY = ["safety/", "visor"]

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


def summary(patch: str) -> PatchSummary:
    return summarize_code_patch(patch)


# --- well-formed patches ---------------------------------------------------------------


def test_valid_patches_have_no_problems():
    for patch in (MODIFY, NEW_FILE, DELETE_FILE, GIT_STYLE, MODIFY + NEW_FILE):
        assert validate_code_patch(patch) == [], patch
        assert summary(patch).ok


def test_patch_paths():
    assert patch_paths(MODIFY) == ["control/gripper.py"]
    assert patch_paths(NEW_FILE) == ["tests/test_new.py"]
    assert patch_paths(DELETE_FILE) == ["old.py"]
    assert patch_paths(GIT_STYLE) == ["control/gripper.py"]
    assert patch_paths(MODIFY + NEW_FILE + MODIFY) == ["control/gripper.py", "tests/test_new.py"]


def test_rename_with_content_reports_both_paths():
    patch = "--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert patch_paths(patch) == ["new.py", "old.py"]


def test_removed_line_that_looks_like_a_header_is_not_a_header():
    # A removed line "-- x" shows up as "--- x" inside a hunk.
    patch = "--- a/f.md\n+++ b/f.md\n@@ -1,2 +1,2 @@\n--- x\n+++ y\n keep\n"
    assert patch_paths(patch) == ["f.md"]
    assert validate_code_patch(patch) == []


def test_crlf_line_endings():
    assert summary(MODIFY.replace("\n", "\r\n")).ok
    assert patch_paths(MODIFY.replace("\n", "\r\n")) == ["control/gripper.py"]


def test_form_feed_inside_a_changed_line_does_not_break_hunks():
    # str.splitlines() would split on \x0c, \x0b, \x1c-\x1e, \x85,  .
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a\x0cb\n+a\x0bc d\n ctx\x85\n"
    assert validate_code_patch(patch) == []


# --- the review's adversarial cases: never silently drop a path ------------------------


def test_pure_rename_without_content():
    patch = (
        "diff --git a/safety/visor.py b/attic/visor.py\n"
        "similarity index 100%\n"
        "rename from safety/visor.py\n"
        "rename to attic/visor.py\n"
    )
    s = summary(patch)
    assert s.ok and s.paths == ("attic/visor.py", "safety/visor.py")
    assert touches_safety(patch, SAFETY)


def test_copy_lines():
    patch = (
        "diff --git a/safety/limits.py b/control/limits.py\n"
        "similarity index 90%\n"
        "copy from safety/limits.py\n"
        "copy to control/limits.py\n"
        "--- a/safety/limits.py\n"
        "+++ b/control/limits.py\n"
        "@@ -1 +1 @@\n-a\n+b\n"
    )
    assert patch_paths(patch) == ["control/limits.py", "safety/limits.py"]
    assert touches_safety(patch, ["safety/"])


@pytest.mark.parametrize(
    "patch",
    [
        "diff --git a/safety/logo.png b/safety/logo.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/safety/logo.png and b/safety/logo.png differ\n",
        "Binary files a/safety/logo.png and b/safety/logo.png differ\n",
        "diff --git a/safety/logo.png b/safety/logo.png\n"
        "index 1111111..2222222 100644\n"
        "GIT binary patch\nliteral 5\nMcmZ?wbhEHbFh&1\n\nliteral 0\nHcmV?d00001\n",
    ],
)
def test_binary_diff_lists_path_and_is_a_problem(patch):
    s = summary(patch)
    assert s.paths == ("safety/logo.png",)
    assert any("binary change" in p for p in s.problems)
    assert touches_safety(patch, ["control/"])  # fails closed even off the safety list


def test_mode_only_change():
    patch = "diff --git a/safety/estop.sh b/safety/estop.sh\nold mode 100644\nnew mode 100755\n"
    s = summary(patch)
    assert s.ok and s.paths == ("safety/estop.sh",)
    assert touches_safety(patch, SAFETY)


def test_delete_empty_file():
    patch = (
        "diff --git a/safety/visor.cfg b/safety/visor.cfg\n"
        "deleted file mode 100644\n"
        "index e69de29..0000000\n"
    )
    assert summary(patch).ok and patch_paths(patch) == ["safety/visor.cfg"]
    assert touches_safety(patch, SAFETY)


def test_quoted_paths_are_unquoted():
    patch = (
        'diff --git "a/safety/vis\\303\\266r.py" "b/safety/vis\\303\\266r.py"\n'
        "index 1111111..2222222 100644\n"
        '--- "a/safety/vis\\303\\266r.py"\n'
        '+++ "b/safety/vis\\303\\266r.py"\n'
        "@@ -1 +1 @@\n-a\n+b\n"
    )
    s = summary(patch)
    assert s.ok and s.paths == ("safety/visör.py",)
    assert touches_safety(patch, ["safety/"])


def test_quoted_path_with_escapes_and_unquoted_other_side():
    patch = (
        'diff --git a/x.py "b/we\\"ird\\tname.py"\n'
        "rename from x.py\n"
        'rename to "we\\"ird\\tname.py"\n'
    )
    assert patch_paths(patch) == ['we"ird\tname.py', "x.py"]


def test_broken_quoting_is_a_problem():
    patch = '--- "a/safety/vis\\q.py"\n+++ "b/safety/vis.py\n@@ -1 +1 @@\n-a\n+b\n'
    s = summary(patch)
    assert any("can't read the quoted path" in p for p in s.problems)
    assert touches_safety(patch, ["other/"])


def test_unprefixed_paths_are_listed_and_reported():
    patch = (
        "diff --git safety/visor.py safety/visor.py\n"
        "--- safety/visor.py\n+++ safety/visor.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    s = summary(patch)
    assert s.paths == ("safety/visor.py",)
    assert any("should start with 'a/'" in p for p in s.problems)
    assert any("should start with 'b/'" in p for p in s.problems)
    assert touches_safety(patch, ["control/"])


def test_mnemonic_prefixes_are_unprefixed():
    patch = "diff --git c/safety/v.py w/safety/v.py\n--- c/safety/v.py\n+++ w/safety/v.py\n"
    s = summary(patch)
    assert not s.ok
    assert touches_safety(patch, [])


RELATIVE_D1 = (
    "diff --git a/robot/control.py b/robot/control.py\n"
    "--- a/robot/control.py\n+++ b/robot/control.py\n@@ -1 +1 @@\n-a\n+b\n"
)
PREFIXED_D1 = RELATIVE_D1.replace("a/robot/", "a/device/robot/").replace(
    "b/robot/", "b/device/robot/"
)


@pytest.mark.parametrize("patch", [RELATIVE_D1, PREFIXED_D1], ids=["relative", "prefixed"])
def test_both_path_forms_read_the_same_relative_to_device(patch):
    s = summary(patch)
    assert s.ok, s.problems
    assert s.paths == ("robot/control.py",)
    assert s.root_prefixed == (patch is PREFIXED_D1)
    assert s.strip == (2 if patch is PREFIXED_D1 else 1)
    assert touches_safety(patch, ["robot/control.py"])
    assert not touches_safety(patch, ["safety"])


def test_device_prefixed_safety_and_verify_paths_are_seen_without_the_prefix():
    patch = "--- a/device/safety/visor.py\n+++ b/device/safety/visor.py\n@@ -1 +1 @@\n-a\n+b\n"
    s = summary(patch)
    assert s.ok and s.paths == ("safety/visor.py",) and s.root_prefixed
    assert touches_safety(patch, ["safety/"])
    verify = "--- /dev/null\n+++ b/device/Verify/test_x.py\n@@ -0,0 +1 @@\n+x\n"
    assert summary(verify).paths == ("Verify/test_x.py",)  # is_under(.., "verify") matches


def test_a_patch_mixing_both_forms_is_a_problem_and_touches_safety():
    patch = PREFIXED_D1 + "--- a/robot/app.py\n+++ b/robot/app.py\n@@ -1 +1 @@\n-a\n+b\n"
    s = summary(patch)
    assert any("mix the two forms" in p for p in s.problems)
    assert not s.root_prefixed
    assert touches_safety(patch, [])


@pytest.mark.parametrize(
    "path", ["device", "device/../robot/x.py", "device/../../etc/passwd", "../device/x.py"]
)
def test_the_device_prefix_never_escapes_or_names_the_root(path):
    patch = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"
    s = summary(patch)
    assert not s.ok and s.rejected_paths
    assert touches_safety(patch, [])


def test_dot_and_double_slash_are_normalized():
    patch = "--- a/./safety/visor.py\n+++ b/safety//visor.py\n@@ -1 +1 @@\n-a\n+b\n"
    s = summary(patch)
    assert s.ok and s.paths == ("safety/visor.py",)
    assert touches_safety(patch, ["safety/"])


@pytest.mark.parametrize(
    "raw",
    ["a/../secrets.txt", "a/safety/../../etc/passwd", "a//etc/passwd", "a/", "a/."],
)
def test_paths_escaping_device_are_rejected_not_normalized(raw):
    patch = f"--- {raw}\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    s = summary(patch)
    assert s.problems and raw in s.rejected_paths
    assert s.paths == ("x.py",)
    assert touches_safety(patch, [])


def test_dotdot_that_would_normalize_inside_is_still_rejected():
    patch = "--- a/control/../safety/visor.py\n+++ b/control/../safety/visor.py\n"
    s = summary(patch)
    assert s.paths == () and len(s.rejected_paths) == 2
    assert touches_safety(patch, ["control/"])


def test_unreadable_diff_git_header_is_a_problem():
    patch = "diff --git a/my file.py b/other file.py\nold mode 100644\nnew mode 100755\n"
    s = summary(patch)
    assert any("can't read the paths" in p for p in s.problems)
    assert touches_safety(patch, [])


def test_diff_git_with_spaces_in_same_path():
    patch = "diff --git a/my file.py b/my file.py\nold mode 100644\nnew mode 100755\n"
    assert summary(patch).ok and patch_paths(patch) == ["my file.py"]


# --- STM32 firmware paths: reported, not rejected (open I0 question) ------------------


def test_firmware_inside_device_is_listed_with_a_note():
    patch = "--- a/firmware/src/main.c\n+++ b/firmware/src/main.c\n@@ -1 +1 @@\n-a\n+b\n"
    s = summary(patch)
    assert s.ok and s.paths == ("firmware/src/main.c",)
    assert s.notes == ("firmware/src/main.c: STM32 code; needs a rebuild and reflash",)


def test_firmware_outside_device_gets_a_hint():
    patch = "--- a/../firmware/src/main.c\n+++ b/../firmware/src/main.c\n"
    s = summary(patch)
    assert "open I0 question" in s.problems[0]
    assert s.rejected_paths == ("a/../firmware/src/main.c", "b/../firmware/src/main.c")


# --- structure problems ----------------------------------------------------------------


def test_empty_and_non_diff():
    assert validate_code_patch("") == ["code_patch is empty"]
    assert validate_code_patch("   \n") == ["code_patch is empty"]
    assert validate_code_patch(None) == ["code_patch is empty"]
    assert validate_code_patch("just text\n")[0].startswith("no file headers found")
    assert patch_paths("just text") == []
    assert touches_safety("just text", [])


def test_hunk_without_header():
    assert validate_code_patch("@@ -1 +1 @@\n-a\n+b\n")[0].startswith("no file headers")
    patch = "@@ -1 +1 @@\n-a\n+b\n" + MODIFY
    assert "line 1: hunk has no file header before it" in validate_code_patch(patch)


def test_both_sides_dev_null_lists_nothing():
    assert patch_paths("--- /dev/null\n+++ /dev/null\n") == []


def test_truncated_hunk_is_reported():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,5 +1,5 @@\n-a\n+b\n"
    assert validate_code_patch(patch) == ["hunk at line 3 is shorter than its header says"]


def test_no_newline_marker_inside_hunk():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n"
    assert validate_code_patch(patch) == []


def test_hunk_cut_short_by_garbage_line():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n-a\n+b\nGARBAGE\n"
    assert validate_code_patch(patch) == ["hunk at line 3 is shorter than its header says"]


# --- touches_safety ---------------------------------------------------------------------


def test_touches_safety_matches_dirs_and_files_by_path_component():
    patch = "--- a/safety/visor.py\n+++ b/safety/visor.py\n@@ -1 +1 @@\n-a\n+b\n"
    assert touches_safety(patch, ["safety"])
    assert touches_safety(patch, ["safety/"])
    assert touches_safety(patch, ["./safety/visor.py"])
    assert not touches_safety(patch, ["safe"])
    assert not touches_safety(patch, ["safety/visor"])
    assert not touches_safety(patch, ["control/"])
    assert not touches_safety(patch, [])


def test_touches_safety_clean_patch_off_list_is_false():
    assert not touches_safety(MODIFY, SAFETY)


def test_touches_safety_none_patch_is_false():
    assert not touches_safety(None, SAFETY)


def test_touches_safety_empty_patch_fails_closed():
    assert touches_safety("", SAFETY)


def test_quoted_binary_paths():
    patch = 'Binary files "a/safety/l\\303\\266go.png" and "b/safety/l\\303\\266go.png" differ\n'
    s = summary(patch)
    assert s.paths == ("safety/lögo.png",) and not s.ok


def test_binary_line_with_quoted_new_side_only():
    patch = 'Binary files /dev/null and "b/safety/n\\303\\244w.bin" differ\n'
    assert summary(patch).paths == ("safety/näw.bin",)


def test_unreadable_binary_line_is_a_problem():
    patch = "Binary files something differ\n"
    s = summary(patch)
    assert any("can't read the paths" in p for p in s.problems)
    assert touches_safety(patch, [])


def test_broken_quoted_rename_is_a_problem():
    patch = 'diff --git a/x.py b/y.py\nrename from x.py\nrename to "y\\q.py"\n'
    s = summary(patch)
    assert any("line 3: can't read the quoted path" in p for p in s.problems)
    assert touches_safety(patch, [])


NEW_SYMLINK = """\
diff --git a/vdir b/vdir
new file mode 120000
index 0000000..1b2c3d4
--- /dev/null
+++ b/vdir
@@ -0,0 +1 @@
+/some/host/dir
\\ No newline at end of file
"""

CHANGED_SYMLINK = """\
diff --git a/link b/link
index 1111111..2222222 120000
--- a/link
+++ b/link
@@ -1 +1 @@
-old
+/etc/passwd
"""

GITLINK = """\
diff --git a/sub b/sub
new file mode 160000
index 0000000..3333333
--- /dev/null
+++ b/sub
@@ -0,0 +1 @@
+Subproject commit 3333333333333333333333333333333333333333
"""

FILE_TO_SYMLINK = "diff --git a/x.py b/x.py\nold mode 100644\nnew mode 120000\n"


@pytest.mark.parametrize(
    "patch,kind",
    [
        (NEW_SYMLINK, "symlink"),
        (CHANGED_SYMLINK, "symlink"),
        (GITLINK, "submodule"),
        (FILE_TO_SYMLINK, "symlink"),
    ],
)
def test_symlinks_and_submodules_are_problems_and_fail_closed(patch, kind):
    assert any(kind in p for p in summary(patch).problems), summary(patch).problems
    assert touches_safety(patch, SAFETY) is True


@pytest.mark.parametrize(
    "mode_line,kind",
    [
        ("new file mode 0120000", "symlink"),  # 7 digits: git apply still reads it as octal
        ("new file mode 00120000", "symlink"),
        ("new mode 120777", "symlink"),  # any permission bits on a symlink type
        ("new file mode 0160000", "submodule"),
        ("index 1111111..2222222 0120000", "symlink"),
        ("new file mode 100899", "unreadable mode"),
    ],
)
def test_link_modes_in_any_spelling_fail_closed(mode_line, kind):
    patch = (
        f"diff --git a/vdir b/vdir\n{mode_line}\n--- /dev/null\n+++ b/vdir\n@@ -0,0 +1 @@\n+/x\n"
    )
    assert any(kind in p for p in summary(patch).problems), summary(patch).problems
    assert touches_safety(patch, []) is True


@pytest.mark.parametrize("path", ["SAFETY/stop.py", "Safety/stop.py", "./safety/stop.py"])
def test_touches_safety_ignores_case_and_dot_slash(path):
    patch = MODIFY.replace("control/gripper.py", path)
    assert touches_safety(patch, ["safety"]) is True
    assert touches_safety(patch, ["./SAFETY/"]) is True


def test_regular_file_modes_are_fine():
    assert (
        validate_code_patch("diff --git a/a.py b/a.py\nold mode 0100644\nnew mode 0100755\n") == []
    )
    patch = "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
    assert validate_code_patch(patch) == []
