# OpenATC DataBridge ↔ OpenFrequency 兼容文档

> 插件版本：1.2
> 适用平台：Windows x64，X-Plane 11.55+ / X-Plane 12
> 文档目的：说明本插件向 OpenFrequency 传输的内容、网络实现和网络接口，便于
> OpenFrequency 原作者按需调整 `core/xplane_provider.py` 或后续 C++ 核心。

---

## 1. 结论先行

X-Plane 12 自带官方 **Local Web API**（默认 `127.0.0.1:8086`），OpenFrequency 的
X-Plane 数据源直接使用该 REST API。X-Plane 11 **没有**这个 API。

本插件在 X-Plane 11 内部启动一个仅监听环回地址的 HTTP/1.1 服务，模拟
OpenFrequency 实际用到的那部分 Local Web API：

- `GET /api/capabilities`
- `GET /api/v3/datarefs?filter[name]=...`
- `GET /api/v3/datarefs/{id}`
- `GET /api/v3/datarefs/{id}/value`
- `PATCH /api/v3/datarefs/{id}/value`
- `GET /api/v3/commands?filter[name]=...`
- `GET /api/v3/commands/{id}`
- `POST /api/v3/commands/{id}/activate`
- `OPTIONS *`（CORS 预检）

因此 **OpenFrequency 不需要改代码**：把 `simulator.provider` 设为 `xplane`，
或让其按 `X-Plane.exe` 进程自动识别，即可在 X-Plane 11 上得到与 X-Plane 12
一致的数据源体验。插件同时保留原有的 UDP 遥测/控制通道（OpenATC 客户端使用）。

```
┌──────────────────────────── X-Plane 11 进程 ────────────────────────────┐
│  OpenATC DataBridge.xpl                                                 │
│                                                                          │
│   flight loop (sim 线程, 10 Hz)                                          │
│     ├─ 读取 dataref → 线程安全快照 (shared_ptr)                          │
│     ├─ 执行写队列 / 命令队列 (XPLMSetData*, XPLMCommandOnce)             │
│     ├─ UDP JSON → 127.0.0.1:49010 / 49020-49029  (OpenATC)               │
│     └─ TCAS 目标注入 (override_TCAS)                                     │
│                                                                          │
│   HTTP 线程 (Winsock, 127.0.0.1:8086, 仅环回)                            │
│     └─ 只读写内存快照 / 队列，不直接调用 XPLM                            │
└──────────────────────────────────────────────────────────────────────────┘
             ▲ HTTP/1.1 REST                  ▼ UDP JSON / 控制指令
┌────────────┴────────────┐      ┌───────────┴───────────────────────────┐
│ OpenFrequency           │      │ OpenATC 客户端 / 原生核心              │
│ core/xplane_provider.py │      │ (AtcShell / OpenATC_Core)              │
└─────────────────────────┘      └────────────────────────────────────────┘
```

---

## 2. 传输的内容

### 2.1 Local Web API（给 OpenFrequency）

插件对外暴露的 dataref 与 OpenFrequency `core/xplane_provider.py` 中的
`DATAREFS`、`TCAS_DREFS`、`_AP_DREFS`、`FAILURE_DREFS` 一一对应。

