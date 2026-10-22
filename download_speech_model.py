"""Explicit opt-in download; model inference never downloads automatically."""
from pathlib import Path
import hashlib
import os
os.environ.setdefault('HF_HOME',str(Path(__file__).parent/'cache/huggingface'))
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
import requests
from speech import MODEL_REVISION


def main():
    from huggingface_hub import HfApi, hf_hub_download
    root=Path(__file__).parent/'models/faster-whisper-small'
    root.mkdir(parents=True,exist_ok=True)
    repo='Systran/faster-whisper-small'
    info=HfApi().model_info(repo,revision=MODEL_REVISION,files_metadata=True)
    entry=next(x for x in info.siblings if x.rfilename=='model.bin')
    expected=entry.lfs.sha256
    target=root/'model.bin'
    if target.exists():
        raise ValueError('model.bin already exists; refusing to overwrite.')
    partial=root/'model.download.partial'
    digest=hashlib.sha256()
    # Bounded range requests avoid a stalled single large transfer.
    with partial.open('wb') as out:
        for start in range(0,entry.size,8*1024*1024):
            end=min(entry.size-1,start+8*1024*1024-1)
            url=f'https://huggingface.co/{repo}/resolve/{MODEL_REVISION}/model.bin?part={start}'
            response=requests.get(url,headers={'Range':f'bytes={start}-{end}'},timeout=(15,60))
            response.raise_for_status()
            data=response.content
            if response.status_code!=206 or len(data)!=end-start+1:
                raise ValueError('Unexpected download range response')
            out.write(data);digest.update(data)
            print(f'{end+1}/{entry.size}',flush=True)
    if digest.hexdigest()!=expected:raise ValueError('Model checksum mismatch')
    partial.rename(target)
    for name in ('config.json','tokenizer.json','vocabulary.txt','README.md'):
        hf_hub_download(repo,name,revision=MODEL_REVISION,local_dir=root)
    (root/'REVISION.txt').write_text(MODEL_REVISION,encoding='utf-8')
    print('Verified model downloaded:',root,flush=True)


if __name__=='__main__':main()
