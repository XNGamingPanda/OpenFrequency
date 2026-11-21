#include "pch.h"

#include "XplmWebApiBackend.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <iterator>
#include <limits>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "XPLMDataAccess.h"
#include "XPLMUtilities.h"

namespace openatc::webapi
{
	namespace
	{
		constexpr int kMaxArrayValues = 1024;  // X-Plane arrays are far below this
		constexpr int kMaxStringBytes = 512;
		constexpr int kFlightIdSlots = 64;
		constexpr int kFlightIdStride = 8;
		constexpr std::size_t kMaxQueuedWrites = 512;
		constexpr std::size_t kMaxQueuedCommands = 64;

		struct DatarefDefinition
		{
			const char* name;     // Name exposed through the Web API
			const char* refName;  // Real X-Plane dataref name (nullptr = same as name)
			DatarefType type;
		};

		// The table mirrors what OpenFrequency's core/xplane_provider.py reads.
		// Keep the order stable so the numeric ids are stable across restarts.
		constexpr DatarefDefinition kDatarefDefinitions[] = {
			// ---- Position / attitude ----
			{"sim/flightmodel/position/latitude", nullptr, DatarefType::Double},
			{"sim/flightmodel/position/longitude", nullptr, DatarefType::Double},
			{"sim/flightmodel/position/elevation", nullptr, DatarefType::Double},
			{"sim/flightmodel/position/psi", nullptr, DatarefType::Float},
			{"sim/flightmodel/position/theta", nullptr, DatarefType::Float},
			{"sim/flightmodel/position/phi", nullptr, DatarefType::Float},
			{"sim/flightmodel/position/indicated_airspeed", nullptr, DatarefType::Float},
			{"sim/flightmodel/position/vh_ind_fpm", nullptr, DatarefType::Float},

			// ---- Engines (exposed as arrays; clients use element 0) ----
			{"sim/flightmodel/engine/ENGN_N1_[0]",
			 "sim/flightmodel/engine/ENGN_N1_", DatarefType::FloatArray},
			{"sim/flightmodel/engine/ENGN_EGT_c[0]",
			 "sim/flightmodel/engine/ENGN_EGT_c", DatarefType::FloatArray},
			{"sim/flightmodel/engine/ENGN_FF_[0]",
			 "sim/flightmodel/engine/ENGN_FF_", DatarefType::FloatArray},

			// ---- Configuration ----
			{"sim/aircraft/parts/acf_gear_deploy", nullptr, DatarefType::FloatArray},
			{"sim/flightmodel/controls/flaprat", nullptr, DatarefType::Float},

			// ---- Radios / transponder ----
			{"sim/cockpit/radios/transponder_code", nullptr, DatarefType::Int},
			{"sim/cockpit2/radios/actuators/com1_frequency_hz_833", nullptr, DatarefType::Int},

			// ---- Aircraft identity ----
			{"sim/aircraft/view/acf_ICAO", nullptr, DatarefType::ByteString},
			{"sim/aircraft/view/acf_descrip", nullptr, DatarefType::ByteString},

			// ---- TCAS traffic arrays (populated by AI traffic plugins) ----
			{"sim/cockpit2/tcas/targets/position/lat", nullptr, DatarefType::FloatArray},
			{"sim/cockpit2/tcas/targets/position/lon", nullptr, DatarefType::FloatArray},
			{"sim/cockpit2/tcas/targets/position/ele", nullptr, DatarefType::FloatArray},
			{"sim/cockpit2/tcas/targets/position/vvi", nullptr, DatarefType::FloatArray},
			{"sim/cockpit2/tcas/targets/position/psi", nullptr, DatarefType::FloatArray},
			{"sim/cockpit2/tcas/targets/position/groundspeed", nullptr, DatarefType::FloatArray},
			{"sim/cockpit2/tcas/targets/position/weight_on_wheels",
			 nullptr, DatarefType::FloatArray},
			{"sim/cockpit2/tcas/targets/flight_id", nullptr, DatarefType::FlightIdArray},

			// ---- Autopilot targets (radar vectoring write-back) ----
			// OpenFrequency uses the XP12 names (sim/autopilot/*). X-Plane 11
			// exposes the same targets through the cockpit2 datarefs below, so
			// keep the API names but resolve the cross-version implementations.
			{"sim/autopilot/heading_mag",
			 "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot", DatarefType::Float},
			{"sim/autopilot/altitude",
			 "sim/cockpit2/autopilot/altitude_dial_ft", DatarefType::Float},
			{"sim/autopilot/airspeed",
			 "sim/cockpit2/autopilot/airspeed_dial_kts", DatarefType::Float},
			{"sim/cockpit/autopilot/autopilot_state", nullptr, DatarefType::Int},
			{"sim/cockpit2/autopilot/heading_mode", nullptr, DatarefType::Int},
			{"sim/cockpit2/autopilot/altitude_mode", nullptr, DatarefType::Int},
			{"sim/cockpit2/autopilot/speed_mode",
			 "sim/cockpit/autopilot/airspeed_mode", DatarefType::Int},

			// ---- Failure injection ----
			{"sim/operation/failures/rel_engfai0", nullptr, DatarefType::Int},
			{"sim/operation/failures/rel_engfai1", nullptr, DatarefType::Int},
			{"sim/operation/failures/rel_hydpmp", nullptr, DatarefType::Int},
			{"sim/operation/failures/rel_gen_esys", nullptr, DatarefType::Int},
			{"sim/operation/failures/rel_gear_act", nullptr, DatarefType::Int},
			{"sim/operation/failures/rel_lagear1", nullptr, DatarefType::Int},
			{"sim/operation/failures/rel_lagear2", nullptr, DatarefType::Int},
			{"sim/operation/failures/rel_lagear3", nullptr, DatarefType::Int},
		};

