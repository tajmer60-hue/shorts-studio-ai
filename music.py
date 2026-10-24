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

SCHEMA = 2

MOODS = ('aggressive_phonk', 'dark_phonk', 'cinematic_epic', 'dark_ambient',
         'emotional', 'electronic_synthwave', 'comedy_ironic', 'none')

NOT_CLEARED = 'NOT_CLEARED'

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


def default_catalog():
    return dict(schema=SCHEMA,
                tracks=[_reference_entry(*row) for row in PHONK_REFERENCES + CINEMA_REFERENCES],
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
    entry = dict(id='user:' + file_path.stem.lower().replace(' ', '-'), title=title or file_path.stem,
                 artist=artist or 'не указан', moods=list(moods), use='',
                 playable=True, license=str(license_id).strip(), source=str(source_url or '').strip(),
                 attribution_required=bool(attribution_required),
                 platform_restrictions=list(platform_restrictions), file=str(file_path.resolve()),
                 note='Импортирован пользователем; условия лицензии записаны им самим.')
    if probe is not None:
        entry.update(probe(file_path))
    data['tracks'].append(entry)
    save_catalog(folder, data)
    return entry


def license_ok(entry):
    return bool(entry.get('playable')) and bool(entry.get('file')) \
        and str(entry.get('license', '')).strip() not in ('', NOT_CLEARED) \
        and Path(entry['file']).is_file()


def match_tracks(catalog, mood, limit=3):
    """Playable licensed tracks of a mood, most specific first. References never match."""
    return [t for t in catalog['tracks'] if mood in t.get('moods', []) and license_ok(t)][:limit]


def style_hints(catalog, mood, limit=3):
    """Uncleared references of a mood: guidance for finding a legal alternative."""
    return [t for t in catalog['tracks'] if mood in t.get('moods', []) and not license_ok(t)][:limit]
