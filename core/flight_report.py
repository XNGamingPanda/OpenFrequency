"""
Flight Report Generator - Creates HTML reports with charts and screenshots.
"""
import os
import json
from datetime import datetime
from .context import event_bus


class FlightReport:
    """Generates comprehensive flight reports with stats and charts."""
    
    def __init__(self, config, socketio, black_box):
        self.config = config
        self.socketio = socketio
        self.black_box = black_box
        
        # Report directory
        self.report_dir = "data/reports"
        self.img_dir = os.path.join(self.report_dir, "img")
        os.makedirs(self.report_dir, exist_ok=True)
        os.makedirs(self.img_dir, exist_ok=True)
        
        # Subscribe to flight end event
        event_bus.on('flight_ended', self.on_flight_ended)
        
        # Latest report path
        self.latest_report = None
        
        print("FlightReport: Initialized")
    
    def on_flight_ended(self, data):
        """Handle flight end and generate report."""
        print("FlightReport: Generating flight report...")
        
        try:
            # Capture screenshot
            screenshot_path = self._capture_screenshot()
            
            # Generate HTML report
            report_path = self._generate_html_report(data, screenshot_path)
            
            self.latest_report = report_path
            
            # Notify UI
            self.socketio.emit('flight_report_ready', {
                'message': '🎉 航程结束！点击查看本次飞行详单',
                'report_url': f'/report/latest'
            })
            
            print(f"FlightReport: Report generated at {report_path}")
            
        except Exception as e:
            print(f"FlightReport: Error generating report: {e}")
    
    def _capture_screenshot(self):
        """Capture MSFS window screenshot."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"flight_{timestamp}.png"
        filepath = os.path.join(self.img_dir, filename)
        
        try:
            import pyautogui
            screenshot = pyautogui.screenshot()
            screenshot.save(filepath)
            print(f"FlightReport: Screenshot saved to {filepath}")
            return filepath
        except ImportError:
            print("FlightReport: pyautogui not installed, skipping screenshot")
            return None
        except Exception as e:
            print(f"FlightReport: Screenshot failed: {e}")
            return None
    
    def _generate_html_report(self, flight_data, screenshot_path=None):
        """Generate static HTML report."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp}.html"
        filepath = os.path.join(self.report_dir, filename)
        
        stats = flight_data.get('stats', {})
        landing = flight_data.get('landing_data', {})
        duration = flight_data.get('duration', 0)
        
        # Format duration
        duration_min = int(duration // 60)
        duration_sec = int(duration % 60)
        
        # G-force rating
        g_force = landing.get('g_force', 1.0) if landing else 1.0
        g_rating = self._get_g_rating(g_force)
        
        # Screenshot HTML
        screenshot_html = ""
        if screenshot_path and os.path.exists(screenshot_path):
            rel_path = os.path.basename(screenshot_path)
            screenshot_html = f'<img src="img/{rel_path}" alt="Flight Screenshot" class="screenshot">'
        
        html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <title>飞行报告 - {timestamp}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #fff;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            font-size: 32px;
            margin-bottom: 30px;
            background: linear-gradient(90deg, #3b82f6, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .grade-badge {{
            display: flex;
            justify-content: center;
            margin-bottom: 30px;
        }}
        .grade {{
            font-size: 72px;
            font-weight: bold;
            width: 120px;
            height: 120px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 20px;
            background: {self._get_grade_color(g_rating[1])};
            box-shadow: 0 10px 40px rgba(0,0,0,0.3);
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .stat-value {{
            font-size: 28px;
            font-weight: bold;
            color: #3b82f6;
        }}
        .stat-label {{
            font-size: 14px;
            color: #888;
            margin-top: 5px;
        }}
        .section {{
            background: rgba(255,255,255,0.03);
            border-radius: 16px;
            padding: 25px;
            margin-bottom: 20px;
        }}
        .section h2 {{
            font-size: 18px;
            margin-bottom: 15px;
            color: #8b5cf6;
        }}
        .landing-details {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
        }}
        .detail {{
            text-align: center;
        }}
        .detail-value {{
            font-size: 24px;
            font-weight: bold;
        }}
        .detail-label {{
            font-size: 12px;
            color: #666;
        }}
        .screenshot {{
            width: 100%;
            border-radius: 12px;
            margin-top: 15px;
        }}
        .footer {{
            text-align: center;
            margin-top: 30px;
            color: #666;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>✈️ 飞行报告</h1>
        
        <div class="grade-badge">
            <div class="grade">{g_rating[1]}</div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{duration_min}:{duration_sec:02d}</div>
                <div class="stat-label">飞行时长</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('max_altitude', 0):,.0f} ft</div>
                <div class="stat-label">最高高度</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('max_airspeed', 0):.0f} kt</div>
                <div class="stat-label">最大速度</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('avg_fuel_flow', 0):.1f} GPH</div>
                <div class="stat-label">平均油耗</div>
            </div>
        </div>
        
        <div class="section">
            <h2>🛬 着陆数据</h2>
            <div class="landing-details">
                <div class="detail">
                    <div class="detail-value" style="color: {self._get_g_color(g_force)}">{g_force:.2f}G</div>
                    <div class="detail-label">着陆G值 ({g_rating[0]})</div>
                </div>
                <div class="detail">
                    <div class="detail-value">{landing.get('bounces', 0)}</div>
                    <div class="detail-label">弹跳次数</div>
                </div>
                <div class="detail">
                    <div class="detail-value">{landing.get('touchdown_speed', 0):.0f} kt</div>
                    <div class="detail-label">接地速度</div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h2>📊 飞行极值</h2>
            <div class="landing-details">
                <div class="detail">
                    <div class="detail-value">{stats.get('max_g_force', 0):.2f}G</div>
                    <div class="detail-label">最大正G</div>
                </div>
                <div class="detail">
                    <div class="detail-value">{stats.get('max_bank_angle', 0):.1f}°</div>
                    <div class="detail-label">最大坡度</div>
                </div>
                <div class="detail">
                    <div class="detail-value">{stats.get('max_climb_rate', 0):.0f} fpm</div>
                    <div class="detail-label">最大爬升率</div>
                </div>
            </div>
        </div>
        
        {f'<div class="section"><h2>📸 飞行截图</h2>{screenshot_html}</div>' if screenshot_html else ''}
        
        <div class="footer">
            OpenFrequency 飞行报告 | 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        </div>
    </div>
