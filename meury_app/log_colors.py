"""Interpretador incremental das cores ANSI emitidas pelo processador de pedidos."""
import re


LOG_COLORS = {
    '31': '#ff9999', '32': '#8fdda0', '33': '#f5d477',
    '36': '#80d4ed', '90': '#a8b5c7',
}
_SGR = re.compile(r'\x1b\[([0-9;]*)m')


class LogColorParser:
    def __init__(self):
        self.color = None
        self.bold = False
        self.pending = ''

    def feed(self, text):
        """Retorna trechos e tags, preservando sequências divididas entre eventos."""
        text = self.pending + text
        self.pending = ''
        position = 0
        chunks = []
        for match in _SGR.finditer(text):
            if match.start() > position:
                chunks.append((text[position:match.start()], self.tags()))
            for code in match[1].split(';'):
                if code in ('', '0'):
                    self.color, self.bold = None, False
                elif code == '1':
                    self.bold = True
                elif code == '22':
                    self.bold = False
                elif code == '39':
                    self.color = None
                elif code in LOG_COLORS:
                    self.color = code
            position = match.end()
        remainder = text[position:]
        partial = re.search(r'\x1b(?:\[[0-9;]*)?$', remainder)
        if partial:
            self.pending = remainder[partial.start():]
            remainder = remainder[:partial.start()]
        if remainder:
            chunks.append((remainder, self.tags()))
        return chunks

    def tags(self):
        return ((f'ansi_{self.color}',) if self.color else ()) + (('bold',) if self.bold else ())
