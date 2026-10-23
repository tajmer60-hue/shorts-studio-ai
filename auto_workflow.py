"""Full-episode local preparation, optional cloud selection, automatic assembly."""
from pathlib import Path
import hashlib
import json
import math
from speech import transcribe,atomic_json
from runtime_support import source_key
from render import executable
from montage_render import run_media,render_montage,aligned_segments
from cloud_planner import OpenRouterPlanner,unique_chronological,REQUEST_RESERVE


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


def create_clips(source,root,key,budget,authorized,cancelled,status,on_clip):
    project,transcript,manifest=prepare_episode(source,root,cancelled,status)
    if not authorized:return dict(prepared=True,cloud=False,project=str(project))
    planner=OpenRouterPlanner(key,project/'cloud',budget,authorized)
    # Fail a fresh project before spending on only part of an episode.
    required=(len(manifest['packets'])+1)*REQUEST_RESERVE
    if not planner.ledger and budget<required:
        raise ValueError(f'Для безопасного резерва всей серии нужен лимит ${required:.2f}. Это верхний резерв, не прогноз цены. Запросы не отправлены.')
    candidates=[]
    for i,item in enumerate(manifest['packets']):
        status(f'AI отбирает события: часть {i+1} из {len(manifest["packets"])}')
        result=planner.propose(item['packet'],item['frames'],cancelled)
        # Window calls cannot invent footage from unobserved parts of the episode.
        for clip in result['candidates']:
            if any(s['in']<item['packet']['source_in'] or s['out']>item['packet']['source_out'] for s in clip['source_segments']):
                raise ValueError('AI выбрал материал за пределами просмотренного окна.')
        candidates.extend(result['candidates'])
    if cancelled.is_set():raise ValueError('Отменено.')
    if candidates:
        status('AI проверяет общий сюжет и отбирает ролики без повторов…')
        global_packet=dict(duration=manifest['duration'],task='Final editorial selection. Select the best proposals, combine only provided segments if necessary. Remove repeated events; chronological order. Do not invent source intervals.',proposals=candidates)
        result=planner.propose(global_packet,(),cancelled)
        allowed=[s for c in candidates for s in c['source_segments']]
        for clip in result['candidates']:
            for s in clip['source_segments']:
                if not any(a['in']<=s['in']<s['out']<=a['out'] for a in allowed):
                    raise ValueError('Итоговый AI-план использует неподтверждённый материал.')
    else:result=dict(schema=1,candidates=[])
    # Align first, then deduplicate, so expanded speech boundaries cannot repeat footage.
    for c in result['candidates']:
        aligned=aligned_segments(c,transcript['words'],manifest['duration'])
        c['source_segments']=[{'in':s['source_in'],'out':s['source_out']} for s in aligned['segments']]
    result=unique_chronological(result);atomic_json(project/'selected.json',result)
    for i,candidate in enumerate(result['candidates']):
        if cancelled.is_set():raise ValueError('Отменено.')
        status(f'Монтирую {i+1} из {len(result["candidates"])}: {candidate["title"]}')
        video=render_montage(source,candidate,transcript,project/'renders',cancelled)
        on_clip(dict(candidate=candidate,path=str(video),part=i+1))
    return dict(prepared=True,cloud=True,clips=len(result['candidates']),project=str(project))
