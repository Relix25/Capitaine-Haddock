"""Turn observed Discord media into distinct, attributed occurrence descriptors.

This module does not fetch anything. A URL, filename or emoji name is metadata,
never evidence of the contents of an image.
"""
import re
from urllib.parse import parse_qsl, urlsplit

from courswatch.conversation.contracts import MediaDescriptor


CUSTOM_EMOJI = re.compile(r'<(a?):([A-Za-z0-9_]{1,64}):(\d{2,30})>')
PAGE_LINK = re.compile(r'https://(?:www\.)?(?:tenor\.com|giphy\.com)/[^\s<>]+', re.I)
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
_DISCORD_MEDIA_HOSTS = frozenset(('cdn.discordapp.com', 'media.discordapp.net'))
_DISCORD_SIGNATURE = frozenset(('ex', 'is', 'hm'))


def media_identity(descriptor):
    """Identify both the occurrence slot and the media referenced by that slot."""
    source = descriptor.url
    try:
        url = urlsplit(source)
    except ValueError:
        url = None
    if url and url.hostname in _DISCORD_MEDIA_HOSTS and url.path.startswith(('/attachments/', '/ephemeral-attachments/')):
        # Discord rotates signed URL parameters without replacing the file. Keep
        # all representation parameters and the full path, even for embeds.
        query = tuple((key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
                      if key not in _DISCORD_SIGNATURE)
        source = (url.scheme, url.netloc, url.path, query, url.fragment)
    return (descriptor.media_id, descriptor.position, descriptor.source_kind,
            descriptor.kind, descriptor.animated, source)


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _kind(url, content_type='', animated=False):
    try:
        path = urlsplit(str(url)).path.lower()
    except ValueError:
        return 'unsupported'
    if animated or path.endswith('.gif') or content_type == 'image/gif':
        return 'animation'
    if content_type.startswith('image/') or path.endswith(IMAGE_SUFFIXES):
        return 'image'
    return 'unsupported'


def normalize_media(message):
    """Normalize attachments, embed images and custom emojis without side effects.

    Caller must first authorize guild/channel/role. Repeated emoji occurrences
    deliberately have different IDs; they can still share intrinsic processing.
    """
    result = []
    for index, attachment in enumerate(getattr(message, 'attachments', ()) or ()):
        url = str(_get(attachment, 'url', '') or '')
        content_type = str(_get(attachment, 'content_type', '') or '')
        filename = str(_get(attachment, 'filename', '') or '')
        result.append(MediaDescriptor(
            media_id='attachment:' + str(_get(attachment, 'id', index)),
            kind=_kind(url, content_type), url=url, position=index,
            content_type=content_type, filename=filename,
            size_bytes=_get(attachment, 'size'),
            animated=url.lower().split('?', 1)[0].endswith('.gif')))
    for index, embed in enumerate(getattr(message, 'embeds', ()) or ()):
        # A video thumbnail is not the animation. Keep video unsupported until
        # an explicit decoder and source adapter exist.
        video = _get(embed, 'video')
        video_url = str(_get(video, 'url', '') or '')
        if video_url:
            result.append(MediaDescriptor('embed:' + str(index) + ':video', 'video', video_url,
                                          'discord_embed', len(result)))
        image = _get(embed, 'image')
        image_url = str(_get(image, 'url', '') or '')
        if image_url:
            result.append(MediaDescriptor('embed:' + str(index) + ':image', _kind(image_url), image_url,
                                          'discord_embed', len(result)))
        elif not video_url:
            thumbnail = _get(embed, 'thumbnail')
            thumbnail_url = str(_get(thumbnail, 'url', '') or '')
            if thumbnail_url:
                result.append(MediaDescriptor('embed:' + str(index) + ':thumbnail', 'image', thumbnail_url,
                                              'discord_embed_thumbnail', len(result)))
    content = str(getattr(message, 'content', '') or '')
    for index, match in enumerate(CUSTOM_EMOJI.finditer(content)):
        animated, name, identity = match.groups()
        # Discord's WebP endpoint preserves animated emoji with animated=true.
        # The source ID and source bytes, rather than a mutable name, key reuse.
        url = 'https://cdn.discordapp.com/emojis/' + identity + '.webp?size=128&quality=lossless'
        if animated:
            url += '&animated=true'
        result.append(MediaDescriptor('emoji:' + identity + ':' + str(index),
                                      'animation' if animated else 'image', url,
                                      'discord_custom_emoji', len(result), 'image/webp',
                                      name, emoji_id=identity, animated=bool(animated)))
    for index, match in enumerate(PAGE_LINK.finditer(content)):
        # A known link page still isn't an image. The explicit unsupported
        # descriptor makes the missing source adapter visible without crawling.
        result.append(MediaDescriptor('page:' + str(index), 'link', match.group(),
                                      'recognized_page_link', len(result)))
    return tuple(result)
