# OpenFrequency — Apple Liquid Glass 主题重设计方案

> **用途：** 本文档是 OpenFrequency 前端改版为 Apple Liquid Glass（iOS 26 / macOS Tahoe 风格）的完整设计与实现指引。
> 将本文档交给设计工具或 Claude Design 会话即可生成改版 UI。

---

## 1 · 产品概述

**OpenFrequency** 是一款为桌面飞行模拟器（MSFS 2020/2024、X-Plane 12）设计的 AI 驱动 ATC 助手。
用户飞行时，OpenFrequency 提供真实的 ATC 无线电通信（PTT 语音 + TTS 回复）、实时动态地图、遥测仪表、通讯记录、滑行引导、气象信息和机组管理。

界面运行在浏览器中（通常挂载在副屏或平板），由本地 Flask 服务提供。使用环境为昏暗的虚拟驾驶舱——**深色模式是主模式**。

---

## 2 · 当前界面结构（DOM 骨架）

### 2.1 全局框架（base.html）

```
<html class="theme-classic">
  <body>
    <nav class="navbar">                  ← Bootstrap 5.3 顶部导航
      品牌名  |  模拟器状态徽章  |  导航链接
    </nav>
    <main class="container-fluid">       ← 各页面内容注入此处
    </main>
    <div id="toast-container">           ← Bootstrap toast 通知（右上角）
    </div>
  </body>
</html>
```

**导航栏内容（从左到右）：**
- `🛩 OpenFrequency` 品牌文字
- 模拟器状态徽章：`● 已连接`（绿）/ `○ 未连接`（红）
- 导航链接：仪表盘 · 设置 · 生涯 · 关于
- 插件状态栏条目动态追加于此

**全局字体：** `'Segoe UI', Tahoma, Geneva, Verdana, sans-serif`
**颜色系统：** Bootstrap 5 默认 + CSS 变量 `--card-bg-color`、`--card-text-color`

---

### 2.2 仪表盘页（dashboard.html）—— 主界面

两列流体布局：

```
┌─────────────────────────────────┬──────────────────────────────┐
│  左列 (col-lg-7)                 │  右列 (col-lg-5)             │
│  ┌──────────────────────────┐   │  ┌────────────────────────┐  │
│  │  仪表行（4 张卡片）        │   │  │  通讯记录              │  │
│  │  高度 · 速度 · 航向 · VS  │   │  │  （可滚动聊天区）       │  │
│  └──────────────────────────┘   │  │  最新 ATC 指令卡片条    │  │
│  ┌──────────────────────────┐   │  └────────────────────────┘  │
│  │  标签：地图 / 附近机场    │   │  ┌────────────────────────┐  │
│  │  ┌────────────────────┐  │   │  │  无线电频道切换         │  │
│  │  │  Leaflet 地图       │  │   │  │  📡 ATC  |  👥 机组   │  │
│  │  │  （填满面板）        │  │   │  └────────────────────────┘  │
│  │  │  [左下：地图工具]   │  │   │  ┌────────────────────────┐  │
│  │  └────────────────────┘  │   │  │  PTT 按钮               │  │
│  └──────────────────────────┘   │  │  （蓝色，全宽，大按钮） │  │
│                                 │  └────────────────────────┘  │
└─────────────────────────────────┴──────────────────────────────┘

指令卡片条（固定在右列底部，水平滚动）：
  [ HDG 270° ]  [ ALT 6000ft ]  [ SPD 250kt ]  [ SQ 2341 ]
```

#### 仪表卡片（4 × col-3）
- 每张：`card shadow-sm gauge-card`
- 标签：小号 muted 文字（ALTITUDE、AIRSPEED、HEADING、VS）
- 数值：`<h4>`，通过 Socket 实时更新；VS 用 `text-info`（青色）着色

#### Leaflet 地图面板
- 全高地图瓦片（CartoDB Dark Matter）
- 飞机标记：旋转 SVG 飞机图标
- 飞行计划航线：虚线蓝色折线
- 飞行航迹：渐隐蓝色点
- 交通目标：橙色菱形 + 呼号标签
- 中国 RVSM 覆盖层：顶部红色横幅"🇨🇳 RVSM 生效 7800m / 25590ft"
- 地图控制（左下）：`🗑️ 清除航迹` + `适配导航` 按钮

