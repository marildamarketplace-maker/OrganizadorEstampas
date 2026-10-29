"""Saída do lote por contexto, sem redirecionar o stdout global do aplicativo."""
import builtins
from contextvars import ContextVar

output_sink = ContextVar('batch_output_sink', default=None)


def print(*values, sep=' ', end='\n', file=None, flush=False):
    sink = output_sink.get()
    if sink is None or file is not None:
        return builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
    sink(sep.join(str(value) for value in values) + end)
