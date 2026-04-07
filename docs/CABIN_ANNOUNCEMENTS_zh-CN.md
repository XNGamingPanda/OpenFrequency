# 客舱广播配置说明

本文档说明 OpenFrequency 当前版本中“机组模式 / 客舱广播”的配置方式、媒体导入方式，以及如何让真实航空公司的音频或视频在前端直接播放。

适用范围：

- 机组模式下的客舱广播快捷按钮
- 机组模式下的机组文字对话
- 航空公司专属广播脚本
- 本地音频文件播放
- 本地视频文件播放
- Dashboard 右下角视频小窗

## 1. 当前实现概览

当前客舱广播已经支持两种来源：

1. 文本脚本 + TTS
2. 本地音频 / 本地视频媒体文件

工作流程如下：

1. 前端切到“机组”模式。
2. 你可以直接输入文字和机组对话。
3. 也可以点击“欢迎广播 / 安全演示 / 起飞前准备 / 客舱服务 / 下降广播”等快捷按钮。
4. 后端 `CrewManager` 会根据动作名称读取对应广播脚本。
5. 如果该广播配置了 `audio` 或 `video`，系统优先播放本地媒体。
6. 如果没有配置媒体，则回退到 TTS 播报文本。
7. 不管是媒体还是 TTS，只要该广播配置了 `text`，通讯记录里都会显示这条广播内容。

## 2. 相关文件

当前主链路涉及这些文件：

- [config.json](/d:/OpenFrequency/config.json)
- [data/cabin/scripts.json](/d:/OpenFrequency/data/cabin/scripts.json)
- [core/crew_manager.py](/d:/OpenFrequency/core/crew_manager.py)
- [templates/dashboard.html](/d:/OpenFrequency/templates/dashboard.html)
- [templates/base.html](/d:/OpenFrequency/templates/base.html)
- [static/cabin_media](/d:/OpenFrequency/static/cabin_media)

## 3. 配置入口

### 3.1 航空公司识别

当前客舱广播会按以下优先级选择航空公司脚本：

1. `config.json` 中的 `cabin.airline`
2. `config.json` 中的 `user_profile.airline_icao`
3. 默认 `Generic`

你当前配置里已有：

```json
"user_profile": {
  "airline_icao": "CCA"
}
```

这意味着系统会优先尝试读取 `scripts.json` 中的 `CCA` 广播配置。

如果你想显式固定客舱广播航空公司，可在 `config.json` 中新增：

```json
"cabin": {
  "airline": "CCA"
}
```

### 3.2 广播脚本文件

广播脚本文件路径：

- [scripts.json](/d:/OpenFrequency/data/cabin/scripts.json)

这是客舱广播最核心的配置文件。

### 3.3 媒体文件目录

建议把真实航空公司的广播音频和视频放到：

- [static/cabin_media](/d:/OpenFrequency/static/cabin_media)

推荐目录结构：

```text
static/
  cabin_media/
    cca/
      welcome.mp3
      safety_demo.mp4
      takeoff_prep.mp3
      descent.mp3
      arrival.mp3
```

## 4. scripts.json 的两种写法

### 4.1 旧格式：纯文本

```json
"welcome": "Ladies and gentlemen, welcome aboard."
```

这种格式下，系统会：

- 在通讯记录显示这段文字
- 使用该航司默认 `voice` 做 TTS 播报

### 4.2 新格式：文本 + 音频 / 视频

从当前版本开始，单条广播也可以写成对象：

```json
"welcome": {
  "text": "女士们，先生们，欢迎乘坐本次航班。",
  "audio": "cabin_media/cca/welcome.mp3"
}
```

或：

```json
"safety_demo": {
  "text": "现在进行客舱安全演示，请您留意乘务员示范。",
  "video": "cabin_media/cca/safety_demo.mp4"
}
```

也可以同时带文本、音频、视频和单独声线：

```json
"arrival_prep": {
  "text": "飞机即将到达，请您再次确认安全带已系好。",
  "audio": "cabin_media/cca/arrival.mp3",
  "video": "cabin_media/cca/arrival.mp4",
  "voice": "zh-CN-XiaoxiaoNeural"
}
```

注意：

- 一旦配置了 `audio` 或 `video`，系统优先播放媒体文件
- 这条广播不会再优先走 TTS
- `text` 仍然建议保留，用于通讯记录显示

## 5. 顶层脚本结构

`scripts.json` 的基本结构如下：

