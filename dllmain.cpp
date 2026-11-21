// OpenATC DataBridge — X-Plane 11.55 / 12 插件
// 以 10 Hz 读取 ATC 所需的飞行数据，通过 UDP JSON 发送到本机 OpenATC 客户端。
//
// 数据端口（默认）：
//   49010           —— 用户指定的 ATC 接收端口（X-Plane 默认联机端口）
//   49020–49029     —— 兼容端口段，接收端任选一个可用端口
// 如果不需要兼容段，可在插件旁的 OpenATC_DataBridge.ini 中设置：
//   SendLegacyPorts=0
//
// 控制端口（默认 49031，仅绑定 127.0.0.1）：
//   SET COM1 118.700         调谐 COM1（写入备用频率后交换）
//   SET COM1_STANDBY
// 121.500 只设置 COM1 备用频率
//   SET COM2 121.800         调谐 COM2
//   SET SQUAWK 3361          设置应答机代码
//   SET XPDR 3               应答机模式（0=off,1=stby,2=on,3=alt,4=test,5=gnd）
//   TRAFFIC <n>              后跟 n 行：呼号,lat,lon,alt_ft,hdg,gs,on_ground
//   TRAFFIC 0                清除模拟交通并释放 AI 飞机
//
// TCAS 交通注入默认关闭。需要时在 INI 中设置 EnableTcasTraffic=1，
// 客户端才会把 Python 管制引擎的模拟交通发布到 X-Plane 的 TCAS 目标。
// 该行为参考原版 OpenFrequencyTrafficBridge，使用官方 override_TCAS 接口。
//
// OpenFrequency 兼容（默认 127.0.0.1:8086）：
//   X-Plane 11 没有官方的 Local Web API。插件在环回地址上模拟 X-Plane 12
//   Local Web API 的 REST 子集（/api/capabilities、/api/v3/datarefs、
//   /api/v3/commands），因此 OpenFrequency 的 X-Plane 数据源无需修改即可
//   连接。协议细节见插件根目录的 OPENFREQUENCY_BRIDGE.md。

#include "pch.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <memory>
#include <string>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <mswsock.h>    // SIO_UDP_CONNRESET

#include "LocalWebApi.h"
#include "XplmWebApiBackend.h"

#include "XPLMDataAccess.h"
#include "XPLMGraphics.h"
#include "XPLMPlanes.h"
#include "XPLMPlugin.h"
#include "XPLMProcessing.h"
#include "XPLMUtilities.h"

#pragma comment(lib, "Ws2_32.lib")

namespace
{
	// 默认值可在 OpenATC_DataBridge.ini 中覆盖，避免与他人端口冲突。
	unsigned short g_primaryPort = 49010;   // 0 = 不发送到该端口
	unsigned short g_dataPort = 49020;      // 数据端口段起始
	int g_dataPortCount = 10;               // 数据端口段长度
	constexpr int kMaxTargetPorts = 33;
	constexpr float kFlightLoopIntervalSec = 0.1f;    // 10 Hz：每 0.1 秒一包
	constexpr double kMetersToFeet = 3.28084;
	constexpr double kMetersPerSecondToKnots = 1.94384449;
	constexpr int kMaxGearCount = 16;
	constexpr int kTcasSlots = 64;
	constexpr int kMaxTraffic = 63;

	// ---- 配置 ----
	unsigned short g_controlPort = 49031;
	bool g_controlEnabled = true;
	bool g_sendLegacyPorts = true;
	// 插件侧总开关；实际是否注入由客户端设置（默认关闭）决定。
	// 插件只有在收到 TRAFFIC 指令时才会占用 AI 飞机，因此保持默认开启。
	bool g_tcasTrafficEnabled = true;
	bool g_controlSocketErrorLogged = false;
	HMODULE g_moduleHandle = nullptr;

	// ---- OpenFrequency 兼容：X-Plane 12 Local Web API 子集（仅 127.0.0.1） ----
	constexpr unsigned short kDefaultWebApiPort = 8086;
	bool g_webApiEnabled = true;
	unsigned short g_webApiPort = kDefaultWebApiPort;
	std::shared_ptr<openatc::webapi::Backend> g_webApiBackend;
	std::unique_ptr<openatc::webapi::Server> g_webApiServer;

	SOCKET gSocket = INVALID_SOCKET;
	sockaddr_in gTargets[kMaxTargetPorts] = {};
	int gTargetCount = 0;

	SOCKET gControlSocket = INVALID_SOCKET;

	// ---- 姿态 / 位置 ----
	XPLMDataRef gElevation = nullptr;          // double, 米（MSL）
	XPLMDataRef gAgl = nullptr;                // float, 米
	XPLMDataRef gIndicatedAirspeed = nullptr;  // float, 节
	XPLMDataRef gGroundSpeed = nullptr;        // float, 米/秒
	XPLMDataRef gTruePsi = nullptr;            // float, 真航向（度）
	XPLMDataRef gMagPsi = nullptr;             // float, 磁航向（度）
	XPLMDataRef gMagneticVariation = nullptr;  // float, 磁差（度，东正西负）
	XPLMDataRef gVerticalSpeed = nullptr;      // float, 英尺/分钟
	XPLMDataRef gLatitude = nullptr;           // double, 度
	XPLMDataRef gLongitude = nullptr;          // double, 度
	XPLMDataRef gPitch = nullptr;              // float, 度
	XPLMDataRef gBank = nullptr;               // float, 度

	// ---- 电台频率（_hz_833 为 kHz：118700 -> 118.700 MHz） ----
	XPLMDataRef gCom1Frequency = nullptr;
	XPLMDataRef gCom2Frequency = nullptr;
	XPLMDataRef gCom1StandbyFrequency = nullptr;
	XPLMDataRef gCom2StandbyFrequency = nullptr;
	XPLMDataRef gCom1AudioSelected = nullptr;
	XPLMDataRef gNav1Frequency = nullptr;       // 10 Hz：11090 -> 110.90 MHz
	XPLMDataRef gNav1StandbyFrequency = nullptr;
	XPLMDataRef gNav1CdiDots = nullptr;
	XPLMDataRef gNav1GsDots = nullptr;

	// ---- 应答机 ----
	XPLMDataRef gTransponderCode = nullptr; // int, 0–7777
	XPLMDataRef gTransponderMode = nullptr; // int 枚举
	XPLMDataRef gTransponderIdent = nullptr;

	// ---- 构型与地面 ----
	XPLMDataRef gOnGround = nullptr;        // int, 1 = 任一机轮触地
	XPLMDataRef gGearHandleDown = nullptr;  // int, 起落架手柄放下
	XPLMDataRef gGearDeployRatio = nullptr; // float 数组，各机轮放下比例 0–1
	XPLMDataRef gFlapRatio = nullptr;       // float, 襟翼实际位置 0–1
	XPLMDataRef gFlapHandleRatio = nullptr; // float, 襟翼手柄位置 0–1
	XPLMDataRef gParkingBrake = nullptr;    // float, 0–1
	XPLMDataRef gEngineRunning = nullptr;   // int[8]
	XPLMDataRef gBeaconLights = nullptr;    // int
	XPLMDataRef gLandingLights = nullptr;   // int

	// ---- 自动驾驶 ----
	XPLMDataRef gApAltitudeDialFt = nullptr; // float, MCP 目标高度（英尺）
	XPLMDataRef gApAltitudeHoldFt = nullptr; // float, 当前 ALT HOLD 高度
	XPLMDataRef gApHeadingMag = nullptr;     // float, MCP 航向（磁）
	XPLMDataRef gApOn = nullptr;             // int
	XPLMDataRef gApApproachStatus = nullptr; // int 0/1/2
	XPLMDataRef gApNavStatus = nullptr;      // int 0/1/2
	XPLMDataRef gApAltitudeHoldStatus = nullptr; // int 0/1/2

	// ---- 时间 / 天气 ----
	XPLMDataRef gZuluTimeSec = nullptr;      // float
	XPLMDataRef gWindDirection = nullptr;    // float[?]，真北
	XPLMDataRef gWindSpeedKts = nullptr;     // float[?]，节
	XPLMDataRef gQnhInHg = nullptr;          // float
	XPLMDataRef gVisibilityM = nullptr;      // float，米
	XPLMDataRef gTemperatureSeaLevelC = nullptr; // float
	XPLMDataRef gTemperatureAmbientC = nullptr;  // float
	XPLMDataRef gCloudType = nullptr;        // int[3]
	XPLMDataRef gCloudCoverage = nullptr;    // float[3]
	XPLMDataRef gCloudBaseM = nullptr;       // float[3]

