"""麦麦找鸡 —— 图片反查（以图找人 / 找出处）。

上游原项目是「鱼鱼找鸡 v1.0.0」（作者：小鲸鱼，MIT），本目录是它的 Maibot 封装。

本插件本身不实现反查算法：它把收到的图片交给 ``vendor/fish`` 里的 Node 命令行
（上游原项目「鱼鱼找鸡」的裁剪版）跑一遍，再把结构化结果转成一句人话发回群里。

为什么是外挂进程而不是纯 Python 重写：
    trace.moe / SauceNAO / 谷歌 Lens 的适配、无头浏览器驱动、结果清洗都在那份
    Node 代码里，重写一遍既没必要也更容易出问题。这里只做「取图 → 调用 → 回话」。

设计取舍：
    * 只接受**消息里的图片**，不接受路径参数 —— 原项目的 OneBot 适配层会把指令
      参数当本地路径读文件，群成员可以借此读走服务器上的任意文件，这里不提供该入口。
    * 检索串行执行（同一时间一个 node 进程），避免群多了把免费引擎刷爆。
    * 配额与冷却在插件侧做，不依赖引擎自己限流。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from maibot_sdk import Command, MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .config import FishFindConfig

PLUGIN_ROOT = Path(__file__).resolve().parent
VENDOR_DIR = PLUGIN_ROOT / "vendor" / "fish"
CLI_PATH = VENDOR_DIR / "bin" / "fish.mjs"

# 找不到 node 时的兜底候选（Windows 常见安装位置）。
NODE_CANDIDATES = (
    r"C:\Program Files\nodejs\node.exe",
    r"C:\Program Files (x86)\nodejs\node.exe",
    r"D:\Nodejs\node.exe",
    r"D:\Program Files\nodejs\node.exe",
)

# 命令正则是在插件加载时注册给 Maibot 的常量，改不了；所以召唤词只能在 import 阶段
# 直接从 config.toml 读一次。改完召唤词要重载插件才生效，on_config_update 里会提示。
DEFAULT_DISPLAY_NAME = "麦麦找鸡"
DEFAULT_TRIGGER_WORDS = ("麦麦找鸡", "鱼鱼找鸡")


def _normalize_trigger(word: Any) -> str:
    """把召唤词收拾干净：去空白、去掉用户自己敲的前导斜杠、限长。"""

    text = str(word or "").strip()
    while text.startswith("/"):
        text = text[1:].strip()
    return text[:20]


def load_trigger_words(root: Path = PLUGIN_ROOT) -> list[str]:
    """读 config.toml 里的 trigger_words；读不到就用默认值。"""

    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        return list(DEFAULT_TRIGGER_WORDS)
    try:
        raw_text = (Path(root) / "config.toml").read_text(encoding="utf-8-sig")
        data = tomllib.loads(raw_text)
    except (OSError, ValueError):
        return list(DEFAULT_TRIGGER_WORDS)

    section = data.get("plugin")
    raw = section.get("trigger_words") if isinstance(section, dict) else None
    words: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        word = _normalize_trigger(item)
        if word and word not in words:
            words.append(word)
    return words[:10] or list(DEFAULT_TRIGGER_WORDS)


def build_command_pattern(words: list[str]) -> str:
    """把召唤词拼成整句匹配的正则；空列表时退回默认词。"""

    cleaned: list[str] = []
    for item in words:
        word = _normalize_trigger(item)
        if word and word not in cleaned:
            cleaned.append(word)
    if not cleaned:
        cleaned = list(DEFAULT_TRIGGER_WORDS)
    # Maibot 会往 processed_plain_text 前面拼占位前缀：
    #   引用图片后发指令 → "[image] /麦麦找鸡"
    #   图与指令同一条   → "[图片：图中一位…] /麦麦找鸡"
    #   引用表情包       → "[表情包: 无语,尴尬] /麦麦找鸡"
    #   先 @ 机器人再发  → "@顾若若  麦麦找鸡"
    # 所以不能只认「以 / 开头」。这里允许任意个方括号占位/@昵称前缀，
    # 也允许省略斜杠（喊名字就当召唤）；仍要求指令占满整句，避免误触闲聊。
    prefix = r"(?:(?:\[[^\]]{0,300}\]|@\S{1,40})\s*)*"
    body = r"/?(?:" + "|".join(re.escape(word) for word in cleaned) + r")"
    return r"^\s*" + prefix + body + r"[\s。．.!！?？~～]*$"


TRIGGER_WORDS = tuple(load_trigger_words())
COMMAND_PATTERN = build_command_pattern(list(TRIGGER_WORDS))
COMMAND_DESCRIPTION = (
    "以图反查 coser / 图片出处（引用带图消息后发送）。"
    f"当前召唤词：{' '.join('/' + word for word in TRIGGER_WORDS)}（可在配置里改）"
)

# 图片离现在超过这个秒数就不再自动采用（工具被 LLM 延迟调用时不至于拿错图）。
IMAGE_FRESH_SECONDS = 600

# 搜索超过这个秒数还没结果，就先回一句「还在查」，避免用户以为卡死。
PROGRESS_AFTER_SECONDS = 25

# LLM 实际只看到 brief（ToolSpec.description 取的就是它），所以关键约束必须写在 brief 里；
# detailed 只在组件详情/WebUI 里展示，用来放完整说明。
TOOL_BRIEF = (
    "以图反查聊天中最近的一张图片，说出它的出处、画师或 coser 的社交账号。"
    "只读取聊天里已有的图片，不要传图片 URL；返回文本已是给用户看的结论，直接转述；"
    "查不到是常态，不要反复重试。"
)

TOOL_DESCRIPTION = (
    "以图反查：把聊天里最近的一张图片交给多个搜图引擎，找出它的出处、画师或 coser 的社媒账号。\n"
    "使用场景：用户发了一张图片（cosplay、动漫截图、插画），并问「这是谁」「哪个 coser」"
    "「出自哪部番」「原图在哪」。\n"
    "注意：\n"
    "1. 本工具只读取**聊天里已有的图片**，不需要也不要传图片 URL；\n"
    "2. 图片会被上传到第三方反查站点（trace.moe 等），这是反查的固有代价，必要时先告知用户；\n"
    "3. 返回文本已经是给用户看的结论，直接转述即可，不要复述内部字段；\n"
    "4. 查不到属于正常情况（冷门图、真人照片、引擎不可用），不要反复重试或换关键词。"
)


# Node 侧抛上来的原始错误五花八门，这里翻译成人能看懂的原因。
REASON_HINTS = (
    ("GOOGLE_UNREACHABLE", "谷歌连不上（这台机器访问不了谷歌）"),
    ("GOOGLE_CAPTCHA", "谷歌要求人机验证"),
    ("RATE_LIMITED", "被引擎限流，等一会儿再试"),
    ("fetch failed", "网络连不上（域名不通或连接被重置）"),
    ("ENOTFOUND", "域名解析失败"),
    ("ETIMEDOUT", "连接超时"),
    ("timeout", "超时"),
    ("403", "被引擎拒绝（限流或反爬）"),
)


# 这些原因属于“再试一次可能就好了”，值得重试一次。
TRANSIENT_MARKERS = ("fetch failed", "ENOTFOUND", "ETIMEDOUT", "连接被重置", "网络连不上", "超时")


def _is_transient(error: BaseException) -> bool:
    """判断失败是否属于网络抖动（可以重试）。"""

    text = str(error)
    return any(marker in text for marker in TRANSIENT_MARKERS)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTP 重定向时逐跳重新校验目标地址。

    urllib 默认会自动跟随 3xx，如果不拦，一个公网 URL 就能把我们带到
    127.0.0.1 / 169.254.169.254 这类内网地址上。这里把每一跳都交给
    ``validate(new_url)`` 检查，不通过就中止下载。
    """

    def __init__(self, validate: Callable[[str], str]) -> None:
        self._validate = validate

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        target = urllib.parse.urljoin(getattr(req, "full_url", "") or "", str(newurl))
        reason = self._validate(target)
        if reason:
            raise urllib.error.URLError(f"重定向被拒绝：{target}（{reason}）")
        return super().redirect_request(req, fp, code, msg, headers, target)


