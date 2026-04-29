import json
import datetime
import threading
from typing import Dict, Any, List

class TraceManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(TraceManager, cls).__new__(cls)
                    cls._instance.traces = []
                    cls._instance.current_trace_id = None
        return cls._instance
    
    def start_trace(self, trace_id: str = None):
        with self._lock:
            self.traces = [] # Reset for new run if needed, or append? Let's reset for this singleton scope.
            if not trace_id:
                trace_id = datetime.datetime.now().isoformat()
            self.current_trace_id = trace_id
            print(f"--- TRACE STARTED: {self.current_trace_id} ---")

    def log_step(self, agent_name: str, input_state: Dict[str, Any], output_diff: Dict[str, Any]):
        """
        Logs a step in the agent workflow.
        
        Args:
            agent_name: Name of the agent executing.
            input_state: The state *before* the agent ran (or at start).
            output_diff: What the agent returned/changed.
        """
        step_record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "agent": agent_name,
            "trace_id": self.current_trace_id,
            "inputs": self._sanitize(input_state),
            "output_delta": self._sanitize(output_diff)
        }
        
        with self._lock:
            self.traces.append(step_record)
            # Optional: Real-time printing or logging
            # print(f"[{agent_name}] Step logged.")

    def _sanitize(self, data: Any) -> Any:
        """
        Helper to make data JSON serializable and truncate huge strings.
        """
        if isinstance(data, dict):
            return {k: self._sanitize(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize(v) for v in data]
        elif hasattr(data, "dict"): # Pydantic
            return self._sanitize(data.dict())
        elif isinstance(data, str):
            if len(data) > 1000:
                return data[:1000] + "...(truncated)"
            return data
        elif hasattr(data, "content") and isinstance(data.content, str): # Langchain Messages
            return data.content[:500] + "..."
        else:
            try:
                # Basic types check
                json.dumps(data)
                return data
            except:
                return str(data)

    def get_traces(self) -> List[Dict]:
        with self._lock:
            return list(self.traces)

    def save_traces(self, filename: str):
        with self._lock:
            try:
                with open(filename, 'w') as f:
                    json.dump(self.traces, f, indent=2)
                print(f"Trace saved to {filename}")
            except Exception as e:
                print(f"Failed to save trace: {e}")

# Global Accessor
tracer = TraceManager()
