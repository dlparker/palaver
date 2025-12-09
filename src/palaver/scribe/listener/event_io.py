import numpy as np
from pathlib import Path
import json
from io import BytesIO


from palaver.scribe.audio_events import (AudioEvent,
                                       AudioErrorEvent,
                                       AudioChunkEvent,
                                       AudioStartEvent,
                                       AudioStopEvent
                                       AudioSpeechStartEvent,
                                       AudioSpeechStopEvent
                                       )


def save_event(event: AudioChunkEvent, path: Path):
    # Save all fields; NumPy handles the array natively
    np.savez(
        path,
        event_type=event.event_type.value,
        source_id=event.source_id,
        timestamp=event.timestamp,
        event_id=event.event_id,
        data=event.data,          # NumPy saves shape/dtype
        duration=event.duration,
        sample_rate=event.sample_rate,
        channels=event.channels,
        blocksize=event.blocksize,
        datatype=event.datatype,
        in_speech=event.in_speech,
        meta_data=event.meta_data,  # Will pickle if complex; keep simple or handle separately
    )

def load_event(path: Path) -> AudioChunkEvent:
    loaded = np.load(path)
    return AudioChunkEvent(
        event_type=AudioEventType(loaded["event_type"]),
        source_id=loaded["source_id"].item(),
        timestamp=loaded["timestamp"].item(),
        event_id=loaded["event_id"].item(),
        data=loaded["data"],  # Already a proper ndarray
        duration=loaded["duration"].item(),
        sample_rate=int(loaded["sample_rate"].item()),
        channels=int(loaded["channels"].item()),
        blocksize=int(loaded["blocksize"].item()),
        datatype=loaded["datatype"].item(),
        in_speech=bool(loaded["in_speech"].item()),
        meta_data=loaded.get("meta_data", None),
    )

def serialize_event(event: AudioChunkEvent) -> bytes:
    # Serialize NumPy array to .npy binary bytes
    buffer = BytesIO()
    np.save(buffer, event.data)  # Includes shape/dtype
    array_bytes = buffer.getvalue()

    # Scalar metadata as JSON
    meta = {
        "event_type": event.event_type.value,
        "source_id": event.source_id,
        "timestamp": event.timestamp,
        "event_id": event.event_id,
        "duration": event.duration,
        "sample_rate": event.sample_rate,
        "channels": event.channels,
        "blocksize": event.blocksize,
        "datatype": event.datatype,
        "in_speech": event.in_speech,
        "meta_data": event.meta_data,  # Assuming JSON-serializable
    }
    meta_bytes = json.dumps(meta).encode('utf-8')

    # Combine: length-prefixed to allow easy parsing
    return len(meta_bytes).to_bytes(4, 'big') + meta_bytes + array_bytes

def deserialize_event(payload: bytes) -> AudioChunkEvent:
    meta_len = int.from_bytes(payload[:4], 'big')
    meta_bytes = payload[4:4 + meta_len]
    array_bytes = payload[4 + meta_len:]

    meta = json.loads(meta_bytes.decode('utf-8'))

    buffer = BytesIO(array_bytes)
    data = np.load(buffer)

    return AudioChunkEvent(
        event_type=AudioEventType(meta["event_type"]),
        source_id=meta["source_id"],
        data=data,
        timestamp=meta["timestamp"],
        event_id=meta["event_id"],
        duration=meta["duration"],
        sample_rate=meta["sample_rate"],
        channels=meta["channels"],
        blocksize=meta["blocksize"],
        datatype=meta["datatype"],
        in_speech=meta["in_speech"],
        meta_data=meta.get("meta_data"),
    )
