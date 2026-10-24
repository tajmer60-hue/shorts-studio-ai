"""Music catalog with licensing discipline.

The track lists supplied by the customer are STYLE REFERENCES collected from
2026 edit charts and playlists. They are stored with `playable: False` and
`license: NOT_CLEARED`: they inform mood matching and help the user find a
legally available alternative, but the renderer will never use them. A track
becomes playable only when the user imports an actual file and records its
license, source, attribution requirement and platform restrictions.

Default when nothing licensed is available: original dialogue audio only.
Silence is a legitimate artistic choice and is never replaced by uncleared music.
"""
from pathlib import Path
import json

from speech import atomic_json

SCHEMA = 3

MOODS = ('aggressive_phonk', 'dark_phonk', 'cinematic_epic', 'dark_ambient',
         'emotional', 'electronic_synthwave', 'comedy_ironic', 'none')

NOT_CLEARED = 'NOT_CLEARED'              # style reference only, never mixable
CLAIMED_PLATFORM = 'CLAIMED_PLATFORM'    # user asserts platform permission; unverified
VERIFIED = 'VERIFIED'                    # license source checked by the user

# Library v1 supplied by the customer on 2026-10-25 with status DEFAULT_LIBRARY_OK
# and the note "commercial use requires verification". That is a CLAIM, not a
# verified license, so these entries are stored as CLAIMED_PLATFORM: mixable only
# with a user-supplied file, only non-commercial, only on the listed platforms.
LIBRARY_V1 = [
    ('F01', 'Montagem Coma (Slowed)', 'ANDROMEDA, elysian.', ['dark_phonk'], 'Funk / Bass'),
    ('F02', 'Passo Bem Solto (Slowed)', 'ATLXS.', ['aggressive_phonk'], 'Funk / Groove'),
    ('F03', 'Los Voltaje', "Sayfalse, Yb Wasg'ood, Ariis.", ['aggressive_phonk'], 'Funk / High Energy'),
    ('F04', 'Montagem Tomada (Slowed)', 'MXZI.', ['dark_phonk'], 'Funk / Mid Tempo'),
    ('F05', 'Taka La Dentro', 'SEKIMANE, shonci, Mc Gw.', ['aggressive_phonk'], 'Funk / Latin Groove'),
    ('F06', 'Montagem Rugada (Slowed)', 'JXNDRO, sayfalse, cape.', ['aggressive_phonk'], 'Funk / Heavy Bass'),
    ('F07', 'Montagem Xonada (Slowed)', 'MXZI, Dj Samir, DJ Javi26.', ['electronic_synthwave'], 'Funk / Dance'),
    ('F08', 'No Batidão (Slowed)', 'ZXKAI, slxughter.', ['dark_ambient'], 'Funk / Brazilian Bass'),
    ('F09', 'Montagem Guerreiro (Slowed)', 'Avenxir, SUNJI.', ['cinematic_epic'], 'Funk / Epic Groove'),
    ('F10', 'Montagem Pegadora (Slowed)', 'Rubikdice, Chilx, WAA.', ['aggressive_phonk'], 'Funk / Catchy'),
    ('C01', 'After Dark', 'Mr.Kitty.', ['electronic_synthwave'], 'Cinematic / Synthwave'),
    ('C02', 'Memory Reboot', 'VØJ & Narvent.', ['dark_ambient'], 'Cinematic / Ambient'),
    ('C03', 'Little Dark Age', 'MGMT.', ['electronic_synthwave'], 'Indie Pop / Retro'),
    ('C04', 'Time Back', 'Sinny, 7vvch.', ['emotional'], 'Cinematic / Emotional'),
    ('C05', 'Pray for Me', 'The Weeknd, Kendrick Lamar.', ['cinematic_epic'], 'R&B / Hip-Hop (Epic)'),
    ('C06', 'Back to Friends', 'sombr.', ['emotional'], 'Cinematic / Chill'),
    ('C07', 'The Reason', 'Hoobastank.', ['emotional'], 'Rock / Emotional'),
    ('C08', 'Brain Stew', 'Green Day.', ['dark_phonk'], 'Punk Rock / High Energy'),
    ('C09', 'Poker Face', 'Lady Gaga.', ['comedy_ironic'], 'Pop / Dance (Iconic)'),
    ('C10', 'Dancing On My Own', 'Robyn.', ['emotional'], 'Synth-Pop / Emotional'),
]
PLATFORM_NOTE = ('Заявлено: разрешено для TikTok/Shorts. Коммерческое использование требует '
                 'проверки. Право на встраивание записи в файл не подтверждено.')

