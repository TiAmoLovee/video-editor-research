"""验证 API 镜像 COPY 的运行时文件能完成只读候选校验，不依赖开发机资源。"""
from contextlib import ExitStack
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from clipforge.decision import candidates, validation, scoring, llm
from clipforge.decision.selection import build_selection
from backend.tests.test_candidates import fixture


class ApiPackageTests(unittest.TestCase):
    def test_readonly_selection_with_only_packaged_resources(self):
        root = candidates.ROOT
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        result = scoring.score_candidates(candidates.generate_candidates(analysis), analysis)
        dockerfile = (root / 'deploy/Dockerfile.api').read_text(encoding='utf-8')
        runtime = dockerfile.split('FROM python:', 1)[1]
        copies = re.findall(r'^COPY --chown=appuser:appuser (schemas|config/scoring|prompts) (/app/\S+)$', runtime, re.M)
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            packaged = Path(temp)
            for source, destination in copies:
                shutil.copytree(root / source, packaged / destination.removeprefix('/app/'))
            for module in (candidates, validation, scoring, llm):
                stack.enter_context(patch.object(module, 'ROOT', packaged))
            report = build_selection(result, analysis)
            self.assertEqual(report['summary']['original_count'], 3)
            self.assertEqual(report['model_requests'], 0)


if __name__ == '__main__':
    unittest.main()