def _reason_hint(reason: str) -> str:
    """把引擎的原始报错翻成一句人话；翻不了就原样带上。"""

    text = str(reason or "").strip()
    for marker, hint in REASON_HINTS:
        if marker.lower() in text.lower():
            return f"{hint}（{text[:80]}）"
    return text[:120] or "未知原因"


class FishFindError(RuntimeError):
    """可预期的失败（给用户看的文案由异常消息携带）。"""


class FishFindPlugin(MaiBotPlugin):
    """麦麦找鸡插件。"""

    config_model = FishFindConfig

    def __init__(self) -> None:
        super().__init__()
        self._node: str = ""
        self._tasks: set[asyncio.Task[Any]] = set()
        self._search_gate = asyncio.Semaphore(1)
        self._cooldowns: dict[str, float] = {}
        self._memory_quota: dict[str, Any] = {}
        self._recent_triggers: dict[str, float] = {}
        self._ready = False

    # ── 生命周期 ──────────────────────────────────────────────────────────

    async def on_load(self) -> None:
        """解析 node 路径并准备数据目录。"""

        self._node = self._resolve_node()
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - 目录不可写时只记录
            self.ctx.logger.warning(f"{self._title()}：数据目录不可写（%s）：%s", self._data_dir, exc)

        if not self._node:
            self.ctx.logger.error(
                f"{self._title()}：找不到 node 可执行文件。请安装 Node.js 20+，或在 WebUI 配置里填 runtime.node_path。"
            )
        elif not CLI_PATH.is_file():
            self.ctx.logger.error(f"{self._title()}：找不到命令行入口 %s，插件包可能不完整。", CLI_PATH)
        else:
            self.ctx.logger.info(
                "%s已就绪：node=%s，召唤词=%s",
                self._title(),
                self._node,
                " ".join("/" + word for word in TRIGGER_WORDS),
            )
        self._ready = bool(self._node) and CLI_PATH.is_file()

    async def on_unload(self) -> None:
        """取消后台任务并收掉还在跑的 node。"""

        pending = list(self._tasks)
        for task in pending:
            task.cancel()
        self._tasks.clear()
        if pending:
            # 取消会在 _run_cli 里触发 kill，这里只留一行记录，方便对上「没回消息」的时间点
            self.ctx.logger.info("%s：插件卸载，已取消 %d 个进行中的搜索", self._title(), len(pending))

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热重载后重新解析 node 路径；召唤词变了要提示重载。"""

        self._node = self._resolve_node()
        self.ctx.logger.info(f"{self._title()}配置已更新（scope=%s, version=%s）", scope, version)

        wanted = {_normalize_trigger(item) for item in self._cfg().plugin.trigger_words}
        wanted.discard("")
        if wanted and wanted != set(TRIGGER_WORDS):
            self.ctx.logger.warning(
                f"{self._title()}：召唤词已改成 %s，但命令正则是加载时注册的，"
                "需要在 WebUI 里重载插件（或重启 Maibot）才会生效；当前生效的仍是 %s",
                " ".join("/" + word for word in sorted(wanted)),
                " ".join("/" + word for word in TRIGGER_WORDS),
            )

    # ── 组件：LLM 工具 ────────────────────────────────────────────────────

    @Tool(
        "fish_find",
        brief_description=TOOL_BRIEF,
        detailed_description=TOOL_DESCRIPTION,
        visibility="visible",
        parameters=[
            ToolParameterInfo(
                name="image_message_id",
                param_type=ToolParamType.STRING,
                description="指定要反查的消息 ID；不填则自动取当前会话里最新的一张图。",
                required=False,
            ),
        ],
        timeout_ms=150000,
    )
    async def handle_fish_find(self, image_message_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """供 LLM 调用的反查工具。"""

        cfg = self._cfg()
        stream_id = str(kwargs.get("chat_id") or kwargs.get("stream_id") or "").strip()
        group_id = str(kwargs.get("group_id") or "").strip()
        user_id = str(kwargs.get("user_id") or "").strip()

        if not cfg.plugin.enabled:
            return {"content": f"{self._title()}插件当前已关闭。"}
        denial = self._access_denial(group_id)
        if denial:
            return {"content": denial}
        if not stream_id:
            return {"content": "拿不到当前会话 ID，无法取图。"}
        if not self._ready:
            return {"content": f"{self._title()}还没准备好：服务器上找不到 node 可执行文件。"}
        wait_text = self._quota_denial(user_id, group_id)
        if wait_text:
            return {"content": wait_text}

        try:
            image = await self._image_for_tool(stream_id, image_message_id)
        except FishFindError as exc:
            return {"content": str(exc)}

        self._charge_quota(user_id, group_id)
        try:
            text = await self._search_with_deadline(image[0])
        except FishFindError as exc:
            return {"content": str(exc)}
        return {"content": text}

    # ── 组件：斜杠指令（兜底，不依赖 LLM 判断） ──────────────────────────

    @Command("fishfind", description=COMMAND_DESCRIPTION, pattern=COMMAND_PATTERN)
    async def handle_fishfind_command(self, **kwargs: Any) -> tuple[bool, str, int]:
        """``/麦麦找鸡`` 指令：立刻回执，后台查完再发结果。"""

        cfg = self._cfg()
        stream_id = str(kwargs.get("stream_id") or "").strip()
        group_id = str(kwargs.get("group_id") or "").strip()
        user_id = str(kwargs.get("user_id") or "").strip()
        message = kwargs.get("message") if isinstance(kwargs.get("message"), dict) else {}

        if not stream_id:
            return True, "", 2
        if not cfg.plugin.enabled:
            await self._send(stream_id, f"{self._title()}现在是关的（config.toml 的 plugin.enabled）")
            return True, "", 2
        denial = self._access_denial(group_id)
        if denial:
            await self._send(stream_id, denial)
            return True, "", 2
        if not self._ready:
            await self._send(stream_id, f"{self._title()}还没准备好：服务器上找不到 node 可执行文件。")
            return True, "", 2
        wait_text = self._quota_denial(user_id, group_id)
        if wait_text:
            await self._send(stream_id, wait_text)
            return True, "", 2

        # 同一条消息可能被网关/框架重复投递：60 秒内只处理一次，避免并发跑两遍 node。
        message_id = str(message.get("message_id") or "").strip()
        if message_id:
            now = time.monotonic()
            key = f"{stream_id}:{message_id}"
            last = self._recent_triggers.get(key)
            if last is not None and now - last < 60:
                self.ctx.logger.info("%s：忽略重复触发（同一消息 60 秒内）", self._title())
                return True, "", 2
            self._recent_triggers[key] = now
            if len(self._recent_triggers) > 200:
                cutoff = now - 300
                self._recent_triggers = {k: v for k, v in self._recent_triggers.items() if v >= cutoff}

        # 先把图取到（可能要在落盘前重试几秒），拿到图再回执，避免「收到」之后又说没图。
        try:
            image = await self._image_for_command(message, stream_id)
        except FishFindError as exc:
            await self._send(stream_id, f"❌ {exc}")
            return True, "", 2

        if cfg.reply.announce:
            await self._send(stream_id, "收到，开始找鸡 🐔")
        task = asyncio.create_task(self._command_worker(image[0], stream_id, user_id, group_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True, "", 2

    # ── 后台执行 ──────────────────────────────────────────────────────────

    async def _command_worker(self, image: bytes, stream_id: str, user_id: str, group_id: str) -> None:
        """后台检索，把结果（或超时提示）发回会话。"""

        search = asyncio.create_task(self._search_with_deadline(image))
        try:
            self._charge_quota(user_id, group_id)
            # 搜得久的时候先说一声，别让人以为卡死了（Lens 正常也要 20~60 秒）
            done, _ = await asyncio.wait({search}, timeout=PROGRESS_AFTER_SECONDS)
            if not done and self._cfg().reply.announce:
                await self._send(
                    stream_id,
                    f"⏳ 还在查，已经 {PROGRESS_AFTER_SECONDS} 秒了；Lens 通常要 20~60 秒，超过 "
                    f"{self._deadline_seconds()} 秒会自动放弃。",
                )
            text = await search
        except FishFindError as exc:
            text = f"❌ {exc}"
        except asyncio.CancelledError:
            search.cancel()
            raise
        except Exception as exc:  # pragma: no cover - 兜底，避免后台任务静默死掉
            self.ctx.logger.exception(f"{self._title()}后台任务异常")
            text = f"❌ 查的时候出错了：{exc}"
        await self._send(stream_id, text)

    # ── 核心：调用 Node CLI ───────────────────────────────────────────────

    def _deadline_seconds(self) -> int:
        """整次搜索的总时限（秒）。"""

        try:
            return max(5, int(self._cfg().engines.search_deadline_seconds))
        except (TypeError, ValueError, RuntimeError):
            return 75

    async def _search_with_deadline(self, image: bytes) -> str:
        """给整次检索套一个硬性时限：到点就放弃，并保证子进程被收掉。"""

        deadline = self._deadline_seconds()
        try:
            return await asyncio.wait_for(self._search_and_format(image), timeout=deadline)
        except asyncio.TimeoutError:
            raise FishFindError(
                f"搜索超时：{deadline} 秒内没拿到结果，这次先放弃。稍后再试，或换张更清晰的图。"
            ) from None

    @staticmethod
    def _kill_quietly(proc: Any) -> None:
        """杀掉子进程**树**并让事件循环回收它（不阻塞调用方）。

        为什么用 taskkill /T：lens 引擎会由 node 再派生一个无头 Edge，
        只杀 node 会留下占着 profile 目录的孤儿；/PID 指向的是我们自己刚起的那个
        node，/T 只带走它的子孙，不会误伤用户自己的浏览器。
        """

        if proc is None or proc.returncode is not None:
            return
        pid = getattr(proc, "pid", 0)
        if os.name == "nt" and pid:
            _spawn_helper(asyncio.to_thread(_kill_tree_sync, pid))
        with_suppress(proc.kill)
        _spawn_helper(_reap_process(proc))

    async def _search_and_format(self, image: bytes) -> str:
        """把图片写进临时文件，调用 node CLI，返回整理好的回话文本。"""

        cfg = self._cfg()
        engines = self._engines()
        timeout_s = int(cfg.engines.timeout_seconds)
        # 记下这张图是谁：以后「不同图却查到同一个人」可以先看这条日志对不对得上。
        try:
            digest = hashlib.sha1(image).hexdigest()[:12]
            self.ctx.logger.info(
                "%s：开始搜索 图片 sha1=%s size=%dKB 引擎=%s",
                self._title(), digest, max(1, len(image) // 1024), ",".join(engines),
            )
        except Exception:  # 日志失败不能影响搜索
            pass

        suffix = ".jpg"
        if image[:4] == b"\x89PNG":
            suffix = ".png"
        elif image[:3] == b"GIF":
            suffix = ".gif"
        elif image[:4] == b"RIFF" and image[8:12] == b"WEBP":
            suffix = ".webp"

        tmp_dir = self._data_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="fishfind-", suffix=suffix, dir=str(tmp_dir))
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            tmp_path.write_bytes(image)
            async with self._search_gate:
                try:
                    payload = await self._run_cli(tmp_path, engines, timeout_s)
                except FishFindError as exc:
                    # trace.moe 这类站点偶发连接重置：网络类失败重试一次，别让用户白等一场
                    if not _is_transient(exc):
                        raise
                    self.ctx.logger.info(f"{self._title()}：%s，1 秒后重试一次", exc)
                    await asyncio.sleep(1.0)
                    payload = await self._run_cli(tmp_path, engines, timeout_s)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        text = str(payload.get("text") or "").strip()
        if not text:
            text = self._fallback_text(payload)
        return self._format_text(text)

    def _build_argv(self, image_path: Path, engines: list[str], timeout_s: int) -> list[str]:
        """拼 node 命令行参数（单独抽出来，方便自检断言）。"""

        argv = [
            self._node,
            str(CLI_PATH),
            str(image_path),
            "--json",
            "--engines",
            ",".join(engines),
            "--timeout",
            str(timeout_s),
            "--max",
            str(int(self._cfg().engines.max_accounts)),
            "--min-sim",
            f"{float(self._cfg().engines.min_similarity):.2f}",
        ]
        if not self._cfg().engines.strict_consensus:
            argv.append("--no-strict")
        # 浏览器类引擎（lens）的代理是启动参数，单独传一份
        proxy = str(self._cfg().engines.proxy or "").strip()
        if proxy:
            argv.extend(["--proxy", proxy])
        return argv

    async def _run_cli(self, image_path: Path, engines: list[str], timeout_s: int) -> dict[str, Any]:
        """执行 ``node bin/fish.mjs <file> --json``，解析 stdout 里的 JSON。"""

        argv = self._build_argv(image_path, engines, timeout_s)

        kwargs: dict[str, Any] = {
            "cwd": str(VENDOR_DIR),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": self._child_env(),
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            proc = await asyncio.create_subprocess_exec(*argv, **kwargs)
        except FileNotFoundError as exc:
            raise FishFindError(f"node 启动失败（{self._node}）：{exc}") from exc

        pid = getattr(proc, "pid", 0)
        self.ctx.logger.info(
            "%s：node 已启动 pid=%s 引擎=%s 预算=%ds（总时限 %ds）",
            self._title(), pid, ",".join(engines), max(10, min(timeout_s + 25, self._deadline_seconds())),
            self._deadline_seconds(),
        )

        # 子进程自己的预算不超过插件总时限；总时限由 _search_with_deadline 兜底。
        budget = max(10, min(timeout_s + 25, self._deadline_seconds()))
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=budget)
        except asyncio.TimeoutError:
            self._kill_quietly(proc)
            self.ctx.logger.warning("%s：跑满 %d 秒，已终止 node pid=%s", self._title(), budget, pid)
            raise FishFindError(f"查图超时（超过 {budget} 秒），`{','.join(engines)}` 可能被网络卡住了。") from None
        except asyncio.CancelledError:
            # 外层总时限到了会取消这里：必须把 node 收掉，否则会留下孤儿进程
            self._kill_quietly(proc)
            self.ctx.logger.warning("%s：搜索被取消（重载/总时限），已终止 node pid=%s", self._title(), pid)
            raise

        out_text = stdout.decode("utf-8", "replace").strip()
        err_text = stderr.decode("utf-8", "replace").strip()
        if not out_text:
            detail = err_text.splitlines()[-1] if err_text else f"退出码 {proc.returncode}"
            raise FishFindError(f"命令行没有返回结果：{detail}")

        start, end = out_text.find("{"), out_text.rfind("}")
        if start < 0 or end <= start:
            raise FishFindError(f"命令行输出无法解析：{out_text[:200]}")
        try:
            payload = json.loads(out_text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise FishFindError(f"命令行输出不是合法 JSON：{exc}") from exc

        failures = payload.get("failures") or []
        if not payload.get("enginesUsed") and failures:
            reasons = "；".join(
                f"{item.get('engine')}: {_reason_hint(item.get('reason'))}" for item in failures[:3]
            )
            raise FishFindError(f"所有引擎都没跑通（{reasons}）")
        return payload

    def _fallback_text(self, payload: dict[str, Any]) -> str:
        """CLI 没给 text 时，用 accounts/failures 拼一句。"""

        accounts = payload.get("accounts") or []
        if not accounts:
            return "❌ 没查到账号。"
        lines = [f"🎯 {self._title()}结果"]
        for item in accounts[: self._cfg().engines.max_accounts]:
            lines.append(f"🌐 {item.get('platform')}：{item.get('handle')} {item.get('url') or ''}".strip())
        return "\n".join(lines)

    # ── 取图 ──────────────────────────────────────────────────────────────

    async def _image_for_tool(self, stream_id: str, message_id: str) -> tuple[bytes, str]:
        """工具路径取图：显式消息 ID 优先，否则翻最近消息里最新的一张图。"""

        if message_id:
            got = await self._image_from_message_id(message_id, stream_id, attempts=3, delay=0.5)
            if got:
                return got
        # 复用最近消息的取图逻辑（含 10 分钟新鲜度过滤），但这里要区分「没有消息」和「没有图」
        recent = await self.ctx.call_capability(
            "message.get_recent",
            chat_id=stream_id,
            limit=10,
            include_binary_data=True,
        )
        messages = self._unwrap_messages(recent)
        if not messages:
            raise FishFindError("这个会话里没找到图片。请让用户先发一张图，再说「这是谁」。")

        now = time.time()
        for message in reversed(messages):
            got = await self._image_from_message(message, recent_window=now)
            if got:
                return got
        raise FishFindError("最近 10 分钟里没找到可用的图片。请让用户重新发一张。")

    async def _image_for_command(self, message: dict[str, Any], stream_id: str) -> tuple[bytes, str]:
        """指令路径取图：引用消息优先，其次本条消息，最后翻最近几条。

        刚收到的消息和图片是异步落盘的，取不到时要重试一两秒，
        不能立刻回「没找到图片」。
        """

        # 1) 被引用的那条（reply 段或 reply_to 字段）
        target_id = self._reply_target(message)
        if target_id:
            got = await self._image_from_message_id(target_id, stream_id)
            if got:
                return got
            self.ctx.logger.info("%s：引用消息 %s 里没取到图，继续找本条", self._title(), target_id)

        # 2) 本条消息自带的图（可能是 base64 / URL，直接就能用）
        got = await self._image_from_message(message)
        if got:
            return got

        # 3) 本条消息可能刚收到、还没入库：按 ID 重试几秒
        #    只有「本条确实带图」才值得等；纯文字指令没必要白等 4 秒。
        has_image_seg = any(
            isinstance(seg, dict) and seg.get("type") == "image" for seg in message.get("raw_message") or []
        )
        if message.get("message_id") and (message.get("is_picture") or has_image_seg):
            got = await self._image_from_message_id(str(message["message_id"]), stream_id, attempts=10, delay=0.4)
            if got:
                return got

        # 4) 退回最近 10 条翻一遍；再等 1 秒翻第二遍（图片落盘通常滞后于消息）
        got = await self._image_from_recent(stream_id, limit=10)
        if got:
            return got
        await asyncio.sleep(1.0)
        got = await self._image_from_recent(stream_id, limit=10)
        if got:
            return got

        self.ctx.logger.warning("%s：取图失败，消息结构=%s", self._title(), self._describe_message(message))
        raise FishFindError(
            f"没找到图片。请引用一条带图的消息，或把图和 /{self._title()} 发在同一条里再试。"
        )

    def _describe_message(self, message: dict[str, Any]) -> str:
        """把消息结构压成一行，方便排查（不含图片内容）。"""

        segs = []
        for seg in message.get("raw_message") or []:
            if not isinstance(seg, dict):
                continue
            kind = str(seg.get("type") or "?")
            extra = ""
            if kind == "image":
                extra = f"(hash={str(seg.get('hash') or '')[:8]}, base64={'有' if seg.get('binary_data_base64') else '无'})"
            elif kind == "reply":
                data = seg.get("data")
                target = data.get("target_message_id") if isinstance(data, dict) else data
                extra = f"(target={target})"
            segs.append(kind + extra)
        return (
            f"id={message.get('message_id')} reply_to={message.get('reply_to')} "
            f"is_picture={message.get('is_picture')} segs={segs or '无'}"
        )

    async def _image_from_recent(self, stream_id: str, *, limit: int = 10) -> tuple[bytes, str] | None:
        """从最近几条消息里找图。"""

        try:
            recent = await self.ctx.call_capability(
                "message.get_recent",
                chat_id=stream_id,
                limit=limit,
                include_binary_data=True,
            )
        except Exception as exc:  # 能力调用失败不该让整条指令崩掉
            self.ctx.logger.warning("%s：get_recent 失败：%s", self._title(), exc)
            return None
        messages = self._unwrap_messages(recent)
        if not messages:
            self.ctx.logger.debug("%s：get_recent 返回空（原始类型 %s）", self._title(), type(recent).__name__)
        now = time.time()
        for item in reversed(messages):
            # 只取「刚发的」图：不然一条裸指令会把一小时前的旧图翻出来查
            got = await self._image_from_message(item, recent_window=now)
            if got:
                return got
        return None

    @staticmethod
    def _unwrap_messages(payload: Any) -> list[dict[str, Any]]:
        """把 message.get_recent 的返回值统一成消息字典列表。

        MaiBot SDK 会先拆掉宿主那层 `{"success": …, "messages": [...]}`，直接返回 list；
        但不同版本/直连调用可能还是包装形态，所以两种都认。
        """

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("messages", "result", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _unwrap_message(payload: Any) -> dict[str, Any] | None:
        """把 message.get_by_id 的返回值统一成单条消息字典。"""

        if not isinstance(payload, dict):
            return None
        inner = payload.get("message")
        if isinstance(inner, dict):
            return inner
        # SDK 拆包后就是这个消息字典本身
        if any(key in payload for key in ("message_id", "raw_message", "session_id", "message_info")):
            return payload
        return None

    @staticmethod
    def _reply_target(message: dict[str, Any]) -> str:
        """从消息字典里找出被引用消息的 ID。"""

        reply_to = message.get("reply_to")
        if isinstance(reply_to, str) and reply_to.strip():
            return reply_to.strip()
        for segment in message.get("raw_message") or []:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data")
            if isinstance(data, dict):
                target = data.get("target_message_id")
                if isinstance(target, str) and target.strip():
                    return target.strip()
            elif isinstance(data, str) and data.strip():
                return data.strip()
        return ""

    async def _image_from_message_id(
        self, message_id: str, stream_id: str, *, attempts: int = 1, delay: float = 0.4
    ) -> tuple[bytes, str] | None:
        """按消息 ID 取图（含二进制）；消息还没落盘时按 attempts/delay 重试。"""

        for index in range(max(1, attempts)):
            got = await self._image_from_message_id_once(message_id, stream_id)
            if got:
                return got
            if index < attempts - 1:
                await asyncio.sleep(delay)
        return None

    async def _image_from_message_id_once(self, message_id: str, stream_id: str) -> tuple[bytes, str] | None:
        """按消息 ID 取一次图。"""

        try:
            payload = await self.ctx.call_capability(
                "message.get_by_id",
                message_id=message_id,
                chat_id=stream_id,
                stream_id=stream_id,
                include_binary_data=True,
            )
        except Exception as exc:  # 能力调用失败不致命，交给后面的兜底
            self.ctx.logger.debug(f"{self._title()}：get_by_id 失败 %s：%s", message_id, exc)
            return None
        message = self._unwrap_message(payload)
        if message is None:
            self.ctx.logger.debug(
                "%s：get_by_id(%s) 没拿到消息（原始类型 %s）", self._title(), message_id, type(payload).__name__
            )
            return None
        return await self._image_from_message(message)

    async def _image_from_message(
        self, message: dict[str, Any] | None, recent_window: float | None = None
    ) -> tuple[bytes, str] | None:
        """从一条消息的段里取出第一张图。"""

        if not isinstance(message, dict):
            return None
        if recent_window is not None:
            stamp = message.get("timestamp")
            try:
                if stamp is not None and recent_window - float(stamp) > IMAGE_FRESH_SECONDS:
                    return None
            except (TypeError, ValueError):
                pass

        for segment in message.get("raw_message") or []:
            if not isinstance(segment, dict) or segment.get("type") != "image":
                continue
            got = await self._segment_to_bytes(segment)
            if got:
                return got
        return None

    async def _segment_to_bytes(self, segment: dict[str, Any]) -> tuple[bytes, str] | None:
        """图片段 → 字节。优先用 Maibot 已存下来的二进制，其次 base64，最后下载 URL。"""

        encoded = segment.get("binary_data_base64")
        if isinstance(encoded, str) and encoded:
            try:
                return base64.b64decode(encoded), "base64"
            except (ValueError, TypeError):
                pass

        data = segment.get("data")
        if isinstance(data, str) and data:
            if data.startswith("base64://"):
                try:
                    return base64.b64decode(data[len("base64://") :]), "base64"
                except (ValueError, TypeError):
                    return None
            if data.startswith(("http://", "https://")):
                raw = await asyncio.to_thread(self._download_image, data)
                if raw:
                    return raw, "url"
        return None

    def _url_rejection_reason(self, url: str) -> str:
        """检查一个图片地址是否安全；返回空串表示放行，否则返回原因。"""

        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            return "地址无法解析"
        if parsed.scheme not in ("http", "https"):
            return f"不支持的协议 {parsed.scheme or '空'}"
        if not parsed.hostname:
            return "缺少主机名"
        if parsed.username or parsed.password:
            return "地址里带账号信息"
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return "端口不合法"
        try:
            infos = socket.getaddrinfo(parsed.hostname, port)
        except OSError as exc:
            return f"域名解析失败（{exc}）"
        for info in infos:
            try:
                address = ipaddress.ip_address(info[4][0])
            except ValueError:
                return f"地址无法解析（{info[4][0]}）"
            if not address.is_global:
                return f"内网/保留地址 {address}"
        return ""

    def _download_image(self, url: str) -> bytes | None:
        """下载图片，带内网地址拦截（含重定向逐跳校验）与大小上限（在子线程里跑）。"""

        limit = int(self._cfg().runtime.max_image_mb) * 1024 * 1024
        reason = self._url_rejection_reason(url)
        if reason:
            self.ctx.logger.warning(f"{self._title()}：拒绝下载 %s（%s）", url, reason)
            return None

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "maibot-fishfind/1.0", "Accept": "image/*"},
        )
        # 自定义 opener：每一个 3xx 目标都会再过一次 _url_rejection_reason
        opener = urllib.request.build_opener(_SafeRedirectHandler(self._url_rejection_reason))
        try:
            with opener.open(request, timeout=20) as response:
                final_url = response.geturl()
                final_reason = self._url_rejection_reason(final_url)
                if final_reason:
                    self.ctx.logger.warning(
                        f"{self._title()}：下载最终落到 %s（%s），已拒绝", final_url, final_reason
                    )
                    return None
                if getattr(response, "status", 200) != 200:
                    return None
                data = response.read(limit + 1)
        except Exception as exc:
            self.ctx.logger.warning(f"{self._title()}：下载图片失败 %s：%s", url, exc)
            return None
        if len(data) > limit:
            raise FishFindError(f"图片超过 {self._cfg().runtime.max_image_mb}MB，拒绝处理。")
        return data or None

    # ── 权限与配额 ────────────────────────────────────────────────────────

    def _access_denial(self, group_id: str) -> str:
        """群白名单 / 私聊开关检查，返回拒绝文案（空串=放行）。"""

        cfg = self._cfg()
        if not group_id and not cfg.access.allow_private:
            return f"{self._title()}在私聊里关掉了。"
        whitelist = {str(item).strip() for item in cfg.access.group_whitelist if str(item).strip()}
        if whitelist and group_id and group_id not in whitelist:
            return f"这个群没开{self._title()}。"
        return ""

    def _quota_denial(self, user_id: str, group_id: str) -> str:
        """冷却与每日额度检查，返回拒绝文案（空串=放行）。"""

        cfg = self._cfg()
        cooldown = int(cfg.access.cooldown_seconds)
        key = f"{group_id}|{user_id}"
        now = time.time()
        if cooldown > 0:
            last = self._cooldowns.get(key, 0.0)
            if now - last < cooldown:
                wait = int(cooldown - (now - last)) + 1
                return f"慢点，{wait} 秒后再试。"

        state = self._quota_state()
        per_user = int(cfg.access.per_user_daily)
        if per_user > 0 and state["users"].get(user_id, 0) >= per_user:
            return f"你今天的 {per_user} 次已经用完了，明天再来。"
        per_group = int(cfg.access.per_group_daily)
        if per_group > 0 and group_id and state["groups"].get(group_id, 0) >= per_group:
            return f"这个群今天的 {per_group} 次已经用完了，明天再来。"
        return ""

    def _charge_quota(self, user_id: str, group_id: str) -> None:
        """记账：冷却时间 + 当日次数。"""

        cfg = self._cfg()
        now = time.time()
        if int(cfg.access.cooldown_seconds) > 0:
            if len(self._cooldowns) > 5000:  # 长期运行后清一次，避免无界增长
                cutoff = now - max(3600, int(cfg.access.cooldown_seconds) * 4)
                self._cooldowns = {k: v for k, v in self._cooldowns.items() if v >= cutoff}
            self._cooldowns[f"{group_id}|{user_id}"] = now
        state = self._quota_state()
        if user_id:
            state["users"][user_id] = int(state["users"].get(user_id, 0)) + 1
        if group_id:
            state["groups"][group_id] = int(state["groups"].get(group_id, 0)) + 1
        state["total"] = int(state.get("total", 0)) + 1
        self._save_quota(state)

    def _quota_state(self) -> dict[str, Any]:
        """读当日配额状态，跨天自动清零。"""

        today = datetime.now().strftime("%Y-%m-%d")
        state = self._load_quota()
        if state.get("date") != today:
            state = {"date": today, "users": {}, "groups": {}, "total": 0}
            self._save_quota(state)
        return state

    def _load_quota(self) -> dict[str, Any]:
        """从数据目录读配额文件，失败就用内存里的。"""

        if not self._ready_storage():
            return dict(self._memory_quota) if self._memory_quota else {"date": "", "users": {}, "groups": {}, "total": 0}
        try:
            raw = self._quota_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("users", {})
                data.setdefault("groups", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return dict(self._memory_quota) if self._memory_quota else {"date": "", "users": {}, "groups": {}, "total": 0}

    def _save_quota(self, state: dict[str, Any]) -> None:
        """写回配额文件；目录不可写时退化为内存计数。"""

        self._memory_quota = dict(state)
        if not self._ready_storage():
            return
        try:
            tmp = self._quota_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._quota_file)
        except OSError as exc:
            self.ctx.logger.warning(f"{self._title()}：配额文件写入失败：%s", exc)

    def _ready_storage(self) -> bool:
        try:
            return self._data_dir.is_dir()
        except Exception:
            return False

    # ── 小工具 ────────────────────────────────────────────────────────────

    @property
    def _data_dir(self) -> Path:
        return Path(self.ctx.paths.data_dir)

    @property
    def _quota_file(self) -> Path:
        return self._data_dir / "quota.json"

    def _cfg(self) -> FishFindConfig:
        """取强类型配置；尚未注入时用默认值，保证工具/指令不会因此炸掉。"""

        try:
            return self.config  # type: ignore[return-value]
        except RuntimeError:
            return FishFindConfig()

    def _title(self) -> str:
        """当前显示名：回话标题、日志前缀、给 node 的标题都用它。"""

        try:
            name = str(self._cfg().plugin.display_name or "").strip()
        except Exception:
            name = ""
        return name or DEFAULT_DISPLAY_NAME

    def _engines(self) -> list[str]:
        """清洗引擎列表，只保留 Node 侧认识的 ID。"""

        known = {"saucenao", "lens", "tracemoe", "iqdb", "baidu", "bing", "yandex", "google"}
        picked = [str(item).strip().lower() for item in self._cfg().engines.engines]
        valid = [item for item in picked if item in known]
        return valid or ["tracemoe"]

    def _resolve_node(self) -> str:
        """按配置 → PATH → 常见安装路径的顺序找 node。"""

        configured = str(self._cfg().runtime.node_path or "").strip()
        if configured:
            path = Path(configured)
            if path.is_file():
                return str(path)
            self.ctx.logger.warning(f"{self._title()}：配置的 node 路径不存在：%s", configured)

        for name in ("node", "node.exe"):
            found = shutil.which(name)
            if found:
                return found
        for candidate in NODE_CANDIDATES:
            if Path(candidate).is_file():
                return candidate
        return ""

    def _child_env(self) -> dict[str, str]:
        """给子进程一份干净环境：去掉所有看起来像凭据的变量。"""

        env: dict[str, str] = {}
        for key, value in os.environ.items():
            upper = key.upper()
            if any(marker in upper for marker in ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD")):
                continue
            env[key] = value
        env["PYTHONIOENCODING"] = "utf-8"
        # 让 node 把浏览器 profile 写进插件数据目录，而不是 vendor 里（插件目录可能只读）
        with_suppress(lambda: env.__setitem__("FISH_FIND_STATE_DIR", str(self._data_dir / "browser")))
        # 回话标题里的产品名也交给 node 拼，免得两边名字对不上
        with_suppress(lambda: env.__setitem__("FISH_FIND_TITLE", self._title()))

        # 代理：Node 24 的 fetch 不读系统代理，必须显式给环境变量 + NODE_USE_ENV_PROXY。
        # trace.moe 走 NO_PROXY 直连，这样就算代理挂了它还能用。
        proxy = str(self._cfg().engines.proxy or "").strip()
        if proxy:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env[key] = proxy
            env["NODE_USE_ENV_PROXY"] = "1"
            env["NO_PROXY"] = "api.trace.moe,localhost,127.0.0.1,::1"
            env["no_proxy"] = env["NO_PROXY"]
        return env

    def _format_text(self, text: str) -> str:
        """把命令行的话术改成 QQ 里好看的形式。"""

        cleaned = text.replace("**", "").strip()
        limit = int(self._cfg().reply.max_chars)
        if len(cleaned) > limit:
            cleaned = cleaned[: limit - 1] + "…"
        return cleaned

    async def _send(self, stream_id: str, text: str) -> None:
        """发一条文字，失败只记日志（不能让后台任务因为发送失败而抛）。"""

        if not stream_id or not text:
            return
        try:
            await self.ctx.send.text(text, stream_id)
        except Exception as exc:  # pragma: no cover
            self.ctx.logger.warning(f"{self._title()}：发送失败：%s", exc)


# 被强杀的 node 进程用后台任务回收，避免留下僵尸；集合持有引用防止任务被 GC。
_PENDING_REAPS: set[asyncio.Task[Any]] = set()


async def _reap_process(proc: Any) -> None:
    """等子进程真正退出并回收，最多等 5 秒。"""

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception:
        pass


def _spawn_helper(coro: Any) -> None:
    """后台跑一个收尾协程，并持有引用直到它结束（防止被 GC 掉）。"""

    try:
        task = asyncio.create_task(coro)
    except Exception:
        with_suppress(getattr(coro, "close", lambda: None))
        return
    _PENDING_REAPS.add(task)
    task.add_done_callback(_PENDING_REAPS.discard)


def _kill_tree_sync(pid: int) -> None:
    """Windows 下按 PID 杀整棵进程树（只用于我们自己起的子进程）。"""

    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def with_suppress(func: Any) -> None:
    """调用一个可能抛异常的小函数并吞掉异常。"""

    try:
        func()
    except Exception:
        pass


def create_plugin() -> FishFindPlugin:
    """插件入口：MaiBot 通过该工厂函数创建实例。"""

    return FishFindPlugin()
