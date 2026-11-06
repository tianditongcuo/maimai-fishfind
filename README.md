# 麦麦找鸡（Maibot 插件）

> **衍生作品声明**
> 本插件**基于「鱼鱼找鸡 v1.0.0」修改**而来——原作者 **小鲸鱼**（MIT 协议），
> 上游包名 `coser-image-search`。**原作者未参与本版本的开发与测试，也不为其背书。**
> 来源、改动清单与许可义务见 **[NOTICE.md](NOTICE.md)**。

以图反查：把群里的一张图交给反查引擎，找出它的出处、画师，或者照片里那位 coser 的社交账号。

- 支持 **Google Lens**（查真人社交账号的关键通道）、**SauceNAO**、**trace.moe**、IQDB（可选）
- 能抽出 **Twitter / 微博 / B站 / 小红书 / 抖音 / Instagram / TikTok / Pixiv** 的账号链接
- 两种入口：LLM 工具 `fish_find` + 斜杠指令 `/麦麦找鸡`
- 反查由插件自带的 Node 命令行完成，**不调用大模型、不消耗 token**

目录结构：

| 路径 | 是什么 | 谁写的 |
|---|---|---|
| `plugin.py` / `config.py` / `config.toml` / `_manifest.json` | Maibot 插件层（Python） | 本衍生作品 |
| `vendor/fish/` | 上游「鱼鱼找鸡 v1.0.0」的 Node 命令行，**已裁剪修改** | 小鲸鱼（原始）+ 本衍生作品（修改） |

---

## 1. 安装

1. 从 GitHub 拿代码：`git clone https://github.com/tianditongcuo/maimai-fishfind.git`，
   然后把目录**重命名为 `maimai_fishfind`**，放进 `my-maibot/plugins/`（Maibot 是按目录名加载的）。
   也可以直接下载 Releases 里的 zip 解压。
2. 机器上要有 **Node.js 20+**。查找顺序：`runtime.node_path` → `PATH` 里的 `node` →
   `Program Files\nodejs\node.exe`、`D:\Nodejs\node.exe`。
3. 重启 Maibot，或从 WebUI 重新加载插件。启动日志里应出现：

   ```
   麦麦找鸡已就绪：node=D:\Nodejs\node.EXE，召唤词=/麦麦找鸡 /鱼鱼找鸡
   ```

   如果是「找不到 node 可执行文件」，在 WebUI 插件配置里填 `runtime.node_path`。

不需要 `npm install`：vendor 只用 Node 内置模块。只有 `lens` 引擎需要本机装有 Edge/Chrome。

---

## 2. 怎么用

### 方式一：斜杠指令（推荐，不依赖 AI 判断）

```
引用一条带图的消息 → 发 /麦麦找鸡
```

插件会先回「收到，开始找鸡 🐔」，查完再发结果。搜得久时会补一句
「⏳ 还在查，已经 25 秒了」——超过总时限会明确回「搜索超时」并杀掉 node 进程，不会一直挂着。

Maibot 会在消息文本前面拼占位符，插件已经按这些真实形态适配过：

| 你怎么发 | Maibot 实际给插件的文本 |
|---|---|
| 引用图片 → `/麦麦找鸡` | `[image] /麦麦找鸡` |
| 图片 + 指令同一条 | `[图片：图中一位…] /麦麦找鸡` |
| 引用表情包 → `/麦麦找鸡` | `[表情包: 无语,尴尬] /麦麦找鸡` |
| 先 @ 机器人再喊 | `@顾若若  麦麦找鸡` |
| 省略斜杠直接喊 | `麦麦找鸡` |

要求指令占满**整句**：`刚才查过 /麦麦找鸡` 这种闲聊不会触发。

**改召唤词**（`config.toml` 或 WebUI）：

```toml
[plugin]
trigger_words = ["麦麦找鸡", "鱼鱼找鸡", "查图"]
```

- 不用自己加斜杠，写成 `"/查图"` 也认；最多 10 个、单个最长 20 字。
- **改完必须重载插件**：命令正则是加载时注册给 Maibot 的，热更新改不了。
  没重载时日志会明确提示「当前生效的仍是旧词」。

### 方式二：让 AI 自己调

用户发图问「这是谁」「哪个 coser」「出自哪部番」时，AI 会调用 `fish_find` 工具。
工具取当前会话**最近 10 分钟内**的图，也可以由 AI 指定 `image_message_id`。

### 结果长什么样

```
🎯 麦麦找鸡结果

🌐 平台：Twitter
👤 名字：JingleKitty
🆔 账号：@Aleph6Zero9 · https://x.com/Aleph6Zero9
```

同一张图被多个号转发时会列多个（受 `max_accounts` 限制）。
如果只找到「相似图」而没有可信账号，会明确告诉你相似度和门槛，而不是随便指一个人。

---

## 3. 配置

配置文件 `config.toml`，也可以在 WebUI 里改。