#### 通讯记录（右列顶部）
- 可滚动 div，每条消息：时间戳 + 发送方标签（粗体）+ 文本
- 颜色：ATC = 蓝，飞行员 = 绿，SYSTEM = 灰
- ATIS 消息：amber 左侧色条
- 最新 ATC 指令高亮

#### 指令卡片条
- 水平滚动 pill 芯片
- 每片：`border-radius: 999px`，白底，蓝色边框 `rgba(13,110,253,0.3)`
- 标签（类型）：蓝色小型大写；数值：等宽字体
- 深色模式：深色背景，柔和蓝色边框

#### PTT 按钮
- `btn btn-primary btn-lg w-100 py-3 fw-bold`，Bootstrap 蓝色，全宽，高大
- 状态：待机 → 激活（脉冲光晕，红色调）→ 录音 → 处理中

---

### 2.3 设置页（settings.html）

单列居中卡片，手风琴分组：
- **通用**：呼号、机场、飞行规则（VFR/IFR）、语言、模型、API Key
- **音频**：TTS 引擎（Edge/Kokoro/Piper）、STT 模型路径、无线电效果开关、麦克风
- **模拟器**：提供商（MSFS/X-Plane/Auto）、主机、端口
- **导航数据**：频率来源、地面数据来源、OSM Overpass URL
- **沉浸感**：繁忙等级、自动繁忙、非英语通讯
- **生涯模式**：生涯模式开关、呼号锁定信息
- **安全**：门铃 / PIN / token 模式
- **紧急**：紧急等级
- **云端 / 隐私**：遥测开关
- **UI 设置**：深色模式开关、主题选择（经典 ✅ / Apple 🚫 开发中）

---

### 2.4 深色模式

通过 `body.dark-mode` 类启用。关键覆盖：
- 背景：`#0f1117`（body），`#1a1d23`（卡片），`#13161c`（侧边栏）
- 文字：`#e5e9f0` 主要，`#8b97a8` 次要
- 卡片边框：`rgba(255,255,255,0.08)`
- 地图：CartoDB Dark 瓦片
- 指令卡片：`#222832` 背景，`rgba(96,165,250,0.45)` 边框，`#7cb8ff` 标签色

---

## 3 · 目标设计：Apple Liquid Glass

### 3.1 核心技术来源

本方案基于以下两个开源项目的技术原理，提取其核心算法，**改写为 Vanilla JS + 纯 CSS**，无需引入 React 或 Vue 框架（项目无构建流程）：

- **liquid-glass-react**：https://github.com/rdev/liquid-glass-react
- **liquid-glass-vue**：https://github.com/WXperia/liquid-glass-vue

两个库底层实现完全相同，核心由三部分组成：

#### A · SVG feDisplacementMap 折射滤镜