| # | dataref | 类型 | 单位/说明 | 读/写 |
|---|---------|------|-----------|-------|
| 1 | `sim/flightmodel/position/latitude` | double | 纬度，度 | 读 |
| 2 | `sim/flightmodel/position/longitude` | double | 经度，度 | 读 |
| 3 | `sim/flightmodel/position/elevation` | double | 海拔，米 MSL | 读 |
| 4 | `sim/flightmodel/position/psi` | float | 真航向，度 | 读 |
| 5 | `sim/flightmodel/position/theta` | float | 俯仰，度 | 读 |
| 6 | `sim/flightmodel/position/phi` | float | 坡度，度 | 读 |
| 7 | `sim/flightmodel/position/indicated_airspeed` | float | 指示空速，节 | 读 |
| 8 | `sim/flightmodel/position/vh_ind_fpm` | float | 垂直速度，英尺/分 | 读 |
| 9 | `sim/flightmodel/engine/ENGN_N1_[0]` | float array | N1，%；客户端取首元素 | 读 |
| 10 | `sim/flightmodel/engine/ENGN_EGT_c[0]` | float array | EGT，°C；取首元素 | 读 |
| 11 | `sim/flightmodel/engine/ENGN_FF_[0]` | float array | 燃油流量，kg/s；取首元素 | 读 |
| 12 | `sim/aircraft/parts/acf_gear_deploy` | float array | 各轮放下比例 0–1；取首元素 | 读 |
| 13 | `sim/flightmodel/controls/flaprat` | float | 襟翼实际位置 0–1 | 读 |
| 14 | `sim/cockpit/radios/transponder_code` | int | 应答机代码 0–7777 | 读/写 |
| 15 | `sim/cockpit2/radios/actuators/com1_frequency_hz_833` | int | COM1，kHz（118700 = 118.700 MHz） | 读/写 |
| 16 | `sim/aircraft/view/acf_ICAO` | string | 机型 ICAO，如 `B738` | 读 |
| 17 | `sim/aircraft/view/acf_descrip` | string | 机型描述 | 读 |
| 18 | `sim/cockpit2/tcas/targets/position/lat` | float[64] | 交通目标纬度，度 | 读 |
| 19 | `sim/cockpit2/tcas/targets/position/lon` | float[64] | 交通目标经度，度 | 读 |
| 20 | `sim/cockpit2/tcas/targets/position/ele` | float[64] | 交通目标海拔，米 MSL | 读 |
| 21 | `sim/cockpit2/tcas/targets/position/vvi` | float[64] | 交通目标垂直速度，米/秒 | 读 |
| 22 | `sim/cockpit2/tcas/targets/position/psi` | float[64] | 交通目标真航向，度 | 读 |
| 23 | `sim/cockpit2/tcas/targets/position/groundspeed` | float[64] | 交通目标地速，米/秒 | 读 |
| 24 | `sim/cockpit2/tcas/targets/position/weight_on_wheels` | float[64] | 交通目标在地面标志 0/1 | 读 |
| 25 | `sim/cockpit2/tcas/targets/flight_id` | string[64] | 每槽 8 字节呼号，尾部 NUL/空格已裁剪 | 读 |
| 26 | `sim/autopilot/heading_mag` | float | 航向目标，度（磁） | 读/写 |
| 27 | `sim/autopilot/altitude` | float | 高度目标，英尺 | 读/写 |
| 28 | `sim/autopilot/airspeed` | float | 速度目标，节（IAS） | 读/写 |
| 29 | `sim/cockpit/autopilot/autopilot_state` | int | AP 状态位 | 读 |
| 30 | `sim/cockpit2/autopilot/heading_mode` | int | 航向模式状态 | 读 |
| 31 | `sim/cockpit2/autopilot/altitude_mode` | int | 高度模式状态 | 读 |
| 32 | `sim/cockpit2/autopilot/speed_mode` | int | 速度模式状态 | 读 |
| 33 | `sim/operation/failures/rel_engfai0` | int | 1 号发动机失效；6 = 失效 | 读/写 |
| 34 | `sim/operation/failures/rel_engfai1` | int | 2 号发动机失效 | 读/写 |
| 35 | `sim/operation/failures/rel_hydpmp` | int | 液压泵失效 | 读/写 |
| 36 | `sim/operation/failures/rel_gen_esys` | int | 发电机失效 | 读/写 |
| 37 | `sim/operation/failures/rel_gear_act` | int | 起落架作动失效 | 读/写 |
| 38 | `sim/operation/failures/rel_lagear1` | int | 左主起落架失效 | 读/写 |
| 39 | `sim/operation/failures/rel_lagear2` | int | 右主起落架失效 | 读/写 |
| 40 | `sim/operation/failures/rel_lagear3` | int | 前起落架失效 | 读/写 |

可执行命令（`/api/v3/commands`）：

| # | 命令 | 触发时机 |
|---|------|----------|
| 1 | `sim/autopilot/heading` | `set_autopilot_heading()` 写入航向目标后 |
| 2 | `sim/autopilot/altitude_hold` | `set_autopilot_altitude()` 写入高度目标后 |
| 3 | `sim/autopilot/autothrottle_toggle` | `set_autopilot_speed()` 写入速度目标后 |

> 编号仅表示插件内部顺序，客户端应通过 `filter[name]` 查询 id，不要写死。
> 遇到自定义航电（ZIBO、FBW 等）时，默认 `sim/autopilot/*` dataref 可能被机模
> 忽略，这与 X-Plane 12 原生 Web API 的行为一致。

### 2.2 UDP JSON 遥测（给 OpenATC 客户端）

