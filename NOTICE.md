# 版权与来源声明（NOTICE）

## 一句话说清

**「麦麦找鸡」是衍生作品（derivative work）**：它的反查能力来自开源项目
**「鱼鱼找鸡 v1.0.0」**（npm 包名 `coser-image-search`，作者 **小鲸鱼**，MIT 协议），
本插件是在**原作者的文件基础上修改**出来的，**不是原创，也不是上游官方版本**。

原作者**没有**参与本版本的开发、测试和发布，也不为本版本背书。

---

## 一、上游信息

| 项 | 内容 |
|---|---|
| 上游名称 | 鱼鱼找鸡 v1.0.0 |
| 上游包名 / 文件名 | `coser-image-search` |
| 上游作者 | **小鲸鱼**（仅见于 `LICENSE` 的版权行） |
| 上游许可 | MIT |
| 上游主页 / 仓库 / 联系方式 | **未知**：原始压缩包里没有任何链接，`package.json` 也没有 `author` / `repository` 字段 |
| 上游代码在本仓库的位置 | `vendor/fish/` 目录 |

---

## 二、本衍生作品

| 项 | 内容 |
|---|---|
| 名称 | 麦麦找鸡（Maibot 插件） |
| 插件 ID | `maimai.fishfind` |
| 维护者 | **tianditongcuo** · https://github.com/tianditongcuo |
| 许可 | MIT（与上游一致） |
| 与上游的关系 | 使用其代码并修改；**未获原作者背书**，原作者未参与开发与测试 |

---

## 三、哪些是原作者的，哪些是改的

**来自上游（原作者的作品，已修改）**

- `vendor/fish/src/fish-find-chicken.js` —— 反查引擎适配、无头浏览器驱动、结果清洗
- `vendor/fish/src/safe-fetch.js` —— 带白名单的抓取
- `vendor/fish/bin/fish.mjs` —— 命令行入口
- `vendor/fish/package.json`、`vendor/fish/LICENSE`

**本衍生作品新增**

- `plugin.py`、`config.py`、`config.toml`、`_manifest.json` —— Maibot 插件层
- `README.md`、`NOTICE.md`、`CHANGELOG.md`
- 打包与自测脚本（不在发布包内）

**对上游代码的修改**（逐条原因见 `README.md` 的「与上游的差异」）

- 删除：OneBot 适配层（存在任意文件读取）、猫猫云 VPN 控制、DSH 凭据读取、公开图床（catbox）上传
- 改造：回话标题与浏览器状态目录改为可配置；CLI 退出方式；Lens 上传入口改为 `www.google.com/searchbyimage/upload`
- 增强：完全匹配优先于视觉匹配、相似度门槛、小红书 / 抖音 / TikTok 账号提取、搜索图片指纹日志

---

## 四、许可义务（分发前必读）

上游是 **MIT**，MIT 只要求**保留版权声明与许可声明**，不要求公开修改：

- `LICENSE` —— 上游原文，含 `Copyright (c) 2026 小鲸鱼`
- `vendor/fish/LICENSE` —— 同一份副本
- 本衍生作品的修改部分同样以 **MIT** 发布

如果你再分发本插件，请**原样保留** `LICENSE`、`vendor/fish/LICENSE` 和本 `NOTICE.md`，
并在你的说明里写清楚「基于鱼鱼找鸡 v1.0.0（作者：小鲸鱼）修改」。

---

## 五、免责声明

- 本衍生作品按「现状」提供，不附带任何明示或暗示的担保。
- 反查会把图片上传给第三方（`api.trace.moe`、`saucenao.com`、Google 等），
  请自行评估隐私风险；**不要**用它查私图、他人隐私或未经同意的照片。
- 上游作者不对本衍生作品负责。本衍生作品的问题请反馈给维护者，**不要打扰上游作者**。

---

## 六、致谢

感谢 **小鲸鱼** 开源「鱼鱼找鸡」，本插件只是把它接到了 Maibot 上，并做了一些安全与易用性上的调整。
如果上游作者看到并希望调整署名方式或移除本项目，请联系维护者处理。