```svg
<!-- 注入到 base.html <body> 末尾的隐藏 SVG，全局复用 -->
<svg style="position:absolute;width:0;height:0" aria-hidden="true">
  <defs>
    <!-- 径向渐变：控制色差范围（越靠边缘越强） -->
    <radialGradient id="lg-edge-mask" cx="50%" cy="50%" r="50%">
      <stop offset="0%"   stop-color="black" stop-opacity="0"/>
      <stop offset="60%"  stop-color="black" stop-opacity="0"/>
      <stop offset="100%" stop-color="white" stop-opacity="1"/>
    </radialGradient>

    <!-- 核心折射滤镜 -->
    <filter id="liquid-glass" x="-35%" y="-35%" width="170%" height="170%"
            color-interpolation-filters="sRGB">
      <!-- 置换贴图（JPEG/PNG base64，见 § 5.1） -->
      <feImage id="lg-disp-map" x="0" y="0" width="100%" height="100%"
               result="DISPLACEMENT_MAP" preserveAspectRatio="xMidYMid slice"
               href="[base64-displacement-map]"/>

      <!-- 转灰度 → 边缘强度图 -->
      <feColorMatrix in="DISPLACEMENT_MAP" type="matrix"
                     values="0.3 0.3 0.3 0 0  0.3 0.3 0.3 0 0  0.3 0.3 0.3 0 0  0 0 0 1 0"
                     result="EDGE_INTENSITY"/>
      <feComponentTransfer in="EDGE_INTENSITY" result="EDGE_MASK">
        <feFuncA type="discrete" tableValues="0 0.1 1"/>
      </feComponentTransfer>

      <!-- 保留中心原图 -->
      <feOffset in="SourceGraphic" dx="0" dy="0" result="CENTER_ORIGINAL"/>

      <!-- R 通道置换 -->
      <feDisplacementMap in="SourceGraphic" in2="DISPLACEMENT_MAP"
                         scale="-70" xChannelSelector="R" yChannelSelector="B"
                         result="RED_DISPLACED"/>
      <feColorMatrix in="RED_DISPLACED" type="matrix"
                     values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"
                     result="RED_CHANNEL"/>

      <!-- G 通道置换（稍弱，形成色差） -->
      <feDisplacementMap in="SourceGraphic" in2="DISPLACEMENT_MAP"
                         scale="-70.1" xChannelSelector="R" yChannelSelector="B"
                         result="GREEN_DISPLACED"/>
      <feColorMatrix in="GREEN_DISPLACED" type="matrix"
                     values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"
                     result="GREEN_CHANNEL"/>

      <!-- B 通道置换（最弱） -->
      <feDisplacementMap in="SourceGraphic" in2="DISPLACEMENT_MAP"
                         scale="-70.2" xChannelSelector="R" yChannelSelector="B"
                         result="BLUE_DISPLACED"/>
      <feColorMatrix in="BLUE_DISPLACED" type="matrix"
                     values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"
                     result="BLUE_CHANNEL"/>

      <!-- 三通道 screen 混合 → 色差效果 -->
      <feBlend in="GREEN_CHANNEL"  in2="BLUE_CHANNEL"  mode="screen" result="GB_COMBINED"/>
      <feBlend in="RED_CHANNEL"    in2="GB_COMBINED"   mode="screen" result="RGB_COMBINED"/>
      <feGaussianBlur in="RGB_COMBINED" stdDeviation="0.3" result="ABERRATED_BLURRED"/>

      <!-- 色差只作用于边缘；中心保持清晰 -->
      <feComposite in="ABERRATED_BLURRED" in2="EDGE_MASK"    operator="in"   result="EDGE_ABERRATION"/>
      <feComponentTransfer in="EDGE_MASK" result="INVERTED_MASK">
        <feFuncA type="table" tableValues="1 0"/>
      </feComponentTransfer>
      <feComposite in="CENTER_ORIGINAL"   in2="INVERTED_MASK" operator="in"   result="CENTER_CLEAN"/>
      <feComposite in="EDGE_ABERRATION"   in2="CENTER_CLEAN"  operator="over"/>
    </filter>
  </defs>
</svg>
```

#### B · CSS 玻璃表面（每个玻璃元素）