默认每 0.1 秒发送一包 JSON 到 `127.0.0.1:49010` 以及 `49020–49029`（可在 INI 中
调整）。字段如下（完整字段以 `dllmain.cpp` 中的 `snprintf` 为准）：

| 字段 | 含义 | 单位 |
|------|------|------|
| `alt` / `agl_ft` | 海拔 / 离地高度 | ft |
| `spd` / `gs` | 指示空速 / 地速 | kt |
| `hdg` / `true_hdg` / `mag_hdg` / `magvar` | 真航向 / 真航向 / 磁航向 / 磁差 | ° |
| `vs` / `pitch` / `bank` | 垂直速度 / 俯仰 / 坡度 | fpm / ° / ° |
| `lat` / `lng` | 纬度 / 经度 | ° |
| `com1` / `com2` / `com1_stdby` / `com2_stdby` | COM 频率 | MHz |
| `nav1` / `nav1_stdby` / `nav1_cdi` / `nav1_gs` | NAV1 频率与偏差 | MHz / dots |
| `squawk` / `xpdr_mode` / `xpdr_mode_text` / `xpdr_ident` | 应答机 | — |
| `on_ground` / `gear_handle_down` / `gear_down` / `gear_ratio` | 地面与起落架 | bool / 0–1 |
| `flap_ratio` / `flap_handle_ratio` | 襟翼实际/手柄位置 | 0–1 |
| `parking_brake` / `engine_running` / `beacon` / `landing_lights` | 刹车/发动机/灯光 | bool |
| `ap_alt_ft` / `ap_alt_hold_ft` / `ap_hdg_mag` | 自动驾驶目标 | ft / ft / ° |
| `ap_on` / `ap_approach` / `ap_nav` / `ap_alt_hold` | AP 状态 | 0–2 |
| `zulu_sec` / `wind_dir_true` / `wind_speed_kt` / `wind_gust_kt` | 时间与风 | s / ° / kt / kt |
| `qnh_inhg` / `vis_sm` / `temp_c` / `dewpoint_c` | 气压/能见度/温度/露点 | inHg / SM / °C / °C |
| `cloud_cover` / `cloud_base_ft` | 云量/云底 | — / ft |
| `aircraft_type` / `tailnum` | 机型 ICAO / 注册号 | — |
| `plugin` | 插件标识 | — |

### 2.3 UDP 控制指令（给 OpenATC / 其他本机客户端）

控制端口默认 `127.0.0.1:49031`，UDP 文本，每行一条：

```
SET COM1 118.700            调谐 COM1（旧频率进入备用位）
SET COM1_STANDBY 121.500    只写 COM1 备用频率
SET COM2 121.800
SET SQUAWK 3361
SET XPDR 3                  0=off 1=stby 2=on 3=alt 4=test 5=gnd 6=TA 7=TA/RA
SET AI 1                    可见 AI 机模开关（1/0）
TRAFFIC 2                   后跟 2 行交通目标：
CCA1234,31.21,121.51,3280,180,155,0
CES5678,31.22,121.52,2952,90,136,1
TRAFFIC 0                   清除交通并释放 AI 飞机
```

交通目标行格式：`呼号,lat,lon,alt_ft,hdg_deg,gs_kt,on_ground`。收到 `TRAFFIC`
后插件使用官方 `override_TCAS` 接口把目标发布到 X-Plane 的 TCAS 数组；是否
允许由 `EnableTcasTraffic` 控制，是否同时生成可见 AI 机模由 `EnableAiPlanes`
控制。

---

## 3. 网络实现

### 3.1 传输层

| 项目 | 实现 |
|------|------|
| 协议 | HTTP/1.1（TCP） |
| 监听地址 | `127.0.0.1:<WebApiPort>`，默认 `8086`，**只绑定环回地址** |
| 连接复用 | 支持 keep-alive；空闲 500 ms 后关闭连接 |
| 并发模型 | 单 HTTP 线程，顺序处理连接；一次一个客户端 |
| 读超时 / 写超时 | 500 ms（`SO_RCVTIMEO` / `SO_SNDTIMEO`） |
| `select` 轮询 | 150 ms，保证停止插件时线程能在约 150 ms 内退出 |
| 请求头上限 | 16 KiB |
| 请求体上限 | 64 KiB |
| 分块传输 | 不支持 `Transfer-Encoding: chunked`（返回 400） |
| TLS | 不支持（本机环回，无需 TLS） |
| CORS | 所有响应带 `Access-Control-Allow-Origin: *`，支持 `OPTIONS` |

