"""Full-episode local preparation, optional cloud selection, automatic assembly.

Nothing is sent to a provider unless the caller passes authorization, a key and a
non-zero budget. Profanity is detected locally but never muted without an
explicit user approval carried in `censor_ops`.
"""
from pathlib import Path
import hashlib
import json
import math
import subprocess
from speech import transcribe,atomic_json
from runtime_support import source_key
from render import executable
from montage_render import run_media,render_montage,aligned_segments
from cloud_planner import OpenRouterPlanner,unique_chronological,REQUEST_RESERVE
from censor import dictionary,profanity_hits,audio_ops
from modes import MODE_PROMPTS,plan_for_mode
from reframe import plan_reframe,SaliencyBoxSource,ProviderBoxSource
from providers import (GeminiPlanner,GroqPlanner,FallbackChain,Ledger,ConsentRequired,FreeQuota,
                       FREE_VISION_MODEL,UNKNOWN_TARIFF_RESERVE)

PROVIDERS=('free','auto','gemini','groq','openrouter')


def prepare_episode(source,root,cancelled,status):
    source=Path(source).resolve();root=Path(root)
    identity=source_key(source)
    project=root/'cache'/'episodes'/hashlib.sha256(json.dumps([identity,'episode-v2']).encode()).hexdigest()
    project.mkdir(parents=True,exist_ok=True)
    status('Распознаю диалоги всей серии на компьютере…')
    transcript=transcribe(source,root/'models'/'faster-whisper-small',root/'cache'/'speech',cancelled,
        lambda p:status(f'Распознаю диалоги: {p:.0f}%'))
    duration=transcript['duration'];packets=[]
    for index,start in enumerate(range(0,math.ceil(duration),300)):
        end=min(duration,start+315);start=max(0,start-15);frames=[]
        status(f'Готовлю обзор серии: часть {index+1} из {math.ceil(duration/300)}')
        for j in range(10):
            stamp=start+(j+.5)*(end-start)/10
            image=project/f'frame-{index:04}-{j:02}.jpg'
            if not image.exists():
                temporary=image.with_name(image.stem+'-working.jpg')
                temporary.unlink(missing_ok=True)
                run_media([executable('ffmpeg'),'-v','error','-nostdin','-n','-ss',str(stamp),'-i',str(source),
                    '-frames:v','1','-vf','scale=512:512:force_original_aspect_ratio=decrease','-q:v','5',str(temporary)],cancelled,project)
                temporary.rename(image)
            frames.append((stamp,str(image)))
        packet=dict(duration=duration,source_in=start,source_out=end,
            dialogue=[c for c in transcript['cues'] if c['end']>start and c['start']<end],
            task='Select coherent stories in this window. Absolute source timestamps. Do not fabricate missing context.')
        packets.append(dict(packet=packet,frames=frames))
    if source_key(source)!=identity:raise ValueError('Исходник изменился при подготовке.')
    manifest=dict(schema=1,source=str(source),source_identity=identity,duration=duration,packets=packets)
    atomic_json(project/'package.json',manifest)
    status('Серия подготовлена локально. Ничего не отправлено в API.')
    return project,transcript,manifest


def build_planner(settings,folder,authorized,budget,quota=None):
    """Create the planner the user approved. `auto` keeps the cheapest first.

    `free` uses a zero-price OpenRouter model, so the scarce resource is the daily
    REQUEST count, not money: budget may stay 0 and FreeQuota does the guarding.
    A free model is only offered when the user allowed data collection, because
    free endpoints are excluded when it is denied.

    Groq is text-only, so FallbackChain skips it whenever frames are attached and
    a vision-capable provider is present; that skip is recorded, never silent.
    """
    settings=dict(settings or {})
    provider=settings.get('provider','free')
    if provider not in PROVIDERS:raise ValueError('Неизвестный провайдер: '+str(provider))
    folder=Path(folder)
    gemini=(settings.get('gemini_key') or '').strip()
    groq=(settings.get('groq_key') or '').strip()
    openrouter=(settings.get('openrouter_key') or '').strip()
    training=bool(settings.get('training_allowed'))
    if not authorized:
        raise ConsentRequired('Облачный анализ выключен: нужно явное разрешение отправки материала.')
    free_wanted=provider in ('free','auto') and openrouter and (provider=='free' or training)
    if provider=='free' and budget<=0:budget=0.0
    if provider not in ('free','auto') and budget<=0:
        raise ConsentRequired('Для платного провайдера нужен ненулевой лимит проекта.')

    def free_planner():
        if not openrouter:
            raise ConsentRequired('Для бесплатных моделей нужен ключ OpenRouter (аккаунт бесплатный).')
        # The user's own consent flag decides; never assume it.
        return OpenRouterPlanner(openrouter,folder,0,True,
            model=settings.get('free_model',FREE_VISION_MODEL),training_allowed=training,
            quota=quota if quota is not None else FreeQuota(openrouter))

    if provider=='free':return free_planner()
    if provider=='openrouter':
        if not openrouter:raise ConsentRequired('Ключ OpenRouter не указан.')
        return OpenRouterPlanner(openrouter,folder,budget,True)
    if provider=='auto' and free_wanted:
        # Cheapest consent-compatible option first; paid providers stay as fallback.
        chain=[free_planner()]
    else:chain=[]
    ledger=Ledger(folder/'usage.sqlite3',budget)
    planners=[]
    if provider in ('auto','gemini') and gemini:
        planners.append(GeminiPlanner(gemini,folder,budget,True,
            model=settings.get('gemini_model','gemini-2.5-flash-lite'),ledger=ledger))
    if provider in ('auto','groq') and groq:
        planners.append(GroqPlanner(groq,folder,budget,True,
            model=settings.get('groq_model','openai/gpt-oss-120b'),ledger=ledger))
    if provider=='auto' and openrouter and not free_wanted:
        planners.append(OpenRouterPlanner(openrouter,folder,budget,True))
    planners=chain+planners
    if not planners:
        ledger.close()
        raise ConsentRequired('Для выбранного провайдера нет ключа. Запрос не отправлен.')
    if len(planners)==1:
        ledger.close()
        return planners[0]
    return FallbackChain(planners)