	// ---- 机型 ----
	XPLMDataRef gAircraftIcao = nullptr;     // byte[40]
	XPLMDataRef gAircraftTailnum = nullptr;  // byte[40]

	// ---- TCAS 交通注入 ----
	XPLMDataRef gOverrideTcas = nullptr;
	XPLMDataRef gTcasModeSId = nullptr;
	XPLMDataRef gTcasFlightId = nullptr;
	XPLMDataRef gTcasRelBearing = nullptr;
	XPLMDataRef gTcasRelDistance = nullptr;
	XPLMDataRef gTcasRelAltitude = nullptr;
	bool g_tcasOwnsPlanes = false;
	bool g_tcasAcquirePending = false;

	// ---- 可见 AI 飞机（可选，使用默认 .acf 模型；名字通过 TCAS flight_id） ----
	constexpr int kMaxAiPlanes = 19;
	bool g_enableAiPlanes = true;
	std::string g_aiModelPath;
	int g_aiModelsLoaded = 0;
	XPLMDataRef gAiX[kMaxAiPlanes + 1] = {};
	XPLMDataRef gAiY[kMaxAiPlanes + 1] = {};
	XPLMDataRef gAiZ[kMaxAiPlanes + 1] = {};
	XPLMDataRef gAiPsi[kMaxAiPlanes + 1] = {};
	XPLMDataRef gAiTheta[kMaxAiPlanes + 1] = {};
	XPLMDataRef gAiPhi[kMaxAiPlanes + 1] = {};
	XPLMDataRef gAiGear[kMaxAiPlanes + 1] = {};
	XPLMDataRef gAiFlap[kMaxAiPlanes + 1] = {};
	XPLMDataRef gAiThrottle[kMaxAiPlanes + 1] = {};

	struct TrafficTarget
	{
		std::string callsign;
		double latitude = 0.0;
		double longitude = 0.0;
		double altitudeFt = 0.0;
		double headingDeg = 0.0;
		double groundSpeedKts = 0.0;
		bool onGround = false;
	};

	TrafficTarget g_traffic[kMaxTraffic];
	int g_trafficCount = 0;
	int g_trafficExpected = 0;
	bool g_tcasLoggedDisabled = false;

	// ---- 字符串 / 工具 ----

	float ReadFloat(XPLMDataRef ref, float fallback = 0.0f)
	{
		if (ref == nullptr)
		{
			return fallback;
		}
		const float value = XPLMGetDataf(ref);
		// NaN/Inf 会被 snprintf 写成 "nan"/"inf"，产生非法 JSON，
		// 导致接收端丢弃整包。
		return std::isfinite(value) ? value : fallback;
	}

	double ReadDouble(XPLMDataRef ref, double fallback = 0.0)
	{
		if (ref == nullptr)
		{
			return fallback;
		}
		const double value = XPLMGetDatad(ref);
		return std::isfinite(value) ? value : fallback;
	}

	int ReadInt(XPLMDataRef ref, int fallback = 0)
	{
		return ref != nullptr ? XPLMGetDatai(ref) : fallback;
	}

	// com*_frequency_hz_833 以 kHz 为单位（118700 = 118.700 MHz）。
	double ReadComFrequencyMhz(XPLMDataRef ref)
	{
		if (ref == nullptr)
		{
			return 0.0;
		}
		return XPLMGetDatai(ref) / 1000.0;
	}

	// nav*_frequency_hz 以 10 Hz 为单位（11090 = 110.90 MHz）。
	double ReadNavFrequencyMhz(XPLMDataRef ref)
	{
		if (ref == nullptr)
		{
			return 0.0;
		}
		return XPLMGetDatai(ref) / 100.0;
	}

	std::string JsonEscape(const std::string& text)
	{
		std::string result;
		result.reserve(text.size());
		for (char c : text)
		{
			switch (c)
			{
			case '"': result += "\\\""; break;
			case '\\': result += "\\\\"; break;
			case '\n': result += "\\n"; break;
			case '\r': result += "\\r"; break;
			case '\t': result += "\\t"; break;
			default:
				if (static_cast<unsigned char>(c) < 0x20)
				{
					result += ' ';
				}
				else
				{
					result += c;
				}
				break;
			}
		}
		return result;
	}

	std::string ReadStringDataref(XPLMDataRef ref)
	{
		if (ref == nullptr)
		{
			return {};
		}
		char buffer[64] = {};
		int bytes = XPLMGetDatab(ref, nullptr, 0, 0);
		if (bytes <= 0)
		{
			return {};
		}
		if (bytes > static_cast<int>(sizeof(buffer)) - 1)
		{
			bytes = static_cast<int>(sizeof(buffer)) - 1;
		}
		XPLMGetDatab(ref, buffer, 0, bytes);
		buffer[bytes] = '\0';
		// 去掉尾部空白。
		size_t length = std::strlen(buffer);
		while (length > 0 && (buffer[length - 1] == ' ' || buffer[length - 1] == '\t'))
		{
			buffer[--length] = '\0';
		}
		return std::string(buffer, length);
	}

	// 起落架实际放下比例：取所有机轮中的最小值。
	float ReadGearDeploymentRatio()
	{
		if (gGearDeployRatio == nullptr)
		{
			return 0.0f;
		}

		float values[kMaxGearCount] = {};
		int count = XPLMGetDatavf(gGearDeployRatio, nullptr, 0, 0);
		if (count <= 0)
		{
			return 0.0f;
		}
		if (count > kMaxGearCount)
		{
			count = kMaxGearCount;
		}
		int read = XPLMGetDatavf(gGearDeployRatio, values, 0, count);
		if (read <= 0)
		{
			return 0.0f;
		}
		float minRatio = 1.0f;
		bool found = false;
		for (int i = 0; i < read; ++i)
		{
			if (!std::isfinite(values[i]))
			{
				continue;
			}
			minRatio = found ? std::min(minRatio, values[i]) : values[i];
			found = true;
		}
		return found ? minRatio : 0.0f;
	}

	bool AnyEngineRunning()
	{
		if (gEngineRunning == nullptr)
		{
			return false;
		}
		int values[8] = {};
		int read = XPLMGetDatavi(gEngineRunning, values, 0, 8);
		for (int i = 0; i < read; ++i)
		{
			if (values[i] != 0)
			{
				return true;
			}
		}
		return false;
	}

	float ReadWeatherArray(XPLMDataRef ref)
	{
		if (ref == nullptr)
		{
			return 0.0f;
		}
		float value = 0.0f;
		if (XPLMGetDatavf(ref, &value, 0, 1) <= 0 || !std::isfinite(value))
		{
			return 0.0f;
		}
		return value;
	}

	int ReadIntArray(XPLMDataRef ref, int* values, int maxCount)
	{
		if (ref == nullptr || values == nullptr || maxCount <= 0)
		{
			return 0;
		}
		return XPLMGetDatavi(ref, values, 0, maxCount);
	}

	int ReadFloatArray(XPLMDataRef ref, float* values, int maxCount)
	{
		if (ref == nullptr || values == nullptr || maxCount <= 0)
		{
			return 0;
		}
		return XPLMGetDatavf(ref, values, 0, maxCount);
	}

	const char* CloudCoverText(int cloudType)
	{
		switch (cloudType)
		{
		case 0: return "CLR";
		case 1: return "FEW";
		case 2: return "SCT";
		case 3: return "BKN";
		case 4: return "OVC";
		case 5: return "OVC";
		default: return "CLR";
		}
	}

	// 0=off,1=stby,2=on,3=alt,4=test,5=ground,6=TA only,7=TA/RA
	// （XP11.51+ / XP12；DataRefs.txt 里的四值说明已过时）
	const char* TransponderModeText(int mode)
	{
		switch (mode)
		{
		case 0: return "off";
		case 1: return "stby";
		case 2: return "on";
		case 3: return "alt";
		case 4: return "test";
		case 5: return "ground";
		case 6: return "ta";
		case 7: return "ta_ra";
		default: return "unknown";
		}
	}

	std::string TrimAscii(std::string text)
	{
		size_t first = text.find_first_not_of(" \t\r\n");
		if (first == std::string::npos)
		{
			return {};
		}
		size_t last = text.find_last_not_of(" \t\r\n");
		return text.substr(first, last - first + 1);
	}

