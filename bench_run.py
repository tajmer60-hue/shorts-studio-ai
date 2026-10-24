"""Run the model comparison on the synthetic window. Free models only.

Guards:
- every candidate id must end with ':free', otherwise the run aborts; paid calls
  are impossible on this key anyway (credit limit is 0), but we refuse early;
- the same packet goes to every candidate, only the model changes;
- responses are cached, so re-running the script costs zero extra requests;
- nothing but the authored synthetic dialogue is sent.
"""
from pathlib import Path
import json
import sys

from bench_dialogue import ground_truth_hits, packet, transcript
from benchmark import evaluate, markdown_table
from cloud_planner import OpenRouterPlanner
from modes import MODE_PROMPTS
from providers import FreeQuota

CANDIDATES = [
    'nvidia/nemotron-3.5-super-vl:free',
    'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
    'google/gemma-4-31b-it:free',
    'thinkingmachines/inkling:free',
    'nvidia/nemotron-3.5-content-safety:free',
]

COLUMNS = [('model', 'Модель', 46), ('ok', 'Ответ', 6), ('seconds', 'Сек', 7),
           ('beats_found', 'Сюжет', 7), ('filler_ratio', 'Мусор', 7),
           ('word_cuts', 'Рез.слов', 9), ('overlaps', 'Повт.', 6),
           ('out_of_window', 'Вне окна', 9), ('coverage', 'Покрыт.', 8),
           ('candidates', 'Клип', 5), ('median_clip_seconds', 'Мед.сек', 8)]


def table(rows):
    header = '| ' + ' | '.join(title for _k, title, _w in COLUMNS) + ' |'
    line = '|' + '|'.join('---' for _ in COLUMNS) + '|'
    body = []
    for row in rows:
        cells = []
        for key, _t, _w in COLUMNS:
            value = row.get(key)
            if isinstance(value, bool):
                value = 'да' if value else 'НЕТ'
            elif isinstance(value, float):
                value = f'{value:.3f}'.rstrip('0').rstrip('.')
            cells.append('' if value is None else str(value))
        body.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join([header, line] + body)


class ModePlanner:
    """Binds a mode prompt so evaluate() exercises the real editorial task."""

    PROVIDER = 'openrouter'

    def __init__(self, inner, prompt):
        self.inner = inner
        self.prompt = prompt
        self.ledger = inner.ledger
        self.quota = getattr(inner, 'quota', None)

    def propose(self, packet, frames=(), cancelled=None):
        return self.inner.propose(packet, frames, cancelled, task_prompt=self.prompt)


def main():
    root = Path(__file__).resolve().parent
    env = root / '.env'
    if not env.exists():
        print('Нет .env с OPENROUTER_KEY. Создай файл и положи туда ключ одной строкой.')
        return 1
    key = [line.split('=', 1)[1].strip() for line in env.read_text(encoding='utf-8').splitlines()
           if line.startswith('OPENROUTER_KEY=')][0]
    for model in CANDIDATES:
        if not model.endswith(':free'):
            print('ОТКАЗ: кандидат не бесплатный: ' + model)
            return 1
    quota = FreeQuota(key)
    before = quota.snapshot(force=True)
    print(f"Бесплатных запросов до прогона: осталось {int(before['remaining'] or 0)} "
          f"из {int(before['limit'] or 0)}.")
    needed = len(CANDIDATES)
    if (before.get('remaining') or 0) < needed:
        print(f'Не хватает бесплатных запросов: нужно {needed}. Прогон отменён, ничего не отправлено.')
        return 1

    folder = root / 'bench' / 'cache'
    folder.mkdir(parents=True, exist_ok=True)
    words = transcript()['words']
    pkt = packet()
    rows = []
    for model in CANDIDATES:
        planner = ModePlanner(OpenRouterPlanner(key, folder, 0, True, model=model,
                                                training_allowed=True, quota=quota),
                              MODE_PROMPTS['viral'])
        row = evaluate(model, planner, pkt, [], words)
        if row['ok']:
            row.update(ground_truth_hits(_plan_from_cache(folder, model, pkt)))
        rows.append(row)
        print(f"  {model:52s} ok={row['ok']} сюжет={row.get('beats_found')} "
              f"мусор={row.get('filler_ratio')} рез.слов={row.get('word_cuts')} "
              f"{row.get('error') or ''}")
    after = quota.snapshot(force=True)
    print(f"Бесплатных запросов после прогона: осталось {int(after['remaining'] or 0)}.")

    out = root / 'bench' / 'results.json'
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    md = ['# Бенчмарк редакционных моделей, синтетическое окно 300 с',
          '',
          'Пакет один и тот же для всех: диалог из `bench_dialogue.py` с тремя зашитыми',
          'сильными моментами (конфликт о деньгах, находка ключей, угроза в финале) и',
          'тремя намеренно скучными вставками. Слово «Сюжет» — сколько из трёх моментов',
          'модель поймала; «Мусор» — доля отобранного хронометража, пришедшаяся на скучные',
          'вставки; «Рез.слов» — сколько границ разрезало распознанное слово (ноль — хорошо).',
          '',
          table(rows),
          '',
          'Это сравнение редакционной дисциплины на синтетическом тексте. Оно не оценивает',
          'работу с кадрами и не является прогнозом просмотров. Окончательное решение —',
          'просмотр смонтированного ролика человеком.']
    (root / 'bench' / 'RESULTS.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    print('\n' + table(rows))
    print('\nСохранено: bench/results.json, bench/RESULTS.md')
    return 0


def _plan_from_cache(folder, model, pkt):
    """Read back the validated plan the planner cached for this exact packet."""
    import hashlib
    from cloud_planner import PROMPT
    cache_key = hashlib.sha256(json.dumps([model, MODE_PROMPTS['viral'], pkt, []],
                                          ensure_ascii=False).encode()).hexdigest()
    path = folder / (cache_key + '.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return dict(candidates=[])


if __name__ == '__main__':
    sys.exit(main())
