"""Automatic chapter formatting: every chapter gets the same clean layout,
however it was typed or pasted (Word, Google Docs, PDFs, phones).

Only spacing and line breaks change. Words, punctuation, dialogue and the
order of paragraphs are never touched — format(text).split() is always
the same as text.split() (apart from invisible characters)."""

import re

# Invisible or odd spaces that pasting leaves behind.
_ZERO_WIDTH = re.compile('[​‌‍⁠﻿­]')
_ODD_SPACES = re.compile('[  -   　\t]')
_SCENE_BREAK = re.compile(r'^[\s*#~\-–—•·=_]{1,20}$')
# A line that ends a sentence (or a line of dialogue) keeps its break.
_ENDS_SENTENCE = re.compile(r'[.!?…:;"”\'’)\]*_—–-]$')


def _starts_mid_sentence(line):
    """A wrapped line continues the sentence above: it starts in lowercase."""
    return bool(line) and line[0].islower()


def format_chapter(text: str) -> str:
    if not text:
        return ''
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace(' ', '\n').replace(' ', '\n\n')
    text = _ZERO_WIDTH.sub('', text)
    text = _ODD_SPACES.sub(' ', text)
    lines = [re.sub(r' {2,}', ' ', line).strip() for line in text.split('\n')]

    # Blocks separated by blank lines.
    blocks, current = [], []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    paragraphs = []
    for block in blocks:
        # Inside a block, a line that breaks mid-sentence (the next line
        # starts in lowercase) is a wrapped line: join it back up.
        para = block[0]
        for line in block[1:]:
            if _SCENE_BREAK.match(line) or _SCENE_BREAK.match(para):
                paragraphs.append(para)
                para = line
            elif not _ENDS_SENTENCE.search(para) and _starts_mid_sentence(line):
                para = f'{para} {line}'
            else:
                paragraphs.append(para)
                para = line
        paragraphs.append(para)
    return '\n\n'.join(paragraphs)
