"""Decode images and reconstruct bounded animation frames before vision input."""
from dataclasses import dataclass
import hashlib
import io
import time
import warnings

from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

from courswatch.media.acquisition import MediaUnavailable

PREPROCESS_VERSION = 'timed-animation-v2'
DESCRIPTION_SCHEMA_VERSION = '1'


@dataclass(frozen=True)
class DecodeLimits:
    max_bytes: int = 10 * 1024 * 1024
    max_pixels: int = 16_000_000
    max_total_pixels: int = 64_000_000
    max_source_frames: int = 128
    max_duration_ms: int = 15_000
    max_frames: int = 8
    max_side: int = 2048
    max_seconds: float = 2.0

    def __post_init__(self):
        if (any(not isinstance(getattr(self, key), int) or getattr(self, key) < 1
                for key in ('max_bytes', 'max_pixels', 'max_total_pixels', 'max_source_frames',
                            'max_duration_ms', 'max_frames', 'max_side')) or
                self.max_seconds <= 0):
            raise ValueError('Positive bounded decode limits are required')


@dataclass(frozen=True)
class DecodedMedia:
    sha256: str
    actual_format: str
    frames: tuple[dict, ...]
    frame_count: int
    duration_ms: int
    sampled_at_ms: tuple[int, ...]
    limitations: tuple[str, ...]


def _check_gif_trailer(data, deadline, clock):
    """Require a trailer at a block boundary, even if Pillow loaded frame zero."""
    size = len(data)
    if size < 13:
        raise MediaUnavailable('invalid_gif_incomplete')
    packed = data[10]
    offset = 13 + (3 << ((packed & 7) + 1) if packed & 128 else 0)
    while offset < size:
        if clock() > deadline:
            raise MediaUnavailable('decode_deadline')
        introducer = data[offset]
        offset += 1
        if introducer == 0x3b:
            return
        if introducer == 0x21:  # Extension label followed by sub-blocks.
            offset += 1
        elif introducer == 0x2c:  # Image descriptor, local palette, LZW size.
            if offset + 9 >= size:
                break
            packed = data[offset + 8]
            offset += 10 + (3 << ((packed & 7) + 1) if packed & 128 else 0)
        else:
            raise MediaUnavailable('invalid_gif_structure')
        while offset < size:
            if clock() > deadline:
                raise MediaUnavailable('decode_deadline')
            length = data[offset]
            offset += 1 + length
            if not length:
                break
    raise MediaUnavailable('invalid_gif_incomplete')


def decode_media(data, *, limits=None, clock=time.monotonic):
    """Sample by display duration and image changes, never assume native GIF API.

    Pillow seek/convert reconstructs GIF/WebP disposal/compositing. Work is
    bounded by bytes, per-frame and cumulative pixels, frame count and a local
    deadline. No global decompression protections are disabled.
    """
    limits = limits or DecodeLimits()
    if not isinstance(data, bytes) or not data or len(data) > limits.max_bytes:
        raise MediaUnavailable('source_too_large' if data else 'empty_source')
    started = clock()
    limitations = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            if image.format not in ('PNG', 'JPEG', 'GIF', 'WEBP'):
                raise MediaUnavailable('unsupported_format')
            actual_format = image.format
            width, height = image.size
            if width * height > limits.max_pixels or min(width, height) < 1:
                raise MediaUnavailable('pixel_limit')
            if actual_format == 'GIF':
                _check_gif_trailer(data, started + limits.max_seconds, clock)
            animated = bool(getattr(image, 'is_animated', False))
            # Metadata scan and small difference measurements, with no unbounded
            # list of decoded full-resolution frames retained in memory.
            metadata = []
            elapsed = 0
            total_pixels = 0
            previous = None
            for index in range(limits.max_source_frames):
                if clock() - started > limits.max_seconds:
                    raise MediaUnavailable('decode_deadline')
                try:
                    image.seek(index)
                except EOFError:
                    break
                if image.width * image.height > limits.max_pixels:
                    raise MediaUnavailable('pixel_limit')
                total_pixels += image.width * image.height
                if total_pixels > limits.max_total_pixels:
                    raise MediaUnavailable('decoded_pixel_limit')
                rgba = image.convert('RGBA')
                thumb = rgba.convert('RGB').resize((24, 24))
                score = (sum(ImageStat.Stat(ImageChops.difference(previous, thumb)).mean)
                         if previous is not None else 0)
                duration = max(1, int(image.info.get('duration', 100))) if animated else 0
                metadata.append((index, elapsed, duration, score))
                previous = thumb
                elapsed += min(duration, max(0, limits.max_duration_ms - elapsed))
                if elapsed >= limits.max_duration_ms:
                    limitations.append('Seule une fenêtre initiale de l’animation a été examinée.')
                    break
            else:
                limitations.append('Le nombre maximal de frames a limité la couverture de cette animation.')
            if not metadata:
                raise MediaUnavailable('empty_source')
            # First, last, timed samples, and strong changes protect brief late
            # overlays better than a first-frame-only or frame-index sampler.
            if len(metadata) <= limits.max_frames:
                selected = {row[0] for row in metadata}
            else:
                selected = ({metadata[0][0], metadata[-1][0]} if limits.max_frames > 1
                            else {metadata[-1][0]})
                slots = max(0, limits.max_frames - 4)
                for slot in range(slots):
                    at = elapsed * (slot + 1) / (slots + 1)
                    selected.add(next((row[0] for row in metadata if row[1] + row[2] > at), metadata[-1][0]))
                for row in sorted(metadata[1:], key=lambda row: row[3], reverse=True):
                    if len(selected) >= limits.max_frames:
                        break
                    selected.add(row[0])
            output = []
            last_thumb = None
            last_digest = None
            for index, at, duration, _score in metadata:
                if index not in selected:
                    continue
                if clock() - started > limits.max_seconds:
                    raise MediaUnavailable('decode_deadline')
                image.seek(index)
                frame = image.convert('RGB')
                if max(frame.size) > limits.max_side:
                    frame.thumbnail((limits.max_side, limits.max_side))
                    limitations.append('La résolution a été réduite ; les petits textes peuvent être illisibles.')
                thumb = frame.resize((24, 24))
                # Remove only consecutive near-duplicates; returning to an old
                # drawing after a change is useful animation information.
                if last_thumb is not None:
                    delta = sum(ImageStat.Stat(ImageChops.difference(last_thumb, thumb)).mean)
                    if delta < 0.5:
                        continue
                stream = io.BytesIO()
                frame.save(stream, format='PNG')
                encoded = stream.getvalue()
                digest = hashlib.sha256(encoded).hexdigest()
                if digest != last_digest:
                    output.append({'at_ms': at, 'data': encoded, 'mime_type': 'image/png'})
                    last_digest, last_thumb = digest, thumb
            if animated:
                limitations.append('Des instants fixes ont été examinés ; les mouvements intermédiaires ne sont pas tous observés.')
            image.close()
    except MediaUnavailable:
        raise
    except (UnidentifiedImageError, OSError, ValueError, EOFError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MediaUnavailable('invalid_image') from None
    return DecodedMedia(hashlib.sha256(data).hexdigest(), actual_format, tuple(output), len(metadata),
                        elapsed, tuple(frame['at_ms'] for frame in output), tuple(dict.fromkeys(limitations)))
