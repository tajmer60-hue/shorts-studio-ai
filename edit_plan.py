"""Source-time EDL. Unapproved pause proposals never remove footage."""
import math


def build_plan(source, start, end, proposals):
    if not all(math.isfinite(x) for x in (start,end)) or start<0 or end<=start:
        raise ValueError('Invalid source interval')
    cuts=[]
    for proposal in proposals:
        if proposal.get('approved') is not True:
            continue
        a,b=proposal['start'],proposal['end']
        if not all(math.isfinite(x) for x in (a,b)) or b<=a:
            raise ValueError('Invalid cut interval')
        a,b=max(start,a),min(end,b)
        if b>a:cuts.append((a,b))
    merged=[]
    for a,b in sorted(cuts):
        if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    segments=[];cursor=start;output=0
    for a,b in merged+[(end,end)]:
        if a>cursor:
            segments.append(dict(source_in=cursor,source_out=a,output_in=output,speed=1))
            output+=a-cursor
        cursor=b
    if not segments:raise ValueError('Cuts remove the entire clip')
    return dict(schema=1,source=source,segments=segments,duration=output,
                cuts=[dict(start=a,end=b) for a,b in merged],requires_review=True)


def remap_words(words, plan):
    result=[]
    for segment in plan['segments']:
        for word in words:
            # Reject edits through words rather than silently shorten speech.
            a,b=word['start'],word['end']
            if b<=segment['source_in'] or a>=segment['source_out']:continue
            if a<segment['source_in'] or b>segment['source_out']:
                raise ValueError('Edit boundary intersects a recognized word')
            result.append(dict(word, start=segment['output_in']+a-segment['source_in'],
                               end=segment['output_in']+b-segment['source_in']))
    return result