		constexpr const char* kCommandNames[] = {
			"sim/autopilot/heading",
			"sim/autopilot/altitude_hold",
			"sim/autopilot/autothrottle_toggle",
		};

		struct PendingWrite
		{
			std::size_t index = 0;
			DatarefValue value;
		};

		template <typename T, typename... Args>
		std::shared_ptr<const DatarefValue> MakeValue(Args&&... args)
		{
			return std::make_shared<const DatarefValue>(
				std::in_place_type<T>, std::forward<Args>(args)...);
		}

		std::shared_ptr<const DatarefValue> MakeDefaultValue(DatarefType type)
		{
			switch (type)
			{
			case DatarefType::Float:
			case DatarefType::Double:
				return MakeValue<double>(0.0);
			case DatarefType::Int:
				return MakeValue<std::int64_t>(std::int64_t{0});
			case DatarefType::FloatArray:
				return MakeValue<std::vector<double>>(std::vector<double>{});
			case DatarefType::IntArray:
				return MakeValue<std::vector<std::int64_t>>(std::vector<std::int64_t>{});
			case DatarefType::ByteString:
				return MakeValue<std::string>(std::string{});
			case DatarefType::FlightIdArray:
				return MakeValue<std::vector<std::string>>(std::vector<std::string>{});
			}
			return MakeValue<double>(0.0);
		}

		std::optional<double> AsDouble(const DatarefValue& value)
		{
			if (const auto* number = std::get_if<double>(&value))
			{
				return std::isfinite(*number) ? *number : 0.0;
			}
			if (const auto* integer = std::get_if<std::int64_t>(&value))
			{
				return static_cast<double>(*integer);
			}
			if (const auto* text = std::get_if<std::string>(&value))
			{
				double parsed = 0.0;
				const auto [end, error] = std::from_chars(
					text->data(), text->data() + text->size(), parsed);
				if (error == std::errc{} && end == text->data() + text->size())
				{
					return parsed;
				}
			}
			return std::nullopt;
		}

		std::optional<std::int64_t> AsInteger(const DatarefValue& value)
		{
			if (const auto* integer = std::get_if<std::int64_t>(&value))
			{
				return *integer;
			}
			if (const auto* number = std::get_if<double>(&value))
			{
				if (std::isfinite(*number))
				{
					return static_cast<std::int64_t>(std::llround(*number));
				}
				return std::nullopt;
			}
			if (const auto* text = std::get_if<std::string>(&value))
			{
				std::int64_t parsed = 0;
				const auto [end, error] = std::from_chars(
					text->data(), text->data() + text->size(), parsed);
				if (error == std::errc{} && end == text->data() + text->size())
				{
					return parsed;
				}
			}
			return std::nullopt;
		}