	std::string UpperAscii(std::string text)
	{
		for (char& c : text)
		{
			c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
		}
		return text;
	}

	double NormalizeHeading(double value)
	{
		double result = std::fmod(value, 360.0);
		if (result < 0.0)
		{
			result += 360.0;
		}
		return result;
	}

	double DistanceMeters(double lat1, double lon1, double lat2, double lon2)
	{
		constexpr double kPi = 3.14159265358979323846;
		const double p1 = lat1 * kPi / 180.0;
		const double p2 = lat2 * kPi / 180.0;
		const double dp = (lat2 - lat1) * kPi / 180.0;
		const double dl = (lon2 - lon1) * kPi / 180.0;
		const double a = std::sin(dp / 2) * std::sin(dp / 2) +
		                 std::cos(p1) * std::cos(p2) *
		                         std::sin(dl / 2) * std::sin(dl / 2);
		return 2.0 * 6371000.0 * std::asin(std::min(1.0, std::sqrt(a)));
	}

	double BearingDegrees(double lat1, double lon1, double lat2, double lon2)
	{
		constexpr double kPi = 3.14159265358979323846;
		const double p1 = lat1 * kPi / 180.0;
		const double p2 = lat2 * kPi / 180.0;
		const double dl = (lon2 - lon1) * kPi / 180.0;
		const double y = std::sin(dl) * std::cos(p2);
		const double x = std::cos(p1) * std::sin(p2) -
		                 std::sin(p1) * std::cos(p2) * std::cos(dl);
		return NormalizeHeading(std::atan2(y, x) * 180.0 / kPi);
	}

	unsigned int StableModeSId(const std::string& callsign)
	{
		// FNV-1a，保证同一呼号在每帧得到相同的 24 位 TCAS 目标 ID。
		unsigned int hash = 2166136261u;
		for (unsigned char c : callsign)
		{
			hash ^= c;
			hash *= 16777619u;
		}
		unsigned int id = hash & 0xFFFFFFu;
		return id == 0 ? 1u : id;
	}

	// ---- 配置 ----

	std::wstring ModuleDirectory()
	{
		wchar_t path[MAX_PATH] = {};
		if (g_moduleHandle == nullptr ||
		    GetModuleFileNameW(g_moduleHandle, path, MAX_PATH) == 0)
		{
			return {};
		}
		std::wstring full(path);
		size_t slash = full.find_last_of(L"\\/");
		return slash == std::wstring::npos ? std::wstring{} : full.substr(0, slash + 1);
	}

	int ReadIntSetting(const std::string& text, int fallback)
	{
		try
		{
			return std::stoi(text);
		}
		catch (...)
		{
			return fallback;
		}
	}

	void LoadConfig()
	{
		const std::wstring directory = ModuleDirectory();
		if (directory.empty())
		{
			return;
		}
		const std::wstring path = directory + L"OpenATC_DataBridge.ini";
		FILE* file = nullptr;
		if (_wfopen_s(&file, path.c_str(), L"rb") != 0 || file == nullptr)
		{
			return;
		}

		char line[512];
		while (std::fgets(line, sizeof(line), file) != nullptr)
		{
			std::string text = TrimAscii(line);
			if (text.empty() || text[0] == '#' || text[0] == ';' || text[0] == '[')
			{
				continue;
			}
			size_t equals = text.find('=');
			if (equals == std::string::npos)
			{
				continue;
			}
			std::string key = UpperAscii(TrimAscii(text.substr(0, equals)));
			std::string value = TrimAscii(text.substr(equals + 1));
			if (key == "CONTROLPORT")
			{
				int port = ReadIntSetting(value, g_controlPort);
				if (port > 1024 && port < 65535)
				{
					g_controlPort = static_cast<unsigned short>(port);
				}
			}
			else if (key == "DATAPORT")
			{
				int port = ReadIntSetting(value, g_dataPort);
				if (port > 1024 && port < 65535)
				{
					g_dataPort = static_cast<unsigned short>(port);
				}
			}
			else if (key == "DATAPORTCOUNT")
			{
				g_dataPortCount = std::clamp(ReadIntSetting(value, g_dataPortCount), 1, 32);
			}
			else if (key == "PRIMARYPORT")
			{
				int port = ReadIntSetting(value, g_primaryPort);
				g_primaryPort = (port >= 1024 && port < 65535)
					? static_cast<unsigned short>(port)
					: 0;
			}
			else if (key == "CONTROLENABLED")
			{
				g_controlEnabled = ReadIntSetting(value, 1) != 0;
			}
			else if (key == "SENDLEGACYPORTS")
			{
				g_sendLegacyPorts = ReadIntSetting(value, 1) != 0;
			}
			else if (key == "ENABLETCASTRAFFIC")
			{
				g_tcasTrafficEnabled = ReadIntSetting(value, 1) != 0;
			}
			else if (key == "ENABLEAIPLANES")
			{
				g_enableAiPlanes = ReadIntSetting(value, 1) != 0;
			}
			else if (key == "WEBAPIENABLED")
			{
				g_webApiEnabled = ReadIntSetting(value, 1) != 0;
			}
			else if (key == "WEBAPIPORT")
			{
				const int port = ReadIntSetting(value, kDefaultWebApiPort);
				if (port > 1024 && port < 65535)
				{
					g_webApiPort = static_cast<unsigned short>(port);
				}
			}
		}
		std::fclose(file);
	}

	// ---- OpenFrequency Local Web API ----

	void StopWebApi()
	{
		if (g_webApiServer)
		{
			g_webApiServer->Stop();
			g_webApiServer.reset();
		}
		g_webApiBackend.reset();
	}

	void StartWebApi()
	{
		if (!g_webApiEnabled || g_webApiServer)
		{
			return;
		}

		g_webApiBackend = openatc::webapi::CreateXplmBackend();
		if (!g_webApiBackend)
		{
			XPLMDebugString("OpenATC DataBridge: 无法创建 Local Web API 后端。\n");
			return;
		}
		// 先填充快照，保证监听建立后第一次请求就能拿到真实数据。
		g_webApiBackend->UpdateSnapshot();

		auto server = std::make_unique<openatc::webapi::Server>(
			g_webApiPort, g_webApiBackend);
		std::string error;
		if (!server->Start(error))
		{
			XPLMDebugString(
				(std::string("OpenATC DataBridge: Local Web API 启动失败 - ") +
				 error + "\n").c_str());
			// 端口被占用等情况下不保留空转的后端；下次插件启用时再重试。
			g_webApiBackend.reset();
			return;
		}

		g_webApiServer = std::move(server);
		XPLMDebugString(
			(std::string("OpenATC DataBridge: Local Web API 已监听 127.0.0.1:") +
			 std::to_string(g_webApiPort) + "（OpenFrequency 兼容）\n").c_str());
	}

	// ---- 控制通道 ----

	void SetComActive(XPLMDataRef activeRef, XPLMDataRef standbyRef, double mhz)
	{
		if (activeRef == nullptr || !std::isfinite(mhz) || mhz < 118.0 || mhz > 137.0)
		{
			return;
		}
		const int newValue = static_cast<int>(std::lround(mhz * 1000.0));
		const int oldValue = XPLMGetDatai(activeRef);
		// 模拟“预置备用频率后交换”：旧工作频率进入备用位。
		XPLMSetDatai(activeRef, newValue);
		if (standbyRef != nullptr && oldValue > 0)
		{
			XPLMSetDatai(standbyRef, oldValue);
		}
	}

	void SetComStandby(XPLMDataRef standbyRef, double mhz)
	{
		if (standbyRef == nullptr || !std::isfinite(mhz) || mhz < 118.0 || mhz > 137.0)
		{
			return;
		}
		XPLMSetDatai(standbyRef, static_cast<int>(std::lround(mhz * 1000.0)));
	}

	void SetSquawk(const std::string& code)
	{
		if (gTransponderCode == nullptr || code.size() != 4)
		{
			return;
		}
		for (char c : code)
		{
			if (c < '0' || c > '7')
			{
				return;
			}
		}
		XPLMSetDatai(gTransponderCode, std::atoi(code.c_str()));
	}

	void SetTransponderMode(int mode)
	{
		if (gTransponderMode == nullptr || mode < 0 || mode > 7)
		{
			return;
		}
		XPLMSetDatai(gTransponderMode, mode);
	}

	void ClearTraffic();
	std::string FindDefaultAircraftModel();
	void InitAiPlaneDatarefs();
	void HideAiPlanes(int count);

