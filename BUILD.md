# OpenFrequency 构建指南

## 前提条件

| 工具 | 安装方式 | 用途 |
|------|----------|------|
| Python 3.10+ | python.org | 运行环境 |
| PyInstaller | `pip install pyinstaller` | 打包 EXE |
| WiX 4 (可选) | `dotnet tool install --global wix` | 生成 MSI 安装程序 |

> **建议**：使用独立虚拟环境构建，避免把 PyTorch / PaddlePaddle 等大型库打进去导致体积膨胀。
>
> ```bat
> python -m venv venv_build
> venv_build\Scripts\activate
> pip install -r requirements.txt
> pip install pyinstaller
> ```

---

## 一、修改版本号

编辑根目录下的 **`version.txt`**，只写版本号，不加换行符以外的内容：

```
3.9-beta
```

格式可以是 `3.9`、`3.9.1`、`3.9-beta`、`4.0.0` 等，WiX 会读取这个文件。

---

## 二、PyInstaller 打包（生成 EXE）

在 **项目根目录**（`D:\OpenFrequency`）执行：

```powershell
pyinstaller openfrequency.spec --noconfirm --clean
```

| 参数 | 含义 |
|------|------|
| `--noconfirm` | 不询问，直接覆盖 `dist/` |
| `--clean` | 清除上次的 PyInstaller 缓存再重建 |

**输出**：
- `dist\OpenFrequency\OpenFrequency.exe` — 无控制台版本
- `dist\OpenFrequency\OpenFrequency-Console.exe` — 调试用控制台版本
- `dist\OpenFrequency\` — 完整的可运行目录，可直接压缩分发

**耗时**：首次约 5–15 分钟（视机器速度和环境大小）；后续增量构建更快。

---

## 三、生成 ZIP（用于 GitHub Release）

```powershell
# 在 PowerShell 中执行
$ver = (Get-Content version.txt).Trim()
Compress-Archive -Path dist\OpenFrequency\* `
                 -DestinationPath "dist\OpenFrequency-$ver.zip" `
                 -Force

# 计算 SHA-256（粘贴到 Release Notes 的 sha256 区域）
(Get-FileHash "dist\OpenFrequency-$ver.zip" -Algorithm SHA256).Hash
```

---

## 四、MSI 安装程序（可选）

需要先安装 WiX 4：

```powershell
dotnet tool install --global wix
```

然后一键构建（会自动调用 PyInstaller + WiX）：

```powershell
.\installer\build_installer.ps1
```

输出：`dist\OpenFrequency-<version>-Setup.msi`

---

## 五、发布到 GitHub Release

```bash
# 确保 gh 在 PATH（首次使用需 gh auth login）
export PATH="$HOME/bin:$PATH"

VER=$(cat version.txt | tr -d '[:space:]')

gh release create "v$VER" \
  "dist/OpenFrequency-$VER.zip#OpenFrequency-$VER.zip" \
  --title "OpenFrequency v$VER" \
  --notes-file RELEASE_NOTES.md \
  --prerelease   # 去掉此行表示正式版
```

> 如果没有 `gh`，可以直接在 GitHub 网页操作：
> **Releases → Draft a new release → 上传 zip → 填写 SHA-256**

---

## 六、完整发布 Checklist

```
[ ] 1. 更新 version.txt
[ ] 2. 更新 RELEASE_NOTES.md 和 RELEASE_NOTES_zh-CN.md
[ ] 3. git add -A && git commit && git push origin main
[ ] 4. pyinstaller openfrequency.spec --noconfirm --clean
[ ] 5. 压缩 dist\OpenFrequency\ → dist\OpenFrequency-x.y.zip
[ ] 6. 计算 SHA-256，填入 RELEASE_NOTES.md 的 sha256 区域
[ ] 7. git add RELEASE_NOTES.md && git commit -m "docs: update sha256 for vX.Y"
[ ] 8. gh release create vX.Y dist/OpenFrequency-X.Y.zip ...
[ ] 9. （可选）运行 build_installer.ps1 生成 MSI 并上传
```

---

## 七、注意事项

### 构建体积问题
如果在全局 Python 环境（装有 PyTorch、PaddlePaddle、OpenCV 等）下构建，输出体积会达到 2–3 GB。
**解决方法**：在干净的 venv 中只安装 `requirements.txt` 的依赖再打包，体积可压缩到 200–400 MB。

### 新增 Python 模块时
如果你新增了 `core/xxx.py` 且 PyInstaller 分析不到（例如通过字符串动态 import），
需要在 `openfrequency.spec` 的 `_hidden` 列表里添加：

```python
_hidden = [
    ...
    "core.xxx",   # 新增模块
]
```

### 新增数据文件时
静态文件（模板、JSON、模型等）放在以下目录之一会自动打包：
`templates/`、`static/`、`data/`、`models/`、`plugins/`

如需排除，修改 `collect_tree()` 的 `excludes` 参数。

### SimConnect
`SimConnect.dll` 已由 spec 自动收集（`collect_all('SimConnect')`），
无需手动下载，但构建机器需要安装 `pip install SimConnect`。

---

## 八、快速命令参考

```powershell
# 完整一键打包（在项目根目录 PowerShell 中执行）
pyinstaller openfrequency.spec --noconfirm --clean

# 打包完成后压缩并查看 SHA256
$v = (Get-Content version.txt).Trim()
Compress-Archive dist\OpenFrequency\* dist\OpenFrequency-$v.zip -Force
(Get-FileHash dist\OpenFrequency-$v.zip -Algorithm SHA256).Hash
```
