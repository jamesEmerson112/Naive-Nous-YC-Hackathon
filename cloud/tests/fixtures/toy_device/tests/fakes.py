class FakeCommand:
    """Records the last gripper command instead of driving a servo."""

    def __init__(self):
        self.gripper = "unknown"

    def hold(self):
        self.gripper = "closed"
        return self

    def open_gripper(self):
        self.gripper = "open"
        return self

    def idle(self):
        self.gripper = "idle"
        return self
