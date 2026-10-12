"""可重算断言 (Recomputable) - 确定性零 LLM 实现"""
from __future__ import annotations

import ast
import csv
import io
import math
import operator as op
import re
from typing import Any

from .models import AssertionResult, AssertionRule, AssertionType

# 安全数学 AST 运算符
SAFE_OPERATORS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}


def _safe_eval_expr(expr_str: str, variables: dict[str, float]) -> float:
    """仅支持基础算术的确定性 AST 表达式计算器，绝对零 eval()"""
    def _eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        elif isinstance(node, ast.Name):
            if node.id in variables:
                return float(variables[node.id])
            raise ValueError(f"未定义的变量: {node.id}")
        elif isinstance(node, ast.BinOp):
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            op_func = SAFE_OPERATORS.get(type(node.op))
            if op_func is None:
                raise ValueError(f"不支持的操作符: {type(node.op).__name__}")
            return float(op_func(left, right))
        elif isinstance(node, ast.UnaryOp):
            operand = _eval_node(node.operand)
            op_func = SAFE_OPERATORS.get(type(node.op))
            if op_func is None:
                raise ValueError(f"不支持的一元操作符: {type(node.op).__name__}")
            return float(op_func(operand))
        raise ValueError(f"不支持的 AST 节点: {type(node).__name__}")

    tree = ast.parse(expr_str.strip(), mode="eval")
    return _eval_node(tree.body)


def _extract_number(val: Any) -> float | None:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # 移除逗号、百分号
        cleaned = val.replace(",", "").replace("%", "").strip()
        m = re.search(r"[-+]?(?:\d*\.\d+|\d+)", cleaned)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


