"""Real multi-segment video assembly, background fill and timed subtitles."""
from pathlib import Path
import hashlib
import json
import subprocess
import uuid
from render import executable,probe,publish_no_replace
from runtime_support import source_key,check_disk,read_log_tail
from process_guard import ProcessGuard
from speech import subtitle_cues,srt,atomic_json
from edit_plan import remap_words
from censor import remap_ops,volume_filter


def run_media(command,cancelled,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    log=folder/(uuid.uuid4().hex+'.log');process=guard=None
    try:
        with log.open('w',encoding='utf-8') as stderr:
            process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=stderr,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            guard=ProcessGuard(process)
            while process.poll() is None:
                if cancelled.wait(.1):raise ValueError('Отменено.')
                check_disk(folder)
            if process.returncode:raise ValueError('Ошибка обработки видео: '+read_log_tail(log)[-1000:])
    finally:
        if process and process.poll() is None:
            process.kill();process.wait(timeout=5)
        if guard:guard.close()
        log.unlink(missing_ok=True)


def aligned_segments(candidate,words,duration):
    spans=[]
    for s in candidate['source_segments']:
        a,b=s['in'],s['out']
        # Expand approximate model boundaries to include whole recognized words.
        for word in reversed(words):
            if word['start']<a<word['end']:a=max(0,word['start'])
        for word in words:
            if word['start']<b<word['end']:b=min(duration,word['end'])
        if spans and a<=spans[-1]['source_out']:
            spans[-1]['source_out']=max(b,spans[-1]['source_out'])
        else:spans.append(dict(source_in=a,source_out=b))
    out=0
    for s in spans:s['output_in']=out;out+=s['source_out']-s['source_in']
    return dict(schema=1,segments=spans,duration=out)


def ass_text(text):
    return str(text).replace('\\','\\\\').replace('{','（').replace('}','）').replace('\r',' ').replace('\n',' ')


def ass_document(cues,title,duration):
    def stamp(t):
        cs=max(0,round(t*100));h,cs=divmod(cs,360000);m,cs=divmod(cs,6000);s,cs=divmod(cs,100)
        return f'{h}:{m:02}:{s:02}.{cs:02}'
    header='''[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Speech,Segoe UI,34,&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,50,50,230,1
Style: Title,Segoe UI,38,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,100,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines=[f'Dialogue: 1,{stamp(0)},{stamp(min(3,duration))},Title,,0,0,0,,{ass_text(title)}']
    lines.extend(f"Dialogue: 0,{stamp(c['start'])},{stamp(c['end'])},Speech,,0,0,0,,{ass_text(c['text'])}" for c in cues)
    return header+'\n'.join(lines)+'\n'


def render_montage(source,candidate,transcript,folder,cancelled,progress=lambda x:None,censor_ops=None):
    if cancelled.is_set():raise ValueError('Отменено.')
    source=Path(source).resolve();folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    identity=source_key(source);total=probe(source)
    words=transcript.get('words',[])
    from ai_contract import validate_ai_plan
    candidate=validate_ai_plan(dict(schema=1,candidates=[candidate]),total)['candidates'][0]
    plan=aligned_segments(candidate,words,total)
    if not 1<=plan['duration']<=180:raise ValueError('Монтаж должен длиться от 1 до 180 секунд.')
    cues=subtitle_cues(remap_words(words,plan))
    # Censor intervals move into output time before rendering. A conflict means a
    # cut would split a muted word, which is exactly the defect we must not ship.
    censored=remap_ops(censor_ops or [],plan['segments'])
    if censored['conflicts']:
        details='; '.join(f"{c['word'] or 'интервал'} {c['start']:.2f}-{c['end']:.2f}: {c['reason']}"
                          for c in censored['conflicts'][:5])
        raise ValueError('Цензура не согласована с монтажом: '+details)
    mute=volume_filter(censored['ops'])
    key=hashlib.sha256(json.dumps([identity,plan,candidate['title'],cues,censored['ops'],'montage-v2'],ensure_ascii=False).encode()).hexdigest()
    work=folder/key;work.mkdir(exist_ok=True);target=work/'preview.mp4'
    marker=work/'complete.json'
    if marker.exists() and target.exists():
        from pipeline import digest
        record=json.loads(marker.read_text(encoding='utf-8'))
        if record.get('sha256')==digest(target):return target
    check_disk(work,plan['duration']*3)
    data=json.loads(subprocess.check_output([executable('ffprobe'),'-v','error','-show_streams','-of','json',str(source)],text=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
    audio=any(s['codec_type']=='audio' for s in data['streams'])
    pieces=[]
    for i,s in enumerate(plan['segments']):
        if cancelled.is_set():raise ValueError('Отменено.')
        duration=s['source_out']-s['source_in'];piece=work/f'part-{i:03}-{uuid.uuid4().hex}.mp4'
        command=[executable('ffmpeg'),'-v','error','-nostdin','-n','-ss',str(s['source_in']),'-i',str(source)]
        if not audio:command+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        filt='[0:v]split=2[bg][fg];[bg]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur=24:2[b];[fg]scale=720:1280:force_original_aspect_ratio=decrease:force_divisible_by=2[f];[b][f]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v]'
        command+=['-t',str(duration),'-filter_complex',filt,'-map','[v]','-map','0:a:0' if audio else '1:a:0',
            '-af',f'aresample=48000,afade=t=in:d=0.01,afade=t=out:st={max(0,duration-.01)}:d=0.01',
            '-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',str(piece)]
        run_media(command,cancelled,work);pieces.append(piece)
        progress((i+1)/(len(plan['segments'])+1)*100)
    listing=work/('concat-'+uuid.uuid4().hex+'.txt')
    listing.write_text('\n'.join("file '"+p.name+"'" for p in pieces),encoding='utf-8')
    subtitles=work/'captions.ass';subtitles.write_text(ass_document(cues,candidate['title'],plan['duration']),encoding='utf-8')
    (work/'captions.srt').write_text(srt(cues),encoding='utf-8')
    # Generated cache path only, never interpolate AI/user text into a filter expression.
    escaped=str(subtitles).replace('\\','/').replace(':','\\:').replace("'","'\\''")
    temp=work/(uuid.uuid4().hex+'.partial.mp4')
    final=[executable('ffmpeg'),'-v','error','-nostdin','-n','-f','concat','-safe','1','-i',str(listing),
        '-vf',"ass='"+escaped+"'",'-c:v','libx264','-preset','fast','-crf','20','-c:a','aac','-b:a','192k']
    # Only approved intervals are silenced; the rest of the dialogue is untouched.
    if mute:final+=['-af',mute]
    final+=['-movflags','+faststart',str(temp)]
    run_media(final,cancelled,work)
    if abs(probe(temp)-plan['duration'])>.3:raise ValueError('Длительность монтажа не совпала с планом.')
    if source_key(source)!=identity:raise ValueError('Исходник изменился.')
    if target.exists():raise ValueError('Существующий предпросмотр повреждён; сохранён для диагностики.')
    from pipeline import digest
    atomic_json(work/'montage.json',dict(candidate=candidate,plan=plan,source=str(source),source_identity=identity,
                cues=cues,censor=dict(schema=1,ops=censored['ops'],conflicts=censored['conflicts'],
                limitations=['Глушение применено только к интервалам, одобренным пользователем.',
                             'Маскировка не устраняет авторские права и не гарантирует отсутствие ограничений платформы.'])))
    # Persist the validated artifact hash before rename. Recovery still requires
    # the final target AND a matching hash, never this marker alone.
    atomic_json(marker,dict(sha256=digest(temp)))
    publish_no_replace(temp,target)
    try:
        run_media([executable('ffmpeg'),'-v','error','-nostdin','-n','-i',str(target),'-frames:v','1','-vf','scale=240:426',str(target.with_suffix('.jpg'))],cancelled,work)
    except ValueError:pass  # A poster is optional; the validated video remains playable.
    for piece in pieces:piece.unlink(missing_ok=True)
    progress(100);return target
