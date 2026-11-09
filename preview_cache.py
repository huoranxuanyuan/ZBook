# SPDX-License-Identifier: AGPL-3.0-only
"""Session snapshots: selecting a book loads its parsed result, never reparses sources."""
from dataclasses import asdict
from pathlib import Path
import gzip
import json
import uuid
from core import Chapter,Metadata,ParsedBook

class SessionPreviewCache:
    def __init__(self,root):
        self.root=Path(root)/uuid.uuid4().hex

    def path(self,job_id,version):
        if not job_id.isalnum() or not isinstance(version,int):raise ValueError('Invalid preview cache key')
        return self.root/f'{job_id}-{version}.json.gz'

    def save(self,job_id,version,book):
        target=self.path(job_id,version);target.parent.mkdir(parents=True,exist_ok=True)
        pending=target.with_suffix('.pending')
        with gzip.open(pending,'wt',encoding='utf-8',compresslevel=1) as stream:
            json.dump(asdict(book),stream,ensure_ascii=False)
        pending.replace(target)

    def exists(self,job_id,version):return self.path(job_id,version).is_file()

    def load(self,job_id,version):
        with gzip.open(self.path(job_id,version),'rt',encoding='utf-8') as stream:data=json.load(stream)
        return ParsedBook(Metadata(**data['metadata']),[Chapter(**row) for row in data['chapters']],data['encoding'],data['characters'],data['warnings'])