	void HandleControlLine(const std::string& raw)
	{
		std::string line = TrimAscii(raw);
		if (line.empty())
		{
			return;
		}

		// 目标数据行：呼号,lat,lon,alt,hdg,gs,on_ground
		if (g_trafficExpected > 0 && line.rfind("TRAFFIC", 0) != 0)
		{
			if (g_trafficCount < kMaxTraffic)
			{
				const char* cursor = line.c_str();
				char callsign[16] = {};
				double latitude = 0.0;
				double longitude = 0.0;
				double altitude = 0.0;
				double heading = 0.0;
				double groundSpeed = 0.0;
				int onGround = 0;
				int parsed = sscanf_s(
					cursor, "%15[^,],%lf,%lf,%lf,%lf,%lf,%d",
					callsign, static_cast<unsigned>(sizeof(callsign)),
					&latitude, &longitude, &altitude,
					&heading, &groundSpeed, &onGround);
				if (parsed >= 6 &&
				    std::isfinite(latitude) && std::isfinite(longitude) &&
				    std::isfinite(altitude) && std::isfinite(heading) &&
				    std::isfinite(groundSpeed) &&
				    latitude >= -90.0 && latitude <= 90.0 &&
				    longitude >= -180.0 && longitude <= 180.0)
				{
					TrafficTarget& target = g_traffic[g_trafficCount++];
					target.callsign = callsign;
					target.latitude = latitude;
					target.longitude = longitude;
					target.altitudeFt = altitude;
					target.headingDeg = heading;
					target.groundSpeedKts = groundSpeed;
					target.onGround = onGround != 0;
				}
			}
			--g_trafficExpected;
			if (g_trafficExpected <= 0)
			{
				g_trafficExpected = 0;
			}
			return;
		}

		std::string upper = UpperAscii(line);
		if (upper.rfind("TRAFFIC", 0) == 0)
		{
			int count = 0;
			if (sscanf_s(line.c_str(), "TRAFFIC %d", &count) == 1 && count > 0)
			{
				g_trafficCount = 0;
				g_trafficExpected = std::min(count, kMaxTraffic);
				if (!g_tcasTrafficEnabled && !g_tcasLoggedDisabled)
				{
					g_tcasLoggedDisabled = true;
					XPLMDebugString(
						"OpenATC DataBridge: 收到交通数据，但 EnableTcasTraffic=0，已忽略。\n");
				}
			}
			else
			{
				ClearTraffic();
			}
			return;
		}

		if (upper.rfind("SET COM1_STANDBY ", 0) == 0)
		{
			SetComStandby(gCom1StandbyFrequency, std::atof(line.c_str() + 17));
		}
		else if (upper.rfind("SET COM2_STANDBY ", 0) == 0)
		{
			SetComStandby(gCom2StandbyFrequency, std::atof(line.c_str() + 17));
		}
		else if (upper.rfind("SET COM1 ", 0) == 0)
		{
			SetComActive(gCom1Frequency, gCom1StandbyFrequency, std::atof(line.c_str() + 9));
		}
		else if (upper.rfind("SET COM2 ", 0) == 0)
		{
			SetComActive(gCom2Frequency, gCom2StandbyFrequency, std::atof(line.c_str() + 9));
		}
		else if (upper.rfind("SET SQUAWK ", 0) == 0)
		{
			SetSquawk(TrimAscii(line.substr(11)));
		}
		else if (upper.rfind("SET XPDR ", 0) == 0)
		{
			SetTransponderMode(std::atoi(line.c_str() + 9));
		}
		else if (upper.rfind("SET AI ", 0) == 0)
		{
			const bool enable = std::atoi(line.c_str() + 7) != 0;
			g_enableAiPlanes = enable;
			if (enable)
			{
				// 启动时可能关闭了 AI 飞机；重新启用时懒加载模型。
				if (g_aiModelPath.empty())
				{
					g_aiModelPath = FindDefaultAircraftModel();
					InitAiPlaneDatarefs();
				}
			}
			else
			{
				// 只显示呼号：隐藏可见机模，TCAS 槽位保持激活。
				HideAiPlanes(g_trafficCount);
			}
		}
	}

	void PollControlSocket()
	{
		if (gControlSocket == INVALID_SOCKET)
		{
			return;
		}
		// 63 个交通目标（每行约 70 字节）+ 控制指令，留足空间。
		char buffer[16384];
		for (;;)
		{
			sockaddr_in from{};
			int fromLength = sizeof(from);
			int received = recvfrom(
				gControlSocket, buffer, static_cast<int>(sizeof(buffer)) - 1, 0,
				reinterpret_cast<sockaddr*>(&from), &fromLength);
			if (received == SOCKET_ERROR)
			{
				const int error = WSAGetLastError();
				if (error == WSAEWOULDBLOCK || error == WSAETIMEDOUT)
				{
					return;
				}
				// 永久性错误时不要再循环，否则会在飞行循环里空转卡死模拟器。
				if (!g_controlSocketErrorLogged)
				{
					g_controlSocketErrorLogged = true;
					XPLMDebugString(
						(std::string("OpenATC DataBridge: 控制端口接收失败，错误码 ") +
						 std::to_string(error) + "\n").c_str());
				}
				return;
			}
			buffer[received] = '\0';
			std::string text(buffer, static_cast<size_t>(received));
			size_t start = 0;
			while (start < text.size())
			{
				size_t newline = text.find('\n', start);
				std::string line = text.substr(
					start, newline == std::string::npos ? std::string::npos : newline - start);
				HandleControlLine(line);
				if (newline == std::string::npos)
				{
					break;
				}
				start = newline + 1;
			}
		}
	}

	void OnPlanesAvailable(void* /*refcon*/)
	{
		g_tcasAcquirePending = false;
	}

	void EnsureTrafficPlanes()
	{
		if (g_tcasOwnsPlanes || g_tcasAcquirePending)
		{
			return;
		}
		if (XPLMAcquirePlanes(nullptr, OnPlanesAvailable, nullptr) != 0)
		{
			g_tcasOwnsPlanes = true;
			g_tcasAcquirePending = false;
			if (gOverrideTcas != nullptr)
			{
				XPLMSetDatai(gOverrideTcas, 1);
			}
		}
		else
		{
			g_tcasAcquirePending = true;
		}
	}

	void ReleaseTrafficPlanes()
	{
		if (!g_tcasOwnsPlanes)
		{
			return;
		}
		if (gOverrideTcas != nullptr)
		{
			XPLMSetDatai(gOverrideTcas, 0);
		}
		// 先隐藏 AI 飞机，再交还控制权，避免关闭 OpenATC 后留下无控 AI。
		XPLMSetActiveAircraftCount(0);
		XPLMReleasePlanes();
		g_tcasOwnsPlanes = false;
		g_tcasAcquirePending = false;
		g_aiModelsLoaded = 0;
	}

