#!/usr/bin/env python3
"""交互式配置和执行评测"""
import json
from evaluator import SkillEvaluator, ModelDiscovery, interactive_setup

def main():
    print("=" * 60)
    print("🎯 Skill Evaluator - 交互式配置")
    print("=" * 60)
    
    # 获取 skill 路径
    skill_path = input("\n请输入 skill 路径: ").strip()
    if not skill_path:
        skill_path = "./recapture_photo_skill"
        print(f"使用默认路径: {skill_path}")
    
    # 自动检测
    print("\n🔍 正在检测本地工具...")
    discovery = ModelDiscovery()
    result = discovery.discover()
    
    if result["has_local"]:
        print(f"\n✅ 检测到 {len(result['tools'])} 个本地工具:")
        for i, tool in enumerate(result["tools"], 1):
            print(f"   {i}. {tool['name']} ({len(tool['models'])} 个模型)")
        
        # 选择工具
        tool_choice = input("\n选择工具编号（直接回车使用第一个）: ").strip()
        if tool_choice and tool_choice.isdigit():
            idx = int(tool_choice) - 1
            selected_tool = result["tools"][idx]
        else:
            selected_tool = result["tools"][0]
        
        print(f"\n使用工具: {selected_tool['name']}")
        
        # 选择模型
        print(f"\n可用模型（前 10 个）:")
        for i, model in enumerate(selected_tool["models"][:10], 1):
            print(f"   {i}. {model}")
        
        if len(selected_tool["models"]) > 10:
            print(f"   ... 还有 {len(selected_tool['models']) - 10} 个")
        
        model_choice = input("\n选择模型编号（直接回车使用第一个）: ").strip()
        if model_choice and model_choice.isdigit():
            idx = int(model_choice) - 1
            selected_model = selected_tool["models"][idx]
        else:
            selected_model = selected_tool["models"][0]
        
        print(f"\n使用模型: {selected_model}")
        
        # 初始化 evaluator
        evaluator = SkillEvaluator(
            skill_path=skill_path,
            executor_type=selected_tool["name"],
            auto_discover=False
        )
        
    elif result["has_api_key"]:
        print(f"\n✅ 检测到 {len(result['api_keys'])} 个 API key:")
        for provider in result["api_keys"]:
            print(f"   - {provider}")
        
        # 选择 provider
        provider_choice = input("\n选择 provider（直接回车使用第一个）: ").strip()
        providers = list(result["api_keys"].keys())
        if provider_choice and provider_choice.isdigit():
            idx = int(provider_choice) - 1
            selected_provider = providers[idx]
        else:
            selected_provider = providers[0]
        
        # 选择模型
        default_models = {
            "openai": ["gpt-4", "gpt-4o", "gpt-3.5-turbo"],
            "anthropic": ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"],
            "minimax": ["MiniMax-M2.7", "MiniMax-M2.5"],
            "dashscope": ["qwen-max", "qwen-plus", "qwen-turbo"]
        }
        
        models = default_models.get(selected_provider, ["default-model"])
        print(f"\n可用模型:")
        for i, model in enumerate(models, 1):
            print(f"   {i}. {model}")
        
        model_choice = input("\n选择模型编号（直接回车使用第一个）: ").strip()
        if model_choice and model_choice.isdigit():
            idx = int(model_choice) - 1
            selected_model = models[idx]
        else:
            selected_model = models[0]
        
        # 初始化 evaluator
        evaluator = SkillEvaluator(
            skill_path=skill_path,
            executor_type="api",
            executor_config={
                "provider": selected_provider,
                "api_key": result["api_keys"][selected_provider]
            },
            auto_discover=False
        )
        
    else:
        print("\n⚠️  未检测到本地工具或 API key")
        print("\n📝 请手动配置:")
        
        print("\n支持的模型提供商:")
        print("   1. OpenAI (需要 API key)")
        print("   2. Anthropic (需要 API key)")
        print("   3. MiniMax (需要 API key)")
        print("   4. 通义千问 (需要 API key)")
        print("   5. 本地模型 (需要 URL)")
        
        provider_choice = input("\n选择提供商编号: ").strip()
        
        provider_map = {
            "1": "openai",
            "2": "anthropic",
            "3": "minimax",
            "4": "dashscope",
            "5": "local"
        }
        
        provider = provider_map.get(provider_choice, "openai")
        
        if provider == "local":
            url = input("本地模型 URL (如 http://localhost:11434): ").strip()
            model = input("模型名称: ").strip()
            
            evaluator = SkillEvaluator(
                skill_path=skill_path,
                executor_type="api",
                executor_config={
                    "provider": "openai",
                    "base_url": url,
                    "api_key": "not-needed"
                },
                auto_discover=False
            )
            selected_model = model
        else:
            api_key = input(f"{provider} API key: ").strip()
            
            default_models = {
                "openai": ["gpt-4", "gpt-4o", "gpt-3.5-turbo"],
                "anthropic": ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"],
                "minimax": ["MiniMax-M2.7", "MiniMax-M2.5"],
                "dashscope": ["qwen-max", "qwen-plus", "qwen-turbo"]
            }
            
            models = default_models.get(provider, ["default-model"])
            print(f"\n可用模型:")
            for i, model in enumerate(models, 1):
                print(f"   {i}. {model}")
            
            model_choice = input("\n选择模型编号（直接回车使用第一个）: ").strip()
            if model_choice and model_choice.isdigit():
                idx = int(model_choice) - 1
                selected_model = models[idx]
            else:
                selected_model = models[0]
            
            evaluator = SkillEvaluator(
                skill_path=skill_path,
                executor_type="api",
                executor_config={
                    "provider": provider,
                    "api_key": api_key
                },
                auto_discover=False
            )
    
    # 执行评测
    print("\n" + "=" * 60)
    print("🚀 开始评测")
    print("=" * 60)
    
    task_prompt = input("\n请输入评测任务描述: ").strip()
    if not task_prompt:
        task_prompt = "评测这个 skill 的功能"
        print(f"使用默认任务: {task_prompt}")
    
    print(f"\n使用模型: {selected_model}")
    print(f"任务: {task_prompt}")
    
    confirm = input("\n确认执行？(y/n): ").strip().lower()
    if confirm != 'y':
        print("已取消")
        return
    
    result = evaluator.run(
        task_prompt=task_prompt,
        models=[selected_model],
        voting_models=[selected_model],
        summary_model=selected_model,
        skip_connectivity_test=True,
        parallelism=1
    )
    
    if result["success"]:
        print("\n" + "=" * 60)
        print("✅ 评测完成！")
        print("=" * 60)
        print(f"\n报告: {result['report_path']}")
        print(f"\n结论: {result['summary'].get('overall', '无')}")
    else:
        print(f"\n❌ 评测失败: {result['error']}")

if __name__ == "__main__":
    main()
