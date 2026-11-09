# SPDX-License-Identifier: AGPL-3.0-only
"""Keep familiar Python regex syntax with bounded chapter matching."""
import re
import time
import regex

class HeadingPattern:
    def __init__(self,pattern):
        if not pattern.strip() or len(pattern)>300: raise ValueError('自定义章节正则需为 1～300 个字符')
        try:
            re.compile(pattern)
            self.pattern=regex.compile(pattern,regex.VERSION0)
        except (re.error,regex.error) as exc: raise ValueError('自定义正则无效：'+str(exc)) from exc
        self.spent=0.0

    def fullmatch(self,text):
        start=time.monotonic()
        try:
            if self.spent>=2: raise TimeoutError()
            return self.pattern.fullmatch(text,timeout=min(.03,2-self.spent))
        except TimeoutError as exc:
            raise ValueError('自定义章节正则计算超时，请简化重复、回溯或分组选项后重试') from exc
        finally: self.spent+=time.monotonic()-start