# (title, artist, mood, suggested use) - style references, NOT cleared recordings.
PHONK_REFERENCES = [
    ('Montagem Coma', 'ANDROMEDA, elysian.', 'dark_phonk', 'Тёмные повороты, угрозы, мощные появления персонажей'),
    ('Passo Bem Solto (Slowed)', 'ATLXS', 'aggressive_phonk', 'Самоуверенные герои, стильные сцены, реакции'),
    ('Los Voltaje', 'Sayfalse, Yb Wasg\'ood, Ariis', 'aggressive_phonk', 'Активные сцены, быстрые переходы, демонстрация силы'),
    ('Montagem Tomada (Slowed)', 'MXZI', 'dark_phonk', 'Напряжённые диалоги с переходом к действию'),
    ('Taka La Dentro', 'SEKIMANE, shonci, Mc Gw', 'aggressive_phonk', 'Энергичные противостояния и неожиданные развязки'),
    ('Montagem Rugada', 'JXNDRO, sayfalse, cape', 'aggressive_phonk', 'Экшен, столкновения, резкие кульминации'),
    ('Montagem Xonada', 'MXZI, Dj Samir, DJ Javi26', 'electronic_synthwave', 'Быстрые музыкальные эдиты и динамика'),
    ('No Batidão (Slowed)', 'ZXKAI, slxughter', 'dark_ambient', 'Напряжённые, атмосферные сцены'),
    ('Montagem Guerreiro (Slowed)', 'Avenxir, SUNJI', 'cinematic_epic', 'Героические появления, сила, решительные действия'),
    ('Montagem Pegadora (Slowed)', 'Rubikdice, Chilx, WAA', 'aggressive_phonk', 'Эффектные раскрытия персонажей и кульминации'),
]

CINEMA_REFERENCES = [
    ('Pray for Me', 'The Weeknd, Kendrick Lamar', 'cinematic_epic', 'Герои, решительные действия, драматичные появления'),
    ('Time Back', 'Sinny, 7vvch', 'dark_ambient', 'Мрачная атмосфера, ожидание, тайна'),
    ('Brain Stew', 'Green Day', 'dark_phonk', 'Угрожающие герои, конфликты'),
    ('Purple Rain', 'Prince', 'emotional', 'Трагические и эмоциональные сцены'),
    ('Dancing On My Own', 'Robyn', 'emotional', 'Одиночество, расставание, тяжёлый выбор'),
    ('Back to Friends', 'sombr', 'emotional', 'Отношения, воспоминания, эмоциональные эдиты'),
    ('The Reason', 'Hoobastank', 'emotional', 'Раскаяние, прощение, переживания'),
    ('Poker Face', 'Lady Gaga', 'comedy_ironic', 'Ирония, неожиданные реакции, комедия'),
    ('After Dark', 'Mr.Kitty', 'electronic_synthwave', 'Атмосферные стильные эдиты'),
    ('Memory Reboot', 'VØJ & Narvent', 'electronic_synthwave', 'Динамичные монтажные переходы'),
    ('Little Dark Age', 'MGMT', 'dark_ambient', 'Иронично-мрачные сюжетные моменты'),
]

LICENSE_NOTE = ('Список собран по подборкам EditLibrary и Spotify от 25.10.2026 как стилевой '
                'ориентир. Это не подтверждённый мировой топ и не разрешение на использование. '
                'Назначения сцен — монтажная рекомендация, а не статистика эффективности.')


def _reference_entry(title, artist, mood, use):
    return dict(id='ref:' + title.lower().replace(' ', '-'), title=title, artist=artist,
                moods=[mood], use=use, playable=False, license=NOT_CLEARED,
                attribution_required=None, platform_restrictions=[], source='style reference',
                file=None, note=LICENSE_NOTE)


