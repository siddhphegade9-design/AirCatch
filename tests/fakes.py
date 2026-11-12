"""Stand-ins for the network service and the camera worker, so UI logic can be tested alone."""
from PySide6.QtCore import QObject, Signal


class FakeService:
    def __init__(self, emit, **kwargs):
        self.emit = emit
        self.kwargs = kwargs
        self.calls = []
        self.stopped = 0
        self.pairing_uri = "aircatch://pair?v=1&h=127.0.0.1&p=5001&f=ab&t=cd&n=Fake"

    def start(self): pass
    def stop(self, *a): self.stopped += 1
    def open_pairing(self): self.calls.append(("open_pairing",)); return self.pairing_uri
    def close_pairing(self): self.calls.append(("close_pairing",))
    def answer_pairing(self, ok): self.calls.append(("answer_pairing", ok))
    def answer_offer(self, tid, ok): self.calls.append(("answer_offer", tid, ok))
    def join_pairing(self, text): self.calls.append(("join", text))
    def send(self, device_id, path, thumb, manual): self.calls.append(("send", device_id, path, thumb, manual))
    def cancel_send(self): self.calls.append(("cancel_send",))
    def unpair(self, device_id): self.calls.append(("unpair", device_id)); return True

    def sent(self):
        return [c for c in self.calls if c[0] == "send"]

    def answers(self):
        return [c for c in self.calls if c[0] == "answer_offer"]


class FakeWorker(QObject):
    live = Signal()
    frameReady = Signal(object)
    statusReady = Signal(object)
    gestures = Signal(object)
    failed = Signal(str)

    def __init__(self, config, mode, camera_index=0):
        super().__init__()
        self.mode = mode
        self.started = False
        self.stopped = False

    def start(self): self.started = True
    def stop(self): self.stopped = True
    def set_mode(self, mode): self.mode = mode