	std::filesystem::path PathFromUtf8(const std::string& text)
	{
		if (text.empty())
		{
			return {};
		}
		const int length = MultiByteToWideChar(
			CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
		if (length <= 0)
		{
			return std::filesystem::path(text);
		}
		std::wstring wide(static_cast<size_t>(length), L'\0');
		MultiByteToWideChar(
			CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
			wide.data(), length);
		return std::filesystem::path(wide);
	}

	std::string PathToUtf8(const std::filesystem::path& path)
	{
		const std::u8string utf8 = path.u8string();
		return std::string(reinterpret_cast<const char*>(utf8.data()), utf8.size());
	}

	std::string FindDefaultAircraftModel()
	{
		char systemPath[1024] = {};
		XPLMGetSystemPath(systemPath);
		if (systemPath[0] == '\0')
		{
			return {};
		}

		const std::filesystem::path base = PathFromUtf8(systemPath);
		const std::filesystem::path candidates[] = {
			base / "Aircraft" / "Laminar Research" / "Cessna 172SP" / "Cessna_172SP.acf",
			base / "Aircraft" / "Laminar Research" / "Cirrus SR22" / "Cirrus SR22.acf",
			base / "Aircraft" / "Laminar Research" / "Boeing 737-800" / "B738.acf",
		};
		for (const std::filesystem::path& candidate : candidates)
		{
			std::error_code error;
			if (std::filesystem::is_regular_file(candidate, error))
			{
				return PathToUtf8(candidate);
			}
		}

		// 没找到已知默认机型：在 Aircraft 下取第一个 .acf。
		const std::filesystem::path aircraftRoot = base / "Aircraft";
		std::error_code error;
		if (!std::filesystem::is_directory(aircraftRoot, error))
		{
			return {};
		}
		size_t count = 0;
		for (std::filesystem::recursive_directory_iterator it(
			     aircraftRoot, std::filesystem::directory_options::skip_permission_denied, error),
		     end;
		     it != end && !error && count < 20000;
		     it.increment(error))
		{
			++count;
			if (it->is_regular_file(error) && it->path().extension() == ".acf")
			{
				return PathToUtf8(it->path());
			}
		}
		return {};
	}

	void InitAiPlaneDatarefs()
	{
		for (int i = 1; i <= kMaxAiPlanes; ++i)
		{
			char name[128] = {};
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_x", i);
			gAiX[i] = XPLMFindDataRef(name);
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_y", i);
			gAiY[i] = XPLMFindDataRef(name);
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_z", i);
			gAiZ[i] = XPLMFindDataRef(name);
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_psi", i);
			gAiPsi[i] = XPLMFindDataRef(name);
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_the", i);
			gAiTheta[i] = XPLMFindDataRef(name);
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_phi", i);
			gAiPhi[i] = XPLMFindDataRef(name);
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_gear_deploy", i);
			gAiGear[i] = XPLMFindDataRef(name);
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_flap_ratio", i);
			gAiFlap[i] = XPLMFindDataRef(name);
			std::snprintf(name, sizeof(name),
				"sim/multiplayer/position/plane%d_throttle", i);
			gAiThrottle[i] = XPLMFindDataRef(name);
		}
	}

	void PublishAiPlanes(int count)
	{
		if (!g_enableAiPlanes || g_aiModelPath.empty())
		{
			return;
		}
		const int aiCount = std::min(count, kMaxAiPlanes);
		if (aiCount <= 0)
		{
			return;
		}
		// 位置 dataref 不可用时只保留 TCAS 目标，不去动 AI 飞机。
		if (gAiX[1] == nullptr)
		{
			return;
		}

		XPLMSetActiveAircraftCount(aiCount);
		for (int slot = 1; slot <= aiCount; ++slot)
		{
			if (slot > g_aiModelsLoaded)
			{
				XPLMSetAircraftModel(slot, g_aiModelPath.c_str());
				g_aiModelsLoaded = slot;
			}

			const TrafficTarget& target = g_traffic[slot - 1];
			double x = 0.0;
			double y = 0.0;
			double z = 0.0;
			XPLMWorldToLocal(
				target.latitude, target.longitude,
				target.altitudeFt * 0.3048, &x, &y, &z);

			if (gAiX[slot] != nullptr) XPLMSetDatad(gAiX[slot], x);
			if (gAiY[slot] != nullptr) XPLMSetDatad(gAiY[slot], y);
			if (gAiZ[slot] != nullptr) XPLMSetDatad(gAiZ[slot], z);
			if (gAiPsi[slot] != nullptr)
			{
				XPLMSetDataf(gAiPsi[slot], static_cast<float>(target.headingDeg));
			}
			if (gAiTheta[slot] != nullptr) XPLMSetDataf(gAiTheta[slot], 0.0f);
			if (gAiPhi[slot] != nullptr) XPLMSetDataf(gAiPhi[slot], 0.0f);
			if (gAiGear[slot] != nullptr)
			{
				float gear[10] = {};
				XPLMSetDatavf(gAiGear[slot], gear, 0, 10);
			}
			if (gAiFlap[slot] != nullptr) XPLMSetDataf(gAiFlap[slot], 0.0f);
			if (gAiThrottle[slot] != nullptr)
			{
				float throttle[8] = {};
				throttle[0] = 0.65f;
				XPLMSetDatavf(gAiThrottle[slot], throttle, 0, 8);
			}
		}
	}

	void HideAiPlanes(int count)
	{
		if (gAiX[1] == nullptr)
		{
			return;
		}
		const int aiCount = std::min(count, kMaxAiPlanes);
		for (int slot = 1; slot <= aiCount; ++slot)
		{
			// 保留槽位给 TCAS 目标，只把可见机模藏到地面以下。
			if (gAiX[slot] != nullptr) XPLMSetDatad(gAiX[slot], 0.0);
			if (gAiY[slot] != nullptr) XPLMSetDatad(gAiY[slot], -100000.0);
			if (gAiZ[slot] != nullptr) XPLMSetDatad(gAiZ[slot], 0.0);
		}
	}

	void ClearTraffic()
	{
		g_trafficCount = 0;
		g_trafficExpected = 0;
		ReleaseTrafficPlanes();
	}

	void PublishTraffic()
	{
		if (!g_tcasTrafficEnabled || g_trafficCount <= 0 ||
		    gOverrideTcas == nullptr || gTcasModeSId == nullptr ||
		    gTcasRelBearing == nullptr || gTcasRelDistance == nullptr ||
		    gTcasRelAltitude == nullptr)
		{
			return;
		}

		EnsureTrafficPlanes();
		if (!g_tcasOwnsPlanes)
		{
			return;
		}

		const int count = std::min(g_trafficCount, kMaxTraffic);
		// 激活 AI 槽位供 TCAS 目标使用；CallsignOnly 模式只跳过可见机模/位置写入。
		XPLMSetActiveAircraftCount(std::min(count, kMaxAiPlanes));
		if (g_enableAiPlanes)
		{
			PublishAiPlanes(count);
		}
		else
		{
			HideAiPlanes(count);
		}

		int modeSIds[kTcasSlots] = {};
		float bearings[kTcasSlots] = {};
		float distances[kTcasSlots] = {};
		float altitudes[kTcasSlots] = {};
		unsigned char flightIds[kTcasSlots * 8] = {};

		const double ownLat = ReadDouble(gLatitude);
		const double ownLon = ReadDouble(gLongitude);
		const double ownAltMeters = ReadDouble(gElevation);
		const double ownHeading = ReadFloat(gTruePsi);

		for (int i = 0; i < count; ++i)
		{
			const TrafficTarget& target = g_traffic[i];
			const int slot = i + 1; // slot 0 保留给本机
			if (slot >= kTcasSlots)
			{
				break;
			}
			modeSIds[slot] = static_cast<int>(StableModeSId(target.callsign));
			const double bearing = BearingDegrees(
				ownLat, ownLon, target.latitude, target.longitude);
			double relative = NormalizeHeading(bearing - ownHeading);
			if (relative > 180.0)
			{
				relative -= 360.0;
			}
			bearings[slot] = static_cast<float>(relative);
			distances[slot] = static_cast<float>(DistanceMeters(
				ownLat, ownLon, target.latitude, target.longitude));
			altitudes[slot] = static_cast<float>(
				target.altitudeFt * 0.3048 - ownAltMeters);

			const size_t offset = static_cast<size_t>(slot) * 8;
			const size_t maxChars = std::min<size_t>(7, target.callsign.size());
			std::memcpy(flightIds + offset, target.callsign.data(), maxChars);
		}

		XPLMSetDatavi(gTcasModeSId, modeSIds, 0, kTcasSlots);
		if (gTcasFlightId != nullptr)
		{
			XPLMSetDatab(
				gTcasFlightId, flightIds, 0, static_cast<int>(sizeof(flightIds)));
		}
		XPLMSetDatavf(gTcasRelBearing, bearings, 0, kTcasSlots);
		XPLMSetDatavf(gTcasRelDistance, distances, 0, kTcasSlots);
		XPLMSetDatavf(gTcasRelAltitude, altitudes, 0, kTcasSlots);
	}

	float FlightLoopCallback(float inElapsedSinceLastCall, float inElapsedTimeSinceLastFlightLoop,
	                         int inCounter, void* inRefcon)
	{
		// Local Web API：所有 XPLM 读写都必须留在模拟器线程上执行。
		if (g_webApiBackend)
		{
			g_webApiBackend->ProcessPending();
			g_webApiBackend->UpdateSnapshot();
		}
		if (g_webApiServer)
		{
			for (const std::string& line : g_webApiServer->DrainLogs())
			{
				XPLMDebugString(("OpenATC DataBridge: " + line + "\n").c_str());
			}
		}

		// 控制通道：调频、应答机、模拟交通（非阻塞）。
		PollControlSocket();

		// ---- 姿态 / 位置 ----
		const double altitudeFeet = ReadDouble(gElevation) * kMetersToFeet;
		const double aglFeet = ReadFloat(gAgl) * kMetersToFeet;
		const float airspeedKts = ReadFloat(gIndicatedAirspeed);
		const float groundspeedKts = static_cast<float>(
			ReadFloat(gGroundSpeed) * kMetersPerSecondToKnots);
		const float trueHeading = ReadFloat(gTruePsi);
		const float magneticHeading = gMagPsi != nullptr
			? ReadFloat(gMagPsi)
			: trueHeading;
		const float magneticVariation = ReadFloat(gMagneticVariation, 0.0f);
		const float verticalSpeedFpm = ReadFloat(gVerticalSpeed);
		const double latitudeDeg = ReadDouble(gLatitude);
		const double longitudeDeg = ReadDouble(gLongitude);
		const float pitchDeg = ReadFloat(gPitch);
		const float bankDeg = ReadFloat(gBank);

		// ---- 电台频率（MHz） ----
		const double com1Mhz = ReadComFrequencyMhz(gCom1Frequency);
		const double com2Mhz = ReadComFrequencyMhz(gCom2Frequency);
		const double com1StandbyMhz = ReadComFrequencyMhz(gCom1StandbyFrequency);
		const double com2StandbyMhz = ReadComFrequencyMhz(gCom2StandbyFrequency);
		const double nav1Mhz = ReadNavFrequencyMhz(gNav1Frequency);
		const double nav1StandbyMhz = ReadNavFrequencyMhz(gNav1StandbyFrequency);
		const float nav1CdiDots = ReadFloat(gNav1CdiDots);
		const float nav1GsDots = ReadFloat(gNav1GsDots);

		// ---- 应答机 ----
		int squawkCode = ReadInt(gTransponderCode);
		if (squawkCode < 0 || squawkCode > 7777)
		{
			squawkCode = 0;
		}
		const int transponderMode = ReadInt(gTransponderMode);
		const bool transponderIdent = ReadInt(gTransponderIdent) != 0;

		// ---- 构型与地面 ----
		const bool onGround = ReadInt(gOnGround) != 0;
		const bool gearHandleDown = ReadInt(gGearHandleDown) != 0;
		const float gearRatio = ReadGearDeploymentRatio();
		const bool gearDown = gearHandleDown && gearRatio > 0.99f;
		const float flapRatio = ReadFloat(gFlapRatio);
		const float flapHandleRatio = ReadFloat(gFlapHandleRatio);
		const bool parkingBrake = ReadFloat(gParkingBrake) > 0.1f;
		const bool engineRunning = AnyEngineRunning();
		const bool beaconOn = ReadInt(gBeaconLights) != 0;
		const bool landingLightsOn = ReadInt(gLandingLights) != 0;

		// ---- 自动驾驶 ----
		float apAltitudeFt = ReadFloat(gApAltitudeDialFt);
		if (apAltitudeFt <= 0.0f)
		{
			apAltitudeFt = ReadFloat(gApAltitudeHoldFt);
		}
		const float apHeadingMag = ReadFloat(gApHeadingMag);
		const int apOn = ReadInt(gApOn);
		const int apApproach = ReadInt(gApApproachStatus);
		const int apNav = ReadInt(gApNavStatus);
		const int apAltitudeHold = ReadInt(gApAltitudeHoldStatus);

		// ---- 时间 / 天气 / 机型 ----
		const float zuluSec = ReadFloat(gZuluTimeSec);
		const float windDirection = ReadWeatherArray(gWindDirection);
		const float windSpeedKts = ReadWeatherArray(gWindSpeedKts);
		const float qnhInHg = ReadFloat(gQnhInHg);
		const float visibilitySm = ReadFloat(gVisibilityM, 16093.4f) / 1609.344f;
		const float temperatureC = ReadFloat(
			gTemperatureSeaLevelC,
			ReadFloat(gTemperatureAmbientC, 15.0f));
		// X-Plane 没有露点 dataref；用温度减 3°C 作为 ATIS 的近似值。
		const float dewpointC = temperatureC - 3.0f;

		int cloudTypes[3] = {};
		float cloudCoverage[3] = {};
		float cloudBaseM[3] = {};
		const int cloudTypeCount = ReadIntArray(gCloudType, cloudTypes, 3);
		ReadFloatArray(gCloudCoverage, cloudCoverage, 3);
		ReadFloatArray(gCloudBaseM, cloudBaseM, 3);
		const char* cloudCover = cloudTypeCount > 0
			? CloudCoverText(cloudTypes[0])
			: "CLR";
		if (cloudTypeCount > 0 && cloudTypes[0] <= 1 && cloudCoverage[0] < 0.05f)
		{
			cloudCover = "CLR";
		}
		const float cloudBaseFt = cloudBaseM[0] * static_cast<float>(kMetersToFeet);

		const std::string aircraftIcao = JsonEscape(ReadStringDataref(gAircraftIcao));
		const std::string aircraftTail = JsonEscape(ReadStringDataref(gAircraftTailnum));
		const bool com1Audio = ReadInt(gCom1AudioSelected) != 0;

		// ---- 打包 JSON 并发送（UDP 不需要接收端在线） ----
		if (gSocket != INVALID_SOCKET && gTargetCount > 0)
		{
			char json[1600];
			int length = std::snprintf(
				json, sizeof(json),
				"{"
				"\"alt\":%.1f,"
				"\"agl_ft\":%.1f,"
				"\"spd\":%.1f,"
				"\"gs\":%.1f,"
				"\"hdg\":%.1f,"          // 真航向（保持旧字段语义）
				"\"true_hdg\":%.1f,"
				"\"mag_hdg\":%.1f,"      // 磁航向（ATC/HSI）
				"\"magvar\":%.2f,"
				"\"vs\":%.1f,"
				"\"pitch\":%.2f,"
				"\"bank\":%.2f,"
				"\"lat\":%.6f,"
				"\"lng\":%.6f,"
				"\"com1\":%.3f,"
				"\"com2\":%.3f,"
				"\"com1_stdby\":%.3f,"
				"\"com2_stdby\":%.3f,"
				"\"com1_audio\":%s,"
				"\"nav1\":%.3f,"
				"\"nav1_stdby\":%.3f,"
				"\"nav1_cdi\":%.3f,"
				"\"nav1_gs\":%.3f,"
				"\"squawk\":\"%04d\","
				"\"xpdr_mode\":%d,"
				"\"xpdr_mode_text\":\"%s\","
				"\"xpdr_ident\":%s,"
				"\"on_ground\":%s,"
				"\"gear_handle_down\":%d,"
				"\"gear_down\":%s,"
				"\"gear_ratio\":%.3f,"
				"\"flap_ratio\":%.3f,"
				"\"flap_handle_ratio\":%.3f,"
				"\"parking_brake\":%s,"
				"\"engine_running\":%s,"
				"\"beacon\":%s,"
				"\"landing_lights\":%s,"
				"\"ap_alt_ft\":%.1f,"
				"\"ap_alt_hold_ft\":%.1f,"
				"\"ap_hdg_mag\":%.1f,"
				"\"ap_on\":%d,"
				"\"ap_approach\":%d,"
				"\"ap_nav\":%d,"
				"\"ap_alt_hold\":%d,"
				"\"zulu_sec\":%.1f,"
				"\"wind_dir_true\":%.1f,"
				"\"wind_speed_kt\":%.1f,"
				"\"wind_gust_kt\":0.0,"
				"\"qnh_inhg\":%.3f,"
				"\"vis_sm\":%.1f,"
				"\"temp_c\":%.1f,"
				"\"dewpoint_c\":%.1f,"
				"\"cloud_cover\":\"%s\","
				"\"cloud_base_ft\":%.0f,"
				"\"aircraft_type\":\"%s\","
				"\"tailnum\":\"%s\","
				"\"plugin\":\"OpenATC DataBridge 1.2\""
				"}",
				altitudeFeet,
				aglFeet,
				airspeedKts,
				groundspeedKts,
				trueHeading,
				trueHeading,
				magneticHeading,
				magneticVariation,
				verticalSpeedFpm,
				pitchDeg,
				bankDeg,
				latitudeDeg,
				longitudeDeg,
				com1Mhz,
				com2Mhz,
				com1StandbyMhz,
				com2StandbyMhz,
				com1Audio ? "true" : "false",
				nav1Mhz,
				nav1StandbyMhz,
				nav1CdiDots,
				nav1GsDots,
				squawkCode,
				transponderMode,
				TransponderModeText(transponderMode),
				transponderIdent ? "true" : "false",
				onGround ? "true" : "false",
				gearHandleDown ? 1 : 0,
				gearDown ? "true" : "false",
				gearRatio,
				flapRatio,
				flapHandleRatio,
				parkingBrake ? "true" : "false",
				engineRunning ? "true" : "false",
				beaconOn ? "true" : "false",
				landingLightsOn ? "true" : "false",
				apAltitudeFt,
				ReadFloat(gApAltitudeHoldFt),
				apHeadingMag,
				apOn,
				apApproach,
				apNav,
				apAltitudeHold,
				zuluSec,
				windDirection,
				windSpeedKts,
				qnhInHg,
				visibilitySm,
				temperatureC,
				dewpointC,
				cloudCover,
				cloudBaseFt,
				aircraftIcao.c_str(),
				aircraftTail.c_str());

			// 缓冲区不足时 snprintf 会返回所需长度；此时丢弃该包而不是发送截断的 JSON。
			if (length > 0 && static_cast<size_t>(length) < sizeof(json))
			{
				for (int i = 0; i < gTargetCount; ++i)
				{
					sendto(
						gSocket, json, length, 0,
						reinterpret_cast<const sockaddr*>(&gTargets[i]),
						sizeof(gTargets[i]));
				}
			}
		}

		// 把 Python 管制引擎的模拟交通发布到 X-Plane TCAS（默认关闭）。
		PublishTraffic();

		return kFlightLoopIntervalSec;
	}

	bool BindControlSocket()
	{
		if (!g_controlEnabled)
		{
			return true;
		}
		gControlSocket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
		if (gControlSocket == INVALID_SOCKET)
		{
			return false;
		}

		BOOL disableConnReset = FALSE;
		DWORD bytesReturned = 0;
		WSAIoctl(
			gControlSocket, SIO_UDP_CONNRESET, &disableConnReset,
			sizeof(disableConnReset), nullptr, 0, &bytesReturned, nullptr, nullptr);

		// 只绑定环回地址；最多尝试 49031–49039。
		for (int attempt = 0; attempt < 9; ++attempt)
		{
			const int portNumber = static_cast<int>(g_controlPort) + attempt;
			if (portNumber > 65535)
			{
				break;
			}
			const unsigned short port = static_cast<unsigned short>(portNumber);
			sockaddr_in address{};
			address.sin_family = AF_INET;
			address.sin_port = htons(port);
			address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
			if (bind(
					gControlSocket,
					reinterpret_cast<const sockaddr*>(&address),
					sizeof(address)) == 0)
			{
				u_long nonBlocking = 1;
				ioctlsocket(gControlSocket, FIONBIO, &nonBlocking);
				g_controlPort = port;
				return true;
			}
		}

		closesocket(gControlSocket);
		gControlSocket = INVALID_SOCKET;
		return false;
	}

	void CloseControlSocket()
	{
		if (gControlSocket != INVALID_SOCKET)
		{
			closesocket(gControlSocket);
			gControlSocket = INVALID_SOCKET;
		}
	}

	void BuildTargetPorts()
	{
		gTargetCount = 0;
		auto add = [](int portNumber)
		{
			if (gTargetCount >= kMaxTargetPorts ||
			    portNumber < 1 || portNumber > 65535)
			{
				return;
			}
			const unsigned short port = static_cast<unsigned short>(portNumber);
			gTargets[gTargetCount].sin_family = AF_INET;
			gTargets[gTargetCount].sin_port = htons(port);
			inet_pton(AF_INET, "127.0.0.1", &gTargets[gTargetCount].sin_addr);
			++gTargetCount;
		};

		// 可选主端口（默认 49010；设为 0 可关闭）。
		if (g_primaryPort != 0)
		{
			add(static_cast<int>(g_primaryPort));
		}

		if (g_sendLegacyPorts)
		{
			for (int i = 0; i < g_dataPortCount; ++i)
			{
				add(static_cast<int>(g_dataPort) + i);
			}
		}
		else
		{
			add(static_cast<int>(g_dataPort));
		}
	}

	void LogMissingDatarefs()
	{
		struct Entry
		{
			const char* name;
			XPLMDataRef ref;
		};
		const Entry entries[] = {
			{"sim/flightmodel/position/elevation", gElevation},
			{"sim/flightmodel/position/y_agl", gAgl},
			{"sim/flightmodel/position/indicated_airspeed", gIndicatedAirspeed},
			{"sim/flightmodel/position/groundspeed", gGroundSpeed},
			{"sim/flightmodel/position/true_psi", gTruePsi},
			{"sim/flightmodel/position/mag_psi", gMagPsi},
			{"sim/flightmodel/position/magnetic_variation", gMagneticVariation},
			{"sim/flightmodel/position/vh_ind_fpm", gVerticalSpeed},
			{"sim/flightmodel/position/latitude", gLatitude},
			{"sim/flightmodel/position/longitude", gLongitude},
			{"sim/cockpit2/radios/actuators/com1_frequency_hz_833", gCom1Frequency},
			{"sim/cockpit2/radios/actuators/com1_standby_frequency_hz_833", gCom1StandbyFrequency},
			{"sim/cockpit2/radios/actuators/nav1_frequency_hz", gNav1Frequency},
			{"sim/cockpit2/radios/actuators/transponder_code", gTransponderCode},
			{"sim/cockpit2/radios/actuators/transponder_mode", gTransponderMode},
			{"sim/flightmodel/failures/onground_any", gOnGround},
			{"sim/cockpit2/autopilot/altitude_dial_ft", gApAltitudeDialFt},
			{"sim/operation/override/override_TCAS", gOverrideTcas},
			{"sim/cockpit2/tcas/targets/modeS_id", gTcasModeSId},
			{"sim/cockpit2/tcas/targets/flight_id", gTcasFlightId},
			{"sim/cockpit2/tcas/indicators/relative_bearing_degs", gTcasRelBearing},
			{"sim/cockpit2/tcas/indicators/relative_distance_mtrs", gTcasRelDistance},
			{"sim/cockpit2/tcas/indicators/relative_altitude_mtrs", gTcasRelAltitude},
		};
		for (const Entry& entry : entries)
		{
			if (entry.ref == nullptr)
			{
				XPLMDebugString(
					(std::string("OpenATC DataBridge: 缺少 dataref ") + entry.name + "\n").c_str());
			}
		}
	}
}

// ---- 模块入口 ----

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID /*reserved*/)
{
	if (reason == DLL_PROCESS_ATTACH)
	{
		g_moduleHandle = hModule;
		DisableThreadLibraryCalls(hModule);
	}
	return TRUE;
}