def review_profanity(transcript,lexicon_path=None):
    """Detect profanity locally. Returns proposals; approval stays with the user."""
    lexicon=dictionary(lexicon_path)
    hits=profanity_hits(transcript.get('words',[]),lexicon)
    return dict(schema=1,lexicon=lexicon,hits=hits,
                approved_ops=audio_ops(hits),
                limitations=['Обнаружение по корням и таймкодам ASR: возможны пропуски и ложные срабатывания.',
                             'Неуверенно распознанное слово не считается ни матом, ни безопасным.',
                             'Без одобрения пользователя ничего не глушится.'])


def probe_size(source):
    data=json.loads(subprocess.check_output([executable('ffprobe'),'-v','error','-show_streams',
        '-of','json',str(source)],text=True,timeout=30,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
    stream=next(s for s in data['streams'] if s['codec_type']=='video')
    return int(stream['width']),int(stream['height'])


def build_reframe(source,manifest,project,planner,settings,cancelled,status):
    """Local by default: Smart Reframe must work with no network and no money.

    The saliency source is an estimate, not a face detector, so the plan is
    labelled low confidence and the rendered result still needs a look.
    """
    if settings.get('reframe',True) is False:return None
    width,height=probe_size(source)
    interval=max(0.5,float(settings.get('reframe_interval',1.0)))
    count=int(manifest['duration']//interval)+1
    stamps=[round(i*interval,3) for i in range(count)][:1200]
    status(f'Строю сопровождение кадра: {len(stamps)} точек обзора…')
    if settings.get('reframe_source')=='provider' and planner is not None             and getattr(planner,'VISION',False):
        box_source=ProviderBoxSource(planner)
    else:
        box_source=SaliencyBoxSource()
    samples=box_source.sample(source,stamps,width,height,cancelled)
    plan=plan_reframe(width,height,samples,manifest['duration'])
    plan['source']='provider' if isinstance(box_source,ProviderBoxSource) else 'local-saliency'
    plan['confidence']='low' if plan['source']=='local-saliency' else 'model'
    atomic_json(project/'reframe.json',plan)
    status('Сопровождение кадра построено локально. Точки между обзорами интерполированы.')
    return plan


def _apply_pause_cuts(candidate,cuts):
    """Turn approved word-safe pause cuts into real segment splits.

    A cut that only partially overlaps a segment is dropped here rather than
    half-applied: half-removed pauses are the defect the spec forbids.
    """
    if not cuts:return candidate['source_segments']
    segments=[]
    for seg in candidate['source_segments']:
        a,b=seg['in'],seg['out']
        inside=[c for c in cuts if c['start']>=a and c['end']<=b]
        if not inside:
            segments.append(dict(**{'in':a,'out':b}));continue
        from edit_plan import build_plan
        plan=build_plan('source',a,b,inside)
        segments.extend(dict(**{'in':x['source_in'],'out':x['source_out']}) for x in plan['segments'])
    return segments


def _finish_and_render(source,transcript,manifest,project,result,ops,reframe,
                       cancelled,status,on_clip):
    for c in result['candidates']:
        c['source_segments']=_apply_pause_cuts(c,c.get('pause_cuts',[]))
        aligned=aligned_segments(c,transcript['words'],manifest['duration'])
        c['source_segments']=[{'in':s['source_in'],'out':s['source_out']} for s in aligned['segments']]
    result=unique_chronological(result);atomic_json(project/'selected.json',result)
    for i,candidate in enumerate(result['candidates']):
        if cancelled.is_set():raise ValueError('Отменено.')
        status(f'Монтирую {i+1} из {len(result["candidates"])}: {candidate["title"]}')
        video=render_montage(source,candidate,transcript,project/'renders',cancelled,censor_ops=ops,reframe=reframe)
        # Report what was actually applied, read back from the rendered artifact.
        try:censor=json.loads((video.parent/'montage.json').read_text(encoding='utf-8')).get('censor',{})
        except (OSError,ValueError,KeyError):censor={}
        on_clip(dict(candidate=candidate,path=str(video),part=i+1,censor=censor))
    return dict(clips=len(result['candidates']),result=result)


def create_clips(source,root,key,budget,authorized,cancelled,status,on_clip,
                 settings=None,censor_ops=None,lexicon_path=None):
    settings=dict(settings or {})
    mode=settings.get('mode','serial')
    if mode not in MODE_PROMPTS:raise ValueError('Неизвестный режим монтажа: '+str(mode))
    duration_range=(float(settings.get('duration_min',45.0)),float(settings.get('duration_max',90.0)))
    aggressiveness=settings.get('aggressiveness','normal')
    top_k=settings.get('top_k')
    project,transcript,manifest=prepare_episode(source,root,cancelled,status)
    review=review_profanity(transcript,lexicon_path)
    atomic_json(project/'censor_review.json',review)
    if review['hits']:
        status(f"Найдено спорных слов: {len(review['hits'])}. Список сохранён в censor_review.json; "
               f"без вашего одобрения ничего не глушится.")
    ops=list(censor_ops or [])
    if not authorized:
        # Fully offline automatic montage: local editorial algorithms, no network,
        # no money. Honest label: heuristics over the transcript, not an AI verdict.
        status(f'Собираю ролики локальным алгоритмом режима «{mode}» без нейросети…')
        raw=plan_for_mode(mode,transcript,(),duration_range,aggressiveness,top_k=top_k)
        if raw.get('reason'):status(raw['reason'])
        from ai_contract import validate_ai_plan
        result=validate_ai_plan(raw,manifest['duration'])
        reframe=build_reframe(source,manifest,project,None,settings,cancelled,status)
        done=_finish_and_render(source,transcript,manifest,project,result,ops,reframe,
                                cancelled,status,on_clip)
        return dict(prepared=True,cloud=False,local_montage=True,mode=mode,
                    clips=done['clips'],project=str(project),profanity=len(review['hits']),
                    muted=len(ops),reason=raw.get('reason'),limitations=raw.get('limitations'))
    planner=(build_planner(settings,project/'cloud',authorized,budget) if settings
             else OpenRouterPlanner(key,project/'cloud',budget,authorized))
    # Fail a fresh project before spending on only part of an episode. This runs
    # BEFORE any provider-backed reframe so a quota check always precedes spend.
    reserve=float(getattr(planner,'reserve_estimate',lambda:REQUEST_RESERVE)())
    ledger=getattr(planner,'ledger',None)
    fresh=not ledger.rows() if ledger is not None else True
    required=(len(manifest['packets'])+1)*reserve
    free_quota=getattr(planner,'quota',None)
    if free_quota is not None:
        # One episode is windows + a global pass; refuse up front if it cannot fit.
        free_quota.ensure(len(manifest['packets'])+1)
        status(f"Бесплатных запросов осталось: {int(free_quota.snapshot().get('remaining') or 0)} "
               f"на сегодня. На серию нужно {len(manifest['packets'])+1}.")
    if fresh and budget<required:
        raise ValueError(f'Для безопасного резерва всей серии нужен лимит ${required:.2f} '
                         f'({len(manifest["packets"])+1} запросов по ${reserve:.4f}). '
                         f'Это верхний резерв, а не прогноз цены. Запросы не отправлены.')
    reframe=build_reframe(source,manifest,project,planner,settings,cancelled,status)
    prompt=MODE_PROMPTS[mode]
    candidates=[]
    for i,item in enumerate(manifest['packets']):
        status(f'AI отбирает события в режиме «{mode}»: часть {i+1} из {len(manifest["packets"])}')
        result=planner.propose(item['packet'],item['frames'],cancelled,task_prompt=prompt)
        # Window calls cannot invent footage from unobserved parts of the episode.
        for clip in result['candidates']:
            if any(s['in']<item['packet']['source_in'] or s['out']>item['packet']['source_out'] for s in clip['source_segments']):
                raise ValueError('AI выбрал материал за пределами просмотренного окна.')
        candidates.extend(result['candidates'])
    if cancelled.is_set():raise ValueError('Отменено.')
    if candidates:
        status('AI проверяет общий сюжет и отбирает ролики без повторов…')
        global_packet=dict(duration=manifest['duration'],mode=mode,task='Final editorial selection. Select the best proposals, combine only provided segments if necessary. Remove repeated events; chronological order. Do not invent source intervals.',proposals=candidates)
        result=planner.propose(global_packet,(),cancelled,task_prompt=prompt)
        allowed=[s for c in candidates for s in c['source_segments']]
        for clip in result['candidates']:
            for s in clip['source_segments']:
                if not any(a['in']<=s['in']<s['out']<=a['out'] for a in allowed):
                    raise ValueError('Итоговый AI-план использует неподтверждённый материал.')
    else:result=dict(schema=1,candidates=[])
    done=_finish_and_render(source,transcript,manifest,project,result,ops,reframe,
                            cancelled,status,on_clip)
    return dict(prepared=True,cloud=True,mode=mode,clips=done['clips'],project=str(project),
                profanity=len(review['hits']),muted=len(ops))
