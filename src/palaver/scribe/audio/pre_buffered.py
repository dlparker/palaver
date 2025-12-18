
from palaver.scribe.audio_events import (AudioEvent,
                                         AudioEventListener,
                                         AudioChunkEvent,
                                         AudioStartEvent,
                                         AudioStopEvent,
                                         AudioSpeechStartEvent,
                                         AudioSpeechStopEvent)

#
# NOTE!!!!
# This code is incomplete and untested. It is a sketch of how to
# solve the problem that can occur if the VAD timing clips off
# the begining of the speech period, the ring buffer stores
# enough earlier samples to be able to insert some to fix the
# problem. The idea would be to capture the minimum amount
# expected to be able to avoid this and then delay the forwarding
# of samples by that much. Then you can detect the VAD start and
# send some saved samples. The ring buffer will always have enough,
# and they will be the most recent.
#
# Don't remove this code, don't modify it until you have
# a good test plan.
# 
class PreBufferVAD:

    def __init__(self):
        buffer = AudioRingBuffer(max_seconds=60.0)  # Keep last 60 seconds

        # Simulate adding events (in practice, these come from your listener)
        for i in range(10):
                    chunk = AudioChunkEvent(
                source_id="test",
                data=np.random.rand(1024, 2).astype(np.float32),
                duration=0.5,  # 0.5 seconds per chunk
                sample_rate=44100,
                channels=2,
                blocksize=1024,
                datatype="float32"
            )
            buffer.add(chunk)
            time.sleep(0.1)  # Simulate delay

        # Get all events
        all_events = buffer.get_all()

        # Get recent events covering at least 2 seconds
        recent_events = buffer.get_recent(min_seconds=2.0)

        # Get concatenated samples for last 2 seconds
        samples = buffer.get_concatenated_samples(min_seconds=2.0)


class AudioRingBuffer:

    def __init__(self, max_seconds: float = 2):
        """
        Initialize the ring buffer.
        
        :param max_seconds: Maximum seconds of audio history to retain.
        """
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        self.max_seconds = max_seconds
        self.buffer: deque[AudioChunkEvent] = deque()

    def add(self, event: AudioChunkEvent) -> None:
        """
        Add a new AudioChunkEvent to the buffer and prune old entries.
        """
        self.buffer.append(event)
        self._prune()

    def _prune(self, now: float = None) -> None:
        """
        Remove events entirely older than the retention window.
        
        :param now: Optional current time (defaults to time.time()).
        """
        if now is None:
            now = time.time()
        while self.buffer and (self.buffer[0].timestamp + self.buffer[0].duration < now - self.max_seconds):
            self.buffer.popleft()

    def get_all(self) -> List[AudioChunkEvent]:
        """Return a list of all current events in the buffer (oldest to newest)."""
        return list(self.buffer)

    def get_recent(self, min_seconds: float = None) -> List[AudioChunkEvent]:
        """
        Return the most recent events covering at least min_seconds of audio (or all if None).
        Starts from the newest and works backward.
        
        :param min_seconds: Minimum seconds to cover (default: None, returns all).
        :return: List of events (oldest to newest within the subset).
        """
        if min_seconds is None:
            return self.get_all()
        
        if min_seconds <= 0:
            return []
        
        subset = []
        total_dur = 0.0
        for event in reversed(self.buffer):
            subset.append(event)
            total_dur += event.duration
            if total_dur >= min_seconds:
                break
        return subset[::-1]  # Reverse to oldest-first order

    def get_concatenated_samples(self, min_seconds: float = None) -> np.ndarray:
        """
        Optional: Concatenate the data arrays from the recent events into a single np.ndarray.
        Assumes all events have compatible shapes (same channels, dtype, etc.).
        
        :param min_seconds: Minimum seconds to cover (default: None, uses all).
        :return: Concatenated float32 array, shape (total_samples, channels).
        """
        events = self.get_recent(min_seconds)
        if not events:
            return np.empty((0, 0), dtype=np.float32)
        return np.concatenate([ev.data for ev in events], axis=0)

    @property
    def total_duration(self) -> float:
        """Total duration of audio in the buffer."""
        return sum(ev.duration for ev in self.buffer)