### 3.2 线程模型与 dataref 安全

X-Plane 的 XPLM dataref 读写只允许在模拟器主线程（飞行循环）执行。插件采用
“快照 + 写队列”的方式，HTTP 线程从不直接调用 XPLM：

1. **快照（读路径）**
   - 飞行循环每 0.1 s 调用 `UpdateSnapshot()`，在主线程读取全部 dataref。
   - 结果写入 `std::shared_ptr<const DatarefValue>` 快照表，由 `std::mutex` 保护。
   - HTTP 线程 `Read()` 只复制 `shared_ptr` 和值，不触碰 XPLM。
2. **写队列（写路径）**
   - `PATCH /datarefs/{id}/value` 只把写入请求放入队列并立即返回 200。
   - 下一个飞行循环 `ProcessPending()` 在主线程执行 `XPLMSetDataf/d/i/...`，
     典型延迟 ≤ 100 ms。
   - 写入前用 `XPLMCanWriteDataRef()` 检查；只读 dataref 会被静默忽略。
3. **命令队列**
   - `POST /commands/{id}/activate` 入队，主线程调用 `XPLMCommandOnce()`。
4. **生命周期**
   - 监听线程在 `XPluginEnable` 启动，在 `XPluginDisable` / `XPluginStop` 停止。
   - 停止时先 `join` HTTP 线程，再 `WSACleanup()`，避免后台线程访问已释放资源。

### 3.3 数值与字符串编码

- 标量：JSON number；`NaN` / `Inf` 统一输出 `0`，避免产生非法 JSON。
- 数组：JSON array；`ENGN_N1_[0]` 等条目返回整个数组，客户端自行取首元素。
- 字节串 dataref（`acf_ICAO`、`acf_descrip`）：输出 JSON 字符串，裁剪尾部 NUL
  和空白；非 UTF-8 字节替换为 `�`，保证 JSON 合法。
- `flight_id`：输出 64 个字符串组成的数组，每个字符串是 8 字节槽位裁剪后的呼号，
  与 OpenFrequency `_parse_flight_id()` 的期望一致。
- JSON 字符串中的 `"`、`\` 和控制字符会被转义。

### 3.4 端口占用与降级

- 默认 `8086` 被占用（例如 X-Plane 12 自带 Web API 已启用）时，`bind()` 失败，
  插件在 `Log.txt` 中输出错误并继续提供 UDP 通道；也不会影响 X-Plane 运行。
- 解决办法：在 `OpenATC_DataBridge.ini` 中设置 `WebApiPort=8087`，并在
  OpenFrequency 配置中设置 `simulator.xplane_web_port = 8087`；或在 X-Plane 12
  中关闭本插件的 Web API（`WebApiEnabled=0`）并使用官方 API。

---

## 4. 网络接口

Base URL：`http://127.0.0.1:8086`

### 4.1 `GET /api/capabilities`

OpenFrequency 用来判断“已连接”。任何 2xx JSON 均可；本插件返回：

```json
{
  "api": { "versions": ["v3"] },
  "x-plane": { "version": "11.55+", "web_api": "openatc-emulated" },
  "plugin": "OpenATC DataBridge"
}
```

### 4.2 `GET /api/v3/datarefs`

查询参数：`filter[name]=<dataref 名称>`（URL 编码与否均可）。

匹配规则：先返回完全同名项，再返回名称包含该字符串的项。建议客户端使用完整
名称查询。

```http
GET /api/v3/datarefs?filter%5Bname%5D=sim/flightmodel/position/latitude
```

```json
{
  "data": [
    { "id": 1, "name": "sim/flightmodel/position/latitude", "type": "double" }
  ]
}
```

`type` 取值：`float`、`double`、`int`、`float_array`、`int_array`、`string`、
`string_array`。

### 4.3 `GET /api/v3/datarefs/{id}`

返回元数据 + 当前值：

```json
{
  "data": {
    "id": 1,
    "name": "sim/flightmodel/position/latitude",
    "type": "double",
    "value": 31.234567
  }
}
```

dataref 当前不可用（未解析到 X-Plane dataref）时 `value` 为 `null`。

### 4.4 `GET /api/v3/datarefs/{id}/value`

返回同一结构。值示例：

