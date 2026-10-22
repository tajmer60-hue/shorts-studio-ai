"""Offline signal analysis. Scores are editing heuristics, not semantics."""
from pathlib import Path
import json
import re
import subprocess
import threading
import uuid
from render import executable, probe
from runtime_support import source_key, check_disk
from process_guard import ProcessGuard


def candidates(duration, boundaries, silences, minimum=20, maximum=60):
    result = []
    start = 0.0
    ends = sorted(set([x for x in boundaries if 0 < x < duration] + [duration]))
    while start < duration - .1:
        eligible = [x for x in ends if minimum <= x - start <= maximum]
        end = min(eligible, key=lambda x: abs(x - start - 35)) if eligible else min(duration, start + maximum)
        quiet = sum(max(0, min(end,b) - max(start,a)) for a,b in silences)
        cuts = sum(start < x < end for x in boundaries)
        score = round(max(0, min(100, 50 + min(cuts,10)*3 - quiet/(end-start)*50)),1)
        result.append(dict(start=round(start,3), end=round(end,3), score=score,
            reason=f'Lite: {cuts} смен плана; тишина {quiet:.1f} с. Смысл и границы речи не проверены.',
            needs_review=True))
        start=end
    return sorted(result,key=lambda x:x['score'],reverse=True)


def analyze(source, cache_dir, cancelled, progress=lambda value: None):
    source=Path(source).resolve()
    identity=source_key(source)
    folder=Path(cache_dir);folder.mkdir(parents=True,exist_ok=True)
    report=folder/(identity+'-lite-v1.json')
    if report.exists():
        result=json.loads(report.read_text(encoding='utf-8'))
        if result.get('schema')==1 and result.get('source_identity')==identity:
            return result
    total=probe(source)
    check_disk(folder)
    command=[executable('ffmpeg'),'-hide_banner','-nostdin','-protocol_whitelist','file,pipe',
             '-i',str(source),'-vf',"fps=5,scale=320:-2,select='gt(scene,0.3)',showinfo",
             '-af','silencedetect=noise=-35dB:d=0.25','-f','null','-']
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    guard=None
    done=threading.Event()
    cuts=[];silences=[];quiet_start=None;tail=[]
    def stop():
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
    def watch():
        while not done.wait(.2):
            if cancelled.is_set():
                stop();return
    worker=threading.Thread(target=watch,daemon=True)
    try:
        guard=ProcessGuard(process);worker.start()
        for line in process.stdout:
            tail.append(line);tail=tail[-12:]
            match=re.search(r'pts_time:([0-9.]+)',line)
            if match:
                timestamp=float(match[1]);cuts.append(timestamp);progress(min(99,timestamp/total*100))
            match=re.search(r'silence_start: ([0-9.]+)',line)
            if match: quiet_start=float(match[1])
            match=re.search(r'silence_end: ([0-9.]+)',line)
            if match and quiet_start is not None:
                silences.append([quiet_start,float(match[1])]);quiet_start=None
        code=process.wait()
        if cancelled.is_set(): raise ValueError('Анализ отменён.')
        if code: raise ValueError('Ошибка анализа FFmpeg: '+''.join(tail)[-1800:])
        if source_key(source)!=identity: raise ValueError('Исходник изменился во время анализа.')
        if quiet_start is not None: silences.append([quiet_start,total])
        result=dict(schema=1,algorithm='lite-v1',source=str(source),source_identity=identity,
                    duration=total,scene_boundaries=cuts,silences=silences,
                    candidates=candidates(total,cuts,silences),
                    limitations=['Не ASR и не VAD: обнаруживается амплитудная тишина.',
                                 'Нет понимания сюжета; кандидаты требуют просмотра.',
                                 'Сцены измеряются на 5 fps; погрешность границ примерно 0.2 с.'])
        temporary=report.with_suffix('.'+uuid.uuid4().hex+'.partial')
        try:
            temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(report)
        finally: temporary.unlink(missing_ok=True)
        progress(100)
        return result
    finally:
        done.set();stop()
        if worker.ident:worker.join(timeout=6)
        if guard:guard.close()
        process.stdout.close()