		std::optional<std::vector<double>> AsDoubleArray(const DatarefValue& value)
		{
			if (const auto* numbers = std::get_if<std::vector<double>>(&value))
			{
				return *numbers;
			}
			if (const auto* integers = std::get_if<std::vector<std::int64_t>>(&value))
			{
				std::vector<double> converted;
				converted.reserve(integers->size());
				for (const std::int64_t item : *integers)
				{
					converted.push_back(static_cast<double>(item));
				}
				return converted;
			}
			return std::nullopt;
		}

		std::optional<std::vector<std::int64_t>> AsIntegerArray(const DatarefValue& value)
		{
			if (const auto* integers = std::get_if<std::vector<std::int64_t>>(&value))
			{
				return *integers;
			}
			if (const auto* numbers = std::get_if<std::vector<double>>(&value))
			{
				std::vector<std::int64_t> converted;
				converted.reserve(numbers->size());
				for (const double item : *numbers)
				{
					converted.push_back(
						std::isfinite(item) ? static_cast<std::int64_t>(std::llround(item)) : 0);
				}
				return converted;
			}
			return std::nullopt;
		}

		class XplmBackend final : public Backend
		{
		public:
			XplmBackend()
			{
				int nextId = 0;
				datarefs_.reserve(std::size(kDatarefDefinitions));
				refs_.reserve(std::size(kDatarefDefinitions));
				snapshot_.reserve(std::size(kDatarefDefinitions));
				for (const DatarefDefinition& definition : kDatarefDefinitions)
				{
					DatarefInfo info;
					info.name = definition.name;
					info.type = definition.type;
					info.id = ++nextId;
					datarefs_.push_back(std::move(info));
					refs_.push_back(Resolve(definition));
					snapshot_.push_back(MakeDefaultValue(definition.type));
				}

				nextId = 0;
				commands_.reserve(std::size(kCommandNames));
				commandRefs_.reserve(std::size(kCommandNames));
				for (const char* name : kCommandNames)
				{
					CommandInfo info;
					info.name = name;
					info.id = ++nextId;
					commands_.push_back(std::move(info));
					commandRefs_.push_back(XPLMFindCommand(name));
				}
			}

			const std::vector<DatarefInfo>& Datarefs() const override { return datarefs_; }
			const std::vector<CommandInfo>& Commands() const override { return commands_; }

			std::optional<DatarefValue> Read(int datarefId) const override
			{
				if (datarefId <= 0 ||
				    static_cast<std::size_t>(datarefId) > snapshot_.size())
				{
					return std::nullopt;
				}
				std::shared_ptr<const DatarefValue> value;
				{
					std::lock_guard<std::mutex> lock(snapshotMutex_);
					value = snapshot_[static_cast<std::size_t>(datarefId - 1)];
				}
				if (!value)
				{
					return std::nullopt;
				}
				return *value;
			}

			bool Write(int datarefId, DatarefValue value) override
			{
				if (datarefId <= 0 ||
				    static_cast<std::size_t>(datarefId) > datarefs_.size())
				{
					return false;
				}
				std::lock_guard<std::mutex> lock(queueMutex_);
				if (pendingWrites_.size() >= kMaxQueuedWrites)
				{
					pendingWrites_.erase(pendingWrites_.begin());
				}
				pendingWrites_.push_back(
					PendingWrite{static_cast<std::size_t>(datarefId - 1), std::move(value)});
				return true;
			}

			bool ActivateCommand(int commandId) override
			{
				if (commandId <= 0 ||
				    static_cast<std::size_t>(commandId) > commands_.size())
				{
					return false;
				}
				std::lock_guard<std::mutex> lock(queueMutex_);
				if (pendingCommands_.size() >= kMaxQueuedCommands)
				{
					pendingCommands_.erase(pendingCommands_.begin());
				}
				pendingCommands_.push_back(commandId);
				return true;
			}