```css
/* 玻璃容器本体 */
.lg-surface {
  position: relative;
  /* 折射滤镜（Chromium 支持；Safari/Firefox 降级为纯模糊） */
  filter: url(#liquid-glass);
  /* 磨砂玻璃模糊 */
  backdrop-filter: blur(20px) saturate(140%);
  -webkit-backdrop-filter: blur(20px) saturate(140%);
  background: rgba(255, 255, 255, 0.06);
  border-radius: var(--lg-radius, 20px);
  transition: transform 0.2s ease-out, filter 0.2s ease-out;
}

/* 高光边框（两层叠加，用 mask 只保留边框区域） */
.lg-surface::before,
.lg-surface::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  /* mask 技术：只保留 padding 区域 = 边框 */
  -webkit-mask: linear-gradient(#000 0 0) content-box,
                linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
}
/* 第一层：screen 混合，随鼠标方向变化 */
.lg-surface::before {
  mix-blend-mode: screen;
  opacity: 0.2;
  background: linear-gradient(
    135deg,
    rgba(255,255,255,0.0)  0%,
    rgba(255,255,255,0.12) 33%,
    rgba(255,255,255,0.4)  66%,
    rgba(255,255,255,0.0)  100%
  );
}
/* 第二层：overlay 混合 */
.lg-surface::after {
  mix-blend-mode: overlay;
  background: linear-gradient(
    135deg,
    rgba(255,255,255,0.0)  0%,
    rgba(255,255,255,0.32) 33%,
    rgba(255,255,255,0.6)  66%,
    rgba(255,255,255,0.0)  100%
  );
  box-shadow: 0 0 0 0.5px rgba(255,255,255,0.5) inset,
              0 1px 3px rgba(255,255,255,0.25) inset,
              0 1px 4px rgba(0,0,0,0.35);
}
```

#### C · Vanilla JS 鼠标追踪（弹性形变 + 边框高光联动）

```javascript
// LiquidGlass.js — 全局单例，管理所有 .lg-surface 元素
class LiquidGlassController {
  constructor() {
    this.mouse = { x: 0, y: 0 };
    this.elements = new Set();
    document.addEventListener('mousemove', e => {
      this.mouse.x = e.clientX;
      this.mouse.y = e.clientY;
      this._updateAll();
    });
  }

  register(el) { this.elements.add(el); }
  unregister(el) { this.elements.delete(el); }

  _updateAll() {
    for (const el of this.elements) this._update(el);
  }

  _update(el) {
    const rect   = el.getBoundingClientRect();
    const cx     = rect.left + rect.width  / 2;
    const cy     = rect.top  + rect.height / 2;
    const dx     = this.mouse.x - cx;
    const dy     = this.mouse.y - cy;

    // 距元素边缘的距离
    const edgeDx = Math.max(0, Math.abs(dx) - rect.width  / 2);
    const edgeDy = Math.max(0, Math.abs(dy) - rect.height / 2);
    const edgeDist = Math.hypot(edgeDx, edgeDy);
    const zone = 200;
    if (edgeDist > zone) {
      el.style.transform = '';
      el.style.setProperty('--lg-grad-angle', '135deg');
      return;
    }

    const fade = 1 - edgeDist / zone;
    const dist = Math.hypot(dx, dy) || 1;
    const nx = dx / dist, ny = dy / dist;
    const stretch = Math.min(dist / 300, 1) * 0.15 * fade;

    // 弹性形变
    const sx = 1 + Math.abs(nx) * stretch * 0.3 - Math.abs(ny) * stretch * 0.15;
    const sy = 1 + Math.abs(ny) * stretch * 0.3 - Math.abs(nx) * stretch * 0.15;
    const tx = dx * 0.015 * fade;
    const ty = dy * 0.015 * fade;
    el.style.transform = `translate(${tx}px,${ty}px) scaleX(${sx.toFixed(3)}) scaleY(${sy.toFixed(3)})`;

    // 鼠标偏移角度 → 边框高光渐变方向
    const offsetX = ((this.mouse.x - cx) / rect.width)  * 100;
    const offsetY = ((this.mouse.y - cy) / rect.height) * 100;
    const angle = 135 + offsetX * 1.2;
    el.style.setProperty('--lg-grad-angle', `${angle}deg`);
    el.style.setProperty('--lg-offset-x', offsetX.toFixed(1));
    el.style.setProperty('--lg-offset-y', offsetY.toFixed(1));
  }
}

window.liquidGlass = new LiquidGlassController();

// 自动注册：有 .lg-surface 类的元素挂载后自动加入
const lgObserver = new MutationObserver(mutations => {
  mutations.forEach(m => m.addedNodes.forEach(n => {
    if (n.nodeType === 1) {
      if (n.classList?.contains('lg-surface')) window.liquidGlass.register(n);
      n.querySelectorAll?.('.lg-surface').forEach(el => window.liquidGlass.register(el));
    }
  }));
});
lgObserver.observe(document.body, { childList: true, subtree: true });
```

