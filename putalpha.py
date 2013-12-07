#!/usr/bin/env python

import sys
from PIL import Image, ImageOps

def alphavalue(x):
    if x >= 180:
        return 0
    else:
        return 180 - x

shade = Image.open(sys.argv[1])
alpha = shade.point(alphavalue)
res = Image.new('RGBA', (shade.size[0], shade.size[1]), (0, 0, 0, 0))
res.putalpha(alpha)
res.save(sys.argv[2], 'png')

