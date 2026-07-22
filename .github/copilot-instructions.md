
# Project: astrbot_plugin_qq_group_daily_analysis

AstrBot plugin for daily QQ/Telegram/Discord group chat analysis & summarization using LLM.

## Architecture: DDD + Clean Architecture

```
src/
├── domain/              ← Pure business logic, NO infrastructure deps
│   ├── entities/        ← Aggregates (AnalysisTask, IncrementalState)
│   ├── value_objects/   ← Immutable (UnifiedMessage, UnifiedGroup, PlatformCapabilities)
│   ├── models/          ← DTOs (SummaryTopic, UserTitle, GoldenQuote)
│   ├── services/        ← Stateless domain services
│   ├── repositories/    ← ABC interfaces (I* prefix)
│   └── exceptions.py    ← DomainException hierarchy
├── application/         ← Use cases, orchestrates domain + infra
│   ├── services/        ← AnalysisApplicationService, MessageProcessingService
│   └── commands/
├── infrastructure/      ← Concrete impls of domain interfaces
│   ├── analysis/        ← LLMAnalyzer, BaseAnalyzer, etc.
│   ├── config/          ← ConfigManager
│   ├── messaging/       ← Cross-platform message dispatch
│   ├── persistence/     ← History, incremental store, registries
│   ├── platform/        ← PlatformAdapter ABC + adapters per platform
│   ├── reporting/       ← Templates, dispatcher, generators
│   ├── scheduler/       ← Auto-scheduling
│   └── visualization/
├── shared/              ← constants.py, trace_context.py
└── utils/               ← logger.py, resilience.py
```

## Naming Conventions

- **Files:** `snake_case` (`analysis_application_service.py`)
- **Classes:** `PascalCase` (`ConfigManager`, `AnalysisTask`)
- **Functions/methods:** `snake_case` (`analyze_topics()`, `_format_msg()`)
- **Private methods:** `_` prefix (`_get_group()`)
- **Constants:** `UPPER_SNAKE_CASE` (`PLUGIN_NAME`, `SUPPORTED_PLATFORMS`)
- **Abstract interfaces:** `I` prefix (`IAnalysisProvider`, `IMessageRepository`)
- **Test files:** `test_` + `snake_case` (`test_message_processing_service.py`)
- **Logger:** `logger` singleton from `src.utils.logger`

## Imports

- **Relative imports** (`.`) inside `src/`:
  ```python
  from ..value_objects.unified_message import UnifiedMessage
  from ...utils.logger import logger
  ```
- **Absolute imports** for AstrBot API:
  ```python
  from astrbot.api.event import filter, AstrMessageEvent
  from astrbot.api.star import Context, Star, register
  ```
- No wildcard imports. Prefer relative for internal, absolute for external.

## Type Hints

- Use `str | None`, `list[dict]`, `dict[str, int]` (Python 3.10+ union syntax)
- Use `TypedDict` for structured dicts
- Use `@dataclass(frozen=True)` for value objects
- Use `collections.abc` for abstract types (`AsyncGenerator`, `Callable`)

## Docstrings (Chinese)

```python
def method(self, arg: str) -> Result:
    """
    方法描述。

    Args:
        arg: 参数说明

    Returns:
        返回值说明

    Raises:
        SomeError: 异常说明
    """
```

## Error Handling

- Domain exceptions in `src/domain/exceptions.py`: `DomainException` → `AnalysisException` / `PlatformException`
- Always log errors with `exc_info=True`: `logger.error(f"msg", exc_info=True)`
- Use `CircuitBreaker` from `src/utils/resilience.py` for LLM calls

## Logging

```python
from src.utils.logger import logger
logger.info(f"[模块] 描述")
logger.error(f"[模块] 描述", exc_info=True)
```

## AstrBot Plugin Patterns

- Plugin class extends `Star`, registered with `@register(...)`
- Commands: `@filter.command("name", alias={"alt"})` on async methods
- Admin-only: `@filter.permission_type(filter.PermissionType.ADMIN)`
- Responses: `yield event.plain_result(...)` or `yield event.chain_result([...])`
- Suppress LLM: `event.should_call_llm(True)`
- Config: `_conf_schema.json` with groups; access via `self.config["key"]`

## DI Pattern

Manual constructor injection in `GroupDailyAnalysis.__init__`; no DI container.

## Platform Abstraction

`PlatformAdapter` ABC combines 4 domain interfaces. Concrete adapters in `infrastructure/platform/adapters/`. Cross-platform messages normalized to `UnifiedMessage` (frozen dataclass).

