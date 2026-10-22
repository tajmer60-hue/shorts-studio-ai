"""Local, bounded-memory speech analysis with per-chunk checkpoints."""
from pathlib import Path
import hashlib
import json
import math
import subprocess
import time
import uuid
import wave

from render import executable, probe
from runtime_support import source_key, check_disk, read_log_tail
from process_guard import ProcessGuard

MODEL_REVISION = '536b0662742c02347bc0e980a01041f333bce120'


def atomic_json(path, data):
    temp=path.with_suffix('.'+uuid.uuid4().hex+'.partial')
    try:
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def extract(source, path, start, duration, cancelled):
    log=path.with_suffix('.log')
    guard=process=None
    try:
        with log.open('w',encoding='utf-8') as stderr:
            process=subprocess.Popen([executable('ffmpeg'),'-hide_banner','-v','error','-nostdin','-n',
                '-protocol_whitelist','file,pipe','-ss',str(start),'-i',str(source),'-t',str(duration),
                '-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(path)],stderr=stderr,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            guard=ProcessGuard(process)
            while process.poll() is None:
                if cancelled.wait(.1):
                    process.terminate();process.wait(timeout=5)
                    raise ValueError('Распознавание отменено.')
            if process.returncode:
                raise ValueError('Не удалось извлечь речь: '+read_log_tail(log))
    finally:
        if process and process.poll() is None:
            process.kill();process.wait(timeout=5)
        if guard:guard.close()
        log.unlink(missing_ok=True)


def subtitle_cues(words, max_chars=42, max_seconds=4):
    cues=[];group=[]
    for word in words:
        if group and (len(' '.join(x['word'] for x in group+[word]))>max_chars or
                      word['end']-group[0]['start']>max_seconds or word['start']-group[-1]['end']>.6):
            cues.append(dict(start=group[0]['start'],end=group[-1]['end'],text=' '.join(x['word'] for x in group)))
            group=[]
        group.append(word)
        if word['word'].endswith(('.', '!', '?')):
            cues.append(dict(start=group[0]['start'],end=group[-1]['end'],text=' '.join(x['word'] for x in group)))
            group=[]
    if group:cues.append(dict(start=group[0]['start'],end=group[-1]['end'],text=' '.join(x['word'] for x in group)))
    return cues


def srt(cues):
    def stamp(seconds):
        ms=max(0,round(seconds*1000));h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    return '\n\n'.join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(cues,1))+'\n'


def pause_proposals(words, minimum=.7, padding=.15):
    """Suggestions only: acoustic gaps do not prove a pause is dispensable."""
    return [dict(start=round(a['end']+padding,3),end=round(b['start']-padding,3),
                 reason='Пауза между распознанными словами; проверьте реакцию и смысл.',approved=False)
            for a,b in zip(words,words[1:]) if b['start']-a['end']>=minimum
            and b['start']-padding>a['end']+padding]


def transcribe(source, model_dir, cache_dir, cancelled, progress=lambda value:None, language='ru', device='auto'):
    import numpy as np
    from cuda_runtime import configure_cuda
    configure_cuda()
    import ctranslate2
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    source=Path(source).resolve();model_dir=Path(model_dir).resolve()
    if not (model_dir/'model.bin').is_file():
        raise ValueError('Локальная модель речи не установлена. Нужна models/faster-whisper-small (~486 MB, MIT).')
    identity=source_key(source)
    weight=(model_dir/'model.bin').stat()
    model_identity=[str(model_dir),weight.st_size,weight.st_mtime_ns]
    key=hashlib.sha256(json.dumps([identity,model_identity,language,'speech-v1']).encode()).hexdigest()
    folder=Path(cache_dir)/key;folder.mkdir(parents=True,exist_ok=True)
    final=folder/'transcript.json'
    if cancelled.is_set():raise ValueError('Распознавание отменено.')
    if final.exists():return json.loads(final.read_text(encoding='utf-8'))
    total=probe(source);check_disk(folder,60)
    selected='cuda' if device=='auto' and ctranslate2.get_cuda_device_count()>0 else ('cpu' if device=='auto' else device)
    compute='int8_float16' if selected=='cuda' else 'int8'
    model=WhisperModel(str(model_dir),device=selected,compute_type=compute,local_files_only=True,cpu_threads=4,num_workers=1)
    words=[];vad=[];started=time.monotonic()
    try:
        for index,start in enumerate(range(0,math.ceil(total),60)):
            if cancelled.is_set():raise ValueError('Распознавание отменено.')
            end=min(start+60,total);chunk=folder/f'chunk-{index:05}.json'
            if chunk.exists():
                data=json.loads(chunk.read_text(encoding='utf-8'))
            else:
                left=max(0,start-1);right=min(total,end+1)
                audio_path=folder/(uuid.uuid4().hex+'.wav')
                try:
                    extract(source,audio_path,left,right-left,cancelled)
                    with wave.open(str(audio_path),'rb') as wav:
                        audio=np.frombuffer(wav.readframes(wav.getnframes()),dtype=np.int16).astype(np.float32)/32768
                    speech=get_speech_timestamps(audio,VadOptions(min_silence_duration_ms=400))
                    intervals=[[max(start,left+x['start']/16000),min(end,left+x['end']/16000)] for x in speech]
                    intervals=[x for x in intervals if x[1]>x[0]]
                    segments,_=model.transcribe(audio,language=language,beam_size=5,word_timestamps=True,
                        vad_filter=True,vad_parameters=dict(min_silence_duration_ms=400),condition_on_previous_text=False)
                    chunk_words=[]
                    for segment in segments:
                        if cancelled.is_set():raise ValueError('Распознавание отменено.')
                        for word in segment.words or []:
                            a,b=left+word.start,left+word.end
                            if start<=(a+b)/2<end and b>a:
                                chunk_words.append(dict(start=max(0,a),end=min(total,b),word=word.word.strip(),probability=word.probability))
                    data=dict(words=chunk_words,vad=intervals)
                    atomic_json(chunk,data)
                finally:audio_path.unlink(missing_ok=True)
            words.extend(data['words']);vad.extend(data['vad']);progress(end/total*100)
        if source_key(source)!=identity:raise ValueError('Исходник изменился во время распознавания.')
        words.sort(key=lambda w:w['start'])
        result=dict(schema=1,source=str(source),source_identity=identity,duration=total,model=str(model_dir),
                    device=selected,compute_type=compute,words=words,vad=vad,cues=subtitle_cues(words),
                    pause_proposals=pause_proposals(words),elapsed_seconds=time.monotonic()-started,
                    limitations=['Таймкоды ASR приблизительные; нужна проверка слов и склеек.',
                                 'Паузы — предложения. Реакции и смысл автоматически не оценены.'])
        (folder/'transcript.srt').write_text(srt(result['cues']),encoding='utf-8')
        atomic_json(final,result)
        return result
    finally:
        del model
