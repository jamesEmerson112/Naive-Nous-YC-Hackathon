"""Fake SO-101 follower: the calls the app uses from LeRobot's SO101Follower.

Faults come from <sim_dir>/arm_faults.json:
  {"absent": [6]}            connect() fails like LeRobot does when a servo doesn't answer
  {"fail_reads": [40, 41]}   those get_observation() calls raise a bus read error
Every command is appended to arm_commands.jsonl; torque and park state live in arm_state.json.
"""
import json
from pathlib import Path

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
IDS = [i + 1 for i in range(len(JOINTS))]


class FakeArm:
    def __init__(self, sim_dir, port="/dev/ttyACM0"):
        self.dir = Path(sim_dir)
        self.port = port
        faults = self.dir / "arm_faults.json"
        self.faults = json.loads(faults.read_text()) if faults.exists() and faults.read_text().strip() else {}
        self.reads = 0
        self.pos = {j: 0.0 for j in JOINTS}
        self.log = None

    def connect(self):
        absent = self.faults.get("absent", [])
        if absent:
            ids = ", ".join(str(i) for i in absent)
            raise RuntimeError(
                f"FeetechMotorsBus motor check failed on port '{self.port}':\n\n"
                f"Missing motor IDs: {ids} (expected model: 777)")
        self.log = open(self.dir / "arm_commands.jsonl", "a")
        self._state(torque=True, parked=False)

    def get_observation(self):
        self.reads += 1
        if self.reads in set(self.faults.get("fail_reads", [])):
            raise ConnectionError(
                f"Failed to sync read 'Present_Position' on ids={IDS} after 1 tries. "
                "[TxRxResult] There is no status packet!")
        return {f"{j}.pos": v for j, v in self.pos.items()}

    def send_action(self, action):
        for key, value in action.items():
            joint = key.removesuffix(".pos")
            if joint in self.pos:
                self.pos[joint] = float(value)
        self.log.write(json.dumps(action) + "\n")
        self.log.flush()
        return action

    def park(self):
        self._state(torque=False, parked=True)

    def disconnect(self):
        if self.log:
            self.log.close()
            self.log = None

    def _state(self, **changes):
        path = self.dir / "arm_state.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        state.update(changes)
        path.write_text(json.dumps(state))
