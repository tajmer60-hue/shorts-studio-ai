"""Channel profiles: every editorial preference lives in one versioned object.

A profile is the single source of truth for mode, pacing, music taste, subtitle
style, censor rules and export parameters, so two channels of the same owner can
cut the same episode differently. `profile_to_settings` maps a profile onto the
settings dict the pipeline already understands, so nothing downstream changes.
"""
from pathlib import Path
import json

from speech import atomic_json

SCHEMA = 1

SUBTITLE_STYLES = ('bold_bottom', 'karaoke_center', 'minimal_top', 'none')
PLATFORMS = ('YouTube Shorts', 'TikTok', 'Reels')
EXPORT_PRESETS = {'shorts_1080': dict(width=1080, height=1920, fps=30, crf=20),
                  'shorts_720': dict(width=720, height=1280, fps=30, crf=21)}


def default_profiles():
    return dict(schema=SCHEMA, profiles=[
        dict(id='serial-story', commercial=False, platform='YouTube Shorts', name='Serial Story', mode='serial',
             duration_min=45.0, duration_max=90.0, intensity='balanced',
             aggressiveness='normal', music_moods=['dark_ambient', 'emotional', 'none'],
             music_level_db=-18.0, sfx_intensity='cinematic', subtitle_style='bold_bottom',
             subtitle_position='bottom_safe', censor='mute_approved',
             export='shorts_1080', hook_min_score=25,
             title_template='{series} • Часть {part}: {title}',
             description_template='Продолжение в следующей части. Смонтировано из серии локально.'),
        dict(id='phonk-viral', commercial=False, platform='YouTube Shorts', name='Phonk Viral Edit', mode='viral',
             duration_min=20.0, duration_max=45.0, intensity='aggressive',
             aggressiveness='fast', music_moods=['aggressive_phonk', 'dark_phonk', 'electronic_synthwave'],
             music_level_db=-12.0, sfx_intensity='aggressive', subtitle_style='karaoke_center',
             subtitle_position='center_safe', censor='mute_approved',
             export='shorts_1080', hook_min_score=40,
             title_template='{title} #shorts',
             description_template='Динамичный эдит. Музыка добавлена только из лицензированной библиотеки.'),
        dict(id='cinematic-drama', commercial=False, platform='YouTube Shorts', name='Cinematic Drama', mode='viral',
             duration_min=30.0, duration_max=75.0, intensity='cinematic',
             aggressiveness='soft', music_moods=['emotional', 'cinematic_epic', 'dark_ambient'],
             music_level_db=-16.0, sfx_intensity='cinematic', subtitle_style='minimal_top',
             subtitle_position='bottom_safe', censor='mute_approved',
             export='shorts_1080', hook_min_score=30,
             title_template='{title}',
             description_template='Эмоциональная сцена без спойлеров к продолжению.'),
    ])


def profiles_path(folder):
    return Path(folder) / 'channel-profiles.json'


def load_profiles(folder):
    path = profiles_path(folder)
    if not path.exists():
        data = default_profiles()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, data)
        return data
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != SCHEMA:
        raise ValueError('Неизвестная версия профилей каналов.')
    for profile in data['profiles']:
        validate(profile)
    return data


def save_profiles(folder, data):
    for profile in data['profiles']:
        validate(profile)
    atomic_json(profiles_path(folder), data)


def validate(profile):
    from beat_sync import INTENSITY
    from modes import AGGRESSIVENESS
    required = ('id', 'name', 'mode', 'duration_min', 'duration_max', 'intensity')
    missing = [field for field in required if field not in profile]
    if missing:
        raise ValueError('В профиле не хватает полей: ' + ', '.join(missing))
    if profile['mode'] not in ('serial', 'viral'):
        raise ValueError('Неизвестный режим профиля: ' + str(profile['mode']))
    if not 0 < profile['duration_min'] <= profile['duration_max'] <= 180:
        raise ValueError('Некорректный диапазон длительности профиля.')
    if profile['intensity'] not in INTENSITY:
        raise ValueError('Неизвестная интенсивность: ' + str(profile['intensity']))
    if profile.get('aggressiveness', 'normal') not in AGGRESSIVENESS:
        raise ValueError('Неизвестная агрессивность: ' + str(profile['aggressiveness']))
    if profile.get('subtitle_style', 'bold_bottom') not in SUBTITLE_STYLES:
        raise ValueError('Неизвестный стиль субтитров: ' + str(profile['subtitle_style']))
    if profile.get('export', 'shorts_1080') not in EXPORT_PRESETS:
        raise ValueError('Неизвестный пресет экспорта: ' + str(profile['export']))
    if profile.get('platform', 'YouTube Shorts') not in PLATFORMS:
        raise ValueError('Неизвестная платформа публикации: ' + str(profile['platform']))
    if not isinstance(profile.get('commercial', False), bool):
        raise ValueError('Признак коммерческой публикации должен быть булевым.')
    return profile


def get_profile(folder, profile_id):
    data = load_profiles(folder)
    for profile in data['profiles']:
        if profile['id'] == profile_id:
            return profile
    raise ValueError('Профиль канала не найден: ' + str(profile_id))


def profile_to_settings(profile):
    """Map a profile onto the pipeline settings dict; keep unknown keys out."""
    from beat_sync import INTENSITY
    return dict(mode=profile['mode'],
                duration_min=float(profile['duration_min']),
                duration_max=float(profile['duration_max']),
                aggressiveness=profile.get('aggressiveness', 'normal'),
                intensity=profile.get('intensity', 'balanced'),
                music_moods=list(profile.get('music_moods', [])),
                music_level_db=float(profile.get('music_level_db', INTENSITY[profile.get('intensity', 'balanced')]['music_level_db'])),
                sfx_intensity=profile.get('sfx_intensity', 'balanced'),
                subtitle_style=profile.get('subtitle_style', 'bold_bottom'),
                subtitle_position=profile.get('subtitle_position', 'bottom_safe'),
                censor=profile.get('censor', 'mute_approved'),
                export=profile.get('export', 'shorts_1080'),
                hook_min_score=int(profile.get('hook_min_score', 25)),
                commercial=bool(profile.get('commercial', False)),
                platform=profile.get('platform', 'YouTube Shorts'),
                title_template=profile.get('title_template', '{title}'),
                description_template=profile.get('description_template', ''))