```json
{
  "Generic": {
    "voice": "en-US-JennyNeural",
    "welcome": "Ladies and gentlemen, welcome aboard.",
    "door_close": "Cabin crew, arm doors and cross check.",
    "safety_demo": "Please pay attention to the safety demonstration.",
    "takeoff_prep": "Cabin crew, seats for takeoff.",
    "climb_service": "We will now begin our inflight service.",
    "descent": "We are beginning our descent. Please return to your seats.",
    "landing_prep": "Cabin crew, seats for landing.",
    "arrival_prep": "Please remain seated until the aircraft has come to a complete stop."
  },
  "CCA": {
    "voice": "zh-CN-XiaoxiaoNeural"
  }
}
```

每个顶层键代表一家航空公司，例如：

- `Generic`
- `CCA`
- `UAL`
- `CPA`

## 6. 字段说明

### 6.1 顶层 `voice`

用于指定该航空公司客舱广播默认声线。

示例：

```json
"voice": "zh-CN-XiaoxiaoNeural"
```

常见可用值示例：

- `zh-CN-XiaoxiaoNeural`
- `zh-CN-YunxiNeural`
- `en-US-JennyNeural`
- `en-US-GuyNeural`
- `en-GB-SoniaNeural`
- `ja-JP-NanamiNeural`

### 6.2 单条广播对象中的 `text`

该广播在通讯记录里显示的文本。

示例：

```json
"text": "现在进行客舱安全演示，请您留意乘务员示范。"
```

### 6.3 单条广播对象中的 `audio`

该广播要播放的本地音频文件。

示例：

```json
"audio": "cabin_media/cca/welcome.mp3"
```

推荐格式：

- `mp3`
- `wav`
- `m4a`
- `ogg`

### 6.4 单条广播对象中的 `video`

该广播要播放的本地视频文件。

示例：

```json
"video": "cabin_media/cca/safety_demo.mp4"
```

当前行为：

- Dashboard 前端会在右下角自动弹出视频小窗
- 视频会在小窗中直接播放
- 如果视频自带音轨，会一起播放
- 点击小窗关闭按钮或“停止音频”按钮可关闭

推荐格式：

- `mp4`
- `webm`

### 6.5 单条广播对象中的 `voice`

如果某一条广播希望使用不同于顶层默认值的 TTS 声线，可以在这一条里单独指定：

```json
"voice": "en-US-GuyNeural"
```

但要注意：

- 如果这条广播配置了 `audio` 或 `video`，实际播放优先用媒体文件
- `voice` 主要用于没有媒体时的 TTS 回退

## 7. 当前按钮和后端动作对应关系

Dashboard 机组模式下，当前广播按钮与后端动作如下：

- `欢迎广播` -> `welcome`
- `登机` -> `boarding`
- `安全演示` -> `safety_demo`
- `起飞前准备` -> `takeoff_prep`
- `客舱服务` -> `climb_service`
- `颠簸提示` -> `turbulence`
- `下降广播` -> `descent`
- `到达前准备` -> `arrival_prep`
- `下机` -> `deboarding`
- `停止音频` -> `stop_ambience`

其中：

- `welcome / safety_demo / takeoff_prep / climb_service / descent / arrival_prep` 会优先查找脚本键
- `boarding / deboarding / turbulence` 是动作层逻辑，部分会复用已有脚本键作为兜底
- `stop_ambience` 用于停止当前媒体播放

## 8. 如何导入真实航空公司音频 / 视频

### 8.1 放置媒体文件

先把文件放进：

- [static/cabin_media](/d:/OpenFrequency/static/cabin_media)

例如：

```text
static/cabin_media/cca/welcome.mp3
static/cabin_media/cca/safety_demo.mp4
static/cabin_media/cca/takeoff_prep.mp3
static/cabin_media/cca/descent.mp3
static/cabin_media/cca/arrival.mp3
```

### 8.2 修改 scripts.json

把对应广播改成对象格式。

例如：

```json
"CCA": {
  "voice": "zh-CN-XiaoxiaoNeural",
  "welcome": {
    "text": "女士们，先生们，欢迎乘坐本次航班，请您系好安全带并确认随身物品已妥善放置。",
    "audio": "cabin_media/cca/welcome.mp3"
  },
  "safety_demo": {
    "text": "现在进行客舱安全演示，请您留意乘务员示范。",
    "video": "cabin_media/cca/safety_demo.mp4"
  },
  "takeoff_prep": {
    "text": "客舱乘务员请就位。",
    "audio": "cabin_media/cca/takeoff_prep.mp3"
  },
  "climb_service": {
    "text": "稍后我们将开始客舱服务。"
  },
  "descent": {
    "text": "飞机即将开始下降，请您返回座位并系好安全带。",
    "audio": "cabin_media/cca/descent.mp3"
  },
  "arrival_prep": {
    "text": "为了您和他人的安全，请在飞机完全停稳并且安全带指示灯熄灭后再解开安全带。",
    "audio": "cabin_media/cca/arrival.mp3"
  }
}
```