def evaluate_recomputable(rule: AssertionRule, target_content: Any) -> AssertionResult:
    """执行确定性重算比对"""
    params = rule.parameters
    op_name = str(params.get("operator", "sum")).lower()
    tolerance = float(params.get("tolerance", 1e-3))
    text = str(target_content or "")

    # 获取参与计算的操作数
    operands = params.get("operands")
    csv_column = params.get("csv_column")

    # 如果指定了从 CSV/表格文本中提取一列
    if csv_column is not None and not operands:
        try:
            reader = csv.DictReader(io.StringIO(text))
            col_vals = []
            for row in reader:
                if str(csv_column) in row:
                    num = _extract_number(row[str(csv_column)])
                    if num is not None:
                        col_vals.append(num)
            operands = col_vals
        except Exception:
            operands = []

    # 规范化操作数为浮点数列表
    numeric_operands: list[float] = []
    if isinstance(operands, list):
        for item in operands:
            num = _extract_number(item)
            if num is not None:
                numeric_operands.append(num)
    elif isinstance(operands, (int, float)):
        numeric_operands = [float(operands)]

    # 获取声明结果 (claimed_result)
    claimed_raw = params.get("claimed_result")
    claimed_regex = params.get("claimed_regex")
    claimed_val = None

    if claimed_raw is not None:
        claimed_val = _extract_number(claimed_raw)
    elif claimed_regex:
        m = re.search(claimed_regex, text)
        if m:
            claimed_val = _extract_number(m.group(1) if m.groups() else m.group(0))

    if claimed_val is None:
        # 尝试在文本中通过断言名称附近定位数值
        claimed_val = _extract_number(text)

    if claimed_val is None:
        return AssertionResult(
            name=rule.name,
            type=AssertionType.RECOMPUTABLE.value,
            passed=False,
            score=0.0,
            weight=rule.weight,
            evidence={"operands": numeric_operands, "claimed_raw": claimed_raw},
            message="未能获取或解析输出中的声明数值 (claimed_result)",
            error="missing_claimed_result",
        )

    # 依据算子执行确定性计算
    recomputed_val: float | None = None
    calc_formula = ""

    try:
        if op_name in ("sum", "add"):
            recomputed_val = sum(numeric_operands)
            calc_formula = f"sum({numeric_operands})"
        elif op_name in ("mean", "average", "avg"):
            if not numeric_operands:
                raise ValueError("平均值计算操作数为空")
            recomputed_val = sum(numeric_operands) / len(numeric_operands)
            calc_formula = f"mean({numeric_operands})"
        elif op_name in ("ratio", "div"):
            if len(numeric_operands) < 2:
                raise ValueError("比例计算至少需要2个操作数")
            if numeric_operands[1] == 0:
                raise ZeroDivisionError("除数不能为0")
            recomputed_val = numeric_operands[0] / numeric_operands[1]
            calc_formula = f"{numeric_operands[0]} / {numeric_operands[1]}"
        elif op_name in ("percentage", "percent"):
            if len(numeric_operands) < 2:
                raise ValueError("百分比计算至少需要2个操作数")
            if numeric_operands[1] == 0:
                raise ZeroDivisionError("除数不能为0")
            recomputed_val = (numeric_operands[0] / numeric_operands[1]) * 100.0
            calc_formula = f"({numeric_operands[0]} / {numeric_operands[1]}) * 100"
        elif op_name in ("difference", "diff", "delta"):
            if len(numeric_operands) < 2:
                raise ValueError("差值计算至少需要2个操作数")
            recomputed_val = numeric_operands[0] - numeric_operands[1]
            calc_formula = f"{numeric_operands[0]} - {numeric_operands[1]}"
        elif op_name in ("growth_rate", "cagr"):
            # (end / begin) ** (1/n) - 1
            if len(numeric_operands) < 3:
                raise ValueError("CAGR 需要 [begin, end, periods]")
            begin, end, periods = numeric_operands[0], numeric_operands[1], numeric_operands[2]
            if begin <= 0 or periods <= 0:
                raise ValueError("CAGR 参数必须大于0")
            recomputed_val = (end / begin) ** (1.0 / periods) - 1.0
            calc_formula = f"({end}/{begin})^(1/{periods}) - 1"
        elif op_name in ("min",):
            if not numeric_operands:
                raise ValueError("操作数为空")
            recomputed_val = min(numeric_operands)
            calc_formula = f"min({numeric_operands})"
        elif op_name in ("max",):
            if not numeric_operands:
                raise ValueError("操作数为空")
            recomputed_val = max(numeric_operands)
            calc_formula = f"max({numeric_operands})"
        elif op_name in ("count",):
            recomputed_val = float(len(numeric_operands))
            calc_formula = f"count({numeric_operands})"
        elif op_name in ("custom_expr", "expr"):
            expr = str(params.get("expr", ""))
            var_dict = {f"x{i+1}": v for i, v in enumerate(numeric_operands)}
            if "variables" in params and isinstance(params["variables"], dict):
                var_dict.update({k: float(v) for k, v in params["variables"].items()})
            recomputed_val = _safe_eval_expr(expr, var_dict)
            calc_formula = f"expr({expr}) with {var_dict}"
        else:
            return AssertionResult(
                name=rule.name,
                type=AssertionType.RECOMPUTABLE.value,
                passed=False,
                score=0.0,
                weight=rule.weight,
                evidence={"unknown_operator": op_name},
                message=f"未知的可重算操作符: {op_name}",
                error="unknown_operator",
            )
    except Exception as e:
        return AssertionResult(
            name=rule.name,
            type=AssertionType.RECOMPUTABLE.value,
            passed=False,
            score=0.0,
            weight=rule.weight,
            evidence={"calc_error": str(e), "operator": op_name},
            message=f"可重算确定性计算失败: {e}",
            error=str(e),
        )

    delta = abs(recomputed_val - claimed_val)
    passed = delta <= tolerance
    evidence = {
        "operator": op_name,
        "formula": calc_formula,
        "operands_count": len(numeric_operands),
        "recomputed_value": round(recomputed_val, 6),
        "claimed_value": round(claimed_val, 6),
        "delta": round(delta, 6),
        "tolerance": tolerance,
    }

    if passed:
        msg = f"可重算核验一致: 重算值 {recomputed_val:.4f} == 声明值 {claimed_val:.4f} (容差 {tolerance})"
    else:
        msg = f"可重算核验不一致: 重算值 {recomputed_val:.4f} != 声明值 {claimed_val:.4f} (差值 {delta:.6f} > 容差 {tolerance})"

    return AssertionResult(
        name=rule.name,
        type=AssertionType.RECOMPUTABLE.value,
        passed=passed,
        score=1.0 if passed else 0.0,
        weight=rule.weight,
        evidence=evidence,
        message=msg,
    )
