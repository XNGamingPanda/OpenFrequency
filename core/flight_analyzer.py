"""
Flight Analyzer - Generates sarcastic landing reviews using LLM.
"""
import json
from .context import event_bus


class FlightAnalyzer:
    """Analyzes landing data and generates roast-style reviews."""
    
    # G-force rating thresholds
    G_RATINGS = [
        (1.2, "Butter", "S"),
        (1.5, "Smooth", "A"),
        (1.8, "Firm", "B"),
        (2.2, "Hard", "C"),
        (2.8, "Very Hard", "D"),
        (float('inf'), "Crash Landing", "F")
    ]
    
    def __init__(self, config, socketio):
        self.config = config
        self.socketio = socketio
        
        # Subscribe to landing events
        event_bus.on('landing_detected', self.on_landing)
        event_bus.on('landing_review_generated', self.on_review_generated)
        
        print("FlightAnalyzer: Initialized (Gordon Ramsay mode)")
    
    def _rate_landing(self, g_force):
        """Rate landing based on G-force."""
        for threshold, desc, grade in self.G_RATINGS:
            if g_force < threshold:
                return desc, grade
        return "Unknown", "?"
    
    def _build_roast_prompt(self, landing_data):
        """Build the prompt for LLM to roast the pilot."""
        g = landing_data.get('g_force', 1.0)
        bounces = landing_data.get('bounces', 0)
        speed = landing_data.get('touchdown_speed', 0)
        hdg_stability = landing_data.get('heading_stability', 0)
        flaps = landing_data.get('flaps', 0)
        
        desc, grade = self._rate_landing(g)
        
        # Determine issues
        issues = []
        if g > 2.0:
            issues.append(f"G-Force: {g:.1f}G ({desc})")
        if bounces > 0:
            issues.append(f"Bounced: Yes ({bounces} time{'s' if bounces > 1 else ''})")
        if speed > 150:
            issues.append(f"Touchdown Speed: {speed:.0f} knots (Too Fast)")
        if hdg_stability > 5:
            issues.append(f"Heading Unstable: {hdg_stability:.1f}° drift")
        if flaps < 0.5:
            issues.append("Flaps: Not fully extended")
        
        if not issues:
            issues.append("No major issues detected - suspiciously smooth...")
        
        prompt = f"""User just landed their aircraft.

Landing Data:
- G-Force: {g:.2f}G ({desc})
- Touchdown Speed: {speed:.0f} knots
- Bounces: {bounces}
- Heading Stability: {hdg_stability:.1f}° average drift
- Flaps Position: {flaps*100:.0f}%

Issues Detected:
{chr(10).join('- ' + i for i in issues)}

Role: You are a sarcastic, mean flight instructor in the style of Gordon Ramsay. 
You love to roast bad landings but will give grudging praise for good ones.
Keep it short (2-3 sentences max), funny, and airplane-themed.
Use aviation humor and puns when possible.

Task: Write a short, funny review of this landing.

Output ONLY valid JSON: {{"score": "{grade}", "comment": "your roast here"}}"""
        
        return prompt, grade
    
    def on_landing(self, landing_data):
        """Handle landing detection and generate review."""
        print(f"FlightAnalyzer: Analyzing landing... G={landing_data.get('g_force', 0):.2f}")
        
        prompt, default_grade = self._build_roast_prompt(landing_data)
        
        # Request LLM to generate roast
        event_bus.emit('llm_request', {
            'text': prompt,
            'callback_event': 'landing_review_generated',
            'metadata': {'landing_data': landing_data, 'default_grade': default_grade}
        })
    
    def on_review_generated(self, response, metadata):
        """Handle LLM response with landing review."""
        try:
            # Parse JSON from response
            # Response might be a tuple or just text depending on how it was emitted
            # LLMClient emits (response_text, metadata)
            
            # Clean up response if needed (markdown)
            clean_text = response.replace("```json", "").replace("```", "").strip()
            review = json.loads(clean_text)
            
            score = review.get('score', metadata.get('default_grade', 'C'))
            comment = review.get('comment', 'Unable to generate review.')
        except (json.JSONDecodeError, TypeError):
            # Fallback if LLM didn't return valid JSON
            score = metadata.get('default_grade', 'C')
            comment = "The landing was... interesting. Let's just say the gear is still attached."
        
        landing_data = metadata.get('landing_data', {})
        
        result = {
            'score': score,
            'comment': comment,
            'g_force': landing_data.get('g_force', 0),
            'bounces': landing_data.get('bounces', 0),
            'speed': landing_data.get('touchdown_speed', 0)
        }
        
        print(f"FlightAnalyzer: Landing Review - {score}: {comment[:50]}...")
        
        # Emit to UI
        self.socketio.emit('landing_review', result)
        
        return result
    
    def generate_quick_review(self, landing_data):
        """Generate a quick local review without LLM (for offline mode)."""
        g = landing_data.get('g_force', 1.0)
        bounces = landing_data.get('bounces', 0)
        desc, grade = self._rate_landing(g)
        
        # Pre-made roasts
        roasts = {
            'S': [
                "Butter. Pure butter. The passengers didn't even wake up.",
                "I've seen clouds touch down harder. Impressive.",
                "Did we even land? I couldn't feel a thing."
            ],
            'A': [
                "Not bad, not bad. The coffee only spilled a little.",
                "Smooth enough that I won't file a complaint. This time.",
                "A gentle kiss with the runway. How romantic."
            ],
            'B': [
                "The landing gear will survive. Probably.",
                "Firm but fair. Like my grandmother's handshake.",
                "You landed. That's... technically an achievement."
            ],
            'C': [
                "Was that a landing or an earthquake?",
                "I've had smoother rides on a mechanical bull.",
                "The runway will need therapy after that."
            ],
            'D': [
                "Did you forget to flare or did you just give up?",
                "I hope you didn't pay full price for those tires.",
                "That wasn't a landing, that was an arrival with attitude."
            ],
            'F': [
                "Ladies and gentlemen, we have... impacted.",
                "Congratulations, you've invented a new form of landing: the controlled crash.",
                "Even the black box is filing a complaint."
            ]
        }
        
        import random
        comments = roasts.get(grade, roasts['C'])
        comment = random.choice(comments)
        
        if bounces > 0:
            bounce_comments = [
                f" Also, {bounces} bounce{'s' if bounces > 1 else ''}? Make up your mind!",
                f" And the {bounces} bounce{'s' if bounces > 1 else ''} really added to the experience.",
                f" The {bounces} bounce{'s' if bounces > 1 else ''} was a nice touch. NOT."
            ]
            comment += random.choice(bounce_comments)
        
        return {
            'score': grade,
            'comment': comment,
            'g_force': g,
            'bounces': bounces,
            'speed': landing_data.get('touchdown_speed', 0)
        }