def _library_v1_entry(track_id, title, artist, moods, style):
    return dict(id=track_id.lower(), title=title, artist=artist, moods=list(moods),
                use=style, playable=False, license=CLAIMED_PLATFORM,
                attribution_required=None, platform_restrictions=['TikTok', 'YouTube Shorts'],
                commercial='requires_verification', source='customer library v1 (2026-10-25)',
                file=None, license_name='DEFAULT_LIBRARY_OK (заявлено заказчиком)',
                note=PLATFORM_NOTE)


def default_catalog():
    return dict(schema=SCHEMA,
                tracks=[_reference_entry(*row) for row in PHONK_REFERENCES + CINEMA_REFERENCES]
                       + [_library_v1_entry(*row) for row in LIBRARY_V1],
                limitations=[LICENSE_NOTE,
                             'Коммерческие композиции не скачиваются и не встраиваются без прав.',
                             'Пока нет ни одного лицензированного файла, рендер идёт с оригинальной '
                             'дорожкой без музыки.'])


def catalog_path(folder):
    return Path(folder) / 'music-catalog.json'


def load_catalog(folder):
    path = catalog_path(folder)
    if not path.exists():
        data = default_catalog()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, data)
        return data
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') == 2:
        # Migrate v2 catalogs: add library v1 without losing imported tracks.
        known = {t['id'] for t in data['tracks']}
        data['tracks'] += [e for e in (_library_v1_entry(*row) for row in LIBRARY_V1)
                           if e['id'] not in known]
        data['schema'] = SCHEMA
        atomic_json(path, data)
    if data.get('schema') != SCHEMA:
        raise ValueError('Неизвестная версия музыкального каталога.')
    return data


def save_catalog(folder, data):
    atomic_json(catalog_path(folder), data)


def import_track(folder, file_path, title, artist, license_id, source_url, moods,
                 attribution_required=False, platform_restrictions=(), probe=None):
    """Register a user-owned licensed file. Nothing is downloaded here."""
    file_path = Path(file_path)
    if not file_path.is_file():
        raise ValueError('Музыкальный файл не найден: импортируется только то, что уже есть у вас.')
    if not moods or any(m not in MOODS for m in moods):
        raise ValueError('Неизвестное настроение трека.')
    if not license_id or not str(license_id).strip():
        raise ValueError('Лицензия обязательна: без неё трек нельзя использовать в рендере.')
    data = load_catalog(folder)
    # Importing your own file and naming its license is the verification act, so
    # the tier becomes VERIFIED; the human-readable name is kept separately.
    entry = dict(id='user:' + file_path.stem.lower().replace(' ', '-'), title=title or file_path.stem,
                 artist=artist or 'не указан', moods=list(moods), use='',
                 playable=True, license=VERIFIED, license_name=str(license_id).strip(),
                 source=str(source_url or '').strip(),
                 attribution_required=bool(attribution_required),
                 platform_restrictions=list(platform_restrictions), file=str(file_path.resolve()),
                 note='Импортирован пользователем; условия лицензии записаны им самим.')
    if probe is not None:
        entry.update(probe(file_path))
    data['tracks'].append(entry)
    save_catalog(folder, data)
    return entry


def license_ok(entry, commercial=False, platform=None):
    """Three licensing tiers, checked against how the clip will be published.

    A claimed platform permission covers non-commercial publishing on the listed
    platforms only; it never covers commercial use and never substitutes for a
    verified license. Without an actual audio file nothing is mixable at all.
    """
    if not entry.get('file') or not Path(entry['file']).is_file():
        return False
    license_id = str(entry.get('license', '')).strip()
    if license_id == VERIFIED:
        return True
    if license_id == CLAIMED_PLATFORM:
        if commercial:
            return False
        if platform and platform not in entry.get('platform_restrictions', []):
            return False
        return True
    return False


def match_tracks(catalog, mood, limit=3, commercial=False, platform=None):
    """Mixable tracks of a mood for the intended publication. References never match."""
    return [t for t in catalog['tracks'] if mood in t.get('moods', [])
            and license_ok(t, commercial, platform)][:limit]


def style_hints(catalog, mood, limit=3):
    """Uncleared references of a mood: guidance for finding a legal alternative."""
    return [t for t in catalog['tracks'] if mood in t.get('moods', []) and not license_ok(t)][:limit]
