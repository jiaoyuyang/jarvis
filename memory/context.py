class ContextManager:
    def __init__(self, max_items=40):
        self.max_items = max_items
        self.sessions = {}

    def add(self, session_id, role, text):
        if session_id not in self.sessions:
            self.sessions[session_id] = []

        self.sessions[session_id].append({
            "role": role,
            "text": text or ""
        })

        self.sessions[session_id] = self.sessions[session_id][-self.max_items:]

    def get(self, session_id):
        return self.sessions.get(session_id, [])