### 8.3 重启程序

修改完成后重启 OpenFrequency。

### 8.4 在 Dashboard 测试

1. 打开 Dashboard
2. 切到“机组”模式
3. 点击相应广播按钮
4. 观察：
   - 通讯记录是否出现文本
   - 音频是否播放
   - 若配置了视频，小窗是否弹出并播放

## 9. 媒体路径写法

推荐写法：

```json
"audio": "cabin_media/cca/welcome.mp3"
```

系统会自动解析为：

```text
/static/cabin_media/cca/welcome.mp3
```

也支持直接写完整静态路径：

```json
"video": "/static/cabin_media/cca/safety_demo.mp4"
```

## 10. 显示与播放行为

当前触发一条客舱广播后，系统可能有三种表现：

### 10.1 纯文本 + TTS

如果没有配置媒体：

1. 通讯记录显示文本
2. 使用 TTS 播报

### 10.2 文本 + 音频文件

如果配置了 `audio`：

1. 通讯记录显示文本
2. 直接播放本地音频

### 10.3 文本 + 视频文件

如果配置了 `video`：

1. 通讯记录显示文本
2. Dashboard 右下角弹出视频小窗
3. 视频开始播放

### 10.4 文本 + 音频 + 视频

如果同时配置了 `audio` 和 `video`：

1. 通讯记录显示文本
2. 视频小窗显示视频画面
3. 独立音频文件同时播放

## 11. 当前前端能力范围

当前版本的视频小窗只在 Desktop Dashboard 上自动展示。

也就是说：

- Desktop Dashboard：支持视频小窗
- Mobile 页面：暂未补视频小窗

如果你后面需要，我可以继续把移动端也补上。

## 12. 机组直接对话与广播的区别

切到“机组”模式后有两类行为：

### 12.1 直接文字对话

输入框里的文字会直接发给机组，不再发给 ATC。

例如：

- “乘务长，客舱情况如何？”
- “副驾驶，帮我检查一下落地性能。”
- “请通知客舱准备落地。”

这类内容由 `CrewManager` 的对话逻辑处理。

### 12.2 预设广播按钮

点击广播按钮时，不走自由问答，而是触发固定广播动作。

这更适合：

- 登机
- 安全演示
- 起飞前准备
- 下降
- 到达前准备

## 13. 一个完整示例

`config.json`：

```json
"cabin": {
  "airline": "CCA"
}
```

`scripts.json`：

```json
{
  "CCA": {
    "voice": "zh-CN-XiaoxiaoNeural",
    "welcome": {
      "text": "女士们，先生们，欢迎乘坐本次航班，请您系好安全带并确认随身物品已妥善放置。",
      "audio": "cabin_media/cca/welcome.mp3"
    },
    "door_close": "乘务组，预位滑梯，互检舱门。",
    "safety_demo": {
      "text": "现在进行客舱安全演示，请您留意乘务员示范。",
      "video": "cabin_media/cca/safety_demo.mp4"
    },
    "takeoff_prep": {
      "text": "客舱乘务员请就位。",
      "audio": "cabin_media/cca/takeoff_prep.mp3"
    },
    "climb_service": {
      "text": "稍后我们将开始客舱服务。"
    },
    "descent": {
      "text": "飞机即将开始下降，请您返回座位并系好安全带。",
      "audio": "cabin_media/cca/descent.mp3"
    },
    "landing_prep": {
      "text": "客舱乘务员请做好着陆准备。"
    },
    "arrival_prep": {
      "text": "为了您和他人的安全，请在飞机完全停稳并且安全带指示灯熄灭后再解开安全带。",
      "audio": "cabin_media/cca/arrival.mp3"
    }
  }
}
```

## 14. 当前限制

当前已经支持“本地媒体文件引用”，但还没有以下功能：

- 设置页直接上传音频文件
- 设置页直接上传视频文件
- 设置页为每条广播选择媒体文件
- 拖拽导入媒体并自动写入 `scripts.json`

也就是说，目前“导入真实航空公司的音频/视频”的方式是：

1. 手动把文件放入 `static/cabin_media`
2. 在 `scripts.json` 中写入路径

## 15. 后续建议

如果下一步继续增强，建议按这个方向做：

- 在设置页增加“客舱广播配置”面板
- 支持前端上传音频 / 视频
- 支持前端直接试听 / 预览
- 支持为每个广播按钮绑定媒体文件
- 支持自动生成 `scripts.json`
- 支持每个航司独立媒体包

如果你要，我下一步可以继续把“设置页里直接上传并绑定客舱广播音频/视频”的功能也做掉。