---

### 3.2 视觉语言规范

#### 颜色系统（深色主模式）

| 角色 | 值 |
|---|---|
| 页面背景 | `#08090d` → `#0e1118`（渐变） |
| 玻璃填充 | `rgba(255,255,255,0.06)` |
| 玻璃边框高光 | `rgba(255,255,255,0.20)` |
| 强调蓝 | `#0a84ff`（iOS 系统蓝） |
| 强调绿 | `#30d158`（iOS 系统绿） |
| 强调红 | `#ff453a`（iOS 系统红） |
| 强调琥珀 | `#ffd60a` |
| 主文字 | `#f5f5f7`（Apple 白） |
| 次要文字 | `rgba(245,245,247,0.60)` |
| 三级文字 | `rgba(245,245,247,0.30)` |

#### 字体

- **界面字体**：`-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Helvetica Neue', sans-serif`
- **等宽字体**：`'SF Mono', 'Fira Code', 'Consolas', monospace`（仪表数值、频率）
- **字重**：标签 300，正文 400，副标题 600，仪表数值 700

#### 圆角

| 元素 | 值 |
|---|---|
| 大面板（地图、设置分组） | `24px` |
| 普通卡片 | `20px` |
| 小组件（仪表块） | `16px` |
| 按钮 / pill | `999px` |
| 指令芯片 | `12px` |

#### 阴影

```css
/* 标准玻璃浮层 */
box-shadow: 0 8px 32px rgba(0,0,0,0.35),
            0 1px 0 rgba(255,255,255,0.12) inset;
/* 悬浮中的玻璃（更高） */
box-shadow: 0 16px 48px rgba(0,0,0,0.50),
            0 1px 0 rgba(255,255,255,0.15) inset;
```

---

### 3.3 逐组件改版规格

#### 导航栏 → 浮动胶囊菜单栏

```
当前：Bootstrap 全宽 solid navbar
目标：
  - 脱离顶边，四周留 12px 间距
  - 磨砂玻璃胶囊：backdrop-blur(20px)，border-radius: 999px
  - 宽度：内容自适应，水平居中
  - 品牌图标：✈ 渐变填充（#0a84ff → #30d158）
  - 状态徽章：填充色胶囊（无边框），绿/红背景 + 白字
  - 导航链接：无下划线纯文字，hover = 轻微背景高亮
  - 高度：44px（iOS 标准点击目标）
  - 移动端：收折为汉堡菜单
```

CSS 关键值：
```css
html.theme-apple .navbar {
  position: fixed; top: 12px; left: 50%; transform: translateX(-50%);
  width: auto; min-width: 480px; max-width: calc(100vw - 24px);
  height: 44px; border-radius: 999px;
  background: rgba(255,255,255,0.08);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid rgba(255,255,255,0.15);
  box-shadow: 0 8px 24px rgba(0,0,0,0.4), 0 1px 0 rgba(255,255,255,0.10) inset;
  z-index: 1050;
}
```

#### 仪表卡片 → 玻璃仪表块

```
当前：Bootstrap card grid，白底，扁平阴影
目标：
  - 玻璃块：4 列网格，border-radius: 16px
  - 背景：rgba(255,255,255,0.07) + backdrop-blur(20px)
  - 边框：上/左 1px rgba(255,255,255,0.15)（高光），下/右 1px rgba(0,0,0,0.2)
  - 标签：11px，letter-spacing 0.06em，全大写，rgba(245,245,247,0.50)
  - 数值：28px SF Pro Display 700，白色
    - 高度（ALT）：千位加逗号
    - VS：爬升 = #30d158，下降 = #ff453a，平飞 = 白色
  - 数据变化时：200ms 白色内发光闪烁后渐隐
```

#### 地图面板 → 沉浸式玻璃框

