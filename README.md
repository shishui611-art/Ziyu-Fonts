# Ziyu-Fonts · 字域在线开源字体库

本仓库为 **字域（ColorOS 字体管理器）** 提供基于 **GitHub Releases + GitHub Pages** 的免费在线开源字体库。

- **严格开源**：仅收录 `SIL OFL 1.1` / `Apache 2.0` / `MIT` 等开放字体协议，每个字体 ZIP 内均附带上游原始 `LICENSE` 与 `module.prop`。
- **仅作者维护**：暂不开放用户上传，所有下载均在字域 App 内强制核对 `SHA-256` 摘要与开源协议白名单。
- **架构分工**：
  - `fonts.json`、`previews/*.png`、`index.html` 放在仓库 `main` 分支并通过 **GitHub Pages**（以及 Raw + 加速前缀）分发；
  - `dist-releases/*.zip` 字体包上传至本仓库 `fonts-v1` Release，不进入 Git 历史，也不污染主仓库 `shishui611-art/Ziyu` 的模块更新检测。

## 首次发布步骤（由作者手动执行）

1. 在 GitHub 新建公开仓库：`shishui611-art/Ziyu-Fonts`（默认分支 `main`）。
2. 在本目录推送静态清单与预览图（`dist-releases/` 与 `.work/` 已在 `.gitignore` 中忽略）：
   ```powershell
   cd c:\Users\Admin\Desktop\ColorOS字体管理器\Ziyu-Fonts
   git init -b main
   git add .
   git commit -m "feat: initial 20 open-source fonts catalog and previews"
   git remote add origin https://github.com/shishui611-art/Ziyu-Fonts.git
   git push -u origin main
   ```
3. 在 GitHub 仓库 **Settings → Pages** 中将 Source 设为 **Deploy from a branch** → `main` / `/ (root)`，保存后几分钟即可通过 `https://shishui611-art.github.io/Ziyu-Fonts/fonts.json` 访问。
4. 创建 Release `fonts-v1` 并上传 `dist-releases/*.zip`（可用 `gh` CLI 一键上传，或网页拖拽上传）：
   ```powershell
   gh release create fonts-v1 dist-releases\*.zip `
     --repo shishui611-art/Ziyu-Fonts `
     --title "Ziyu Open-Source Fonts (fonts-v1)" `
     --notes "首批 20 款开源字体包（全部附带原始 LICENSE）"
   ```

## 后续新增开源字体

1. 在 `sources.json` 的 `fonts` 数组追加新条目（仅允许开源协议字体）。
2. 本地运行：
   ```powershell
   py scripts/build_store.py --only <新字体id>
   ```
   或在 GitHub Actions 手动触发 **Update Open-Source Font Store** 自动重建 `fonts.json`、生成明暗双色预览图并上传至 `fonts-v1` Release。
