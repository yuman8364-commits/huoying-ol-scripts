"""Normalize the discovered CEF game surface independently from browser chrome."""
from dataclasses import dataclass
from PIL import Image

REFERENCE_VIEW = (1,69,1919,1031)


@dataclass(frozen=True)
class GameGeometry:
    window: tuple[int,int,int,int]
    content: tuple[int,int,int,int]

    def __post_init__(self):
        l,t,r,b = self.window
        x1,y1,x2,y2 = self.content
        if not (l <= x1 < x2 <= r and t < y1 < y2 <= b):
            raise ValueError('game surface is not contained in the window')

    def point(self, x, y):
        if not (0 <= x < 1920 and 0 <= y < 1032):
            raise ValueError('reference point outside client')
        l,t,r,b = self.window
        x1,y1,x2,y2 = self.content
        if y < 69:
            return min(r-1,round(l+x*(r-l)/1920)), min(y1-1,round(t+y*(y1-t)/69))
        if not (1 <= x < 1919 and 69 <= y < 1031):
            raise ValueError('content point outside game surface')
        return min(x2-1,round(x1+(x-1)*(x2-x1)/1918)), min(y2-1,round(y1+(y-69)*(y2-y1)/962))

    def normalize(self, raw):
        l,t,r,b = self.window
        x1,y1,x2,y2 = self.content
        if raw.size != (r-l,b-t):
            raise ValueError('capture size changed while reading game surface')
        result = Image.new('RGB',(1920,1032))
        chrome = raw.crop((0,0,r-l,y1-t)).resize((1920,69),Image.Resampling.BILINEAR)
        content = raw.crop((x1-l,y1-t,x2-l,y2-t)).resize((1918,962),Image.Resampling.BILINEAR)
        result.paste(chrome,(0,0))
        result.paste(content,(1,69))
        return result