```
当前：Tab 切换地图，白色边框，扁平
目标：
  - 地图填满左列，移除 Tab，地图始终可见
  - 附近机场改为底部上拉抽屉（iOS bottom sheet）
  - 地图容器：border-radius: 24px，overflow: hidden
  - 保留 CartoDB Dark Matter 瓦片
  - 飞机标记：发光环动画（移动时脉冲）
  - 地图控制按钮：translucent 玻璃胶囊，非不透明实体按钮
  - 中国 RVSM 横幅：amber 玻璃条，固定在地图顶部内侧，非全页
  - 航线：渐变描边（#0a84ff），虚线 dash-offset 动画
  - 交通目标：玻璃圆圈 + 呼号，按相对高度着色
```

#### 通讯记录 → 聊天气泡（iMessage 风格）

```
当前：简单 div 列表，发送方 + 文本
目标：
  - ATC 消息：左对齐气泡，玻璃背景 rgba(255,255,255,0.10)，白色文字
  - 飞行员消息：右对齐气泡，填充 #0a84ff，白色文字
  - SYSTEM 消息：居中胶囊，小字，muted——无气泡
  - ATIS：独特样式——amber 玻璃气泡，全宽
  - 时间戳：气泡尾部内联，微小字，3s 后渐隐
  - 新消息：从底部 spring 弹入动画
  - 长放行许可：截断 + "…展开" 折叠
```

CSS 关键值：
```css
html.theme-apple .chat-bubble-atc {
  align-self: flex-start;
  background: rgba(255,255,255,0.10);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 18px 18px 18px 4px;
  padding: 8px 14px;
  color: #f5f5f7;
  animation: bubbleIn 0.28s cubic-bezier(0.34, 1.56, 0.64, 1);
}
html.theme-apple .chat-bubble-pilot {
  align-self: flex-end;
  background: #0a84ff;
  border-radius: 18px 18px 4px 18px;
  padding: 8px 14px;
  color: #fff;
}
@keyframes bubbleIn {
  from { opacity: 0; transform: translateY(12px) scale(0.94); }
  to   { opacity: 1; transform: translateY(0)    scale(1);    }
}
```

#### PTT 按钮 → Dynamic Island 风格激活

```
当前：大号 Bootstrap btn-primary，全宽
目标：
  - 待机：大号胶囊，玻璃表面，边框 1px rgba(255,255,255,0.20)
    文字：PUSH TO TALK，SF Pro Display semibold，次要文字色
    左侧麦克风图标
  - 按下中：填充 #ff453a（录音红），白色"RECORDING…"
    图标替换为 4 根振幅振动的波形柱
    按钮边缘红色光晕辐射
  - 处理中：iOS 菊花加载动画，文字"处理中…"，#ffd60a 色调
```

CSS 关键值：
```css
html.theme-apple #ptt-btn {
  --lg-radius: 999px;
  width: 100%; height: 64px;
  background: rgba(255,255,255,0.08);
  backdrop-filter: blur(20px) saturate(140%);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 999px;
  color: rgba(245,245,247,0.70);
  font: 600 17px/1 -apple-system, sans-serif;
  letter-spacing: 0.02em;
  transition: all 0.15s ease-out;
  box-shadow: 0 4px 16px rgba(0,0,0,0.3), 0 1px 0 rgba(255,255,255,0.1) inset;
}
html.theme-apple #ptt-btn.recording {
  background: #ff453a;
  color: #fff;
  box-shadow: 0 0 0 6px rgba(255,69,58,0.25), 0 0 0 12px rgba(255,69,58,0.10);
  animation: pttGlow 1.6s ease-in-out infinite;
}
@keyframes pttGlow {
  0%,100% { box-shadow: 0 0 0 6px rgba(255,69,58,0.25), 0 0 0 12px rgba(255,69,58,0.10); }
  50%     { box-shadow: 0 0 0 10px rgba(255,69,58,0.20), 0 0 0 20px rgba(255,69,58,0.05); }
}
```

#### 无线电频道切换 → 分段控件

```
当前：Bootstrap btn-group radio
目标：
  - 单个磨砂玻璃胶囊容器，border-radius: 999px
  - 两段：ATC（📡）· 机组（👥）
  - 已选：白色填充（#f5f5f7），深色文字
  - 未选：透明，文字 rgba(245,245,247,0.60)
  - 切换动画：白色胶囊滑动（left/right transition）
```

