#!/usr/bin/env python3
"""示例：使用 SDK 评测 skill"""
import os
from evaluator import SkillEvaluator

# 配置
SKILL_PATH = "./recapture_photo_skill"
TASK_PROMPT = "给定一个包含门店照片 URL 的 Excel 文件，使用翻拍检测 skill 检测哪些照片是翻拍的"
MODELS = ["gpt-4", "claude-3", "qwen-2.5"]
VOTING_MODELS = ["gpt-4", "claude-3"]
SUMMARY_MODEL = "gpt-4"

def main():
    # 方式 1：使用 opencode 执行器
    print("=== 方式 1：使用 opencode 执行器 ===")
    evaluator = SkillEvaluator(
        skill_path=SKILL_PATH,
        executor_type="opencode"
    )
    
    # 检查连通性
    for model in MODELS:
        connected = evaluator.check_connectivity(model)
        print(f"  {model}: {'✅' if connected else '❌'}")
    
    # 方式 2：使用 API 执行器
    print("\n=== 方式 2：使用 API 执行器 ===")
    evaluator = SkillEvaluator(
        skill_path=SKILL_PATH,
        executor_type="api",
        provider="openai",
        api_key=os.environ.get("OPENAI_API_KEY", "test-key")
    )
    
    # 分析 skill
    analysis = evaluator.analyze_skill()
    print(f"  Skill 名称: {analysis['skill_name']}")
    print(f"  能力: {analysis['capability']['summary'][:80]}...")
    
    # 执行评测
    print("\n=== 执行评测 ===")
    result = evaluator.run(
        task_prompt=TASK_PROMPT,
        models=MODELS,
        voting_models=VOTING_MODELS,
        summary_model=SUMMARY_MODEL
    )
    
    if result["success"]:
        print(f"✅ 评测成功！")
        print(f"   报告: {result['report_path']}")
        print(f"   结论: {result['summary']['overall']}")
    else:
        print(f"❌ 评测失败: {result['error']}")

if __name__ == "__main__":
    main()
