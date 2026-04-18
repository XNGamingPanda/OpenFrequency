"""
BlackBox Recorder - Records flight data at 2Hz for flight analysis.
Extended version with full telemetry for post-flight reports.
"""
import time
import os
import json
import threading
import math
from datetime import datetime
from collections import deque
from .context import event_bus, shared_context, context_lock

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

    REPORT_COPY = {
        'en': {
            'report_title': 'Flight Analysis Report',
            'report_subtitle': 'Structured post-flight review with operational insights',
            'summary_title': 'Executive Summary',
            'metrics_title': 'Key Metrics',
            'analysis_title': 'Phase Analysis',
            'findings_title': 'Findings',
            'score_title': 'Flight Quality Score',
            'charts_title': 'Telemetry Charts',
            'screenshot_title': 'End-of-Flight Snapshot',
            'no_screenshot': 'No screenshot available for this flight.',
            'narrative_title': 'Narrative Review',
        },
        'zh': {
            'report_title': '飞行分析报告',
            'report_subtitle': '包含运营洞察的结构化落地复盘',
            'summary_title': '执行摘要',
            'metrics_title': '关键指标',
            'analysis_title': '飞行阶段分析',
            'findings_title': '重点发现',
            'score_title': '飞行质量评分',
            'charts_title': '飞行曲线',
            'screenshot_title': '落地后座舱画面',
            'no_screenshot': '本次飞行没有可用截图。',
            'narrative_title': '文字点评',
        },
        'ja': {
            'report_title': 'フライト分析レポート',
            'report_subtitle': '運航インサイト付きの構造化ポストフライトレビュー',
            'summary_title': 'エグゼクティブサマリー',
            'metrics_title': '主要指標',
            'analysis_title': 'フェーズ分析',
            'findings_title': '主要所見',
            'score_title': 'フライト品質スコア',
            'charts_title': 'テレメトリーチャート',
            'screenshot_title': '到着後コックピット画面',
            'no_screenshot': 'このフライトではスクリーンショットを取得できませんでした。',
            'narrative_title': '総合コメント',
        },
    }
    
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
        self.destination_airport = None
        self.arrival_prompt_emitted = False
        self.awaiting_manual_end = False
        self.last_report_url = None
        
        # Subscribe to telemetry
        event_bus.on('telemetry_update', self.on_telemetry)
        
        # 2Hz recording timer (0.5s interval)
        self._last_record_time = 0
        self._record_interval = 0.5  # 2Hz
        
        # Data directory
        runtime_root = os.environ.get("OPENFREQUENCY_RUNTIME_DIR", os.getcwd())
        self.data_dir = os.path.join(runtime_root, "data", "reports")
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
        valid_sim_data = self._has_valid_sim_data(ac)
        if not self.destination_airport or self.destination_airport == 'N/A':
            _, self.destination_airport = self._flight_plan_airports()

        # Flight start: liftoff or high speed on ground
        if not self.flight_started and valid_sim_data and (not on_ground or airspeed > 40):
            self.flight_started = True
            self.flight_ended = False
            self.flight_start_time = current_time
            self.arrival_prompt_emitted = False
            self.awaiting_manual_end = False
            self.departure_airport, self.destination_airport = self._flight_plan_airports()
            print("BlackBox: Flight started (liftoff/takeoff roll detected)")
            event_bus.emit('flight_started', {
                'timestamp': current_time,
                'origin': self.departure_airport or 'N/A',
                'destination': self.destination_airport or 'N/A',
            })
        
        # Landing detection
        if self.flight_started and not self.was_on_ground and on_ground:
            self._capture_landing(record)
        
        if self.flight_started and not self.flight_ended and on_ground:
            if self._should_offer_manual_end(ac):
                self.awaiting_manual_end = True
                if not self.arrival_prompt_emitted:
                    self.arrival_prompt_emitted = True
                    event_bus.emit('flight_arrival_ready', {
                        'origin': self.departure_airport or 'N/A',
                        'destination': self.destination_airport or 'N/A',
                        'current_airport': self._current_airport() or 'N/A',
                    })
                self.was_on_ground = on_ground
                return

        # Flight end: (speed < 1kt) AND (parking_brake OR engine_off) AND on_ground
        if self.flight_started and not self.flight_ended and not self.awaiting_manual_end and on_ground:
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
        self.awaiting_manual_end = False
        self.arrival_prompt_emitted = False
        flight_duration = time.time() - self.flight_start_time if self.flight_start_time else 0
        
        # Only report if flight was > 1 minute (ignore taxi tests)
        if flight_duration < 60:
            print(f"BlackBox: Flight ended but too short ({flight_duration:.1f}s). No report.")
            return

        print(f"BlackBox: Flight ended! Duration: {flight_duration/60:.1f} minutes. Generating report...")
        event_bus.emit('flight_ended', {
            'timestamp': time.time(),
            'duration_sec': flight_duration,
            'origin': self.departure_airport or 'N/A',
            'destination': self.destination_airport or 'N/A',
        })
        
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
            stats = self._calculate_flight_stats(df)
            landing = self.landing_data or {}
            analysis = self._build_report_analysis(df, stats, landing, duration)
            score = analysis['score']
            findings_html = ''.join(f'<li>{item}</li>' for item in analysis['findings'])
            screenshot_html = (
                f'<img src="img/{os.path.basename(screenshot_path)}" alt="flight screenshot">'
                if screenshot_path else
                f'<p class="muted">{self.REPORT_COPY["en"]["no_screenshot"]}</p>'
            )

            # 5. HTML Generation
            html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Flight Analysis Report {timestamp}</title>
                <style>
                    :root {{
                        --bg: #f4efe7;
                        --panel: rgba(255,255,255,0.84);
                        --ink: #172126;
                        --muted: #60717a;
                        --line: rgba(23,33,38,0.1);
                        --accent: #0d6c63;
                        --shadow: 0 24px 80px rgba(27, 39, 47, 0.10);
                    }}
                    * {{ box-sizing: border-box; }}
                    body {{ margin: 0; font-family: "Segoe UI", "PingFang SC", "Hiragino Sans", "Noto Sans CJK SC", sans-serif; color: var(--ink); background: radial-gradient(circle at top left, rgba(13,108,99,0.18), transparent 32%), radial-gradient(circle at top right, rgba(219,104,63,0.15), transparent 28%), linear-gradient(180deg, #fbf8f2 0%, var(--bg) 100%); }}
                    .page {{ max-width: 1320px; margin: 0 auto; padding: 36px 28px 48px; }}
                    .panel {{ background: var(--panel); backdrop-filter: blur(10px); border: 1px solid var(--line); border-radius: 24px; box-shadow: var(--shadow); }}
                    .hero {{ display: grid; grid-template-columns: 1.5fr 0.85fr; gap: 20px; margin-bottom: 22px; }}
                    .hero-main, .score-panel, .summary {{ padding: 28px; }}
                    .eyebrow {{ text-transform: uppercase; letter-spacing: 0.16em; font-size: 12px; color: var(--accent); font-weight: 700; }}
                    h1 {{ margin: 10px 0 8px; font-size: 40px; line-height: 1.05; }}
                    h2 {{ margin: 0; font-size: 22px; }}
                    h3 {{ margin: 0 0 10px; font-size: 18px; }}
                    .subtitle {{ color: var(--muted); font-size: 16px; max-width: 60ch; line-height: 1.6; }}
                    .tripline {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 22px; }}
                    .chip {{ padding: 10px 14px; border-radius: 999px; background: rgba(13,108,99,0.08); color: var(--accent); font-weight: 600; }}
                    .score-panel {{ background: linear-gradient(160deg, rgba(13,108,99,0.95), rgba(23,33,38,0.92)); color: white; display:flex; flex-direction:column; justify-content:space-between; }}
                    .score-value {{ font-size: 64px; line-height: 1; font-weight: 800; margin: 10px 0 6px; }}
                    .score-band {{ color: rgba(255,255,255,0.82); font-size: 14px; }}
                    .section {{ margin-bottom: 22px; }}
                    .section-head {{ display:flex; justify-content:space-between; gap:16px; align-items:baseline; margin-bottom:14px; }}
                    .section-head p, .muted {{ margin:0; color: var(--muted); }}
                    .summary-grid, .metric-grid, .analysis-grid, .lang-grid, .media-grid {{ display:grid; gap:16px; }}
                    .summary-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top:16px; }}
                    .metric-grid {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
                    .analysis-grid, .lang-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
                    .media-grid {{ grid-template-columns: 1.1fr 1fr; }}
                    .mini, .metric, .phase, .language-card, .media {{ border: 1px solid var(--line); border-radius: 18px; background: rgba(255,255,255,0.76); padding: 18px; }}
                    .metric-value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
                    .label {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; }}
                    .phase p, .language-card p {{ margin: 0; line-height: 1.7; }}
                    .finding-list {{ margin: 0; padding-left: 20px; line-height: 1.7; }}
                    .chart-stack {{ display:grid; gap:14px; }}
                    img {{ width:100%; display:block; border-radius:16px; border:1px solid var(--line); }}
                    @media (max-width: 1100px) {{ .hero, .summary-grid, .metric-grid, .analysis-grid, .lang-grid, .media-grid {{ grid-template-columns: 1fr; }} }}
                </style>
            </head>
            <body>
                <div class="page">
                    <section class="hero">
                        <div class="panel hero-main">
                            <div class="eyebrow">{self.REPORT_COPY['en']['report_title']}</div>
                            <h1>{self.REPORT_COPY['zh']['report_title']} / {self.REPORT_COPY['ja']['report_title']}</h1>
                            <p class="subtitle">{analysis['headline']}</p>
                            <div class="tripline">
                                <div class="chip">{self.departure_airport or 'N/A'} → {self.destination_airport or 'N/A'}</div>
                                <div class="chip">{timestamp}</div>
                                <div class="chip">{analysis['grade']}</div>
                            </div>
                        </div>
                        <div class="panel score-panel">
                            <div>
                                <div class="eyebrow">{self.REPORT_COPY['en']['score_title']}</div>
                                <div class="score-value">{score}</div>
                                <div class="score-band">{analysis['score_label_en']} / {analysis['score_label_zh']} / {analysis['score_label_ja']}</div>
                            </div>
                            <div>{analysis['summary_en']}</div>
                        </div>
                    </section>

                    <section class="panel summary section">
                        <div class="section-head">
                            <h2>{self.REPORT_COPY['en']['summary_title']} / {self.REPORT_COPY['zh']['summary_title']} / {self.REPORT_COPY['ja']['summary_title']}</h2>
                            <p>{self.REPORT_COPY['en']['report_subtitle']}</p>
                        </div>
                        <div class="summary-grid">
                            {self._metric_card('Route', f"{self.departure_airport or 'N/A'} → {self.destination_airport or 'N/A'}")}
                            {self._metric_card('Duration', self._format_minutes(duration / 60.0))}
                            {self._metric_card('Estimated Distance', f"{stats.get('estimated_distance_nm', 0):.0f} nm")}
                        </div>
                    </section>

                    <section class="section">
                        <div class="section-head">
                            <h2>{self.REPORT_COPY['en']['metrics_title']} / {self.REPORT_COPY['zh']['metrics_title']} / {self.REPORT_COPY['ja']['metrics_title']}</h2>
                            <p>{analysis['metrics_blurb']}</p>
                        </div>
                        <div class="metric-grid">
                            {self._metric_card('Max Altitude', f"{stats.get('max_altitude', 0):.0f} ft")}
                            {self._metric_card('Average Airspeed', f"{stats.get('avg_airspeed', 0):.0f} kts")}
                            {self._metric_card('Landing G', f"{landing.get('g_force', 0):.2f} G")}
                            {self._metric_card('Touchdown VS', f"{landing.get('vs', 0):.0f} fpm")}
                            {self._metric_card('Max Bank', f"{stats.get('max_bank_angle', 0):.0f}°")}
                        </div>
                    </section>

                    <section class="section">
                        <div class="section-head">
                            <h2>{self.REPORT_COPY['en']['analysis_title']} / {self.REPORT_COPY['zh']['analysis_title']} / {self.REPORT_COPY['ja']['analysis_title']}</h2>
                            <p>{analysis['phase_intro']}</p>
                        </div>
                        <div class="analysis-grid">
                            <div class="phase"><h3>Departure / Climb</h3><p>{analysis['phases']['departure']}</p></div>
                            <div class="phase"><h3>Cruise</h3><p>{analysis['phases']['cruise']}</p></div>
                            <div class="phase"><h3>Arrival / Landing</h3><p>{analysis['phases']['arrival']}</p></div>
                        </div>
                    </section>

                    <section class="panel summary section">
                        <div class="section-head">
                            <h2>{self.REPORT_COPY['en']['findings_title']} / {self.REPORT_COPY['zh']['findings_title']} / {self.REPORT_COPY['ja']['findings_title']}</h2>
                            <p>{analysis['finding_intro']}</p>
                        </div>
                        <ul class="finding-list">{findings_html}</ul>
                    </section>

                    <section class="section">
                        <div class="section-head">
                            <h2>{self.REPORT_COPY['en']['narrative_title']} / {self.REPORT_COPY['zh']['narrative_title']} / {self.REPORT_COPY['ja']['narrative_title']}</h2>
                            <p>English / 中文 / 日本語</p>
                        </div>
                        <div class="lang-grid">
                            <div class="language-card"><h3>English</h3><p>{analysis['summary_en']}</p></div>
                            <div class="language-card"><h3>中文</h3><p>{analysis['summary_zh']}</p></div>
                            <div class="language-card"><h3>日本語</h3><p>{analysis['summary_ja']}</p></div>
                        </div>
                    </section>

                    <section class="section">
                        <div class="section-head">
                            <h2>{self.REPORT_COPY['en']['charts_title']} / {self.REPORT_COPY['zh']['charts_title']} / {self.REPORT_COPY['ja']['charts_title']}</h2>
                            <p>{analysis['chart_intro']}</p>
                        </div>
                        <div class="media-grid">
                            <div class="media">
                                <h3>{self.REPORT_COPY['en']['screenshot_title']}</h3>
                                {screenshot_html}
                            </div>
                            <div class="chart-stack">
                                <div class="media">
                                    <h3>Altitude / Speed</h3>
                                    <img src="img/{os.path.basename(chart1_path)}" alt="profile chart">
                                </div>
                                <div class="media">
                                    <h3>G-Force / Pitch</h3>
                                    <img src="img/{os.path.basename(chart2_path)}" alt="dynamics chart">
                                </div>
                            </div>
                        </div>
                    </section>
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
                'message': f"Flight analysis report ready. Score: {score}/100",
                'report_url': f"/reports/{report_filename}",
                'timestamp': timestamp
            })
            self.last_report_url = f"/reports/{report_filename}"

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"BlackBox: Report generation failed: {e}")

    def _calculate_flight_stats(self, df=None):
        """Calculate comprehensive flight statistics."""
        if df is None and not self.flight_data:
            return {}

        data = df.to_dict('records') if df is not None else list(self.flight_data)
        
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
        avg_airspeed = sum(r['airspeed'] for r in data) / len(data) if data else 0
        
        # Flight time in air
        airborne_records = [r for r in data if not r['on_ground']]
        airborne_time = len(airborne_records) * self._record_interval
        
        # Max vertical speed
        max_vs_up = max(r['vs'] for r in data)
        max_vs_down = min(r['vs'] for r in data)
        estimated_distance_nm = self._estimate_distance_nm(data)
        
        return {
            'max_altitude': max_altitude,
            'max_airspeed': max_airspeed,
            'max_g_force': max_g,
            'min_g_force': min_g,
            'max_bank_angle': max_bank,
            'max_pitch_angle': max_pitch,
            'avg_fuel_flow': avg_fuel_flow,
            'avg_airspeed': avg_airspeed,
            'airborne_time': airborne_time,
            'max_climb_rate': max_vs_up,
            'max_descent_rate': abs(max_vs_down),
            'total_records': len(data),
            'estimated_distance_nm': estimated_distance_nm,
        }

    def _estimate_distance_nm(self, data):
        total_nm = 0.0
        for prev, curr in zip(data, data[1:]):
            lat1 = math.radians(prev.get('latitude', 0) or 0)
            lon1 = math.radians(prev.get('longitude', 0) or 0)
            lat2 = math.radians(curr.get('latitude', 0) or 0)
            lon2 = math.radians(curr.get('longitude', 0) or 0)
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))
            total_nm += 3440.065 * c
        return total_nm

    def _format_minutes(self, minutes):
        if minutes >= 60:
            hours = int(minutes // 60)
            mins = int(round(minutes % 60))
            return f"{hours}h {mins}m"
        return f"{minutes:.1f} min"

    def _metric_card(self, label, value):
        return f'<div class="metric"><div class="label">{label}</div><div class="metric-value">{value}</div></div>'

    def _build_report_analysis(self, df, stats, landing, duration):
        top_altitude = float(stats.get('max_altitude', 0) or 0)
        cruise_threshold = top_altitude * 0.75 if top_altitude > 0 else 0
        departure_df = df.head(max(1, len(df) // 3))
        cruise_df = df[df['altitude'] >= cruise_threshold] if cruise_threshold > 0 else df.iloc[0:0]
        arrival_df = df.tail(max(1, len(df) // 4))

        bounce_count = int(landing.get('bounces', 0) or 0)
        landing_g = float(landing.get('g_force', 1.0) or 1.0)
        touchdown_vs = abs(float(landing.get('vs', 0) or 0))
        max_bank = float(stats.get('max_bank_angle', 0) or 0)

        score = 100
        if landing_g > 1.35:
            score -= min(30, int((landing_g - 1.35) * 35))
        if touchdown_vs > 250:
            score -= min(18, int((touchdown_vs - 250) / 35))
        if bounce_count > 0:
            score -= min(15, bounce_count * 6)
        if max_bank > 35:
            score -= min(10, int((max_bank - 35) / 3))
        score = max(35, min(100, score))

        if score >= 92:
            grade = "Excellent"
            score_labels = ("Stable and polished", "稳定且成熟", "安定して洗練")
        elif score >= 82:
            grade = "Good"
            score_labels = ("Operationally solid", "整体可靠", "運航上は良好")
        elif score >= 70:
            grade = "Fair"
            score_labels = ("Acceptable with improvement areas", "可接受但仍需改进", "許容範囲だが改善余地あり")
        else:
            grade = "Needs Work"
            score_labels = ("Several handling issues observed", "操纵问题较多", "操縦上の課題が多い")

        dep_avg_vs = departure_df['vs'].mean() if not departure_df.empty else 0
        cruise_alt_std = cruise_df['altitude'].std() if not cruise_df.empty else 0
        cruise_alt_std = 0 if math.isnan(cruise_alt_std) else cruise_alt_std
        arrival_speed_mean = arrival_df['airspeed'].mean() if not arrival_df.empty else 0

        findings = [
            f"Estimated trip distance was {stats.get('estimated_distance_nm', 0):.0f} nm with {self._format_minutes(stats.get('airborne_time', 0) / 60.0)} airborne time.",
            f"Peak altitude reached {stats.get('max_altitude', 0):.0f} ft and average airspeed held around {stats.get('avg_airspeed', 0):.0f} kts.",
            f"Landing registered {landing_g:.2f}G at {landing.get('touchdown_speed', 0):.0f} kts with touchdown vertical speed {landing.get('vs', 0):.0f} fpm.",
        ]
        if bounce_count:
            findings.append(f"Bounce detection recorded {bounce_count} rebound event(s), indicating flare or sink-rate control needs attention.")
        if cruise_alt_std > 350:
            findings.append(f"Cruise altitude variation was noticeable at roughly {cruise_alt_std:.0f} ft standard deviation.")
        if max_bank > 35:
            findings.append(f"Maximum bank angle reached {max_bank:.0f}°, which is aggressive for a routine line flight profile.")

        headline = (
            f"Route {self.departure_airport or 'N/A'} to {self.destination_airport or 'N/A'} completed in "
            f"{self._format_minutes(duration / 60.0)} with a {grade.lower()} overall handling assessment."
        )

        summary_en = (
            f"This flight shows {grade.lower()} operational control. The climb profile averaged {dep_avg_vs:.0f} fpm, "
            f"cruise stability variation was about {cruise_alt_std:.0f} ft, and arrival energy averaged {arrival_speed_mean:.0f} kts. "
            f"The landing measured {landing_g:.2f}G with {bounce_count} bounce(s), so the main improvement area is flare timing and sink-rate management."
        )
        summary_zh = (
            f"本次飞行整体操纵评价为{'优秀' if score >= 92 else '良好' if score >= 82 else '合格' if score >= 70 else '需改进'}。"
            f"爬升阶段平均垂直速度约 {dep_avg_vs:.0f} fpm，巡航高度波动约 {cruise_alt_std:.0f} ft，进近阶段平均速度约 {arrival_speed_mean:.0f} kts。"
            f"着陆载荷为 {landing_g:.2f}G，反弹 {bounce_count} 次，主要改进点在拉平时机和下沉率控制。"
        )
        summary_ja = (
            f"今回のフライトは全体として{'優秀' if score >= 92 else '良好' if score >= 82 else '許容範囲' if score >= 70 else '改善必要'}な操縦でした。"
            f"上昇中の平均上昇率は {dep_avg_vs:.0f} fpm、巡航高度の変動は約 {cruise_alt_std:.0f} ft、進入中の平均速度は約 {arrival_speed_mean:.0f} kts です。"
            f"着陸は {landing_g:.2f}G、バウンド {bounce_count} 回で、主な改善点はフレア開始のタイミングと降下率の管理です。"
        )

        return {
            'score': score,
            'grade': grade,
            'score_label_en': score_labels[0],
            'score_label_zh': score_labels[1],
            'score_label_ja': score_labels[2],
            'headline': headline,
            'summary_en': summary_en,
            'summary_zh': summary_zh,
            'summary_ja': summary_ja,
            'metrics_blurb': 'Operational summary across altitude, energy, touchdown, and handling discipline.',
            'phase_intro': 'The flight is segmented into departure, cruise, and arrival so the report reads like an actual review, not a raw export.',
            'finding_intro': 'These findings are derived from the telemetry rather than copied from the raw log.',
            'chart_intro': 'Charts remain available, but now support the written analysis instead of replacing it.',
            'phases': {
                'departure': f"Departure performance was driven by an average climb rate of {dep_avg_vs:.0f} fpm. The aircraft built energy {'smoothly' if dep_avg_vs > 500 else 'slowly'} and stayed within a peak bank angle of {max_bank:.0f}°.",
                'cruise': f"Cruise tracking held an average airspeed of {stats.get('avg_airspeed', 0):.0f} kts. Altitude dispersion was {cruise_alt_std:.0f} ft, which {'suggests steady control' if cruise_alt_std < 250 else 'shows room for tighter altitude discipline'}.",
                'arrival': f"Arrival energy averaged {arrival_speed_mean:.0f} kts before touchdown. Landing outcome was {landing_g:.2f}G with touchdown vertical speed {landing.get('vs', 0):.0f} fpm and {bounce_count} bounce(s), pointing to {'a controlled finish' if landing_g < 1.5 and bounce_count == 0 else 'an arrival that needs smoother flare and sink-rate control'}.",
            },
            'findings': findings,
        }
    
    def clear(self):
        """Clear all recorded data (for new flight)."""
        self.flight_data.clear()
        self.landing_data = None
        self.was_on_ground = True
        self.flight_started = False
        self.flight_ended = False
        self.flight_start_time = None
        self.departure_airport = None
        self.destination_airport = None
        self.arrival_prompt_emitted = False
        self.awaiting_manual_end = False
        self.last_report_url = None

    def complete_current_flight(self):
        """Manually finalize the current flight after arrival confirmation."""
        if not self.flight_started or self.flight_ended:
            return False, "No active flight to end."
        if not self.flight_data:
            return False, "No flight data recorded yet."
        self._end_flight(self.flight_data[-1])
        return True, "Flight ended and report generation started."

    def _has_valid_sim_data(self, aircraft):
        lat = float(aircraft.get('latitude') or 0.0)
        lon = float(aircraft.get('longitude') or 0.0)
        alt = float(aircraft.get('altitude') or 0.0)
        spd = float(aircraft.get('airspeed') or 0.0)
        return abs(lat) > 0.01 or abs(lon) > 0.01 or alt > 50 or spd > 5

    def _flight_plan_airports(self):
        with context_lock:
            flight_plan = dict(shared_context.get('flight_plan', {}))
        return (
            (flight_plan.get('origin') or 'N/A').upper(),
            (flight_plan.get('destination') or 'N/A').upper(),
        )

    def _current_airport(self):
        with context_lock:
            environment = dict(shared_context.get('environment', {}))
        return (
            environment.get('current_airport')
            or environment.get('nearest_airport')
            or 'N/A'
        ).upper()

    def _should_offer_manual_end(self, aircraft):
        if not self.destination_airport or self.destination_airport == 'N/A':
            return False
        current_airport = self._current_airport()
        if current_airport != self.destination_airport:
            return False
        on_ground = bool(aircraft.get('on_ground', False))
        airspeed = float(aircraft.get('airspeed') or 0.0)
        parking_brake = bool(aircraft.get('parking_brake', False))
        combustion = bool(aircraft.get('combustion', True))
        n1 = float(aircraft.get('n1') or 0.0)
        engine_low = n1 < 10 or not combustion
        return on_ground and airspeed < 5 and (parking_brake or engine_low)
