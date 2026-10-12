"""脚本判定断言 (Script Eval) - 确定性零 LLM 实现"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path
from typing import Any

from .models import AssertionResult, AssertionRule, AssertionType


# 内置确定性判定函数注册表
def _eval_artifact_path_exists(rule: AssertionRule, text: str, artifacts: dict[str, Any], context: dict) -> tuple[bool, float, dict, str]:
    path_name = rule.parameters.get("path") or rule.target
    found = path_name in artifacts or any(k.endswith(f"/{path_name}") for k in artifacts)
    evidence = {"target_path": path_name, "found": found, "available_artifacts": list(artifacts.keys())}
    msg = f"产物文件存在: {path_name}" if found else f"产物文件缺失: {path_name}"
    return found, 1.0 if found else 0.0, evidence, msg


def _eval_artifact_extensions(rule: AssertionRule, text: str, artifacts: dict[str, Any], context: dict) -> tuple[bool, float, dict, str]:
    exts = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in rule.parameters.get("extensions", [])]
    found_exts = set()
    for art in artifacts.keys():
        suffix = Path(art).suffix.lower()
        if suffix in exts:
            found_exts.add(suffix)
    missing = [e for e in exts if e not in found_exts]
    passed = len(missing) == 0
    score = len(found_exts) / len(exts) if exts else 1.0
    evidence = {"required_extensions": exts, "found": list(found_exts), "missing": missing}
    msg = f"所有指定产物后缀均满足 ({len(found_exts)}/{len(exts)})" if passed else f"缺少产物文件类型: {missing}"
    return passed, score, evidence, msg


def _eval_completion_signal(rule: AssertionRule, text: str, artifacts: dict[str, Any], context: dict) -> tuple[bool, float, dict, str]:
    signals = ["任务完成", "已完成", "完成分析", "task completed", "successfully finished", "summary.md", "执行成功"]
    found = [s for s in signals if s in text.lower()]
    passed = len(found) > 0
    evidence = {"found_signals": found}
    msg = f"检测到任务完成信号: {found[:2]}" if passed else "未检测到明确的任务完成信号"
    return passed, 1.0 if passed else 0.0, evidence, msg


def _is_json_event_stream(text: str) -> bool:
    """判断字符串是否为 OpenCode/Agent 的原始 JSON 调度事件流"""
    if not text or not text.strip():
        return False
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return False
    event_lines = 0
    for line in lines[:15]:
        if (line.startswith('{"type":') or line.startswith('{"sessionID":') or 
            line.startswith('{"id":') or line.startswith('{"part":')):
            event_lines += 1
    return event_lines >= 1 and event_lines >= len(lines[:15]) * 0.4


def _validate_single_data_file(file_path: str, content: str) -> tuple[bool, float, dict, str]:
    """校验单个数据文件（CSV / JSON / XLSX）的结构完整性"""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(content)
            if data is None:
                return False, 0.0, {"file": file_path, "error": "JSON 内容为空"}, f"{file_path} 内容为 null"
            if isinstance(data, (dict, list)):
                item_count = len(data)
                return True, 1.0, {"file": file_path, "type": "json", "size": len(content), "items": item_count}, f"{file_path} 结构完整有效 (JSON 包含 {item_count} 项)"
            return True, 1.0, {"file": file_path, "type": "json", "size": len(content)}, f"{file_path} JSON 解析成功"
        except Exception as e:
            return False, 0.0, {"file": file_path, "parse_error": str(e)}, f"{file_path} JSON 解析失败: {e}"
    elif suffix == ".csv":
        try:
            f = io.StringIO(content)
            reader = list(csv.reader(f))
            if not reader or (len(reader) == 1 and not reader[0]):
                return False, 0.0, {"file": file_path, "error": "CSV 为空"}, f"{file_path} 内容为空"
            col_count = len(reader[0])
            inconsistent_rows = []
            for i, row in enumerate(reader):
                if len(row) != col_count:
                    inconsistent_rows.append(i + 1)
            if inconsistent_rows:
                return False, 0.5, {"file": file_path, "total_rows": len(reader), "inconsistent_rows": inconsistent_rows}, f"{file_path} CSV 列数不一致 (第 {inconsistent_rows[:3]} 行)"
            return True, 1.0, {"file": file_path, "rows": len(reader), "cols": col_count}, f"{file_path} CSV 格式规范 (共 {len(reader)} 行 {col_count} 列)"
        except Exception as e:
            return False, 0.0, {"file": file_path, "parse_error": str(e)}, f"{file_path} CSV 解析失败: {e}"
    elif suffix in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content.encode("latin1", errors="ignore") if isinstance(content, str) else content), read_only=True)
            sheets = wb.sheetnames
            return True, 1.0, {"file": file_path, "type": "xlsx", "sheets": sheets}, f"{file_path} Excel 格式规范 (工作表: {sheets[:3]})"
        except ImportError:
            if len(content) > 50:
                return True, 1.0, {"file": file_path, "type": "excel", "bytes": len(content)}, f"{file_path} Excel 数据文件存在"
            return False, 0.0, {"file": file_path, "error": "Excel 文件过小或损坏"}, f"{file_path} 文件异常"
        except Exception as e:
            return False, 0.0, {"file": file_path, "error": str(e)}, f"{file_path} Excel 解析失败: {e}"
    return True, 1.0, {"file": file_path, "status": "unknown_data_ext"}, f"{file_path} 数据文件已存在"


def _eval_csv_well_formed(rule: AssertionRule, text: str, artifacts: dict[str, Any], context: dict) -> tuple[bool, float, dict, str]:
    """结构化数据完整性校验 (CSV / JSON / XLSX)：
    优先在 artifacts 中寻找真实数据文件；若无数据文件产物则判 not_applicable（不扣分），杜绝硬解析原始事件流。
    """
    target_file = rule.parameters.get("path") or rule.target

    # 1. 显式指定具体产物文件名（非通配 target='output'）
    if target_file and target_file != "output":
        found_key = None
        if target_file in artifacts:
            found_key = target_file
        else:
            for k in artifacts:
                if k.endswith(f"/{target_file}") or k == target_file:
                    found_key = k
                    break
        if found_key:
            return _validate_single_data_file(found_key, str(artifacts[found_key]))
        else:
            must_exist = bool(rule.parameters.get("must_exist", True))
            if must_exist:
                return False, 0.0, {"missing_file": target_file}, f"指定的数据文件缺失: {target_file}"
            return True, 1.0, {"status": "not_applicable", "missing_file": target_file}, f"数据文件 {target_file} 未生成（非强制，不扣分）"

    # 2. 通配 target='output'：在 artifacts 中优先寻找真实数据文件 (.csv, .xlsx, .json)
    data_files = {}
    for k, v in artifacts.items():
        fname = Path(k).name.lower()
        if fname in ("result_manifest.json", "result_selection.json", "config.snapshot.json", "analysis.json", "anonymous_ids.json"):
            continue
        suffix = Path(k).suffix.lower()
        if suffix in (".csv", ".json", ".xlsx", ".xls"):
            data_files[k] = v

    # 3. 如果在 artifacts 中找到了结构化数据文件，逐一校验
    if data_files:
        passed_all = True
        details = []
        validated_files = []
        for k, content in data_files.items():
            ok_file, sc_file, ev_file, msg_file = _validate_single_data_file(k, str(content))
            validated_files.append({"file": k, "passed": ok_file, "evidence": ev_file, "message": msg_file})
            if not ok_file:
                passed_all = False
                details.append(f"{k}: {msg_file}")

        if passed_all:
            return True, 1.0, {"validated_files": validated_files}, f"结构化数据文件校验通过 (共 {len(data_files)} 个数据文件)"
        else:
            return False, 0.0, {"validated_files": validated_files, "errors": details}, f"数据文件校验未通过: {'; '.join(details[:3])}"

    # 4. 如果 artifacts 中无数据文件：
    # 检查 text 是否为单测中显式传递的 CSV 纯文本，且绝非 JSON 事件流
    if text and text.strip() and not _is_json_event_stream(text):
        if "," in text or "	" in text:
            return _validate_single_data_file("output.csv", text)

    # 5. 无任何数据文件产物（且 text 是事件流或无数据）：
    must_exist = bool(rule.parameters.get("must_exist", False))
    if must_exist:
        return False, 0.0, {"error": "未生成任何结构化数据文件"}, "未生成预期的结构化数据文件"

    evidence = {"status": "not_applicable", "reason": "产物中无数据文件（.csv/.xlsx/.json），不适用结构化数据完整性检查"}
    msg = "无数据文件产物，该规则不适用（不扣分）"
    return True, 1.0, evidence, msg


def _eval_balance_sheet(rule: AssertionRule, text: str, artifacts: dict[str, Any], context: dict) -> tuple[bool, float, dict, str]:
    # 资产 = 负债 + 所有者权益
    assets = float(rule.parameters.get("assets", 0.0))
    liabilities = float(rule.parameters.get("liabilities", 0.0))
    equity = float(rule.parameters.get("equity", 0.0))
    tolerance = float(rule.parameters.get("tolerance", 0.01))

    diff = abs(assets - (liabilities + equity))
    passed = diff <= tolerance
    evidence = {"assets": assets, "liabilities": liabilities, "equity": equity, "diff": diff}
    msg = "资产负债表平衡公式成立" if passed else f"资产负债表不平衡: 资产 {assets} != 负债+权益 {liabilities + equity} (差额 {diff})"
    return passed, 1.0 if passed else 0.0, evidence, msg


BUILTIN_SCRIPTS = {
    "artifact_path_exists": _eval_artifact_path_exists,
    "artifact_extensions": _eval_artifact_extensions,
    "completion_signal": _eval_completion_signal,
    "csv_well_formed": _eval_csv_well_formed,
    "structured_data_integrity": _eval_csv_well_formed,
    "balance_sheet": _eval_balance_sheet,
}


def evaluate_script_eval(
    rule: AssertionRule,
    target_content: Any,
    artifacts: dict[str, Any] = None,
    context: dict[str, Any] = None
) -> AssertionResult:
    """执行脚本判定"""
    params = rule.parameters
    script_name = params.get("script_name") or rule.name
    artifacts_map = artifacts or {}
    ctx = context or {}
    text = str(target_content or "")

    # 1. 尝试匹配内置脚本
    builtin_func = BUILTIN_SCRIPTS.get(script_name)
    if not builtin_func:
        # 支持按 target 类型退回检查
        if rule.target and rule.target != "output" and rule.target in artifacts_map:
            builtin_func = _eval_artifact_path_exists

    if builtin_func:
        try:
            passed, score, evidence, msg = builtin_func(rule, text, artifacts_map, ctx)
            return AssertionResult(
                name=rule.name,
                type=AssertionType.SCRIPT_EVAL.value,
                passed=passed,
                score=score,
                weight=rule.weight,
                evidence=evidence,
                message=msg,
            )
        except Exception as e:
            return AssertionResult(
                name=rule.name,
                type=AssertionType.SCRIPT_EVAL.value,
                passed=False,
                score=0.0,
                weight=rule.weight,
                evidence={"error": str(e)},
                message=f"内置脚本判定执行异常: {e}",
                error=str(e),
            )

    # 2. 自定义脚本文件执行
    script_path = params.get("script_path")
    if script_path:
        func_name = params.get("function_name", "evaluate")
        p = Path(script_path)
        if not p.exists():
            return AssertionResult(
                name=rule.name,
                type=AssertionType.SCRIPT_EVAL.value,
                passed=False,
                score=0.0,
                weight=rule.weight,
                evidence={"missing_script": str(p)},
                message=f"指定的判定脚本文件不存在: {script_path}",
                error="script_file_not_found",
            )
        try:
            spec = importlib.util.spec_from_file_location(p.stem, p)
            if not spec or not spec.loader:
                raise ImportError(f"无法加载模块 spec: {p}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            func = getattr(mod, func_name, None)
            if not callable(func):
                raise AttributeError(f"模块中找不到可调用的函数 '{func_name}'")

            out = func(target_content, artifacts_map, ctx)
            if isinstance(out, dict):
                passed = bool(out.get("passed", False))
                score = float(out.get("score", 1.0 if passed else 0.0))
                evidence = dict(out.get("evidence", {}))
                msg = str(out.get("text") or out.get("message") or ("脚本判定通过" if passed else "脚本判定未通过"))
            else:
                passed = bool(out)
                score = 1.0 if passed else 0.0
                evidence = {"return_val": str(out)}
                msg = "脚本判定通过" if passed else "脚本判定未通过"

            return AssertionResult(
                name=rule.name,
                type=AssertionType.SCRIPT_EVAL.value,
                passed=passed,
                score=score,
                weight=rule.weight,
                evidence=evidence,
                message=msg,
            )
        except Exception as e:
            return AssertionResult(
                name=rule.name,
                type=AssertionType.SCRIPT_EVAL.value,
                passed=False,
                score=0.0,
                weight=rule.weight,
                evidence={"script_error": str(e)},
                message=f"自定义判定脚本执行失败: {e}",
                error=str(e),
            )

    return AssertionResult(
        name=rule.name,
        type=AssertionType.SCRIPT_EVAL.value,
        passed=False,
        score=0.0,
        weight=rule.weight,
        evidence={"unknown_script": script_name},
        message=f"未找到指定的判定脚本或内置规则: {script_name}",
        error="script_not_found",
    )
