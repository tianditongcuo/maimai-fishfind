"""鱼鱼找鸡插件配置模型。

MaiBot 会依据本模型生成 config.toml，并在 WebUI 里渲染成可编辑表单。
所有字段都带默认值——空配置也能直接跑（默认只用 trace.moe）。
"""

from pydantic import Field

from maibot_sdk.config import PluginConfigBase


class PluginSection(PluginConfigBase):
    """插件基础开关。"""

    __ui_label__ = "插件设置"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="是否启用鱼鱼找鸡。关闭后指令与工具都不响应。",
        json_schema_extra={"label": "启用插件"},
    )
    config_version: str = Field(
        default="1.0.0",
        description="配置版本号，仅用于追踪配置来源，运行时不做强制校验。",
        json_schema_extra={"label": "配置版本"},
    )
    display_name: str = Field(
        default="麦麦找鸡",
        description=(
            "显示名。用在回话标题（「🎯 麦麦找鸡结果」）和插件日志里。"
            "改它只影响文案，不影响指令；指令用下面的召唤词。"
        ),
        json_schema_extra={"label": "显示名"},
    )
    trigger_words: list[str] = Field(
        default_factory=lambda: ["麦麦找鸡", "鱼鱼找鸡"],
        description=(
            "召唤词。填「鱼鱼找鸡」就等于 /鱼鱼找鸡——前面自动加斜杠，不用自己写。"
            "可以随便改、随便加，比如「查图」「这是谁」。"
            "注意：命令正则是在插件加载时生成的，改完要在 WebUI 里重载插件（或重启 Maibot）才生效。"
        ),
        json_schema_extra={"label": "召唤词", "hint": "列表，例如 [\"鱼鱼找鸡\", \"麦麦找鸡\"]"},
    )


class EngineSection(PluginConfigBase):
    """反查引擎配置。"""

    __ui_label__ = "反查引擎"
    __ui_icon__ = "search"
    __ui_order__ = 1

    engines: list[str] = Field(
        default_factory=lambda: ["saucenao", "tracemoe"],
        description=(
            "要调用的引擎，按顺序执行。可选：saucenao（真人 cosplay / 同人图最有用，国内需走代理）、"
            "tracemoe（动漫截图溯源，国内直连可用）、iqdb（动漫图库，需代理）、"
            "lens（谷歌 Lens，需能访问谷歌）、baidu/bing/yandex（需公网图片地址，当前插件不提供）。"
            "只查动漫截图的话 tracemoe 一个就够；查真人照片必须靠 saucenao。"
        ),
        json_schema_extra={"label": "引擎列表", "hint": "建议 saucenao + tracemoe"},
    )
    proxy: str = Field(
        default="",
        description=(
            "反查引擎走的 HTTP 代理，例如 http://127.0.0.1:7890。留空 = 直连。"
            "SauceNAO / IQDB / 谷歌在国内通常必须走代理才通；"
            "trace.moe 会自动绕过代理直连（NO_PROXY），不受影响。"
        ),
        json_schema_extra={"label": "搜索引擎代理", "hint": "例如 http://127.0.0.1:7890；关掉代理时要清空"},
    )
    timeout_seconds: int = Field(
        default=45,
        ge=10,
        le=120,
        description="单个引擎的超时时间（秒）。谷歌 Lens 冷启动较慢，给它 45 秒以上。",
        json_schema_extra={"label": "单引擎超时（秒）"},
    )
    min_similarity: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "相似度门槛（0~1）。saucenao 对「只是长得像」的图也会给 30~50% 的匹配，"
            "低于这个值的一律不报，免得把你带到完全无关的画师主页。"
            "0.8 表示至少要 80% 相似；设 0 表示不筛选（会开始出现大量错配）。"
        ),
        json_schema_extra={"label": "相似度门槛", "hint": "0.8 = 80%，低于它的候选不报", "min": 0.0, "max": 1.0, "step": 0.05},
    )
    search_deadline_seconds: int = Field(
        default=75,
        ge=5,
        le=300,
        description=(
            "整次搜索的总时限（秒）。超过就强制结束、回一句「搜索超时」，"
            "并把还在跑的 node 进程杀掉。引擎开得多或单引擎超时调长时，"
            "这个值要大于最坏情况，否则容易误触发。"
        ),
        json_schema_extra={"label": "总搜索时限（秒）"},
    )
    max_accounts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="最多回几个账号。",
        json_schema_extra={"label": "最多账号数"},
    )
    strict_consensus: bool = Field(
        default=True,
        description="true = 标题里名字对不上的账号不报（少报不错报）；false = 全都报出来自己挑。",
        json_schema_extra={"label": "严格同名过滤"},
    )


