"""分子结构渲染工具测试：SMILES→PNG 成功、非法 SMILES 优雅降级。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from utils.molecule_viz import (  # noqa: E402
    mol_from_smiles,
    png_temp_file,
    render_imine_product,
    smiles_to_image,
    smiles_to_png_file,
)

ALD = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"  # Tp
AMINE = "Nc1ccc(N)cc1"  # Pa


class TestSmilesParsing:
    def test_valid_smiles(self):
        assert mol_from_smiles(ALD) is not None
        assert mol_from_smiles(AMINE) is not None

    def test_invalid_smiles_returns_none(self):
        assert mol_from_smiles("not_a_smiles_((((") is None
        assert mol_from_smiles("") is None
        assert mol_from_smiles("   ") is None


class TestSmilesToImage:
    def test_valid_smiles_to_image(self):
        img = smiles_to_image(ALD)
        assert img is not None
        assert img.size[0] > 0 and img.size[1] > 0

    def test_invalid_smiles_to_image_returns_none(self):
        assert smiles_to_image("not_a_smiles_((((") is None

    def test_png_file_success(self, tmp_path):
        out = smiles_to_png_file(AMINE, tmp_path / "amine.png")
        assert out is not None and out.exists() and out.stat().st_size > 0

    def test_png_file_invalid_returns_none(self, tmp_path):
        out = smiles_to_png_file("###invalid###", tmp_path / "bad.png")
        assert out is None
        assert not (tmp_path / "bad.png").exists()

    def test_png_temp_file_cleanup_pattern(self):
        png = png_temp_file(ALD)
        assert png is not None and png.exists()
        png.unlink()
        assert not png.exists()


class TestImineProduct:
    def test_render_imine_product(self):
        img = render_imine_product(ALD, AMINE)
        assert img is not None
        assert img.size[0] > 0 and img.size[1] > 0

    def test_imine_product_invalid_returns_none(self):
        assert render_imine_product("###invalid###", AMINE) is None
        assert render_imine_product(ALD, "") is None


class TestReportWithImages:
    def test_report_embeds_structure_images(self, tmp_path):
        from report_generator.exporter import generate_report

        path = generate_report(
            ALD, AMINE,
            {"gnn_probability": 0.9, "tree_probability": 0.8, "ensemble_probability": 0.85},
            {"method": "溶剂热法"},
            output_path=tmp_path / "report_img.docx",
        )
        assert path.exists() and path.stat().st_size > 0
        # docx 内应包含嵌入的 PNG 图片
        import zipfile
        with zipfile.ZipFile(path) as zf:
            media = [n for n in zf.namelist() if n.startswith("word/media/")]
        assert len(media) >= 2

    def test_report_invalid_smiles_degrades(self, tmp_path):
        from report_generator.exporter import generate_report

        path = generate_report(
            "###invalid###", AMINE,
            {"ensemble_probability": 0.5},
            {"method": "溶剂热法"},
            output_path=tmp_path / "report_bad.docx",
        )
        assert path.exists() and path.stat().st_size > 0