			// ---- Simulator-thread hooks ----

			void UpdateSnapshot() override
			{
				ResolveMissingRefs();

				std::vector<std::shared_ptr<const DatarefValue>> next;
				next.reserve(datarefs_.size());
				for (std::size_t i = 0; i < datarefs_.size(); ++i)
				{
					next.push_back(ReadLiveValue(i));
				}

				{
					std::lock_guard<std::mutex> lock(snapshotMutex_);
					snapshot_.swap(next);
				}
				// |next| now holds the previous snapshot and is freed outside the
				// lock, keeping the HTTP read path responsive.
			}

			void ProcessPending() override
			{
				std::vector<PendingWrite> writes;
				std::vector<int> commands;
				{
					std::lock_guard<std::mutex> lock(queueMutex_);
					writes.swap(pendingWrites_);
					commands.swap(pendingCommands_);
				}

				for (const PendingWrite& write : writes)
				{
					ApplyWrite(write);
				}
				for (const int commandId : commands)
				{
					ApplyCommand(commandId);
				}
			}

		private:
			static XPLMDataRef Resolve(const DatarefDefinition& definition)
			{
				const char* name =
					definition.refName != nullptr ? definition.refName : definition.name;
				return XPLMFindDataRef(name);
			}

			void ResolveMissingRefs()
			{
				for (std::size_t i = 0; i < refs_.size(); ++i)
				{
					if (refs_[i] == nullptr)
					{
						refs_[i] = Resolve(kDatarefDefinitions[i]);
					}
				}
				for (std::size_t i = 0; i < commandRefs_.size(); ++i)
				{
					if (commandRefs_[i] == nullptr)
					{
						commandRefs_[i] = XPLMFindCommand(commands_[i].name.c_str());
					}
				}
			}

			std::shared_ptr<const DatarefValue> ReadLiveValue(std::size_t index) const
			{
				const DatarefInfo& info = datarefs_[index];
				const XPLMDataRef ref = refs_[index];
				if (ref == nullptr)
				{
					return nullptr;
				}

				switch (info.type)
				{
				case DatarefType::Float:
				{
					const float value = XPLMGetDataf(ref);
					return MakeValue<double>(std::isfinite(value) ? value : 0.0);
				}
				case DatarefType::Double:
				{
					const double value = XPLMGetDatad(ref);
					return MakeValue<double>(std::isfinite(value) ? value : 0.0);
				}
				case DatarefType::Int:
					return MakeValue<std::int64_t>(
						static_cast<std::int64_t>(XPLMGetDatai(ref)));
				case DatarefType::FloatArray:
					return MakeValue<std::vector<double>>(ReadNumericArray(ref));
				case DatarefType::IntArray:
					return MakeValue<std::vector<std::int64_t>>(ReadIntegerArray(ref));
				case DatarefType::ByteString:
					return MakeValue<std::string>(ReadByteString(ref));
				case DatarefType::FlightIdArray:
					return MakeValue<std::vector<std::string>>(ReadFlightIds(ref));
				}
				return MakeValue<double>(0.0);
			}

