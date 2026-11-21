#pragma once

// LocalWebApi: an X-Plane 12 "Local Web API" compatible HTTP server.
//
// X-Plane 11 does not expose the Local Web API that OpenFrequency uses for its
// X-Plane provider. This module publishes the same REST subset on
// 127.0.0.1:<port> (8086 by default), so OpenFrequency can treat X-Plane 11
// exactly like X-Plane 12 without any changes on the OpenFrequency side.
//
// Threading contract:
//   * The HTTP thread only calls Backend::Read/Write/ActivateCommand.
//   * Backend::UpdateSnapshot/ProcessPending are called from the X-Plane flight
//     loop thread by the host plugin. The backend implementation is responsible
//     for making simulator access safe (this plugin marshals it).
//   * The server never touches XPLM/XPL types directly.

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace openatc::webapi
{
	enum class DatarefType
	{
		Float,
		Double,
		Int,
		FloatArray,
		IntArray,
		ByteString,
		FlightIdArray,
	};

	struct DatarefInfo
	{
		std::string name;
		DatarefType type = DatarefType::Float;
		int id = 0;
	};

	struct CommandInfo
	{
		std::string name;
		int id = 0;
	};

	// JSON-compatible representation of a dataref value. Byte strings are
	// exposed as std::string and the TCAS flight-id block as a vector of
	// per-slot strings, matching what OpenFrequency's parser expects.
	using DatarefValue = std::variant<
		double,
		std::int64_t,
		std::string,
		std::vector<double>,
		std::vector<std::int64_t>,
		std::vector<std::string>>;

	// Implemented by the simulator side. The server owns a shared reference so
	// the host plugin can also drive the snapshot/command processing.
	class Backend
	{
	public:
		virtual ~Backend() = default;

		virtual const std::vector<DatarefInfo>& Datarefs() const = 0;
		virtual const std::vector<CommandInfo>& Commands() const = 0;

		// Called from the HTTP thread. Must be thread-safe. Returning
		// std::nullopt means "dataref is currently unavailable".
		virtual std::optional<DatarefValue> Read(int datarefId) const = 0;

		// Called from the HTTP thread. Queues a write for the simulator
		// thread; returns false only when the dataref id is unknown.
		virtual bool Write(int datarefId, DatarefValue value) = 0;

		// Called from the HTTP thread. Queues a command activation.
		virtual bool ActivateCommand(int commandId) = 0;

		// ---- Hooks invoked by the host on the simulator thread ----

		// Refresh the thread-safe value cache from the simulator.
		virtual void UpdateSnapshot() {}
		// Apply queued writes and command activations to the simulator.
		virtual void ProcessPending() {}
	};

	// Minimal HTTP/1.1 server. Loopback only, keep-alive enabled.
	class Server
	{
	public:
		Server(std::uint16_t port, std::shared_ptr<Backend> backend);
		~Server();

		Server(const Server&) = delete;
		Server& operator=(const Server&) = delete;
		Server(Server&&) = delete;
		Server& operator=(Server&&) = delete;

		// Binds and starts the listener thread. On failure, fills |error| and
		// leaves the server stopped.
		bool Start(std::string& error);

		// Stops the listener and joins the thread. Safe to call repeatedly.
		void Stop();

		bool Running() const noexcept;

		// Returns and clears diagnostic lines produced by the server thread.
		std::vector<std::string> DrainLogs();

		std::shared_ptr<Backend> GetBackend() const noexcept;

	private:
		class Impl;
		std::unique_ptr<Impl> impl_;
	};
}
