-- Permite que -1, -modelo, -mockup e outros arquivos da mesma variante
-- sejam armazenados e processados de forma independente.
ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS arquivo_id text;

UPDATE public.estampas
SET arquivo_id = lower(
    regexp_replace(coalesce(original_filename, ''), '\.[^.]+$', '')
)
WHERE arquivo_id IS NULL OR btrim(arquivo_id) = '';

ALTER TABLE public.estampas ALTER COLUMN arquivo_id SET NOT NULL;

DROP INDEX IF EXISTS public.estampas_codigo_variante_unique;

CREATE UNIQUE INDEX IF NOT EXISTS estampas_codigo_variante_arquivo_unique
    ON public.estampas (codigo, variante, arquivo_id);

COMMENT ON COLUMN public.estampas.arquivo_id IS
    'Identidade do arquivo dentro de codigo+variante, por exemplo 6236-a-1 ou 6236-a-mockup.';