```json
// 标量
{"data":{"id":1,"name":"sim/flightmodel/position/latitude","type":"double","value":31.234567}}

// 字符串
{"data":{"id":16,"name":"sim/aircraft/view/acf_ICAO","type":"string","value":"B738"}}

// 数组
{"data":{"id":18,"name":"sim/cockpit2/tcas/targets/position/lat","type":"float_array","value":[31.21,31.22,0,0,...]}}

// TCAS 呼号
{"data":{"id":25,"name":"sim/cockpit2/tcas/targets/flight_id","type":"string_array","value":["CCA1234","CES5678"]}}
```

不可用时返回 `503`：

```json
{"error":{"code":503,"message":"Dataref is not available"}}
```

> OpenFrequency 的 `_get_dref()` 会捕获异常并回退到默认值，因此 503 不会中断
> 遥测循环。

### 4.5 `PATCH /api/v3/datarefs/{id}/value`

请求体必须是 JSON 对象，`data` 字段可以是标量、字符串或数组：

```http
PATCH /api/v3/datarefs/15/value
Content-Type: application/json

{"data": 118700}
```

同时兼容 X-Plane 12 风格的嵌套形式：

```json
{"data": {"value": 118700}}
```

响应：

```json
{"data":{"id":15,"name":"sim/cockpit2/radios/actuators/com1_frequency_hz_833","type":"int","ok":true}}
```

写入只是入队；真正写 dataref 发生在下一个模拟器飞行循环。只读 dataref 会返回
200 但实际被忽略（与官方 API 的“写入只读 dataref 无效果”一致）。

错误：

| 状态码 | 含义 |
|--------|------|
| 400 | 请求体不是合法 JSON、缺少 `data` 字段，或值类型不支持 |
| 404 | dataref id 不存在 |
| 405 | 方法不允许 |

### 4.6 `GET /api/v3/commands`

```http
GET /api/v3/commands?filter%5Bname%5D=sim/autopilot/heading
```

```json
{"data":[{"id":1,"name":"sim/autopilot/heading"}]}
```

### 4.7 `GET /api/v3/commands/{id}`

```json
{"data":{"id":1,"name":"sim/autopilot/heading"}}
```

### 4.8 `POST /api/v3/commands/{id}/activate`

```json
{"data":{"id":1,"name":"sim/autopilot/heading","activated":true}}
```

命令入队后由主线程执行 `XPLMCommandOnce()`。命令不存在或暂不可用时返回 503。

### 4.9 `OPTIONS *`

