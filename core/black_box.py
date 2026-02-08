"""
BlackBox Recorder - Records flight data at 2Hz for flight analysis.
Extended version with full telemetry for post-flight reports.
"""
import time
import os
import json
import threading
from datetime import datetime
from collections import deque
from .context import event_bus

# Optional dependencies for reporting
try:
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    import pyautogui
    REPORTING_AVAILABLE = True
except ImportError:
    print("BlackBox: Warning - Reporting dependencies (pandas, matplotlib, pyautogui) not found.")
    REPORTING_AVAILABLE = False


class BlackBox:
    """Records flight data for post-flight analysis at 2Hz."""
    
    def __init__(self, config):
        self.config = config
        self.enabled = config.get('debug', {}).get('black_box', True)
        
        # Flight data buffer (circular, last 60 minutes at 2Hz = 7200 records)
        self.flight_data = deque(maxlen=7200)
        
        # Landing detection state
        self.was_on_ground = True
        self.landing_data = None
        
        # Flight end detection state
        self.flight_started = False
        self.flight_ended = False
        self.flight_start_time = None
        self.departure_airport = None
        
        # Subscribe to telemetry
        event_bus.on('telemetry_update', self.on_telemetry)
        
        # 2Hz recording timer (0.5s interval)
        self._last_record_time = 0
        self._record_interval = 0.5  # 2Hz
        
        # Data directory
        self.data_dir = "data/reports"
        self.img_dir = os.path.join(self.data_dir, "img")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.img_dir, exist_ok=True)
        
        print("BlackBox: Initialized (2Hz extended recording)")
    
    def on_telemetry(self, data):
        """Handle telemetry updates and record at 2Hz."""
        current_time = time.time()
        
        # 2Hz rate limiting
        if current_time - self._last_record_time < self._record_interval:
            return
        self._last_record_time = current_time
        
        ac = data.get('aircraft', {})
        
        # Extended record with all flight data
        record = {
            'timestamp': current_time,
            'latitude': ac.get('latitude', 0),
            'longitude': ac.get('longitude', 0),
            'altitude': ac.get('altitude', 0),
            'airspeed': ac.get('airspeed', 0),
            'heading': ac.get('heading', 0),
            'g_force': ac.get('g_force', 1.0),
            'on_ground': ac.get('on_ground', True),
            'throttle': ac.get('throttle', 0),
            'flaps': ac.get('flaps', 0),
            # Extended fields
            'n1': ac.get('n1', 0),
            'egt': ac.get('egt', 0),
            'vs': ac.get('vs', 0),  # Vertical speed ft/min
            'pitch': ac.get('pitch', 0),
            'bank': ac.get('bank', 0),
            'wind_dir': ac.get('wind_dir', 0),
            'wind_spd': ac.get('wind_spd', 0),
            'fuel_flow': ac.get('fuel_flow', 0),
            'parking_brake': ac.get('parking_brake', False),
            'gear': ac.get('gear', 0),
            'combustion': ac.get('combustion', True)
        }
        
        self.flight_data.append(record)
        
        # Detect flight phases
        on_ground = ac.get('on_ground', True)
        airspeed = ac.get('airspeed', 0)
        parking_brake = ac.get('parking_brake', False)
        combustion = ac.get('combustion', True)
        n1 = ac.get('n1', 0)
        
        # Flight start: liftoff or high speed on ground
        if not self.flight_started and (not on_ground or airspeed > 40):
            self.flight_started = True
            self.flight_ended = False
            self.flight_start_time = current_time
            print("BlackBox: Flight started (liftoff/takeoff roll detected)")
            event_bus.emit('flight_started', {'timestamp': current_time})
        
        # Landing detection
        if self.flight_started and not self.was_on_ground and on_ground:
            self._capture_landing(record)
        
        # Flight end: (speed < 1kt) AND (parking_brake OR engine_off) AND on_ground
        if self.flight_started and not self.flight_ended and on_ground:
            engine_off = n1 < 5 or not combustion
            stopped = airspeed < 1
            
            if stopped and (parking_brake or engine_off):
                # Debounce: Ensure we stay stopped for a moment? 
                # For now, immediate trigger is fine as these are deliberate actions
                self._end_flight(record)
        
        self.was_on_ground = on_ground
    
    def _capture_landing(self, touchdown_record):
        """Capture landing moment data for analysis."""
        print(f"BlackBox: Landing detected! G-Force: {touchdown_record['g_force']:.2f}")
        
        recent_data = list(self.flight_data)[-20:]  # Last 10 seconds at 2Hz
        
        touchdown_g = touchdown_record['g_force']
        
        # Count bounces
        bounces = 0
        last_ground_state = True
        for r in recent_data:
            if r['on_ground'] != last_ground_state:
                if r['on_ground']:
                    bounces += 1
                last_ground_state = r['on_ground']
        
        # Heading stability
        heading_changes = []
        for i in range(1, len(recent_data)):
            hdg_diff = abs(recent_data[i]['heading'] - recent_data[i-1]['heading'])
            if hdg_diff > 180:
                hdg_diff = 360 - hdg_diff
            heading_changes.append(hdg_diff)
        
        heading_stability = sum(heading_changes) / len(heading_changes) if heading_changes else 0
        
        self.landing_data = {
            'timestamp': touchdown_record['timestamp'],
            'g_force': touchdown_g,
            'bounces': max(0, bounces - 1),
            'heading_stability': heading_stability,
            'touchdown_speed': touchdown_record['airspeed'],
            'flaps': touchdown_record['flaps'],
            'pitch': touchdown_record['pitch'],
            'vs': touchdown_record['vs']
        }
        
        event_bus.emit('landing_detected', self.landing_data)

        # Feature 2.11: Passenger Reaction
        reaction_type = 'normal'
        if touchdown_g < 1.3:
            reaction_type = 'applause'
        elif touchdown_g > 1.8:
            reaction_type = 'scream'
            
        print(f"BlackBox: Passenger Reaction -> {reaction_type.upper()}")
        event_bus.emit('passenger_reaction', {'type': reaction_type, 'g_force': touchdown_g})
    
    def _end_flight(self, final_record):
        """Handle flight end and trigger report generation."""
        self.flight_ended = True
        self.flight_started = False # Reset
        flight_duration = time.time() - self.flight_start_time if self.flight_start_time else 0
        
        # Only report if flight was > 1 minute (ignore taxi tests)
        if flight_duration < 60:
            print(f"BlackBox: Flight ended but too short ({flight_duration:.1f}s). No report.")
            return

        print(f"BlackBox: Flight ended! Duration: {flight_duration/60:.1f} minutes. Generating report...")
        
        # Generate Report in background thread
        if REPORTING_AVAILABLE:
            threading.Thread(target=self._generate_report_thread, args=(flight_duration, final_record)).start()
        else:
            print("BlackBox: Reporting disabled (dependencies missing).")

    def _generate_report_thread(self, duration, final_record):
        """Background thread to generate charts and HTML."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_id = f"flight_{timestamp}"
            
            # 1. Screenshot
            screenshot_path = os.path.join(self.img_dir, f"{report_id}_screen.png")
            try:
                pyautogui.screenshot(screenshot_path)
                print(f"BlackBox: Screenshot saved to {screenshot_path}")
            except Exception as e:
                print(f"BlackBox: Screenshot failed: {e}")
                screenshot_path = None

            # 2. DataFrame
            df = pd.DataFrame(list(self.flight_data))
            # Filter for this flight only (approximate based on start time)
            if self.flight_start_time:
                df = df[df['timestamp'] >= self.flight_start_time]
            
            if df.empty:
                print("BlackBox: No data to report.")
                return

            # Relative time
            start_t = df['timestamp'].iloc[0]
            df['t_min'] = (df['timestamp'] - start_t) / 60.0
            
            # 3. Charts
            # Altitude & Speed
            fig, ax1 = plt.subplots(figsize=(10, 6))
            ax1.set_xlabel('Time (min)')
            ax1.set_ylabel('Altitude (ft)', color='tab:blue')
            ax1.plot(df['t_min'], df['altitude'], color='tab:blue', label='Altitude')
            ax1.tick_params(axis='y', labelcolor='tab:blue')
            
            ax2 = ax1.twinx()
            ax2.set_ylabel('Airspeed (kts)', color='tab:orange')
            ax2.plot(df['t_min'], df['airspeed'], color='tab:orange', label='Airspeed')
            ax2.tick_params(axis='y', labelcolor='tab:orange')
            
            plt.title('Flight Profile: Altitude & Speed')
            chart1_path = os.path.join(self.img_dir, f"{report_id}_profile.png")
            plt.savefig(chart1_path)
            plt.close()
            
            # G-Force & Pitch
            fig, ax1 = plt.subplots(figsize=(10, 6))
            ax1.set_xlabel('Time (min)')
            ax1.set_ylabel('G-Force', color='tab:red')
            ax1.plot(df['t_min'], df['g_force'], color='tab:red', label='G-Force')
            ax1.tick_params(axis='y', labelcolor='tab:red')
            # Add 1G line
            ax1.axhline(y=1.0, color='gray', linestyle='--', alpha=0.5)
            
            ax2 = ax1.twinx()
            ax2.set_ylabel('Pitch (deg)', color='tab:green')
            ax2.plot(df['t_min'], df['pitch'], color='tab:green', label='Pitch')
            ax2.tick_params(axis='y', labelcolor='tab:green')
            
            plt.title('Flight Dynamics: G-Force & Pitch')
            chart2_path = os.path.join(self.img_dir, f"{report_id}_dynamics.png")
            plt.savefig(chart2_path)
            plt.close()
            
            # 4. Stats
            stats = self._calculate_flight_stats()
            landing = self.landing_data or {}
            
            # Passenger Comments Generation
            g_force = landing.get('g_force', 1.0)
            pax_mood = "Neutral"
            pax_comment = "The flight was okay."
            score = 80
            
            if g_force < 1.2:
                pax_mood = "Ecstatic (Butter Landing)"
                pax_comment = "Total Butter! Did we even touch the ground? 👏"
                score = 100
            elif g_force < 1.5:
                pax_mood = "Happy"
                pax_comment = "Smooth landing, captain."
                score = 90
            elif g_force < 1.8:
                pax_mood = "Concerned"
                pax_comment = "A bit firm, but we're alive."
                score = 70
            elif g_force < 2.5:
                pax_mood = "Terminated"
                pax_comment = "My coffee is on the ceiling! 😱"
                score = 40
            else:
                pax_mood = "Traumatized"
                pax_comment = "Grandma's dentures flew into the cockpit. I'm suing! 🚑"
                score = 0
            
            # 5. HTML Generation
            html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Flight Report {timestamp}</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
                <style>
                    body {{ background: #f8f9fa; padding: 20px; }}
                    .card {{ margin-bottom: 20px; border: none; shadow: 0 4px 6px rgba(0,0,0,0.1); }}
                    .stat-value {{ font-size: 1.5rem; font-weight: bold; color: #2c3e50; }}
                    .stat-label {{ color: #7f8c8d; font-size: 0.9rem; }}
                    .mood-score {{ font-size: 2rem; color: {'#2ecc71' if score > 80 else '#e74c3c'}; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1 class="mb-4">✈️ Flight Report <small class="text-muted">{timestamp}</small></h1>
                    
                    <!-- Passenger Mood Section -->
                    <div class="card p-4 bg-white border-start border-5 border-{'success' if score > 80 else 'danger'}">
                        <div class="row align-items-center">
                            <div class="col-md-3 text-center">
                                <div class="text-muted">Passenger Satisfaction</div>
                                <div class="mood-score">{score}/100</div>
                            </div>
                            <div class="col-md-9">
                                <h5>{pax_mood}</h5>
                                <p class="lead fst-italic">"{pax_comment}"</p>
                            </div>
                        </div>
                    </div>

                    <!-- Stats Grid -->
                    <div class="row mb-4">
                        <div class="col-md-4">
                            <div class="card p-3">
                                <div class="stat-label">Duration</div>
                                <div class="stat-value">{duration/60:.1f} min</div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="card p-3">
                                <div class="stat-label">Max Altitude</div>
                                <div class="stat-value">{stats.get('max_altitude', 0):.0f} ft</div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="card p-3">
                                <div class="stat-label">Max G-Force</div>
                                <div class="stat-value">{stats.get('max_g_force', 0):.2f} G</div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="row mb-4">
                        <div class="col-md-4">
                            <div class="card p-3">
                                <div class="stat-label">Landing G</div>
                                <div class="stat-value" style="color: {'green' if landing.get('g_force', 1) < 1.5 else 'red'}">{landing.get('g_force', 0):.2f} G</div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="card p-3">
                                <div class="stat-label">Touchdown Speed</div>
                                <div class="stat-value">{landing.get('touchdown_speed', 0):.0f} kts</div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="card p-3">
                                <div class="stat-label">Avg Fuel Flow</div>
                                <div class="stat-value">{stats.get('avg_fuel_flow', 0):.1f} pph</div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Screenshot -->
                    <div class="card p-3">
                        <h5>Cockpit View (End of Flight)</h5>
                        {'<img src="img/' + os.path.basename(screenshot_path) + '" class="img-fluid rounded">' if screenshot_path else '<p class="text-muted">No screenshot available (PyAutoGUI optional)</p>'}
                    </div>

                    <!-- Charts -->
                    <div class="row">
                        <div class="col-md-6">
                            <div class="card p-3">
                                <h5>Profile</h5>
                                <img src="img/{os.path.basename(chart1_path)}" class="img-fluid">
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="card p-3">
                                <h5>Dynamics</h5>
                                <img src="img/{os.path.basename(chart2_path)}" class="img-fluid">
                            </div>
                        </div>
                    </div>
                    
                    <div class="text-center mt-4">
                        <a href="/" class="btn btn-primary">Back to Dashboard</a>
                    </div>
                </div>
            </body>
            </html>
            """
            
            report_filename = f"report_{timestamp}.html"
            report_path = os.path.join(self.data_dir, report_filename)
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
                
            print(f"BlackBox: Report generated at {report_path}")
            
            # Emit event to notify UI
            event_bus.emit('flight_report_ready', {
                'message': f"Flight processed. Score: {score}/100",
                'report_url': f"/reports/{report_filename}",
                'timestamp': timestamp
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"BlackBox: Report generation failed: {e}")

    def _calculate_flight_stats(self):
        """Calculate comprehensive flight statistics."""
        if not self.flight_data:
            return {}
        
        data = list(self.flight_data)
        
        # Basic stats
        max_altitude = max(r['altitude'] for r in data)
        max_airspeed = max(r['airspeed'] for r in data)
        max_g = max(r['g_force'] for r in data)
        min_g = min(r['g_force'] for r in data)
        max_bank = max(abs(r['bank']) for r in data)
        max_pitch = max(abs(r['pitch']) for r in data)
        
        # Fuel consumption
        fuel_flows = [r['fuel_flow'] for r in data if r['fuel_flow'] > 0]
        avg_fuel_flow = sum(fuel_flows) / len(fuel_flows) if fuel_flows else 0
        
        # Flight time in air
        airborne_records = [r for r in data if not r['on_ground']]
        airborne_time = len(airborne_records) * self._record_interval
        
        # Max vertical speed
        max_vs_up = max(r['vs'] for r in data)
        max_vs_down = min(r['vs'] for r in data)
        
        return {
            'max_altitude': max_altitude,
            'max_airspeed': max_airspeed,
            'max_g_force': max_g,
            'min_g_force': min_g,
            'max_bank_angle': max_bank,
            'max_pitch_angle': max_pitch,
            'avg_fuel_flow': avg_fuel_flow,
            'airborne_time': airborne_time,
            'max_climb_rate': max_vs_up,
            'max_descent_rate': abs(max_vs_down),
            'total_records': len(data)
        }
    
    def clear(self):
        """Clear all recorded data (for new flight)."""
        self.flight_data.clear()
        self.landing_data = None
        self.was_on_ground = True
        self.flight_started = False
        self.flight_ended = False
        self.flight_start_time = None
