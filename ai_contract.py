"""Provider-neutral AI planning inputs and validation. No network calls."""
import math
from typing import Protocol


class MontagePlanner(Protocol):
    def propose(self,packet:dict)->dict:...


def analysis_windows(duration,words,scene_boundaries,window_seconds=300):
    """Cover the complete timeline; later providers inspect each window then rank globally."""
    if not math.isfinite(duration) or duration<=0 or not 30<=window_seconds<=600:
        raise ValueError('Invalid analysis window')
    result=[];start=0
    while start<duration:
        end=min(duration,start+window_seconds)
        result.append(dict(schema=1,source_in=start,source_out=end,
            words=[w for w in words if w['end']>start and w['start']<end],
            scene_boundaries=[t for t in scene_boundaries if start<=t<end],
            requested_output=['title','rationale','source_segments','context_needed','review_flags']))
        start=end
    return result


def validate_ai_plan(plan,duration):
    """Reject fabricated/out-of-range timecodes. AI proposals never imply approval."""
    if not math.isfinite(duration) or duration<=0:raise ValueError('Invalid source duration')
    if not isinstance(plan,dict) or plan.get('schema')!=1:raise ValueError('Unknown plan schema')
    candidates=plan.get('candidates')
    if not isinstance(candidates,list) or len(candidates)>100:raise ValueError('Invalid candidates')
    clean=[]
    for item in candidates:
        if not isinstance(item,dict):raise ValueError('Invalid candidate')
        title=item.get('title');reason=item.get('rationale')
        if not isinstance(title,str) or not title.strip() or len(title)>90:raise ValueError('Invalid title')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:raise ValueError('Missing explanation')
        segments=item.get('source_segments')
        if not isinstance(segments,list) or not 1<=len(segments)<=100:raise ValueError('Invalid segments')
        previous=-1
        for segment in segments:
            if not isinstance(segment,dict):raise ValueError('Invalid segment')
            a,b=segment.get('in'),segment.get('out')
            if isinstance(a,bool) or isinstance(b,bool) or not isinstance(a,(int,float)) or not isinstance(b,(int,float)):
                raise ValueError('Invalid timestamps')
            if not math.isfinite(a) or not math.isfinite(b) or not 0<=a<b<=duration:raise ValueError('Timestamp outside source')
            if a<previous:raise ValueError('Nonchronological or overlapping edit needs separate explicit review')
            previous=b
        clean.append(dict(title=title,rationale=reason,source_segments=segments,approved=False,needs_review=True))
    return dict(schema=1,candidates=clean)
