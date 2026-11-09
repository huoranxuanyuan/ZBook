# SPDX-License-Identifier: AGPL-3.0-only
"""Regression checks for real originals, accurate matching and untouched cache bytes."""
import unittest,tempfile
from unittest.mock import patch
from pathlib import Path
from io import BytesIO
from PIL import Image
import covers

def picture(size,color='blue'):
    b=BytesIO();Image.new('RGB',size,color).save(b,'JPEG',quality=90);return b.getvalue()

class HDCoverTests(unittest.TestCase):
    def test_original_variants(self):
        self.assertEqual(covers.image_urls('https://bookcover.yuewen.com/qdbimg/349573/x/150')[0],'https://bookcover.yuewen.com/qdbimg/349573/x/0')
        self.assertEqual(covers.image_urls('https://cdn.static.17k.com/book/189x272/39/32/493239.jpg-189x272?v=1')[0],'https://cdn.static.17k.com/book/189x272/39/32/493239.jpg?v=1')
        self.assertEqual(covers.image_urls('https://p3-novel-sign.byteimg.com/novel-pic/xyz~tplv-resize:225:300.image?x-signature=a')[0],'https://p3-novel.byteimg.com/origin/novel-pic/xyz')
        signed='https://p3-novel-sign.byteimg.com/novel-pic/xyz~tplv-resize:225:300.image?x-signature=a'
        self.assertEqual(covers.image_urls(signed)[-1],signed)

    def test_best_image_preserves_bytes(self):
        small=picture((150,200));large=picture((600,800))
        with tempfile.TemporaryDirectory() as directory,patch.object(covers,'CACHE',Path(directory)),patch.object(covers,'_fetch',side_effect=lambda u,r:large if u.endswith('/0') else small):
            row=covers._cache_row(dict(label='测试书',url='https://bookcover.yuewen.com/qdbimg/349573/x/150',source='https://book.qq.com/'))
            self.assertEqual((row.width,row.height),(600,800));self.assertEqual(Path(row.path).read_bytes(),large)

    def test_bigger_fallback_wins(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(covers,'CACHE',Path(directory)),patch.object(covers,'_fetch',side_effect=[picture((100,140)),picture((900,1200))]):
            row=covers._cache_row(dict(label='测试书',url='https://example.org/s.jpg',fallback='https://example.org/original.jpg'))
            self.assertEqual((row.width,row.height),(900,1200))

    def test_small_is_never_upscaled(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(covers,'CACHE',Path(directory)),patch.object(covers,'_fetch',return_value=picture((100,140))):
            row=covers._cache_row(dict(label='测试书',url='https://example.org/s.jpg'))
            self.assertEqual((row.width,row.height),(100,140));self.assertIn('小图',covers.quality_label(row.width,row.height))

    def test_wrong_author_rejected(self):
        rows=[dict(label='测试书',author='甲',url='https://example.org/a',official=True),dict(label='测试书外传',author='乙',url='https://example.org/b',official=True)]
        self.assertEqual(covers.rank_rows(rows,'测试书','乙'),[])

    def test_duplicate_retains_larger_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            images=[]
            for i,size in enumerate([(150,200),(600,800)]):
                p=Path(directory)/f'{i}.jpg';p.write_bytes(picture(size));images.append(covers.Cover(str(p),'测试书','','',*size,score=600))
            result=covers.deduplicate(images);self.assertEqual(len(result),1);self.assertEqual(result[0].width,600)

if __name__=='__main__':unittest.main()