| 配置 | 默认 | 说明 |
|---|---|---|
| `plugin.enabled` | `true` | 总开关 |
| `plugin.display_name` | `麦麦找鸡` | 回话标题与日志前缀 |
| `plugin.trigger_words` | `["麦麦找鸡","鱼鱼找鸡"]` | 召唤词，改完需重载 |
| `engines.engines` | `["saucenao","tracemoe"]` | 引擎列表；**查真人必须加 `lens`** |
| `engines.proxy` | `""` | 引擎用的 HTTP 代理，如 `http://127.0.0.1:7890` |
| `engines.min_similarity` | `0.8` | 相似度门槛，低于它不报（防「只是长得像」） |
| `engines.timeout_seconds` | `45` | 单引擎超时；开 lens 建议 90 |
| `engines.search_deadline_seconds` | `75` | 整次搜索总时限；开 lens 建议 180 |
| `engines.max_accounts` | `3` | 最多回几个账号 |
| `engines.strict_consensus` | `true` | 标题里名字对不上就不报 |
| `access.group_whitelist` | `[]` | 群白名单，留空 = 所有群可用 |
| `access.allow_private` | `true` | 允许私聊 |
| `access.cooldown_seconds` | `30` | 同一人冷却 |
| `access.per_user_daily` | `20` | 每人每日上限（0 = 不限） |
| `access.per_group_daily` | `100` | 每群每日上限（0 = 不限） |
| `runtime.node_path` | `""` | node 路径，留空自动找 |
| `runtime.max_image_mb` | `10` | 图片大小上限 |
| `reply.announce` | `true` | 先回「收到」再查 |
| `reply.max_chars` | `1200` | 单条回复截断长度 |

### 引擎怎么选

| 引擎 | 覆盖什么 | 国内网络 |
|---|---|---|
| `lens`（Google Lens） | **真人**社交账号：Twitter / IG / 微博 / B站 / 小红书 / 抖音 / TikTok | 必须走代理，且代理能访问 `www.google.com` |
| `saucenao` | 二次元插画 / 同人图（Pixiv、Danbooru、yande.re…） | 直连通常不通，走代理可通 |
| `tracemoe` | 动漫番剧 / 剧场版截图 | 直连可用 |
| `iqdb` | 动漫图库 | 走代理 |
| `baidu` / `bing` / `yandex` / `google` | 需要**公网图片 URL**，本插件不提供图床，因此未启用 | — |

查真人的推荐配置：

```toml
[engines]
engines = ["lens", "saucenao", "tracemoe"]
proxy = "http://127.0.0.1:7890"
timeout_seconds = 90
search_deadline_seconds = 180
```

两个要点：

1. **Node 的 `fetch` 不读系统代理**。插件会把 `HTTP(S)_PROXY` + `NODE_USE_ENV_PROXY=1`
   显式传给子进程，并用 `NO_PROXY=api.trace.moe` 让 trace.moe 保持直连（代理挂了它还能用）。
2. `lens.google.com` 在很多网络下被墙，插件走的是同一个后端的
   `www.google.com/searchbyimage/upload`，只要 `www.google.com` 通就行。

### 精确度：完全匹配 vs 视觉匹配

Lens 有两页结果：

- **完全匹配**：同一张图的原出处 —— 其中抽出的账号标为**可信**；
- **视觉匹配**：只是「长得像」—— 标为 `weak`，回话里写「相似图，未核实」。

**有可信账号时就只报可信的**；一条都没有才退回展示相似图的结论。这能避免「不同的图却查到同一个人」。

### 超时

整次检索有总时限（`search_deadline_seconds`）。到点还没结果会：

- 回一句「❌ 搜索超时：N 秒内没拿到结果，这次先放弃。稍后再试，或换张更清晰的图。」
- 同时把还在跑的 node 进程**连同它派生的无头 Edge 一起收掉**（只针对自己起的 PID），不留孤儿。

---

## 4. 与上游（鱼鱼找鸡 v1.0.0）的差异

> 完整说明见 [NOTICE.md](NOTICE.md)。这里只列对使用者有影响的部分。

### 删掉的（安全原因）

| 删掉的东西 | 原因 |
|---|---|
| `adapters/onebot.mjs` | 原版会把指令参数当本地路径 `readFileSync` 再上传，群成员可用 `/鱼鱼找鸡 C:\Users\...\id_rsa` 读走任意文件 |
| 猫猫云 VPN 控制段 | 会 `spawn` 任意 core 二进制、改系统代理、`taskkill` 杀进程，原 README 未提及 |
| `src/web-search.js` + `inferAccounts` | 会读 `~/.dsh/.credentials.yaml` 和 `DEEPSEEK_API_KEY` |
| `uploadPublicImage`（catbox 图床） | 会把图片传到公开匿名图床，与原 README「上传域名白名单」矛盾 |

### 改掉的

