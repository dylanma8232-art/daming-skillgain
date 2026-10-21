import pytest
from evaluator.plain_language import verdict_to_plain_language, contains_untranslated_jargon

def test_verdict_to_plain_language_skill_effective():
    result = verdict_to_plain_language("skill_effective", n=10, mean_delta=2.5, primary_metric="assertion")
    assert "带这个 skill 的产出明显更好，建议开启。" in result
    assert "10 轮同样的任务里" in result
    assert "平均多得高 2.5" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_skill_effective_blind_vote():
    result = verdict_to_plain_language("skill_effective", n=10, mean_delta=2.5, primary_metric="blind_vote")
    assert "带这个 skill 的产出明显更好，建议开启。" in result
    assert "10 轮同样的任务里" in result
    assert "平均多拿高 2.5" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_negative_migration():
    result = verdict_to_plain_language("negative_migration", n=5, mean_delta=-1.2, primary_metric="assertion")
    assert "带这个 skill 反而更差，建议在这个模型上关闭。" in result
    assert "5 轮同样的任务里" in result
    assert "平均多得低 1.2" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_inconclusive_sufficient_n():
    result = verdict_to_plain_language("inconclusive", n=10)
    assert "两者差距不大、或结果波动太大，这次没测出明显区别。" in result
    assert "而且只对比了" not in result
    assert "想进一步确认" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_inconclusive_small_n():
    result = verdict_to_plain_language("inconclusive", n=3)
    assert "两者差距不大、或结果波动太大，这次没测出明显区别。" in result
    assert "而且只对比了 3 轮，轮数偏少。" in result
    assert "想进一步确认" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_unknown_verdict():
    result = verdict_to_plain_language("unknown_verdict", n=2)
    assert "两者差距不大、或结果波动太大，这次没测出明显区别。" in result
    assert "而且只对比了 2 轮，轮数偏少。" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_zero_n():
    result = verdict_to_plain_language("skill_effective", n=0, mean_delta=2.5)
    assert result == "带这个 skill 的产出明显更好，建议开启。"

    result_neg = verdict_to_plain_language("negative_migration", n=0, mean_delta=-1.5)
    assert result_neg == "带这个 skill 反而更差，建议在这个模型上关闭。"

    result_inc = verdict_to_plain_language("inconclusive", n=0)
    assert "而且只对比了" not in result_inc

def test_verdict_to_plain_language_zero_mean_delta():
    result = verdict_to_plain_language("skill_effective", n=10, mean_delta=0.0)
    assert result == "带这个 skill 的产出明显更好，建议开启。"

    result_neg = verdict_to_plain_language("negative_migration", n=10, mean_delta=0.0)
    assert result_neg == "带这个 skill 反而更差，建议在这个模型上关闭。"