			static std::vector<double> ReadNumericArray(XPLMDataRef ref)
			{
				const XPLMDataTypeID types = XPLMGetDataRefTypes(ref);

				if ((types & xplmType_FloatArray) != 0)
				{
					int count = XPLMGetDatavf(ref, nullptr, 0, 0);
					count = std::clamp(count, 0, kMaxArrayValues);
					if (count > 0)
					{
						std::vector<float> raw(static_cast<std::size_t>(count));
						const int read = XPLMGetDatavf(ref, raw.data(), 0, count);
						raw.resize(static_cast<std::size_t>(std::max(0, read)));
						std::vector<double> values;
						values.reserve(raw.size());
						for (const float item : raw)
						{
							values.push_back(std::isfinite(item) ? item : 0.0);
						}
						return values;
					}
				}

				if ((types & xplmType_IntArray) != 0)
				{
					int count = XPLMGetDatavi(ref, nullptr, 0, 0);
					count = std::clamp(count, 0, kMaxArrayValues);
					if (count > 0)
					{
						std::vector<int> raw(static_cast<std::size_t>(count));
						const int read = XPLMGetDatavi(ref, raw.data(), 0, count);
						raw.resize(static_cast<std::size_t>(std::max(0, read)));
						std::vector<double> values;
						values.reserve(raw.size());
						for (const int item : raw)
						{
							values.push_back(static_cast<double>(item));
						}
						return values;
					}
				}

				if ((types & xplmType_Data) != 0)
				{
					// Fallback for double arrays (the SDK 4.3 C API has no
					// XPLMGetDatavd); interpret the byte block as doubles.
					const int bytes = XPLMGetDatab(ref, nullptr, 0, 0);
					const int stride = static_cast<int>(sizeof(double));
					if (bytes >= stride)
					{
						int count = bytes / stride;
						count = std::clamp(count, 0, kMaxArrayValues);
						std::vector<double> raw(static_cast<std::size_t>(count));
						const int read = XPLMGetDatab(
							ref, raw.data(), 0, count * stride);
						if (read > 0)
						{
							raw.resize(static_cast<std::size_t>(read / stride));
						}
						else
						{
							raw.clear();
						}
						for (double& item : raw)
						{
							if (!std::isfinite(item))
							{
								item = 0.0;
							}
						}
						return raw;
					}
				}

				return {};
			}

			static std::vector<std::int64_t> ReadIntegerArray(XPLMDataRef ref)
			{
				const XPLMDataTypeID types = XPLMGetDataRefTypes(ref);

				if ((types & xplmType_IntArray) != 0)
				{
					int count = XPLMGetDatavi(ref, nullptr, 0, 0);
					count = std::clamp(count, 0, kMaxArrayValues);
					if (count > 0)
					{
						std::vector<int> raw(static_cast<std::size_t>(count));
						const int read = XPLMGetDatavi(ref, raw.data(), 0, count);
						raw.resize(static_cast<std::size_t>(std::max(0, read)));
						std::vector<std::int64_t> values;
						values.reserve(raw.size());
						for (const int item : raw)
						{
							values.push_back(static_cast<std::int64_t>(item));
						}
						return values;
					}
				}

				if ((types & xplmType_FloatArray) != 0)
				{
					const std::vector<double> floats = ReadNumericArray(ref);
					std::vector<std::int64_t> values;
					values.reserve(floats.size());
					for (const double item : floats)
					{
						values.push_back(
							std::isfinite(item)
								? static_cast<std::int64_t>(std::llround(item))
								: 0);
					}
					return values;
				}

				return {};
			}

			static std::string ReadByteString(XPLMDataRef ref)
			{
				const int bytes = XPLMGetDatab(ref, nullptr, 0, 0);
				if (bytes <= 0)
				{
					return {};
				}
				const int size = std::min(bytes, kMaxStringBytes);
				std::vector<char> raw(static_cast<std::size_t>(size));
				const int read = XPLMGetDatab(ref, raw.data(), 0, size);
				if (read <= 0)
				{
					return {};
				}
				std::size_t length = 0;
				while (length < static_cast<std::size_t>(read) && raw[length] != '\0')
				{
					++length;
				}
				while (length > 0 &&
				       (raw[length - 1] == ' ' || raw[length - 1] == '\t' ||
				        raw[length - 1] == '\r' || raw[length - 1] == '\n'))
				{
					--length;
				}
				return std::string(raw.data(), length);
			}

			static std::vector<std::string> ReadFlightIds(XPLMDataRef ref)
			{
				const int bytes = XPLMGetDatab(ref, nullptr, 0, 0);
				if (bytes < kFlightIdStride)
				{
					return {};
				}
				const int slots = std::min(bytes / kFlightIdStride, kFlightIdSlots);
				std::vector<char> raw(static_cast<std::size_t>(slots * kFlightIdStride));
				const int read = XPLMGetDatab(
					ref, raw.data(), 0, static_cast<int>(raw.size()));
				if (read <= 0)
				{
					return {};
				}
				const int usable = read - (read % kFlightIdStride);
				std::vector<std::string> ids;
				ids.reserve(static_cast<std::size_t>(usable / kFlightIdStride));
				for (int offset = 0; offset < usable; offset += kFlightIdStride)
				{
					std::size_t length = 0;
					while (length < static_cast<std::size_t>(kFlightIdStride) &&
					       raw[static_cast<std::size_t>(offset) + length] != '\0')
					{
						++length;
					}
					while (length > 0)
					{
						const char last = raw[static_cast<std::size_t>(offset) + length - 1];
						if (last != ' ' && last != '\t' && last != '\r' && last != '\n')
						{
							break;
						}
						--length;
					}
					ids.emplace_back(raw.data() + offset, length);
				}
				return ids;
			}

