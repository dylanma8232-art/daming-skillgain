import openpyxl
from evaluator.assertions import AssertionEngine
from evaluator.assertions.script_eval import _validate_single_data_file


def test_large_artifact_metadata_preservation(tmp_path):
    """验证大于 500 KiB 的大型文件（如 PDF）其键与元数据得到保留，且能通过 artifact_extensions 断言。"""
    pdf_file = tmp_path / "artifacts" / "report.pdf"
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    # 创建超过 500 KiB (例如 600 KiB) 的文件
    pdf_file.write_bytes(b"%PDF-1.4 " + b"0" * (600 * 1024))

    # 模拟 engine.py 产物收集和 artifacts_map 构建逻辑
    from evaluator.engine import BINARY_EXTENSIONS, TEXT_EXTENSIONS
    artifact_files = ["artifacts/report.pdf"]
    workspace_dir = tmp_path
    max_bytes = 500 * 1024
    artifacts_map = {}
    for art_rel in artifact_files:
        art_full = workspace_dir / art_rel
        suffix = art_full.suffix.lower()
        is_binary = suffix in BINARY_EXTENSIONS and suffix not in TEXT_EXTENSIONS
        if is_binary:
            with art_full.open("rb") as f_bin:
                artifacts_map[art_rel] = f_bin.read(max_bytes)
        else:
            with art_full.open("r", encoding="utf-8", errors="replace") as f_txt:
                artifacts_map[art_rel] = f_txt.read(max_bytes)

    # 验证键名保留在 artifacts_map 中
    assert "artifacts/report.pdf" in artifacts_map
    assert len(artifacts_map["artifacts/report.pdf"]) == max_bytes

    # 验证断言评估 artifact_extensions 可以成功检测到 .pdf 存在
    engine = AssertionEngine()
    rule = {
        "name": "expected_output_formats",
        "type": "script_eval",
        "parameters": {
            "script_name": "artifact_extensions",
            "extensions": [".pdf"],
        },
    }
    res = engine.evaluate_suite([rule], artifacts=artifacts_map)
    assert res.passed is True
    assert res.score == 100.0


def test_xlsx_lossless_binary_validation(tmp_path):
    """验证正常的 XLSX 二进制文件无损读取并通过 csv_well_formed / structured_data_integrity 断言。"""
    xlsx_file = tmp_path / "artifacts" / "data.xlsx"
    xlsx_file.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Name", "Value"])
    ws.append(["Item1", 100])
    wb.save(xlsx_file)

    xlsx_bytes = xlsx_file.read_bytes()

    # 直接使用 bytes 验证 _validate_single_data_file
    ok, score, evidence, msg = _validate_single_data_file("artifacts/data.xlsx", xlsx_bytes, xlsx_file)
    assert ok is True
    assert score == 1.0
    assert "Summary" in evidence.get("sheets", [])

    # 使用 AssertionEngine 校验 csv_well_formed
    engine = AssertionEngine()
    rule = {
        "name": "structured_data_integrity",
        "type": "script_eval",
        "parameters": {"script_name": "csv_well_formed"},
    }
    artifacts_map = {"artifacts/data.xlsx": xlsx_bytes}
    res = engine.evaluate_suite(
        [rule],
        artifacts=artifacts_map,
        context={"workspace_dir": str(tmp_path)},
    )
    assert res.passed is True
    assert res.score == 100.0


def test_large_text_artifact_truncation(tmp_path):
    """验证超过 500 KiB 的大型文本文件保留键且仅读取前 500 KiB。"""
    txt_file = tmp_path / "artifacts" / "large.txt"
    txt_file.parent.mkdir(parents=True, exist_ok=True)
    txt_file.write_text("A" * (600 * 1024), encoding="utf-8")

    from evaluator.engine import BINARY_EXTENSIONS, TEXT_EXTENSIONS
    artifact_files = ["artifacts/large.txt"]
    workspace_dir = tmp_path
    max_bytes = 500 * 1024
    artifacts_map = {}
    for art_rel in artifact_files:
        art_full = workspace_dir / art_rel
        suffix = art_full.suffix.lower()
        is_binary = suffix in BINARY_EXTENSIONS and suffix not in TEXT_EXTENSIONS
        if is_binary:
            with art_full.open("rb") as f_bin:
                artifacts_map[art_rel] = f_bin.read(max_bytes)
        else:
            with art_full.open("r", encoding="utf-8", errors="replace") as f_txt:
                artifacts_map[art_rel] = f_txt.read(max_bytes)

    assert "artifacts/large.txt" in artifacts_map
    assert len(artifacts_map["artifacts/large.txt"]) == max_bytes