class AccessSection(PluginConfigBase):
    """谁能用、能用几次。"""

    __ui_label__ = "使用范围"
    __ui_icon__ = "shield"
    __ui_order__ = 2

    group_whitelist: list[str] = Field(
        default_factory=list,
        description=(
            "允许使用的群号白名单。留空表示所有群都能用；填了之后只有列表里的群能用。"
            "建议至少在自己的群测好再放开——这个功能本质是「以图找人」。"
        ),
        json_schema_extra={"label": "群白名单", "hint": "留空 = 不限制"},
    )
    allow_private: bool = Field(
        default=True,
        description="是否允许私聊使用。",
        json_schema_extra={"label": "允许私聊"},
    )
    cooldown_seconds: int = Field(
        default=30,
        ge=0,
        le=600,
        description="同一个人在同一会话里的冷却时间（秒）。0 = 不冷却。",
        json_schema_extra={"label": "个人冷却（秒）"},
    )
    per_user_daily: int = Field(
        default=20,
        ge=0,
        le=1000,
        description="每人每天最多查几次。0 = 不限制。",
        json_schema_extra={"label": "每人每日上限"},
    )
    per_group_daily: int = Field(
        default=100,
        ge=0,
        le=10000,
        description="每个群每天最多查几次。0 = 不限制。",
        json_schema_extra={"label": "每群每日上限"},
    )


class RuntimeSection(PluginConfigBase):
    """运行时与文件路径。"""

    __ui_label__ = "运行时"
    __ui_icon__ = "cpu"
    __ui_order__ = 3

    node_path: str = Field(
        default="",
        description=(
            "node 可执行文件路径。留空则依次尝试：环境变量 PATH 里的 node、"
            "常见安装路径（Program Files\\nodejs、D:\\Nodejs 等）。需要 Node.js 20 及以上。"
        ),
        json_schema_extra={"label": "Node 路径", "hint": "留空自动查找"},
    )
    max_image_mb: int = Field(
        default=10,
        ge=1,
        le=30,
        description="允许处理的图片大小上限（MB）。超过直接拒绝，避免把内存吃满。",
        json_schema_extra={"label": "图片大小上限（MB）"},
    )


class ReplySection(PluginConfigBase):
    """回话样式。"""

    __ui_label__ = "回话"
    __ui_icon__ = "message-circle"
    __ui_order__ = 4

    announce: bool = Field(
        default=True,
        description="收到指令后先回一句「收到，开始找鸡」，避免长时间没反应。",
        json_schema_extra={"label": "先回执"},
    )
    max_chars: int = Field(
        default=1200,
        ge=200,
        le=4000,
        description="单条回复的最大字符数，超出会截断。",
        json_schema_extra={"label": "回复长度上限"},
    )


class FishFindConfig(PluginConfigBase):
    """鱼鱼找鸡总配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    engines: EngineSection = Field(default_factory=EngineSection)
    access: AccessSection = Field(default_factory=AccessSection)
    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    reply: ReplySection = Field(default_factory=ReplySection)


def create_config() -> FishFindConfig:
    """创建默认配置实例（供手动生成 config.toml 使用）。"""

    return FishFindConfig()