			void ApplyWrite(const PendingWrite& write)
			{
				if (write.index >= datarefs_.size())
				{
					return;
				}
				const DatarefInfo& info = datarefs_[write.index];
				XPLMDataRef ref = refs_[write.index];
				if (ref == nullptr)
				{
					refs_[write.index] = Resolve(kDatarefDefinitions[write.index]);
					ref = refs_[write.index];
				}
				if (ref == nullptr || XPLMCanWriteDataRef(ref) == 0)
				{
					return;
				}

				switch (info.type)
				{
				case DatarefType::Float:
					if (const auto value = AsDouble(write.value))
					{
						XPLMSetDataf(ref, static_cast<float>(*value));
					}
					break;
				case DatarefType::Double:
					if (const auto value = AsDouble(write.value))
					{
						XPLMSetDatad(ref, *value);
					}
					break;
				case DatarefType::Int:
					if (const auto value = AsInteger(write.value))
					{
						const std::int64_t clamped = std::clamp<std::int64_t>(
							*value,
							std::numeric_limits<int>::min(),
							std::numeric_limits<int>::max());
						XPLMSetDatai(ref, static_cast<int>(clamped));
					}
					break;
				case DatarefType::FloatArray:
					if (const auto values = AsDoubleArray(write.value))
					{
						std::vector<float> converted;
						converted.reserve(values->size());
						for (const double item : *values)
						{
							converted.push_back(static_cast<float>(item));
						}
						if (!converted.empty())
						{
							XPLMSetDatavf(
								ref,
								converted.data(),
								0,
								static_cast<int>(converted.size()));
						}
					}
					break;
				case DatarefType::IntArray:
					if (const auto values = AsIntegerArray(write.value))
					{
						std::vector<int> converted;
						converted.reserve(values->size());
						for (const std::int64_t item : *values)
						{
							converted.push_back(static_cast<int>(std::clamp<std::int64_t>(
								item,
								std::numeric_limits<int>::min(),
								std::numeric_limits<int>::max())));
						}
						if (!converted.empty())
						{
							XPLMSetDatavi(
								ref,
								converted.data(),
								0,
								static_cast<int>(converted.size()));
						}
					}
					break;
				case DatarefType::ByteString:
				case DatarefType::FlightIdArray:
					// Read-only through this bridge.
					break;
				}
			}

			void ApplyCommand(int commandId)
			{
				if (commandId <= 0 ||
				    static_cast<std::size_t>(commandId) > commandRefs_.size())
				{
					return;
				}
				const std::size_t index = static_cast<std::size_t>(commandId - 1);
				if (commandRefs_[index] == nullptr)
				{
					commandRefs_[index] = XPLMFindCommand(commands_[index].name.c_str());
				}
				if (commandRefs_[index] != nullptr)
				{
					XPLMCommandOnce(commandRefs_[index]);
				}
			}

			std::vector<DatarefInfo> datarefs_;
			std::vector<XPLMDataRef> refs_;  // XPLM opaque handles, sim-thread access only
			std::vector<CommandInfo> commands_;
			std::vector<XPLMCommandRef> commandRefs_;

			mutable std::mutex snapshotMutex_;
			std::vector<std::shared_ptr<const DatarefValue>> snapshot_;

			std::mutex queueMutex_;
			std::vector<PendingWrite> pendingWrites_;
			std::vector<int> pendingCommands_;
		};
	}

	std::shared_ptr<Backend> CreateXplmBackend()
	{
		return std::make_shared<XplmBackend>();
	}
}