| 改动 | 说明 |
|---|---|
| 回话标题可配置 | 原来写死 `🎯 **鱼鱼找鸡结果**`，现在读 `FISH_FIND_TITLE` |
| 浏览器状态目录可配置 | 原来写死在 `vendor/fish/state/`，现在读 `FISH_FIND_STATE_DIR` |
| CLI 退出方式 | `process.exit()` 在 Node 24 / Windows 会触发 libuv 断言崩溃，改成设 `process.exitCode` 并收尾关闭浏览器 |
| Lens 上传入口 | `lens.google.com/v3/upload` 被墙，改走 `www.google.com/searchbyimage/upload` |
| 结果口径 | 完全匹配优先；视觉匹配降级为「相似图，未核实」 |
| 相似度门槛 | 新增 `min_similarity`，防止把 40% 的相似图当成结果报出去 |
| 平台提取 | 新增小红书 / 抖音 / TikTok，仍只认个人主页（内容页与短链丢弃） |
| 搜索日志 | 每次搜索打印图片 `sha1` + 大小，便于核对「是不是拿错图」 |

### 保留的

`saucenao` / `iqdb` / `tracemoe` 适配、无头浏览器驱动、结果清洗。`closeBrowser()` 里仍有一处
`taskkill`，只针对**自己启动的那个浏览器进程**。

### 本封装额外加的安全措施

- 只接受**消息里的图片**，没有「传路径」的入口；
- 图片下载拦截内网/环回地址，限 10MB、20 秒超时；
- 子进程环境变量剔除所有含 `KEY` / `TOKEN` / `SECRET` / `PASSWORD` 的变量；
- 群白名单、每人/每群每日上限、个人冷却；
- 反查串行执行，避免把免费引擎额度刷爆。

---

## 5. 隐私提示

反查的代价是**图片必须上传给第三方**（`api.trace.moe`、`saucenao.com`、Google 等）。
私图、他人照片不要拿来查；在群里开放前请想清楚：这个功能本质是「以图找人」，
被用来开盒的成本很低。建议配合 `access.group_whitelist` 只在自家群开。

---

## 6. 已知限制

- **Google Lens 必须能访问 `www.google.com`**，且单次 20~60 秒（冷启动更久）。
- `yandex` / `bing` / `baidu` / `google` 这些引擎原版依赖「公网图片 URL」，本插件不提供图床，所以没启用。
- 相似度门槛是**宁可漏、不可错**：没有 80% 以上的匹配时会明确说「没找到原图」，而不是给一个可疑账号。
- Instagram 有时只拿到帖子链接（`/p/…`）而拿不到作者主页，这种情况下不会报账号。
- 引擎都是免费接口，`saucenao` 有频率限制（403 时回话会说明）。

---

## 7. 排查

| 现象 | 处理 |
|---|---|
| 「还没准备好：找不到 node」 | 装 Node 20+，或在 WebUI 填 `runtime.node_path` |
| 「所有引擎都没跑通（…）」 | 手动跑：`node vendor/fish/bin/fish.mjs <图> --engines lens --proxy http://127.0.0.1:7890` 看具体报错 |
| 一直「没查到账号」 | 只有二次元截图才可能被 trace.moe 命中；真人的图要开 `lens` |
| Lens 报「谷歌连不上」 | 代理没开，或代理不能访问 `www.google.com` |
| 回复「没找到图片」 | 引用一条带图的消息再发指令；图片超过 10MB 也会被拒 |
| 不同图查到同一个人 | 看日志 `开始搜索 图片 sha1=…`：sha1 相同说明拿错图；sha1 不同则是 Lens 视觉匹配，属正常，可调高 `min_similarity` 或降 `max_accounts` |
| 改召唤词/引擎没生效 | 命令正则与配置在加载时读取，去 WebUI 重载插件或重启 Maibot |
| 老是「搜索超时」 | 调大 `search_deadline_seconds`，或先只留 `tracemoe` |

---

## 8. 自测

**① Node 侧（不需要 Maibot）**

```bat
cd vendor\fish
node bin\fish.mjs --check
node bin\fish.mjs 你的图.jpg --engines lens --proxy http://127.0.0.1:7890
```

第一条列引擎、代理与超时；第二条实跑一次。

**② 插件层** —— 放进 `plugins/` 重启后看两处：

- 启动日志有没有 `麦麦找鸡已就绪：node=...，召唤词=...`
- WebUI 插件页能看到工具 `fish_find` 与指令 `/麦麦找鸡`

**③ 端到端** —— 群里引用一张图发 `/麦麦找鸡`，应先后收到「收到，开始找鸡 🐔」和结果。

---

## 9. 许可、署名与致谢

- 许可：**MIT**，`LICENSE` 保留上游原始版权行 `Copyright (c) 2026 小鲸鱼`，
  `vendor/fish/LICENSE` 为同一份副本。
- 本插件是**修改版**，不代表上游；原作者未参与本版本。
- 分发时必须保留 `LICENSE`、`vendor/fish/LICENSE`、`NOTICE.md`，并注明「基于鱼鱼找鸡 v1.0.0（小鲸鱼）修改」。
- 完整来源、改动归属与免责声明见 **[NOTICE.md](NOTICE.md)**。

感谢 **小鲸鱼** 开源「鱼鱼找鸡」——反查能力全部来自原作者的工作，本插件只做了 Maibot 接入与安全裁剪。

---

版本 1.2.1 · 插件 ID `maimai.fishfind` · 需要 Maibot ≥ 1.0.0、maibot_sdk ≥ 2.0.0、Node.js ≥ 20
