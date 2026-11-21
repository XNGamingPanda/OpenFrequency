#pragma once

#include "LocalWebApi.h"

#include <memory>

namespace openatc::webapi
{
	// Creates the X-Plane backed implementation used by LocalWebApi::Server.
	// Must be called from the simulator thread (XPLM lookup calls are made in
	// the constructor). UpdateSnapshot()/ProcessPending() must also be called
	// from the simulator thread; Read/Write/ActivateCommand are thread-safe.
	std::shared_ptr<Backend> CreateXplmBackend();
}
