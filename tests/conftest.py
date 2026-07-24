import logging
import sys
import types


if "astrbot.api" not in sys.modules:
    astrbot_module = types.ModuleType("astrbot")
    astrbot_api_module = types.ModuleType("astrbot.api")
    astrbot_event_module = types.ModuleType("astrbot.api.event")
    astrbot_star_module = types.ModuleType("astrbot.api.star")
    astrbot_provider_module = types.ModuleType("astrbot.api.provider")

    class AstrMessageEvent:
        pass

    class Context:
        pass

    class AstrBotConfig(dict):
        def save_config(self):
            pass

    class StarTools:
        @staticmethod
        def get_data_dir(plugin_name):
            from pathlib import Path

            return Path("data") / plugin_name

    class LLMResponse:
        pass

    astrbot_api_module.logger = logging.getLogger("astrbot-test")
    astrbot_api_module.AstrBotConfig = AstrBotConfig
    astrbot_event_module.AstrMessageEvent = AstrMessageEvent
    astrbot_star_module.Context = Context
    astrbot_star_module.StarTools = StarTools
    astrbot_provider_module.LLMResponse = LLMResponse
    astrbot_module.api = astrbot_api_module
    sys.modules.setdefault("astrbot", astrbot_module)
    sys.modules.setdefault("astrbot.api", astrbot_api_module)
    sys.modules.setdefault("astrbot.api.event", astrbot_event_module)
    sys.modules.setdefault("astrbot.api.star", astrbot_star_module)
    sys.modules.setdefault("astrbot.api.provider", astrbot_provider_module)