</body>
</html>"""
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return filepath
    
    def _get_g_rating(self, g_force):
        """Get G-force rating description and grade."""
        if g_force < 1.2:
            return ("Butter 黄油着陆", "S")
        elif g_force < 1.5:
            return ("Smooth 平滑", "A")
        elif g_force < 1.8:
            return ("Firm 结实", "B")
        elif g_force < 2.2:
            return ("Hard 重着陆", "C")
        elif g_force < 2.8:
            return ("Very Hard 非常重", "D")
        else:
            return ("Crash 砸机", "F")
    
    def _get_grade_color(self, grade):
        """Get background color for grade badge."""
        colors = {
            'S': 'linear-gradient(135deg, #ffd700, #ff8c00)',
            'A': 'linear-gradient(135deg, #22c55e, #16a34a)',
            'B': 'linear-gradient(135deg, #3b82f6, #2563eb)',
            'C': 'linear-gradient(135deg, #f97316, #ea580c)',
            'D': 'linear-gradient(135deg, #ef4444, #dc2626)',
            'F': 'linear-gradient(135deg, #991b1b, #7f1d1d)'
        }
        return colors.get(grade, colors['C'])
    
    def _get_g_color(self, g_force):
        """Get color for G-force display."""
        if g_force < 1.2:
            return '#22c55e'
        elif g_force < 1.5:
            return '#84cc16'
        elif g_force < 1.8:
            return '#eab308'
        elif g_force < 2.2:
            return '#f97316'
        else:
            return '#ef4444'
    
    def get_latest_report(self):
        """Return path to latest report."""
        return self.latest_report