// ---- 必需的插件入口 ----

PLUGIN_API int XPluginStart(char* outName, char* outSig, char* outDesc)
{
	strncpy_s(outName, 256, "OpenATC DataBridge", _TRUNCATE);
	strncpy_s(outSig, 256, "com.openatc.databridge", _TRUNCATE);
	strncpy_s(
		outDesc, 256,
		"UDP telemetry + X-Plane 12 Local Web API (OpenFrequency) for XP11/XP12",
		_TRUNCATE);

	LoadConfig();

	WSADATA wsaData;
	if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
	{
		return 0;
	}

	gSocket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	if (gSocket == INVALID_SOCKET)
	{
		WSACleanup();
		return 0;
	}

	// 关闭 ICMP 端口不可达导致的 WSAECONNRESET。
	BOOL disableConnReset = FALSE;
	DWORD bytesReturned = 0;
	WSAIoctl(
		gSocket, SIO_UDP_CONNRESET, &disableConnReset,
		sizeof(disableConnReset), nullptr, 0, &bytesReturned, nullptr, nullptr);

	BuildTargetPorts();
	if (!BindControlSocket())
	{
		XPLMDebugString("OpenATC DataBridge: 控制端口绑定失败，调频/交通注入不可用。\n");
	}

	// ---- 姿态 / 位置 ----
	gElevation = XPLMFindDataRef("sim/flightmodel/position/elevation");
	gAgl = XPLMFindDataRef("sim/flightmodel/position/y_agl");
	gIndicatedAirspeed = XPLMFindDataRef("sim/flightmodel/position/indicated_airspeed");
	gGroundSpeed = XPLMFindDataRef("sim/flightmodel/position/groundspeed");
	gTruePsi = XPLMFindDataRef("sim/flightmodel/position/true_psi");
	gMagPsi = XPLMFindDataRef("sim/flightmodel/position/mag_psi");
	gMagneticVariation = XPLMFindDataRef("sim/flightmodel/position/magnetic_variation");
	gVerticalSpeed = XPLMFindDataRef("sim/flightmodel/position/vh_ind_fpm");
	gLatitude = XPLMFindDataRef("sim/flightmodel/position/latitude");
	gLongitude = XPLMFindDataRef("sim/flightmodel/position/longitude");
	gPitch = XPLMFindDataRef("sim/flightmodel/position/theta");
	gBank = XPLMFindDataRef("sim/flightmodel/position/phi");

	// ---- 电台频率 ----
	gCom1Frequency = XPLMFindDataRef("sim/cockpit2/radios/actuators/com1_frequency_hz_833");
	gCom2Frequency = XPLMFindDataRef("sim/cockpit2/radios/actuators/com2_frequency_hz_833");
	gCom1StandbyFrequency = XPLMFindDataRef("sim/cockpit2/radios/actuators/com1_standby_frequency_hz_833");
	gCom2StandbyFrequency = XPLMFindDataRef("sim/cockpit2/radios/actuators/com2_standby_frequency_hz_833");
	gCom1AudioSelected = XPLMFindDataRef("sim/cockpit2/radios/actuators/audio_selection_com1");
	gNav1Frequency = XPLMFindDataRef("sim/cockpit2/radios/actuators/nav1_frequency_hz");
	gNav1StandbyFrequency = XPLMFindDataRef("sim/cockpit2/radios/actuators/nav1_standby_frequency_hz");
	gNav1CdiDots = XPLMFindDataRef("sim/cockpit2/radios/indicators/nav1_hdef_dots_pilot");
	gNav1GsDots = XPLMFindDataRef("sim/cockpit2/radios/indicators/nav1_vdef_dots_pilot");

	// ---- 应答机 ----
	gTransponderCode = XPLMFindDataRef("sim/cockpit2/radios/actuators/transponder_code");
	gTransponderMode = XPLMFindDataRef("sim/cockpit2/radios/actuators/transponder_mode");
	gTransponderIdent = XPLMFindDataRef("sim/cockpit/radios/transponder_id");

	// ---- 构型与地面 ----
	gOnGround = XPLMFindDataRef("sim/flightmodel/failures/onground_any");
	gGearHandleDown = XPLMFindDataRef("sim/cockpit2/controls/gear_handle_down");
	gGearDeployRatio = XPLMFindDataRef("sim/flightmodel2/gear/deploy_ratio");
	gFlapRatio = XPLMFindDataRef("sim/cockpit2/controls/flap_ratio");
	gFlapHandleRatio = XPLMFindDataRef("sim/cockpit2/controls/flap_handle_request_ratio");
	gParkingBrake = XPLMFindDataRef("sim/cockpit2/controls/parking_brake_ratio");
	gEngineRunning = XPLMFindDataRef("sim/flightmodel/engine/ENGN_running");
	gBeaconLights = XPLMFindDataRef("sim/cockpit/electrical/beacon_lights_on");
	gLandingLights = XPLMFindDataRef("sim/cockpit/electrical/landing_lights_on");

	// ---- 自动驾驶 ----
	gApAltitudeDialFt = XPLMFindDataRef("sim/cockpit2/autopilot/altitude_dial_ft");
	gApAltitudeHoldFt = XPLMFindDataRef("sim/cockpit2/autopilot/altitude_hold_ft");
	gApHeadingMag = XPLMFindDataRef("sim/cockpit2/autopilot/heading_dial_deg_mag_pilot");
	gApOn = XPLMFindDataRef("sim/cockpit2/autopilot/autopilot_on");
	gApApproachStatus = XPLMFindDataRef("sim/cockpit2/autopilot/approach_status");
	gApNavStatus = XPLMFindDataRef("sim/cockpit2/autopilot/nav_status");
	gApAltitudeHoldStatus = XPLMFindDataRef("sim/cockpit2/autopilot/altitude_hold_status");

	// ---- 时间 / 天气 ----
	gZuluTimeSec = XPLMFindDataRef("sim/time/zulu_time_sec");
	gWindDirection = XPLMFindDataRef("sim/weather/wind_direction_degt");
	gWindSpeedKts = XPLMFindDataRef("sim/weather/wind_speed_kt");
	gQnhInHg = XPLMFindDataRef("sim/weather/barometer_sealevel_inhg");
	gVisibilityM = XPLMFindDataRef("sim/weather/visibility_reported_m");
	gTemperatureSeaLevelC = XPLMFindDataRef("sim/weather/temperature_sealevel_c");
	gTemperatureAmbientC = XPLMFindDataRef("sim/weather/temperature_ambient_c");
	gCloudType = XPLMFindDataRef("sim/weather/cloud_type");
	gCloudCoverage = XPLMFindDataRef("sim/weather/cloud_coverage");
	gCloudBaseM = XPLMFindDataRef("sim/weather/cloud_base_msl_m");

	// ---- 机型 ----
	gAircraftIcao = XPLMFindDataRef("sim/aircraft/view/acf_ICAO");
	gAircraftTailnum = XPLMFindDataRef("sim/aircraft/view/acf_tailnum");

	// ---- TCAS 交通注入（仅在使用时获取 AI 飞机） ----
	gOverrideTcas = XPLMFindDataRef("sim/operation/override/override_TCAS");
	gTcasModeSId = XPLMFindDataRef("sim/cockpit2/tcas/targets/modeS_id");
	gTcasFlightId = XPLMFindDataRef("sim/cockpit2/tcas/targets/flight_id");
	gTcasRelBearing = XPLMFindDataRef("sim/cockpit2/tcas/indicators/relative_bearing_degs");
	gTcasRelDistance = XPLMFindDataRef("sim/cockpit2/tcas/indicators/relative_distance_mtrs");
	gTcasRelAltitude = XPLMFindDataRef("sim/cockpit2/tcas/indicators/relative_altitude_mtrs");

	LogMissingDatarefs();

	InitAiPlaneDatarefs();
	if (g_enableAiPlanes)
	{
		g_aiModelPath = FindDefaultAircraftModel();
		XPLMDebugString(
			(std::string("OpenATC DataBridge: AI 飞机模型 ") +
			 (g_aiModelPath.empty() ? "未找到（仅 TCAS 目标）" : g_aiModelPath) + "\n").c_str());
	}

	// OpenFrequency 兼容层：X-Plane 11 没有官方的 Local Web API。
	StartWebApi();

	int xplaneVersion = 0;
	int xplmVersion = 0;
	XPLMHostApplicationID host = xplm_Host_Unknown;
	XPLMGetVersions(&xplaneVersion, &xplmVersion, &host);
	XPLMDebugString(
		(std::string("OpenATC DataBridge 1.2 已加载：X-Plane ") +
		 std::to_string(xplaneVersion) + "，XPLM " + std::to_string(xplmVersion) +
		 "，数据端口 " + std::to_string(g_dataPort) + "×" + std::to_string(g_dataPortCount) +
		 "，主端口 " + std::to_string(g_primaryPort) +
		 "，控制端口 " + std::to_string(g_controlPort) +
		 "，TCAS 交通 " + (g_tcasTrafficEnabled ? "开" : "关") +
		 "，Web API " +
		 (g_webApiEnabled ? "127.0.0.1:" + std::to_string(g_webApiPort) : "关") +
		 "\n").c_str());

	XPLMRegisterFlightLoopCallback(FlightLoopCallback, kFlightLoopIntervalSec, nullptr);
	return 1;
}

PLUGIN_API void XPluginStop(void)
{
	// 先停 HTTP 线程，再释放 Winsock，避免后台线程访问已清理的资源。
	StopWebApi();
	XPLMUnregisterFlightLoopCallback(FlightLoopCallback, nullptr);
	ClearTraffic();
	CloseControlSocket();

	if (gSocket != INVALID_SOCKET)
	{
		closesocket(gSocket);
		gSocket = INVALID_SOCKET;
	}

	WSACleanup();
}

PLUGIN_API int XPluginEnable(void)
{
	// 插件被重新启用时，X-Plane 会收回 AI 飞机访问权；重新获取交给交通发布路径。
	g_tcasOwnsPlanes = false;
	g_tcasAcquirePending = false;
	StartWebApi();
	return 1;
}

PLUGIN_API void XPluginDisable(void)
{
	// 禁用后飞行循环不再执行，快照会冻结；停止监听以免继续返回过期数据。
	StopWebApi();
	ClearTraffic();
}
