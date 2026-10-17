"""Sample local videos by presentation timestamp without writing extracted frames."""
import io
import math
from pathlib import Path

import av


def sample_video(path: Path, *, fps: float = 1, max_px: int = 640,
                 limit: int | None = None):
    """Yield (seconds since first frame, upright JPEG bytes) at up to fps."""
    if not math.isfinite(fps) or not 0 < fps <= 30:
        raise ValueError("Sampling rate must be greater than zero and at most 30 fps.")
    if not 128 <= max_px <= 2048 or (limit is not None and limit < 1):
        raise ValueError("Use a frame size of 128..2048 pixels and a positive frame limit.")
    if not path.is_file():
        raise ValueError("Video file does not exist.")
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("Input contains no video stream.")
        first = None
        next_sample = 0.0
        count = 0
        for frame in container.decode(video=0):
            if frame.time is None:
                raise ValueError("Video frame has no timestamp; re-export the clip before importing.")
            if first is None:
                first = frame.time
            offset = frame.time - first
            if offset + 1e-6 < next_sample:
                continue
            picture = frame.to_image()
            if frame.rotation:
                picture = picture.rotate(frame.rotation, expand=True)
            picture.thumbnail((max_px, max_px))
            output = io.BytesIO()
            picture.convert("RGB").save(output, "JPEG", quality=80)
            yield offset, output.getvalue()
            count += 1
            if limit is not None and count >= limit:
                return
            # Advance the sampling grid, including across gaps/variable frame rates.
            next_sample = (math.floor((offset + 1e-6) * fps) + 1) / fps
        if not count:
            raise ValueError("Video contains no decodable frames.")
