from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._payload


def _load_skill_example(function_name: str, namespace: dict | None = None):
    """Execute the fenced Python example that defines ``function_name``."""
    blocks = re.findall(
        r"```python\n(.*?)\n```", SKILL_PATH.read_text(encoding="utf-8"), re.DOTALL
    )
    for block in blocks:
        if f"def {function_name}(" in block:
            globals_ = {"__name__": "skill_example", **(namespace or {})}
            module = ast.parse(block)
            preamble = []
            for node in module.body:
                if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
                    preamble.append(node)
                if isinstance(node, ast.FunctionDef) and node.name == function_name:
                    preamble.append(node)
                    break
            exec(
                compile(ast.fix_missing_locations(ast.Module(body=preamble, type_ignores=[])), str(SKILL_PATH), "exec"),
                globals_,
            )
            return globals_[function_name]
    raise AssertionError(f"No runnable example defines {function_name}")


class SkillRoutingRegressionTests(unittest.TestCase):
    def test_get_prefix_routes_920_before_the_broad_9_branch(self):
        get_prefix = _load_skill_example("get_prefix")

        self.assertEqual(
            {code: get_prefix(code) for code in ("920002", "900901", "600519", "000001")},
            {"920002": "bj", "900901": "sh", "600519": "sh", "000001": "sz"},
        )

    def test_tencent_quote_requests_the_correct_market_prefixes(self):
        tencent_quote = _load_skill_example("tencent_quote")
        requested_urls: list[str] = []

        def fake_urlopen(request, *, timeout):
            requested_urls.append(request.full_url)
            return _Response(b"")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(tencent_quote(["920002", "900901", "600519", "000001"]), {})

        self.assertEqual(
            requested_urls,
            ["https://qt.gtimg.cn/q=bj920002,sh900901,sh600519,sz000001"],
        )

    def test_sina_fund_flow_keeps_existing_920_and_900_routes(self):
        fund_flow_backup = _load_skill_example("fund_flow_backup", {"UA": "test-agent"})
        requested_urls: list[str] = []

        def fake_urlopen(request, *, timeout):
            requested_urls.append(request.full_url)
            return _Response(b"[]")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            for code in ("920002", "900901", "600519", "000001"):
                self.assertEqual(fund_flow_backup(code), [])

        self.assertEqual(
            [parse_qs(urlparse(url).query)["daima"][0] for url in requested_urls],
            ["bj920002", "sh900901", "sh600519", "sz000001"],
        )
