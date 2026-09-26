import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class V20Tests(unittest.TestCase):
    def test_default_keyword_template_reaches_minimum(self):
        config = json.loads((ROOT / "templates" / "keywords-expansion.template.json").read_text(encoding="utf-8"))
        result = load_script("expand-keywords.py").expand_questions(config)
        self.assertGreaterEqual(result["summary"]["total"], 16)
        self.assertGreaterEqual(result["summary"]["recommendation_keyword_count"], 10)
        self.assertFalse(result["summary"]["flagged_for_review"])

    def test_asset_matrix_covers_all_questions(self):
        matrix = load_script("content-asset-matrix.py")
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            cfg = {
                "scope": {"questions": [
                    {"id": f"Q{i:02d}", "type": "brand_cognition" if i in (6, 7) else "non_brand", "business_value": "high", "prompt": f"问题{i}"}
                    for i in range(1, 9)
                ]}
            }
            (run / "frozen-config.json").write_text(json.dumps(cfg), encoding="utf-8")
            (run / "observations.jsonl").write_text("", encoding="utf-8")
            old_argv = __import__("sys").argv
            try:
                __import__("sys").argv = ["content-asset-matrix.py", str(run)]
                self.assertEqual(matrix.main(), 0)
            finally:
                __import__("sys").argv = old_argv
            with (run / "report" / "content-asset-list.csv").open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual({r["qid"] for r in rows}, {f"Q{i:02d}" for i in range(1, 9)})

    def test_generated_assets_have_no_template_tokens(self):
        schema_mod = load_script("generate-schema.py")
        brand = {"canonical_name": "示例公司", "aliases": []}
        cfg = {"brand": brand, "scope": {"questions": []}}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "schema.jsonld"
            schema_mod.generate_schema(cfg, str(ROOT / "templates" / "schema-org.template.jsonld"), str(out))
            self.assertNotIn("CHANGE_ME", out.read_text(encoding="utf-8"))
            self.assertNotIn("CHANGE_ME", schema_mod.derive_summary_from_brand(brand))
            self.assertLessEqual(len(schema_mod.derive_summary_from_brand(brand)), 200)


if __name__ == "__main__":
    unittest.main()
