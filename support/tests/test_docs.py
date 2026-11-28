"""The submission docs stay true to the code: commands exist, links resolve, the Meko usage table
names real Meko tools and every tool the code calls, and the demo fits the 5-minute limit."""

from __future__ import annotations

import json
import re
from pathlib import Path

from django.conf import settings
from django.core.management import get_commands
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
DOC_FILES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
CODE_DIRS = [ROOT / "dejafix", ROOT / "support"]
MEKO_USAGE = ROOT / "docs" / "meko-usage.md"
DEMO_SCRIPT = ROOT / "docs" / "demo-script.md"
VIDEO_LIMIT_SECONDS = 5 * 60
CI_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
USES_LINE = re.compile(r"uses:\s*([\w./-]+)@(\S+)")
BENCHMARK_REPORT = ROOT / "docs" / "benchmark.md"
# README results row -> the benchmark report row it quotes, for run 1 / run 2 of each arm.
README_RESULT_ROWS = {
    "Correct fixes, of 36": "Correct, first occurrences and repeats",
    "Correct on repeat tickets, of 24": "Correct, repeats",
    "Avg tokens on repeat tickets": "Avg tokens, repeats",
    "Tickets a human had to handle, of 36": "Human touches",
}
NUMBER = re.compile(r"\d[\d,]*")
SUBMISSION = ROOT / "docs" / "submission.md"
MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
MAX_IMAGE_BYTES = 400_000

MANAGE_COMMAND = re.compile(r"manage\.py\s+([a-z_]+)")
MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
MEKO_TOOL = re.compile(r"`((?:amp|artifact|context|conversation|datapack|knowledgebase|memory|project|selflearning|track)_[a-z_]+)`")
CODE_CALL = re.compile(r"\.call\(\s*\"([a-z_]+)\"")
SCENE_END = re.compile(r"^#+\s.*?(\d+):(\d{2})\s*[–-]\s*(\d+):(\d{2})", re.MULTILINE)


def tools_in_tables(text: str) -> set[str]:
    """Tool names from the first cell of each table row; prose also names arguments like `datapack_id`."""
    first_cells = (line.split("|")[1] for line in text.splitlines() if line.startswith("|") and line.count("|") > 2)
    return {name for cell in first_cells for name in MEKO_TOOL.findall(cell)}


def table_rows(text: str) -> dict[str, list[str]]:
    return {
        cells[0]: cells[1:]
        for line in text.splitlines()
        if line.startswith("|") and (cells := [cell.strip() for cell in line.strip("|").split("|")])
    }


def production_python_files():
    for directory in CODE_DIRS:
        for path in directory.rglob("*.py"):
            if "tests" not in path.parts and "migrations" not in path.parts:
                yield path


class DocsTests(SimpleTestCase):
    def test_every_documented_manage_command_exists(self):
        commands = set(get_commands())
        for doc in DOC_FILES:
            for name in MANAGE_COMMAND.findall(doc.read_text(encoding="utf-8")):
                with self.subTest(doc=doc.name, command=name):
                    self.assertIn(name, commands)

    def test_relative_links_resolve(self):
        for doc in DOC_FILES:
            for target in MARKDOWN_LINK.findall(doc.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                path = (doc.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(doc=doc.name, link=target):
                    self.assertTrue(path.exists(), f"{target} does not exist")

    def test_meko_usage_names_only_real_tools(self):
        live_tools = {tool["name"] for tool in json.loads((ROOT / "docs" / "meko-tools.json").read_text(encoding="utf-8"))}
        named = tools_in_tables(MEKO_USAGE.read_text(encoding="utf-8"))
        self.assertTrue(named)
        self.assertEqual(named - live_tools, set())

    def test_meko_usage_covers_every_tool_the_code_calls(self):
        called = {name for path in production_python_files() for name in CODE_CALL.findall(path.read_text(encoding="utf-8"))}
        documented = tools_in_tables(MEKO_USAGE.read_text(encoding="utf-8"))
        self.assertTrue(called)
        self.assertEqual(called - documented, set())

    def test_ci_workflow_runs_suite_with_pinned_actions_and_no_secrets(self):
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        actions = USES_LINE.findall(workflow)
        self.assertTrue(actions)
        for action, ref in actions:
            with self.subTest(action=action):
                self.assertRegex(ref, r"^[0-9a-f]{40}$", "pin actions to a full commit SHA")
        self.assertIn("python manage.py test", workflow)
        self.assertRegex(workflow, r"permissions:\s*\n\s*contents: read")
        self.assertNotIn("secrets.", workflow)

    def test_demo_script_fits_the_video_limit(self):
        scenes = SCENE_END.findall(DEMO_SCRIPT.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(scenes), 3)
        last_end = max(int(minutes) * 60 + int(seconds) for _, _, minutes, seconds in scenes)
        self.assertLess(last_end, VIDEO_LIMIT_SECONDS)

    def test_readme_results_quote_the_benchmark_report(self):
        report = table_rows(BENCHMARK_REPORT.read_text(encoding="utf-8"))
        readme = table_rows((ROOT / "README.md").read_text(encoding="utf-8"))
        # Week 11 added M3 (after the correction-retrieval fix): two docs-only runs, three Meko runs.
        self.assertEqual(report["Metric"], ["D1 (docs_only)", "D2 (docs_only)", "M1 (meko)", "M2 (meko)", "M3 (meko)"])
        for readme_label, report_label in README_RESULT_ROWS.items():
            with self.subTest(row=readme_label):
                docs_cell, meko_cell = readme[readme_label]
                quoted = NUMBER.findall(docs_cell)[:2] + NUMBER.findall(meko_cell)[:3]
                expected = [NUMBER.findall(cell)[0] for cell in report[report_label]]
                self.assertEqual(quoted, expected)

    def test_readme_images_exist_have_alt_text_and_are_small(self):
        images = [(alt, link) for alt, link in MARKDOWN_IMAGE.findall((ROOT / "README.md").read_text(encoding="utf-8"))
                  if not link.startswith("https://")]  # the CI badge is GitHub's
        self.assertGreaterEqual(len(images), 3)
        for alt, link in images:
            with self.subTest(image=link):
                path = ROOT / link
                self.assertTrue(path.is_file())
                self.assertLessEqual(path.stat().st_size, MAX_IMAGE_BYTES)
                self.assertGreater(len(alt.strip()), 10)

    def test_submission_results_match_the_readme(self):
        readme = table_rows((ROOT / "README.md").read_text(encoding="utf-8"))
        submission = table_rows(SUBMISSION.read_text(encoding="utf-8"))
        for label in README_RESULT_ROWS:
            with self.subTest(row=label):
                self.assertEqual(submission[label], readme[label])
