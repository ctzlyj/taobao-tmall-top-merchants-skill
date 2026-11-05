import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_skill import collect_files, find_personal_paths, validate_package


class PackagingCleanTests(unittest.TestCase):
    def test_collect_files_excludes_caches_artifacts_and_personal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            (source / "scripts").mkdir()
            (source / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
            (source / "scripts" / "__pycache__").mkdir()
            (source / "scripts" / "__pycache__" / "run.cpython-312.pyc").write_bytes(b"\x00")
            (source / "tests" / "__pycache__").mkdir(parents=True)
            (source / "tests" / "__pycache__" / "t.cpython-312.pyc").write_bytes(b"\x00")
            (source / ".pytest_cache" / "v").mkdir(parents=True)
            (source / ".pytest_cache" / "v" / "lastfailed").write_text("[]", encoding="utf-8")
            (source / "work").mkdir()
            (source / "work" / "checkpoint.json").write_text("{}", encoding="utf-8")
            (source / "outputs").mkdir()
            (source / "outputs" / "result.xlsx").write_bytes(b"\x00")
            (source / "docs").mkdir()
            (source / "docs" / "internal-plan.md").write_text("internal", encoding="utf-8")
            (source / "run.log").write_text("log", encoding="utf-8")
            (source / "subjects.json").write_text("[]", encoding="utf-8")
            (source / ".env").write_text("SECRET=1", encoding="utf-8")

            relative = sorted(item.as_posix() for item in collect_files(source))

        self.assertEqual(relative, ["SKILL.md", "scripts/run.py"])

    def test_repository_package_is_clean_and_portable(self):
        files = collect_files(ROOT)
        relative = [item.as_posix() for item in files]

        self.assertIn("SKILL.md", relative)
        self.assertIn("scripts/bootstrap.ps1", relative)
        self.assertIn("scripts/preflight.py", relative)
        self.assertIn("tests/test_packaging_clean.py", relative)
        for item in relative:
            self.assertNotIn("__pycache__", item)
            self.assertNotIn(".pytest_cache", item)
            self.assertFalse(item.endswith((".pyc", ".pyo", ".log")))
        self.assertEqual(find_personal_paths(files, ROOT), [])
        self.assertEqual(validate_package(files, ROOT), [])

    def test_personal_path_scan_detects_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "SKILL.md").write_text("# ok\n", encoding="utf-8")
            (source / "run.py").write_text(
                "path = 'C:/Users/" + "caotong" + ".888" + "/Desktop/x'\n", encoding="utf-8"
            )

            violations = find_personal_paths(collect_files(source), source)

        self.assertEqual({item["file"] for item in violations}, {"run.py"})

    def test_skill_frontmatter_declares_version(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertRegex(text.split("---")[1], r"version:\s*\d+\.\d+\.\d+")

    def test_powershell_scripts_carry_utf8_bom_for_windows_51(self):
        # Windows PowerShell 5.1 对无 BOM 的 UTF-8 脚本按 ANSI/GBK 解析，
        # 中文字符串会被截断报错；发布包中的 .ps1 必须带 BOM。
        for relative in collect_files(ROOT):
            if relative.suffix.lower() == ".ps1":
                self.assertTrue(
                    (ROOT / relative).read_bytes().startswith(b"\xef\xbb\xbf"),
                    f"missing UTF-8 BOM: {relative}",
                )


if __name__ == "__main__":
    unittest.main()
