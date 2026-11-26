import pytest
from evaluator.plain_language import verdict_to_plain_language, contains_untranslated_jargon

def test_verdict_to_plain_language_skill_effective():
    result = verdict_to_plain_language("skill_effective", n=10, mean_delta=2.5, primary_metric="assertion")
    assert "带这个 skill 的产出明显更好，建议开启。" in result
    assert "同一个任务对比了 10 轮" in result
    assert "平均多得 2.5 分" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_skill_effective_blind_vote():
    result = verdict_to_plain_language("skill_effective", n=10, mean_delta=2.5, primary_metric="blind_vote")
    assert "带这个 skill 的产出明显更好，建议开启。" in result
    assert "同一个任务对比了 10 轮" in result
    assert "平均多得 2.5 票" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_negative_migration():
    result = verdict_to_plain_language("negative_migration", n=5, mean_delta=-1.2, primary_metric="assertion")
    assert "带这个 skill 反而更差，建议在这个模型上关闭。" in result
    assert "同一个任务对比了 5 轮" in result
    assert "平均少得 1.2 分" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_inconclusive_sufficient_n():
    result = verdict_to_plain_language("inconclusive", n=10)
    assert "哪边" in result
    assert "只对比了" not in result
    assert "再" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_inconclusive_small_n():
    result = verdict_to_plain_language("inconclusive", n=3)
    assert "哪边" in result
    assert "只对比了 3 轮，还看不出哪边更好。" in result
    assert "再" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_unknown_verdict():
    result = verdict_to_plain_language("unknown_verdict", n=2)
    assert "哪边" in result
    assert "只对比了 2 轮，还看不出哪边更好。" in result
    assert not contains_untranslated_jargon(result)

def test_verdict_to_plain_language_zero_n():
    result = verdict_to_plain_language("skill_effective", n=0, mean_delta=2.5)
    assert result == "带这个 skill 的产出明显更好，建议开启。"

    result_neg = verdict_to_plain_language("negative_migration", n=0, mean_delta=-1.5)
    assert result_neg == "带这个 skill 反而更差，建议在这个模型上关闭。"

    result_inc = verdict_to_plain_language("inconclusive", n=0)
    assert "没有能配成对的结果" in result_inc

def test_verdict_to_plain_language_zero_mean_delta():
    result = verdict_to_plain_language("skill_effective", n=10, mean_delta=0.0)
    assert result == "带这个 skill 的产出明显更好，建议开启。"

    result_neg = verdict_to_plain_language("negative_migration", n=10, mean_delta=0.0)
    assert result_neg == "带这个 skill 反而更差，建议在这个模型上关闭。"