#### 指令卡片条 → 悬浮 HUD 条

```
当前：右列底部水平滚动 pill 芯片
目标：
  - 悬浮在地图底部上方，NOT 在右列内
  - 玻璃条：border-radius: 18px，backdrop-blur(20px)，padding 8px 14px
  - 每个芯片：稍微凸起的玻璃表面，border-radius: 12px
    等宽数值粗体，标签用强调色
    新指令：弹入 spring 动画；过期：渐隐 + 缩小
  - HDG = #0a84ff，ALT = #30d158，SPD = #64d2ff，SQ = #ffd60a
```

#### 设置页 → iOS 设置 App 风格

```
当前：Bootstrap 手风琴，标准表单控件
目标：
  - 背景：深空 #08090d，非白色卡片
  - 每分组：独立玻璃卡片，border-radius: 20px
    分组标题：小型全大写标签，muted，12px，卡片外侧上方
  - 表单控件：
    文字输入：玻璃凹槽，仅底部线（无方框）
    开关：iOS UISwitch 样式（白色圆钮，开启 = 绿色填充）
    选择器：自定义玻璃下拉，右侧角形图标
  - 主题选择器：水平分段控件（同无线电切换）
  - 保存按钮：底部固定，全宽填充 #0a84ff 胶囊
```

---

### 3.4 动效规范

| 触发时机 | 动画内容 | 时长 | 缓动 |
|---|---|---|---|
| 页面加载 | 各块依次渐入（间隔 20ms） | 400ms | `ease-out` |
| Socket 数据更新 | 数字滚动变化 | 180ms | `ease-out` |
| 新聊天消息 | 从底部滑入 + 淡入 | 280ms | `cubic-bezier(0.34,1.56,0.64,1)` |
| PTT 按下 | 缩放 0.96→1.0 + 颜色闪变 | 150ms | `ease-out` |
| 面板展开 | scale(0.97→1) + blur(4→0) + opacity | 320ms | Apple ease |
| 地图标记移动 | 平滑插值位移 | 400ms | `linear` |
| 指令芯片出现 | scale(0.85→1) + opacity spring | 260ms | `cubic-bezier(0.34,1.56,0.64,1)` |
| 指令芯片过期 | 渐隐 + scale(1→0.9) | 200ms | `ease-in` |
| Toast 通知 | 从右上滑入 | 300ms | `cubic-bezier(0.34,1.56,0.64,1)` |

Apple ease = `cubic-bezier(0.25, 0.1, 0.25, 1.0)`

---

### 3.5 响应式断点

| 屏幕尺寸 | 布局 |
|---|---|
| ≥1200px（桌面） | 双列 7:5，地图填满左侧，所有面板可见 |
| 768–1199px（平板横屏） | 双列 6:6，地图缩短，右列可滚动 |
| 576–767px（平板竖屏） | 单列，地图固定 300px，右列在下方 |
| <576px（手机） | 单列，地图 220px，PTT 全宽固定在底部 |

---

### 3.6 技术约束

1. **保留 Bootstrap 5.3** —— 以 CSS 变量覆盖 + 自定义类扩展，**不删除**现有 Bootstrap 类
2. **Leaflet.js 地图** —— 不改动 Leaflet 内部；只给容器加 `border-radius` + `overflow:hidden`
3. **Socket.IO 实时更新** —— 所有 DOM 更新保持 Vanilla JS；CSS transition 处理动画
4. **无构建流程** —— 纯 CSS + Vanilla JS，不引入 React / Vue / npm
5. **SVG 滤镜兼容性** —— Chromium 完整支持；Safari / Firefox 自动降级为纯 `backdrop-filter` 模糊
6. **主题门控** —— 所有 Apple 样式仅在 `html.theme-apple` 存在时生效，不影响经典主题
7. **置换贴图** —— 从 liquid-glass-react `src/utils.ts` 提取三个 base64 图片，嵌入 CSS 文件