返回 `204 No Content`，并带：

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, PATCH, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type
```

### 4.10 状态码汇总

| 状态码 | 使用场景 |
|--------|----------|
| 200 | 成功 |
| 204 | `OPTIONS` 预检 |
| 400 | 请求格式错误、JSON 非法、`Transfer-Encoding` 不支持 |
| 404 | 路径 / dataref id / command id 不存在 |
| 405 | HTTP 方法不允许 |
| 413 | 请求体超过 64 KiB |
| 431 | 请求头超过 16 KiB |
| 503 | dataref / command 当前不可用 |

错误统一为：

```json
{"error":{"code":404,"message":"Dataref not found"}}
```

---

## 5. 安装与配置

### 5.1 插件安装

1. 把构建产物 `OpenATC_DataBridge.xpl` 重命名为 `win.xpl`，复制到：
   `X-Plane 11/Resources/plugins/OpenATC_DataBridge/64/win.xpl`
   （X-Plane 12 同样放 `Resources/plugins/OpenATC_DataBridge/64/win.xpl`）。
2. 把 `OpenATC_DataBridge.ini.example` 复制为 `OpenATC_DataBridge.ini`，与
   `win.xpl` 放在同一个目录（`Resources/plugins/OpenATC_DataBridge/64/`），
   按需修改端口。
3. 启动 X-Plane。`Log.txt` 应出现：
   `Local Web API 已监听 127.0.0.1:8086（OpenFrequency 兼容）`。

### 5.2 OpenFrequency 配置

OpenFrequency 默认会按进程识别 X-Plane；也可在配置中显式指定：

```json
{
  "simulator": {
    "provider": "xplane",
    "xplane_host": "127.0.0.1",
    "xplane_web_port": 8086,
    "xplane_root": "E:/steam/steamapps/common/X-Plane 11"
  }
}
```

`xplane_root` 只在 OpenFrequency 读取 `apt.dat` / 地面服务时使用，与 Web API
无关；Steam 版 X-Plane 11 若不在默认路径，建议显式配置。

先后启动顺序无要求：OpenFrequency 连接失败会自动重试。

### 5.3 INI 配置项

| 键 | 默认值 | 说明 |
|----|--------|------|
| `WebApiEnabled` | `1` | 是否启动 OpenFrequency 兼容 HTTP 服务 |
| `WebApiPort` | `8086` | HTTP 端口，仅绑定 127.0.0.1 |
| `ControlPort` | `49031` | UDP 控制通道起始端口（最多尝试 +8） |
| `ControlEnabled` | `1` | 是否启用 UDP 控制通道 |
| `DataPort` | `49020` | UDP 遥测端口段起始 |
| `DataPortCount` | `10` | 遥测端口段长度 |
| `PrimaryPort` | `49010` | 旧版兼容主端口；`0` 表示关闭 |
| `SendLegacyPorts` | `1` | `1` 发整个端口段，`0` 只发 `DataPort` |
| `EnableTcasTraffic` | `1` | 是否允许发布模拟交通到 TCAS |
| `EnableAiPlanes` | `1` | 是否同时生成可见 AI 机模 |

---

## 6. 给 OpenFrequency 维护者的说明

1. **无需修改 OpenFrequency 代码。** 本插件实现的是 `core/xplane_provider.py`
   实际调用的 REST 子集；`connect()`、`get_*()`、`set_*()`、`get_traffic_targets()`、
   `trigger_event()` 都已用 OpenFrequency v3.9-beta-ef 的真实 provider 代码验证通过。
2. **id 不要写死。** dataref / command 的 id 按插件内部表顺序分配（当前为 1..40
   和 1..3），未来版本可能增减。请继续使用 `filter[name]` 查询。
3. **如果希望减少 HTTP 请求数**，可以在 OpenFrequency 侧新增一个批量接口约定，
   或改用 UDP 遥测通道；插件侧可在后续版本追加，不影响现有接口。
4. **交通目标方向**：本插件已经能通过 UDP `TRAFFIC` 命令接收并注入 TCAS，
   也支持把 X-Plane 已有 TCAS 目标通过上述数组读回 OpenFrequency。如果希望
   OpenFrequency 主动向 X-Plane 11 推送交通，有两个无需大改插件的方式：
   - 继续使用 `plugins/xplane/OpenFrequencyTrafficBridge.py`（XPPython3，XP11/XP12
     均可）；
   - 由 OpenFrequency 把 `/api/xplane/traffic_targets` 的结果转成 UDP `TRAFFIC`
     行发到 `127.0.0.1:49031`。
5. **X-Plane 12**：可直接使用官方 Local Web API；若同时启用本插件的 Web API，
   请让两者使用不同端口，或在 INI 中设置 `WebApiEnabled=0`。

---

## 7. 验证与限制

已完成：

- MSVC C++20（v145，x64，静态 CRT）Release/Debug 编译通过。
- 协议测试：capabilities、dataref 查询、标量/数组/字符串读取、PATCH、命令激活、
  400/404/405/413/431/503、300 次 keep-alive 连续请求、`Connection: close`、
  超长请求头、停止时线程正常 join。
- 兼容性测试：直接导入 OpenFrequency `core/xplane_provider.py`，对 mock 后端
  完成位置、姿态、空速、发动机、起落架、襟翼、COM1、机型、TCAS 交通、
  调频、应答机、自动驾驶三轴、失效注入的全流程断言。
- 内存检查：测试程序启用 CRT 泄漏检测（`_CRTDBG_LEAK_CHECK_DF`），退出无泄漏
  报告；所有堆分配由 `std::vector` / `std::string` / `std::shared_ptr` /
  `std::unique_ptr` 管理，无裸 `new/delete`。

未覆盖：

- 尚未在完整的 X-Plane 11 运行环境中做真人飞行冒烟测试（当前开发机上的
  X-Plane 11 安装不完整）。建议首次安装后在 `Log.txt` 和 OpenFrequency 的
  `Connected to X-Plane Web API` 日志中确认。
- 单 HTTP 线程一次只服务一个客户端。OpenFrequency 作为唯一客户端没有问题；
  若同时用浏览器调试，另一个连接最多等待约 500 ms。
- 环回地址无鉴权，任何本机进程都能通过该端口读写 dataref，这与 X-Plane 12
  官方 Local Web API 的信任模型一致。
