import hashlib


class ATCTemplateResponder:
    """Generate stable ATC phraseology for common intents without a full LLM round-trip."""

    def __init__(self, airport_frequency_service=None):
        self.airport_frequency_service = airport_frequency_service

    def respond(self, intent, normalized_text, context, ground_summary=None, entities=None):
        atc_state = (context or {}).get("atc_state", {}) or {}
        aircraft = (context or {}).get("aircraft", {}) or {}
        flight_plan = (context or {}).get("flight_plan", {}) or {}
        environment = (context or {}).get("environment", {}) or {}
        entities = entities or {}

        callsign = aircraft.get("callsign", "Aircraft")
        role = atc_state.get("current_controller", "ATC")
        current_airport = environment.get("current_airport") or environment.get("nearest_airport") or flight_plan.get("origin") or "N/A"
        current_airport = str(current_airport).upper()

        if intent == "say_again":
            return f"{callsign}, say again."

        if intent == "with_atis":
            info = entities.get("atis")
            if "Clearance" in role:
                return f"{callsign}, information {info} received, clearance on request." if info else f"{callsign}, clearance on request."
            if "Ground" in role:
                return f"{callsign}, information {info} received, taxi request acknowledged." if info else f"{callsign}, roger, taxi request acknowledged."
            if "Tower" in role:
                return f"{callsign}, roger, continue."
            return f"{callsign}, roger."

        if intent == "readback_ack":
            return ""

        if intent == "request_ifr_clearance" and "Clearance" in role:
            destination = flight_plan.get("destination", "N/A")
            route = flight_plan.get("route", "N/A")
            sid = route.split()[0] if route and route != "N/A" else "flight planned route"
            squawk = self._squawk_for_callsign(callsign)
            runway = self._preferred_runway(current_airport)
            return f"{callsign}, cleared to {destination} via {sid}, runway {runway}, squawk {squawk}."

        if intent == "request_pushback" and "Ground" in role:
            return f"{callsign}, pushback approved, face west."

        if intent == "request_taxi" and "Ground" in role:
            runway = self._preferred_runway(current_airport)
            taxi_route = self._taxi_route_text(ground_summary or {})
            if taxi_route:
                return f"{callsign}, taxi to runway {runway} via {taxi_route}, hold short runway {runway}."
            return f"{callsign}, taxi to runway {runway}, hold short runway {runway}."

        if intent == "ready_departure":
            if "Tower" in role:
                runway = self._preferred_runway(current_airport)
                return f"{callsign}, runway {runway}, line up and wait."
            if "Ground" in role:
                tower_freq = self._frequency_for(current_airport, "Tower")
                if tower_freq:
                    return f"{callsign}, contact tower on {tower_freq}."
                return f"{callsign}, contact tower."

        if intent == "request_climb":
            cruise_alt = int(flight_plan.get("cruise_alt") or 0)
            if cruise_alt > 0:
                return f"{callsign}, climb and maintain {cruise_alt}."
            return f"{callsign}, climb and maintain flight planned altitude."

        if intent == "request_descent":
            if "Approach" in role:
                return f"{callsign}, descend and maintain 5000."
            return f"{callsign}, descend and maintain flight level 240."

        if intent == "initial_checkin":
            if "Approach" in role or "Departure" in role or "Center" in role:
                return f"{callsign}, radar contact."
            if "Tower" in role:
                runway = entities.get("runway") or self._preferred_runway(current_airport)
                return f"{callsign}, continue approach runway {runway}."

        return None

    def _preferred_runway(self, airport_ident):
        if not self.airport_frequency_service:
            return "N/A"
        runways = self.airport_frequency_service.get_preferred_runways(airport_ident, limit=1)
        return runways[0] if runways else "N/A"

    def _frequency_for(self, airport_ident, role):
        if not self.airport_frequency_service:
            return None
        freq_map = self.airport_frequency_service.get_frequency_map(airport_ident)
        return freq_map.get(role)

    def _taxi_route_text(self, ground_summary):
        route = (ground_summary or {}).get("suggested_taxi_route") or {}
        taxiways = route.get("taxiways") or []
        return ", ".join(taxiways[:8])

    def _squawk_for_callsign(self, callsign):
        digest = hashlib.sha1((callsign or "Aircraft").encode("utf-8")).hexdigest()
        value = int(digest[:6], 16) % 4096
        squawk = f"{value:04o}"[-4:]
        if squawk == "0000":
            squawk = "1201"
        return squawk