---

### 3.7 实现策略（分四阶段）

#### 阶段一：基础层（CSS 变量 + SVG 滤镜）

1. 从 `https://raw.githubusercontent.com/rdev/liquid-glass-react/master/src/utils.ts` 提取三个 base64 置换贴图（standard / polar / prominent）
2. 新建 `static/css/theme-apple.css`，作用域严格限定于 `html.theme-apple`
3. 在 `base.html` `<body>` 末尾注入隐藏 SVG（包含 `#liquid-glass` filter + `#lg-edge-mask` gradient）
4. 定义 CSS 变量：`--lg-radius`、`--glass-bg`、`--glass-blur`、`--apple-*` 颜色系列

#### 阶段二：逐组件样式覆盖

```css
/* 文件：static/css/theme-apple.css */

/* 全局背景 */
html.theme-apple body {
  background: linear-gradient(160deg, #08090d 0%, #0e1118 100%);
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display',
               'Helvetica Neue', sans-serif;
}

/* 玻璃卡片基类 */
html.theme-apple .card,
html.theme-apple .lg-surface {
  background: rgba(255,255,255,0.06) !important;
  border: 1px solid rgba(255,255,255,0.12) !important;
  backdrop-filter: blur(20px) saturate(140%);
  -webkit-backdrop-filter: blur(20px) saturate(140%);
  filter: url(#liquid-glass);
  border-radius: 20px !important;
  box-shadow: 0 8px 32px rgba(0,0,0,0.35),
              0 1px 0 rgba(255,255,255,0.12) inset !important;
  /* 边框高光伪元素（见 § 3.1 B） */
}

/* 仪表数值字体 */
html.theme-apple .gauge-card h4 {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 1.75rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #f5f5f7;
}

/* 导航栏 */
html.theme-apple .navbar { /* 见 § 3.3 */ }

/* PTT 按钮 */
html.theme-apple #ptt-btn { /* 见 § 3.3 */ }

/* 聊天气泡 — 通过 JS 在 appendMessage() 时添加 chat-bubble-atc / chat-bubble-pilot 类 */
html.theme-apple .chat-bubble-atc { /* 见 § 3.3 */ }
html.theme-apple .chat-bubble-pilot { /* 见 § 3.3 */ }
```

#### 阶段三：Vanilla JS 鼠标追踪

在 `static/js/liquid-glass.js` 中实现（见 § 3.1 C），在 `base.html` 底部引入：
```html
<script src="/static/js/liquid-glass.js"></script>
```

#### 阶段四：进阶组件

- 底部抽屉（附近机场面板 → bottom sheet）
- 分段控件（无线电切换 / 主题选择）
- iMessage 气泡（修改 `appendMessage()` 函数，添加气泡类）
- 悬浮 HUD 指令条（脱离右列，绝对定位于地图下方）

---

## 4 · 要求输出的内容

请生成以下文件：

1. **`static/css/theme-apple.css`**（约 600 行）
   - 完整 Liquid Glass 主题样式表
   - 所有规则严格限定于 `html.theme-apple` 作用域
   - 包含 CSS 变量定义、玻璃表面基类、所有组件覆盖、动效 `@keyframes`

2. **`static/js/liquid-glass.js`**（约 120 行）
   - `LiquidGlassController` 类（见 § 3.1 C）
   - SVG 滤镜动态注入（附 standard 模式置换贴图 base64）
   - MutationObserver 自动注册新增 `.lg-surface` 元素

3. **`base.html` 修改差异**
   - `<link>` 引入 `theme-apple.css`（始终加载，`html.theme-apple` 控制生效）
   - `<script>` 引入 `liquid-glass.js`
   - `<body>` 末尾隐藏 SVG（`#liquid-glass` filter 定义）

4. **`dashboard.html` 修改差异**
   - 为仪表卡片添加 `.lg-surface` 类
   - 修改 `appendMessage()` 添加气泡类
   - 将指令卡片条改为绝对定位悬浮 HUD

**实现要求：不破坏任何现有 JS 行为，仅改变视觉层。**
