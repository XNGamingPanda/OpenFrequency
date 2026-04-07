# X-Plane Traffic Bridge

OpenFrequency can run its own synthetic/live traffic model and expose it to X-Plane using the official TCAS override path instead of driving X-Plane's default AI aircraft.

## Architecture

OpenFrequency side:
- `TrafficStateManager` owns the traffic objects.
- `/api/xplane/traffic_targets` exports up to 63 traffic targets as JSON.
- The exported targets include callsign, Mode-S style ID, position, altitude, heading, groundspeed, vertical speed, and state.

X-Plane side:
- `plugins/xplane/OpenFrequencyTrafficBridge.py` is an XPPython3 plugin example.
- The plugin acquires AI planes from X-Plane.
- It enables `override_TCAS`.
- It publishes traffic into TCAS target datarefs every flight loop.

Result:
- X-Plane renders the targets through the official TCAS/traffic interface.
- Third-party readers that consume TCAS or multiplayer-style traffic data can see them.
- OpenFrequency remains the owner of the traffic logic. X-Plane is only the publication layer.

## Install

1. Install XPPython3 in X-Plane 12.
2. Copy `plugins/xplane/OpenFrequencyTrafficBridge.py` into:
   `Resources/plugins/PythonPlugins/`
3. Start OpenFrequency.
4. Start X-Plane.

## Export Endpoint

Default endpoint:

`http://127.0.0.1:5000/api/xplane/traffic_targets?limit=63`

Example payload:

```json
{
  "targets": [
    {
      "slot": 1,
      "mode_s_id": 123456,
      "flight_id": "CCA1234",
      "latitude": 30.12,
      "longitude": 120.31,
      "altitude_ft": 4200,
      "heading_deg": 184.0,
      "groundspeed_kt": 168.0,
      "vertical_speed_fpm": -700.0,
      "on_ground": false,
      "state": "APPROACH"
    }
  ],
  "count": 1,
  "source": "openfrequency_self_managed"
}
```

## Notes

- This avoids trying to directly fly X-Plane default AI aircraft around the airport.
- If no external live traffic feed is available yet, OpenFrequency can still self-manage synthetic traffic and publish it through the same bridge.
- The plugin script is intentionally minimal and is meant as a reference bridge. If you want a production-grade plugin, the next step is to port the same logic into a compiled X-Plane SDK plugin.
